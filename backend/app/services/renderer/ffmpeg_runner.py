from __future__ import annotations

import asyncio
import logging
import re
import sys
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ...core.config import TEMP_DIR, get_ffmpeg_path
from ..ingest.ffprobe_utils import probe_media
from .ad_injector import (
    build_overlay_filtergraph,
    build_overlay_input_args,
    build_video_ad_splice_filtergraph,
    resolve_ad_params,
)
from .anti_cid import build_anti_cid_filtergraph
from .crop_filters import build_crop_filtergraph
from .encoder_detect import build_encoder_args, resolve_encoder
from .subtitle_ass import format_ass_filter_path, generate_ass_file

logger = logging.getLogger("narezki.ffmpeg")

ProgressCallback = Callable[[float, str], Awaitable[None]]

_CACHED_FILTER_SCRIPT_FLAG: Optional[str] = None


async def _get_filter_script_flag(ffmpeg_exe: str) -> str:
    """Detect whether FFmpeg expects -/filter_complex (FFmpeg 8+) or -filter_complex_script."""
    global _CACHED_FILTER_SCRIPT_FLAG
    if _CACHED_FILTER_SCRIPT_FLAG is not None:
        return _CACHED_FILTER_SCRIPT_FLAG

    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe,
        "-h",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **kwargs,
    )
    stdout, _ = await proc.communicate()
    help_text = stdout.decode("utf-8", errors="replace")
    if "-filter_complex_script" in help_text:
        _CACHED_FILTER_SCRIPT_FLAG = "-filter_complex_script"
    else:
        _CACHED_FILTER_SCRIPT_FLAG = "-/filter_complex"
    return _CACHED_FILTER_SCRIPT_FLAG


def sanitize_filename(name: str, fallback: str = "project") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", name).strip(" ._")
    return cleaned[:64] if cleaned else fallback


def build_companion_txt(clip_data: dict[str, Any], project_title: str = "") -> str:
    """Format companion .txt metadata file for manual TikTok / Shorts / Reels publishing."""
    tags = clip_data.get("tags_json") or []
    tags_str = " ".join(
        t if str(t).startswith("#") else f"#{t}" for t in tags if str(t).strip()
    )
    duration = max(0.0, float(clip_data.get("end_time", 0.0)) - float(clip_data.get("start_time", 0.0)))
    lines = [
        f"Заголовок: {clip_data.get('title', '')}",
        f"Хук (0-3с): {clip_data.get('hook_text', '')}",
        "",
        "Описание:",
        str(clip_data.get("description", "")).strip(),
        "",
        f"Хэштеги: {tags_str}",
        "",
        "--- Метаданные клипа ---",
        f"Проект: {project_title}",
        f"Категория: {clip_data.get('category', 'dialogue')}",
        f"Виральность: {clip_data.get('virality_score', 0)}/100",
        f"Таймкод в источнике: {float(clip_data.get('start_time', 0)):.2f}s - {float(clip_data.get('end_time', 0)):.2f}s (длительность {duration:.2f}s)",
        f"Кроп: {clip_data.get('crop_mode', 'auto_track')}",
        f"Уникализация (Anti-CID): {'Вкл' if clip_data.get('anti_cid_enabled') else 'Выкл'}",
    ]
    return "\n".join(lines).strip() + "\n"


async def run_ffmpeg_with_progress(
    cmd: list[str],
    total_duration: float,
    stage_label: str = "Рендер",
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> None:
    """Execute an FFmpeg command asynchronously, parsing -progress pipe:1 and supporting cancellation."""
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    if register_proc:
        register_proc(proc)

    stderr_lines: list[str] = []

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                stderr_lines.append(line)
                if len(stderr_lines) > 100:
                    stderr_lines.pop(0)

    stderr_task = asyncio.create_task(_drain_stderr())
    loop = asyncio.get_running_loop()
    last_emit_t = 0.0
    last_emit_pct = -10.0

    try:
        assert proc.stdout is not None
        while True:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                await proc.wait()
                raise asyncio.CancelledError()

            raw_line = await proc.stdout.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line.startswith("out_time_us=") and total_duration > 0 and on_progress:
                try:
                    us = int(line.split("=", 1)[1])
                    pct = min(99.0, max(0.0, (us / 1_000_000.0) / total_duration * 100.0))
                    now = loop.time()
                    if (pct - last_emit_pct >= 2.0) or (now - last_emit_t >= 0.4):
                        last_emit_t = now
                        last_emit_pct = pct
                        await on_progress(pct, f"{stage_label}: {int(pct)}%")
                except ValueError:
                    pass
            elif line == "progress=end" and on_progress:
                await on_progress(100.0, f"{stage_label}: завершено")

        await stderr_task
        rc = await proc.wait()
        if rc != 0:
            tail = "\n".join(stderr_lines[-15:])
            raise RuntimeError(f"FFmpeg завершился с кодом {rc}:\n{tail}")
    finally:
        if proc.returncode is None:
            with suppress(Exception):
                proc.kill()
            with suppress(Exception):
                await proc.wait()
        if not stderr_task.done():
            stderr_task.cancel()
        with suppress(asyncio.CancelledError):
            await stderr_task
        if unregister_proc:
            unregister_proc(proc)


async def render_basic_cut(
    source_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    preferred_encoder: str = "auto",
    quality_cq: int = 21,
    has_audio: bool = True,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> Path:
    """Frame-accurate cut of a source segment to MP4 using hardware encoder with libx264 fallback."""
    ffmpeg_exe = get_ffmpeg_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(0.1, float(end_time) - float(start_time))

    encoder = await resolve_encoder(preferred_encoder)
    attempts = [(encoder, build_encoder_args(encoder, quality_cq=quality_cq))]
    if encoder != "libx264":
        attempts.append(("libx264", build_encoder_args("libx264", quality_cq=quality_cq)))

    last_err: Exception | None = None
    for enc_name, enc_args in attempts:
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-ss",
            f"{start_time:.3f}",
            "-i",
            str(source_path),
            "-t",
            f"{duration:.3f}",
            *enc_args,
        ]
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"])
        else:
            cmd.extend(["-an"])
        cmd.extend(["-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)])

        try:
            await run_ffmpeg_with_progress(
                cmd=cmd,
                total_duration=duration,
                stage_label=f"Нарезка ({enc_name})",
                on_progress=on_progress,
                cancel_event=cancel_event,
                register_proc=register_proc,
                unregister_proc=unregister_proc,
            )
            if output_path.exists() and output_path.stat().st_size > 0:
                return output_path
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            last_err = exc
            logger.warning("Basic cut with %s failed: %s", enc_name, exc)

    raise RuntimeError(f"Не удалось выполнить нарезку: {last_err}")


def _scale_cues_for_speed(cues: list[dict[str, Any]], speed_factor: float) -> list[dict[str, Any]]:
    if abs(speed_factor - 1.0) <= 0.0005:
        return cues
    inv = 1.0 / speed_factor
    scaled: list[dict[str, Any]] = []
    for cue in cues:
        new_cue = dict(cue)
        new_cue["start"] = round(float(cue.get("start", 0.0)) * inv, 3)
        new_cue["end"] = round(float(cue.get("end", 0.0)) * inv, 3)
        words = cue.get("words") or []
        if words:
            new_cue["words"] = [
                {
                    **w,
                    "start": round(float(w.get("start", 0.0)) * inv, 3),
                    "end": round(float(w.get("end", 0.0)) * inv, 3),
                }
                for w in words
            ]
        scaled.append(new_cue)
    return scaled


async def render_clip(
    source_path: Path,
    source_width: int,
    source_height: int,
    source_has_audio: bool,
    clip_data: dict[str, Any],
    output_path: Path,
    subtitle_preset: Optional[dict[str, Any]] = None,
    ad_preset: Optional[dict[str, Any]] = None,
    background_path: Optional[Path] = None,
    preferred_encoder: str = "auto",
    quality_cq: int = 21,
    target_fps: int = 30,
    fast_preview: bool = False,
    source_duration: Optional[float] = None,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> Path:
    """Render a full 1080x1920 vertical clip with smart reframing, ASS subtitles, ads, and Anti-CID."""
    ffmpeg_exe = get_ffmpeg_path()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)

    raw_start = float(clip_data["start_time"])
    raw_end = float(clip_data["end_time"])
    if raw_end <= raw_start:
        raise ValueError(
            f"Некорректные таймкоды клипа: end_time ({raw_end:.3f}s) <= start_time ({raw_start:.3f}s)"
        )
    start_time = max(0.0, raw_start)
    end_time = raw_end

    # Ensure source dimensions and duration are valid
    src_dur = float(source_duration or 0.0)
    if source_width <= 0 or source_height <= 0 or src_dur <= 0.0:
        src_meta = await probe_media(source_path, require_video=True)
        if source_width <= 0 or source_height <= 0:
            source_width = int(src_meta.get("width") or 1920)
            source_height = int(src_meta.get("height") or 1080)
            source_has_audio = bool(src_meta.get("has_audio", True))
        if src_dur <= 0.0:
            src_dur = float(src_meta.get("duration") or 0.0)

    if src_dur > 0.0:
        if start_time >= src_dur - 0.1:
            raise ValueError(
                f"Начало клипа ({start_time:.2f}s) выходит за длительность исходного видео ({src_dur:.2f}s)"
            )
        end_time = min(end_time, src_dur)
        if end_time <= start_time:
            raise ValueError(
                f"Некорректный диапазон клипа ({start_time:.2f}s..{end_time:.2f}s) для видео длительностью {src_dur:.2f}s"
            )

    clip_dur = end_time - start_time

    temp_id = uuid.uuid4().hex[:10]
    ass_path: Optional[Path] = None
    filter_script_path = TEMP_DIR / f"filter_{temp_id}.txt"

    try:
        anti_cid_enabled = bool(clip_data.get("anti_cid_enabled"))
        speed_factor = 1.0
        if anti_cid_enabled:
            cid_cfg = clip_data.get("anti_cid_json") or {}
            speed_factor = max(0.95, min(1.10, float(cid_cfg.get("speed", 1.015))))
        main_dur = clip_dur / max(0.5, speed_factor)

        # 1. Build ASS subtitle + hook file if needed (in post-Anti-CID timebase)
        sub_enabled = bool(clip_data.get("subtitles_enabled", True))
        hook_enabled = bool(clip_data.get("hook_enabled", False))
        hook_text = str(clip_data.get("hook_text") or "").strip()
        cues = clip_data.get("subtitles_json") or []

        if (sub_enabled and cues) or (hook_enabled and hook_text):
            ass_path = TEMP_DIR / f"subs_{temp_id}.ass"
            scaled_cues = _scale_cues_for_speed(cues, speed_factor)
            generate_ass_file(
                cues=scaled_cues,
                preset=subtitle_preset or {},
                output_path=ass_path,
                subtitles_enabled=sub_enabled,
                hook_enabled=hook_enabled,
                hook_text=hook_text,
                clip_duration=main_dur,
            )

        # 2. Collect FFmpeg inputs
        input_args: list[str] = [
            "-ss",
            f"{start_time:.3f}",
            "-t",
            f"{clip_dur + 0.5:.3f}",
            "-i",
            str(source_path),
        ]
        next_idx = 1

        bg_in_label: Optional[str] = None
        crop_mode = str(clip_data.get("crop_mode") or "auto_track")
        if crop_mode == "split_screen" and background_path and background_path.exists():
            input_args.extend(
                ["-stream_loop", "-1", "-t", f"{clip_dur:.3f}", "-i", str(background_path)]
            )
            bg_in_label = f"{next_idx}:v"
            next_idx += 1

        # Ad preset inspection
        resolved_ad: Optional[dict[str, Any]] = None
        ov_idx: Optional[int] = None
        vad_idx: Optional[int] = None
        ad_meta: Optional[dict[str, Any]] = None

        if ad_preset and ad_preset.get("asset_path"):
            ad_file = Path(str(ad_preset["asset_path"]))
            if not ad_file.exists():
                raise FileNotFoundError(f"Файл рекламного пресета не найден: {ad_file}")
            resolved_ad = resolve_ad_params(ad_preset, clip_data.get("ad_override_json"))
            ad_type = str(resolved_ad.get("ad_type", ""))
            if ad_type == "overlay":
                ov_codec: Optional[str] = None
                if ad_file.suffix.lower() == ".webm":
                    try:
                        ov_meta = await probe_media(ad_file)
                        ov_codec = ov_meta.get("video_codec")
                    except Exception:
                        ov_codec = "vp9"
                input_args.extend(build_overlay_input_args(ad_file, main_dur, video_codec=ov_codec))
                ov_idx = next_idx
                next_idx += 1
            elif ad_type == "video_insert":
                ad_meta = await probe_media(ad_file)
                input_args.extend(["-i", str(ad_file)])
                vad_idx = next_idx
                next_idx += 1

        # 3. Assemble filter_complex_script lines
        fg: list[str] = [f"[0:v]trim=duration={clip_dur:.3f},setpts=PTS-STARTPTS[v_base]"]
        if source_has_audio:
            fg.append(
                f"[0:a]atrim=duration={clip_dur:.3f},asetpts=PTS-STARTPTS,"
                "aformat=sample_rates=48000:channel_layouts=stereo[a_base]"
            )
        else:
            fg.append(f"anullsrc=r=48000:cl=stereo:d={clip_dur:.3f}[a_base]")

        v_cur = "v_base"
        a_cur = "a_base"

        # 3a. Smart Reframing 9:16
        fg.extend(
            build_crop_filtergraph(
                crop_mode=crop_mode,
                source_w=source_width,
                source_h=source_height,
                target_fps=target_fps,
                track_data=clip_data.get("track_data_json"),
                v_in=v_cur,
                bg_in=bg_in_label,
                out_label="v_crop",
            )
        )
        v_cur = "v_crop"

        # 3b. Anti-Content ID (zoom, eq, speed+pitch) BEFORE burning subtitles & ad overlays
        extra_output_args: list[str] = []
        if anti_cid_enabled:
            cid_lines, extra_output_args, speed_factor = build_anti_cid_filtergraph(
                v_in=v_cur,
                a_in=a_cur,
                v_out="v_cid",
                a_out="a_cid",
                config=clip_data.get("anti_cid_json"),
                target_fps=target_fps,
            )
            fg.extend(cid_lines)
            v_cur = "v_cid"
            a_cur = "a_cid"
            main_dur = clip_dur / max(0.5, speed_factor)

        # 3c. Burn ASS subtitles + 3s hook banner
        if ass_path is not None:
            ass_filter = format_ass_filter_path(ass_path)
            fg.append(f"[{v_cur}]{ass_filter}[v_sub]")
            v_cur = "v_sub"

        # 3d. Mode 2 Ad Overlay (PNG/WebM/GIF)
        if ov_idx is not None and resolved_ad is not None:
            inv_speed = 1.0 / max(0.5, speed_factor)
            fg.extend(
                build_overlay_filtergraph(
                    v_in=v_cur,
                    ov_in=f"{ov_idx}:v",
                    out_label="v_ov",
                    overlay_x=float(resolved_ad.get("overlay_x", 50.0)),
                    overlay_y=float(resolved_ad.get("overlay_y", 22.0)),
                    overlay_scale=float(resolved_ad.get("overlay_scale", 35.0)),
                    overlay_start=float(resolved_ad.get("overlay_start", 3.0)) * inv_speed,
                    overlay_end=float(resolved_ad.get("overlay_end", 12.0)) * inv_speed,
                    fade_duration=float(resolved_ad.get("fade_duration", 0.4)),
                )
            )
            v_cur = "v_ov"

        # 3e. Audio loudness normalization on main segment (skip loudnorm on short/silent clips to avoid NaN in AAC)
        if source_has_audio and main_dur >= 1.5:
            fg.append(
                f"[{a_cur}]loudnorm=I=-14:TP=-1.5:LRA=11,"
                "aformat=sample_rates=48000:channel_layouts=stereo[a_norm]"
            )
        else:
            fg.append(
                f"[{a_cur}]aformat=sample_rates=48000:channel_layouts=stereo[a_norm]"
            )
        a_cur = "a_norm"

        # 3f. Mode 1 Video Ad Insert (mid_roll / outro)
        total_render_dur = main_dur
        if vad_idx is not None and resolved_ad is not None and ad_meta is not None:
            ad_dur = float(ad_meta.get("duration") or resolved_ad.get("duration") or 3.0)
            ad_has_audio = bool(ad_meta.get("has_audio", False))
            raw_insert_at = resolved_ad.get("insert_at_sec")
            scaled_insert_at = (
                float(raw_insert_at) / max(0.5, speed_factor)
                if raw_insert_at is not None
                else None
            )
            fg.extend(
                build_video_ad_splice_filtergraph(
                    v_main=v_cur,
                    a_main=a_cur,
                    ad_v_in=f"{vad_idx}:v",
                    ad_a_in=f"{vad_idx}:a" if ad_has_audio else None,
                    v_out="v_final",
                    a_out="a_final",
                    main_duration=main_dur,
                    ad_duration=ad_dur,
                    target_fps=target_fps,
                    insert_position=str(resolved_ad.get("insert_position", "outro")),
                    insert_at_sec=scaled_insert_at,
                )
            )
            v_cur = "v_final"
            a_cur = "a_final"
            total_render_dur += ad_dur

        filter_script_text = ";\n".join(fg)
        filter_script_path.write_text(filter_script_text, encoding="utf-8")

        encoder = await resolve_encoder(preferred_encoder)
        if fast_preview and encoder == "hevc_nvenc":
            encoder = "h264_nvenc"

        eff_cq = max(quality_cq, 24) if fast_preview else quality_cq
        attempts = [(encoder, build_encoder_args(encoder, quality_cq=eff_cq, fast_proxy=fast_preview))]
        if encoder != "libx264":
            attempts.append(
                ("libx264", build_encoder_args("libx264", quality_cq=eff_cq, fast_proxy=fast_preview))
            )

        filter_flag = await _get_filter_script_flag(ffmpeg_exe)
        last_err: Exception | None = None
        for enc_name, enc_args in attempts:
            cmd = [
                ffmpeg_exe,
                "-hide_banner",
                "-loglevel",
                "warning",
                "-y",
                *input_args,
                filter_flag,
                str(filter_script_path),
                "-map",
                f"[{v_cur}]",
                "-map",
                f"[{a_cur}]",
                *enc_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "48000",
                "-ac",
                "2",
                *extra_output_args,
                "-movflags",
                "+faststart",
                "-progress",
                "pipe:1",
                "-nostats",
                str(output_path),
            ]
            try:
                await run_ffmpeg_with_progress(
                    cmd=cmd,
                    total_duration=total_render_dur,
                    stage_label=f"Рендер 9:16 ({enc_name})",
                    on_progress=on_progress,
                    cancel_event=cancel_event,
                    register_proc=register_proc,
                    unregister_proc=unregister_proc,
                )
                if output_path.exists() and output_path.stat().st_size > 0:
                    out_meta = await probe_media(output_path, require_video=True)
                    if float(out_meta.get("duration") or 0.0) <= 0.0:
                        raise RuntimeError("Отрендеренный файл имеет нулевую длительность")
                    return output_path
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_err = exc
                logger.warning("Render with %s failed: %s", enc_name, exc)

        raise RuntimeError(f"Не удалось отрендерить клип: {last_err}")
    finally:
        if ass_path is not None:
            ass_path.unlink(missing_ok=True)
        filter_script_path.unlink(missing_ok=True)
