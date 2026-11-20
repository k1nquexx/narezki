from __future__ import annotations

import asyncio
import math
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np

from ...core.config import get_ffmpeg_path

SAMPLE_RATE = 16000
STEP_SECONDS = 0.5
SAMPLES_PER_STEP = int(SAMPLE_RATE * STEP_SECONDS)


async def analyze_audio_peaks(
    video_path: Path,
    duration: float,
    has_audio: bool = True,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> dict[str, Any]:
    """Extract mono 16kHz audio stream via FFmpeg and compute 0.5s RMS dBFS curve + spike events."""
    if not has_audio or duration <= 0.0:
        return {
            "sample_step": STEP_SECONDS,
            "rms_db": [],
            "peaks": [],
            "median_db": -60.0,
            "duration": float(duration),
        }

    ffmpeg_exe = get_ffmpeg_path()
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(SAMPLE_RATE),
        "-f",
        "f32le",
        "pipe:1",
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    if register_proc:
        register_proc(proc)

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break

    stderr_task = asyncio.create_task(_drain_stderr())

    assert proc.stdout is not None
    bytes_per_step = SAMPLES_PER_STEP * 4  # float32 = 4 bytes
    leftover = b""
    rms_db: list[float] = []

    try:
        while True:
            if cancel_event and cancel_event.is_set():
                proc.kill()
                await proc.wait()
                raise asyncio.CancelledError("Анализ аудио отменён")

            chunk = await proc.stdout.read(bytes_per_step * 16)
            if not chunk:
                break
            buf = leftover + chunk
            usable = (len(buf) // bytes_per_step) * bytes_per_step
            leftover = buf[usable:]
            if usable == 0:
                continue
            arr = np.frombuffer(buf[:usable], dtype=np.float32)
            frames = arr.reshape(-1, SAMPLES_PER_STEP)
            rms_vals = np.sqrt(np.mean(np.square(frames), axis=1))
            for val in rms_vals:
                db = 20.0 * math.log10(max(float(val), 1e-5))
                rms_db.append(round(max(-80.0, min(0.0, db)), 2))

        if len(leftover) >= 4:
            usable_tail = (len(leftover) // 4) * 4
            tail_arr = np.frombuffer(leftover[:usable_tail], dtype=np.float32)
            val = float(np.sqrt(np.mean(np.square(tail_arr))))
            db = 20.0 * math.log10(max(val, 1e-5))
            rms_db.append(round(max(-80.0, min(0.0, db)), 2))

        await stderr_task
        await proc.wait()
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

    if not rms_db:
        return {
            "sample_step": STEP_SECONDS,
            "rms_db": [],
            "peaks": [],
            "median_db": -60.0,
            "duration": float(duration),
        }

    arr_db = np.array(rms_db, dtype=np.float32)
    active = arr_db[arr_db > -65.0]
    median_db = float(np.median(active)) if len(active) > 0 else float(np.median(arr_db))

    raw_flags: list[tuple[int, float, float]] = []
    for idx, db_val in enumerate(rms_db):
        delta = db_val - median_db
        prev_db = rms_db[idx - 1] if idx > 0 else median_db
        onset_jump = db_val - prev_db
        if db_val > -40.0 and (delta >= 7.0 or (onset_jump >= 6.5 and delta >= 3.5)):
            raw_flags.append((idx, db_val, max(delta, onset_jump)))

    peaks: list[dict[str, Any]] = []
    for idx, db_val, delta in raw_flags:
        t_start = round(idx * STEP_SECONDS, 2)
        t_end = round((idx + 1) * STEP_SECONDS, 2)
        if peaks and t_start - float(peaks[-1]["end"]) <= 1.5:
            prev = peaks[-1]
            prev["end"] = t_end
            if db_val > float(prev["peak_db"]):
                prev["peak_db"] = round(db_val, 2)
            if delta > float(prev["delta_db"]):
                prev["delta_db"] = round(delta, 2)
            prev["score"] = round(min(100.0, 45.0 + float(prev["delta_db"]) * 3.5), 1)
        else:
            peaks.append(
                {
                    "start": t_start,
                    "end": t_end,
                    "peak_db": round(db_val, 2),
                    "delta_db": round(delta, 2),
                    "score": round(min(100.0, 45.0 + delta * 3.5), 1),
                }
            )

    return {
        "sample_step": STEP_SECONDS,
        "rms_db": rms_db,
        "peaks": peaks,
        "median_db": round(median_db, 2),
        "duration": float(duration),
    }
