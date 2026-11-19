from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import AppSettingsModel, DB_PATH, ensure_data_dirs
from .models import AppSetting, Base, SubtitlePreset

DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH.as_posix()}"

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
    connect_args={"timeout": 30},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


DEFAULT_SUBTITLE_PRESETS: list[dict[str, Any]] = [
    {
        "id": "sub_viral_yellow",
        "name": "TikTok Viral Yellow",
        "font_name": "Arial",
        "font_size": 70,
        "primary_color": "#FFFFFF",
        "highlight_color": "#FFE600",
        "outline_color": "#000000",
        "outline_width": 6,
        "shadow_depth": 3,
        "bold": True,
        "uppercase": True,
        "margin_bottom": 390,
        "words_per_line": 3,
        "karaoke_mode": "karaoke_fill",
        "hook_bg_color": "#7C3AED",
        "hook_text_color": "#FFFFFF",
        "is_default": True,
    },
    {
        "id": "sub_anime_clean",
        "name": "Anime Clean White",
        "font_name": "Segoe UI",
        "font_size": 60,
        "primary_color": "#F8FAFC",
        "highlight_color": "#38BDF8",
        "outline_color": "#09090D",
        "outline_width": 5,
        "shadow_depth": 2,
        "bold": True,
        "uppercase": False,
        "margin_bottom": 340,
        "words_per_line": 4,
        "karaoke_mode": "word_highlight",
        "hook_bg_color": "#0F172A",
        "hook_text_color": "#38BDF8",
        "is_default": False,
    },
    {
        "id": "sub_streamer_neon",
        "name": "Streamer Neon Purple",
        "font_name": "Impact",
        "font_size": 74,
        "primary_color": "#FFFFFF",
        "highlight_color": "#C084FC",
        "outline_color": "#1E0B36",
        "outline_width": 6,
        "shadow_depth": 4,
        "bold": True,
        "uppercase": True,
        "margin_bottom": 410,
        "words_per_line": 2,
        "karaoke_mode": "word_highlight",
        "hook_bg_color": "#DB2777",
        "hook_text_color": "#FFFFFF",
        "is_default": False,
    },
]


async def init_db() -> None:
    """Create tables and seed default subtitle presets and app settings."""
    ensure_data_dirs()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        existing_presets = (await session.execute(select(SubtitlePreset))).scalars().all()
        if not existing_presets:
            for preset_data in DEFAULT_SUBTITLE_PRESETS:
                session.add(SubtitlePreset(**preset_data))

        existing_settings = await session.get(AppSetting, "app_settings")
        if existing_settings is None:
            defaults = AppSettingsModel()
            session.add(AppSetting(key="app_settings", value_json=defaults.model_dump()))

        await session.commit()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def load_settings(session: AsyncSession) -> AppSettingsModel:
    row = await session.get(AppSetting, "app_settings")
    if row is None or not isinstance(row.value_json, dict):
        return AppSettingsModel()
    return AppSettingsModel.model_validate(row.value_json)


async def save_settings(session: AsyncSession, settings: AppSettingsModel) -> AppSettingsModel:
    row = await session.get(AppSetting, "app_settings")
    payload = settings.model_dump()
    if row is None:
        session.add(AppSetting(key="app_settings", value_json=payload))
    else:
        row.value_json = payload
    await session.commit()
    return settings
