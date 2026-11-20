from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ...core.config import BIN_DIR, get_ffmpeg_path
from ...core.cuda_env import run_cancellable_thread

ProgressCallback = Callable[[float, str], Awaitable[None]]


def _consume_future_exc(fut: Any) -> None:
    try:
        fut.exception()
    except Exception:
        pass


async def download_video_url(
    url: str,
    output_dir: Path,
    on_progress: Optional[ProgressCallback] = None,
    cancel_event: Optional[asyncio.Event] = None,
) -> tuple[Path, str]:
    """Download remote video using yt-dlp into output_dir and return (file_path, video_title)."""
    import yt_dlp

    output_dir.mkdir(parents=True, exist_ok=True)
    loop = asyncio.get_running_loop()
    last_emit = 0.0

    def _emit_from_thread(pct: float, msg: str) -> None:
        nonlocal last_emit
        if on_progress is None:
            return
        now = loop.time()
        if pct < 99.0 and (now - last_emit) < 0.35:
            return
        last_emit = now
        fut = asyncio.run_coroutine_threadsafe(on_progress(pct, msg), loop)
        fut.add_done_callback(_consume_future_exc)

    def _hook(d: dict[str, Any]) -> None:
        if cancel_event and cancel_event.is_set():
            raise yt_dlp.utils.DownloadError("Отменено пользователем")
        status = d.get("status")
        if status == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            if total > 0:
                pct = min(98.0, (downloaded / total) * 100.0)
                _emit_from_thread(pct, f"Скачивание видео: {pct:.1f}%")
            else:
                _emit_from_thread(10.0, "Скачивание видео...")
        elif status == "finished":
            _emit_from_thread(99.0, "Сборка аудио и видеодорожек...")

    try:
        ffmpeg_dir = str(Path(get_ffmpeg_path()).parent)
    except FileNotFoundError:
        ffmpeg_dir = str(BIN_DIR)

    outtmpl = str(output_dir / "source.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": ffmpeg_dir,
        "progress_hooks": [_hook],
    }

    def _run_download() -> tuple[Path, str]:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info or not isinstance(info, dict):
                raise RuntimeError("yt-dlp не смог получить метаданные видео")
            title = str(info.get("title") or "Загруженное видео")
            downloaded_path = ydl.prepare_filename(info)
            p = Path(downloaded_path)
            mp4_candidate = p.with_suffix(".mp4")
            if mp4_candidate.exists():
                return mp4_candidate, title
            if p.exists():
                return p, title
            for item in output_dir.glob("source.*"):
                if item.is_file() and item.suffix.lower() not in (".part", ".ytdl"):
                    return item, title
            raise FileNotFoundError("yt-dlp завершил работу, но итоговый видеофайл не найден.")

    return await run_cancellable_thread(_run_download, cancel_event=cancel_event)
