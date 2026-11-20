from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent
BIN_DIR = BACKEND_DIR / "bin"
DATA_DIR = Path(os.environ.get("NAREZKI_DATA_DIR") or (BACKEND_DIR / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
ADS_DIR = DATA_DIR / "ads"
BACKGROUNDS_DIR = DATA_DIR / "backgrounds"
EXPORTS_DIR = DATA_DIR / "exports"
LIVE_BUFFER_DIR = DATA_DIR / "live_buffers"
MODELS_DIR = Path(os.environ.get("NAREZKI_MODELS_DIR") or (DATA_DIR / "models"))
LOGS_DIR = DATA_DIR / "logs"
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "narezki.db"

ALLOWED_MEDIA_DIRS = (PROJECTS_DIR, ADS_DIR, BACKGROUNDS_DIR, EXPORTS_DIR, LIVE_BUFFER_DIR)
BLOCKED_MEDIA_SUFFIXES = {
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".env",
    ".key",
    ".pem",
}


def ensure_data_dirs() -> None:
    """Create all runtime directories under backend/data."""
    for d in (
        DATA_DIR,
        PROJECTS_DIR,
        ADS_DIR,
        BACKGROUNDS_DIR,
        EXPORTS_DIR,
        LIVE_BUFFER_DIR,
        MODELS_DIR,
        LOGS_DIR,
        TEMP_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


def _resolve_binary(name: str) -> str:
    """Resolve a bundled platform-specific binary or fall back to system PATH."""
    if sys.platform == "win32":
        local = BIN_DIR / f"{name}.exe"
        if local.exists() and local.is_file():
            return str(local)
    else:
        local_unix = BIN_DIR / name
        if local_unix.exists() and local_unix.is_file() and os.access(local_unix, os.X_OK):
            return str(local_unix)

    found = shutil.which(name)
    if found:
        return found
    expected = f"{name}.exe" if sys.platform == "win32" else name
    raise FileNotFoundError(
        f"{name} binary not found in {BIN_DIR} or PATH. Ensure {expected} is installed or present in backend/bin."
    )


def get_ffmpeg_path() -> str:
    """Return path to bundled ffmpeg or system ffmpeg for the current OS."""
    return _resolve_binary("ffmpeg")


def get_ffprobe_path() -> str:
    """Return path to bundled ffprobe or system ffprobe for the current OS."""
    return _resolve_binary("ffprobe")


class AppSettingsModel(BaseModel):
    # Cloud LLM API (OpenAI-compatible)
    llm_base_url: str = Field(default="https://openrouter.ai/api/v1")
    llm_api_key: str = Field(default="")
    llm_model: str = Field(default="deepseek/deepseek-chat")
    llm_temperature: float = Field(default=0.3, ge=0.0, le=1.5)
    llm_max_highlights: int = Field(default=10, ge=1, le=30)
    llm_chunk_minutes: int = Field(default=10, ge=2, le=30)

    # Whisper GPU
    whisper_model_size: Literal["large-v3-turbo", "medium", "small", "base", "large-v3"] = Field(
        default="large-v3-turbo"
    )
    whisper_compute_type: Literal["float16", "int8_float16", "int8"] = Field(default="float16")
    whisper_device: Literal["cuda", "cpu"] = Field(default="cuda")
    whisper_language: str = Field(default="auto")

    # Video Renderer & Hardware Encoding
    preferred_encoder: Literal[
        "auto", "h264_nvenc", "hevc_nvenc", "h264_amf", "h264_qsv", "libx264"
    ] = Field(default="auto")
    target_fps: int = Field(default=30, ge=24, le=60)
    crf_or_cq: int = Field(default=21, ge=12, le=35)
    max_render_workers: int = Field(default=2, ge=1, le=4)
    export_dir: str = Field(default=str(EXPORTS_DIR))

    # Smart Reframing Tracker (YOLOv8)
    yolo_model_path: str = Field(default="yolov8n.pt")
    tracker_sample_fps: float = Field(default=6.0, ge=1.0, le=30.0)
    smoothing_method: Literal["kalman", "ema"] = Field(default="kalman")
    smoothing_strength: float = Field(default=0.65, ge=0.1, le=0.95)

    # Anti-Content ID Defaults
    default_anti_cid_enabled: bool = Field(default=False)
    default_anti_cid_zoom: float = Field(default=1.03, ge=1.0, le=1.15)
    default_anti_cid_speed: float = Field(default=1.015, ge=0.95, le=1.10)
    default_anti_cid_brightness: float = Field(default=0.01, ge=-0.1, le=0.1)
    default_anti_cid_contrast: float = Field(default=1.02, ge=0.9, le=1.2)
    default_anti_cid_saturation: float = Field(default=1.03, ge=0.85, le=1.25)
    default_anti_cid_strip_metadata: bool = Field(default=True)

    # Live Monitor & Auto-Posting
    live_check_interval_sec: int = Field(default=60, ge=15, le=600)
    live_default_buffer_minutes: int = Field(default=20, ge=5, le=60)
    live_default_ai_mode: Literal["zero_ai", "llm", "auto"] = Field(default="zero_ai")
    live_auto_publish: bool = Field(default=False)

    publish_mode: Literal["local_only", "auto_publish"] = Field(default="local_only")
    publish_paused: bool = Field(default=False)
    publish_default_gap_minutes: int = Field(default=120, ge=5, le=1440)
    publish_default_daily_cap: int = Field(default=6, ge=1, le=50)
    publish_public_base_url: str = Field(default="http://127.0.0.1:8765")
    youtube_client_id: str = Field(default="")
    youtube_client_secret: str = Field(default="")
    tiktok_client_key: str = Field(default="")
    tiktok_client_secret: str = Field(default="")
    instagram_app_id: str = Field(default="")
    instagram_app_secret: str = Field(default="")


def get_project_dir(project_id: str) -> Path:
    d = PROJECTS_DIR / project_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def is_safe_media_path(target: Path, allowed_extra_paths: Optional[list[Path]] = None) -> bool:
    """Validate that a requested file path lives inside safe media directories or a registered asset path."""
    try:
        resolved = target.resolve()
    except OSError:
        return False

    lower_name = resolved.name.lower()
    if resolved.suffix.lower() in BLOCKED_MEDIA_SUFFIXES or lower_name.endswith((".db-wal", ".db-shm")):
        return False

    try:
        logs_resolved = LOGS_DIR.resolve()
        if resolved == logs_resolved or logs_resolved in resolved.parents:
            return False
        if resolved == DB_PATH.resolve():
            return False
    except OSError:
        return False

    for allowed_dir in ALLOWED_MEDIA_DIRS:
        try:
            dir_resolved = allowed_dir.resolve()
            if resolved == dir_resolved or dir_resolved in resolved.parents:
                return True
        except OSError:
            continue

    if allowed_extra_paths:
        for allowed in allowed_extra_paths:
            try:
                allowed_resolved = allowed.resolve()
                if resolved == allowed_resolved or allowed_resolved in resolved.parents:
                    return True
            except OSError:
                continue
    return False
