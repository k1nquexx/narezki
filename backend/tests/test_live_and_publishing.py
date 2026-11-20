from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.config import get_ffmpeg_path, get_project_dir
from app.core.database import AsyncSessionLocal, load_settings, save_settings
from app.core.models import Clip, LiveChannel, Project, PublishQueueItem, SocialAccount, new_id
from app.core.queue_manager import queue_manager
from app.services.analyzer.llm_highlights import (
    analyze_highlights_heuristic,
    build_multimodal_chunks,
)
from app.services.ingest.ffprobe_utils import probe_media
from app.services.live import publisher as pub_mod
from app.services.live.chat_analyzer import (
    ChatPulseAnalyzer,
    count_hype_and_emotes,
    detect_clip_trigger,
    parse_kick_pusher_message,
    parse_twitch_irc_line,
    parse_youtube_chat_actions,
)
from app.services.live.live_watcher import RollingBufferManager, check_channel_live
from app.services.live.publisher import (
    PublisherService,
    compute_next_slot,
    create_signed_clip_token,
    upload_instagram_reel,
    upload_tiktok_video,
    upload_youtube_short,
    verify_signed_clip_token,
)
from tests.test_m1_m2_ingest import _wait_job


def _run_ffmpeg(cmd: list[str]) -> None:
    kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed ({proc.returncode}): {proc.stderr[-500:]}")


def test_chat_pulse_analyzer_and_parsers() -> None:
    # Platform chat line/frame parsers
    assert parse_twitch_irc_line("@badges=broadcaster/1 :streamer!u@u.tmi.twitch.tv PRIVMSG #ch :KEKW !clip") == (
        "streamer",
        "KEKW !clip",
    )
    assert parse_twitch_irc_line("PING :tmi.twitch.tv") is None

    kick_raw = json.dumps(
        {
            "event": "App\\Events\\ChatMessageEvent",
            "data": json.dumps({"sender": {"username": "kick_viewer"}, "content": "POGGERS OMG 🔥"}),
        }
    )
    assert parse_kick_pusher_message(kick_raw) == ("kick_viewer", "POGGERS OMG 🔥")
    assert parse_kick_pusher_message('{"event": "pusher:pong"}') is None

    yt_payload = {
        "continuationContents": {
            "liveChatContinuation": {
                "actions": [
                    {
                        "addChatItemAction": {
                            "item": {
                                "liveChatTextMessageRenderer": {
                                    "authorName": {"simpleText": "yt_fan"},
                                    "message": {
                                        "runs": [
                                            {"text": "клип это "},
                                            {"emoji": {"shortcuts": [":fire:"]}},
                                        ]
                                    },
                                }
                            }
                        }
                    }
                ]
            }
        }
    }
    assert parse_youtube_chat_actions(yt_payload) == [("yt_fan", "клип это :fire:")]

    assert count_hype_and_emotes("KEKW POG 🔥🔥🔥!!!") >= 4
    assert detect_clip_trigger("!clip right now") is True
    assert detect_clip_trigger("сделайте клип это жесть") is True
    assert detect_clip_trigger("hello world") is False

    # Spike detection with unique-author anti-spam
    analyzer = ChatPulseAnalyzer(keep_seconds=600, warmup_seconds=45)
    for sec in range(0, 50):
        analyzer.record_message(sec, f"calm_{sec % 4}", "hello")
    assert analyzer.evaluate_pulse(50) is None

    # One spammer sending 20 messages in 2 seconds must be ignored (< 3 unique authors)
    for _ in range(20):
        analyzer.record_message(52, "single_spam_bot", "KEKW KEKW 🔥")
    assert analyzer.evaluate_pulse(52, sensitivity=1.5, min_gap_sec=10.0) is None

    # Genuine multi-author burst triggers spike
    for i in range(18):
        analyzer.record_message(60 + (i % 4), f"viewer_{i}", "POGGERS OMEGALUL жесть 🔥")
    spike = analyzer.evaluate_pulse(63, sensitivity=1.5, min_gap_sec=30.0)
    assert spike is not None
    assert spike.unique_authors >= 3
    assert spike.emote_hits > 0
    assert spike.ratio > 1.5
    assert spike.score >= 55.0

    # Cooldown must block immediate re-trigger
    assert analyzer.evaluate_pulse(66, sensitivity=1.5, min_gap_sec=30.0) is None


def test_chat_spikes_feed_into_analyzer() -> None:
    spikes = [
        {
            "timestamp": 38.0,
            "start": 32.0,
            "end": 42.0,
            "ratio": 4.2,
            "unique_authors": 24,
            "emote_hits": 19,
            "score": 88.0,
            "reason": "Чат x4.2 (24 авторов, 19 эмодзи/хайп)",
        }
    ]
    chunks = build_multimodal_chunks(
        duration=80.0,
        transcript=[],
        audio_peaks=[],
        action_bursts=[],
        chunk_minutes=10,
        chat_spikes=spikes,
    )
    assert len(chunks) == 1
    assert "[CHAT_SPIKE]" in chunks[0][2]
    assert "x4.2" in chunks[0][2]

    highlights = analyze_highlights_heuristic(
        duration=80.0,
        transcript=[],
        audio_peaks=[{"start": 35.0, "end": 39.0, "delta_db": 9.0, "peak_db": -6.0, "score": 82.0}],
        scenes=[30.0, 34.0, 42.0],
        action_bursts=[],
        min_duration=15.0,
        max_duration=40.0,
        max_highlights=5,
        chat_spikes=spikes,
    )
    assert len(highlights) >= 1
    top = highlights[0]
    assert top.start_time <= 42.0 and top.end_time >= 32.0
    assert top.virality_score >= 60.0


@pytest.mark.asyncio
async def test_rolling_buffer_segment_scan_prune_and_extract(
    fixtures_dir: Path,
    tmp_path: Path,
) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    ffmpeg = get_ffmpeg_path()
    buf_dir = tmp_path / "ring_buf"
    buf_dir.mkdir(parents=True, exist_ok=True)

    mgr = RollingBufferManager(buf_dir, buffer_minutes=2, segment_seconds=2)
    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-i",
            str(sample),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-g",
            "30",
            "-keyint_min",
            "30",
            "-c:a",
            "aac",
            "-f",
            "segment",
            "-segment_time",
            "2",
            "-segment_format",
            "mpegts",
            "-segment_list",
            str(mgr.playlist_path),
            "-segment_list_type",
            "csv",
            "-reset_timestamps",
            "1",
            str(buf_dir / "seg_%06d.ts"),
        ]
    )

    segs = mgr.scan_segments()
    assert len(segs) >= 3
    assert mgr.get_latest_time() >= 8.0
    assert mgr.get_disk_usage_mb() > 0.0

    # Extract [1.5 .. 6.5] window to MP4 cleanly
    out_mp4 = tmp_path / "slice.mp4"
    _s, _e, res_path = await mgr.extract_window(1.5, 6.5, out_mp4)
    assert res_path.exists()
    info = await probe_media(res_path, require_video=True)
    assert info["width"] == 1920
    assert info["height"] == 1080
    assert info["duration"] >= 3.0

    # Prune behavior when timestamps exceed buffer_minutes (2 * 60 = 120s)
    synthetic_rows = []
    for idx, seg in enumerate(segs):
        t0 = float(idx * 60)
        synthetic_rows.append(f"{seg.filename},{t0:.3f},{t0 + 50.0:.3f}")
    mgr.playlist_path.write_text("\n".join(synthetic_rows) + "\n", encoding="utf-8")
    removed = mgr.prune_old_segments()
    assert removed >= 1
    assert len(mgr.scan_segments()) < len(segs)


@pytest.mark.asyncio
async def test_live_process_clip_zero_ai_unified_pipeline_and_publish_guard(
    api_client: AsyncClient,
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sample = fixtures_dir / "sample_1080p.mp4"
    ad_png = fixtures_dir / "ad_overlay.png"

    # Mock fast deterministic Whisper output so no GPU download/delay occurs inside unit CI
    async def _fake_transcribe(**_kwargs: Any) -> dict[str, Any]:
        return {
            "language": "en",
            "device": "cpu",
            "compute_type": "int8",
            "segments": [{"start": 0.2, "end": 2.4, "text": "Insane clutch moment!", "words": []}],
            "words": [
                {"word": "Insane", "start": 0.2, "end": 0.8},
                {"word": "clutch", "start": 0.9, "end": 1.6},
                {"word": "moment!", "start": 1.7, "end": 2.4},
            ],
        }

    monkeypatch.setattr("app.services.live.handlers.transcribe_video", _fake_transcribe)

    ad_resp = await api_client.post(
        "/api/presets/ads",
        json={
            "name": "Live Overlay Test",
            "ad_type": "overlay",
            "asset_path": str(ad_png),
            "overlay_x": 50.0,
            "overlay_y": 20.0,
            "overlay_scale": 35.0,
            "overlay_start": 0.3,
            "overlay_end": 2.5,
            "fade_duration": 0.2,
        },
    )
    assert ad_resp.status_code == 200
    ad_id = ad_resp.json()["id"]

    acc_resp = await api_client.post(
        "/api/publishing/accounts",
        json={
            "platform": "youtube",
            "name": "YT Shorts Channel",
            "auth_type": "token",
            "credentials_json": {"access_token": "tok_live_test"},
        },
    )
    assert acc_resp.status_code == 200
    acc_id = acc_resp.json()["id"]

    ch_resp = await api_client.post(
        "/api/live/channels",
        json={
            "channel_url": "https://www.twitch.tv/live_test_channel",
            "name": "live_test_channel",
            "ai_mode": "zero_ai",
            "crop_mode": "cinema_blur",
            "ad_preset_id": ad_id,
            "anti_cid_enabled": True,
            "auto_publish": True,
            "publish_account_ids_json": [acc_id],
        },
    )
    assert ch_resp.status_code == 200
    ch_id = ch_resp.json()["id"]

    async def _create_and_enqueue_live_clip(tag: str) -> tuple[str, str]:
        proj_id = new_id()
        clip_id = new_id()
        src_dir = get_project_dir(proj_id) / "source"
        src_dir.mkdir(parents=True, exist_ok=True)
        slice_mp4 = src_dir / f"live_{clip_id}.mp4"
        _run_ffmpeg(
            [
                get_ffmpeg_path(),
                "-y",
                "-ss",
                "0.0",
                "-i",
                str(sample),
                "-t",
                "3.5",
                "-c",
                "copy",
                str(slice_mp4),
            ]
        )
        info = await probe_media(slice_mp4, require_video=True)
        async with AsyncSessionLocal() as db:
            db.add(
                Project(
                    id=proj_id,
                    title=f"LIVE Test {tag}",
                    source_type="live",
                    source_url="https://www.twitch.tv/live_test_channel",
                    source_path=str(slice_mp4),
                    duration=info["duration"],
                    width=info["width"],
                    height=info["height"],
                    fps=info["fps"],
                    has_audio=info["has_audio"],
                    status="ready",
                    analysis_json={"live_channel_id": ch_id, "channel_name": "live_test_channel", "platform": "twitch"},
                )
            )
            await db.flush()
            db.add(
                Clip(
                    id=clip_id,
                    project_id=proj_id,
                    title=f"Zero-AI Highlight {tag}",
                    hook_text="Чат взорвался!",
                    hook_enabled=True,
                    virality_score=91.0,
                    category="live_spike",
                    reason="Всплеск чата x4.5",
                    start_time=0.0,
                    end_time=float(info["duration"]),
                    crop_mode="cinema_blur",
                    subtitles_enabled=True,
                    ad_preset_id=ad_id,
                    anti_cid_enabled=True,
                    anti_cid_json={
                        "zoom": 1.03,
                        "speed": 1.01,
                        "brightness": 0.01,
                        "contrast": 1.02,
                        "saturation": 1.02,
                        "strip_metadata": True,
                    },
                    status="draft",
                )
            )
            await db.commit()

        job = await queue_manager.enqueue(
            "live_process_clip",
            payload={"channel_id": ch_id, "clip_id": clip_id, "ai_mode": "zero_ai"},
            project_id=proj_id,
            clip_id=clip_id,
        )
        return clip_id, job.id

    async with AsyncSessionLocal() as db:
        orig_settings = await load_settings(db)
        orig_mode = orig_settings.publish_mode
        orig_live_auto = orig_settings.live_auto_publish

    try:
        # 1. With local_only, render completes to 1080x1920 MP4 + .txt and creates 0 queue items
        async with AsyncSessionLocal() as db:
            cur = await load_settings(db)
            cur.publish_mode = "local_only"
            cur.live_auto_publish = False
            await save_settings(db, cur)

        c1_id, j1_id = await _create_and_enqueue_live_clip("local")
        j1 = await _wait_job(api_client, j1_id, timeout=120.0)
        assert j1["status"] == "completed", j1.get("error")
        out_mp4 = Path(j1["result_json"]["output_path"])
        assert out_mp4.exists()
        assert Path(j1["result_json"]["txt_path"]).exists()
        out_probe = await probe_media(out_mp4, require_video=True)
        assert out_probe["width"] == 1080
        assert out_probe["height"] == 1920
        assert j1["result_json"]["published_items"] == 0

        pub_tok = create_signed_clip_token(c1_id, expires_sec=60)
        pub_resp = await api_client.get(f"/api/publishing/public/{pub_tok}.mp4")
        assert pub_resp.status_code == 200
        assert len(pub_resp.content) > 1000

        # 2. With auto_publish enabled, clip is automatically enqueued in publish_queue
        async with AsyncSessionLocal() as db:
            cur = await load_settings(db)
            cur.publish_mode = "auto_publish"
            cur.live_auto_publish = True
            await save_settings(db, cur)

        c2_id, j2_id = await _create_and_enqueue_live_clip("autopub")
        j2 = await _wait_job(api_client, j2_id, timeout=120.0)
        assert j2["status"] == "completed", j2.get("error")
        assert j2["result_json"]["published_items"] == 1

        async with AsyncSessionLocal() as db:
            rows = (
                await db.execute(select(PublishQueueItem).where(PublishQueueItem.clip_id == c2_id))
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].status == "scheduled"
            assert rows[0].platform == "youtube"
            rows_c1 = (
                await db.execute(select(PublishQueueItem).where(PublishQueueItem.clip_id == c1_id))
            ).scalars().all()
            assert len(rows_c1) == 0
    finally:
        async with AsyncSessionLocal() as db:
            cur = await load_settings(db)
            cur.publish_mode = orig_mode
            cur.live_auto_publish = orig_live_auto
            await save_settings(db, cur)


@pytest.mark.asyncio
async def test_publisher_scheduler_caps_tokens_and_mocked_uploaders(
    fixtures_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Anti-shadowban spacing and daily rollover to 09:00 local
    now = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    slot1 = compute_next_slot([], gap_minutes=120, daily_cap=2, now=now)
    assert slot1 == now

    slot2 = compute_next_slot([slot1], gap_minutes=120, daily_cap=2, now=now)
    assert slot2 == now + timedelta(minutes=120)

    slot3 = compute_next_slot([slot1, slot2], gap_minutes=120, daily_cap=2, now=now)
    assert slot3.astimezone().date() > slot2.astimezone().date()
    assert slot3.astimezone().hour == 9

    # 2. Signed URL token validation
    tok = create_signed_clip_token("clip_abc123", expires_sec=60)
    assert verify_signed_clip_token(tok) == "clip_abc123"
    assert verify_signed_clip_token(tok[:-2] + "xx") is None

    # 3. local_only guard on PublisherService.process_due_once
    svc = PublisherService()
    async with AsyncSessionLocal() as db:
        cur = await load_settings(db)
        prev_mode = cur.publish_mode
        cur.publish_mode = "local_only"
        await save_settings(db, cur)
    try:
        assert await svc.process_due_once() == 0
    finally:
        async with AsyncSessionLocal() as db:
            cur = await load_settings(db)
            cur.publish_mode = prev_mode
            await save_settings(db, cur)

    # 4. Mocked HTTP uploaders for YouTube Shorts, TikTok (SELF_ONLY fallback), IG Reels
    async with AsyncSessionLocal() as db:
        settings = await load_settings(db)

    video_path = fixtures_dir / "sample_1080p.mp4"

    async def _noop_save(_creds: dict[str, Any]) -> None:
        return None

    async def _mock_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "googleapis.com/upload/youtube" in url:
            return httpx.Response(200, headers={"Location": "https://upload.yt.test/put"}, json={})
        if "upload.yt.test" in url:
            return httpx.Response(200, json={"id": "yt_short_999"})
        if "open.tiktokapis.com/v2/post/publish/creator_info/query/" in url:
            return httpx.Response(200, json={"data": {"privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"]}})
        if "open.tiktokapis.com/v2/post/publish/video/init/" in url:
            body = json.loads(request.content.decode())
            if body["post_info"]["privacy_level"] != "SELF_ONLY":
                return httpx.Response(
                    403,
                    json={"error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "Sandbox"}},
                )
            return httpx.Response(200, json={"data": {"publish_id": "tt_pub_42", "upload_url": "https://upload.tt.test/chunk"}})
        if "upload.tt.test" in url:
            return httpx.Response(200)
        if "open.tiktokapis.com/v2/post/publish/status/fetch/" in url:
            return httpx.Response(200, json={"data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": ["tt_post_77"]}})
        if "graph.instagram.com/ig_user_1/media_publish" in url:
            return httpx.Response(200, json={"id": "ig_media_55"})
        if "graph.instagram.com/ig_user_1/media" in url:
            return httpx.Response(200, json={"id": "ig_box_11"})
        if "graph.instagram.com/ig_box_11" in url:
            return httpx.Response(200, json={"status_code": "FINISHED"})
        if "graph.instagram.com/ig_media_55" in url:
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/XYZ/"})
        return httpx.Response(404, json={"error": "unexpected url"})

    transport = httpx.MockTransport(_mock_handler)
    real_async_client = httpx.AsyncClient

    class _PatchedClient(real_async_client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(pub_mod.httpx, "AsyncClient", _PatchedClient)

    yt_id, yt_url = await upload_youtube_short(
        creds={"access_token": "tok_yt", "expires_at": time.time() + 3600},
        video_path=video_path,
        title="Test Short",
        caption="Clutch!",
        tags=["gaming"],
        privacy="public",
        settings=settings,
        save_creds=_noop_save,
    )
    assert yt_id == "yt_short_999"
    assert yt_url.endswith("/yt_short_999")

    tt_id, _tt_url = await upload_tiktok_video(
        creds={"access_token": "tok_tt", "expires_at": time.time() + 3600},
        video_path=video_path,
        caption="TikTok Clutch",
        privacy="public",
        settings=settings,
        save_creds=_noop_save,
    )
    assert tt_id == "tt_post_77"

    ig_id, ig_url = await upload_instagram_reel(
        creds={"access_token": "tok_ig", "ig_user_id": "ig_user_1"},
        video_path=video_path,
        caption="IG Reel Clutch",
        settings=settings,
        save_creds=_noop_save,
        signed_video_url="https://example.trycloudflare.com/api/publishing/public/abc.mp4",
    )
    assert ig_id == "ig_media_55"
    assert "instagram.com/reel/" in ig_url


@pytest.mark.asyncio
async def test_live_and_publishing_http_api(
    api_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Live channel CRUD & URL normalization
    r_create = await api_client.post(
        "/api/live/channels",
        json={"channel_url": "kick.com/xQc"},
    )
    assert r_create.status_code == 200
    ch = r_create.json()
    assert ch["platform"] == "kick"
    assert ch["channel_url"] == "https://kick.com/xqc"
    assert ch["name"] == "xqc"

    r_list = await api_client.get("/api/live/channels")
    assert r_list.status_code == 200
    assert any(item["id"] == ch["id"] for item in r_list.json())

    r_patch = await api_client.patch(
        f"/api/live/channels/{ch['id']}",
        json={"chat_sensitivity": 2.1, "ai_mode": "auto"},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["chat_sensitivity"] == 2.1
    assert r_patch.json()["ai_mode"] == "auto"

    async def _fake_probe(_plat: str, _url: str) -> dict[str, Any]:
        return {
            "is_live": True,
            "stream_title": "Mock Live Broadcast",
            "viewer_count": 4200,
            "video_id": "123",
            "stream_target": _url,
            "error": None,
        }

    lw_module = sys.modules["app.services.live.live_watcher"]
    monkeypatch.setattr(lw_module, "check_channel_live", _fake_probe)
    # Keep enabled=False so check does not launch real ffmpeg against mock URL
    await api_client.patch(f"/api/live/channels/{ch['id']}", json={"enabled": False})
    r_check = await api_client.post(f"/api/live/channels/{ch['id']}/check")
    assert r_check.status_code == 200
    assert r_check.json()["probe"]["is_live"] is True
    assert r_check.json()["channel"]["status"] == "online"

    r_clips = await api_client.get("/api/live/clips")
    assert r_clips.status_code == 200

    # Publishing account security: credentials_json must never leak in API JSON
    r_acc = await api_client.post(
        "/api/publishing/accounts",
        json={
            "platform": "tiktok",
            "name": "My TikTok",
            "auth_type": "cookies",
            "credentials_json": {"access_token": "secret_12345", "cookies": "sessionid=abc"},
        },
    )
    assert r_acc.status_code == 200
    acc_body = r_acc.json()
    assert acc_body["has_credentials"] is True
    assert "credentials_json" not in acc_body
    assert "secret_12345" not in json.dumps(acc_body)

    # Queue + signed public MP4 404 check
    r_queue = await api_client.get("/api/publishing/queue")
    assert r_queue.status_code == 200
    if r_queue.json():
        q_id = r_queue.json()[0]["id"]
        assert (await api_client.post(f"/api/publishing/queue/{q_id}/cancel")).json()["status"] == "cancelled"
        assert (await api_client.post(f"/api/publishing/queue/{q_id}/retry")).json()["status"] == "scheduled"
        assert (await api_client.delete(f"/api/publishing/queue/{q_id}")).status_code == 200

    r_bad_tok = await api_client.get("/api/publishing/public/invalid_token.mp4")
    assert r_bad_tok.status_code == 404

    # Cleanup
    assert (await api_client.delete(f"/api/publishing/accounts/{acc_body['id']}")).status_code == 200
    assert (await api_client.delete(f"/api/live/channels/{ch['id']}")).status_code == 200
