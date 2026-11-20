from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import LIVE_BUFFER_DIR
from ..core.database import get_db
from ..core.models import Clip, LiveChannel, Project
from ..core.schemas import (
    ClipOut,
    LiveChannelCreate,
    LiveChannelOut,
    LiveChannelUpdate,
    LiveClipFeedOut,
    LiveClipNowRequest,
)
from ..services.live.live_watcher import (
    extract_channel_display_name,
    live_watcher,
    normalize_channel_url,
)

router = APIRouter(prefix="/api/live", tags=["live"])


def _serialize_channel(ch: LiveChannel) -> LiveChannelOut:
    out = LiveChannelOut.model_validate(ch)
    out.runtime = live_watcher.get_runtime_state(ch.id)
    return out


@router.get("/channels", response_model=list[LiveChannelOut])
async def list_live_channels(db: AsyncSession = Depends(get_db)) -> list[LiveChannelOut]:
    rows = (
        await db.execute(select(LiveChannel).order_by(LiveChannel.created_at.desc()))
    ).scalars().all()
    return [_serialize_channel(r) for r in rows]


@router.post("/channels", response_model=LiveChannelOut)
async def create_live_channel(
    body: LiveChannelCreate,
    db: AsyncSession = Depends(get_db),
) -> LiveChannelOut:
    try:
        platform, normalized_url = normalize_channel_url(body.channel_url, body.platform)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    display_name = (body.name or "").strip() or extract_channel_display_name(platform, normalized_url)
    ch = LiveChannel(
        name=display_name[:120],
        platform=platform,
        channel_url=normalized_url,
        enabled=body.enabled,
        check_interval_sec=body.check_interval_sec,
        buffer_minutes=body.buffer_minutes,
        ai_mode=body.ai_mode,
        chat_sensitivity=body.chat_sensitivity,
        audio_sensitivity=body.audio_sensitivity,
        clip_pre_sec=body.clip_pre_sec,
        clip_post_sec=body.clip_post_sec,
        min_clip_gap_sec=body.min_clip_gap_sec,
        crop_mode=body.crop_mode,
        subtitle_preset_id=body.subtitle_preset_id,
        ad_preset_id=body.ad_preset_id,
        background_asset_id=body.background_asset_id,
        anti_cid_enabled=body.anti_cid_enabled,
        auto_publish=body.auto_publish,
        publish_account_ids_json=list(body.publish_account_ids_json),
    )
    db.add(ch)
    await db.commit()
    await db.refresh(ch)
    return _serialize_channel(ch)


@router.patch("/channels/{channel_id}", response_model=LiveChannelOut)
async def update_live_channel(
    channel_id: str,
    body: LiveChannelUpdate,
    db: AsyncSession = Depends(get_db),
) -> LiveChannelOut:
    ch = await db.get(LiveChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Канал не найден")

    updates = body.model_dump(exclude_unset=True)
    if "channel_url" in updates and updates["channel_url"]:
        try:
            platform, normalized_url = normalize_channel_url(updates["channel_url"], None)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ch.platform = platform
        ch.channel_url = normalized_url
        updates.pop("channel_url", None)

    for key, val in updates.items():
        setattr(ch, key, val)

    await db.commit()
    await db.refresh(ch)
    return _serialize_channel(ch)


@router.delete("/channels/{channel_id}")
async def delete_live_channel(channel_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    ch = await db.get(LiveChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Канал не найден")

    await live_watcher.stop_channel(channel_id)

    buf_root = LIVE_BUFFER_DIR.resolve()
    buf_dir = (LIVE_BUFFER_DIR / channel_id).resolve()
    if buf_dir.exists() and buf_root in buf_dir.parents:
        with suppress(OSError):
            await asyncio.to_thread(shutil.rmtree, buf_dir, True)

    ch = await db.get(LiveChannel, channel_id)
    if ch is not None:
        await db.delete(ch)
        await db.commit()
    return {"ok": True}


@router.post("/channels/{channel_id}/start", response_model=LiveChannelOut)
async def start_live_channel(channel_id: str, db: AsyncSession = Depends(get_db)) -> LiveChannelOut:
    try:
        await live_watcher.start_channel(channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ch = await db.get(LiveChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Канал не найден")
    await db.refresh(ch)
    return _serialize_channel(ch)


@router.post("/channels/{channel_id}/stop", response_model=LiveChannelOut)
async def stop_live_channel(channel_id: str, db: AsyncSession = Depends(get_db)) -> LiveChannelOut:
    ch = await db.get(LiveChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Канал не найден")
    await live_watcher.stop_channel(channel_id)
    await db.refresh(ch)
    return _serialize_channel(ch)


@router.post("/channels/{channel_id}/check")
async def check_live_channel(channel_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    try:
        probe = await live_watcher.check_channel_now(channel_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ch = await db.get(LiveChannel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Канал не найден")
    await db.refresh(ch)
    return {"probe": probe, "channel": _serialize_channel(ch).model_dump(mode="json")}


@router.post("/channels/{channel_id}/clip-now")
async def clip_live_channel_now(channel_id: str, body: LiveClipNowRequest) -> dict[str, Any]:
    try:
        return await live_watcher.clip_now(channel_id, seconds=body.seconds)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/clips", response_model=list[LiveClipFeedOut])
async def list_live_clips(
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[LiveClipFeedOut]:
    rows = (
        await db.execute(
            select(Clip, Project)
            .join(Project, Clip.project_id == Project.id)
            .where(Project.source_type == "live")
            .order_by(Clip.created_at.desc())
            .limit(limit)
        )
    ).all()

    items: list[LiveClipFeedOut] = []
    for clip, project in rows:
        meta = project.analysis_json if isinstance(project.analysis_json, dict) else {}
        items.append(
            LiveClipFeedOut(
                clip=ClipOut.model_validate(clip),
                channel_id=meta.get("live_channel_id"),
                channel_name=str(meta.get("channel_name") or project.title),
                platform=str(meta.get("platform") or ""),
            )
        )
    return items
