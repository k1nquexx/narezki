from __future__ import annotations

import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.core import config as cfg
from app.services.analyzer import transcriber
from app.services.analyzer.transcriber import transcribe_video
from app.services.ingest.ffprobe_utils import probe_media
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





