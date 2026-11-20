from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from ...core.config import DATA_DIR, AppSettingsModel
from ...core.database import AsyncSessionLocal, load_settings
from ...core.models import Clip, PublishQueueItem, SocialAccount
from ...core.schemas import PublishQueueItemOut
from ...core.ws_manager import ws_manager

logger = logging.getLogger("narezki.live.publisher")

YT_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/youtube.readonly"
)
IG_API = "https://graph.instagram.com"
IG_UPLOAD = "https://rupload.facebook.com/ig-api-upload/v21.0"
IG_SCOPES = "instagram_business_basic,instagram_business_content_publish"
TT_API = "https://open.tiktokapis.com/v2"
TT_SCOPES = "user.info.basic,video.publish"

TT_PRIVACY_MAP = {
    "public": ["PUBLIC_TO_EVERYONE", "FOLLOWER_OF_CREATOR", "MUTUAL_FOLLOW_FRIENDS"],
    "unlisted": ["MUTUAL_FOLLOW_FRIENDS", "FOLLOWER_OF_CREATOR"],
    "private": ["SELF_ONLY"],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_next_slot(
    existing_times: list[datetime],
    gap_minutes: int,
    daily_cap: int,
    now: Optional[datetime] = None,
) -> datetime:
    """Compute earliest anti-shadowban publish timestamp respecting gap & daily cap (PeakCut pattern)."""
    ref_now = _as_aware_utc(now or _utcnow())
    gap = timedelta(minutes=max(1, int(gap_minutes)))
    cap = max(1, int(daily_cap))

    normalized = [_as_aware_utc(dt) for dt in existing_times]
    latest = max(normalized) if normalized else None
    candidate = ref_now if latest is None else max(ref_now, latest + gap)

    for _ in range(30):
        local_day = candidate.astimezone().date()
        day_count = sum(1 for dt in normalized if dt.astimezone().date() == local_day)
        if day_count < cap:
            return candidate
        next_day_local = (candidate.astimezone() + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )
        candidate = next_day_local.astimezone(timezone.utc)

    return candidate


def _get_link_key() -> bytes:
    key_file = DATA_DIR / "publish_link.key"
    if not key_file.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(secrets.token_bytes(32))
    return key_file.read_bytes()


def create_signed_clip_token(clip_id: str, expires_sec: int = 3600) -> str:
    payload = json.dumps([clip_id, int(time.time()) + expires_sec], separators=(",", ":")).encode()
    b64 = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = base64.urlsafe_b64encode(
        hmac.new(_get_link_key(), b64.encode(), hashlib.sha256).digest()[:18]
    ).decode().rstrip("=")
    return f"{b64}.{sig}"


def verify_signed_clip_token(token: str) -> Optional[str]:
    try:
        b64, sig = token.split(".", 1)
        expected = base64.urlsafe_b64encode(
            hmac.new(_get_link_key(), b64.encode(), hashlib.sha256).digest()[:18]
        ).decode().rstrip("=")
        if not hmac.compare_digest(sig, expected):
            return None
        padded = b64 + "=" * (-len(b64) % 4)
        clip_id, until = json.loads(base64.urlsafe_b64decode(padded))
        if time.time() > float(until):
            return None
        return str(clip_id)
    except Exception:
        return None


def _app_keys_for(platform: str, settings: AppSettingsModel) -> tuple[str, str]:
    if platform == "youtube":
        return settings.youtube_client_id.strip(), settings.youtube_client_secret.strip()
    if platform == "tiktok":
        return settings.tiktok_client_key.strip(), settings.tiktok_client_secret.strip()
    if platform == "instagram":
        return settings.instagram_app_id.strip(), settings.instagram_app_secret.strip()
    raise ValueError(f"Неподдерживаемая платформа: {platform}")


def build_oauth_url(
    platform: str,
    settings: AppSettingsModel,
    redirect_uri: str,
    state: str,
) -> str:
    cid, secret = _app_keys_for(platform, settings)
    if not cid or not secret:
        raise ValueError(f"Заполните Client ID и Secret для {platform} в настройках публикации.")
    if platform == "youtube":
        return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(
            {
                "client_id": cid,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": YT_SCOPES,
                "access_type": "offline",
                "prompt": "consent",
                "state": state,
            }
        )
    if platform == "instagram":
        return "https://www.instagram.com/oauth/authorize?" + urlencode(
            {
                "client_id": cid,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": IG_SCOPES,
                "state": state,
            }
        )
    return "https://www.tiktok.com/v2/auth/authorize/?" + urlencode(
        {
            "client_key": cid,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": TT_SCOPES,
            "state": state,
        }
    )


async def exchange_oauth_code(
    platform: str,
    code: str,
    settings: AppSettingsModel,
    redirect_uri: str,
) -> tuple[str, dict[str, Any]]:
    """Exchange OAuth2 authorization code for tokens and return (account_name, credentials_json)."""
    cid, secret = _app_keys_for(platform, settings)
    clean_code = code.split("#")[0].strip()
    if not clean_code:
        raise ValueError("Платформа не передала код авторизации.")

    async with httpx.AsyncClient(timeout=30.0) as client:
        if platform == "youtube":
            r = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": clean_code,
                    "client_id": cid,
                    "client_secret": secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            r.raise_for_status()
            tok = r.json()
            ch = await client.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {tok['access_token']}"},
            )
            ch.raise_for_status()
            items = ch.json().get("items") or []
            name = items[0]["snippet"]["title"] if items else "YouTube Shorts"
            creds = {
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", ""),
                "expires_at": time.time() + int(tok.get("expires_in", 3600)) - 60,
            }
            return name, creds

        if platform == "instagram":
            r = await client.post(
                "https://api.instagram.com/oauth/access_token",
                data={
                    "client_id": cid,
                    "client_secret": secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                    "code": clean_code,
                },
            )
            r.raise_for_status()
            short = r.json()
            if isinstance(short.get("data"), list) and short["data"]:
                short = short["data"][0]
            long_r = await client.get(
                f"{IG_API}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": secret,
                    "access_token": short["access_token"],
                },
            )
            long_r.raise_for_status()
            long_tok = long_r.json()["access_token"]
            me = (
                await client.get(
                    f"{IG_API}/me",
                    params={"fields": "user_id,username"},
                    headers={"Authorization": f"Bearer {long_tok}"},
                )
            ).json()
            name = f"@{me.get('username', 'instagram')}"
            creds = {
                "user_id": str(me.get("user_id", "")),
                "access_token": long_tok,
                "token_time": time.time(),
            }
            return name, creds

        if platform == "tiktok":
            r = await client.post(
                f"{TT_API}/oauth/token/",
                data={
                    "client_key": cid,
                    "client_secret": secret,
                    "code": clean_code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            r.raise_for_status()
            tok = r.json()
            if tok.get("error"):
                raise RuntimeError(tok.get("error_description") or str(tok["error"]))
            creds = {
                "open_id": tok.get("open_id", ""),
                "access_token": tok["access_token"],
                "refresh_token": tok.get("refresh_token", ""),
                "expires_at": time.time() + int(tok.get("expires_in", 7200)) - 120,
            }
            return "TikTok Creator", creds

    raise ValueError(f"Неизвестная платформа: {platform}")


def _check_http(r: httpx.Response, label: str) -> httpx.Response:
    if r.status_code >= 400:
        raise RuntimeError(f"{label} ({r.status_code}): {r.text[:260]}")
    return r


def _safe_expires_at(creds: dict[str, Any]) -> float:
    raw = creds.get("expires_at")
    if raw is None or raw == "":
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


async def upload_youtube_short(
    creds: dict[str, Any],
    video_path: Path,
    title: str,
    caption: str,
    tags: list[str],
    privacy: str,
    settings: AppSettingsModel,
    save_creds: Callable[[dict[str, Any]], Awaitable[None]],
) -> tuple[str, str]:
    """Upload MP4 to YouTube Shorts via resumable Data API v3."""
    async with httpx.AsyncClient(timeout=900.0) as client:
        token = str(creds.get("access_token") or "")
        if not token or _safe_expires_at(creds) <= time.time():
            cid, secret = _app_keys_for("youtube", settings)
            refresh = str(creds.get("refresh_token") or "")
            if not (cid and secret and refresh):
                if not token:
                    raise RuntimeError("Не задан access_token / refresh_token для YouTube.")
            else:
                r = _check_http(
                    await client.post(
                        "https://oauth2.googleapis.com/token",
                        data={
                            "client_id": cid,
                            "client_secret": secret,
                            "refresh_token": refresh,
                            "grant_type": "refresh_token",
                        },
                        timeout=30.0,
                    ),
                    "YouTube token refresh",
                )
                tok = r.json()
                token = tok["access_token"]
                creds["access_token"] = token
                creds["expires_at"] = time.time() + int(tok.get("expires_in", 3600)) - 60
                await save_creds(creds)

        clean_title = re.sub(r"[<>]", "", title)[:95] or "Short Clip"
        if "#shorts" not in clean_title.lower():
            clean_title = f"{clean_title[:88]} #Shorts"
        clean_desc = re.sub(r"[<>]", "", caption)[:4900]
        clean_tags = [re.sub(r"[^A-Za-zА-Яа-яЁё0-9_]", "", str(t)) for t in tags if str(t).strip()]
        meta = {
            "snippet": {
                "title": clean_title,
                "description": f"{clean_desc}\n\n#Shorts".strip(),
                "tags": [t for t in clean_tags if t][:15],
                "categoryId": "20",
            },
            "status": {
                "privacyStatus": privacy if privacy in ("public", "unlisted", "private") else "public",
                "selfDeclaredMadeForKids": False,
            },
        }
        size = video_path.stat().st_size
        start = _check_http(
            await client.post(
                "https://www.googleapis.com/upload/youtube/v3/videos",
                params={"uploadType": "resumable", "part": "snippet,status"},
                json=meta,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Upload-Content-Type": "video/mp4",
                    "X-Upload-Content-Length": str(size),
                },
                timeout=60.0,
            ),
            "YouTube upload init",
        )
        upload_url = start.headers["Location"]
        video_bytes = await asyncio.to_thread(video_path.read_bytes)
        done = _check_http(
            await client.put(
                upload_url,
                content=video_bytes,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4"},
            ),
            "YouTube upload PUT",
        ).json()
        vid = str(done["id"])
        return vid, f"https://www.youtube.com/shorts/{vid}"


def _tt_check(r: httpx.Response, label: str) -> dict[str, Any]:
    try:
        data = r.json()
    except ValueError:
        data = {}
    err = data.get("error") if isinstance(data.get("error"), dict) else {}
    if r.status_code >= 400 or (err and err.get("code") not in (None, "ok")) or isinstance(data.get("error"), str):
        code = err.get("code") or data.get("error") or r.status_code
        msg = err.get("message") or data.get("error_description") or r.text[:200]
        exc = RuntimeError(f"{label} ({code}): {msg}")
        setattr(exc, "code", code)
        raise exc
    return data


async def upload_tiktok_video(
    creds: dict[str, Any],
    video_path: Path,
    caption: str,
    privacy: str,
    settings: AppSettingsModel,
    save_creds: Callable[[dict[str, Any]], Awaitable[None]],
) -> tuple[str, str]:
    """Upload MP4 to TikTok Content Posting API v2 with chunked upload and SELF_ONLY fallback."""
    async with httpx.AsyncClient(timeout=900.0) as client:
        token = str(creds.get("access_token") or "")
        if not token or (creds.get("refresh_token") and _safe_expires_at(creds) <= time.time()):
            cid, secret = _app_keys_for("tiktok", settings)
            refresh = str(creds.get("refresh_token") or "")
            if cid and secret and refresh:
                r = await client.post(
                    f"{TT_API}/oauth/token/",
                    data={
                        "client_key": cid,
                        "client_secret": secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30.0,
                )
                tok = _tt_check(r, "TikTok token refresh")
                token = tok["access_token"]
                creds["access_token"] = token
                if tok.get("refresh_token"):
                    creds["refresh_token"] = tok["refresh_token"]
                creds["expires_at"] = time.time() + int(tok.get("expires_in", 7200)) - 120
                await save_creds(creds)

        if not token:
            raise RuntimeError("Не задан access_token для TikTok.")

        async def _tt_post(path_: str, body: dict[str, Any], what: str) -> dict[str, Any]:
            resp = await client.post(
                f"{TT_API}{path_}",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=UTF-8",
                },
                timeout=60.0,
            )
            return _tt_check(resp, what).get("data") or {}

        creator_info = await _tt_post("/post/publish/creator_info/query/", {}, "TikTok creator_info")
        options = creator_info.get("privacy_level_options") or ["SELF_ONLY"]
        desired = TT_PRIVACY_MAP.get(privacy, ["SELF_ONLY"])
        level = next((p for p in desired if p in options), "SELF_ONLY" if "SELF_ONLY" in options else options[0])

        size = video_path.stat().st_size
        if size <= 0:
            raise RuntimeError("Видеофайл пуст (0 байт), загрузка в TikTok невозможна")
        chunk_size = size if size <= 64_000_000 else 10_000_000
        chunk_count = 1 if size <= 64_000_000 else max(1, size // 10_000_000)

        async def _init(lv: str) -> dict[str, Any]:
            return await _tt_post(
                "/post/publish/video/init/",
                {
                    "post_info": {
                        "title": caption[:2200],
                        "privacy_level": lv,
                        "disable_duet": False,
                        "disable_comment": False,
                        "disable_stitch": False,
                        "video_cover_timestamp_ms": 1000,
                    },
                    "source_info": {
                        "source": "FILE_UPLOAD",
                        "video_size": size,
                        "chunk_size": chunk_size,
                        "total_chunk_count": chunk_count,
                    },
                },
                "TikTok publish init",
            )

        try:
            box = await _init(level)
        except RuntimeError as exc:
            if "unaudited" not in str(getattr(exc, "code", "")).lower() or level == "SELF_ONLY" or "SELF_ONLY" not in options:
                raise
            box = await _init("SELF_ONLY")

        raw_bytes = await asyncio.to_thread(video_path.read_bytes)
        for idx in range(chunk_count):
            first = idx * chunk_size
            last = size - 1 if idx == chunk_count - 1 else first + chunk_size - 1
            part = raw_bytes[first : last + 1]
            put_r = await client.put(
                box["upload_url"],
                content=part,
                headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(part)),
                    "Content-Range": f"bytes {first}-{last}/{size}",
                },
            )
            if put_r.status_code not in (200, 201, 206):
                raise RuntimeError(f"TikTok upload chunk failed ({put_r.status_code}): {put_r.text[:200]}")

        publish_id = str(box["publish_id"])
        status_data: dict[str, Any] = {}
        completed = False
        for _ in range(60):
            status_data = await _tt_post(
                "/post/publish/status/fetch/",
                {"publish_id": publish_id},
                "TikTok publish status",
            )
            st = status_data.get("status")
            if st == "PUBLISH_COMPLETE":
                completed = True
                break
            if st == "FAILED":
                raise RuntimeError(f"TikTok отклонил публикацию: {status_data.get('fail_reason') or 'unknown'}")
            await asyncio.sleep(2.0)

        if not completed:
            raise RuntimeError(
                f"Таймаут обработки видео на стороне TikTok (последний статус: {status_data.get('status') or 'unknown'})"
            )

        ids = status_data.get("publicaly_available_post_id") or status_data.get("publicly_available_post_id") or []
        username = creator_info.get("creator_username") or ""
        post_id = str(ids[0]) if ids else publish_id
        url = f"https://www.tiktok.com/@{username}/video/{ids[0]}" if ids and username else ""
        return post_id, url


async def upload_instagram_reel(
    creds: dict[str, Any],
    video_path: Path,
    caption: str,
    settings: AppSettingsModel,
    save_creds: Callable[[dict[str, Any]], Awaitable[None]],
    signed_video_url: Optional[str] = None,
) -> tuple[str, str]:
    """Upload MP4 Reel via Instagram Graph API."""
    token = str(creds.get("access_token") or "")
    uid = str(creds.get("user_id") or creds.get("ig_user_id") or "")
    if not token or not uid:
        raise RuntimeError("Не заданы access_token и user_id для Instagram Reels.")

    async with httpx.AsyncClient(timeout=900.0) as client:
        if time.time() - float(creds.get("token_time", time.time())) > 7 * 86400:
            try:
                ref_r = _check_http(
                    await client.get(
                        f"{IG_API}/refresh_access_token",
                        params={"grant_type": "ig_refresh_token"},
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=30.0,
                    ),
                    "Instagram token refresh",
                ).json()
                token = ref_r["access_token"]
                creds["access_token"] = token
                creds["token_time"] = time.time()
                await save_creds(creds)
            except Exception:
                pass

        auth = {"Authorization": f"Bearer {token}"}
        if signed_video_url and signed_video_url.startswith("https://"):
            box = _check_http(
                await client.post(
                    f"{IG_API}/{uid}/media",
                    data={
                        "media_type": "REELS",
                        "video_url": signed_video_url,
                        "caption": caption[:2200],
                        "share_to_feed": "true",
                    },
                    headers=auth,
                    timeout=60.0,
                ),
                "Instagram Reel init",
            ).json()
        else:
            box = _check_http(
                await client.post(
                    f"{IG_API}/{uid}/media",
                    data={
                        "media_type": "REELS",
                        "upload_type": "resumable",
                        "caption": caption[:2200],
                        "share_to_feed": "true",
                    },
                    headers=auth,
                    timeout=60.0,
                ),
                "Instagram resumable init",
            ).json()
            raw_bytes = await asyncio.to_thread(video_path.read_bytes)
            _check_http(
                await client.post(
                    f"{IG_UPLOAD}/{box['id']}",
                    content=raw_bytes,
                    headers={
                        "Authorization": f"OAuth {token}",
                        "offset": "0",
                        "file_size": str(len(raw_bytes)),
                    },
                ),
                "Instagram Reel upload",
            )

        creation_id = str(box["id"])
        ig_ready = False
        last_ig_code: Any = None
        for _ in range(60):
            st = _check_http(
                await client.get(
                    f"{IG_API}/{creation_id}",
                    params={"fields": "status_code,status"},
                    headers=auth,
                    timeout=30.0,
                ),
                "Instagram processing status",
            ).json()
            code = st.get("status_code")
            last_ig_code = code
            if code in ("FINISHED", "PUBLISHED"):
                ig_ready = True
                break
            if code in ("ERROR", "EXPIRED"):
                raise RuntimeError(f"Instagram отклонил видео: {st.get('status', code)}")
            await asyncio.sleep(2.0)

        if not ig_ready:
            raise RuntimeError(
                f"Таймаут обработки видео на стороне Instagram (последний статус: {last_ig_code or 'unknown'})"
            )

        pub = _check_http(
            await client.post(
                f"{IG_API}/{uid}/media_publish",
                data={"creation_id": creation_id},
                headers=auth,
                timeout=60.0,
            ),
            "Instagram publish",
        ).json()
        pub_id = str(pub["id"])
        link = ""
        with suppress(Exception):
            pr = await client.get(
                f"{IG_API}/{pub_id}",
                params={"fields": "permalink"},
                headers=auth,
                timeout=20.0,
            )
            if pr.status_code == 200:
                link = str(pr.json().get("permalink") or "")
        return pub_id, link


class PublisherService:
    """Async scheduled auto-poster with anti-shadowban gaps, daily caps, retries, and local-only mode."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task[None]] = None
        self._started = False
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._started:
            return
        async with AsyncSessionLocal() as session:
            stuck = (
                await session.execute(select(PublishQueueItem).where(PublishQueueItem.status == "posting"))
            ).scalars().all()
            for row in stuck:
                row.status = "failed"
                row.error = "Загрузка была прервана при закрытии приложения."
                row.updated_at = _utcnow()
            await session.commit()

        self._started = True
        self._task = asyncio.create_task(self._scheduler_loop(), name="narezki-publisher")
        logger.info("PublisherService started")

    async def stop(self) -> None:
        self._started = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def enqueue_clip(
        self,
        clip_id: str,
        account_ids: list[str],
        publish_now: bool = False,
        title: Optional[str] = None,
        caption: Optional[str] = None,
        privacy: Optional[str] = None,
    ) -> list[PublishQueueItemOut]:
        created_items: list[PublishQueueItemOut] = []
        async with self._lock:
            async with AsyncSessionLocal() as session:
                clip = await session.get(Clip, clip_id)
                if clip is None:
                    raise ValueError(f"Клип {clip_id} не найден")
                video_path = clip.export_path or clip.preview_path or ""
                if not video_path or not Path(video_path).exists():
                    raise ValueError("Сначала отрендерите или экспортируйте клип в MP4.")
                settings = await load_settings(session)

                tags = [str(t).lstrip("#").strip() for t in (clip.tags_json or []) if str(t).strip()]
                tags_line = " ".join(f"#{t}" for t in tags)
                built_caption = (
                    caption
                    if caption is not None
                    else "\n\n".join(x for x in (clip.description.strip(), tags_line) if x)
                )
                eff_title = (title or clip.title or clip.hook_text or "Clip").strip()

                for aid in account_ids:
                    acc = await session.get(SocialAccount, aid)
                    if acc is None or not acc.enabled:
                        continue

                    existing_active = (
                        await session.execute(
                            select(PublishQueueItem).where(
                                PublishQueueItem.clip_id == clip.id,
                                PublishQueueItem.account_id == acc.id,
                                PublishQueueItem.status.in_(["scheduled", "posting", "posted"]),
                            )
                        )
                    ).scalars().first()
                    if existing_active is not None:
                        continue

                    if publish_now:
                        due = _utcnow()
                    else:
                        hist = (
                            await session.execute(
                                select(PublishQueueItem).where(
                                    PublishQueueItem.account_id == acc.id,
                                    PublishQueueItem.status.in_(["scheduled", "posting", "posted"]),
                                )
                            )
                        ).scalars().all()
                        hist_times = [h.posted_at or h.scheduled_for for h in hist]
                        gap_min = acc.post_gap_minutes or settings.publish_default_gap_minutes
                        cap = acc.daily_cap or settings.publish_default_daily_cap
                        due = compute_next_slot(hist_times, gap_minutes=gap_min, daily_cap=cap)

                    item = PublishQueueItem(
                        clip_id=clip.id,
                        project_id=clip.project_id,
                        account_id=acc.id,
                        platform=acc.platform,
                        account_name=acc.name,
                        title=eff_title[:250],
                        caption=built_caption,
                        tags_json=tags,
                        privacy=privacy or acc.default_privacy or "public",
                        video_path=video_path,
                        status="scheduled",
                        scheduled_for=due,
                    )
                    session.add(item)
                    await session.flush()
                    await session.refresh(item)
                    created_items.append(PublishQueueItemOut.model_validate(item))

                await session.commit()

        for out in created_items:
            await ws_manager.emit_publish_update(out.model_dump(mode="json"))
        return created_items

    async def process_due_once(self) -> int:
        """Process any scheduled publish items whose scheduled_for <= now. Returns count processed."""
        processed = 0
        async with AsyncSessionLocal() as session:
            settings = await load_settings(session)
            if settings.publish_paused or settings.publish_mode == "local_only":
                return 0
            now = _utcnow()
            due_rows = (
                await session.execute(
                    select(PublishQueueItem)
                    .where(
                        PublishQueueItem.status == "scheduled",
                        PublishQueueItem.scheduled_for <= now,
                    )
                    .order_by(PublishQueueItem.scheduled_for.asc())
                )
            ).scalars().all()
            due_ids = [r.id for r in due_rows]

        for item_id in due_ids:
            await self._publish_one(item_id)
            processed += 1
        return processed

    async def _publish_one(self, item_id: str) -> None:
        async with AsyncSessionLocal() as session:
            item = await session.get(PublishQueueItem, item_id)
            if item is None or item.status != "scheduled":
                return
            acc = await session.get(SocialAccount, item.account_id)
            settings = await load_settings(session)
            item.status = "posting"
            item.updated_at = _utcnow()
            await session.commit()
            await session.refresh(item)
            await ws_manager.emit_publish_update(PublishQueueItemOut.model_validate(item).model_dump(mode="json"))

        try:
            if acc is None or not acc.enabled:
                raise RuntimeError("Аккаунт публикации отключён или удалён.")
            vpath = Path(item.video_path)
            if not vpath.exists():
                raise RuntimeError(f"Файл видео не найден: {vpath}")
            creds = dict(acc.credentials_json or {})
            if acc.auth_type == "cookies" and not creds.get("access_token"):
                raise RuntimeError(
                    "Для выбранного cookie-аккаунта не указан access_token; используйте OAuth2/токен либо режим «Только локальный экспорт»."
                )

            async def _save_creds(next_creds: dict[str, Any]) -> None:
                async with AsyncSessionLocal() as s2:
                    a2 = await s2.get(SocialAccount, acc.id)
                    if a2 is not None:
                        a2.credentials_json = next_creds
                        a2.updated_at = _utcnow()
                        await s2.commit()

            if acc.platform == "youtube":
                remote_id, remote_url = await upload_youtube_short(
                    creds=creds,
                    video_path=vpath,
                    title=item.title,
                    caption=item.caption,
                    tags=list(item.tags_json or []),
                    privacy=item.privacy,
                    settings=settings,
                    save_creds=_save_creds,
                )
            elif acc.platform == "tiktok":
                remote_id, remote_url = await upload_tiktok_video(
                    creds=creds,
                    video_path=vpath,
                    caption=f"{item.title}\n\n{item.caption}".strip(),
                    privacy=item.privacy,
                    settings=settings,
                    save_creds=_save_creds,
                )
            elif acc.platform == "instagram":
                pub_base = settings.publish_public_base_url.rstrip("/")
                signed_url = (
                    f"{pub_base}/api/publishing/public/{create_signed_clip_token(item.clip_id)}.mp4"
                    if pub_base.startswith("https://")
                    else None
                )
                remote_id, remote_url = await upload_instagram_reel(
                    creds=creds,
                    video_path=vpath,
                    caption=f"{item.title}\n\n{item.caption}".strip(),
                    settings=settings,
                    save_creds=_save_creds,
                    signed_video_url=signed_url,
                )
            else:
                raise RuntimeError(f"Неподдерживаемая платформа: {acc.platform}")

            async with AsyncSessionLocal() as session:
                row = await session.get(PublishQueueItem, item_id)
                if row is not None and row.status != "cancelled":
                    row.status = "posted"
                    row.posted_at = _utcnow()
                    row.remote_id = remote_id
                    row.remote_url = remote_url
                    row.error = None
                    row.updated_at = _utcnow()
                    await session.commit()
                    await session.refresh(row)
                    out = PublishQueueItemOut.model_validate(row).model_dump(mode="json")
                    await ws_manager.emit_publish_update(out)
                    await ws_manager.log(
                        f"Опубликовано в {row.platform} ({row.account_name}): {row.title}",
                        stage="publish",
                    )
        except Exception as exc:
            err_msg = str(exc) or exc.__class__.__name__
            async with AsyncSessionLocal() as session:
                row = await session.get(PublishQueueItem, item_id)
                if row is not None and row.status != "cancelled":
                    row.tries += 1
                    row.error = err_msg[:300]
                    if row.tries < 3:
                        row.status = "scheduled"
                        row.scheduled_for = _utcnow() + timedelta(minutes=10)
                    else:
                        row.status = "failed"
                    row.updated_at = _utcnow()
                    await session.commit()
                    await session.refresh(row)
                    out = PublishQueueItemOut.model_validate(row).model_dump(mode="json")
                    await ws_manager.emit_publish_update(out)
                    await ws_manager.log(
                        f"Ошибка публикации ({row.platform}): {err_msg}",
                        level="error",
                        stage="publish",
                    )

    async def _scheduler_loop(self) -> None:
        while self._started:
            try:
                await asyncio.sleep(15.0)
                await self.process_due_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Publisher loop error: %s", exc)


publisher_service = PublisherService()
