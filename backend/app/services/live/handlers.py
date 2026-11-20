from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ...core.cuda_env import vram_guard
from ...core.database import AsyncSessionLocal, load_settings
from ...core.models import Clip, LiveChannel, Project, SocialAccount, SubtitlePreset
from ...core.queue_manager import JobCancelledError, JobContext, QueueManager
from ...core.ws_manager import ws_manager
from ..analyzer.llm_highlights import analyze_highlights_llm, build_clip_subtitles
from ..analyzer.transcriber import transcribe_video
from ..renderer.handlers import _render_or_export_single
from .publisher import publisher_service

logger = logging.getLogger("narezki.live.handlers")


async def handle_live_process_clip(ctx: JobContext) -> dict[str, Any]:
    """Process a captured Live Monitor highlight through subtitles, optional LLM, 9:16 NVENC render, and optional auto-publish."""
    clip_id = ctx.clip_id or ctx.payload.get("clip_id")
    if not clip_id:
        raise ValueError("live_process_clip требует clip_id")

    channel_id = ctx.payload.get("channel_id")
    ai_mode = str(ctx.payload.get("ai_mode") or "zero_ai")
    chat_spike = ctx.payload.get("chat_spike")

    async with AsyncSessionLocal() as session:
        clip = await session.get(Clip, clip_id)
        if clip is None:
            raise ValueError(f"Клип {clip_id} не найден")
        project = await session.get(Project, clip.project_id)
        if project is None:
            raise ValueError("Проект клипа не найден")
        settings = await load_settings(session)
        sub_preset = (
            await session.get(SubtitlePreset, clip.subtitle_preset_id)
            if clip.subtitle_preset_id
            else None
        )
        if sub_preset is None:
            sub_preset = (
                await session.execute(select(SubtitlePreset).where(SubtitlePreset.is_default.is_(True)))
            ).scalars().first()
        words_per_line = sub_preset.words_per_line if sub_preset else 3

        source_path = Path(project.source_path)
        duration = float(project.duration or max(1.0, clip.end_time - clip.start_time))
        has_audio = bool(project.has_audio)
        need_subtitles = bool(clip.subtitles_enabled and not clip.subtitles_json and has_audio)
        clip_start = float(clip.start_time)
        clip_end = float(clip.end_time)

    whisper_segments: list[dict[str, Any]] = []

    try:
        # Stage 1 (0 - 25%): Whisper subtitles when enabled & missing
        if need_subtitles and source_path.exists():
            await ctx.update_progress(3.0, "whisper", "Транскрибация live-фрагмента (faster-whisper)...")
            try:
                async def _wp(pct: float, msg: str) -> None:
                    await ctx.update_progress(3.0 + (pct / 100.0) * 20.0, "whisper", msg)

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
                            on_progress=_wp,
                            cancel_event=ctx.cancel_event,
                            register_proc=ctx.register_process,
                            unregister_proc=ctx.unregister_process,
                        )
                whisper_segments = list(whisper_data.get("segments") or [])
                sub_cues = build_clip_subtitles(
                    words=list(whisper_data.get("words") or []),
                    clip_start=clip_start,
                    clip_end=clip_end,
                    words_per_line=words_per_line,
                )
                if sub_cues:
                    async with AsyncSessionLocal() as session:
                        c = await session.get(Clip, clip_id)
                        if c is not None:
                            c.subtitles_json = sub_cues
                            c.updated_at = datetime.now(timezone.utc)
                            await session.commit()
                    await ctx.log(f"Субтитры для live-клипа: {len(sub_cues)} фраз", stage="live")
            except (JobCancelledError, asyncio.CancelledError):
                raise
            except Exception as exc:
                await ctx.log(
                    f"Не удалось создать субтитры для live-клипа ({exc}); продолжаем рендер",
                    level="warning",
                    stage="live",
                )

        # Stage 2 (25 - 35%): Optional LLM refinement ONLY in llm/auto mode with API key
        if ai_mode in ("llm", "auto") and settings.llm_api_key.strip():
            await ctx.update_progress(27.0, "llm", f"Улучшение заголовка и хука через {settings.llm_model}...")
            try:
                span = max(3.0, clip_end - clip_start)
                refined = await analyze_highlights_llm(
                    base_url=settings.llm_base_url,
                    api_key=settings.llm_api_key,
                    model=settings.llm_model,
                    temperature=settings.llm_temperature,
                    duration=duration,
                    transcript=whisper_segments,
                    audio_peaks=[],
                    action_bursts=[],
                    max_highlights=1,
                    chunk_minutes=settings.llm_chunk_minutes,
                    min_duration=max(3.0, span - 2.0),
                    max_duration=max(10.0, span + 2.0),
                    focus_prompt="",
                    cancel_event=ctx.cancel_event,
                    chat_spikes=[chat_spike] if isinstance(chat_spike, dict) else None,
                )
                if refined:
                    raw_best = refined[0]
                    best = (
                        raw_best.model_dump()
                        if hasattr(raw_best, "model_dump")
                        else dict(raw_best)
                    )
                    async with AsyncSessionLocal() as session:
                        c = await session.get(Clip, clip_id)
                        if c is not None:
                            if best.get("title"):
                                c.title = str(best["title"])[:250]
                            if best.get("hook_text"):
                                c.hook_text = str(best["hook_text"])[:250]
                            if best.get("description"):
                                c.description = str(best["description"])
                            if isinstance(best.get("tags"), list) and best["tags"]:
                                c.tags_json = [str(t).strip() for t in best["tags"] if str(t).strip()][:12]
                            if best.get("virality_score") is not None:
                                c.virality_score = float(best["virality_score"])
                            c.updated_at = datetime.now(timezone.utc)
                            await session.commit()
            except (JobCancelledError, asyncio.CancelledError):
                raise
            except Exception as exc:
                await ctx.log(
                    f"LLM-улучшение live-клипа пропущено ({exc})",
                    level="warning",
                    stage="live",
                )

        # Stage 3 (35 - 95%): Unified 9:16 YOLOv8 + ASS + Ads + Anti-CID + NVENC pipeline
        render_res = await _render_or_export_single(
            ctx,
            clip_id=clip_id,
            is_export=True,
            progress_offset=35.0,
            progress_span=60.0,
        )

        # Stage 4 (95 - 100%): Conditional Auto-Publish Queueing
        enqueued_count = 0
        async with AsyncSessionLocal() as session:
            fresh_settings = await load_settings(session)
            ch = await session.get(LiveChannel, channel_id) if channel_id else None
            should_publish = (
                fresh_settings.publish_mode == "auto_publish"
                and (
                    (ch is not None and bool(ch.auto_publish))
                    or bool(fresh_settings.live_auto_publish)
                )
            )
            target_account_ids: list[str] = []
            if should_publish:
                if ch is not None and ch.publish_account_ids_json:
                    target_account_ids = [str(aid) for aid in ch.publish_account_ids_json if aid]
                else:
                    enabled_accounts = (
                        await session.execute(
                            select(SocialAccount.id).where(SocialAccount.enabled == True)  # noqa: E712
                        )
                    ).scalars().all()
                    target_account_ids = [str(aid) for aid in enabled_accounts]

        if target_account_ids:
            await ctx.update_progress(97.0, "publish", "Постановка live-клипа в очередь автопостинга...")
            try:
                queued = await publisher_service.enqueue_clip(
                    clip_id=clip_id,
                    account_ids=target_account_ids,
                )
                enqueued_count = len(queued)
            except Exception as exc:
                await ctx.log(
                    f"Автопостинг live-клипа не поставлен в очередь: {exc}",
                    level="warning",
                    stage="publish",
                )

        await ctx.update_progress(100.0, "done", "Live-клип готов к просмотру и публикации")
        return {
            "clip_id": clip_id,
            "output_path": render_res.get("output_path"),
            "txt_path": render_res.get("txt_path"),
            "published_items": enqueued_count,
        }
    except (JobCancelledError, asyncio.CancelledError):
        raise


def register_live_handlers(queue: QueueManager) -> None:
    """Register live monitoring background job handlers."""
    queue.register_handler("live_process_clip", handle_live_process_clip)
