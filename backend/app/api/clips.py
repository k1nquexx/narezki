from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_db
from ..core.models import Clip, Project, SubtitlePreset
from ..core.queue_manager import queue_manager
from ..core.schemas import ClipCreate, ClipOut, ClipUpdate, JobOut
from ..services.analyzer.llm_highlights import build_clip_subtitles

router = APIRouter(prefix="/api/clips", tags=["clips"])


@router.get("", response_model=list[ClipOut])
async def list_clips(
    project_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
) -> list[ClipOut]:
    rows = (
        await db.execute(
            select(Clip)
            .where(Clip.project_id == project_id)
            .order_by(Clip.virality_score.desc(), Clip.start_time.asc())
        )
    ).scalars().all()
    return [ClipOut.model_validate(r) for r in rows]


@router.get("/{clip_id}", response_model=ClipOut)
async def get_clip(clip_id: str, db: AsyncSession = Depends(get_db)) -> ClipOut:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    return ClipOut.model_validate(clip)


@router.post("", response_model=ClipOut)
async def create_clip(body: ClipCreate, db: AsyncSession = Depends(get_db)) -> ClipOut:
    proj = await db.get(Project, body.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="Конец клипа должен быть больше начала")

    preset_id = body.subtitle_preset_id
    if preset_id is None:
        default_sub = (
            await db.execute(select(SubtitlePreset).where(SubtitlePreset.is_default.is_(True)))
        ).scalars().first()
        preset_id = default_sub.id if default_sub else None

    clip = Clip(
        project_id=body.project_id,
        title=body.title,
        hook_text=body.hook_text,
        hook_enabled=body.hook_enabled,
        description=body.description,
        tags_json=body.tags_json,
        category=body.category,
        start_time=body.start_time,
        end_time=body.end_time,
        crop_mode=body.crop_mode,
        background_asset_id=body.background_asset_id,
        subtitle_preset_id=preset_id,
        subtitles_enabled=body.subtitles_enabled,
        ad_preset_id=body.ad_preset_id,
        anti_cid_enabled=body.anti_cid_enabled,
    )
    db.add(clip)
    await db.commit()
    await db.refresh(clip)
    return ClipOut.model_validate(clip)


@router.patch("/{clip_id}", response_model=ClipOut)
async def update_clip(
    clip_id: str,
    body: ClipUpdate,
    db: AsyncSession = Depends(get_db),
) -> ClipOut:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")

    updates = body.model_dump(exclude_unset=True)
    next_start = updates.get("start_time", clip.start_time)
    next_end = updates.get("end_time", clip.end_time)
    if next_end <= next_start:
        raise HTTPException(status_code=400, detail="Конец клипа должен быть больше начала")

    if "subtitles_json" in updates and updates["subtitles_json"] is not None:
        updates["subtitles_json"] = [
            c.model_dump() if hasattr(c, "model_dump") else c for c in updates["subtitles_json"]
        ]
    if "track_data_json" in updates and updates["track_data_json"] is not None:
        val = updates["track_data_json"]
        updates["track_data_json"] = val.model_dump() if hasattr(val, "model_dump") else val
    if "ad_override_json" in updates and updates["ad_override_json"] is not None:
        val = updates["ad_override_json"]
        updates["ad_override_json"] = val.model_dump() if hasattr(val, "model_dump") else val
    if "anti_cid_json" in updates and updates["anti_cid_json"] is not None:
        val = updates["anti_cid_json"]
        updates["anti_cid_json"] = val.model_dump() if hasattr(val, "model_dump") else val

    # Invalidate old tracking if trim range changed substantially
    if (
        ("start_time" in updates or "end_time" in updates)
        and "track_data_json" not in updates
        and clip.track_data_json is not None
    ):
        td = clip.track_data_json
        if (
            abs(float(td.get("clip_start", -1)) - float(next_start)) > 0.5
            or abs(float(td.get("clip_end", -1)) - float(next_end)) > 0.5
        ):
            clip.track_data_json = None

    for k, v in updates.items():
        setattr(clip, k, v)

    await db.commit()
    await db.refresh(clip)
    return ClipOut.model_validate(clip)


@router.delete("/{clip_id}")
async def delete_clip(clip_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    await db.delete(clip)
    await db.commit()
    return {"ok": True}


@router.post("/{clip_id}/resync-subtitles")
async def resync_clip_subtitles(clip_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    proj = await db.get(Project, clip.project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    words = []
    if proj.analysis_json and isinstance(proj.analysis_json, dict):
        words = proj.analysis_json.get("words") or []

    words_per_line = 3
    if clip.subtitle_preset_id:
        preset = await db.get(SubtitlePreset, clip.subtitle_preset_id)
        if preset:
            words_per_line = preset.words_per_line

    cues = build_clip_subtitles(words, clip.start_time, clip.end_time, words_per_line=words_per_line)
    clip.subtitles_json = cues
    await db.commit()
    await db.refresh(clip)
    return {"cues": cues, "clip": ClipOut.model_validate(clip)}


@router.post("/{clip_id}/track", response_model=JobOut)
async def track_clip_endpoint(clip_id: str, db: AsyncSession = Depends(get_db)) -> JobOut:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    return await queue_manager.enqueue(
        job_type="track_clip",
        project_id=clip.project_id,
        clip_id=clip.id,
        message=f"YOLOv8 трекинг: {clip.title}",
    )


@router.post("/{clip_id}/render-preview", response_model=JobOut)
async def render_clip_preview_endpoint(clip_id: str, db: AsyncSession = Depends(get_db)) -> JobOut:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    return await queue_manager.enqueue(
        job_type="render_preview",
        project_id=clip.project_id,
        clip_id=clip.id,
        message=f"Рендер превью 9:16: {clip.title}",
    )


@router.post("/{clip_id}/export", response_model=JobOut)
async def export_clip_endpoint(clip_id: str, db: AsyncSession = Depends(get_db)) -> JobOut:
    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")
    return await queue_manager.enqueue(
        job_type="export_clip",
        project_id=clip.project_id,
        clip_id=clip.id,
        message=f"Экспорт клипа: {clip.title}",
    )
