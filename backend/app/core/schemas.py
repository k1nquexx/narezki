from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class SubtitleCue(BaseModel):
    id: str
    start: float
    end: float
    text: str
    words: list[WordTiming] = Field(default_factory=list)


class TrackPoint(BaseModel):
    t: float
    cx: float = Field(ge=0.0, le=1.0)
    cy: float = Field(ge=0.0, le=1.0)
    conf: float = Field(default=1.0, ge=0.0, le=1.0)


class TrackData(BaseModel):
    source_width: int
    source_height: int
    clip_start: float
    clip_end: float
    points: list[TrackPoint] = Field(default_factory=list)


class AntiCidConfig(BaseModel):
    zoom: float = Field(default=1.03, ge=1.0, le=1.15)
    speed: float = Field(default=1.015, ge=0.95, le=1.10)
    brightness: float = Field(default=0.01, ge=-0.1, le=0.1)
    contrast: float = Field(default=1.02, ge=0.9, le=1.2)
    saturation: float = Field(default=1.03, ge=0.85, le=1.25)
    strip_metadata: bool = True


class AdOverrideConfig(BaseModel):
    insert_position: Optional[Literal["mid_roll", "outro"]] = None
    insert_at_sec: Optional[float] = None
    overlay_x: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    overlay_y: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    overlay_scale: Optional[float] = Field(default=None, ge=5.0, le=95.0)
    overlay_start: Optional[float] = Field(default=None, ge=0.0)
    overlay_end: Optional[float] = Field(default=None, ge=0.0)
    fade_duration: Optional[float] = Field(default=None, ge=0.0, le=3.0)


# --- Projects ---


class ImportLocalRequest(BaseModel):
    file_path: str
    title: Optional[str] = None


class ImportUrlRequest(BaseModel):
    url: str
    title: Optional[str] = None


class BasicCutRequest(BaseModel):
    start_time: float = Field(ge=0.0)
    end_time: float = Field(gt=0.0)
    title: Optional[str] = None


class AnalyzeProjectRequest(BaseModel):
    mode: Literal["auto", "llm", "heuristic"] = "auto"
    max_highlights: Optional[int] = Field(default=None, ge=1, le=30)
    min_duration: float = Field(default=15.0, ge=3.0, le=120.0)
    max_duration: float = Field(default=60.0, ge=5.0, le=180.0)
    focus_prompt: str = ""


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    source_type: str
    source_url: Optional[str] = None
    source_path: str
    proxy_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    duration: float
    width: int
    height: int
    fps: float
    video_codec: Optional[str] = None
    audio_codec: Optional[str] = None
    has_audio: bool
    status: str
    error_message: Optional[str] = None
    analysis_json: Optional[dict[str, Any]] = None
    clip_count: int = 0
    created_at: datetime
    updated_at: datetime


# --- Clips ---


class ClipCreate(BaseModel):
    project_id: str
    title: str = "Новый клип"
    hook_text: str = ""
    hook_enabled: bool = True
    description: str = ""
    tags_json: list[str] = Field(default_factory=list)
    category: Literal["dialogue", "action", "hybrid", "manual", "live_spike"] = "manual"
    start_time: float = Field(ge=0.0)
    end_time: float = Field(gt=0.0)
    crop_mode: Literal["auto_track", "cinema_blur", "split_screen"] = "auto_track"
    background_asset_id: Optional[str] = None
    subtitle_preset_id: Optional[str] = None
    subtitles_enabled: bool = True
    ad_preset_id: Optional[str] = None
    anti_cid_enabled: bool = False


class ClipUpdate(BaseModel):
    title: Optional[str] = None
    hook_text: Optional[str] = None
    hook_enabled: Optional[bool] = None
    description: Optional[str] = None
    tags_json: Optional[list[str]] = None
    virality_score: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    category: Optional[Literal["dialogue", "action", "hybrid", "manual", "live_spike"]] = None
    reason: Optional[str] = None
    start_time: Optional[float] = Field(default=None, ge=0.0)
    end_time: Optional[float] = Field(default=None, gt=0.0)
    crop_mode: Optional[Literal["auto_track", "cinema_blur", "split_screen"]] = None
    background_asset_id: Optional[str] = None
    subtitle_preset_id: Optional[str] = None
    subtitles_enabled: Optional[bool] = None
    subtitles_json: Optional[list[SubtitleCue]] = None
    track_data_json: Optional[TrackData] = None
    ad_preset_id: Optional[str] = None
    ad_override_json: Optional[AdOverrideConfig] = None
    anti_cid_enabled: Optional[bool] = None
    anti_cid_json: Optional[AntiCidConfig] = None
    selected_for_export: Optional[bool] = None


class ClipOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    hook_text: str
    hook_enabled: bool
    description: str
    tags_json: list[str]
    virality_score: float
    category: str
    reason: str
    start_time: float
    end_time: float
    crop_mode: str
    background_asset_id: Optional[str] = None
    subtitle_preset_id: Optional[str] = None
    subtitles_enabled: bool
    subtitles_json: list[dict[str, Any]]
    track_data_json: Optional[dict[str, Any]] = None
    ad_preset_id: Optional[str] = None
    ad_override_json: Optional[dict[str, Any]] = None
    anti_cid_enabled: bool
    anti_cid_json: Optional[dict[str, Any]] = None
    selected_for_export: bool
    preview_path: Optional[str] = None
    export_path: Optional[str] = None
    status: str
    created_at: datetime
    updated_at: datetime


class BatchExportRequest(BaseModel):
    clip_ids: Optional[list[str]] = None
    output_dir: Optional[str] = None


# --- Presets & Assets ---


class SubtitlePresetCreate(BaseModel):
    name: str
    font_name: str = "Arial"
    font_size: int = Field(default=68, ge=20, le=140)
    primary_color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    outline_color: str = "#000000"
    outline_width: int = Field(default=5, ge=0, le=15)
    shadow_depth: int = Field(default=3, ge=0, le=15)
    bold: bool = True
    uppercase: bool = True
    margin_bottom: int = Field(default=380, ge=80, le=900)
    words_per_line: int = Field(default=3, ge=1, le=8)
    karaoke_mode: Literal["karaoke_fill", "word_highlight", "phrase"] = "karaoke_fill"
    hook_bg_color: str = "#7C3AED"
    hook_text_color: str = "#FFFFFF"
    is_default: bool = False


class SubtitlePresetUpdate(BaseModel):
    name: Optional[str] = None
    font_name: Optional[str] = None
    font_size: Optional[int] = Field(default=None, ge=20, le=140)
    primary_color: Optional[str] = None
    highlight_color: Optional[str] = None
    outline_color: Optional[str] = None
    outline_width: Optional[int] = Field(default=None, ge=0, le=15)
    shadow_depth: Optional[int] = Field(default=None, ge=0, le=15)
    bold: Optional[bool] = None
    uppercase: Optional[bool] = None
    margin_bottom: Optional[int] = Field(default=None, ge=80, le=900)
    words_per_line: Optional[int] = Field(default=None, ge=1, le=8)
    karaoke_mode: Optional[Literal["karaoke_fill", "word_highlight", "phrase"]] = None
    hook_bg_color: Optional[str] = None
    hook_text_color: Optional[str] = None
    is_default: Optional[bool] = None


class SubtitlePresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    font_name: str
    font_size: int
    primary_color: str
    highlight_color: str
    outline_color: str
    outline_width: int
    shadow_depth: int
    bold: bool
    uppercase: bool
    margin_bottom: int
    words_per_line: int
    karaoke_mode: str
    hook_bg_color: str
    hook_text_color: str
    is_default: bool
    created_at: datetime


class AdPresetCreate(BaseModel):
    name: str
    ad_type: Literal["video_insert", "overlay"]
    asset_path: str
    insert_position: Literal["mid_roll", "outro"] = "outro"
    insert_at_sec: Optional[float] = Field(default=None, ge=0.0)
    overlay_x: float = Field(default=50.0, ge=0.0, le=100.0)
    overlay_y: float = Field(default=22.0, ge=0.0, le=100.0)
    overlay_scale: float = Field(default=35.0, ge=5.0, le=95.0)
    overlay_start: float = Field(default=3.0, ge=0.0)
    overlay_end: float = Field(default=12.0, ge=0.0)
    fade_duration: float = Field(default=0.4, ge=0.0, le=3.0)


class AdPresetUpdate(BaseModel):
    name: Optional[str] = None
    ad_type: Optional[Literal["video_insert", "overlay"]] = None
    asset_path: Optional[str] = None
    insert_position: Optional[Literal["mid_roll", "outro"]] = None
    insert_at_sec: Optional[float] = Field(default=None, ge=0.0)
    overlay_x: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    overlay_y: Optional[float] = Field(default=None, ge=0.0, le=100.0)
    overlay_scale: Optional[float] = Field(default=None, ge=5.0, le=95.0)
    overlay_start: Optional[float] = Field(default=None, ge=0.0)
    overlay_end: Optional[float] = Field(default=None, ge=0.0)
    fade_duration: Optional[float] = Field(default=None, ge=0.0, le=3.0)


class AdPresetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    ad_type: str
    asset_path: str
    insert_position: str
    insert_at_sec: Optional[float] = None
    overlay_x: float
    overlay_y: float
    overlay_scale: float
    overlay_start: float
    overlay_end: float
    fade_duration: float
    duration: float
    created_at: datetime


class BackgroundAssetCreate(BaseModel):
    file_path: str
    name: Optional[str] = None


class BackgroundFolderScanRequest(BaseModel):
    folder_path: str


class BackgroundAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    file_path: str
    duration: float
    width: int
    height: int
    thumbnail_path: Optional[str] = None
    created_at: datetime


# --- Jobs & System ---


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_type: str
    project_id: Optional[str] = None
    clip_id: Optional[str] = None
    status: str
    stage: str
    progress: float
    message: str
    payload_json: dict[str, Any]
    result_json: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class GPUInfoOut(BaseModel):
    cuda_available: bool
    device_name: str
    vram_total_mb: int
    vram_used_mb: int
    vram_free_mb: int
    torch_allocated_mb: int
    torch_reserved_mb: int
    driver_version: Optional[str] = None


class EncoderProbeOut(BaseModel):
    selected: str
    available: list[str]
    details: dict[str, bool]


class LLMTestRequest(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None


class LLMTestResponse(BaseModel):
    ok: bool
    model: str
    latency_ms: int
    reply: str = ""
    error: Optional[str] = None


# --- LLM Highlights Structured Output ---


class LLMHighlightItem(BaseModel):
    start_time: float = Field(ge=0.0)
    end_time: float = Field(gt=0.0)
    title: str
    hook_text: str
    description: str
    tags: list[str] = Field(default_factory=list)
    virality_score: float = Field(ge=0.0, le=100.0)
    category: Literal["dialogue", "action", "hybrid"]
    reason: str


class LLMHighlightsResponse(BaseModel):
    highlights: list[LLMHighlightItem] = Field(default_factory=list)


# --- Live Monitor & Auto-Posting ---


class LiveChannelCreate(BaseModel):
    channel_url: str
    name: Optional[str] = None
    platform: Optional[Literal["twitch", "kick", "youtube"]] = None
    enabled: bool = True
    check_interval_sec: int = Field(default=60, ge=15, le=600)
    buffer_minutes: int = Field(default=20, ge=5, le=60)
    ai_mode: Literal["zero_ai", "llm", "auto"] = "zero_ai"
    chat_sensitivity: float = Field(default=1.6, ge=1.1, le=5.0)
    audio_sensitivity: float = Field(default=1.6, ge=1.1, le=5.0)
    clip_pre_sec: float = Field(default=25.0, ge=5.0, le=90.0)
    clip_post_sec: float = Field(default=15.0, ge=2.0, le=60.0)
    min_clip_gap_sec: int = Field(default=90, ge=15, le=600)
    crop_mode: Literal["auto_track", "cinema_blur", "split_screen"] = "auto_track"
    subtitle_preset_id: Optional[str] = None
    ad_preset_id: Optional[str] = None
    background_asset_id: Optional[str] = None
    anti_cid_enabled: bool = True
    auto_publish: bool = False
    publish_account_ids_json: list[str] = Field(default_factory=list)


class LiveChannelUpdate(BaseModel):
    name: Optional[str] = None
    channel_url: Optional[str] = None
    enabled: Optional[bool] = None
    check_interval_sec: Optional[int] = Field(default=None, ge=15, le=600)
    buffer_minutes: Optional[int] = Field(default=None, ge=5, le=60)
    ai_mode: Optional[Literal["zero_ai", "llm", "auto"]] = None
    chat_sensitivity: Optional[float] = Field(default=None, ge=1.1, le=5.0)
    audio_sensitivity: Optional[float] = Field(default=None, ge=1.1, le=5.0)
    clip_pre_sec: Optional[float] = Field(default=None, ge=5.0, le=90.0)
    clip_post_sec: Optional[float] = Field(default=None, ge=2.0, le=60.0)
    min_clip_gap_sec: Optional[int] = Field(default=None, ge=15, le=600)
    crop_mode: Optional[Literal["auto_track", "cinema_blur", "split_screen"]] = None
    subtitle_preset_id: Optional[str] = None
    ad_preset_id: Optional[str] = None
    background_asset_id: Optional[str] = None
    anti_cid_enabled: Optional[bool] = None
    auto_publish: Optional[bool] = None
    publish_account_ids_json: Optional[list[str]] = None


class LiveChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    platform: str
    channel_url: str
    enabled: bool
    status: str
    stream_title: str
    viewer_count: Optional[int] = None
    check_interval_sec: int
    buffer_minutes: int
    ai_mode: str
    chat_sensitivity: float
    audio_sensitivity: float
    clip_pre_sec: float
    clip_post_sec: float
    min_clip_gap_sec: int
    crop_mode: str
    subtitle_preset_id: Optional[str] = None
    ad_preset_id: Optional[str] = None
    background_asset_id: Optional[str] = None
    anti_cid_enabled: bool
    auto_publish: bool
    publish_account_ids_json: list[str]
    active_project_id: Optional[str] = None
    last_error: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    runtime: dict[str, Any] = Field(default_factory=dict)


class LiveClipNowRequest(BaseModel):
    seconds: float = Field(default=45.0, ge=5.0, le=180.0)


class LiveClipFeedOut(BaseModel):
    clip: ClipOut
    channel_id: Optional[str] = None
    channel_name: str = ""
    platform: str = ""


class SocialAccountCreate(BaseModel):
    platform: Literal["youtube", "tiktok", "instagram"]
    name: str
    auth_type: Literal["oauth", "token", "cookies"] = "token"
    credentials_json: dict[str, Any] = Field(default_factory=dict)
    default_privacy: Literal["public", "unlisted", "private"] = "public"
    post_gap_minutes: int = Field(default=120, ge=5, le=1440)
    daily_cap: int = Field(default=6, ge=1, le=50)
    enabled: bool = True


class SocialAccountUpdate(BaseModel):
    name: Optional[str] = None
    credentials_json: Optional[dict[str, Any]] = None
    default_privacy: Optional[Literal["public", "unlisted", "private"]] = None
    post_gap_minutes: Optional[int] = Field(default=None, ge=5, le=1440)
    daily_cap: Optional[int] = Field(default=None, ge=1, le=50)
    enabled: Optional[bool] = None


class SocialAccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    name: str
    auth_type: str
    has_credentials: bool = False
    default_privacy: str
    post_gap_minutes: int
    daily_cap: int
    enabled: bool
    detail: str
    created_at: datetime
    updated_at: datetime


class PublishEnqueueRequest(BaseModel):
    clip_id: str
    account_ids: list[str]
    publish_now: bool = False
    title: Optional[str] = None
    caption: Optional[str] = None
    privacy: Optional[Literal["public", "unlisted", "private"]] = None


class PublishQueueItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    clip_id: str
    project_id: Optional[str] = None
    account_id: str
    platform: str
    account_name: str
    title: str
    caption: str
    tags_json: list[str]
    privacy: str
    video_path: str
    status: str
    scheduled_for: datetime
    posted_at: Optional[datetime] = None
    tries: int
    remote_id: str
    remote_url: str
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
