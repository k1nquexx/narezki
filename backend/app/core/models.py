from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, inspect as sa_inspect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


def filter_non_nullable_updates(model_cls: type[Base], updates: dict[str, Any]) -> dict[str, Any]:
    """Drop explicit None values for columns declared NOT NULL on the SQLAlchemy model."""
    mapper = sa_inspect(model_cls)
    non_nullable = {col.key for col in mapper.columns if not col.nullable}
    return {
        k: v
        for k, v in updates.items()
        if not (v is None and k in non_nullable)
    }


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), default="local")
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    proxy_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    fps: Mapped[float] = mapped_column(Float, default=30.0)
    video_codec: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    audio_codec: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(32), default="importing")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    analysis_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    clips: Mapped[list["Clip"]] = relationship(
        "Clip", back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )
    jobs: Mapped[list["Job"]] = relationship(
        "Job", back_populates="project", cascade="all, delete-orphan", passive_deletes=True
    )


class SubtitlePreset(Base):
    __tablename__ = "subtitle_presets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    font_name: Mapped[str] = mapped_column(String(120), default="Arial")
    font_size: Mapped[int] = mapped_column(Integer, default=68)
    primary_color: Mapped[str] = mapped_column(String(16), default="#FFFFFF")
    highlight_color: Mapped[str] = mapped_column(String(16), default="#FFE600")
    outline_color: Mapped[str] = mapped_column(String(16), default="#000000")
    outline_width: Mapped[int] = mapped_column(Integer, default=5)
    shadow_depth: Mapped[int] = mapped_column(Integer, default=3)
    bold: Mapped[bool] = mapped_column(Boolean, default=True)
    uppercase: Mapped[bool] = mapped_column(Boolean, default=True)
    margin_bottom: Mapped[int] = mapped_column(Integer, default=380)
    words_per_line: Mapped[int] = mapped_column(Integer, default=3)
    karaoke_mode: Mapped[str] = mapped_column(String(32), default="karaoke_fill")
    hook_bg_color: Mapped[str] = mapped_column(String(16), default="#7C3AED")
    hook_text_color: Mapped[str] = mapped_column(String(16), default="#FFFFFF")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdPreset(Base):
    __tablename__ = "ad_presets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ad_type: Mapped[str] = mapped_column(String(32), nullable=False)  # video_insert | overlay
    asset_path: Mapped[str] = mapped_column(Text, nullable=False)
    insert_position: Mapped[str] = mapped_column(String(32), default="outro")  # mid_roll | outro
    insert_at_sec: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    overlay_x: Mapped[float] = mapped_column(Float, default=50.0)
    overlay_y: Mapped[float] = mapped_column(Float, default=22.0)
    overlay_scale: Mapped[float] = mapped_column(Float, default=35.0)
    overlay_start: Mapped[float] = mapped_column(Float, default=3.0)
    overlay_end: Mapped[float] = mapped_column(Float, default=12.0)
    fade_duration: Mapped[float] = mapped_column(Float, default=0.4)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackgroundAsset(Base):
    __tablename__ = "background_assets"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    duration: Mapped[float] = mapped_column(Float, default=0.0)
    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hook_text: Mapped[str] = mapped_column(String(255), default="")
    hook_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    virality_score: Mapped[float] = mapped_column(Float, default=0.0)
    category: Mapped[str] = mapped_column(String(32), default="dialogue")
    reason: Mapped[str] = mapped_column(Text, default="")
    start_time: Mapped[float] = mapped_column(Float, nullable=False)
    end_time: Mapped[float] = mapped_column(Float, nullable=False)

    crop_mode: Mapped[str] = mapped_column(String(32), default="auto_track")
    background_asset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("background_assets.id", ondelete="SET NULL"), nullable=True
    )
    subtitle_preset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("subtitle_presets.id", ondelete="SET NULL"), nullable=True
    )
    subtitles_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    subtitles_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    track_data_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    ad_preset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("ad_presets.id", ondelete="SET NULL"), nullable=True
    )
    ad_override_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    anti_cid_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    anti_cid_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)

    selected_for_export: Mapped[bool] = mapped_column(Boolean, default=True)
    preview_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    export_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="draft")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    project: Mapped["Project"] = relationship("Project", back_populates="clips")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    clip_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("clips.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_json: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="jobs")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class LiveChannel(Base):
    __tablename__ = "live_channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(24), nullable=False)  # twitch | kick | youtube
    channel_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    status: Mapped[str] = mapped_column(String(32), default="offline")  # offline | online | recording | error
    stream_title: Mapped[str] = mapped_column(Text, default="")
    viewer_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    check_interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=20)
    ai_mode: Mapped[str] = mapped_column(String(24), default="zero_ai")  # zero_ai | llm | auto
    chat_sensitivity: Mapped[float] = mapped_column(Float, default=1.6)
    audio_sensitivity: Mapped[float] = mapped_column(Float, default=1.6)
    clip_pre_sec: Mapped[float] = mapped_column(Float, default=25.0)
    clip_post_sec: Mapped[float] = mapped_column(Float, default=15.0)
    min_clip_gap_sec: Mapped[int] = mapped_column(Integer, default=90)

    crop_mode: Mapped[str] = mapped_column(String(32), default="auto_track")
    subtitle_preset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("subtitle_presets.id", ondelete="SET NULL"), nullable=True
    )
    ad_preset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("ad_presets.id", ondelete="SET NULL"), nullable=True
    )
    background_asset_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("background_assets.id", ondelete="SET NULL"), nullable=True
    )
    anti_cid_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=False)
    publish_account_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)

    active_project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(24), nullable=False)  # youtube | tiktok | instagram
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(24), default="token")  # oauth | token | cookies
    credentials_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    default_privacy: Mapped[str] = mapped_column(String(24), default="public")  # public | unlisted | private
    post_gap_minutes: Mapped[int] = mapped_column(Integer, default=120)
    daily_cap: Mapped[int] = mapped_column(Integer, default=6)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class PublishQueueItem(Base):
    __tablename__ = "publish_queue"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    clip_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("clips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    project_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    account_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("social_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    platform: Mapped[str] = mapped_column(String(24), nullable=False)
    account_name: Mapped[str] = mapped_column(String(120), default="")
    title: Mapped[str] = mapped_column(String(255), default="")
    caption: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    privacy: Mapped[str] = mapped_column(String(24), default="public")
    video_path: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[str] = mapped_column(String(24), default="scheduled", index=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    tries: Mapped[int] = mapped_column(Integer, default=0)
    remote_id: Mapped[str] = mapped_column(String(128), default="")
    remote_url: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
