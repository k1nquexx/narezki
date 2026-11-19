from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ...core.config import get_ffmpeg_path
from ..renderer.encoder_detect import build_encoder_args, resolve_encoder

logger = logging.getLogger("narezki.proxy")

ProgressCallback = Callable[[float, str], Awaitable[None]]


async def generate_thumbnail(source_path: Path, thumb_path: Path, seek_sec: float = 1.5) -> Path:
    """Extract a crisp JPEG thumbnail frame from the video."""
    ffmpeg_exe = get_ffmpeg_path()
    thumb_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    for ss in (seek_sec, 0.0):
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{max(0.0, ss):.2f}",
            "-i",
            str(source_path),
            "-frames:v",
            "1",
            "-vf",
            "scale=640:-2:flags=lanczos",
            "-q:v",
            "3",
            str(thumb_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        await proc.communicate()
        if proc.returncode == 0 and thumb_path.exists() and thumb_path.stat().st_size > 0:
            return thumb_path

    raise RuntimeError(f"Не удалось создать превью-кадр для {source_path.name}")


async def generate_proxy(
    source_path: Path,
    proxy_path: Path,
    duration: float,
    preferred_encoder: str = "auto",
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> Path:
    """Create a fast-seeking 720p H.264 proxy MP4 for responsive Studio timeline scrubbing."""
    ffmpeg_exe = get_ffmpeg_path()
    proxy_path.parent.mkdir(parents=True, exist_ok=True)

    encoder = await resolve_encoder(preferred_encoder)
    # Keep H.264 for maximum browser/WebView2 video tag compatibility
    if encoder == "hevc_nvenc":
        encoder = "h264_nvenc"

    enc_args = build_encoder_args(encoder, quality_cq=25, fast_proxy=True)

    # Try hardware encoder first, fallback to libx264 ultrafast if driver rejects format
    for attempt_enc, attempt_args in (
        (encoder, enc_args),
        ("libx264", build_encoder_args("libx264", quality_cq=25, fast_proxy=True)),
    ):
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-i",
            str(source_path),
            "-vf",
            "scale=w='trunc(min(1280,iw)/2)*2':h=-2:force_original_aspect_ratio=decrease",
            "-g",
            "30",
            "-keyint_min",
            "30",
            *attempt_args,
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-progress",
            "pipe:1",
            "-nostats",
            str(proxy_path),
        ]
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

        async def _read_stderr() -> None:
            assert proc.stderr is not None
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                stderr_lines.append(line.decode("utf-8", errors="replace").strip())

        stderr_task = asyncio.create_task(_read_stderr())
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
                if line.startswith("out_time_us=") and duration > 0 and on_progress:
                    try:
                        us = int(line.split("=", 1)[1])
                        pct = min(99.0, max(0.0, (us / 1_000_000.0) / duration * 100.0))
                        now = loop.time()
                        if (pct - last_emit_pct >= 2.0) or (now - last_emit_t >= 0.4):
                            last_emit_t = now
                            last_emit_pct = pct
                            await on_progress(pct, f"Генерация прокси ({attempt_enc}): {int(pct)}%")
                    except ValueError:
                        pass
            await stderr_task
            rc = await proc.wait()
        finally:
            if not stderr_task.done():
                stderr_task.cancel()
            with suppress(asyncio.CancelledError):
                await stderr_task
            if unregister_proc:
                unregister_proc(proc)

        if rc == 0 and proxy_path.exists() and proxy_path.stat().st_size > 0:
            return proxy_path

        err_tail = " | ".join(stderr_lines[-6:])
        logger.warning("Proxy attempt with %s failed (rc=%s): %s", attempt_enc, rc, err_tail)
        if attempt_enc == "libx264":
            raise RuntimeError(f"Ошибка генерации прокси-видео: {err_tail}")

    raise RuntimeError("Не удалось сгенерировать прокси-видео")
