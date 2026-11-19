from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.main import app
from app.services.ingest.ffprobe_utils import probe_media
from app.services.renderer.encoder_detect import probe_encoders


async def _wait_job(client: AsyncClient, job_id: str, timeout: float = 90.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        resp = await client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        job = resp.json()
        if job["status"] in ("completed", "failed", "cancelled"):
            return job
        await asyncio.sleep(0.35)
    raise TimeoutError(f"Job {job_id} did not finish within {timeout}s")


@pytest.mark.asyncio
async def test_m1_health_gpu_and_default_presets(api_client: AsyncClient) -> None:
    health = await api_client.get("/api/system/health")
    assert health.status_code == 200
    data = health.json()
    assert data["status"] == "ok"
    assert data["ffmpeg_ok"] is True
    assert data["ffprobe_ok"] is True
    assert data["cuda_available"] is True

    gpu = await api_client.get("/api/system/gpu")
    assert gpu.status_code == 200
    gpu_data = gpu.json()
    assert gpu_data["cuda_available"] is True
    assert gpu_data["vram_total_mb"] >= 8000

    presets = await api_client.get("/api/presets/subtitles")
    assert presets.status_code == 200
    assert len(presets.json()) >= 3


def test_m1_websocket_connection_and_gpu_snapshot() -> None:
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        first_msg = ws.receive_json()
        assert "type" in first_msg
        ws.send_text("ping")
        # Drain until pong (in case log history preceded it)
        got_pong = False
        for _ in range(25):
            pkt = ws.receive_json()
            if pkt.get("type") == "pong":
                got_pong = True
                break
        assert got_pong is True


@pytest.mark.asyncio
async def test_m2_encoder_probe_detects_nvenc() -> None:
    probe = await probe_encoders(force=True)
    assert "libx264" in probe["available"]
    assert probe["details"]["h264_nvenc"] is True
    assert probe["selected"] == "h264_nvenc"


@pytest.mark.asyncio
async def test_m2_ingest_local_and_basic_cut(api_client: AsyncClient, fixtures_dir: Path) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Test M2 Project"},
    )
    assert imp.status_code == 200
    payload = imp.json()
    project_id = payload["project"]["id"]
    job = await _wait_job(api_client, payload["job"]["id"])
    assert job["status"] == "completed", job.get("error")

    proj_resp = await api_client.get(f"/api/projects/{project_id}")
    assert proj_resp.status_code == 200
    proj = proj_resp.json()
    assert proj["status"] == "ready"
    assert proj["width"] == 1920
    assert proj["height"] == 1080
    assert 11.0 <= proj["duration"] <= 13.0
    assert Path(proj["proxy_path"]).exists()
    assert Path(proj["thumbnail_path"]).exists()

    # Basic cut 1.0 -> 4.5s
    cut_resp = await api_client.post(
        f"/api/projects/{project_id}/basic-cut",
        json={"start_time": 1.0, "end_time": 4.5, "title": "Basic Cut Test"},
    )
    assert cut_resp.status_code == 200
    cut_payload = cut_resp.json()
    cut_job = await _wait_job(api_client, cut_payload["job"]["id"])
    assert cut_job["status"] == "completed", cut_job.get("error")

    out_path = Path(cut_job["result_json"]["preview_path"])
    assert out_path.exists()
    meta = await probe_media(out_path)
    assert 3.1 <= meta["duration"] <= 3.9
    assert meta["width"] == 1920 and meta["height"] == 1080

    # Clean up test project
    del_resp = await api_client.delete(f"/api/projects/{project_id}")
    assert del_resp.status_code == 200
