from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import AppSettingsModel, get_ffmpeg_path, get_ffprobe_path
from ..core.cuda_env import get_gpu_info_async, release_vram_async
from ..core.database import get_db, load_settings, save_settings
from ..core.queue_manager import queue_manager
from ..core.schemas import EncoderProbeOut, GPUInfoOut, LLMTestRequest, LLMTestResponse
from ..core.ws_manager import ws_manager
from ..services.renderer.encoder_detect import probe_encoders

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/health")
async def health_check() -> dict[str, Any]:
    ffmpeg_ok = False
    ffprobe_ok = False
    ffmpeg_path = None
    ffprobe_path = None
    try:
        ffmpeg_path = get_ffmpeg_path()
        ffmpeg_ok = True
    except FileNotFoundError:
        pass
    try:
        ffprobe_path = get_ffprobe_path()
        ffprobe_ok = True
    except FileNotFoundError:
        pass

    gpu = await get_gpu_info_async()
    return {
        "status": "ok",
        "ffmpeg_ok": ffmpeg_ok,
        "ffprobe_ok": ffprobe_ok,
        "ffmpeg_path": ffmpeg_path,
        "ffprobe_path": ffprobe_path,
        "cuda_available": gpu["cuda_available"],
        "gpu_name": gpu["device_name"],
    }


@router.get("/gpu", response_model=GPUInfoOut)
async def gpu_status() -> GPUInfoOut:
    info = await get_gpu_info_async()
    await ws_manager.emit_gpu_status(info)
    return GPUInfoOut(**info)


@router.post("/gpu/release", response_model=GPUInfoOut)
async def force_release_vram() -> GPUInfoOut:
    info = await release_vram_async("manual")
    await ws_manager.emit_gpu_status(info)
    await ws_manager.log(
        f"Принудительная очистка VRAM выполнена ({info['vram_used_mb']}/{info['vram_total_mb']} MB)",
        stage="vram",
    )
    return GPUInfoOut(**info)


@router.get("/encoders", response_model=EncoderProbeOut)
async def get_encoders(refresh: bool = Query(default=False)) -> EncoderProbeOut:
    result = await probe_encoders(force=refresh)
    return EncoderProbeOut(**result)


@router.get("/settings", response_model=AppSettingsModel)
async def get_settings(db: AsyncSession = Depends(get_db)) -> AppSettingsModel:
    return await load_settings(db)


@router.put("/settings", response_model=AppSettingsModel)
async def update_settings(
    body: AppSettingsModel,
    db: AsyncSession = Depends(get_db),
) -> AppSettingsModel:
    saved = await save_settings(db, body)
    queue_manager.set_render_concurrency(saved.max_render_workers)
    await ws_manager.log("Настройки приложения обновлены", stage="settings")
    return saved


@router.post("/test-llm", response_model=LLMTestResponse)
async def test_llm_connection(
    body: LLMTestRequest,
    db: AsyncSession = Depends(get_db),
) -> LLMTestResponse:
    saved = await load_settings(db)
    base_url = (body.base_url or saved.llm_base_url).strip()
    api_key = (body.api_key if body.api_key is not None else saved.llm_api_key).strip()
    model = (body.model or saved.llm_model).strip()

    if not api_key:
        return LLMTestResponse(
            ok=False,
            model=model,
            latency_ms=0,
            error="API ключ не указан. Введите ключ в настройках или используйте локальный эвристический анализ.",
        )

    t0 = time.perf_counter()
    try:
        async with AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=20.0) as client:
            resp = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Reply with one short word: OK"},
                    {"role": "user", "content": "ping"},
                ],
                max_tokens=16,
                temperature=0.0,
            )
        latency_ms = int((time.perf_counter() - t0) * 1000)
        reply = (resp.choices[0].message.content or "").strip() if resp.choices else "OK"
        return LLMTestResponse(ok=True, model=model, latency_ms=latency_ms, reply=reply)
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return LLMTestResponse(
            ok=False,
            model=model,
            latency_ms=latency_ms,
            error=str(exc),
        )
