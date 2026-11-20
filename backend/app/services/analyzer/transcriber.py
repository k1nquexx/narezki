from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ...core.config import MODELS_DIR, TEMP_DIR, get_ffmpeg_path
from ...core.cuda_env import release_vram, run_cancellable_thread, setup_cuda_dlls
from ...core.queue_manager import JobCancelledError

logger = logging.getLogger("narezki.whisper")

ProgressCallback = Callable[[float, str], Awaitable[None]]


def _consume_future_exc(fut: Any) -> None:
    try:
        fut.exception()
    except Exception:
        pass


async def _extract_wav_16k(
    video_path: Path,
    wav_path: Path,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> None:
    ffmpeg_exe = get_ffmpeg_path()
    wav_path.parent.mkdir(parents=True, exist_ok=True)
    kwargs: dict[str, Any] = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = 0x08000000

    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(wav_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **kwargs,
    )
    if register_proc:
        register_proc(proc)
    try:
        _, stderr = await proc.communicate()
        if cancel_event and cancel_event.is_set():
            raise asyncio.CancelledError("Извлечение аудио отменено")
        if proc.returncode != 0 or not wav_path.exists():
            err = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Не удалось извлечь аудиодорожку для Whisper: {err}")
    except asyncio.CancelledError:
        if proc.returncode is None:
            with suppress(Exception):
                proc.kill()
            with suppress(Exception):
                await proc.wait()
        raise
    finally:
        if unregister_proc:
            unregister_proc(proc)


def _transcribe_sync(
    wav_path: Path,
    duration: float,
    model_size: str,
    device: str,
    compute_type: str,
    language: str,
    loop: asyncio.AbstractEventLoop,
    on_progress: Optional[ProgressCallback],
    cancel_event: Optional[asyncio.Event],
) -> dict[str, Any]:
    setup_cuda_dlls()
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    from faster_whisper import WhisperModel

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    cuda_ok = False
    if device == "cuda":
        try:
            import torch

            cuda_ok = bool(torch.cuda.is_available())
        except Exception:
            cuda_ok = False

    eff_device = device if (device != "cuda" or cuda_ok) else "cpu"
    attempts: list[tuple[str, str]] = []
    if eff_device == "cuda":
        attempts.append(("cuda", compute_type))
        if compute_type == "float16":
            attempts.append(("cuda", "int8_float16"))
        attempts.append(("cpu", "int8"))
    else:
        cpu_ct = "int8" if compute_type in ("float16", "int8_float16") else compute_type
        attempts.append(("cpu", cpu_ct))
        if cpu_ct != "int8":
            attempts.append(("cpu", "int8"))

    last_err: Exception | None = None
    used_device = device
    used_compute = compute_type
    segments_out: list[dict[str, Any]] = []
    words_out: list[dict[str, Any]] = []
    detected_lang = language
    succeeded = False

    for dev, ct in attempts:
        model = None
        segments_out = []
        words_out = []
        try:
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("Транскрибация отменена")

            logger.info("Loading faster-whisper model=%s device=%s compute_type=%s", model_size, dev, ct)
            model = WhisperModel(
                model_size,
                device=dev,
                compute_type=ct,
                download_root=str(MODELS_DIR / "whisper"),
            )
            used_device = dev
            used_compute = ct

            lang_arg = None if not language or language == "auto" else language
            raw_segments, info = model.transcribe(
                str(wav_path),
                language=lang_arg,
                word_timestamps=True,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 500},
                beam_size=5,
            )
            detected_lang = getattr(info, "language", None) or language
            last_pct = -1.0

            for seg in raw_segments:
                if cancel_event and cancel_event.is_set():
                    raise asyncio.CancelledError("Транскрибация отменена")

                seg_words: list[dict[str, Any]] = []
                if getattr(seg, "words", None):
                    for w in seg.words:
                        word_txt = str(w.word).strip()
                        if not word_txt:
                            continue
                        w_item = {
                            "word": word_txt,
                            "start": round(float(w.start), 3),
                            "end": round(float(w.end), 3),
                        }
                        seg_words.append(w_item)
                        words_out.append(w_item)

                segments_out.append(
                    {
                        "start": round(float(seg.start), 3),
                        "end": round(float(seg.end), 3),
                        "text": str(seg.text).strip(),
                        "words": seg_words,
                    }
                )

                if on_progress and duration > 0:
                    pct = min(99.0, max(0.0, (float(seg.end) / duration) * 100.0))
                    if pct - last_pct >= 2.0:
                        last_pct = pct
                        fut = asyncio.run_coroutine_threadsafe(
                            on_progress(pct, f"Whisper ({used_device}/{used_compute}): {int(pct)}%"),
                            loop,
                        )
                        fut.add_done_callback(_consume_future_exc)

            succeeded = True
            break
        except (asyncio.CancelledError, JobCancelledError):
            raise
        except Exception as exc:
            last_err = exc
            logger.warning("Whisper failed for (%s, %s): %s", dev, ct, exc)
        finally:
            if model is not None:
                del model
                model = None
            release_vram("faster-whisper")

    if not succeeded:
        raise RuntimeError(f"Не удалось выполнить транскрибацию Whisper ({model_size}): {last_err}")

    return {
        "language": detected_lang,
        "device": used_device,
        "compute_type": used_compute,
        "segments": segments_out,
        "words": words_out,
    }


async def transcribe_video(
    video_path: Path,
    duration: float,
    has_audio: bool,
    model_size: str = "large-v3-turbo",
    device: str = "cuda",
    compute_type: str = "float16",
    language: str = "auto",
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
    register_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    unregister_proc: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
) -> dict[str, Any]:
    """Extract 16kHz WAV, run faster-whisper on GPU with VAD + word timestamps, and release VRAM."""
    if not has_audio or duration <= 0.0:
        return {
            "language": "none",
            "device": "none",
            "compute_type": "none",
            "segments": [],
            "words": [],
        }

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    wav_path = TEMP_DIR / f"whisper_{uuid.uuid4().hex[:10]}.wav"
    eff_cancel = cancel_event if cancel_event is not None else asyncio.Event()

    try:
        if on_progress:
            await on_progress(2.0, "Извлечение аудиодорожки (16kHz WAV)...")
        await _extract_wav_16k(
            video_path,
            wav_path,
            cancel_event=eff_cancel,
            register_proc=register_proc,
            unregister_proc=unregister_proc,
        )
        if on_progress:
            await on_progress(5.0, f"Загрузка модели Whisper ({model_size})...")
        loop = asyncio.get_running_loop()
        return await run_cancellable_thread(
            _transcribe_sync,
            wav_path,
            duration,
            model_size,
            device,
            compute_type,
            language,
            loop,
            on_progress,
            eff_cancel,
            cancel_event=eff_cancel,
        )
    finally:
        with suppress(OSError):
            wav_path.unlink(missing_ok=True)
