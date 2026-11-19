from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import AsyncIterator

import aiofiles
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import is_safe_media_path
from ..core.database import get_db
from ..core.models import AdPreset, BackgroundAsset, Clip, Project

router = APIRouter(prefix="/api/media", tags=["media"])

CHUNK_SIZE = 1024 * 512  # 512 KB

EXTRA_MIME_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


async def _collect_allowed_roots(db: AsyncSession) -> list[Path]:
    allowed: list[Path] = []
    for col in (
        (await db.execute(select(Project.source_path, Project.proxy_path, Project.thumbnail_path))).all()
    ):
        for p in col:
            if p:
                allowed.append(Path(p))
    for col in (await db.execute(select(AdPreset.asset_path))).all():
        if col[0]:
            allowed.append(Path(col[0]))
    for col in (await db.execute(select(BackgroundAsset.file_path, BackgroundAsset.thumbnail_path))).all():
        for p in col:
            if p:
                allowed.append(Path(p))
    for col in (await db.execute(select(Clip.preview_path, Clip.export_path))).all():
        for p in col:
            if p:
                allowed.append(Path(p))
    return allowed


def _guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in EXTRA_MIME_TYPES:
        return EXTRA_MIME_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


async def _file_range_iterator(path: Path, start: int, end: int) -> AsyncIterator[bytes]:
    async with aiofiles.open(path, "rb") as f:
        await f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            to_read = min(CHUNK_SIZE, remaining)
            chunk = await f.read(to_read)
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@router.get("/stream")
async def stream_media(
    request: Request,
    path: str = Query(..., description="Absolute path to media file"),
    db: AsyncSession = Depends(get_db),
):
    target = Path(path)
    if not is_safe_media_path(target):
        allowed_roots = await _collect_allowed_roots(db)
        if not is_safe_media_path(target, allowed_roots):
            raise HTTPException(
                status_code=403,
                detail="Доступ запрещён: файл находится вне рабочей директории и не зарегистрирован в проекте.",
            )
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Медиафайл не найден")

    file_size = target.stat().st_size
    media_type = _guess_media_type(target)
    range_header = request.headers.get("range")

    if not range_header:
        return FileResponse(
            path=str(target),
            media_type=media_type,
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "no-cache",
            },
        )

    if file_size == 0:
        raise HTTPException(
            status_code=416,
            detail="Файл пуст",
            headers={"Content-Range": "bytes */0"},
        )

    if not range_header.startswith("bytes="):
        raise HTTPException(status_code=416, detail="Некорректный заголовок Range")

    range_spec = range_header.removeprefix("bytes=").split(",")[0].strip()
    start_str, _, end_str = range_spec.partition("-")

    try:
        if start_str == "":
            suffix_len = int(end_str)
            if suffix_len <= 0:
                raise ValueError("Invalid suffix length")
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            start = int(start_str)
            end = min(int(end_str), file_size - 1) if end_str else file_size - 1
    except ValueError as exc:
        raise HTTPException(
            status_code=416,
            detail="Некорректный диапазон байтов",
            headers={"Content-Range": f"bytes */{file_size}"},
        ) from exc

    if start < 0 or start >= file_size or start > end:
        raise HTTPException(
            status_code=416,
            detail="Диапазон выходит за пределы файла",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Cache-Control": "no-cache",
    }

    return StreamingResponse(
        _file_range_iterator(target, start, end),
        status_code=206,
        media_type=media_type,
        headers=headers,
    )
