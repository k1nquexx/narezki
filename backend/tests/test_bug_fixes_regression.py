from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core import config as cfg
from app.core.queue_manager import JobCancelledError, JobContext
from app.services.analyzer import pipeline as analyzer_pipeline
from app.services.analyzer import transcriber
from app.services.analyzer.transcriber import transcribe_video
from app.services.ingest.ffprobe_utils import parse_ffprobe_output, probe_media
from app.services.renderer import handlers as render_handlers
from app.services.renderer.ad_injector import (
    build_overlay_input_args,
    build_video_ad_splice_filtergraph,
)
from app.services.renderer.anti_cid import build_anti_cid_filtergraph
from app.services.renderer.ffmpeg_runner import _get_filter_script_flag, render_clip
from app.services.renderer.subtitle_ass import format_ass_filter_path, generate_ass_file
from app.services.tracker import yolo_tracker
from app.services.tracker.yolo_tracker import track_clip_subject


def test_bug01_cross_platform_ffmpeg_paths(monkeypatch, tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe_ffmpeg = bin_dir / "ffmpeg.exe"
    exe_ffprobe = bin_dir / "ffprobe.exe"
    exe_ffmpeg.write_bytes(b"win-exe")
    exe_ffprobe.write_bytes(b"win-exe")

    monkeypatch.setattr(cfg, "BIN_DIR", bin_dir)

    # On Windows, bundled .exe binaries must be selected
    monkeypatch.setattr(cfg.sys, "platform", "win32")
    assert cfg.get_ffmpeg_path() == str(exe_ffmpeg)
    assert cfg.get_ffprobe_path() == str(exe_ffprobe)

    # On Linux, .exe must NEVER be selected; falls back to system PATH
    monkeypatch.setattr(cfg.sys, "platform", "linux")
    monkeypatch.setattr(
        cfg.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in ("ffmpeg", "ffprobe") else None,
    )
    assert cfg.get_ffmpeg_path() == "/usr/bin/ffmpeg"
    assert cfg.get_ffprobe_path() == "/usr/bin/ffprobe"

    # If executable POSIX binary exists in BIN_DIR, prefer it on Linux
    unix_ffmpeg = bin_dir / "ffmpeg"
    unix_ffprobe = bin_dir / "ffprobe"
    unix_ffmpeg.write_bytes(b"#!/bin/sh\n")
    unix_ffprobe.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(cfg.os, "access", lambda path, mode: Path(path) in (unix_ffmpeg, unix_ffprobe))
    assert cfg.get_ffmpeg_path() == str(unix_ffmpeg)
    assert cfg.get_ffprobe_path() == str(unix_ffprobe)


@pytest.mark.asyncio
async def test_bug02_whisper_cpu_float16_fallback_to_int8(fixtures_dir: Path) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    res = await transcribe_video(
        video_path=sample,
        duration=12.0,
        has_audio=True,
        model_size="base",
        device="cpu",
        compute_type="float16",
    )
    assert res["device"] == "cpu"
    assert res["compute_type"] == "int8"
    assert isinstance(res["segments"], list)


@pytest.mark.asyncio
async def test_bug03_ass_path_with_apostrophe_and_font_comma(tmp_path: Path) -> None:
    weird_dir = tmp_path / "user's [test], dir; 1"
    weird_dir.mkdir(parents=True, exist_ok=True)
    ass_file = weird_dir / "sub's [1].ass"
    generate_ass_file(
        cues=[{"start": 0.0, "end": 1.0, "text": "HELLO"}],
        preset={"font_name": "Arial,Bold\nEvil"},
        output_path=ass_file,
    )
    ass_txt = ass_file.read_text(encoding="utf-8-sig")
    assert "Style: Default,Arial Bold Evil," in ass_txt

    ffmpeg = cfg.get_ffmpeg_path()
    filter_flag = await _get_filter_script_flag(ffmpeg)
    fg_file = weird_dir / "fg.txt"
    fg_file.write_text(f"[0:v]{format_ass_filter_path(ass_file)}[v]", encoding="utf-8")
    out_mp4 = weird_dir / "out.mp4"

    proc = subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:d=1",
            filter_flag,
            str(fg_file),
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            str(out_mp4),
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert out_mp4.exists() and out_mp4.stat().st_size > 0


@pytest.mark.asyncio
async def test_bug04_gpu_to_thread_cancellation_waits_for_worker_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    gpu_sem = asyncio.Semaphore(1)
    worker_started = threading.Event()
    worker_cleaned_up = threading.Event()
    wav_existed_during_cleanup = False

    async def fake_extract(_video: Path, wav_path: Path, **_kwargs) -> None:
        wav_path.write_bytes(b"RIFF0000WAVE")

    def slow_transcribe_sync(
        wav_path: Path,
        _duration: float,
        _model_size: str,
        _device: str,
        _compute_type: str,
        _language: str,
        _loop: asyncio.AbstractEventLoop,
        _on_progress,
        cancel_event: asyncio.Event | None,
    ):
        nonlocal wav_existed_during_cleanup
        worker_started.set()
        try:
            while not (cancel_event and cancel_event.is_set()):
                time.sleep(0.02)
            time.sleep(0.08)
            wav_existed_during_cleanup = wav_path.exists()
            raise asyncio.CancelledError("cancelled")
        finally:
            worker_cleaned_up.set()

    monkeypatch.setattr(transcriber, "_extract_wav_16k", fake_extract)
    monkeypatch.setattr(transcriber, "_transcribe_sync", slow_transcribe_sync)

    cancel_evt = asyncio.Event()

    async def run_whisper() -> None:
        async with gpu_sem:
            await transcribe_video(
                video_path=tmp_path / "dummy.mp4",
                duration=5.0,
                has_audio=True,
                cancel_event=cancel_evt,
            )

    task = asyncio.create_task(run_whisper())
    while not worker_started.is_set():
        await asyncio.sleep(0.01)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Semaphore must ONLY be released AFTER worker thread finished cleanup & WAV still existed during cleanup
    assert worker_cleaned_up.is_set()
    assert wav_existed_during_cleanup is True
    assert not gpu_sem.locked()

    # Same verification for track_clip_subject
    yolo_started = threading.Event()
    yolo_cleaned = threading.Event()

    def slow_track_sync(*args):
        c_evt = args[-1]
        yolo_started.set()
        try:
            while not (c_evt and c_evt.is_set()):
                time.sleep(0.02)
            time.sleep(0.08)
            raise asyncio.CancelledError("cancelled")
        finally:
            yolo_cleaned.set()

    monkeypatch.setattr(yolo_tracker, "_track_clip_sync", slow_track_sync)
    y_cancel = asyncio.Event()

    async def run_yolo() -> None:
        async with gpu_sem:
            await track_clip_subject(
                video_path=tmp_path / "dummy.mp4",
                clip_start=0.0,
                clip_end=3.0,
                cancel_event=y_cancel,
            )

    ytask = asyncio.create_task(run_yolo())
    while not yolo_started.is_set():
        await asyncio.sleep(0.01)

    ytask.cancel()
    with pytest.raises(asyncio.CancelledError):
        await ytask

    assert yolo_cleaned.is_set()
    assert not gpu_sem.locked()


def test_bug05_ad_injector_loudnorm_sample_rate_and_av1_webm(tmp_path: Path) -> None:
    av1_webm = tmp_path / "overlay_av1.webm"
    av1_webm.write_bytes(b"")
    args_av1 = build_overlay_input_args(av1_webm, clip_duration=5.0, video_codec="av1")
    assert "-c:v" not in args_av1
    assert args_av1[-1] == str(av1_webm)

    args_vp8 = build_overlay_input_args(av1_webm, clip_duration=5.0, video_codec="vp8")
    assert args_vp8[:2] == ["-c:v", "libvpx"]

    fg = build_video_ad_splice_filtergraph(
        v_main="v_cur",
        a_main="a_cur",
        ad_v_in="1:v",
        ad_a_in="1:a",
        v_out="v_final",
        a_out="a_final",
        main_duration=4.0,
        ad_duration=3.0,
        target_fps=30,
        insert_position="outro",
    )
    fg_text = "\n".join(fg)
    assert "setpts=PTS-STARTPTS[_ad_v]" in fg_text
    ad_a_line = [ln for ln in fg if "[_ad_a]" in ln][0]
    assert ad_a_line.index("loudnorm=") < ad_a_line.index("aformat=sample_rates=48000")
    assert "asetpts=PTS-STARTPTS[_ad_a]" in ad_a_line


@pytest.mark.asyncio
async def test_bug06_anticid_fps_and_midroll_scaling(fixtures_dir: Path, tmp_path: Path) -> None:
    lines, _, spd = build_anti_cid_filtergraph(
        "v_in", "a_in", "v_out", "a_out", config={"speed": 1.05}, target_fps=30
    )
    assert spd == pytest.approx(1.05)
    assert "setpts=(PTS-STARTPTS)/1.05000,fps=30" in lines[0]

    sample = fixtures_dir / "sample_1080p.mp4"
    ad_video = fixtures_dir / "ad_video.mp4"
    out_mp4 = tmp_path / "anticid_midroll.mp4"
    await render_clip(
        source_path=sample,
        source_width=1920,
        source_height=1080,
        source_has_audio=True,
        clip_data={
            "start_time": 0.0,
            "end_time": 4.0,
            "crop_mode": "cinema_blur",
            "subtitles_enabled": False,
            "hook_enabled": False,
            "anti_cid_enabled": True,
            "anti_cid_json": {"zoom": 1.02, "speed": 1.05, "strip_metadata": True},
        },
        output_path=out_mp4,
        ad_preset={
            "id": "ad1",
            "ad_type": "video_insert",
            "asset_path": str(ad_video),
            "insert_position": "mid_roll",
            "insert_at_sec": 3.85,  # 3.85 / 1.05 = 3.667s -> valid mid-roll inside 3.81s main_dur!
            "duration": 3.0,
        },
        target_fps=30,
        fast_preview=True,
    )
    meta = await probe_media(out_mp4)
    assert meta["fps"] == pytest.approx(30.0, abs=0.2)
    assert 6.6 <= meta["duration"] <= 7.0


@pytest.mark.asyncio
async def test_bug07_clip_status_restored_on_cancel(
    api_client, fixtures_dir: Path, monkeypatch, tmp_path: Path
) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Bug07 Cancel Status"},
    )
    assert imp.status_code == 200
    proj_id = imp.json()["project"]["id"]
    from tests.test_m1_m2_ingest import _wait_job

    await _wait_job(api_client, imp.json()["job"]["id"])

    # Create a clip and set an existing preview file on disk to test restoration to "rendered"
    c_resp = await api_client.post(
        "/api/clips",
        json={
            "project_id": proj_id,
            "title": "Clip Cancel Test",
            "start_time": 0.0,
            "end_time": 3.0,
            "crop_mode": "cinema_blur",
        },
    )
    assert c_resp.status_code == 200
    clip_id = c_resp.json()["id"]

    existing_preview = tmp_path / "existing_prev.mp4"
    existing_preview.write_bytes(b"mp4")

    from app.core.database import AsyncSessionLocal
    from app.core.models import Clip

    async with AsyncSessionLocal() as session:
        c = await session.get(Clip, clip_id)
        assert c is not None
        c.preview_path = str(existing_preview)
        c.status = "rendered"
        await session.commit()

    async def fail_with_job_cancel(**_kwargs):
        raise JobCancelledError("cancelled")

    monkeypatch.setattr(render_handlers, "render_clip", fail_with_job_cancel)
    ctx = JobContext(
        job_id="job-cancel-1",
        job_type="render_preview",
        project_id=proj_id,
        clip_id=clip_id,
        payload={},
        cancel_event=asyncio.Event(),
        gpu_semaphore=asyncio.Semaphore(1),
        render_semaphore=asyncio.Semaphore(1),
    )
    with pytest.raises(JobCancelledError):
        await render_handlers.handle_render_preview(ctx)

    after_1 = (await api_client.get(f"/api/clips/{clip_id}")).json()
    assert after_1["status"] == "rendered"

    # Now verify asyncio.CancelledError on track_clip when no preview exists restores to "draft"
    async with AsyncSessionLocal() as session:
        c = await session.get(Clip, clip_id)
        assert c is not None
        c.preview_path = None
        c.status = "draft"
        await session.commit()

    async def fail_track_cancel(**_kwargs):
        raise asyncio.CancelledError("cancelled")

    monkeypatch.setattr(render_handlers, "track_clip_subject", fail_track_cancel)
    ctx2 = JobContext(
        job_id="job-cancel-2",
        job_type="track_clip",
        project_id=proj_id,
        clip_id=clip_id,
        payload={},
        cancel_event=asyncio.Event(),
        gpu_semaphore=asyncio.Semaphore(1),
        render_semaphore=asyncio.Semaphore(1),
    )
    with pytest.raises(asyncio.CancelledError):
        await render_handlers.handle_track_clip(ctx2)

    after_2 = (await api_client.get(f"/api/clips/{clip_id}")).json()
    assert after_2["status"] == "draft"

    await api_client.delete(f"/api/projects/{proj_id}")


@pytest.mark.asyncio
async def test_bug11_media_stream_no_cache_header(api_client, fixtures_dir: Path) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Bug11 Media Cache"},
    )
    assert imp.status_code == 200
    proj_id = imp.json()["project"]["id"]
    from tests.test_m1_m2_ingest import _wait_job

    await _wait_job(api_client, imp.json()["job"]["id"])

    full_resp = await api_client.get("/api/media/stream", params={"path": str(sample), "v": "123"})
    assert full_resp.status_code == 200
    assert full_resp.headers.get("cache-control") == "no-cache"

    range_resp = await api_client.get(
        "/api/media/stream",
        params={"path": str(sample), "v": "456"},
        headers={"Range": "bytes=0-127"},
    )
    assert range_resp.status_code == 206
    assert range_resp.headers.get("cache-control") == "no-cache"

    await api_client.delete(f"/api/projects/{proj_id}")


@pytest.mark.asyncio
async def test_bug12_ffprobe_na_values_and_audio_only_rejection(tmp_path: Path) -> None:
    dummy = tmp_path / "dummy.mkv"
    dummy.write_bytes(b"1234")

    # Test "N/A" fields in format and stream metadata
    parsed = parse_ffprobe_output(
        {
            "format": {"duration": "N/A", "bit_rate": "N/A", "size": "N/A"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "duration": "12.5",
                    "avg_frame_rate": "30/1",
                }
            ],
        },
        dummy,
        require_video=True,
    )
    assert parsed["duration"] == 12.5
    assert parsed["bitrate"] == 0
    assert parsed["size_bytes"] == 4
    assert parsed["width"] == 1920

    # Cover art (attached_pic == 1) must be ignored and rejected when require_video=True
    with pytest.raises(ValueError, match="не содержит видеопотока"):
        parse_ffprobe_output(
            {
                "format": {"duration": "3.0"},
                "streams": [
                    {
                        "codec_type": "video",
                        "codec_name": "mjpeg",
                        "width": 500,
                        "height": 500,
                        "disposition": {"attached_pic": 1},
                    },
                    {"codec_type": "audio", "codec_name": "mp3"},
                ],
            },
            dummy,
            require_video=True,
        )

    # Real audio-only file via ffmpeg + ffprobe
    audio_only = tmp_path / "audio_only.wav"
    ffmpeg = cfg.get_ffmpeg_path()
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=1",
            str(audio_only),
        ],
        check=True,
    )
    meta_ok = await probe_media(audio_only, require_video=False)
    assert meta_ok["has_audio"] is True
    assert meta_ok["width"] == 0

    with pytest.raises(ValueError, match="не содержит видеопотока"):
        await probe_media(audio_only, require_video=True)


@pytest.mark.asyncio
async def test_bug13_patch_clip_null_delete_cleanup_and_missing_project(
    api_client, fixtures_dir: Path, monkeypatch
) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Bug13 Project"},
    )
    assert imp.status_code == 200
    proj_id = imp.json()["project"]["id"]
    from tests.test_m1_m2_ingest import _wait_job

    await _wait_job(api_client, imp.json()["job"]["id"])

    c_resp = await api_client.post(
        "/api/clips",
        json={
            "project_id": proj_id,
            "title": "Clip Null Patch",
            "start_time": 1.0,
            "end_time": 4.0,
        },
    )
    assert c_resp.status_code == 200
    clip_id = c_resp.json()["id"]

    # 1. PATCH with null start_time/end_time must NOT return 500
    patch_resp = await api_client.patch(
        f"/api/clips/{clip_id}",
        json={"start_time": None, "end_time": None, "title": "Updated Title"},
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["start_time"] == 1.0
    assert patch_resp.json()["end_time"] == 4.0
    assert patch_resp.json()["title"] == "Updated Title"

    # 2. DELETE /api/clips must remove preview/export artifacts inside managed directories
    prev_dir = cfg.get_project_dir(proj_id) / "previews"
    prev_dir.mkdir(parents=True, exist_ok=True)
    prev_file = prev_dir / f"{clip_id}_preview.mp4"
    prev_file.write_bytes(b"preview-data")

    cfg.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    exp_dir = cfg.EXPORTS_DIR / f"bug13_{proj_id[:6]}"
    exp_dir.mkdir(parents=True, exist_ok=True)
    exp_file = exp_dir / "clip.mp4"
    exp_txt = exp_dir / "clip.txt"
    exp_file.write_bytes(b"export-data")
    exp_txt.write_text("meta", encoding="utf-8")

    from app.core.database import AsyncSessionLocal
    from app.core.models import Clip, Project
    from app.core.schemas import LLMHighlightItem

    async with AsyncSessionLocal() as session:
        c = await session.get(Clip, clip_id)
        assert c is not None
        c.preview_path = str(prev_file)
        c.export_path = str(exp_file)
        await session.commit()

    del_resp = await api_client.delete(f"/api/clips/{clip_id}")
    assert del_resp.status_code == 200
    assert not prev_file.exists()
    assert not exp_file.exists()
    assert not exp_txt.exists()

    # 3. Project deleted mid-analysis must not raise IntegrityError
    async def fake_audio(*_a, **_kw):
        return {"peaks": [], "rms_db": -20.0}

    async def fake_scenes(*_a, **_kw):
        return {"scenes": [2.0], "action_bursts": []}

    def fake_heuristic(*_a, **_kw):
        return [
            LLMHighlightItem(
                start_time=0.0,
                end_time=5.0,
                title="Highlight",
                hook_text="HOOK",
                description="Desc",
                tags=["#shorts"],
                virality_score=80.0,
                category="dialogue",
                reason="Test",
            )
        ]

    monkeypatch.setattr(analyzer_pipeline, "analyze_audio_peaks", fake_audio)
    monkeypatch.setattr(analyzer_pipeline, "detect_scenes", fake_scenes)
    monkeypatch.setattr(analyzer_pipeline, "analyze_highlights_heuristic", fake_heuristic)

    orig_finalize = analyzer_pipeline.finalize_highlight_clips

    async def fake_transcribe_and_delete(*_a, **_kw):
        async with AsyncSessionLocal() as session:
            p = await session.get(Project, proj_id)
            if p is not None:
                await session.delete(p)
                await session.commit()
        return {"language": "en", "device": "cpu", "compute_type": "int8", "segments": [], "words": []}

    monkeypatch.setattr(analyzer_pipeline, "transcribe_video", fake_transcribe_and_delete)
    monkeypatch.setattr(analyzer_pipeline, "finalize_highlight_clips", orig_finalize)

    ctx = JobContext(
        job_id="job-bug13-analyze",
        job_type="analyze",
        project_id=proj_id,
        clip_id=None,
        payload={"mode": "heuristic"},
        cancel_event=asyncio.Event(),
        gpu_semaphore=asyncio.Semaphore(1),
        render_semaphore=asyncio.Semaphore(1),
    )
    res = await analyzer_pipeline.run_analysis_pipeline(ctx)
    assert res["clip_count"] == 0









