from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import WebSocket

logger = logging.getLogger("narezki.ws")


class ConnectionManager:
    """Manages WebSocket clients and broadcasts job progress, logs, and GPU telemetry."""

    def __init__(self, max_log_history: int = 200) -> None:
        self._connections: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._log_history: deque[dict[str, Any]] = deque(maxlen=max_log_history)

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)
            history = list(self._log_history)

        for entry in history:
            try:
                await websocket.send_json(entry)
            except Exception:
                await self.disconnect(websocket)
                break

    async def disconnect(self, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        payload = {
            "type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        if event_type == "log":
            self._log_history.append(payload)

        async with self._lock:
            targets = list(self._connections)

        dead: list[WebSocket] = []
        for ws in targets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    async def log(
        self,
        message: str,
        level: str = "info",
        job_id: Optional[str] = None,
        stage: Optional[str] = None,
    ) -> None:
        log_fn = getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")
        log_fn("[%s] %s", stage or "system", message)
        await self.broadcast(
            "log",
            {
                "level": level,
                "message": message,
                "job_id": job_id,
                "stage": stage,
            },
        )

    async def emit_job_update(self, job_dict: dict[str, Any]) -> None:
        await self.broadcast("job_update", job_dict)

    async def emit_gpu_status(self, gpu_dict: dict[str, Any]) -> None:
        await self.broadcast("gpu_status", gpu_dict)

    async def emit_project_update(self, project_id: str, status: str, **extra: Any) -> None:
        await self.broadcast(
            "project_update",
            {"project_id": project_id, "status": status, **extra},
        )

    async def emit_clip_update(self, clip_id: str, project_id: str, status: str, **extra: Any) -> None:
        await self.broadcast(
            "clip_update",
            {"clip_id": clip_id, "project_id": project_id, "status": status, **extra},
        )


ws_manager = ConnectionManager()
