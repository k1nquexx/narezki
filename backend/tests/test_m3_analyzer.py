from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.core.cuda_env import get_gpu_info
from app.services.analyzer.audio_peaks import analyze_audio_peaks
from app.services.analyzer.llm_highlights import (
    build_multimodal_chunks,
    deduplicate_highlights,
)
from app.services.analyzer.scene_detect import detect_scenes, snap_clip_boundaries
from app.services.analyzer.transcriber import transcribe_video
from app.core.schemas import LLMHighlightItem
from tests.test_m1_m2_ingest import _wait_job


@pytest.mark.asyncio
async def test_m3_audio_peaks_and_scene_detect(fixtures_dir: Path) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"

    audio = await analyze_audio_peaks(sample, duration=12.0, has_audio=True)
    assert len(audio["rms_db"]) >= 20
    assert len(audio["peaks"]) >= 1
    assert any(p["start"] <= 7.6 and p["end"] >= 4.4 for p in audio["peaks"])

    sc = await detect_scenes(sample, threshold=20.0)
    assert len(sc["scenes"]) >= 5
    assert len(sc["action_bursts"]) >= 1

    # Boundary snapping avoids slicing mid-word and snaps to scene cut
    words = [
        {"word": "hello", "start": 1.0, "end": 1.4},
        {"word": "world", "start": 1.9, "end": 2.3},
    ]
    s, e = snap_clip_boundaries(
        start=1.65,
        end=9.5,
        scenes=[4.0, 8.05],
        words=words,
        duration=12.0,
        tolerance=1.8,
        min_duration=5.0,
    )
    assert 1.5 <= s <= 1.85
    assert e == 8.05


@pytest.mark.asyncio
async def test_m3_whisper_cuda_and_vram_release(fixtures_dir: Path) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    before = get_gpu_info()
    res = await transcribe_video(
        video_path=sample,
        duration=12.0,
        has_audio=True,
        model_size="base",
        device="cuda",
        compute_type="float16",
    )
    after = get_gpu_info()
    assert res["device"] == "cuda"
    assert res["compute_type"] == "float16"
    # Allow minor CUDA context overhead (< 450 MB)
    assert (after["vram_used_mb"] - before["vram_used_mb"]) < 450


def test_m3_llm_chunking_and_dedup() -> None:
    chunks = build_multimodal_chunks(
        duration=900.0,
        transcript=[{"start": 10.0, "end": 15.0, "text": "test speech"}],
        audio_peaks=[{"start": 580.0, "end": 584.0, "delta_db": 9.0, "peak_db": -3.0, "score": 80}],
        action_bursts=[{"start": 582.0, "end": 590.0, "cut_count": 6}],
        chunk_minutes=10,
        overlap_sec=60.0,
    )
    assert len(chunks) == 2
    assert "AUDIO_SPIKE" in chunks[1][2]
    assert "ACTION_MONTAGE" in chunks[1][2]

    items = [
        LLMHighlightItem(
            start_time=10.0,
            end_time=40.0,
            title="A",
            hook_text="A",
            description="A",
            tags=["#a"],
            virality_score=70.0,
            category="dialogue",
            reason="ok",
        ),
        LLMHighlightItem(
            start_time=12.0,
            end_time=41.0,
            title="B",
            hook_text="B",
            description="B",
            tags=["#b"],
            virality_score=91.0,
            category="hybrid",
            reason="better",
        ),
    ]
    deduped = deduplicate_highlights(items, max_count=5)
    assert len(deduped) == 1
    assert deduped[0].title == "B"


@pytest.mark.asyncio
async def test_m3_full_analyze_job_heuristic(api_client: AsyncClient, fixtures_dir: Path) -> None:
    # Temporarily configure base Whisper model for fast test execution
    s_resp = await api_client.get("/api/system/settings")
    orig = s_resp.json()
    fast_settings = {**orig, "whisper_model_size": "base"}
    await api_client.put("/api/system/settings", json=fast_settings)

    try:
        sample = fixtures_dir / "sample_1080p.mp4"
        imp = await api_client.post(
            "/api/projects/import-local",
            json={"file_path": str(sample), "title": "Test M3 Analyze"},
        )
        proj_id = imp.json()["project"]["id"]
        imp_job = await _wait_job(api_client, imp.json()["job"]["id"])
        assert imp_job["status"] == "completed"

        an_resp = await api_client.post(
            f"/api/projects/{proj_id}/analyze",
            json={"mode": "heuristic", "min_duration": 5.0, "max_duration": 10.0, "max_highlights": 3},
        )
        assert an_resp.status_code == 200
        an_job = await _wait_job(api_client, an_resp.json()["id"], timeout=120.0)
        assert an_job["status"] == "completed", an_job.get("error")

        clips_resp = await api_client.get(f"/api/clips?project_id={proj_id}")
        assert clips_resp.status_code == 200
        clips = clips_resp.json()
        assert len(clips) >= 1

        await api_client.delete(f"/api/projects/{proj_id}")
    finally:
        await api_client.put("/api/system/settings", json=orig)
