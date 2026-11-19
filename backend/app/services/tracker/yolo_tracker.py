from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import cv2

from ...core.config import MODELS_DIR
from ...core.cuda_env import release_vram, run_cancellable_thread, setup_cuda_dlls
from .smoother import smooth_trajectory

logger = logging.getLogger("narezki.tracker")

ProgressCallback = Callable[[float, str], Awaitable[None]]


def _consume_future_exc(fut: Any) -> None:
    try:
        fut.exception()
    except Exception:
        pass


def _resolve_yolo_weights(yolo_model_path: str) -> str:
    p = Path(yolo_model_path)
    if p.is_absolute() and p.exists():
        return str(p)
    if p.exists():
        return str(p.resolve())
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    return str(MODELS_DIR / p.name)


def _pick_primary_target(
    boxes_xyxy: list[tuple[float, float, float, float, float, int]],
    frame_w: int,
    frame_h: int,
    prev_cx: float,
    is_coco_model: bool,
) -> tuple[float, float, float] | None:
    """Choose best character/person bounding box favoring larger size, confidence, and continuity."""
    if not boxes_xyxy or frame_w <= 0 or frame_h <= 0:
        return None

    filtered = boxes_xyxy
    if is_coco_model:
        persons = [b for b in boxes_xyxy if b[5] == 0]
        if persons:
            filtered = persons

    frame_area = float(frame_w * frame_h)
    best_score = -1.0
    best_target: tuple[float, float, float] | None = None

    for x1, y1, x2, y2, conf, _cls in filtered:
        w = max(1.0, x2 - x1)
        h = max(1.0, y2 - y1)
        cx = max(0.0, min(1.0, ((x1 + x2) * 0.5) / frame_w))
        cy = max(0.0, min(1.0, (y1 + h * 0.35) / frame_h))
        area_ratio = (w * h) / frame_area
        dist_penalty = abs(cx - prev_cx) * 0.45
        score = (area_ratio * 2.5 + 0.1) * conf * max(0.35, 1.0 - dist_penalty)
        if score > best_score:
            best_score = score
            best_target = (cx, cy, conf)

    return best_target


def _track_clip_sync(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    yolo_model_path: str,
    sample_fps: float,
    smoothing_method: str,
    smoothing_strength: float,
    scene_cuts: list[float],
    loop: asyncio.AbstractEventLoop,
    on_progress: Optional[ProgressCallback],
    cancel_event: Optional[asyncio.Event],
) -> dict[str, Any]:
    setup_cuda_dlls()
    import torch
    from ultralytics import YOLO

    weights_str = _resolve_yolo_weights(yolo_model_path)
    use_cuda = torch.cuda.is_available()
    device_arg: Any = 0 if use_cuda else "cpu"

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"OpenCV не смог открыть видео для трекинга: {video_path}")

    source_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920)
    source_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 1080)
    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    if src_fps <= 1.0 or src_fps > 240.0:
        src_fps = 30.0

    model = None
    raw_points: list[dict[str, Any]] = []

    try:
        logger.info("Loading YOLO weights=%s on device=%s", weights_str, device_arg)
        model = YOLO(weights_str)
        names = getattr(model, "names", {}) or {}
        is_coco_model = isinstance(names, dict) and names.get(0) == "person" and len(names) >= 20

        duration = max(0.2, clip_end - clip_start)
        step_sec = 1.0 / max(1.0, float(sample_fps))

        # Seek near clip_start once and step cleanly forward by frame index
        start_frame = max(0, int(round(clip_start * src_fps)))
        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        cur_frame = start_frame
        prev_cx = 0.5
        prev_cy = 0.42
        next_sample_rel = 0.0
        last_pct = -1.0

        while next_sample_rel <= duration + 1e-4:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("YOLOv8 трекинг отменён")

            target_global_t = clip_start + next_sample_rel
            target_frame = max(0, int(round(target_global_t * src_fps)))
            if target_frame - cur_frame > int(src_fps * 1.5) or target_frame < cur_frame:
                cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
                cur_frame = target_frame
            else:
                while cur_frame < target_frame:
                    if not cap.grab():
                        break
                    cur_frame += 1

            ok, frame = cap.read()
            if not ok or frame is None:
                break
            cur_frame += 1

            results = model.predict(
                frame,
                device=device_arg,
                conf=0.25,
                imgsz=640,
                verbose=False,
            )

            boxes_xyxy: list[tuple[float, float, float, float, float, int]] = []
            if results and results[0].boxes is not None and len(results[0].boxes) > 0:
                b = results[0].boxes
                xyxy_list = b.xyxy.cpu().tolist()
                conf_list = b.conf.cpu().tolist()
                cls_list = b.cls.cpu().tolist()
                for coords, cf, cl in zip(xyxy_list, conf_list, cls_list):
                    boxes_xyxy.append(
                        (
                            float(coords[0]),
                            float(coords[1]),
                            float(coords[2]),
                            float(coords[3]),
                            float(cf),
                            int(cl),
                        )
                    )

            picked = _pick_primary_target(boxes_xyxy, source_w, source_h, prev_cx, is_coco_model)
            if picked is not None:
                prev_cx, prev_cy, conf = picked
                raw_points.append({"t": round(next_sample_rel, 3), "cx": prev_cx, "cy": prev_cy, "conf": conf})
            else:
                raw_points.append({"t": round(next_sample_rel, 3), "cx": prev_cx, "cy": prev_cy, "conf": 0.0})

            if on_progress:
                pct = min(98.0, (next_sample_rel / duration) * 100.0)
                if pct - last_pct >= 5.0:
                    last_pct = pct
                    fut = asyncio.run_coroutine_threadsafe(
                        on_progress(pct, f"YOLOv8 трекинг: {int(pct)}%"),
                        loop,
                    )
                    fut.add_done_callback(_consume_future_exc)

            next_sample_rel += step_sec
    finally:
        cap.release()
        if model is not None:
            del model
        release_vram("yolov8-tracker")

    if not raw_points:
        raw_points = [
            {"t": 0.0, "cx": 0.5, "cy": 0.42, "conf": 0.0},
            {"t": round(max(0.5, clip_end - clip_start), 3), "cx": 0.5, "cy": 0.42, "conf": 0.0},
        ]

    smoothed = smooth_trajectory(
        raw_points=raw_points,
        scene_cuts=scene_cuts,
        clip_start=clip_start,
        method=smoothing_method,
        strength=smoothing_strength,
    )

    return {
        "source_width": source_w,
        "source_height": source_h,
        "clip_start": float(clip_start),
        "clip_end": float(clip_end),
        "points": smoothed,
    }


async def track_clip_subject(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    yolo_model_path: str = "yolov8n.pt",
    sample_fps: float = 6.0,
    smoothing_method: str = "kalman",
    smoothing_strength: float = 0.65,
    scene_cuts: list[float] | None = None,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> dict[str, Any]:
    """Track primary character/person in the clip using YOLOv8 on GPU and smooth trajectory."""
    loop = asyncio.get_running_loop()
    eff_cancel = cancel_event if cancel_event is not None else asyncio.Event()
    return await run_cancellable_thread(
        _track_clip_sync,
        video_path,
        clip_start,
        clip_end,
        yolo_model_path,
        sample_fps,
        smoothing_method,
        smoothing_strength,
        scene_cuts or [],
        loop,
        on_progress,
        eff_cancel,
        cancel_event=eff_cancel,
    )
