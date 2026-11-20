from __future__ import annotations

import asyncio
import collections
import contextlib
import csv
import html as html_lib
import logging
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import numpy as np
from sqlalchemy import select

from ...core.config import (
    LIVE_BUFFER_DIR,
    get_ffmpeg_path,
    get_ffprobe_path,
    get_project_dir,
)
from ...core.database import AsyncSessionLocal, load_settings
from ...core.models import Clip, LiveChannel, Project, new_id
from ...core.queue_manager import queue_manager
from ...core.ws_manager import ws_manager
from ..ingest.ffprobe_utils import probe_media
from .chat_analyzer import (
    ChatPulseAnalyzer,
    run_kick_chat_reader,
    run_twitch_chat_reader,
    run_youtube_chat_reader,
)

logger = logging.getLogger("narezki.live.watcher")

_TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
}


def _subprocess_kwargs() -> dict[str, Any]:
    """Hide console windows for background FFmpeg/yt-dlp subprocesses on Windows."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    return {}


def detect_platform(url: str) -> str:
    """Detect streaming platform from a channel URL."""
    low = (url or "").strip().lower()
    if "twitch.tv" in low:
        return "twitch"
    if "kick.com" in low:
        return "kick"
    if "youtube.com" in low or "youtu.be" in low:
        return "youtube"
    if re.fullmatch(r"[a-zA-Z0-9_]{2,25}", low):
        return "twitch"
    raise ValueError("Не удалось определить платформу. Используйте ссылку на Twitch, Kick или YouTube.")


def normalize_channel_url(url: str, platform: Optional[str] = None) -> tuple[str, str]:
    """Normalize channel URL and return (platform, normalized_url)."""
    raw = (url or "").strip()
    if not raw:
        raise ValueError("Укажите ссылку на канал")
    if "://" not in raw and not raw.startswith("@"):
        if "." not in raw:
            raw = f"https://www.twitch.tv/{raw}"
        else:
            raw = f"https://{raw}"
    elif raw.startswith("@"):
        raw = f"https://www.youtube.com/{raw}"

    resolved_platform = platform or detect_platform(raw)
    parsed = urllib.parse.urlparse(raw)
    path = parsed.path.strip("/")

    if resolved_platform == "twitch":
        login = path.split("/")[0].lower() if path else ""
        if not login:
            raise ValueError("Некорректная ссылка на Twitch канал")
        return "twitch", f"https://www.twitch.tv/{login}"

    if resolved_platform == "kick":
        slug = path.split("/")[0].lower() if path else ""
        if not slug:
            raise ValueError("Некорректная ссылка на Kick канал")
        return "kick", f"https://kick.com/{slug}"

    # YouTube
    if parsed.netloc.lower() == "youtu.be" and path:
        return "youtube", f"https://www.youtube.com/watch?v={path.split('/')[0]}"
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/live"):
        base_path = base_path[:-5]
    query = f"?{parsed.query}" if parsed.query and "v=" in parsed.query else ""
    return "youtube", f"https://www.youtube.com{base_path}{query}"


def extract_twitch_login(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[0].lower() if parts else None


def extract_kick_slug(url: str) -> Optional[str]:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    return parts[0].lower() if parts else None


def extract_channel_display_name(platform: str, url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    parts = [p for p in parsed.path.split("/") if p]
    if platform in ("twitch", "kick") and parts:
        return parts[0]
    if parts:
        return parts[-1].lstrip("@")
    return url


def _yt_dlp_base_cmd() -> list[str]:
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    return [sys.executable, "-m", "yt_dlp"]


async def _run_yt_dlp_json(target_url: str, timeout: float = 20.0) -> Optional[dict[str, Any]]:
    import json

    cmd = [
        *_yt_dlp_base_cmd(),
        "--dump-single-json",
        "--no-warnings",
        "--no-playlist",
        "--playlist-items",
        "1",
        target_url,
    ]
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_kwargs(),
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0 or not stdout:
            return None
        text = stdout.decode("utf-8", errors="replace").strip().splitlines()
        for line in reversed(text):
            if line.startswith("{"):
                data = json.loads(line)
                if isinstance(data, dict):
                    return data
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        raise
    except Exception as exc:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        logger.debug("yt-dlp metadata probe failed for %s: %s", target_url, exc)
    return None


async def check_channel_live(platform: str, channel_url: str, timeout: float = 12.0) -> dict[str, Any]:
    """Check whether a Twitch, Kick, or YouTube channel is currently live."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=_HTTP_HEADERS) as client:
            if platform == "twitch":
                login = extract_twitch_login(channel_url)
                if not login:
                    return {"is_live": False, "stream_title": "", "viewer_count": None, "video_id": None, "stream_target": channel_url, "error": "Invalid Twitch URL"}
                gql_payload = {
                    "query": (
                        "query LiveStatus($login: String!) {"
                        "  user(login: $login) {"
                        "    stream { id title viewersCount type }"
                        "  }"
                        "}"
                    ),
                    "variables": {"login": login},
                }
                resp = await client.post(
                    "https://gql.twitch.tv/gql",
                    headers={"Client-ID": _TWITCH_CLIENT_ID},
                    json=gql_payload,
                )
                resp.raise_for_status()
                body = resp.json()
                stream = ((body.get("data") or {}).get("user") or {}).get("stream")
                if stream and stream.get("type", "live") == "live":
                    return {
                        "is_live": True,
                        "stream_title": str(stream.get("title") or ""),
                        "viewer_count": int(stream["viewersCount"]) if stream.get("viewersCount") is not None else None,
                        "video_id": str(stream.get("id") or ""),
                        "stream_target": channel_url,
                        "error": None,
                    }
                return {"is_live": False, "stream_title": "", "viewer_count": None, "video_id": None, "stream_target": channel_url, "error": None}

            if platform == "kick":
                slug = extract_kick_slug(channel_url)
                if not slug:
                    return {"is_live": False, "stream_title": "", "viewer_count": None, "video_id": None, "stream_target": channel_url, "error": "Invalid Kick URL"}
                resp = await client.get(f"https://kick.com/api/v2/channels/{slug}")
                if resp.status_code == 200:
                    data = resp.json()
                    livestream = data.get("livestream")
                    if isinstance(livestream, dict) and livestream.get("is_live", True):
                        return {
                            "is_live": True,
                            "stream_title": str(livestream.get("session_title") or ""),
                            "viewer_count": int(livestream["viewer_count"]) if livestream.get("viewer_count") is not None else None,
                            "video_id": str(livestream.get("id") or ""),
                            "stream_target": channel_url,
                            "error": None,
                        }
                    return {"is_live": False, "stream_title": "", "viewer_count": None, "video_id": None, "stream_target": channel_url, "error": None}

                # Fallback through yt-dlp when Kick API returns Cloudflare 403
                meta = await _run_yt_dlp_json(channel_url, timeout=18.0)
                if meta and (meta.get("is_live") or meta.get("live_status") == "is_live"):
                    return {
                        "is_live": True,
                        "stream_title": str(meta.get("title") or ""),
                        "viewer_count": int(meta["concurrent_view_count"]) if meta.get("concurrent_view_count") is not None else None,
                        "video_id": str(meta.get("id") or ""),
                        "stream_target": channel_url,
                        "error": None,
                    }
                return {"is_live": False, "stream_title": "", "viewer_count": None, "video_id": None, "stream_target": channel_url, "error": None}

            # YouTube live check
            target = channel_url if "watch?v=" in channel_url else f"{channel_url.rstrip('/')}/live"
            resp = await client.get(target)
            if resp.status_code == 200:
                text = resp.text
                is_live_flag = '"isLive":true' in text or '"isLiveNow":true' in text
                vid_match = re.search(r'"videoId":"([A-Za-z0-9_-]{11})"', text)
                title_match = re.search(r'<title>(.*?)</title>', text, flags=re.IGNORECASE | re.DOTALL)
                if is_live_flag and vid_match:
                    vid = vid_match.group(1)
                    title = html_lib.unescape(title_match.group(1)).replace(" - YouTube", "").strip() if title_match else ""
                    return {
                        "is_live": True,
                        "stream_title": title,
                        "viewer_count": None,
                        "video_id": vid,
                        "stream_target": f"https://www.youtube.com/watch?v={vid}",
                        "error": None,
                    }
            # Fallback yt-dlp metadata check
            meta = await _run_yt_dlp_json(target, timeout=18.0)
            if meta and (meta.get("is_live") or meta.get("live_status") == "is_live"):
                vid = str(meta.get("id") or "")
                return {
                    "is_live": True,
                    "stream_title": str(meta.get("title") or ""),
                    "viewer_count": int(meta["concurrent_view_count"]) if meta.get("concurrent_view_count") is not None else None,
                    "video_id": vid or None,
                    "stream_target": f"https://www.youtube.com/watch?v={vid}" if vid else target,
                    "error": None,
                }
            return {"is_live": False, "stream_title": "", "viewer_count": None, "video_id": None, "stream_target": target, "error": None}
    except Exception as exc:
        return {
            "is_live": False,
            "stream_title": "",
            "viewer_count": None,
            "video_id": None,
            "stream_target": channel_url,
            "error": str(exc),
        }


async def resolve_stream_urls(stream_target: str, timeout: float = 25.0) -> tuple[Optional[str], Optional[str]]:
    """Resolve playable media URL(s) via yt-dlp primary, with optional streamlink fallback."""
    cmd = [
        *_yt_dlp_base_cmd(),
        "-g",
        "--no-warnings",
        "--no-playlist",
        "-f",
        "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        stream_target,
    ]
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_kwargs(),
        )
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0 and stdout:
            urls = [line.strip() for line in stdout.decode("utf-8", errors="replace").splitlines() if line.strip()]
            if len(urls) >= 2:
                return urls[0], urls[1]
            if len(urls) == 1:
                return urls[0], None
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        raise
    except Exception as exc:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        logger.debug("yt-dlp -g failed for %s: %s", stream_target, exc)

    streamlink_bin = shutil.which("streamlink")
    if streamlink_bin:
        sl_proc: Optional[asyncio.subprocess.Process] = None
        try:
            sl_proc = await asyncio.create_subprocess_exec(
                streamlink_bin,
                "--stream-url",
                stream_target,
                "1080p,720p,best",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_subprocess_kwargs(),
            )
            stdout, _ = await asyncio.wait_for(sl_proc.communicate(), timeout=timeout)
            if sl_proc.returncode == 0 and stdout:
                line = stdout.decode("utf-8", errors="replace").strip().splitlines()[-1].strip()
                if line.startswith("http"):
                    return line, None
        except asyncio.CancelledError:
            if sl_proc is not None and sl_proc.returncode is None:
                with contextlib.suppress(Exception):
                    sl_proc.kill()
                with contextlib.suppress(Exception):
                    await sl_proc.wait()
            raise
        except Exception as exc:
            if sl_proc is not None and sl_proc.returncode is None:
                with contextlib.suppress(Exception):
                    sl_proc.kill()
                with contextlib.suppress(Exception):
                    await sl_proc.wait()
            logger.debug("streamlink fallback failed for %s: %s", stream_target, exc)

    return None, None


async def _probe_stream_has_audio(url: str, timeout: float = 8.0) -> bool:
    """Check via ffprobe whether a stream/media URL contains an audio track.

    Returns True by default if probing times out or fails (to avoid dropping audio on slow live HLS).
    Returns False only when ffprobe succeeds and confirms there are no audio streams.
    """
    try:
        ffprobe = get_ffprobe_path()
    except Exception:
        return True
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "csv=p=0",
        url,
    ]
    proc: Optional[asyncio.subprocess.Process] = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_subprocess_kwargs(),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode == 0:
            return b"audio" in stdout.lower()
    except asyncio.CancelledError:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
        raise
    except Exception:
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(Exception):
                proc.kill()
            with contextlib.suppress(Exception):
                await proc.wait()
    return True


def build_live_ffmpeg_cmd(
    ffmpeg: str,
    video_url: str,
    audio_url: Optional[str],
    has_audio: bool,
    seg_pattern: str,
    csv_path: str,
    segment_seconds: int,
    segment_list_size: Optional[int] = None,
    include_reconnect: bool = True,
) -> list[str]:
    """Build the FFmpeg command for LiveStreamSession ring buffer (+ optional PCM audio pipe)."""
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "warning"]
    reconnect_args = (
        [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
        if include_reconnect and video_url.startswith(("http://", "https://"))
        else []
    )
    cmd.extend([*reconnect_args, "-i", video_url])
    if audio_url:
        audio_reconnect = (
            [
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
            ]
            if include_reconnect and audio_url.startswith(("http://", "https://"))
            else []
        )
        cmd.extend([*audio_reconnect, "-i", audio_url, "-map", "0:v:0", "-map", "1:a:0"])

    # Output 1: rolling MPEG-TS ring buffer
    seg_args = [
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-segment_format", "mpegts",
        "-segment_list", csv_path,
        "-segment_list_type", "csv",
    ]
    if segment_list_size is not None and segment_list_size > 0:
        seg_args.extend(["-segment_list_size", str(int(segment_list_size))])
    seg_args.extend(["-reset_timestamps", "1", seg_pattern])
    cmd.extend(seg_args)

    # Output 2: live 16kHz mono s16le PCM to stdout for audio energy spike detection (only if audio exists)
    if has_audio:
        if audio_url:
            cmd.extend(["-map", "1:a:0"])
        else:
            cmd.extend(["-map", "0:a:0"])
        cmd.extend(["-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"])

    return cmd


@dataclass
class SegmentEntry:
    filename: str
    path: Path
    start_sec: float
    end_sec: float


class RollingBufferManager:
    """Manages bounded MPEG-TS rolling buffer segments indexed via FFmpeg segment CSV."""

    def __init__(self, buffer_dir: Path, buffer_minutes: int = 20, segment_seconds: int = 6) -> None:
        self.buffer_dir = buffer_dir
        self.buffer_minutes = max(2, int(buffer_minutes))
        self.segment_seconds = max(2, int(segment_seconds))
        self.playlist_path = self.buffer_dir / "segments.csv"
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self._cached_stats: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def cached_stats(self) -> tuple[float, float, float]:
        """Return last refreshed (earliest_sec, latest_sec, disk_mb) without blocking disk I/O."""
        return self._cached_stats

    def refresh_stats(self) -> tuple[float, float, float]:
        """Scan segments and disk usage in a single pass and update cached_stats."""
        segments = self.scan_segments()
        earliest = segments[0].start_sec if segments else 0.0
        latest = segments[-1].end_sec if segments else 0.0
        disk_mb = self.get_disk_usage_mb()
        self._cached_stats = (earliest, latest, disk_mb)
        return self._cached_stats

    def scan_segments(self) -> list[SegmentEntry]:
        """Parse FFmpeg segment CSV entries that currently exist on disk."""
        if not self.playlist_path.exists():
            return []
        entries: list[SegmentEntry] = []
        try:
            with self.playlist_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                reader = csv.reader(handle)
                for row in reader:
                    if len(row) < 3:
                        continue
                    fname = row[0].strip()
                    if not fname:
                        continue
                    try:
                        start_sec = float(row[1])
                        end_sec = float(row[2])
                    except ValueError:
                        continue
                    seg_path = (self.buffer_dir / fname).resolve()
                    if not seg_path.exists():
                        continue
                    entries.append(
                        SegmentEntry(
                            filename=fname,
                            path=seg_path,
                            start_sec=start_sec,
                            end_sec=end_sec,
                        )
                    )
        except OSError as exc:
            logger.debug("Failed scanning segment CSV %s: %s", self.playlist_path, exc)
        entries.sort(key=lambda item: item.start_sec)
        return entries

    def get_latest_time(self) -> float:
        segments = self.scan_segments()
        return segments[-1].end_sec if segments else 0.0

    def get_earliest_time(self) -> float:
        segments = self.scan_segments()
        return segments[0].start_sec if segments else 0.0

    def get_disk_usage_mb(self) -> float:
        total = 0
        for p in self.buffer_dir.glob("*.ts"):
            with contextlib.suppress(OSError):
                total += p.stat().st_size
        return round(total / (1024 * 1024), 2)

    def prune_old_segments(self) -> int:
        """Delete TS segments older than buffer_minutes from disk while leaving latest ring intact."""
        segments = self.scan_segments()
        if len(segments) <= 2:
            return 0
        max_end = segments[-1].end_sec
        keep_after = max(0.0, max_end - float(self.buffer_minutes * 60))
        removed = 0
        for seg in segments[:-2]:
            if seg.end_sec < keep_after:
                with contextlib.suppress(OSError):
                    seg.path.unlink()
                    removed += 1
        return removed

    def clear(self) -> None:
        """Remove buffered TS and CSV files for this channel directory."""
        if not self.buffer_dir.exists():
            return
        for item in self.buffer_dir.iterdir():
            if item.is_file():
                with contextlib.suppress(OSError):
                    item.unlink()

    async def extract_window(
        self,
        start_sec: float,
        end_sec: float,
        output_path: Path,
    ) -> tuple[float, float, Path]:
        """Losslessly concatenate buffered MPEG-TS slices covering [start_sec, end_sec] into MP4."""
        ffmpeg = get_ffmpeg_path()
        if not ffmpeg:
            raise RuntimeError("FFmpeg не найден")

        segments = self.scan_segments()
        if not segments:
            raise RuntimeError("Буфер трансляции пока пуст")

        latest_end = segments[-1].end_sec
        earliest_start = segments[0].start_sec
        target_start = max(earliest_start, float(start_sec))
        target_end = min(latest_end, float(end_sec))
        if target_end - target_start < 3.0:
            target_start = max(earliest_start, latest_end - max(10.0, float(end_sec - start_sec)))
            target_end = latest_end

        selected = [
            seg for seg in segments
            if seg.end_sec >= target_start - 0.5 and seg.start_sec <= target_end + 0.5
        ]
        if not selected:
            selected = segments[-max(1, min(len(segments), 8)):]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        concat_path = output_path.with_suffix(".concat.txt")
        lines = []
        for seg in selected:
            escaped = seg.path.as_posix().replace("'", "'\\''")
            lines.append(f"file '{escaped}'")
        concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        offset = max(0.0, target_start - selected[0].start_sec)
        clip_duration = max(3.0, target_end - target_start)

        # Check if first segment has AAC audio so we only apply aac_adtstoasc when appropriate
        bsf_args: list[str] = []
        try:
            seg_meta = await probe_media(selected[0].path)
            if seg_meta.get("has_audio") and str(seg_meta.get("audio_codec") or "").lower() == "aac":
                bsf_args = ["-bsf:a", "aac_adtstoasc"]
        except Exception:
            pass

        async def _run_extract_cmd(cmd_args: list[str]) -> tuple[int, bytes]:
            proc = await asyncio.create_subprocess_exec(
                *cmd_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_subprocess_kwargs(),
            )
            try:
                _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=60.0)
                return int(proc.returncode or 0), stderr_bytes
            except (asyncio.TimeoutError, asyncio.CancelledError):
                with contextlib.suppress(Exception):
                    proc.kill()
                with contextlib.suppress(Exception):
                    await proc.wait()
                raise

        copy_cmd = [
            ffmpeg,
            "-y",
            "-ss",
            f"{offset:.3f}",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-t",
            f"{clip_duration:.3f}",
            "-c",
            "copy",
            *bsf_args,
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        reencode_cmd = [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-ss",
            f"{offset:.3f}",
            "-t",
            f"{clip_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]

        try:
            rc, stderr = await _run_extract_cmd(copy_cmd)
            need_reencode = rc != 0 or not output_path.exists() or output_path.stat().st_size == 0
            if not need_reencode:
                try:
                    out_meta = await probe_media(output_path, require_video=True)
                    if float(out_meta.get("duration") or 0.0) <= 0.0:
                        need_reencode = True
                except Exception:
                    need_reencode = True

            if need_reencode:
                logger.warning(
                    "Stream-copy extract_window produced invalid/no-video output; falling back to fast re-encode"
                )
                with contextlib.suppress(OSError):
                    output_path.unlink()
                rc, stderr = await _run_extract_cmd(reencode_cmd)
                if rc != 0 or not output_path.exists() or output_path.stat().st_size == 0:
                    err = stderr.decode("utf-8", errors="replace")[-400:]
                    raise RuntimeError(f"Ошибка извлечения окна из буфера: {err}")
                out_meta = await probe_media(output_path, require_video=True)
                if float(out_meta.get("duration") or 0.0) <= 0.0:
                    raise RuntimeError("Извлечённый фрагмент буфера имеет нулевую длительность")
        finally:
            with contextlib.suppress(OSError):
                concat_path.unlink()

        return target_start, target_end, output_path


@dataclass
class PendingTrigger:
    peak_sec: float
    capture_at_sec: float
    start_sec: float
    end_sec: float
    reason: str
    score: float
    chat_spike: Optional[dict[str, Any]]
    audio_ratio: float


class LiveStreamSession:
    """Active live stream watcher: dual-output FFmpeg buffer/PCM + chat parser + auto-clipper."""

    def __init__(self, channel_id: str) -> None:
        self.channel_id = channel_id
        self.platform = "twitch"
        self.channel_url = ""
        self.channel_name = ""
        self.stream_target = ""
        self.stream_title = ""
        self.viewer_count: Optional[int] = None
        self.video_id: Optional[str] = None
        self.project_id: Optional[str] = None

        self.buffer_dir = LIVE_BUFFER_DIR / channel_id
        self.buffer_manager = RollingBufferManager(self.buffer_dir, buffer_minutes=20)
        self.chat_analyzer = ChatPulseAnalyzer(keep_seconds=600, warmup_seconds=45)

        self._stop_event = asyncio.Event()
        self._capture_lock = asyncio.Lock()
        self._main_task: Optional[asyncio.Task[None]] = None
        self._proc: Optional[asyncio.subprocess.Process] = None

        self._audio_db_history: collections.deque[tuple[int, float]] = collections.deque(maxlen=300)
        self._current_sec = 0
        self._last_clip_sec = -9999.0
        self._pending_triggers: list[PendingTrigger] = []
        self._clips_created = 0
        self._status = "starting"
        self._last_error: Optional[str] = None
        self._started_at: float = time.monotonic()
        self._clip_pre_sec: float = 25.0
        self._clip_post_sec: float = 15.0
        self._min_clip_gap_sec: int = 90

    def snapshot(self) -> dict[str, Any]:
        recent_audio = [round(db, 1) for _, db in list(self._audio_db_history)[-40:]]
        recent_chat = self.chat_analyzer.get_recent_series(self._current_sec, window_sec=40)
        earliest_t, latest_t, disk_mb = self.buffer_manager.cached_stats
        return {
            "channel_id": self.channel_id,
            "status": self._status,
            "stream_seconds": round(latest_t, 1),
            "buffer_span_sec": round(max(0.0, latest_t - earliest_t), 1),
            "buffer_disk_mb": disk_mb,
            "clips_created": self._clips_created,
            "audio_db_recent": recent_audio,
            "chat_rate_recent": recent_chat,
            "last_error": self._last_error,
        }

    def start(self) -> None:
        if self._main_task and not self._main_task.done():
            return
        self._stop_event.clear()
        self._main_task = asyncio.create_task(self._run(), name=f"live-session-{self.channel_id}")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._proc and self._proc.returncode is None:
            with contextlib.suppress(Exception):
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=4.0)
            if self._proc.returncode is None:
                with contextlib.suppress(Exception):
                    self._proc.kill()
        if self._main_task:
            self._main_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._main_task
        self._status = "offline"

    async def _load_channel(self) -> Optional[LiveChannel]:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(LiveChannel).where(LiveChannel.id == self.channel_id))
            return res.scalar_one_or_none()

    async def _update_channel_db(self, **fields: Any) -> None:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(LiveChannel).where(LiveChannel.id == self.channel_id))
            ch = res.scalar_one_or_none()
            if not ch:
                return
            for k, v in fields.items():
                setattr(ch, k, v)
            await db.commit()

    async def _run(self) -> None:
        try:
            ch = await self._load_channel()
            if not ch:
                return
            self.platform = ch.platform
            self.channel_url = ch.channel_url
            self.channel_name = ch.name
            self._clip_pre_sec = float(ch.clip_pre_sec)
            self._clip_post_sec = float(ch.clip_post_sec)
            self._min_clip_gap_sec = int(ch.min_clip_gap_sec)
            self.buffer_manager = RollingBufferManager(self.buffer_dir, buffer_minutes=ch.buffer_minutes)
            self.buffer_manager.clear()

            probe = await check_channel_live(ch.platform, ch.channel_url)
            if not probe.get("is_live"):
                await self._update_channel_db(
                    status="offline",
                    stream_title="",
                    viewer_count=None,
                    last_error=probe.get("error"),
                    last_checked_at=datetime.now(timezone.utc),
                )
                await ws_manager.emit_live_update(self.channel_id, "offline", runtime=self.snapshot())
                return

            self.stream_target = str(probe.get("stream_target") or ch.channel_url)
            self.stream_title = str(probe.get("stream_title") or "")
            self.viewer_count = probe.get("viewer_count")
            self.video_id = probe.get("video_id")

            video_url, audio_url = await resolve_stream_urls(self.stream_target)
            if not video_url:
                err = "Не удалось получить URL видеопотока через yt-dlp/streamlink"
                self._status = "error"
                self._last_error = err
                await self._update_channel_db(status="error", last_error=err, last_checked_at=datetime.now(timezone.utc))
                await ws_manager.emit_live_update(self.channel_id, "error", error=err, runtime=self.snapshot())
                return

            ffmpeg = get_ffmpeg_path()
            if not ffmpeg:
                raise RuntimeError("FFmpeg не найден")

            self.buffer_dir.mkdir(parents=True, exist_ok=True)
            seg_pattern = str(self.buffer_dir / "seg_%06d.ts")
            csv_path = str(self.buffer_manager.playlist_path)

            has_audio = True if audio_url else await _probe_stream_has_audio(video_url)
            if not has_audio:
                logger.info("Live Monitor: поток %s без аудиодорожки — отключаем PCM-анализ", ch.name)

            seg_list_size = max(
                20,
                (self.buffer_manager.buffer_minutes * 60 // self.buffer_manager.segment_seconds) + 20,
            )
            cmd = build_live_ffmpeg_cmd(
                ffmpeg=ffmpeg,
                video_url=video_url,
                audio_url=audio_url,
                has_audio=has_audio,
                seg_pattern=seg_pattern,
                csv_path=csv_path,
                segment_seconds=self.buffer_manager.segment_seconds,
                segment_list_size=seg_list_size,
            )

            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                **_subprocess_kwargs(),
            )
            self._status = "recording"
            self._last_error = None
            await self._update_channel_db(
                status="recording",
                stream_title=self.stream_title,
                viewer_count=self.viewer_count,
                last_error=None,
                last_checked_at=datetime.now(timezone.utc),
            )
            await ws_manager.emit_live_update(
                self.channel_id,
                "recording",
                stream_title=self.stream_title,
                viewer_count=self.viewer_count,
                runtime=self.snapshot(),
            )
            await ws_manager.log(
                f"Live Monitor: началась запись {ch.name} ({ch.platform}, режим {ch.ai_mode})",
                stage="live",
            )

            audio_task = asyncio.create_task(self._audio_reader_loop(), name=f"live-audio-{self.channel_id}")
            stderr_task = asyncio.create_task(self._stderr_drain_loop(), name=f"live-stderr-{self.channel_id}")
            chat_task = asyncio.create_task(self._chat_loop(), name=f"live-chat-{self.channel_id}")
            detect_task = asyncio.create_task(self._detector_loop(), name=f"live-detect-{self.channel_id}")

            proc_wait_task = asyncio.create_task(self._proc.wait())
            stop_wait_task = asyncio.create_task(self._stop_event.wait())
            await asyncio.wait(
                [proc_wait_task, stop_wait_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            self._stop_event.set()
            for task in (audio_task, stderr_task, chat_task, detect_task, proc_wait_task, stop_wait_task):
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._status = "error"
            self._last_error = str(exc)
            logger.exception("Live session %s crashed", self.channel_id)
            await self._update_channel_db(status="error", last_error=str(exc))
            await ws_manager.emit_live_update(self.channel_id, "error", error=str(exc), runtime=self.snapshot())
        finally:
            if self._proc and self._proc.returncode is None:
                with contextlib.suppress(Exception):
                    self._proc.terminate()
            if self._status != "error":
                self._status = "offline"
                await self._update_channel_db(status="offline")
                await ws_manager.emit_live_update(self.channel_id, "offline", runtime=self.snapshot())

    async def _audio_reader_loop(self) -> None:
        if not self._proc or not self._proc.stdout:
            return
        bytes_per_sec = 16000 * 2
        while not self._stop_event.is_set():
            try:
                chunk = await self._proc.stdout.readexactly(bytes_per_sec)
            except (asyncio.IncompleteReadError, EOFError):
                break
            samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
            if samples.size == 0:
                continue
            rms = float(np.sqrt(np.mean(np.square(samples)))) / 32768.0
            db = 20.0 * math.log10(max(rms, 1e-6))
            self._current_sec += 1
            self._audio_db_history.append((self._current_sec, db))

    async def _stderr_drain_loop(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        while not self._stop_event.is_set():
            line = await self._proc.stderr.readline()
            if not line:
                break
            msg = line.decode("utf-8", errors="replace").strip()
            if msg:
                logger.debug("[live:%s ffmpeg] %s", self.channel_id, msg)

    def _get_current_sec(self) -> int:
        latest = int(self.buffer_manager.get_latest_time())
        return max(self._current_sec, latest)

    def _on_chat_clip_trigger(self, sec: int, text: str) -> None:
        now_sec = max(self.buffer_manager.get_latest_time(), float(sec), float(self._current_sec))
        if now_sec - self._last_clip_sec < self._min_clip_gap_sec:
            return
        snippet = (text or "").strip()[:32]
        self._schedule_trigger(
            peak_sec=now_sec,
            pre_sec=self._clip_pre_sec,
            post_sec=min(8.0, self._clip_post_sec),
            reason=f"Команда зрителя в чате ({snippet})" if snippet else "Команда зрителя в чате (!clip)",
            score=82.0,
            chat_spike=None,
            audio_ratio=1.0,
        )

    async def _chat_loop(self) -> None:
        if self.platform == "twitch":
            login = extract_twitch_login(self.channel_url)
            if login:
                await run_twitch_chat_reader(
                    login,
                    self._get_current_sec,
                    self.chat_analyzer,
                    self._stop_event,
                    on_clip_trigger=self._on_chat_clip_trigger,
                )
        elif self.platform == "kick":
            slug = extract_kick_slug(self.channel_url)
            if slug:
                await run_kick_chat_reader(
                    slug,
                    self._get_current_sec,
                    self.chat_analyzer,
                    self._stop_event,
                    on_clip_trigger=self._on_chat_clip_trigger,
                )
        elif self.platform == "youtube" and self.video_id:
            await run_youtube_chat_reader(
                self.video_id,
                self._get_current_sec,
                self.chat_analyzer,
                self._stop_event,
                on_clip_trigger=self._on_chat_clip_trigger,
            )

    def _compute_audio_jump_ratio(self) -> tuple[float, float]:
        if len(self._audio_db_history) < 18:
            return 1.0, -60.0
        vals = [db for _, db in self._audio_db_history]
        baseline_amps = [10 ** (db / 20.0) for db in vals[:-8]]
        recent_amps = [10 ** (db / 20.0) for db in vals[-5:]]
        base_mean = sum(baseline_amps) / max(1, len(baseline_amps))
        recent_mean = sum(recent_amps) / max(1, len(recent_amps))
        recent_db = sum(vals[-5:]) / max(1, len(vals[-5:]))
        return (recent_mean / max(base_mean, 1e-5)), recent_db

    def _schedule_trigger(
        self,
        peak_sec: float,
        pre_sec: float,
        post_sec: float,
        reason: str,
        score: float,
        chat_spike: Optional[dict[str, Any]],
        audio_ratio: float,
    ) -> None:
        for existing in self._pending_triggers:
            if abs(existing.peak_sec - peak_sec) < 30.0:
                return
        self._pending_triggers.append(
            PendingTrigger(
                peak_sec=peak_sec,
                capture_at_sec=peak_sec + post_sec,
                start_sec=max(0.0, peak_sec - pre_sec),
                end_sec=peak_sec + post_sec,
                reason=reason,
                score=score,
                chat_spike=chat_spike,
                audio_ratio=audio_ratio,
            )
        )

    async def _detector_loop(self) -> None:
        tick = 0
        while not self._stop_event.is_set():
            await asyncio.sleep(1.0)
            tick += 1
            if tick % 10 == 0:
                await asyncio.to_thread(self.buffer_manager.prune_old_segments)

            ch = await self._load_channel()
            if not ch:
                continue
            self._clip_pre_sec = float(ch.clip_pre_sec)
            self._clip_post_sec = float(ch.clip_post_sec)
            self._min_clip_gap_sec = int(ch.min_clip_gap_sec)

            cur_sec = self._get_current_sec()
            _, latest_buf_sec, _ = await asyncio.to_thread(self.buffer_manager.refresh_stats)
            audio_ratio, recent_db = self._compute_audio_jump_ratio()

            chat_spike = self.chat_analyzer.evaluate_pulse(
                cur_sec,
                sensitivity=ch.chat_sensitivity,
                min_gap_sec=float(ch.min_clip_gap_sec),
            )
            audio_spiked = audio_ratio >= ch.audio_sensitivity and recent_db >= -32.0

            if (cur_sec - self._last_clip_sec) >= ch.min_clip_gap_sec and latest_buf_sec >= 15.0:
                if chat_spike and audio_spiked:
                    self._schedule_trigger(
                        peak_sec=float(cur_sec),
                        pre_sec=ch.clip_pre_sec,
                        post_sec=ch.clip_post_sec,
                        reason=f"Всплеск чата ({chat_spike.rate:.1f} сообщ/с) + громкость ({audio_ratio:.1f}x)",
                        score=min(98.0, max(chat_spike.score, 78.0)),
                        chat_spike=chat_spike.to_dict(),
                        audio_ratio=audio_ratio,
                    )
                elif chat_spike and chat_spike.ratio >= ch.chat_sensitivity * 1.25:
                    self._schedule_trigger(
                        peak_sec=float(cur_sec),
                        pre_sec=ch.clip_pre_sec,
                        post_sec=ch.clip_post_sec,
                        reason=f"Реакция чата ({chat_spike.rate:.1f} сообщ/с, эмоции/хайп {chat_spike.emote_hits})",
                        score=chat_spike.score,
                        chat_spike=chat_spike.to_dict(),
                        audio_ratio=audio_ratio,
                    )
                elif audio_spiked and audio_ratio >= ch.audio_sensitivity * 1.4:
                    self._schedule_trigger(
                        peak_sec=float(cur_sec),
                        pre_sec=ch.clip_pre_sec,
                        post_sec=ch.clip_post_sec,
                        reason=f"Мощный аудио-пик ({audio_ratio:.1f}x, {recent_db:.0f} dBFS)",
                        score=min(90.0, 62.0 + audio_ratio * 8.0),
                        chat_spike=None,
                        audio_ratio=audio_ratio,
                    )

            # Drain matured pending triggers
            ready: list[PendingTrigger] = []
            waiting: list[PendingTrigger] = []
            for trig in self._pending_triggers:
                if latest_buf_sec >= trig.capture_at_sec + 2.0:
                    ready.append(trig)
                else:
                    waiting.append(trig)
            self._pending_triggers = waiting

            for trig in ready:
                if (latest_buf_sec - self._last_clip_sec) < ch.min_clip_gap_sec:
                    continue
                try:
                    await self.capture_clip_window(
                        start_sec=trig.start_sec,
                        end_sec=trig.end_sec,
                        reason=trig.reason,
                        score=trig.score,
                        chat_spike=trig.chat_spike,
                        audio_ratio=trig.audio_ratio,
                    )
                    self._last_clip_sec = latest_buf_sec
                except Exception as exc:
                    logger.warning("Live auto-clip capture failed for %s: %s", self.channel_id, exc)
                    await ws_manager.log(
                        f"Live Monitor [{ch.name}]: ошибка авто-нарезки клипа — {exc}",
                        level="warning",
                        stage="live",
                    )

            if tick % 2 == 0:
                await ws_manager.emit_live_update(
                    self.channel_id,
                    self._status,
                    stream_title=self.stream_title,
                    viewer_count=self.viewer_count,
                    runtime=self.snapshot(),
                )

    async def clip_last_seconds(self, seconds: float = 45.0) -> dict[str, Any]:
        """Manually slice the last N seconds from the rolling buffer into a full Live Clip job."""
        _, latest, _ = await asyncio.to_thread(self.buffer_manager.refresh_stats)
        if latest <= 3.0:
            raise RuntimeError("Буфер ещё не накопил достаточно видео (подождите 6–10 секунд)")
        start_sec = max(0.0, latest - float(seconds))
        return await self.capture_clip_window(
            start_sec=start_sec,
            end_sec=latest,
            reason=f"Ручной клип из Live Monitor ({int(seconds)}с)",
            score=90.0,
            chat_spike=None,
            audio_ratio=1.0,
        )

    async def capture_clip_window(
        self,
        start_sec: float,
        end_sec: float,
        reason: str,
        score: float,
        chat_spike: Optional[dict[str, Any]] = None,
        audio_ratio: float = 1.0,
    ) -> dict[str, Any]:
        """Cut an MP4 slice from the ring buffer, register Project+Clip in SQLite, and queue processing."""
        async with self._capture_lock:
            ch = await self._load_channel()
            if not ch:
                raise RuntimeError("Канал не найден")
            async with AsyncSessionLocal() as db:
                settings = await load_settings(db)

            project_id = new_id()
            clip_id = new_id()
            proj_dir = get_project_dir(project_id)
            slices_dir = proj_dir / "source"
            slices_dir.mkdir(parents=True, exist_ok=True)
            slice_path = slices_dir / f"live_{clip_id}.mp4"

            actual_start, actual_end, slice_path = await self.buffer_manager.extract_window(
                start_sec=start_sec,
                end_sec=end_sec,
                output_path=slice_path,
            )
            probe = await probe_media(slice_path, require_video=True)
            dur = max(3.0, float(probe.get("duration") or (actual_end - actual_start)))

            stamp = datetime.now().strftime("%d.%m %H:%M:%S")
            clip_title = f"{ch.name}: {reason[:64]}"
            proj_title = f"LIVE · {ch.name} · {stamp}"
            anti_cid_cfg = {
                "zoom": settings.default_anti_cid_zoom,
                "speed": settings.default_anti_cid_speed,
                "brightness": settings.default_anti_cid_brightness,
                "contrast": settings.default_anti_cid_contrast,
                "saturation": settings.default_anti_cid_saturation,
                "strip_metadata": settings.default_anti_cid_strip_metadata,
            }
            async with AsyncSessionLocal() as db:
                proj = Project(
                    id=project_id,
                    title=proj_title,
                    source_type="live",
                    source_url=ch.channel_url,
                    source_path=str(slice_path),
                    duration=dur,
                    width=int(probe.get("width") or 1920),
                    height=int(probe.get("height") or 1080),
                    fps=float(probe.get("fps") or 30.0),
                    video_codec=probe.get("video_codec"),
                    audio_codec=probe.get("audio_codec"),
                    has_audio=bool(probe.get("has_audio", True)),
                    status="ready",
                    analysis_json={
                        "live_channel_id": ch.id,
                        "channel_name": ch.name,
                        "platform": ch.platform,
                        "stream_title": self.stream_title,
                        "chat_spikes": [chat_spike] if chat_spike else [],
                    },
                )
                db.add(proj)
                await db.flush()

                ch_row = await db.get(LiveChannel, ch.id)
                if ch_row is not None:
                    ch_row.active_project_id = project_id

                clip = Clip(
                    id=clip_id,
                    project_id=project_id,
                    title=clip_title,
                    hook_text=reason[:90],
                    hook_enabled=True,
                    virality_score=round(float(score), 1),
                    reason=reason,
                    category="live_spike",
                    start_time=0.0,
                    end_time=dur,
                    crop_mode=ch.crop_mode,
                    subtitles_enabled=True,
                    subtitle_preset_id=ch.subtitle_preset_id,
                    ad_preset_id=ch.ad_preset_id,
                    background_asset_id=ch.background_asset_id,
                    anti_cid_enabled=ch.anti_cid_enabled,
                    anti_cid_json=anti_cid_cfg,
                    status="draft",
                    selected_for_export=True,
                    tags_json=[ch.platform, ch.name.lower(), "live", "shorts"],
                    description=f"Live highlight from {ch.name} ({ch.platform}): {reason}",
                )
                db.add(clip)
                await db.commit()

            self.project_id = project_id
            job = await queue_manager.enqueue(
                "live_process_clip",
                payload={
                    "channel_id": self.channel_id,
                    "clip_id": clip_id,
                    "slice_path": str(slice_path),
                    "ai_mode": ch.ai_mode,
                    "chat_spike": chat_spike,
                    "audio_ratio": round(audio_ratio, 2),
                },
                project_id=project_id,
                clip_id=clip_id,
                message=f"Live-клип [{ch.name}] поставлен в обработку...",
            )
            job_id = job.id
            self._clips_created += 1
            await ws_manager.emit_project_update(project_id, "ready")
            await ws_manager.emit_clip_update(clip_id, project_id, "draft")
            await ws_manager.emit_live_update(self.channel_id, self._status, runtime=self.snapshot())
            await ws_manager.log(f"Live-клип создан: {clip_title} (job {job_id})", stage="live", job_id=job_id)
            return {"clip_id": clip_id, "project_id": project_id, "job_id": job_id}


class LiveWatcherManager:
    """Global manager overseeing configured live channels and rolling buffer sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, LiveStreamSession] = {}
        self._poll_task: Optional[asyncio.Task[None]] = None
        self._stop_event = asyncio.Event()
        self._last_checked: dict[str, float] = {}

    def get_runtime_state(self, channel_id: str) -> dict[str, Any]:
        sess = self._sessions.get(channel_id)
        if sess:
            return sess.snapshot()
        return {
            "channel_id": channel_id,
            "status": "offline",
            "stream_seconds": 0.0,
            "buffer_span_sec": 0.0,
            "buffer_disk_mb": 0.0,
            "clips_created": 0,
            "audio_db_recent": [],
            "chat_rate_recent": [],
            "last_error": None,
        }

    async def start(self) -> None:
        if self._poll_task and not self._poll_task.done():
            return
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(self._poll_loop(), name="live-watcher-poll")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        for sess in list(self._sessions.values()):
            await sess.stop()
        self._sessions.clear()

    async def start_channel(self, channel_id: str) -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(LiveChannel).where(LiveChannel.id == channel_id))
            ch = res.scalar_one_or_none()
            if not ch:
                raise ValueError("Канал не найден")
            ch.enabled = True
            await db.commit()

        existing = self._sessions.get(channel_id)
        if existing and existing._main_task and not existing._main_task.done():
            return existing.snapshot()

        sess = LiveStreamSession(channel_id)
        self._sessions[channel_id] = sess
        sess.start()
        return sess.snapshot()

    async def stop_channel(self, channel_id: str) -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(LiveChannel).where(LiveChannel.id == channel_id))
            ch = res.scalar_one_or_none()
            if ch:
                ch.enabled = False
                ch.status = "offline"
                await db.commit()

        sess = self._sessions.pop(channel_id, None)
        if sess:
            await sess.stop()
        await ws_manager.emit_live_update(channel_id, "offline", runtime=self.get_runtime_state(channel_id))
        return self.get_runtime_state(channel_id)

    async def check_channel_now(self, channel_id: str) -> dict[str, Any]:
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(LiveChannel).where(LiveChannel.id == channel_id))
            ch = res.scalar_one_or_none()
            if not ch:
                raise ValueError("Канал не найден")
            platform = ch.platform
            url = ch.channel_url
            enabled = ch.enabled

        probe = await check_channel_live(platform, url)
        sess = self._sessions.get(channel_id)
        is_recording = bool(sess and sess._main_task and not sess._main_task.done() and sess._status == "recording")

        new_status = "recording" if is_recording else ("online" if probe["is_live"] else ("error" if probe.get("error") else "offline"))
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(LiveChannel).where(LiveChannel.id == channel_id))
            ch = res.scalar_one_or_none()
            if ch:
                ch.status = new_status
                ch.stream_title = str(probe.get("stream_title") or "")
                ch.viewer_count = probe.get("viewer_count")
                ch.last_error = probe.get("error")
                ch.last_checked_at = datetime.now(timezone.utc)
                await db.commit()

        if probe["is_live"] and enabled and not is_recording:
            await self.start_channel(channel_id)
        else:
            await ws_manager.emit_live_update(channel_id, new_status, runtime=self.get_runtime_state(channel_id))
        return probe

    async def clip_now(self, channel_id: str, seconds: float = 45.0) -> dict[str, Any]:
        sess = self._sessions.get(channel_id)
        if not sess or sess._status != "recording":
            raise RuntimeError("Канал сейчас не записывается")
        return await sess.clip_last_seconds(seconds)

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                async with AsyncSessionLocal() as db:
                    res = await db.execute(select(LiveChannel).where(LiveChannel.enabled == True))  # noqa: E712
                    channels = list(res.scalars().all())

                now = time.monotonic()
                for ch in channels:
                    if self._stop_event.is_set():
                        break
                    sess = self._sessions.get(ch.id)
                    if sess and sess._main_task and not sess._main_task.done():
                        continue
                    last = self._last_checked.get(ch.id, 0.0)
                    if now - last < max(15, int(ch.check_interval_sec)):
                        continue
                    self._last_checked[ch.id] = now
                    try:
                        probe = await check_channel_live(ch.platform, ch.channel_url)
                        async with AsyncSessionLocal() as db:
                            row_res = await db.execute(select(LiveChannel).where(LiveChannel.id == ch.id))
                            row = row_res.scalar_one_or_none()
                            if row:
                                row.status = (
                                    "online"
                                    if probe["is_live"]
                                    else ("error" if probe.get("error") else "offline")
                                )
                                row.stream_title = str(probe.get("stream_title") or "")
                                row.viewer_count = probe.get("viewer_count")
                                row.last_error = probe.get("error")
                                row.last_checked_at = datetime.now(timezone.utc)
                                await db.commit()
                        if probe["is_live"]:
                            await self.start_channel(ch.id)
                    except Exception as poll_exc:
                        logger.warning("Live poll failed for channel %s: %s", ch.id, poll_exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug("Live poll tick warning: %s", exc)

            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                continue


live_watcher = LiveWatcherManager()
