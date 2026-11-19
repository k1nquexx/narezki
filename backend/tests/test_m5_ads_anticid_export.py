from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.services.ingest.ffprobe_utils import probe_media
from app.services.renderer.ad_injector import build_overlay_input_args
from tests.test_m1_m2_ingest import _wait_job


def test_m5_webm_alpha_decoder_flag_before_input(tmp_path: Path) -> None:
    webm_path = tmp_path / "promo_alpha.webm"
    webm_path.write_bytes(b"")
    args = build_overlay_input_args(webm_path, clip_duration=10.0, video_codec="vp9")
    assert args[:2] == ["-c:v", "libvpx-vp9"]
    assert args.index("-c:v") < args.index("-i")


@pytest.mark.asyncio
async def test_m5_ads_anticid_and_batch_export(
    api_client: AsyncClient,
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    ad_video = fixtures_dir / "ad_video.mp4"
    ad_png = fixtures_dir / "ad_overlay.png"

    imp = await api_client.post(
        "/api/projects/import-local",
        json={"file_path": str(sample), "title": "Test M5 Export"},
    )
    proj_id = imp.json()["project"]["id"]
    await _wait_job(api_client, imp.json()["job"]["id"])

    # Create Mode 1 (video insert mid_roll) & Mode 2 (PNG overlay) presets
    ad1_resp = await api_client.post(
        "/api/presets/ads",
        json={
            "name": "Sponsor MidRoll",
            "ad_type": "video_insert",
            "asset_path": str(ad_video),
            "insert_position": "mid_roll",
            "insert_at_sec": 2.0,
        },
    )
    assert ad1_resp.status_code == 200
    ad1_id = ad1_resp.json()["id"]

    ad2_resp = await api_client.post(
        "/api/presets/ads",
        json={
            "name": "TG Overlay",
            "ad_type": "overlay",
            "asset_path": str(ad_png),
            "overlay_x": 50.0,
            "overlay_y": 25.0,
            "overlay_scale": 40.0,
            "overlay_start": 0.5,
            "overlay_end": 3.5,
            "fade_duration": 0.3,
        },
    )
    assert ad2_resp.status_code == 200
    ad2_id = ad2_resp.json()["id"]

    # Clip 1: Mode 1 mid_roll video ad (4s clip + 3s ad = ~7s)
    c1 = (
        await api_client.post(
            "/api/clips",
            json={
                "project_id": proj_id,
                "title": "Clip MidRoll Ad",
                "start_time": 4.0,
                "end_time": 8.0,
                "crop_mode": "cinema_blur",
                "ad_preset_id": ad1_id,
            },
        )
    ).json()

    # Clip 2: Mode 2 PNG overlay + Anti-Content ID (4s / 1.05 = ~3.8s)
    c2 = (
        await api_client.post(
            "/api/clips",
            json={
                "project_id": proj_id,
                "title": "Clip Overlay + AntiCID",
                "start_time": 8.0,
                "end_time": 12.0,
                "crop_mode": "cinema_blur",
                "ad_preset_id": ad2_id,
                "anti_cid_enabled": True,
            },
        )
    ).json()
    await api_client.patch(
        f"/api/clips/{c2['id']}",
        json={
            "description": "Описание для публикации в Shorts",
            "tags_json": ["#anime", "#shorts", "#рек"],
            "anti_cid_json": {
                "zoom": 1.04,
                "speed": 1.05,
                "brightness": 0.01,
                "contrast": 1.03,
                "saturation": 1.04,
                "strip_metadata": True,
            },
        },
    )

    # Clip 3: Mode 1 outro video ad (4s clip + 3s ad = ~7s)
    c3 = (
        await api_client.post(
            "/api/clips",
            json={
                "project_id": proj_id,
                "title": "Clip Outro Ad",
                "start_time": 0.0,
                "end_time": 4.0,
                "crop_mode": "cinema_blur",
                "ad_preset_id": ad1_id,
            },
        )
    ).json()
    await api_client.patch(
        f"/api/clips/{c3['id']}",
        json={"ad_override_json": {"insert_position": "outro"}},
    )

    export_out = tmp_path / "batch_exports"
    batch_resp = await api_client.post(
        f"/api/projects/{proj_id}/export-batch",
        json={"clip_ids": [c1["id"], c2["id"], c3["id"]], "output_dir": str(export_out)},
    )
    assert batch_resp.status_code == 200
    batch_job = await _wait_job(api_client, batch_resp.json()["id"], timeout=240.0)
    assert batch_job["status"] == "completed", batch_job.get("error")

    batch_dir = Path(batch_job["result_json"]["export_dir"])
    mp4_1 = batch_dir / "clip_01.mp4"
    txt_1 = batch_dir / "clip_01.txt"
    mp4_2 = batch_dir / "clip_02.mp4"
    txt_2 = batch_dir / "clip_02.txt"
    mp4_3 = batch_dir / "clip_03.mp4"
    txt_3 = batch_dir / "clip_03.txt"

    for p in (mp4_1, txt_1, mp4_2, txt_2, mp4_3, txt_3):
        assert p.exists(), f"Missing batch artifact: {p}"

    meta_1 = await probe_media(mp4_1)
    assert meta_1["width"] == 1080 and meta_1["height"] == 1920
    assert 6.5 <= meta_1["duration"] <= 7.6  # 4s main + 3s sponsor

    meta_2 = await probe_media(mp4_2)
    assert meta_2["width"] == 1080 and meta_2["height"] == 1920
    assert 3.4 <= meta_2["duration"] <= 4.0  # 4s / 1.05

    meta_3 = await probe_media(mp4_3)
    assert meta_3["width"] == 1080 and meta_3["height"] == 1920
    assert 6.5 <= meta_3["duration"] <= 7.6  # 4s main + 3s outro sponsor

    txt2_content = txt_2.read_text(encoding="utf-8")
    assert "Clip Overlay + AntiCID" in txt2_content
    assert "#anime" in txt2_content
    assert "Описание для публикации в Shorts" in txt2_content

    # Verify HTTP Range 206 media streaming and path-traversal protection
    range_resp = await api_client.get(
        "/api/media/stream",
        params={"path": str(mp4_1)},
        headers={"Range": "bytes=0-1023"},
    )
    assert range_resp.status_code == 206
    assert range_resp.headers["content-range"].startswith("bytes 0-1023/")
    assert len(range_resp.content) == 1024

    forbidden_resp = await api_client.get(
        "/api/media/stream",
        params={"path": r"C:\Windows\System32\drivers\etc\hosts"},
    )
    assert forbidden_resp.status_code == 403

    await api_client.delete(f"/api/presets/ads/{ad1_id}")
    await api_client.delete(f"/api/presets/ads/{ad2_id}")
    await api_client.delete(f"/api/projects/{proj_id}")
