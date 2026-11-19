from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from ...core.config import get_ffprobe_path


def _parse_fps(rate_str: str | None) -> float:
    if not rate_str or rate_str in ("0/0", "N/A"):
        return 30.0
    if "/" in rate_str:
        num_s, den_s = rate_str.split("/", 1)
        try:
            num = float(num_s)
            den = float(den_s)
            if den > 0:
                val = num / den
                if 1.0 <= val <= 240.0:
                    return round(val, 3)
        except ValueError:
            pass
        return 30.0
    try:
        val = float(rate_str)
        return round(val, 3) if 1.0 <= val <= 240.0 else 30.0
    except ValueError:
        return 30.0


async def probe_media(path: str | Path) -> dict[str, Any]:
    """Run ffprobe asynchronously and return normalized stream and container metadata."""
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Файл не найден: {target}")

    ffprobe_exe = get_ffprobe_path()
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    cmd = [
        ffprobe_exe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(target),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"FFprobe не смог прочитать файл {target.name}: {err}")

    raw = json.loads(stdout.decode("utf-8", errors="replace"))
    streams = raw.get("streams", [])
    fmt = raw.get("format", {})

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration") or 0.0)
    if duration <= 0.0 and video_stream:
        duration = float(video_stream.get("duration") or 0.0)

    width = int(video_stream.get("width") or 0) if video_stream else 0
    height = int(video_stream.get("height") or 0) if video_stream else 0
    fps = (
        _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        if video_stream
        else 30.0
    )

    return {
        "duration": round(duration, 3),
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": video_stream.get("codec_name") if video_stream else None,
        "audio_codec": audio_stream.get("codec_name") if audio_stream else None,
        "has_audio": audio_stream is not None,
        "bitrate": int(fmt.get("bit_rate") or 0),
        "size_bytes": int(fmt.get("size") or target.stat().st_size),
    }
