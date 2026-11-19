from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.ingest.ffprobe_utils import probe_media
from app.services.renderer.subtitle_ass import generate_ass_file, hex_to_ass_color
from app.services.tracker.smoother import smooth_trajectory
from app.services.tracker.yolo_tracker import track_clip_subject
from tests.test_m1_m2_ingest import _wait_job


def test_m4_smoother_scene_cut_reset_and_ass_colors(tmp_path: Path) -> None:
    raw = [
        {"t": 0.0, "cx": 0.2, "cy": 0.4, "conf": 0.9},
        {"t": 0.2, "cx": 0.21, "cy": 0.4, "conf": 0.9},
        {"t": 0.4, "cx": 0.85, "cy": 0.4, "conf": 0.9},
    ]
    smoothed = smooth_trajectory(raw, scene_cuts=[0.35], clip_start=0.0, method="kalman", strength=0.8)
    # Hard cut reset at t=0.35 means t=0.4 snaps straight to ~0.85 instead of lagging near 0.3
    assert smoothed[-1]["cx"] > 0.80

    assert hex_to_ass_color("#FFE600") == "&H0000E6FF"
    ass_file = tmp_path / "test.ass"
    generate_ass_file(
        cues=[
            {
                "id": "1",
                "start": 0.1,
                "end": 1.8,
                "text": "Привет мир",
                "words": [
                    {"word": "Привет", "start": 0.1, "end": 0.8},
                    {"word": "мир", "start": 0.9, "end": 1.8},
                ],
            }
        ],
        preset={"karaoke_mode": "karaoke_fill", "highlight_color": "#FFE600"},
        output_path=ass_file,
        subtitles_enabled=True,
        hook_enabled=True,
        hook_text="ЭПИЧНЫЙ МОМЕНТ",
        clip_duration=4.0,
    )
    txt = ass_file.read_text(encoding="utf-8-sig")
    assert "PlayResX: 1080" in txt
    assert "PlayResY: 1920" in txt
    assert "\\kf" in txt
    assert "ЭПИЧНЫЙ МОМЕНТ" in txt


@pytest.mark.asyncio
async def test_m4_yolo_tracking_and_all_three_crop_modes(
    api_client: AsyncClient,
    fixtures_dir: Path,
) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    bg_video = fixtures_dir / "bg_split.mp4"

    # Direct GPU tracking check on moving person segment (0-4s)
    track_data = await track_clip_subject(
        video_path=sample,
        clip_start=0.0,
        clip_end=4.0,
        yolo_model_path="yolov8n.pt",
        sample_fps=4.0,
        scene_cuts=[4.0, 8.0],
    )
    assert track_data["source_width"] == 1920
    assert len(track_data["points"]) >= 10

    # Import project + background for full 9:16 renders
    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Test M4 Reframe"},
    )
    proj_id = imp.json()["project"]["id"]
    imp_job = await _wait_job(api_client, imp.json()["job"]["id"])
    assert imp_job["status"] == "completed"

    bg_resp = await api_client.post(
        "/api/presets/backgrounds",
        json={"file_path": str(bg_video), "name": "Mandelbrot Test BG"},
    )
    assert bg_resp.status_code == 200
    bg_id = bg_resp.json()["id"]

    for mode in ("auto_track", "cinema_blur", "split_screen"):
        c_resp = await api_client.post(
            "/api/clips",
            json={
                "project_id": proj_id,
                "title": f"Clip {mode}",
                "start_time": 0.0,
                "end_time": 4.0,
                "crop_mode": mode,
                "background_asset_id": bg_id if mode == "split_screen" else None,
            },
        )
        assert c_resp.status_code == 200
        clip_id = c_resp.json()["id"]

        await api_client.patch(
            f"/api/clips/{clip_id}",
            json={
                "hook_enabled": True,
                "hook_text": f"РЕЖИМ {mode}",
                "subtitles_enabled": True,
                "subtitles_json": [
                    {
                        "id": "c1",
                        "start": 0.2,
                        "end": 3.5,
                        "text": "Тест субтитров караоке",
                        "words": [
                            {"word": "Тест", "start": 0.2, "end": 1.1},
                            {"word": "субтитров", "start": 1.1, "end": 2.3},
                            {"word": "караоке", "start": 2.3, "end": 3.5},
                        ],
                    }
                ],
            },
        )

        ren_resp = await api_client.post(f"/api/clips/{clip_id}/render-preview")
        assert ren_resp.status_code == 200
        ren_job = await _wait_job(api_client, ren_resp.json()["id"], timeout=120.0)
        assert ren_job["status"] == "completed", ren_job.get("error")

        out_mp4 = Path(ren_job["result_json"]["output_path"])
        assert out_mp4.exists()
        meta = await probe_media(out_mp4)
        assert meta["width"] == 1080
        assert meta["height"] == 1920
        assert meta["has_audio"] is True
        assert 3.5 <= meta["duration"] <= 4.5

    await api_client.delete(f"/api/presets/backgrounds/{bg_id}")
    await api_client.delete(f"/api/projects/{proj_id}")
