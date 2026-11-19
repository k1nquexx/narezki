from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import PROJECTS_DIR, get_project_dir
from ..core.database import get_db
from ..core.models import Clip, Project, SubtitlePreset, new_id
from ..core.queue_manager import queue_manager
from ..core.schemas import (
    AnalyzeProjectRequest,
    BasicCutRequest,
    BatchExportRequest,
    ClipOut,
    ImportLocalRequest,
    ImportUrlRequest,
    JobOut,
    ProjectOut,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def _slim_analysis_json(analysis: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(analysis, dict):
        return analysis
    return {k: v for k, v in analysis.items() if k not in ("words", "transcript")}


async def _to_project_out(db: AsyncSession, proj: Project) -> ProjectOut:
    count_res = await db.execute(select(func.count(Clip.id)).where(Clip.project_id == proj.id))
    clip_count = int(count_res.scalar() or 0)
    data = ProjectOut.model_validate(proj)
    data.clip_count = clip_count
    return data


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: AsyncSession = Depends(get_db)) -> list[ProjectOut]:
    rows = (await db.execute(select(Project).order_by(Project.created_at.desc()))).scalars().all()
    counts_rows = (
        await db.execute(select(Clip.project_id, func.count(Clip.id)).group_by(Clip.project_id))
    ).all()
    counts = {pid: int(cnt) for pid, cnt in counts_rows}

    result: list[ProjectOut] = []
    for p in rows:
        out = ProjectOut.model_validate(p)
        out.analysis_json = _slim_analysis_json(out.analysis_json)
        out.clip_count = counts.get(p.id, 0)
        result.append(out)
    return result


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)) -> ProjectOut:
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    return await _to_project_out(db, proj)


@router.post("/import-local")
async def import_local_video(
    body: ImportLocalRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    src = Path(body.file_path)
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=400, detail=f"Файл не найден: {body.file_path}")

    project_id = new_id()
    get_project_dir(project_id)

    proj = Project(
        id=project_id,
        title=body.title.strip() if body.title and body.title.strip() else src.stem,
        source_type="local",
        source_path=str(src.resolve()),
        status="importing",
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)

    job: JobOut = await queue_manager.enqueue(
        job_type="ingest_local",
        project_id=proj.id,
        payload={"file_path": str(src.resolve())},
        message=f"Импорт {src.name}",
    )
    return {"project": await _to_project_out(db, proj), "job": job}


@router.post("/import-url")
async def import_url_video(
    body: ImportUrlRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    url = body.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(status_code=400, detail="Укажите корректный HTTP/HTTPS URL")

    project_id = new_id()
    proj_dir = get_project_dir(project_id)

    proj = Project(
        id=project_id,
        title=body.title.strip() if body.title and body.title.strip() else "Загрузка по ссылке...",
        source_type="url",
        source_url=url,
        source_path=str(proj_dir / "source.mp4"),
        status="importing",
    )
    db.add(proj)
    await db.commit()
    await db.refresh(proj)

    job: JobOut = await queue_manager.enqueue(
        job_type="ingest_url",
        project_id=proj.id,
        payload={"url": url},
        message="В очереди на скачивание (yt-dlp)",
    )
    return {"project": await _to_project_out(db, proj), "job": job}


@router.delete("/{project_id}")
async def delete_project(project_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    await queue_manager.cancel_project_jobs(project_id)
    await db.delete(proj)
    await db.commit()

    proj_dir = (PROJECTS_DIR / project_id).resolve()
    if proj_dir.exists() and PROJECTS_DIR.resolve() in proj_dir.parents:
        shutil.rmtree(proj_dir, ignore_errors=True)
    return {"ok": True}


@router.post("/{project_id}/basic-cut")
async def basic_cut_project(
    project_id: str,
    body: BasicCutRequest,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if body.end_time <= body.start_time:
        raise HTTPException(status_code=400, detail="end_time должен быть больше start_time")

    clamped_end = (
        min(body.end_time, proj.duration)
        if (proj.duration and proj.duration > 0)
        else body.end_time
    )
    if clamped_end <= body.start_time:
        raise HTTPException(
            status_code=400,
            detail="start_time выходит за пределы длительности видео или >= end_time",
        )

    default_sub = (
        await db.execute(select(SubtitlePreset).where(SubtitlePreset.is_default.is_(True)))
    ).scalars().first()

    clip = Clip(
        project_id=proj.id,
        title=body.title or f"Нарезка {body.start_time:.1f}s - {clamped_end:.1f}s",
        start_time=body.start_time,
        end_time=clamped_end,
        category="manual",
        subtitle_preset_id=default_sub.id if default_sub else None,
        status="draft",
    )
    db.add(clip)
    await db.commit()
    await db.refresh(clip)

    job = await queue_manager.enqueue(
        job_type="basic_cut",
        project_id=proj.id,
        clip_id=clip.id,
        message="Нарезка отрезка FFmpeg",
    )
    return {"clip": ClipOut.model_validate(clip), "job": job}


@router.post("/{project_id}/analyze", response_model=JobOut)
async def analyze_project(
    project_id: str,
    body: AnalyzeProjectRequest,
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")
    if proj.status == "importing":
        raise HTTPException(status_code=409, detail="Дождитесь завершения импорта проекта")
    if body.max_duration < body.min_duration:
        raise HTTPException(status_code=400, detail="max_duration должен быть >= min_duration")

    return await queue_manager.enqueue(
        job_type="analyze",
        project_id=proj.id,
        payload=body.model_dump(),
        message="В очереди на AI-анализ (Audio + SceneDetect + Whisper + Хайлайты)",
    )


@router.post("/{project_id}/export-batch", response_model=JobOut)
async def export_project_batch(
    project_id: str,
    body: BatchExportRequest,
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    proj = await db.get(Project, project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Проект не найден")

    clip_ids = body.clip_ids
    if not clip_ids:
        rows = (
            await db.execute(
                select(Clip.id)
                .where(Clip.project_id == project_id, Clip.selected_for_export.is_(True))
                .order_by(Clip.virality_score.desc(), Clip.start_time.asc())
            )
        ).all()
        clip_ids = [r[0] for r in rows]

    if not clip_ids:
        raise HTTPException(status_code=400, detail="Нет выбранных клипов для батч-экспорта")

    return await queue_manager.enqueue(
        job_type="export_batch",
        project_id=proj.id,
        payload={"clip_ids": clip_ids, "output_dir": body.output_dir},
        message=f"Батч-экспорт ({len(clip_ids)} клипов)",
    )
