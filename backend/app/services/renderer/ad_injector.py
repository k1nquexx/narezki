from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

IMAGE_OVERLAY_EXTS = {".png", ".webp", ".jpg", ".jpeg"}


def build_overlay_input_args(
    asset_path: Path,
    clip_duration: float,
    video_codec: Optional[str] = None,
) -> list[str]:
    """Return FFmpeg input flags for PNG/WebP/GIF/WebM overlay preserving transparency."""
    ext = asset_path.suffix.lower()
    dur = max(1.0, float(clip_duration))

    if ext in IMAGE_OVERLAY_EXTS:
        return ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(asset_path)]
    if ext == ".gif":
        return ["-ignore_loop", "0", "-t", f"{dur:.3f}", "-i", str(asset_path)]
    if ext == ".webm":
        codec = (video_codec or "").lower()
        if codec == "vp8":
            return [
                "-c:v",
                "libvpx",
                "-stream_loop",
                "-1",
                "-t",
                f"{dur:.3f}",
                "-i",
                str(asset_path),
            ]
        if codec in ("vp9", ""):
            return [
                "-c:v",
                "libvpx-vp9",
                "-stream_loop",
                "-1",
                "-t",
                f"{dur:.3f}",
                "-i",
                str(asset_path),
            ]
        return ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(asset_path)]
    return ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(asset_path)]


def build_overlay_filtergraph(
    v_in: str,
    ov_in: str,
    out_label: str,
    overlay_x: float = 50.0,
    overlay_y: float = 22.0,
    overlay_scale: float = 35.0,
    overlay_start: float = 3.0,
    overlay_end: float = 12.0,
    fade_duration: float = 0.4,
) -> list[str]:
    """Overlay PNG/WebM asset onto 1080x1920 video with scale, alpha fade-in/out, and positioning."""
    scale_pct = max(5.0, min(95.0, float(overlay_scale)))
    target_w = int(round(1080.0 * scale_pct / 100.0))
    target_w = max(40, target_w - (target_w % 2))

    start_t = max(0.0, float(overlay_start))
    end_t = max(start_t + 0.2, float(overlay_end))
    dur = end_t - start_t
    fade_d = max(0.0, min(float(fade_duration), dur * 0.45))

    ov_chain = [f"[{ov_in}]setpts=PTS-STARTPTS", "format=rgba", f"scale={target_w}:-2:flags=lanczos"]
    if fade_d >= 0.05:
        ov_chain.append(f"fade=t=in:st={start_t:.3f}:d={fade_d:.3f}:alpha=1")
        ov_chain.append(f"fade=t=out:st={max(start_t, end_t - fade_d):.3f}:d={fade_d:.3f}:alpha=1")

    x_frac = max(0.0, min(1.0, float(overlay_x) / 100.0))
    y_frac = max(0.0, min(1.0, float(overlay_y) / 100.0))
    x_expr = f"max(0,min(W-w,W*{x_frac:.4f}-w/2))"
    y_expr = f"max(0,min(H-h,H*{y_frac:.4f}-h/2))"

    return [
        ",".join(ov_chain) + "[_ov_ready]",
        f"[{v_in}][_ov_ready]overlay=x='{x_expr}':y='{y_expr}':"
        f"enable='between(t,{start_t:.3f},{end_t:.3f})':eof_action=pass:shortest=0[{out_label}]",
    ]


def build_video_ad_splice_filtergraph(
    v_main: str,
    a_main: str,
    ad_v_in: str,
    ad_a_in: Optional[str],
    v_out: str,
    a_out: str,
    main_duration: float,
    ad_duration: float,
    target_fps: int = 30,
    insert_position: str = "outro",
    insert_at_sec: Optional[float] = None,
) -> list[str]:
    """Normalize video ad to 1080x1920 @ target_fps / 48kHz loudnorm and splice mid-roll or outro."""
    fps = max(24, min(60, int(target_fps)))
    ad_dur = max(0.3, float(ad_duration or 3.0))

    lines = [
        f"[{ad_v_in}]scale=1080:1920:force_original_aspect_ratio=decrease,"
        f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p,setpts=PTS-STARTPTS[_ad_v]"
    ]

    if ad_a_in:
        if ad_dur >= 1.5:
            lines.append(
                f"[{ad_a_in}]loudnorm=I=-14:TP=-1.5:LRA=11,"
                "aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[_ad_a]"
            )
        else:
            lines.append(
                f"[{ad_a_in}]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[_ad_a]"
            )
    else:
        lines.append(f"anullsrc=r=48000:cl=stereo:d={ad_dur:.3f},asetpts=PTS-STARTPTS[_ad_a]")

    use_mid_roll = False
    split_t = main_duration * 0.5
    if insert_position == "mid_roll":
        requested = float(insert_at_sec) if insert_at_sec is not None else main_duration * 0.5
        if 0.4 < requested < main_duration - 0.4:
            use_mid_roll = True
            split_t = requested
        elif main_duration > 1.2:
            use_mid_roll = True
            split_t = main_duration * 0.5

    if use_mid_roll:
        lines.extend(
            [
                f"[{v_main}]format=yuv420p,setsar=1,fps={fps},split=2[_vm1][_vm2]",
                f"[_vm1]trim=0:{split_t:.3f},setpts=PTS-STARTPTS[_vp1]",
                f"[_vm2]trim=start={split_t:.3f},setpts=PTS-STARTPTS[_vp2]",
                f"[{a_main}]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[_am1][_am2]",
                f"[_am1]atrim=0:{split_t:.3f},asetpts=PTS-STARTPTS[_ap1]",
                f"[_am2]atrim=start={split_t:.3f},asetpts=PTS-STARTPTS[_ap2]",
                f"[_vp1][_ap1][_ad_v][_ad_a][_vp2][_ap2]concat=n=3:v=1:a=1[{v_out}][{a_out}]",
            ]
        )
    else:
        lines.extend(
            [
                f"[{v_main}]format=yuv420p,setsar=1,fps={fps},setpts=PTS-STARTPTS[_vm_outro]",
                f"[{a_main}]aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS[_am_outro]",
                f"[_vm_outro][_am_outro][_ad_v][_ad_a]concat=n=2:v=1:a=1[{v_out}][{a_out}]",
            ]
        )

    return lines


def resolve_ad_params(ad_preset: dict[str, Any], override: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Merge AdPreset defaults with per-clip overrides."""
    merged = dict(ad_preset)
    if override:
        for k, v in override.items():
            if v is not None:
                merged[k] = v
    return merged
