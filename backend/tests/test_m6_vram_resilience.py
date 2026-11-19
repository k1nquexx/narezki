from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.cuda_env import get_gpu_info, release_vram, vram_guard
from app.services.analyzer.transcriber import transcribe_video
from app.services.tracker.yolo_tracker import track_clip_subject


@pytest.mark.asyncio
async def test_m6_sequential_whisper_then_yolo_vram_cleanup(fixtures_dir: Path) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    release_vram("m6_baseline")
    baseline = get_gpu_info()
    assert baseline["cuda_available"] is True

    async with vram_guard("m6-whisper"):
        await transcribe_video(
            video_path=sample,
            duration=12.0,
            has_audio=True,
            model_size="base",
            device="cuda",
            compute_type="float16",
        )
    after_whisper = get_gpu_info()

    async with vram_guard("m6-yolo"):
        await track_clip_subject(
            video_path=sample,
            clip_start=0.0,
            clip_end=3.0,
            yolo_model_path="yolov8n.pt",
            sample_fps=3.0,
        )
    after_yolo = get_gpu_info()

    # After sequential Whisper + YOLOv8, total GPU VRAM delta must stay bounded (no model leak)
    assert after_whisper["torch_allocated_mb"] == 0
    assert after_yolo["torch_allocated_mb"] == 0
    assert (after_yolo["vram_used_mb"] - baseline["vram_used_mb"]) < 500


@pytest.mark.asyncio
async def test_m6_error_handling_and_job_cancellation(
    api_client: AsyncClient,
    fixtures_dir: Path,
) -> None:
    bad_import = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(fixtures_dir / "non_existent_video.mp4")},
    )
    assert bad_import.status_code == 400

    missing_proj = await api_client.get("/api/projects/does_not_exist")
    assert missing_proj.status_code == 404

    sample = fixtures_dir / "sample_1080p.mp4"
    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Cancel Test"},
    )
    assert imp.status_code == 200
    proj_id = imp.json()["project"]["id"]
    job_id = imp.json()["job"]["id"]

    cancel_resp = await api_client.post(f"/api/jobs/{job_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"

    await api_client.delete(f"/api/projects/{proj_id}")
