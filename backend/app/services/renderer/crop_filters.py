from __future__ import annotations

import math
from typing import Any, Optional


def _rdp_simplify(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker simplification for (t, x_px) trajectory."""
    if len(points) <= 2:
        return points

    t0, x0 = points[0]
    t1, x1 = points[-1]
    dt = t1 - t0
    dx = x1 - x0
    denom = math.hypot(dt * 40.0, dx)

    max_dist = -1.0
    max_idx = 0
    for i in range(1, len(points) - 1):
        ti, xi = points[i]
        if denom == 0.0:
            dist = abs(xi - x0)
        else:
            dist = abs(dx * (ti - t0) * 40.0 - (dt * 40.0) * (xi - x0)) / denom
        if dist > max_dist:
            max_dist = dist
            max_idx = i

    if max_dist > epsilon:
        left = _rdp_simplify(points[: max_idx + 1], epsilon)
        right = _rdp_simplify(points[max_idx:], epsilon)
        return left[:-1] + right
    return [points[0], points[-1]]


def _build_piecewise_x_expr(keyframes: list[tuple[float, float]], max_x: int) -> str:
    """Build compact piecewise-linear FFmpeg crop x(t) expression."""
    if max_x <= 0 or not keyframes:
        return "0"
    if len(keyframes) == 1:
        return str(int(round(max(0.0, min(float(max_x), keyframes[0][1])))))

    # Cap to <= 35 segments so FFmpeg expression parser stays fast and clean
    pts = list(keyframes)
    eps = 6.0
    while len(pts) > 36 and eps < 120.0:
        pts = _rdp_simplify(pts, epsilon=eps)
        eps *= 1.4

    if len(pts) <= 1:
        return str(int(round(max(0.0, min(float(max_x), pts[0][1])))))

    expr = f"{pts[-1][1]:.1f}"
    for i in range(len(pts) - 2, -1, -1):
        t0, x0 = pts[i]
        t1, x1 = pts[i + 1]
        dt = max(0.001, t1 - t0)
        slope = (x1 - x0) / dt
        if abs(slope) < 0.05:
            seg_expr = f"{x0:.1f}"
        else:
            seg_expr = f"{x0:.1f}+({slope:.2f})*(t-{t0:.3f})"
        expr = f"if(lt(t,{t1:.3f}),{seg_expr},{expr})"

    return f"clip({expr},0,{max_x})"


def build_crop_filtergraph(
    crop_mode: str,
    source_w: int,
    source_h: int,
    target_fps: int = 30,
    track_data: Optional[dict[str, Any]] = None,
    v_in: str = "0:v",
    bg_in: Optional[str] = None,
    out_label: str = "v_cropped",
) -> list[str]:
    """Generate FFmpeg filtergraph steps converting input video to 1080x1920 (9:16)."""
    src_w = max(64, int(source_w or 1920))
    src_h = max(64, int(source_h or 1080))
    fps = max(24, min(60, int(target_fps)))

    effective_mode = crop_mode
    if effective_mode == "split_screen" and not bg_in:
        effective_mode = "cinema_blur"

    if effective_mode == "cinema_blur":
        return [
            f"[{v_in}]split=2[_cb_fg_src][_cb_bg_src]",
            f"[_cb_bg_src]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=luma_radius=24:luma_power=2,eq=brightness=-0.12[_cb_bg]",
            f"[_cb_fg_src]scale=1080:1920:force_original_aspect_ratio=decrease[_cb_fg]",
            f"[_cb_bg][_cb_fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}[{out_label}]",
        ]

    if effective_mode == "split_screen" and bg_in:
        return [
            f"[{v_in}]scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1,fps={fps}[_ss_top]",
            f"[{bg_in}]setpts=PTS-STARTPTS,scale=1080:960:force_original_aspect_ratio=increase,crop=1080:960,setsar=1,fps={fps}[_ss_bot]",
            f"[_ss_top][_ss_bot]vstack=inputs=2:shortest=1,setsar=1,format=yuv420p[{out_label}]",
        ]

    # Default: auto_track (dynamic 9:16 crop)
    if (src_w / src_h) >= (9.0 / 16.0):
        crop_h = src_h - (src_h % 2)
        crop_w = int(round(crop_h * 9.0 / 16.0))
        crop_w = min(src_w, crop_w - (crop_w % 2))
    else:
        crop_w = src_w - (src_w % 2)
        crop_h = int(round(crop_w * 16.0 / 9.0))
        crop_h = min(src_h, crop_h - (crop_h % 2))

    crop_w = max(64, crop_w)
    crop_h = max(64, crop_h)
    max_x = max(0, src_w - crop_w)
    max_y = max(0, src_h - crop_h)
    y_offset = max_y // 2

    points = (track_data or {}).get("points") or []
    if max_x == 0 or not points:
        x_center = max_x // 2
        return [
            f"[{v_in}]crop=w={crop_w}:h={crop_h}:x={x_center}:y={y_offset},"
            f"scale=1080:1920:flags=lanczos,setsar=1,fps={fps}[{out_label}]"
        ]

    keyframes: list[tuple[float, float]] = []
    for pt in points:
        t = float(pt.get("t", 0.0))
        cx = max(0.0, min(1.0, float(pt.get("cx", 0.5))))
        x_px = max(0.0, min(float(max_x), cx * src_w - crop_w * 0.5))
        keyframes.append((t, x_px))

    simplified = _rdp_simplify(keyframes, epsilon=8.0)
    x_expr = _build_piecewise_x_expr(simplified, max_x)

    return [
        f"[{v_in}]crop=w={crop_w}:h={crop_h}:x='{x_expr}':y={y_offset},"
        f"scale=1080:1920:flags=lanczos,setsar=1,fps={fps}[{out_label}]"
    ]
