from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Optional

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import ADS_DIR, BACKGROUNDS_DIR, ensure_data_dirs
from ..core.database import get_db
from ..core.models import AdPreset, BackgroundAsset, SubtitlePreset, new_id
from ..core.schemas import (
    AdPresetCreate,
    AdPresetOut,
    AdPresetUpdate,
    BackgroundAssetCreate,
    BackgroundAssetOut,
    BackgroundFolderScanRequest,
    SubtitlePresetCreate,
    SubtitlePresetOut,
    SubtitlePresetUpdate,
)

router = APIRouter(prefix="/api/presets", tags=["presets"])

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
OVERLAY_EXTENSIONS = {".png", ".webp", ".gif", ".webm", ".jpg", ".jpeg"}
MAX_AD_UPLOAD_BYTES = 500 * 1024 * 1024


# --- Subtitle Presets ---


@router.get("/subtitles", response_model=list[SubtitlePresetOut])
async def list_subtitle_presets(db: AsyncSession = Depends(get_db)) -> list[SubtitlePresetOut]:
    rows = (
        await db.execute(select(SubtitlePreset).order_by(SubtitlePreset.is_default.desc(), SubtitlePreset.created_at.asc()))
    ).scalars().all()
    return [SubtitlePresetOut.model_validate(r) for r in rows]


@router.post("/subtitles", response_model=SubtitlePresetOut)
async def create_subtitle_preset(
    body: SubtitlePresetCreate,
    db: AsyncSession = Depends(get_db),
) -> SubtitlePresetOut:
    if body.is_default:
        existing = (await db.execute(select(SubtitlePreset))).scalars().all()
        for row in existing:
            row.is_default = False
    preset = SubtitlePreset(**body.model_dump())
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return SubtitlePresetOut.model_validate(preset)


@router.patch("/subtitles/{preset_id}", response_model=SubtitlePresetOut)
async def update_subtitle_preset(
    preset_id: str,
    body: SubtitlePresetUpdate,
    db: AsyncSession = Depends(get_db),
) -> SubtitlePresetOut:
    preset = await db.get(SubtitlePreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Пресет субтитров не найден")

    updates = body.model_dump(exclude_unset=True)
    if updates.get("is_default"):
        all_rows = (await db.execute(select(SubtitlePreset))).scalars().all()
        for row in all_rows:
            row.is_default = False

    for k, v in updates.items():
        setattr(preset, k, v)
    await db.commit()
    await db.refresh(preset)
    return SubtitlePresetOut.model_validate(preset)


@router.delete("/subtitles/{preset_id}")
async def delete_subtitle_preset(preset_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    preset = await db.get(SubtitlePreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Пресет субтитров не найден")
    was_default = bool(preset.is_default)
    await db.delete(preset)
    await db.flush()
    if was_default:
        fallback = (
            await db.execute(select(SubtitlePreset).order_by(SubtitlePreset.created_at.asc()))
        ).scalars().first()
        if fallback is not None:
            fallback.is_default = True
    await db.commit()
    return {"ok": True}


# --- Ad Presets ---


async def _probe_duration_if_video(path: Path) -> float:
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return 0.0
    try:
        from ..services.ingest.ffprobe_utils import probe_media

        info = await probe_media(path)
        return float(info.get("duration") or 0.0)
    except Exception:
        return 0.0


@router.get("/ads", response_model=list[AdPresetOut])
async def list_ad_presets(db: AsyncSession = Depends(get_db)) -> list[AdPresetOut]:
    rows = (await db.execute(select(AdPreset).order_by(AdPreset.created_at.desc()))).scalars().all()
    return [AdPresetOut.model_validate(r) for r in rows]


@router.post("/ads", response_model=AdPresetOut)
async def create_ad_preset(
    body: AdPresetCreate,
    db: AsyncSession = Depends(get_db),
) -> AdPresetOut:
    src = Path(body.asset_path)
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=400, detail=f"Файл рекламы не найден: {body.asset_path}")

    ensure_data_dirs()
    preset_id = new_id()
    dest = ADS_DIR / f"{preset_id}_{src.name}"
    if src.resolve() != dest.resolve():
        await asyncio.to_thread(shutil.copy2, src, dest)

    duration = await _probe_duration_if_video(dest) if body.ad_type == "video_insert" else 0.0
    data = body.model_dump()
    data["asset_path"] = str(dest)
    preset = AdPreset(id=preset_id, duration=duration, **data)
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return AdPresetOut.model_validate(preset)


@router.post("/ads/upload", response_model=AdPresetOut)
async def upload_ad_preset(
    name: str = Form(...),
    ad_type: str = Form(...),
    insert_position: str = Form("outro"),
    insert_at_sec: Optional[float] = Form(None),
    overlay_x: float = Form(50.0),
    overlay_y: float = Form(22.0),
    overlay_scale: float = Form(35.0),
    overlay_start: float = Form(3.0),
    overlay_end: float = Form(12.0),
    fade_duration: float = Form(0.4),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> AdPresetOut:
    if ad_type not in ("video_insert", "overlay"):
        raise HTTPException(status_code=400, detail="ad_type должен быть 'video_insert' или 'overlay'")

    safe_name = Path(file.filename or "ad_asset.bin").name
    ext = Path(safe_name).suffix.lower()
    allowed_exts = VIDEO_EXTENSIONS if ad_type == "video_insert" else OVERLAY_EXTENSIONS
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"Неподдерживаемое расширение файла для {ad_type}: {ext or 'без расширения'}",
        )

    if not (0.0 <= overlay_x <= 100.0) or not (0.0 <= overlay_y <= 100.0):
        raise HTTPException(status_code=400, detail="overlay_x и overlay_y должны быть от 0 до 100")
    if not (5.0 <= overlay_scale <= 95.0):
        raise HTTPException(status_code=400, detail="overlay_scale должен быть от 5 до 95")
    if overlay_start < 0.0 or overlay_end < 0.0:
        raise HTTPException(status_code=400, detail="overlay_start и overlay_end должны быть >= 0")
    if not (0.0 <= fade_duration <= 3.0):
        raise HTTPException(status_code=400, detail="fade_duration должен быть от 0 до 3")
    if insert_at_sec is not None and insert_at_sec < 0.0:
        raise HTTPException(status_code=400, detail="insert_at_sec должен быть >= 0")

    ensure_data_dirs()
    preset_id = new_id()
    dest = ADS_DIR / f"{preset_id}_{safe_name}"
    total_bytes = 0
    try:
        async with aiofiles.open(dest, "wb") as out:
            while chunk := await file.read(1024 * 512):
                total_bytes += len(chunk)
                if total_bytes > MAX_AD_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Размер файла рекламы превышает лимит 500 МБ",
                    )
                await out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    duration = await _probe_duration_if_video(dest) if ad_type == "video_insert" else 0.0
    preset = AdPreset(
        id=preset_id,
        name=name,
        ad_type=ad_type,
        asset_path=str(dest),
        insert_position=insert_position if insert_position in ("mid_roll", "outro") else "outro",
        insert_at_sec=insert_at_sec,
        overlay_x=overlay_x,
        overlay_y=overlay_y,
        overlay_scale=overlay_scale,
        overlay_start=overlay_start,
        overlay_end=overlay_end,
        fade_duration=fade_duration,
        duration=duration,
    )
    db.add(preset)
    await db.commit()
    await db.refresh(preset)
    return AdPresetOut.model_validate(preset)


@router.patch("/ads/{preset_id}", response_model=AdPresetOut)
async def update_ad_preset(
    preset_id: str,
    body: AdPresetUpdate,
    db: AsyncSession = Depends(get_db),
) -> AdPresetOut:
    preset = await db.get(AdPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Рекламный пресет не найден")

    updates = body.model_dump(exclude_unset=True)
    if "asset_path" in updates and updates["asset_path"]:
        src = Path(updates["asset_path"])
        if not src.exists():
            raise HTTPException(status_code=400, detail="Указанный файл рекламы не найден")
        if src.resolve() != Path(preset.asset_path).resolve():
            ensure_data_dirs()
            dest = ADS_DIR / f"{preset.id}_{src.name}"
            await asyncio.to_thread(shutil.copy2, src, dest)
            updates["asset_path"] = str(dest)
            if (updates.get("ad_type") or preset.ad_type) == "video_insert":
                updates["duration"] = await _probe_duration_if_video(dest)

    for k, v in updates.items():
        setattr(preset, k, v)
    await db.commit()
    await db.refresh(preset)
    return AdPresetOut.model_validate(preset)


@router.delete("/ads/{preset_id}")
async def delete_ad_preset(preset_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    preset = await db.get(AdPreset, preset_id)
    if preset is None:
        raise HTTPException(status_code=404, detail="Рекламный пресет не найден")
    asset = Path(preset.asset_path)
    await db.delete(preset)
    await db.commit()
    if asset.exists() and ADS_DIR.resolve() in asset.resolve().parents:
        asset.unlink(missing_ok=True)
    return {"ok": True}


# --- Split-Screen Backgrounds ---


async def _register_background_file(db: AsyncSession, src: Path, name: Optional[str] = None) -> BackgroundAsset:
    ensure_data_dirs()
    asset_id = new_id()
    duration = 0.0
    width = 0
    height = 0
    thumb_path: Optional[str] = None
    try:
        from ..services.ingest.ffprobe_utils import probe_media
        from ..services.ingest.proxy import generate_thumbnail

        info = await probe_media(src)
        duration = float(info.get("duration") or 0.0)
        width = int(info.get("width") or 0)
        height = int(info.get("height") or 0)
        thumb = BACKGROUNDS_DIR / f"{asset_id}_thumb.jpg"
        await generate_thumbnail(src, thumb)
        if thumb.exists():
            thumb_path = str(thumb)
    except Exception:
        pass

    asset = BackgroundAsset(
        id=asset_id,
        name=name or src.stem,
        file_path=str(src.resolve()),
        duration=duration,
        width=width,
        height=height,
        thumbnail_path=thumb_path,
    )
    db.add(asset)
    return asset


@router.get("/backgrounds", response_model=list[BackgroundAssetOut])
async def list_backgrounds(db: AsyncSession = Depends(get_db)) -> list[BackgroundAssetOut]:
    rows = (
        await db.execute(select(BackgroundAsset).order_by(BackgroundAsset.created_at.desc()))
    ).scalars().all()
    return [BackgroundAssetOut.model_validate(r) for r in rows]


@router.post("/backgrounds", response_model=BackgroundAssetOut)
async def add_background(
    body: BackgroundAssetCreate,
    db: AsyncSession = Depends(get_db),
) -> BackgroundAssetOut:
    src = Path(body.file_path)
    if not src.exists() or not src.is_file():
        raise HTTPException(status_code=400, detail=f"Файл фона не найден: {body.file_path}")
    asset = await _register_background_file(db, src, name=body.name)
    await db.commit()
    await db.refresh(asset)
    return BackgroundAssetOut.model_validate(asset)


@router.post("/backgrounds/scan-folder", response_model=list[BackgroundAssetOut])
async def scan_background_folder(
    body: BackgroundFolderScanRequest,
    db: AsyncSession = Depends(get_db),
) -> list[BackgroundAssetOut]:
    folder = Path(body.folder_path)
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"Папка не найдена: {body.folder_path}")

    existing_paths = {
        row[0] for row in (await db.execute(select(BackgroundAsset.file_path))).all()
    }

    added: list[BackgroundAsset] = []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        resolved = str(p.resolve())
        if resolved in existing_paths:
            continue
        asset = await _register_background_file(db, p)
        added.append(asset)

    await db.commit()
    for a in added:
        await db.refresh(a)
    return [BackgroundAssetOut.model_validate(a) for a in added]


@router.delete("/backgrounds/{asset_id}")
async def delete_background(asset_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, bool]:
    asset = await db.get(BackgroundAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="Фон не найден")
    thumb = Path(asset.thumbnail_path) if asset.thumbnail_path else None
    await db.delete(asset)
    await db.commit()
    if thumb and thumb.exists() and BACKGROUNDS_DIR.resolve() in thumb.resolve().parents:
        thumb.unlink(missing_ok=True)
    return {"ok": True}
