from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select

from ...core.config import PROJECTS_DIR
from ...core.cuda_env import vram_guard
from ...core.database import AsyncSessionLocal, load_settings
from ...core.models import Clip, Project, SubtitlePreset
from ...core.queue_manager import JobCancelledError, JobContext
from ...core.ws_manager import ws_manager
from .audio_peaks import analyze_audio_peaks
from .llm_highlights import (
    analyze_highlights_heuristic,
    analyze_highlights_llm,
    finalize_highlight_clips,
)
from .scene_detect import detect_scenes
from .transcriber import transcribe_video


async def run_analysis_pipeline(ctx: JobContext) -> dict[str, Any]:
    project_id = ctx.project_id
    if not project_id:
        raise ValueError("Отсутствует project_id для анализа")

    mode = str(ctx.payload.get("mode", "auto"))
    min_duration = float(ctx.payload.get("min_duration", 15.0))
    max_duration = float(ctx.payload.get("max_duration", 60.0))
    focus_prompt = str(ctx.payload.get("focus_prompt", ""))

    async with AsyncSessionLocal() as session:
        proj = await session.get(Project, project_id)
        settings = await load_settings(session)
        if proj is None:
            raise ValueError("Проект не найден")
        source_path = Path(proj.source_path)
        analysis_video = Path(proj.proxy_path) if proj.proxy_path and Path(proj.proxy_path).exists() else source_path
        duration = float(proj.duration or 0.0)
        has_audio = bool(proj.has_audio)

        default_sub = (
            await session.execute(select(SubtitlePreset).where(SubtitlePreset.is_default.is_(True)))
        ).scalars().first()
        default_sub_id = default_sub.id if default_sub else None
        words_per_line = default_sub.words_per_line if default_sub else 3

        proj.status = "analyzing"
        proj.error_message = None
        await session.commit()

    await ws_manager.emit_project_update(project_id, "analyzing")

    try:
        # Step 1: Audio peaks (0 - 15%)
        await ctx.update_progress(3.0, "audio_peaks", "Анализ громкости и пиков аудио...")
        audio_data = await analyze_audio_peaks(
            source_path,
            duration=duration,
            has_audio=has_audio,
            cancel_event=ctx.cancel_event,
            register_proc=ctx.register_process,
            unregister_proc=ctx.unregister_process,
        )
        await ctx.update_progress(
            15.0,
            "audio_peaks",
            f"Найдено аудио-всплесков: {len(audio_data['peaks'])}",
        )
        await ctx.log(f"Аудио-анализ завершён: {len(audio_data['peaks'])} пиков")

        # Step 2: PySceneDetect (15 - 30%)
        await ctx.update_progress(18.0, "scene_detect", "Поиск склеек сцен (PySceneDetect)...")
        scene_data = await detect_scenes(analysis_video, cancel_event=ctx.cancel_event)
        await ctx.update_progress(
            30.0,
            "scene_detect",
            f"Склеек сцен: {len(scene_data['scenes'])}, экшен-монтажей: {len(scene_data['action_bursts'])}",
        )
        await ctx.log(
            f"PySceneDetect: {len(scene_data['scenes'])} склеек, {len(scene_data['action_bursts'])} экшен-эпизодов"
        )

        # Step 3: Whisper GPU transcription (30 - 75%) serialized on GPU semaphore
        async def _whisper_progress(pct: float, msg: str) -> None:
            mapped = 30.0 + (pct / 100.0) * 45.0
            await ctx.update_progress(mapped, "whisper", msg)

        await ctx.update_progress(31.0, "whisper", "Ожидание GPU для faster-whisper...")
        async with ctx.gpu_semaphore:
            async with vram_guard("faster-whisper", on_gpu_status=ws_manager.emit_gpu_status):
                whisper_data = await transcribe_video(
                    video_path=source_path,
                    duration=duration,
                    has_audio=has_audio,
                    model_size=settings.whisper_model_size,
                    device=settings.whisper_device,
                    compute_type=settings.whisper_compute_type,
                    language=settings.whisper_language,
                    on_progress=_whisper_progress,
                    cancel_event=ctx.cancel_event,
                    register_proc=ctx.register_process,
                    unregister_proc=ctx.unregister_process,
                )

        await ctx.log(
            f"Whisper завершён ({whisper_data['device']}/{whisper_data['compute_type']}): "
            f"{len(whisper_data['segments'])} фраз, {len(whisper_data['words'])} слов"
        )

        # Step 4: Highlight extraction (75 - 95%)
        max_highlights = int(ctx.payload.get("max_highlights") or settings.llm_max_highlights)
        use_llm = (mode == "llm") or (mode == "auto" and bool(settings.llm_api_key.strip()))

        if use_llm:
            await ctx.update_progress(
                78.0, "llm", f"Поиск хайлайтов через Cloud LLM ({settings.llm_model})..."
            )
            try:
                raw_items = await analyze_highlights_llm(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    temperature=settings.llm_temperature,
                    duration=duration,
                    transcript=whisper_data["segments"],
                    audio_peaks=audio_data["peaks"],
                    action_bursts=scene_data["action_bursts"],
                    max_highlights=max_highlights,
                    chunk_minutes=settings.llm_chunk_minutes,
                    min_duration=min_duration,
                    max_duration=max_duration,
                    focus_prompt=focus_prompt,
                    cancel_event=ctx.cancel_event,
                )
            except Exception as llm_exc:
                if mode == "llm":
                    raise
                await ctx.log(
                    f"Cloud LLM недоступен ({llm_exc}); переключение на локальный мультимодальный анализатор",
                    level="warning",
                    stage="llm",
                )
                raw_items = analyze_highlights_heuristic(
                    duration=duration,
                    transcript=whisper_data["segments"],
                    audio_peaks=audio_data["peaks"],
                    scenes=scene_data["scenes"],
                    action_bursts=scene_data["action_bursts"],
                    min_duration=min_duration,
                    max_duration=max_duration,
                    max_highlights=max_highlights,
                )
        else:
            await ctx.update_progress(
                78.0, "heuristic", "Мультимодальный локальный поиск хайлайтов..."
            )
            raw_items = analyze_highlights_heuristic(
                duration=duration,
                transcript=whisper_data["segments"],
                audio_peaks=audio_data["peaks"],
                scenes=scene_data["scenes"],
                action_bursts=scene_data["action_bursts"],
                min_duration=min_duration,
                max_duration=max_duration,
                max_highlights=max_highlights,
            )

        await ctx.update_progress(92.0, "saving", "Привязка границ клипов и сохранение в БД...")
        finalized = finalize_highlight_clips(
            raw_items=raw_items,
            scenes=scene_data["scenes"],
            words=whisper_data["words"],
            duration=duration,
            words_per_line=words_per_line,
        )

        analysis_payload = {
            "scenes": scene_data["scenes"],
            "action_bursts": scene_data["action_bursts"],
            "audio_peaks": audio_data,
            "transcript": whisper_data["segments"],
            "words": whisper_data["words"],
            "language": whisper_data["language"],
            "summary": f"Клипов: {len(finalized)}, сцен: {len(scene_data['scenes'])}, пиков: {len(audio_data['peaks'])}",
        }

        async with AsyncSessionLocal() as session:
            proj = await session.get(Project, project_id)
            if proj is not None:
                proj.analysis_json = analysis_payload
                proj.status = "analyzed"

            # Clean up orphaned preview files for auto-generated clips before replacing
            old_auto_previews = (
                await session.execute(
                    select(Clip.preview_path).where(
                        Clip.project_id == project_id,
                        Clip.category != "manual",
                        Clip.preview_path.is_not(None),
                    )
                )
            ).scalars().all()
            projects_root = PROJECTS_DIR.resolve()
            for p_str in old_auto_previews:
                if not p_str:
                    continue
                try:
                    p = Path(p_str).resolve()
                    if projects_root in p.parents:
                        p.unlink(missing_ok=True)
                except OSError:
                    pass

            # Replace previously auto-generated clips (keep manual clips intact)
            await session.execute(
                delete(Clip).where(Clip.project_id == project_id, Clip.category != "manual")
            )

            for item in finalized:
                session.add(
                    Clip(
                        project_id=project_id,
                        title=item["title"],
                        hook_text=item["hook_text"],
                        hook_enabled=bool(item["hook_text"]),
                        description=item["description"],
                        tags_json=item["tags_json"],
                        virality_score=item["virality_score"],
                        category=item["category"],
                        reason=item["reason"],
                        start_time=item["start_time"],
                        end_time=item["end_time"],
                        crop_mode="cinema_blur" if item["category"] == "action" else "auto_track",
                        subtitle_preset_id=default_sub_id,
                        subtitles_enabled=bool(item["subtitles_json"]),
                        subtitles_json=item["subtitles_json"],
                        anti_cid_enabled=settings.default_anti_cid_enabled,
                        anti_cid_json={
                            "zoom": settings.default_anti_cid_zoom,
                            "speed": settings.default_anti_cid_speed,
                            "brightness": settings.default_anti_cid_brightness,
                            "contrast": settings.default_anti_cid_contrast,
                            "saturation": settings.default_anti_cid_saturation,
                            "strip_metadata": settings.default_anti_cid_strip_metadata,
                        },
                        status="draft",
                    )
                )
            await session.commit()

        await ws_manager.emit_project_update(project_id, "analyzed", clip_count=len(finalized))
        await ctx.log(f"Анализ проекта завершён: найдено {len(finalized)} виральных клипов")
        return {
            "project_id": project_id,
            "clip_count": len(finalized),
            "scenes_count": len(scene_data["scenes"]),
            "peaks_count": len(audio_data["peaks"]),
            "words_count": len(whisper_data["words"]),
        }

    except (JobCancelledError, asyncio.CancelledError):
        async with AsyncSessionLocal() as session:
            proj = await session.get(Project, project_id)
            if proj is not None:
                proj.status = "analyzed" if proj.analysis_json else "ready"
                proj.error_message = None
                await session.commit()
        await ws_manager.emit_project_update(project_id, "ready")
        raise
    except Exception as exc:
        async with AsyncSessionLocal() as session:
            proj = await session.get(Project, project_id)
            if proj is not None:
                proj.status = "error"
                proj.error_message = str(exc)
                await session.commit()
        await ws_manager.emit_project_update(project_id, "error", error_message=str(exc))
        raise
