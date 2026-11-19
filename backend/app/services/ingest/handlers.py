from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ...core.config import get_project_dir
from ...core.database import AsyncSessionLocal, load_settings
from ...core.models import Project
from ...core.queue_manager import JobCancelledError, JobContext, QueueManager
from ...core.ws_manager import ws_manager
from .downloader import download_video_url
from .ffprobe_utils import probe_media
from .proxy import generate_proxy, generate_thumbnail


async def _set_project_error(project_id: str, error_msg: str) -> None:
    async with AsyncSessionLocal() as session:
        proj = await session.get(Project, project_id)
        if proj is not None:
            proj.status = "error"
            proj.error_message = error_msg
            await session.commit()
    await ws_manager.emit_project_update(project_id, "error", error_message=error_msg)


async def _set_project_cancelled(project_id: str) -> None:
    async with AsyncSessionLocal() as session:
        proj = await session.get(Project, project_id)
        if proj is not None:
            proj.status = "cancelled"
            proj.error_message = None
            await session.commit()
    await ws_manager.emit_project_update(project_id, "cancelled")


async def _finalize_project_media(
    ctx: JobContext,
    project_id: str,
    source_path: Path,
    progress_offset: float,
    progress_span: float,
    title_override: str | None = None,
) -> dict[str, Any]:
    proj_dir = get_project_dir(project_id)
    await ctx.update_progress(progress_offset, "probe", "Чтение параметров видео (FFprobe)...")
    meta = await probe_media(source_path)

    thumb_path = proj_dir / "thumb.jpg"
    await ctx.update_progress(
        progress_offset + progress_span * 0.1, "thumbnail", "Создание обложки..."
    )
    await generate_thumbnail(source_path, thumb_path)

    proxy_path = proj_dir / "proxy.mp4"
    async with AsyncSessionLocal() as session:
        settings = await load_settings(session)

    async def _proxy_progress(pct: float, msg: str) -> None:
        mapped = progress_offset + progress_span * (0.15 + 0.80 * (pct / 100.0))
        await ctx.update_progress(mapped, "proxy", msg)

    await generate_proxy(
        source_path=source_path,
        proxy_path=proxy_path,
        duration=float(meta.get("duration") or 0.0),
        preferred_encoder=settings.preferred_encoder,
        on_progress=_proxy_progress,
        cancel_event=ctx.cancel_event,
        register_proc=ctx.register_process,
        unregister_proc=ctx.unregister_process,
    )

    async with AsyncSessionLocal() as session:
        proj = await session.get(Project, project_id)
        if proj is not None:
            if title_override and (not proj.title or proj.title == "Загрузка по ссылке..."):
                proj.title = title_override
            proj.source_path = str(source_path)
            proj.proxy_path = str(proxy_path)
            proj.thumbnail_path = str(thumb_path)
            proj.duration = float(meta["duration"])
            proj.width = int(meta["width"])
            proj.height = int(meta["height"])
            proj.fps = float(meta["fps"])
            proj.video_codec = meta["video_codec"]
            proj.audio_codec = meta["audio_codec"]
            proj.has_audio = bool(meta["has_audio"])
            proj.status = "ready"
            proj.error_message = None
            await session.commit()

    await ws_manager.emit_project_update(project_id, "ready")
    await ctx.log(
        f"Проект готов: {source_path.name} ({meta['width']}x{meta['height']}, {meta['duration']:.1f}с)"
    )
    return {
        "project_id": project_id,
        "duration": meta["duration"],
        "width": meta["width"],
        "height": meta["height"],
        "proxy_path": str(proxy_path),
    }


async def handle_ingest_local(ctx: JobContext) -> dict[str, Any]:
    project_id = ctx.project_id
    if not project_id:
        raise ValueError("Отсутствует project_id в задаче ingest_local")

    source_path = Path(str(ctx.payload.get("file_path", "")))
    if not source_path.exists() or not source_path.is_file():
        err = f"Локальный файл не найден: {source_path}"
        await _set_project_error(project_id, err)
        raise FileNotFoundError(err)

    try:
        return await _finalize_project_media(ctx, project_id, source_path, 5.0, 95.0)
    except (JobCancelledError, asyncio.CancelledError):
        await _set_project_cancelled(project_id)
        raise
    except Exception as exc:
        await _set_project_error(project_id, str(exc))
        raise


async def handle_ingest_url(ctx: JobContext) -> dict[str, Any]:
    project_id = ctx.project_id
    if not project_id:
        raise ValueError("Отсутствует project_id в задаче ingest_url")

    url = str(ctx.payload.get("url", "")).strip()
    if not url:
        err = "Не указана ссылка для скачивания"
        await _set_project_error(project_id, err)
        raise ValueError(err)

    proj_dir = get_project_dir(project_id)

    async def _dl_progress(pct: float, msg: str) -> None:
        await ctx.update_progress(pct * 0.55, "download", msg)

    try:
        await ctx.update_progress(2.0, "download", f"Запуск yt-dlp: {url}")
        downloaded_path, video_title = await download_video_url(
            url=url,
            output_dir=proj_dir,
            on_progress=_dl_progress,
            cancel_event=ctx.cancel_event,
        )
        return await _finalize_project_media(
            ctx,
            project_id,
            downloaded_path,
            55.0,
            45.0,
            title_override=video_title,
        )
    except (JobCancelledError, asyncio.CancelledError):
        await _set_project_cancelled(project_id)
        raise
    except Exception as exc:
        await _set_project_error(project_id, str(exc))
        raise


def register_ingest_handlers(queue: QueueManager) -> None:
    queue.register_handler("ingest_local", handle_ingest_local)
    queue.register_handler("ingest_url", handle_ingest_url)
