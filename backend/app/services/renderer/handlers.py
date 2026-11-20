from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ...core.config import EXPORTS_DIR, get_project_dir
from ...core.cuda_env import vram_guard
from ...core.database import AsyncSessionLocal, load_settings
from ...core.models import AdPreset, BackgroundAsset, Clip, Project, SubtitlePreset
from ...core.queue_manager import JobCancelledError, JobContext, QueueManager
from ...core.schemas import ClipOut
from ...core.ws_manager import ws_manager
from ..tracker.yolo_tracker import track_clip_subject
from .ffmpeg_runner import (
    build_companion_txt,
    render_basic_cut,
    render_clip,
    sanitize_filename,
)


async def _restore_clip_status_on_cancel(clip_id: str, project_id: str) -> None:
    restored = "draft"
    preview_path: Optional[str] = None
    export_path: Optional[str] = None
    async with AsyncSessionLocal() as session:
        c = await session.get(Clip, clip_id)
        if c is not None:
            if c.export_path and Path(c.export_path).exists():
                restored = "exported"
                export_path = c.export_path
            elif c.preview_path and Path(c.preview_path).exists():
                restored = "rendered"
                preview_path = c.preview_path
            c.status = restored
            await session.commit()
    await ws_manager.emit_clip_update(
        clip_id,
        project_id,
        restored,
        preview_path=preview_path,
        export_path=export_path,
    )


async def _ensure_clip_tracking(
    ctx: JobContext,
    project: Project,
    clip: Clip,
    settings_dict: dict[str, Any],
    force: bool = False,
    progress_cb: Optional[Any] = None,
) -> Optional[dict[str, Any]]:
    if clip.crop_mode != "auto_track" and not force:
        return clip.track_data_json

    if not force and clip.track_data_json:
        td = clip.track_data_json
        if (
            abs(float(td.get("clip_start", -1)) - float(clip.start_time)) < 0.3
            and abs(float(td.get("clip_end", -1)) - float(clip.end_time)) < 0.3
            and td.get("points")
        ):
            return td

    scenes = []
    if project.analysis_json and isinstance(project.analysis_json, dict):
        scenes = project.analysis_json.get("scenes") or []

    async with AsyncSessionLocal() as session:
        c = await session.get(Clip, clip.id)
        if c is not None:
            c.status = "tracking"
            await session.commit()
    await ws_manager.emit_clip_update(clip.id, project.id, "tracking")

    async with ctx.gpu_semaphore:
        async with vram_guard("yolov8-tracker", on_gpu_status=ws_manager.emit_gpu_status):
            track_data = await track_clip_subject(
                video_path=Path(project.source_path),
                clip_start=clip.start_time,
                clip_end=clip.end_time,
                yolo_model_path=str(settings_dict["yolo_model_path"]),
                sample_fps=float(settings_dict["tracker_sample_fps"]),
                smoothing_method=str(settings_dict["smoothing_method"]),
                smoothing_strength=float(settings_dict["smoothing_strength"]),
                scene_cuts=scenes,
                on_progress=progress_cb,
                cancel_event=ctx.cancel_event,
            )

    async with AsyncSessionLocal() as session:
        c = await session.get(Clip, clip.id)
        if c is not None:
            c.track_data_json = track_data
            await session.commit()

    return track_data


async def _load_render_bundle(clip_id: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        clip = await session.get(Clip, clip_id)
        if clip is None:
            raise ValueError(f"Клип {clip_id} не найден")
        project = await session.get(Project, clip.project_id)
        if project is None:
            raise ValueError("Проект не найден")
        settings = await load_settings(session)

        sub_preset = (
            await session.get(SubtitlePreset, clip.subtitle_preset_id)
            if clip.subtitle_preset_id
            else None
        )
        if sub_preset is None:
            from sqlalchemy import select

            sub_preset = (
                await session.execute(select(SubtitlePreset).where(SubtitlePreset.is_default.is_(True)))
            ).scalars().first()

        ad_preset = await session.get(AdPreset, clip.ad_preset_id) if clip.ad_preset_id else None
        bg_asset = (
            await session.get(BackgroundAsset, clip.background_asset_id)
            if clip.background_asset_id
            else None
        )

        return {
            "clip": clip,
            "project": project,
            "settings": settings,
            "clip_dict": ClipOut.model_validate(clip).model_dump(),
            "sub_dict": {
                "font_name": sub_preset.font_name,
                "font_size": sub_preset.font_size,
                "primary_color": sub_preset.primary_color,
                "highlight_color": sub_preset.highlight_color,
                "outline_color": sub_preset.outline_color,
                "outline_width": sub_preset.outline_width,
                "shadow_depth": sub_preset.shadow_depth,
                "bold": sub_preset.bold,
                "uppercase": sub_preset.uppercase,
                "margin_bottom": sub_preset.margin_bottom,
                "words_per_line": sub_preset.words_per_line,
                "karaoke_mode": sub_preset.karaoke_mode,
                "hook_bg_color": sub_preset.hook_bg_color,
                "hook_text_color": sub_preset.hook_text_color,
            }
            if sub_preset
            else {},
            "ad_dict": {
                "id": ad_preset.id,
                "ad_type": ad_preset.ad_type,
                "asset_path": ad_preset.asset_path,
                "insert_position": ad_preset.insert_position,
                "insert_at_sec": ad_preset.insert_at_sec,
                "overlay_x": ad_preset.overlay_x,
                "overlay_y": ad_preset.overlay_y,
                "overlay_scale": ad_preset.overlay_scale,
                "overlay_start": ad_preset.overlay_start,
                "overlay_end": ad_preset.overlay_end,
                "fade_duration": ad_preset.fade_duration,
                "duration": ad_preset.duration,
            }
            if ad_preset
            else None,
            "bg_path": Path(bg_asset.file_path) if bg_asset and bg_asset.file_path else None,
        }


async def handle_basic_cut(ctx: JobContext) -> dict[str, Any]:
    clip_id = ctx.clip_id
    project_id = ctx.project_id
    if not clip_id or not project_id:
        raise ValueError("basic_cut требует project_id и clip_id")

    async with AsyncSessionLocal() as session:
        proj = await session.get(Project, project_id)
        clip = await session.get(Clip, clip_id)
        settings = await load_settings(session)
        if proj is None or clip is None:
            raise ValueError("Проект или клип не найден")
        source_path = Path(proj.source_path)
        has_audio = proj.has_audio
        start_time = clip.start_time
        end_time = clip.end_time
        clip.status = "rendering"
        await session.commit()

    out_path = get_project_dir(project_id) / "previews" / f"{clip_id}_cut.mp4"

    async def _progress(pct: float, msg: str) -> None:
        await ctx.update_progress(pct, "render", msg)

    try:
        async with ctx.render_semaphore:
            await render_basic_cut(
                source_path=source_path,
                start_time=start_time,
                end_time=end_time,
                output_path=out_path,
                preferred_encoder=settings.preferred_encoder,
                quality_cq=settings.crf_or_cq,
                has_audio=has_audio,
                on_progress=_progress,
                cancel_event=ctx.cancel_event,
                register_proc=ctx.register_process,
                unregister_proc=ctx.unregister_process,
            )
            async with AsyncSessionLocal() as session:
                clip = await session.get(Clip, clip_id)
                if clip is not None:
                    clip.preview_path = str(out_path)
                    clip.status = "rendered"
                    clip.updated_at = datetime.now(timezone.utc)
                    await session.commit()
            await ws_manager.emit_clip_update(clip_id, project_id, "rendered", preview_path=str(out_path))
            return {"clip_id": clip_id, "preview_path": str(out_path)}
    except (JobCancelledError, asyncio.CancelledError):
        await _restore_clip_status_on_cancel(clip_id, project_id)
        raise
    except Exception:
        async with AsyncSessionLocal() as session:
            clip = await session.get(Clip, clip_id)
            if clip is not None:
                clip.status = "error"
                await session.commit()
        await ws_manager.emit_clip_update(clip_id, project_id, "error")
        raise


async def handle_track_clip(ctx: JobContext) -> dict[str, Any]:
    clip_id = ctx.clip_id
    if not clip_id:
        raise ValueError("track_clip требует clip_id")

    bundle = await _load_render_bundle(clip_id)
    project: Project = bundle["project"]
    clip: Clip = bundle["clip"]
    settings = bundle["settings"]

    async def _track_prog(pct: float, msg: str) -> None:
        await ctx.update_progress(pct, "tracking", msg)

    try:
        track_data = await _ensure_clip_tracking(
            ctx,
            project,
            clip,
            settings.model_dump(),
            force=True,
            progress_cb=_track_prog,
        )
        async with AsyncSessionLocal() as session:
            c = await session.get(Clip, clip_id)
            if c is not None:
                c.status = "draft"
                await session.commit()
        await ws_manager.emit_clip_update(clip_id, project.id, "tracked")
        await ctx.log(
            f"YOLOv8 трекинг завершён: {len((track_data or {}).get('points', []))} ключевых точек"
        )
        return {"clip_id": clip_id, "points_count": len((track_data or {}).get("points", []))}
    except (JobCancelledError, asyncio.CancelledError):
        await _restore_clip_status_on_cancel(clip_id, project.id)
        raise
    except Exception:
        async with AsyncSessionLocal() as session:
            c = await session.get(Clip, clip_id)
            if c is not None:
                c.status = "error"
                await session.commit()
        await ws_manager.emit_clip_update(clip_id, project.id, "error")
        raise


async def _render_or_export_single(
    ctx: JobContext,
    clip_id: str,
    is_export: bool,
    explicit_output: Optional[Path] = None,
    progress_offset: float = 0.0,
    progress_span: float = 100.0,
    on_progress: Optional[Callable[[float, str, str], Awaitable[None]]] = None,
) -> dict[str, Any]:
    bundle = await _load_render_bundle(clip_id)
    project: Project = bundle["project"]
    clip: Clip = bundle["clip"]
    settings = bundle["settings"]
    clip_dict: dict[str, Any] = bundle["clip_dict"]

    async def _emit(local_pct: float, stage: str, msg: str) -> None:
        clamped = max(0.0, min(100.0, float(local_pct)))
        if on_progress is not None:
            await on_progress(clamped, stage, msg)
        else:
            await ctx.update_progress(
                progress_offset + progress_span * (clamped / 100.0),
                stage,
                msg,
            )

    try:
        # Step A: Always validate/ensure tracking for auto_track (handles empty/stale track_data_json)
        if clip.crop_mode == "auto_track":
            async def _tp(pct: float, msg: str) -> None:
                await _emit(30.0 * (pct / 100.0), "tracking", msg)

            track_data = await _ensure_clip_tracking(
                ctx, project, clip, settings.model_dump(), force=False, progress_cb=_tp
            )
            clip_dict["track_data_json"] = track_data
            render_local_start = 30.0
            render_local_span = 70.0
        else:
            render_local_start = 0.0
            render_local_span = 100.0

        async with AsyncSessionLocal() as session:
            c = await session.get(Clip, clip_id)
            if c is not None:
                c.status = "rendering"
                await session.commit()
        await ws_manager.emit_clip_update(clip_id, project.id, "rendering")

        if is_export:
            if explicit_output is not None:
                out_mp4 = explicit_output
            else:
                base_export = Path(settings.export_dir or str(EXPORTS_DIR))
                proj_slug = sanitize_filename(project.title, fallback=project.id)
                clip_slug = sanitize_filename(clip.title, fallback=clip.id)
                out_mp4 = base_export / proj_slug / f"{clip_slug}_{clip.id[:6]}.mp4"
        else:
            out_mp4 = get_project_dir(project.id) / "previews" / f"{clip_id}_preview.mp4"

        async def _rp(pct: float, msg: str) -> None:
            await _emit(render_local_start + render_local_span * (pct / 100.0), "render", msg)

        async with ctx.render_semaphore:
            await render_clip(
                source_path=Path(project.source_path),
                source_width=project.width,
                source_height=project.height,
                source_has_audio=project.has_audio,
                source_duration=project.duration,
                clip_data=clip_dict,
                output_path=out_mp4,
                subtitle_preset=bundle["sub_dict"],
                ad_preset=bundle["ad_dict"],
                background_path=bundle["bg_path"],
                preferred_encoder=settings.preferred_encoder,
                quality_cq=settings.crf_or_cq,
                target_fps=settings.target_fps,
                fast_preview=not is_export,
                on_progress=_rp,
                cancel_event=ctx.cancel_event,
                register_proc=ctx.register_process,
                unregister_proc=ctx.unregister_process,
            )

        txt_path: Optional[Path] = None
        if is_export:
            txt_path = out_mp4.with_suffix(".txt")
            txt_path.write_text(build_companion_txt(clip_dict, project.title), encoding="utf-8")

        async with AsyncSessionLocal() as session:
            c = await session.get(Clip, clip_id)
            if c is not None:
                if is_export:
                    c.export_path = str(out_mp4)
                    c.status = "exported"
                else:
                    c.preview_path = str(out_mp4)
                    c.status = "rendered"
                c.updated_at = datetime.now(timezone.utc)
                await session.commit()

        await ws_manager.emit_clip_update(
            clip_id,
            project.id,
            "exported" if is_export else "rendered",
            preview_path=None if is_export else str(out_mp4),
            export_path=str(out_mp4) if is_export else None,
        )
        return {
            "clip_id": clip_id,
            "output_path": str(out_mp4),
            "txt_path": str(txt_path) if txt_path else None,
        }
    except (JobCancelledError, asyncio.CancelledError):
        await _restore_clip_status_on_cancel(clip_id, project.id)
        raise
    except Exception:
        async with AsyncSessionLocal() as session:
            c = await session.get(Clip, clip_id)
            if c is not None:
                c.status = "error"
                await session.commit()
        await ws_manager.emit_clip_update(clip_id, project.id, "error")
        raise


async def handle_render_preview(ctx: JobContext) -> dict[str, Any]:
    if not ctx.clip_id:
        raise ValueError("render_preview требует clip_id")
    res = await _render_or_export_single(ctx, ctx.clip_id, is_export=False)
    await ctx.log(f"Превью 9:16 готово: {Path(res['output_path']).name}")
    return res


async def handle_export_clip(ctx: JobContext) -> dict[str, Any]:
    if not ctx.clip_id:
        raise ValueError("export_clip требует clip_id")
    res = await _render_or_export_single(ctx, ctx.clip_id, is_export=True)
    await ctx.log(f"Экспорт клипа завершён: {res['output_path']}")
    return res


def _next_batch_output_path(
    batch_dir: Path, idx: int, clip_id: str, reserved: set[Path]
) -> Path:
    base = batch_dir / f"clip_{idx + 1:02d}.mp4"
    if not base.exists() and base not in reserved:
        reserved.add(base)
        return base
    candidate = batch_dir / f"clip_{idx + 1:02d}_{clip_id[:6]}.mp4"
    if not candidate.exists() and candidate not in reserved:
        reserved.add(candidate)
        return candidate
    suffix = 2
    while True:
        alt = batch_dir / f"clip_{idx + 1:02d}_{clip_id[:6]}_{suffix}.mp4"
        if not alt.exists() and alt not in reserved:
            reserved.add(alt)
            return alt
        suffix += 1


async def handle_export_batch(ctx: JobContext) -> dict[str, Any]:
    project_id = ctx.project_id
    clip_ids: list[str] = list(ctx.payload.get("clip_ids") or [])
    if not project_id or not clip_ids:
        raise ValueError("export_batch требует project_id и список clip_ids")

    async with AsyncSessionLocal() as session:
        project = await session.get(Project, project_id)
        settings = await load_settings(session)
        if project is None:
            raise ValueError("Проект не найден")
        proj_slug = sanitize_filename(project.title, fallback=project.id)

    base_dir = Path(str(ctx.payload.get("output_dir") or settings.export_dir or EXPORTS_DIR))
    batch_dir = base_dir / proj_slug
    batch_dir.mkdir(parents=True, exist_ok=True)

    total = len(clip_ids)
    per_clip_progress = [0.0] * total
    max_reported = 0.0
    reserved_paths: set[Path] = set()
    targets = [
        (idx, cid, _next_batch_output_path(batch_dir, idx, cid, reserved_paths))
        for idx, cid in enumerate(clip_ids)
    ]

    async def _report_clip_progress(idx: int, pct: float, stage: str, msg: str) -> None:
        nonlocal max_reported
        per_clip_progress[idx] = max(per_clip_progress[idx], max(0.0, min(100.0, pct)))
        overall = sum(per_clip_progress) / total
        if overall >= max_reported:
            max_reported = overall
            await ctx.update_progress(max_reported, stage, msg)

    async def _export_one(idx: int, cid: str, out_mp4: Path) -> tuple[bool, dict[str, Any]]:
        ctx.check_cancelled()
        await ctx.log(f"Батч-экспорт [{idx + 1}/{total}] -> {out_mp4.name}")
        try:
            async def _on_prog(pct: float, stage: str, msg: str) -> None:
                await _report_clip_progress(idx, pct, stage, msg)

            res = await _render_or_export_single(
                ctx,
                cid,
                is_export=True,
                explicit_output=out_mp4,
                on_progress=_on_prog,
            )
            await _report_clip_progress(idx, 100.0, "render", f"Экспортирован {out_mp4.name}")
            return True, res
        except (JobCancelledError, asyncio.CancelledError):
            raise
        except Exception as exc:
            err_msg = str(exc) or exc.__class__.__name__
            await _report_clip_progress(idx, 100.0, "render", f"Ошибка клипа {cid}")
            await ctx.log(
                f"Ошибка батч-экспорта клипа {cid} ({out_mp4.name}): {err_msg}",
                level="error",
            )
            return False, {"clip_id": cid, "output_path": str(out_mp4), "error": err_msg}

    results = await asyncio.gather(*(_export_one(idx, cid, out_mp4) for idx, cid, out_mp4 in targets))
    exported_files = [item for ok, item in results if ok]
    failed_clips = [item for ok, item in results if not ok]

    if not exported_files and failed_clips:
        raise RuntimeError(
            f"Батч-экспорт завершился ошибкой для всех клипов: {failed_clips[0]['error']}"
        )

    await ctx.log(
        f"Батч-экспорт завершён: успешно {len(exported_files)}/{total}, "
        f"ошибок {len(failed_clips)} -> {batch_dir}"
    )
    return {
        "project_id": project_id,
        "export_dir": str(batch_dir),
        "exported": exported_files,
        "failed": failed_clips,
    }


def register_render_handlers(queue: QueueManager) -> None:
    queue.register_handler("basic_cut", handle_basic_cut)
    queue.register_handler("track_clip", handle_track_clip)
    queue.register_handler("render_preview", handle_render_preview)
    queue.register_handler("export_clip", handle_export_clip)
    queue.register_handler("export_batch", handle_export_batch)
