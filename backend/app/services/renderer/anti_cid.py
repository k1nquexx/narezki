from __future__ import annotations

from typing import Any, Optional


def build_anti_cid_filtergraph(
    v_in: str,
    a_in: str,
    v_out: str,
    a_out: str,
    config: Optional[dict[str, Any]] = None,
    target_fps: Optional[int] = None,
) -> tuple[list[str], list[str], float]:
    """Build Anti-Content ID video/audio filtergraph and container flags.

    Applies micro-zoom (1.03x), subtle color/contrast shift, synchronized 1.5% speed+pitch shift,
    and returns metadata-stripping FFmpeg output flags plus effective speed factor.
    """
    cfg = config or {}
    zoom = max(1.0, min(1.15, float(cfg.get("zoom", 1.03))))
    speed = max(0.95, min(1.10, float(cfg.get("speed", 1.015))))
    brightness = max(-0.1, min(0.1, float(cfg.get("brightness", 0.01))))
    contrast = max(0.9, min(1.2, float(cfg.get("contrast", 1.02))))
    saturation = max(0.85, min(1.25, float(cfg.get("saturation", 1.03))))
    strip_meta = bool(cfg.get("strip_metadata", True))

    v_steps: list[str] = []
    if zoom > 1.001:
        v_steps.append(
            f"scale=w='trunc(iw*{zoom:.4f}/2)*2':h='trunc(ih*{zoom:.4f}/2)*2':flags=lanczos"
        )
        v_steps.append("crop=1080:1920:(iw-1080)/2:(ih-1920)/2")

    v_steps.append(
        f"eq=brightness={brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f}"
    )

    if abs(speed - 1.0) > 0.0005:
        v_steps.append(f"setpts=(PTS-STARTPTS)/{speed:.5f}")
        if target_fps is not None and int(target_fps) > 0:
            clamped_fps = max(24, min(60, int(target_fps)))
            v_steps.append(f"fps={clamped_fps}")

    v_line = f"[{v_in}]" + ",".join(v_steps) + f"[{v_out}]"

    if abs(speed - 1.0) > 0.0005:
        a_line = (
            f"[{a_in}]aformat=sample_rates=48000:channel_layouts=stereo,"
            f"asetrate=48000*{speed:.5f},aresample=48000,asetpts=PTS-STARTPTS[{a_out}]"
        )
    else:
        a_line = f"[{a_in}]anull[{a_out}]"

    extra_output_args: list[str] = []
    if strip_meta:
        extra_output_args.extend(["-map_metadata", "-1", "-fflags", "+bitexact"])

    return [v_line, a_line], extra_output_args, speed
