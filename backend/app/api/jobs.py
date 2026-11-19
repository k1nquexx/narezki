from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.database import get_db
from ..core.models import Job
from ..core.queue_manager import queue_manager
from ..core.schemas import JobOut

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
async def list_jobs(
    project_id: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    stmt = select(Job).order_by(Job.created_at.desc()).limit(limit)
    if project_id:
        stmt = select(Job).where(Job.project_id == project_id).order_by(Job.created_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return [JobOut.model_validate(r) for r in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)) -> JobOut:
    job = await db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return JobOut.model_validate(job)


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str) -> JobOut:
    cancelled = await queue_manager.cancel_job(job_id)
    if cancelled is None:
        raise HTTPException(status_code=404, detail="Задача не найдена")
    return cancelled


@router.delete("/completed")
async def clear_completed_jobs(db: AsyncSession = Depends(get_db)) -> dict[str, int]:
    res = await db.execute(
        delete(Job).where(Job.status.in_(["completed", "failed", "cancelled"]))
    )
    await db.commit()
    return {"deleted": int(res.rowcount or 0)}
