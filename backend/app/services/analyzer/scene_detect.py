from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Optional


def _detect_scenes_sync(
    video_path: Path,
    threshold: float = 27.0,
    cancel_event: Optional[asyncio.Event] = None,
) -> list[float]:
    from scenedetect import ContentDetector, SceneDetector, SceneManager, open_video

    video = open_video(str(video_path))
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector(threshold=threshold))

    if cancel_event is not None:
        class _CancelCheckDetector(SceneDetector):
            def __init__(self, event: asyncio.Event) -> None:
                super().__init__()
                self._event = event
                self._frames = 0

            def process_frame(self, timecode: Any, frame_img: Any) -> list[Any]:
                self._frames += 1
                if self._frames % 30 == 0 and self._event.is_set():
                    raise asyncio.CancelledError("PySceneDetect отменён")
                return []

        scene_manager.add_detector(_CancelCheckDetector(cancel_event))

    scene_manager.detect_scenes(video, show_progress=False)
    scene_list = scene_manager.get_scene_list()

    cuts: list[float] = []
    for i, (start_tc, _end_tc) in enumerate(scene_list):
        if i == 0:
            continue
        sec = getattr(start_tc, "seconds", None)
        cuts.append(round(float(sec if sec is not None else start_tc.get_seconds()), 3))
    return cuts


def compute_action_bursts(
    scenes: list[float],
    window_sec: float = 10.0,
    min_cuts: int = 4,
) -> list[dict[str, Any]]:
    """Detect rapid-cut action montages where >= min_cuts occur within window_sec."""
    if len(scenes) < min_cuts:
        return []

    bursts: list[dict[str, Any]] = []
    n = len(scenes)
    j = 0
    for i in range(n):
        while j < n and scenes[j] - scenes[i] <= window_sec:
            j += 1
        count = j - i
        if count >= min_cuts:
            b_start = round(max(0.0, scenes[i] - 1.0), 2)
            b_end = round(scenes[j - 1] + 1.5, 2)
            if bursts and b_start <= float(bursts[-1]["end"]) + 2.0:
                bursts[-1]["end"] = max(float(bursts[-1]["end"]), b_end)
                bursts[-1]["cut_count"] = max(int(bursts[-1]["cut_count"]), count)
            else:
                bursts.append({"start": b_start, "end": b_end, "cut_count": count})
    return bursts


async def detect_scenes(
    video_path: Path,
    threshold: float = 27.0,
    cancel_event: Optional[asyncio.Event] = None,
) -> dict[str, Any]:
    """Run PySceneDetect asynchronously and compute cut timestamps + action montage bursts."""
    scenes = await asyncio.to_thread(_detect_scenes_sync, video_path, threshold, cancel_event)
    bursts = compute_action_bursts(scenes)
    return {
        "scenes": scenes,
        "action_bursts": bursts,
    }


def _cuts_inside_word(cut_t: float, words: list[dict[str, Any]]) -> bool:
    for w in words:
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", 0.0))
        if ws + 0.08 < cut_t < we - 0.08:
            return True
    return False


def snap_clip_boundaries(
    start: float,
    end: float,
    scenes: list[float],
    words: list[dict[str, Any]],
    duration: float,
    tolerance: float = 2.5,
    min_duration: float = 8.0,
) -> tuple[float, float]:
    """Snap clip start/end cleanly to nearby scene cuts and speech pauses without slicing words."""
    safe_dur = max(0.0, float(duration))
    min_span = min(1.0, safe_dur)
    orig_start = max(0.0, min(float(start), max(0.0, safe_dur - min_span)))
    orig_end = min(safe_dur, max(orig_start + min_span, min(float(end), safe_dur)))

    # Collect speech pause boundaries
    pause_starts: list[float] = []
    pause_ends: list[float] = []
    for idx in range(len(words)):
        w_start = float(words[idx].get("start", 0.0))
        w_end = float(words[idx].get("end", 0.0))
        if idx == 0:
            pause_starts.append(max(0.0, w_start - 0.2))
        else:
            prev_end = float(words[idx - 1].get("end", 0.0))
            if w_start - prev_end >= 0.30:
                pause_ends.append(prev_end + 0.2)
                pause_starts.append(max(prev_end, w_start - 0.2))
        if idx == len(words) - 1:
            pause_ends.append(min(duration, w_end + 0.25))

    # Snap start
    best_start = orig_start
    scene_candidates_start = [
        c for c in scenes if abs(c - orig_start) <= tolerance and not _cuts_inside_word(c, words)
    ]
    if scene_candidates_start:
        best_start = min(scene_candidates_start, key=lambda c: abs(c - orig_start))
    else:
        pause_candidates_start = [p for p in pause_starts if abs(p - orig_start) <= tolerance]
        if pause_candidates_start:
            best_start = min(pause_candidates_start, key=lambda p: abs(p - orig_start))

    # Snap end
    best_end = orig_end
    scene_candidates_end = [
        c for c in scenes if abs(c - orig_end) <= tolerance and not _cuts_inside_word(c, words)
    ]
    if scene_candidates_end:
        best_end = min(scene_candidates_end, key=lambda c: abs(c - orig_end))
    else:
        pause_candidates_end = [p for p in pause_ends if abs(p - orig_end) <= tolerance]
        if pause_candidates_end:
            best_end = min(pause_candidates_end, key=lambda p: abs(p - orig_end))

    best_start = max(0.0, min(best_start, safe_dur))
    best_end = max(0.0, min(best_end, safe_dur))

    if best_end - best_start < min_duration:
        return round(orig_start, 2), round(orig_end, 2)
    return round(best_start, 2), round(best_end, 2)
