from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Optional

from ...core.config import get_ffmpeg_path

logger = logging.getLogger("narezki.encoder")

CANDIDATE_ENCODERS = [
    "h264_nvenc",
    "hevc_nvenc",
    "h264_amf",
    "h264_qsv",
    "libx264",
]

_cached_probe: Optional[dict[str, Any]] = None
_probe_lock = asyncio.Lock()


async def _test_encoder(ffmpeg_exe: str, encoder: str) -> bool:
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x256:d=0.04",
        "-frames:v",
        "1",
        "-c:v",
        encoder,
        "-f",
        "null",
        "-",
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **kwargs,
        )
        try:
            await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return False
        return proc.returncode == 0
    except Exception as exc:
        logger.debug("Encoder probe error for %s: %s", encoder, exc)
        return False


async def probe_encoders(force: bool = False) -> dict[str, Any]:
    """Probe candidate H.264/HEVC encoders with a real 1-frame FFmpeg run."""
    global _cached_probe
    async with _probe_lock:
        if _cached_probe is not None and not force:
            return _cached_probe

        try:
            ffmpeg_exe = get_ffmpeg_path()
        except FileNotFoundError as exc:
            logger.warning("Cannot probe encoders: %s", exc)
            _cached_probe = {
                "selected": "libx264",
                "available": [],
                "details": {enc: False for enc in CANDIDATE_ENCODERS},
            }
            return _cached_probe

        details: dict[str, bool] = {}
        available: list[str] = []
        for enc in CANDIDATE_ENCODERS:
            ok = await _test_encoder(ffmpeg_exe, enc)
            details[enc] = ok
            if ok:
                available.append(enc)

        selected = available[0] if available else "libx264"
        _cached_probe = {
            "selected": selected,
            "available": available,
            "details": details,
        }
        logger.info("Encoder probe complete: selected=%s available=%s", selected, available)
        return _cached_probe


async def resolve_encoder(preferred: str = "auto") -> str:
    """Resolve user preference ('auto' or explicit encoder) to a verified working encoder."""
    probe = await probe_encoders()
    available = probe["available"]
    if preferred != "auto" and preferred in available:
        return preferred
    if "h264_nvenc" in available:
        return "h264_nvenc"
    for enc in CANDIDATE_ENCODERS:
        if enc in available:
            return enc
    return "libx264"


def build_encoder_args(encoder: str, quality_cq: int = 21, fast_proxy: bool = False) -> list[str]:
    """Build FFmpeg video encoder arguments tailored to the chosen hardware/software codec."""
    cq = max(12, min(35, int(quality_cq)))

    if encoder == "h264_nvenc":
        preset = "p1" if fast_proxy else "p5"
        args = [
            "-c:v",
            "h264_nvenc",
            "-preset",
            preset,
            "-rc",
            "vbr",
            "-cq",
            str(cq),
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
        if not fast_proxy:
            args.extend(["-spatial-aq", "1", "-temporal-aq", "1"])
        return args

    if encoder == "hevc_nvenc":
        preset = "p1" if fast_proxy else "p5"
        return [
            "-c:v",
            "hevc_nvenc",
            "-preset",
            preset,
            "-rc",
            "vbr",
            "-cq",
            str(cq),
            "-b:v",
            "0",
            "-tag:v",
            "hvc1",
            "-pix_fmt",
            "yuv420p",
        ]

    if encoder == "h264_amf":
        return [
            "-c:v",
            "h264_amf",
            "-quality",
            "speed" if fast_proxy else "quality",
            "-rc",
            "cqp",
            "-qp_i",
            str(cq),
            "-qp_p",
            str(cq),
            "-pix_fmt",
            "yuv420p",
        ]

    if encoder == "h264_qsv":
        return [
            "-c:v",
            "h264_qsv",
            "-preset",
            "veryfast" if fast_proxy else "medium",
            "-global_quality",
            str(cq),
            "-pix_fmt",
            "yuv420p",
        ]

    # Software fallback: libx264
    return [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast" if fast_proxy else "medium",
        "-crf",
        str(cq),
        "-pix_fmt",
        "yuv420p",
    ]
