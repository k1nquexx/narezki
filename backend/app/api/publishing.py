from __future__ import annotations

import html as html_lib
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import is_safe_media_path
from ..core.database import get_db, load_settings
from ..core.models import PublishQueueItem, SocialAccount
from ..core.schemas import (
    PublishEnqueueRequest,
    PublishQueueItemOut,
    SocialAccountCreate,
    SocialAccountOut,
    SocialAccountUpdate,
)
from ..core.ws_manager import ws_manager
from ..services.live.publisher import (
    build_oauth_url,
    exchange_oauth_code,
    publisher_service,
    verify_signed_clip_token,
)

router = APIRouter(prefix="/api/publishing", tags=["publishing"])

_oauth_states: dict[str, tuple[str, float]] = {}
_VALID_PLATFORMS = {"youtube", "tiktok", "instagram"}


def _prune_states() -> None:
    now = time.time()
    for key in [k for k, (_p, exp) in _oauth_states.items() if exp < now]:
        _oauth_states.pop(key, None)


def _serialize_account(acc: SocialAccount) -> SocialAccountOut:
    creds = acc.credentials_json if isinstance(acc.credentials_json, dict) else {}
    has_creds = any(str(v).strip() for v in creds.values())
    return SocialAccountOut(
        id=acc.id,
        platform=acc.platform,
        name=acc.name,
        auth_type=acc.auth_type,
        has_credentials=has_creds,
        default_privacy=acc.default_privacy,
        post_gap_minutes=acc.post_gap_minutes,
        daily_cap=acc.daily_cap,
        enabled=acc.enabled,
        detail=acc.detail,
        created_at=acc.created_at,
        updated_at=acc.updated_at,
    )


@router.get("/accounts", response_model=list[SocialAccountOut])
async def list_social_accounts(db: AsyncSession = Depends(get_db)) -> list[SocialAccountOut]:
    rows = (
        await db.execute(select(SocialAccount).order_by(SocialAccount.created_at.desc()))
    ).scalars().all()
    return [_serialize_account(r) for r in rows]


@router.post("/accounts", response_model=SocialAccountOut)
async def create_social_account(
    body: SocialAccountCreate,
    db: AsyncSession = Depends(get_db),
) -> SocialAccountOut:
    creds = dict(body.credentials_json or {})
    detail = str(creds.get("username") or body.auth_type)
    acc = SocialAccount(
        platform=body.platform,
        name=body.name.strip()[:120] or f"{body.platform} account",
        auth_type=body.auth_type,
        credentials_json=creds,
        default_privacy=body.default_privacy,
        post_gap_minutes=body.post_gap_minutes,
        daily_cap=body.daily_cap,
        enabled=body.enabled,
        detail=detail[:250],
    )
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return _serialize_account(acc)


@router.patch("/accounts/{account_id}", response_model=SocialAccountOut)
async def update_social_account(
    account_id: str,
    body: SocialAccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> SocialAccountOut:
    acc = await db.get(SocialAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")

    updates = body.model_dump(exclude_unset=True)
    if "credentials_json" in updates and updates["credentials_json"] is not None:
        merged = dict(acc.credentials_json or {})
        merged.update(updates.pop("credentials_json"))
        acc.credentials_json = merged
    for key, val in updates.items():
        setattr(acc, key, val)

    await db.commit()
    await db.refresh(acc)
    return _serialize_account(acc)


@router.delete("/accounts/{account_id}")
async def delete_social_account(account_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    acc = await db.get(SocialAccount, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    await db.delete(acc)
    await db.commit()
    return {"ok": True}


def _resolve_oauth_redirect_uri(platform: str, request: Request, public_base_url: str) -> str:
    """Resolve OAuth2 redirect_uri preferring configured public_base_url over ephemeral local request URL."""
    base = (public_base_url or "").strip().rstrip("/")
    if base and base != "http://127.0.0.1:8765":
        return f"{base}/api/publishing/oauth/{platform}/callback"
    return str(request.base_url).rstrip("/") + f"/api/publishing/oauth/{platform}/callback"


@router.get("/oauth/{platform}/url")
async def get_oauth_authorize_url(
    platform: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, str]:
    plat = platform.lower().strip()
    if plat not in _VALID_PLATFORMS:
        raise HTTPException(status_code=400, detail="Поддерживаются платформы: youtube, tiktok, instagram")
    settings = await load_settings(db)
    redirect_uri = _resolve_oauth_redirect_uri(plat, request, settings.publish_public_base_url)
    _prune_states()
    state = secrets.token_urlsafe(24)
    _oauth_states[state] = (plat, time.time() + 600.0)
    try:
        url = build_oauth_url(plat, settings, redirect_uri, state)
    except ValueError as exc:
        _oauth_states.pop(state, None)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"url": url, "redirect_uri": redirect_uri}


@router.get("/oauth/{platform}/callback", response_class=HTMLResponse)
async def oauth_callback(
    platform: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    plat = platform.lower().strip()
    _prune_states()
    if error:
        safe_err = html_lib.escape(error)
        return HTMLResponse(
            f"<html><body style='background:#09090b;color:#fca5a5;font-family:sans-serif;padding:32px'>"
            f"<h2>Ошибка авторизации {html_lib.escape(plat)}</h2><p>{safe_err}</p></body></html>",
            status_code=400,
        )

    expected = _oauth_states.pop(state or "", None)
    if not expected or expected[0] != plat or not code:
        return HTMLResponse(
            "<html><body style='background:#09090b;color:#fca5a5;font-family:sans-serif;padding:32px'>"
            "<h2>Недействительный или просроченный OAuth state</h2></body></html>",
            status_code=400,
        )

    settings = await load_settings(db)
    redirect_uri = _resolve_oauth_redirect_uri(plat, request, settings.publish_public_base_url)
    try:
        account_name, creds = await exchange_oauth_code(plat, code, settings, redirect_uri)
        existing = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.platform == plat,
                    SocialAccount.name == account_name,
                )
            )
        ).scalars().first()
        if existing is not None:
            existing.auth_type = "oauth"
            existing.credentials_json = creds
            existing.enabled = True
            existing.detail = "OAuth2 обновлён"
        else:
            acc = SocialAccount(
                platform=plat,
                name=account_name[:120],
                auth_type="oauth",
                credentials_json=creds,
                default_privacy="public",
                post_gap_minutes=settings.publish_default_gap_minutes,
                daily_cap=settings.publish_default_daily_cap,
                enabled=True,
                detail="OAuth2 подключён",
            )
            db.add(acc)
        await db.commit()
    except Exception as exc:
        safe_err = html_lib.escape(str(exc))
        return HTMLResponse(
            f"<html><body style='background:#09090b;color:#fca5a5;font-family:sans-serif;padding:32px'>"
            f"<h2>Не удалось завершить OAuth2</h2><p>{safe_err}</p></body></html>",
            status_code=400,
        )

    return HTMLResponse(
        f"<html><body style='background:#09090b;color:#86efac;font-family:sans-serif;padding:32px'>"
        f"<h2>Аккаунт {html_lib.escape(plat)} подключён!</h2>"
        f"<p>Можете закрыть эту вкладку браузера и вернуться в Narezki.</p></body></html>"
    )


@router.get("/queue", response_model=list[PublishQueueItemOut])
async def list_publish_queue(
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
) -> list[PublishQueueItemOut]:
    stmt = select(PublishQueueItem).order_by(PublishQueueItem.scheduled_for.asc()).limit(limit)
    if status:
        stmt = (
            select(PublishQueueItem)
            .where(PublishQueueItem.status == status)
            .order_by(PublishQueueItem.scheduled_for.asc())
            .limit(limit)
        )
    rows = (await db.execute(stmt)).scalars().all()
    return [PublishQueueItemOut.model_validate(r) for r in rows]


@router.post("/enqueue", response_model=list[PublishQueueItemOut])
async def enqueue_publish(body: PublishEnqueueRequest) -> list[PublishQueueItemOut]:
    if not body.account_ids:
        raise HTTPException(status_code=400, detail="Выберите хотя бы один аккаунт для публикации")
    try:
        return await publisher_service.enqueue_clip(
            clip_id=body.clip_id,
            account_ids=body.account_ids,
            publish_now=body.publish_now,
            title=body.title,
            caption=body.caption,
            privacy=body.privacy,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/queue/{item_id}/cancel", response_model=PublishQueueItemOut)
async def cancel_publish_item(item_id: str, db: AsyncSession = Depends(get_db)) -> PublishQueueItemOut:
    item = await db.get(PublishQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Элемент очереди не найден")
    if item.status == "posting":
        raise HTTPException(status_code=409, detail="Нельзя отменить публикацию во время активной загрузки")
    item.status = "cancelled"
    item.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    out = PublishQueueItemOut.model_validate(item)
    await ws_manager.emit_publish_update(out.model_dump(mode="json"))
    return out


@router.post("/queue/{item_id}/retry", response_model=PublishQueueItemOut)
async def retry_publish_item(item_id: str, db: AsyncSession = Depends(get_db)) -> PublishQueueItemOut:
    item = await db.get(PublishQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Элемент очереди не найден")
    item.status = "scheduled"
    item.tries = 0
    item.error = None
    item.scheduled_for = datetime.now(timezone.utc)
    item.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(item)
    out = PublishQueueItemOut.model_validate(item)
    await ws_manager.emit_publish_update(out.model_dump(mode="json"))
    return out


@router.delete("/queue/{item_id}")
async def delete_publish_item(item_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    item = await db.get(PublishQueueItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Элемент очереди не найден")
    await db.delete(item)
    await db.commit()
    return {"ok": True}


@router.get("/public/{token}.mp4")
async def stream_signed_clip(token: str, db: AsyncSession = Depends(get_db)) -> FileResponse:
    from ..core.models import Clip

    clip_id = verify_signed_clip_token(token)
    if not clip_id:
        raise HTTPException(status_code=404, detail="Ссылка недействительна или истекла")

    clip = await db.get(Clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Клип не найден")

    raw_path = clip.export_path or clip.preview_path or ""
    if not raw_path:
        raise HTTPException(status_code=404, detail="Файл клипа отсутствует")

    settings = await load_settings(db)
    target = Path(raw_path)
    extra = [Path(settings.export_dir)] if settings.export_dir else []
    if not is_safe_media_path(target, allowed_extra_paths=extra) or not target.is_file():
        raise HTTPException(status_code=404, detail="Файл недоступен")

    return FileResponse(target, media_type="video/mp4")
