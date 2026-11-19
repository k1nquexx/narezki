from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core import config as cfg
from app.services.analyzer.transcriber import transcribe_video


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

