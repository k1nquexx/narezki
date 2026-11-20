from __future__ import annotations

from app.core.cuda_env import get_gpu_info_async, release_vram_async, setup_cuda_dlls

setup_cuda_dlls()

import asyncio
import logging
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import jobs, media, presets, system
from app.core.config import ensure_data_dirs
from app.core.database import init_db
from app.core.queue_manager import queue_manager
from app.core.ws_manager import ws_manager
from app.services.renderer.encoder_detect import probe_encoders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("narezki.main")


async def _gpu_telemetry_loop() -> None:
    while True:
        try:
            await asyncio.sleep(5.0)
            info = await get_gpu_info_async()
            await ws_manager.emit_gpu_status(info)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("GPU telemetry loop error: %s", exc)


def _register_job_handlers() -> None:
    """Register job handlers for all initialized service modules."""
    # Handlers are wired as each milestone's service modules are added.
    try:
        from app.services.ingest.handlers import register_ingest_handlers

        register_ingest_handlers(queue_manager)
    except ImportError:
        pass
    try:
        from app.services.analyzer.handlers import register_analyzer_handlers

        register_analyzer_handlers(queue_manager)
    except ImportError:
        pass
    try:
        from app.services.renderer.handlers import register_render_handlers

        register_render_handlers(queue_manager)
    except ImportError:
        pass
    try:
        from app.services.live.handlers import register_live_handlers

        register_live_handlers(queue_manager)
    except ImportError:
        pass


@asynccontextmanager
async def lifespan(_app: FastAPI):
    setup_cuda_dlls()
    ensure_data_dirs()
    await init_db()
    _register_job_handlers()
    await probe_encoders()
    await queue_manager.start()

    try:
        from app.services.live.live_watcher import live_watcher
        from app.services.live.publisher import publisher_service

        await live_watcher.start()
        await publisher_service.start()
    except ImportError:
        live_watcher = None  # type: ignore[assignment]
        publisher_service = None  # type: ignore[assignment]

    gpu = await get_gpu_info_async()
    await ws_manager.log(
        f"Narezki Backend запущен. GPU: {gpu['device_name']} ({gpu['vram_total_mb']} MB VRAM)",
        stage="startup",
    )
    telemetry_task = asyncio.create_task(_gpu_telemetry_loop(), name="gpu-telemetry")
    try:
        yield
    finally:
        telemetry_task.cancel()
        with suppress(asyncio.CancelledError):
            await telemetry_task
        try:
            from app.services.live.live_watcher import live_watcher
            from app.services.live.publisher import publisher_service

            await live_watcher.stop()
            await publisher_service.stop()
        except ImportError:
            pass
        await queue_manager.stop()
        await release_vram_async("shutdown")


app = FastAPI(
    title="Narezki API",
    version="0.1.0",
    description="AI Vertical Video Clipper Backend (9:16)",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "http://tauri.localhost",
        "https://tauri.localhost",
        "tauri://localhost",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "detail": "Ошибка валидации входных данных",
            "code": "validation_error",
            "errors": exc.errors(),
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(_request: Request, exc: Exception):
    logger.exception("Unhandled backend exception: %s", exc)
    await ws_manager.log(f"Внутренняя ошибка сервера: {exc}", level="error", stage="api")
    return JSONResponse(
        status_code=500,
        content={
            "detail": str(exc) or "Внутренняя ошибка сервера",
            "code": "internal_error",
        },
    )


app.include_router(system.router)
app.include_router(jobs.router)
app.include_router(media.router)
app.include_router(presets.router)

# Dynamically attach optional milestone routers when modules exist
try:
    from app.api import projects as projects_router

    app.include_router(projects_router.router)
except ImportError:
    pass

try:
    from app.api import clips as clips_router

    app.include_router(clips_router.router)
except ImportError:
    pass

try:
    from app.api import live as live_router

    app.include_router(live_router.router)
except ImportError:
    pass

try:
    from app.api import publishing as publishing_router

    app.include_router(publishing_router.router)
except ImportError:
    pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await ws_manager.connect(websocket)
    try:
        await websocket.send_json(
            {
                "type": "gpu_status",
                "data": await get_gpu_info_async(),
            }
        )
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception:
        await ws_manager.disconnect(websocket)
