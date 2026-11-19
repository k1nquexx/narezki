from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


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
