from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy import select

from .cuda_env import get_gpu_info_async
from .database import AsyncSessionLocal, load_settings
from .models import Job
from .schemas import JobOut
from .ws_manager import ws_manager

logger = logging.getLogger("narezki.queue")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobCancelledError(Exception):
    """Raised when an active job is cancelled by the user."""


class _DynamicSemaphore:
    """Async semaphore whose concurrency limit can be updated in place without replacing the instance."""

    def __init__(self, limit: int = 2) -> None:
        self._limit = max(1, int(limit))
        self._active = 0
        self._waiters: list[asyncio.Future[None]] = []

    @property
    def limit(self) -> int:
        return self._limit

    def _wake_waiters(self) -> None:
        while self._active < self._limit and self._waiters:
            fut = self._waiters.pop(0)
            if not fut.done():
                self._active += 1
                fut.set_result(None)

    def set_limit(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._wake_waiters()

    async def acquire(self) -> None:
        while self._active >= self._limit:
            fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            self._waiters.append(fut)
            try:
                await fut
                return
            except asyncio.CancelledError:
                if fut in self._waiters:
                    self._waiters.remove(fut)
                if fut.done() and not fut.cancelled():
                    self.release()
                raise
        self._active += 1

    def release(self) -> None:
        if self._active > 0:
            self._active -= 1
        self._wake_waiters()

    async def __aenter__(self) -> "_DynamicSemaphore":
        await self.acquire()
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        self.release()


@dataclass
class JobContext:
    job_id: str
    job_type: str
    project_id: Optional[str]
    clip_id: Optional[str]
    payload: dict[str, Any]
    cancel_event: asyncio.Event
    gpu_semaphore: asyncio.Semaphore
    render_semaphore: asyncio.Semaphore | _DynamicSemaphore
    _subprocesses: set[asyncio.subprocess.Process] = field(default_factory=set)

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelledError(f"Задача {self.job_id} отменена пользователем")

    def register_process(self, proc: asyncio.subprocess.Process) -> None:
        self._subprocesses.add(proc)

    def unregister_process(self, proc: asyncio.subprocess.Process) -> None:
        self._subprocesses.discard(proc)

    def terminate_subprocesses(self) -> None:
        for proc in list(self._subprocesses):
            try:
                if proc.returncode is None:
                    if sys.platform == "win32":
                        proc.kill()
                    else:
                        proc.terminate()
            except Exception:
                pass

    async def update_progress(
        self,
        progress: float,
        stage: Optional[str] = None,
        message: Optional[str] = None,
    ) -> None:
        self.check_cancelled()
        clamped = max(0.0, min(100.0, float(progress)))
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, self.job_id)
            if job is None:
                return
            job.progress = clamped
            if stage is not None:
                job.stage = stage
            if message is not None:
                job.message = message
            job.updated_at = _utcnow()
            await session.commit()
            await session.refresh(job)
            job_dict = JobOut.model_validate(job).model_dump(mode="json")

        await ws_manager.emit_job_update(job_dict)

    async def log(self, message: str, level: str = "info", stage: Optional[str] = None) -> None:
        await ws_manager.log(message, level=level, job_id=self.job_id, stage=stage or self.job_type)


JobHandler = Callable[[JobContext], Awaitable[Optional[dict[str, Any]]]]


class QueueManager:
    """Async SQLite-backed job queue with GPU serialization and cancellation support."""

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker_tasks: list[asyncio.Task[None]] = []
        self._active_tasks: dict[str, asyncio.Task[None]] = {}
        self._contexts: dict[str, JobContext] = {}
        self._gpu_semaphore = asyncio.Semaphore(1)
        self._render_limit: int = 2
        self._render_semaphore = _DynamicSemaphore(self._render_limit)
        self._started = False

    def register_handler(self, job_type: str, handler: JobHandler) -> None:
        self._handlers[job_type] = handler

    async def start(self, num_workers: int = 4) -> None:
        if self._started:
            return

        async with AsyncSessionLocal() as session:
            settings = await load_settings(session)
            self._render_limit = max(1, min(4, settings.max_render_workers))
            self._render_semaphore.set_limit(self._render_limit)

            # Mark stale running jobs from an unexpected shutdown as failed
            running_rows = (
                await session.execute(select(Job).where(Job.status == "running"))
            ).scalars().all()
            for row in running_rows:
                row.status = "failed"
                row.stage = "interrupted"
                row.error = "Задача была прервана при прошлом завершении работы приложения."
                row.finished_at = _utcnow()

            # Re-enqueue pending queued jobs
            queued_rows = (
                await session.execute(
                    select(Job).where(Job.status == "queued").order_by(Job.created_at.asc())
                )
            ).scalars().all()
            await session.commit()

            for row in queued_rows:
                await self._queue.put(row.id)

        self._started = True
        effective_workers = max(num_workers, self._render_limit)
        for i in range(effective_workers):
            task = asyncio.create_task(self._worker_loop(i), name=f"narezki-worker-{i}")
            self._worker_tasks.append(task)
        logger.info("QueueManager started with %d workers", effective_workers)

    async def stop(self) -> None:
        self._started = False
        for ctx in list(self._contexts.values()):
            ctx.cancel_event.set()
            ctx.terminate_subprocesses()
        for task in list(self._active_tasks.values()):
            task.cancel()
        for task in self._worker_tasks:
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)
        self._worker_tasks.clear()
        self._active_tasks.clear()
        self._contexts.clear()

    def set_render_concurrency(self, limit: int) -> None:
        clamped = max(1, min(4, int(limit)))
        if clamped != self._render_limit:
            self._render_limit = clamped
            self._render_semaphore.set_limit(clamped)

    async def _cancel_jobs_where(self, *conditions: Any) -> int:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(Job.id).where(
                        *conditions,
                        Job.status.in_(["queued", "running"]),
                    )
                )
            ).all()
            job_ids = [r[0] for r in rows]

        cancelled_count = 0
        for jid in job_ids:
            if await self.cancel_job(jid) is not None:
                cancelled_count += 1
        return cancelled_count

    async def cancel_project_jobs(self, project_id: str) -> int:
        return await self._cancel_jobs_where(Job.project_id == project_id)

    async def cancel_clip_jobs(self, clip_id: str) -> int:
        return await self._cancel_jobs_where(Job.clip_id == clip_id)

    async def enqueue(
        self,
        job_type: str,
        payload: Optional[dict[str, Any]] = None,
        project_id: Optional[str] = None,
        clip_id: Optional[str] = None,
        message: str = "В очереди",
    ) -> JobOut:
        async with AsyncSessionLocal() as session:
            job = Job(
                job_type=job_type,
                project_id=project_id,
                clip_id=clip_id,
                status="queued",
                stage="queued",
                progress=0.0,
                message=message,
                payload_json=payload or {},
            )
            session.add(job)
            await session.commit()
            await session.refresh(job)
            out = JobOut.model_validate(job)

        await ws_manager.emit_job_update(out.model_dump(mode="json"))
        await self._queue.put(out.id)
        return out

    async def cancel_job(self, job_id: str) -> Optional[JobOut]:
        ctx = self._contexts.get(job_id)
        if ctx is not None:
            ctx.cancel_event.set()
            ctx.terminate_subprocesses()

        task = self._active_tasks.get(job_id)
        if task is not None and not task.done():
            task.cancel()

        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None:
                return None
            if job.status in ("queued", "running"):
                job.status = "cancelled"
                job.stage = "cancelled"
                job.message = "Отменено пользователем"
                job.finished_at = _utcnow()
                job.updated_at = _utcnow()
                await session.commit()
                await session.refresh(job)
            out = JobOut.model_validate(job)

        await ws_manager.emit_job_update(out.model_dump(mode="json"))
        await ws_manager.log(f"Задача {job_id} отменена", level="warning", job_id=job_id)
        return out

    async def _worker_loop(self, worker_idx: int) -> None:
        while self._started:
            try:
                job_id = await self._queue.get()
            except asyncio.CancelledError:
                break

            try:
                await self._execute_job(job_id)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Worker-%d unhandled exception for job %s: %s", worker_idx, job_id, exc)
            finally:
                self._queue.task_done()

    async def _execute_job(self, job_id: str) -> None:
        async with AsyncSessionLocal() as session:
            job = await session.get(Job, job_id)
            if job is None or job.status == "cancelled":
                return
            handler = self._handlers.get(job.job_type)
            if handler is None:
                job.status = "failed"
                job.stage = "error"
                job.error = f"Неизвестный тип задачи: {job.job_type}"
                job.finished_at = _utcnow()
                await session.commit()
                await session.refresh(job)
                await ws_manager.emit_job_update(JobOut.model_validate(job).model_dump(mode="json"))
                return

            job.status = "running"
            job.stage = "starting"
            job.started_at = _utcnow()
            job.updated_at = _utcnow()
            await session.commit()
            await session.refresh(job)
            job_snapshot = JobOut.model_validate(job)

        await ws_manager.emit_job_update(job_snapshot.model_dump(mode="json"))

        cancel_event = asyncio.Event()
        ctx = JobContext(
            job_id=job_snapshot.id,
            job_type=job_snapshot.job_type,
            project_id=job_snapshot.project_id,
            clip_id=job_snapshot.clip_id,
            payload=job_snapshot.payload_json,
            cancel_event=cancel_event,
            gpu_semaphore=self._gpu_semaphore,
            render_semaphore=self._render_semaphore,
        )
        self._contexts[job_id] = ctx

        try:
            handler_task = asyncio.create_task(handler(ctx), name=f"narezki-job-{job_id}")
            self._active_tasks[job_id] = handler_task
            result = await handler_task
            ctx.check_cancelled()

            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)
                if job is not None and job.status != "cancelled":
                    job.status = "completed"
                    job.stage = "done"
                    job.progress = 100.0
                    if not job.message or job.message == "В очереди":
                        job.message = "Готово"
                    job.result_json = result
                    job.finished_at = _utcnow()
                    job.updated_at = _utcnow()
                    await session.commit()
                    await session.refresh(job)
                    await ws_manager.emit_job_update(JobOut.model_validate(job).model_dump(mode="json"))

        except (JobCancelledError, asyncio.CancelledError):
            ctx.terminate_subprocesses()
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)
                if job is not None:
                    job.status = "cancelled"
                    job.stage = "cancelled"
                    job.message = "Отменено пользователем"
                    job.finished_at = _utcnow()
                    job.updated_at = _utcnow()
                    await session.commit()
                    await session.refresh(job)
                    await ws_manager.emit_job_update(JobOut.model_validate(job).model_dump(mode="json"))
        except Exception as exc:
            logger.exception("Job %s (%s) failed: %s", job_id, job_snapshot.job_type, exc)
            err_text = str(exc) or exc.__class__.__name__
            async with AsyncSessionLocal() as session:
                job = await session.get(Job, job_id)
                if job is not None:
                    job.status = "failed"
                    job.stage = "error"
                    job.error = err_text
                    job.message = f"Ошибка: {err_text[:180]}"
                    job.finished_at = _utcnow()
                    job.updated_at = _utcnow()
                    await session.commit()
                    await session.refresh(job)
                    await ws_manager.emit_job_update(JobOut.model_validate(job).model_dump(mode="json"))
            await ws_manager.log(f"Ошибка задачи {job_id}: {err_text}", level="error", job_id=job_id)
        finally:
            self._contexts.pop(job_id, None)
            self._active_tasks.pop(job_id, None)
            await ws_manager.emit_gpu_status(await get_gpu_info_async())


queue_manager = QueueManager()
