# Narezki — Прогресс разработки

Десктопное приложение для автоматической нарезки длинных видео (фильмы, аниме, стримы) в вертикальные ролики **9:16** для TikTok, YouTube Shorts и Reels.

## Статус этапов (Milestones)

- [x] **Этап 1: Инициализация и связка Tauri v2 + FastAPI** — завершён и проверен: `src-tauri` (`cargo build` 0 ошибок, трей, автозапуск `.venv` с логом `backend/data/logs/backend.log`, `taskkill /T /F`), SQLite (`aiosqlite` + WAL), WebSocket `/ws`, React + TS + Tailwind + Zustand (`npm.cmd run build` 0 ошибок).
- [x] **Этап 2: Ingest и FFmpeg-движок** — завершён и проверен: FFmpeg GPL (`ffmpeg.exe` / `ffprobe.exe` в `backend/bin/`), аппаратная автодетекция (`h264_nvenc`, `hevc_nvenc`, `libx264`), импорт локальных видео и URL (`yt-dlp`), прокси 720p + обложки и нарезка по таймкодам.
- [x] **Этап 3: AI-анализатор** — завершён и проверен: `audio_peaks.py` (RMS dBFS пики), `scene_detect.py` (`PySceneDetect` + склейки + привязка границ к склейкам/паузам), `transcriber.py` (`faster-whisper` на CUDA `float16` с VAD и `word_timestamps`), `llm_highlights.py` (OpenAI-compatible Cloud LLM с чанкованием и JSON-схемой + локальный мультимодальный анализатор).
- [x] **Этап 4: Трекинг YOLOv8 и субтитры `.ass`** — завершён и проверен: `smoother.py` (Kalman/EMA с deadzone и сбросом на склейках), `yolo_tracker.py` (`yolov8n.pt` и кастомные `.pt` веса на CUDA), `crop_filters.py` (`auto_track`, `cinema_blur`, `split_screen`), `subtitle_ass.py` (`.ass` с `\kf` караоке, `word_highlight`, safe-zone отступами и 3-секундным хуком).
- [x] **Этап 5: Микрореклама, уникализация, UI-студия и батч-экспорт** — завершён и проверен: `ad_injector.py` (видеовклейки `mid_roll`/`outro` с ресайзом 1080×1920 и `loudnorm`, PNG/WebM/GIF оверлеи с alpha `fade-in`/`fade-out`), `anti_cid.py` (микро-зум, цветокоррекция, скорость+питч, очистка метаданных), мастер-рендерер (`-/filter_complex` / `-filter_complex_script`), полная студия `ClipStudioPage`, `PresetsPage` и батч-экспорт `.mp4` + `.txt`.
- [x] **Этап 6: Оптимизация VRAM, обработка ошибок и верификация** — завершён и проверен: `VRAMGuard` + `gpu_semaphore(1)` + `release_vram()` гарантируют очистку CUDA между Whisper и YOLOv8 (дельта VRAM < 500 МБ), отмена задач завершает дочерние процессы FFmpeg, HTTP Range `206` и защита путей (`403`) проверены.
- [x] **Этап 7: Live Monitor, Chat Spike Detection и Автопостинг (интеграция PeakCut)** — завершён и проверен: [PEAKCUT_ANALYSIS.md](file:///f:/projects/narezki/PEAKCUT_ANALYSIS.md), `backend/app/services/live/` (`live_watcher.py`, `chat_analyzer.py`, `handlers.py`, `publisher.py`), единый 9:16 пайплайн (`YOLOv8` → `.ass` → реклама → Anti-CID → NVENC), режим `zero_ai` (без LLM), автопостинг в YouTube Shorts / TikTok / Instagram Reels с анти-тенебан интервалами и режимом «Только локальный экспорт» (`local_only`), а также вкладка `Live Monitor` и настройки публикаций во фронтенде.

---

## Результаты финальной верификации

- **Backend Python `.venv` (CUDA):** `torch 2.6.0+cu124`, `CUDA: True`, GPU `NVIDIA GeForce RTX 3060` (12288 MB VRAM, driver `621.01`).
- **Интеграционные тесты (`pytest -v`):** **`14 passed in 78.66s`** (0 ошибок, 0 пропусков):
  - `test_m1_health_gpu_and_default_presets`, `test_m1_websocket_connection_and_gpu_snapshot`
  - `test_m2_encoder_probe_detects_nvenc` (`selected=h264_nvenc`, `available=['h264_nvenc', 'hevc_nvenc', 'libx264']`)
  - `test_m2_ingest_local_and_basic_cut`
  - `test_m3_audio_peaks_and_scene_detect`, `test_m3_whisper_cuda_and_vram_release`, `test_m3_llm_chunking_and_dedup`, `test_m3_full_analyze_job_heuristic`
  - `test_m4_smoother_scene_cut_reset_and_ass_colors`, `test_m4_yolo_tracking_and_all_three_crop_modes` (`1080×1920` для `auto_track`, `cinema_blur`, `split_screen` с `.ass` и хуком)
  - `test_m5_webm_alpha_decoder_flag_before_input`, `test_m5_ads_anticid_and_batch_export` (`mid_roll`, `outro`, PNG overlay, Anti-CID, `clip_01..03.mp4` + `.txt`, HTTP Range `206` и `403`)
  - `test_m6_sequential_whisper_then_yolo_vram_cleanup`, `test_m6_error_handling_and_job_cancellation`
- **Живой smoke-тест FastAPI + WebSocket (`uvicorn`):** `/api/system/health` (`200 OK`), `/api/system/gpu` (`200 OK`), `/api/system/encoders` (`h264_nvenc`), `/api/presets/subtitles` (`3` пресета по умолчанию), `/ws` (`gpu_status` + `ping`/`pong`).
- **Frontend (`npm.cmd run build`):** `tsc && vite build` — `0` ошибок (сборка `dist/assets/index-BrmS3dfl.js`, 260.70 kB).
- **Desktop Rust (`cargo build` в `src-tauri/`):** `Finished dev profile` — `0` ошибок.

---

## Стабилизация и исправление багов (`BUG-01`..`BUG-20`)

Все 20 багов из [BUG_REPORT.md](file:///f:/projects/narezki/BUG_REPORT.md) (4 `CRITICAL`, 11 `MAJOR`, 5 `MINOR`) устранены атомарными коммитами с прогоном регрессионных тестов:

- **`CRITICAL` (`BUG-01`..`BUG-04`):** кроссплатформенный выбор бинарников `ffmpeg`/`ffprobe` по `sys.platform`, фолбэк `faster-whisper` на `int8` при `device="cpu"`, двухпроходное экранирование спецсимволов FFmpeg для `ass=filename=...` и санитизация `font_name`, кооперативное ожидание завершения GPU-потоков (`run_cancellable_thread`) до освобождения `gpu_semaphore`/VRAM при отмене.
- **`MAJOR` (`BUG-05`..`BUG-15`):** нормализация `48000 Hz` и `asetpts` после `loudnorm` + `setpts=PTS-STARTPTS` на рекламных вклейках и поддержка AV1 WebM; нормализация `fps` и масштабирование `mid_roll` при включённом Anti-CID; восстановление статуса клипа при отмене задач; идемпотентное переподключение WS-подписок и защита несохранённого черновика клипа, пробелов в хэштегах и cache-busting превью; обработка `"N/A"` и отклонение audio-only файлов в `ffprobe_utils`; защита `PATCH /api/clips` от `null`, очистка файлов при удалении клипа и проверка существования проекта после анализа; кроссплатформенные улучшения Tauri (`python3`, `PYTHONUTF8=1`, process group kill, нормализация путей `open_in_explorer`, `bundle.resources`); фиксация CUDA-зависимости `torch` в `requirements.txt` и `requirements-cpu.txt`.
- **`MINOR` (`BUG-16`..`BUG-20`):** повтор LLM только на `BadRequestError`, проброс `min_duration` и клампинг `< 1s` в `snap_clip_boundaries`; `_DynamicSemaphore` для потокобезопасной смены `max_render_workers`; параллельный `export_batch` с изоляцией ошибок и уникализацией имён; закрытие старого WebSocket при смене порта; обработка ошибок в UI-тостах, опция Whisper `"base"`, гарды `isTauriRuntime()`, форматирование 422 `errors`, неблокирующий `shutil.rmtree` после ожидания отменённых задач и ESLint.

### Итоговый отчёт о стабильности системы (2026-11-20)

- **Frontend (`npm.cmd run lint` & `npm.cmd run build`):** `eslint .` (`0` errors, `0` warnings), `tsc && vite build` (`0` ошибок, сборка `dist/assets/index-B9gyz-SL.js` `262.79 kB` за `2.50s`).
- **Tauri / Rust (`cargo check` в `src-tauri/`):** `Finished dev profile` (`0` ошибок).
- **Backend (`pytest backend/tests -v`):** **`28 passed in 83.14s`** (все 14 базовых интеграционных тестов M1–M6 + 14 новых регрессионных тестов `test_bug_fixes_regression.py`, 0 ошибок, 0 пропусков), `pip check` — `No broken requirements found`.
- **Сквозной живой Smoke Test (`uvicorn` на `127.0.0.1:18765`):**
  - `/api/system/health` (`ok`, `NVIDIA GeForce RTX 3060`, `h264_nvenc`), `/ws` (`gpu_status` + `pong`).
  - Полный цикл: `import-local` -> `analyze` (`heuristic` + `faster-whisper` CUDA `base` -> `10` слов, `1` клип) -> `track` (YOLOv8 CUDA, `46` точек) -> `export` (с субтитрами `.ass`, 3s хуком и Anti-CID `speed=1.04`).
  - Валидация итогового MP4 (`7,471,509` байт) через `ffprobe.exe`: `h264` `1080×1920`, `30/1` fps, аудио `aac` `48000 Hz`, длительность `4.833s`, файл `.txt` создан, удаление проекта прошло чисто.
  - В логах `uvicorn` зафиксировано **`0` необработанных исключений / traceback и `0` строк `[ERROR]`**.

---

## Архитектура проекта

```text
narezki/
├── src-tauri/                 # Tauri v2 (Rust): автозапуск backend/.venv, трей, нативные диалоги, open_in_explorer
├── src/                       # React + TypeScript (strict) + Tailwind CSS + Zustand
│   ├── components/            # Layout, LogDrawer, ToastContainer, VideoPreview916, ClipTimelineEditor, SubtitleCueEditor
│   ├── pages/                 # ProjectsPage, LiveMonitorPage, ClipStudioPage, PresetsPage, SettingsPage
│   ├── store/useAppStore.ts   # Zustand-состояние и обработка WebSocket-событий
│   └── lib/                   # api.ts, ws.ts, tauri.ts
├── backend/                   # Python 3.11+ FastAPI + SQLite (aiosqlite) + asyncio QueueManager
│   ├── bin/                   # ffmpeg.exe, ffprobe.exe (Windows GPL с NVENC и libass)
│   ├── app/core/              # cuda_env.py (DLL + VRAMGuard), config.py, models.py, schemas.py, database.py, ws_manager.py, queue_manager.py
│   ├── app/api/               # system.py, projects.py, clips.py, presets.py, jobs.py, media.py, live.py, publishing.py
│   ├── app/services/ingest/   # ffprobe_utils.py, proxy.py, downloader.py (yt-dlp), handlers.py
│   ├── app/services/analyzer/ # audio_peaks.py, scene_detect.py, transcriber.py (faster-whisper), llm_highlights.py, pipeline.py
│   ├── app/services/tracker/  # smoother.py (Kalman/EMA + reset), yolo_tracker.py
│   ├── app/services/renderer/ # encoder_detect.py, crop_filters.py, subtitle_ass.py, ad_injector.py, anti_cid.py, ffmpeg_runner.py
│   └── app/services/live/     # live_watcher.py, chat_analyzer.py, handlers.py, publisher.py
└── tests/                     # Интеграционные тесты pytest (M1–M6, регрессии BUG-XX и Live/Publishing)
```

---

## Основные API-эндпоинты

### System & Settings (`/api/system`)
- `GET /api/system/health` — статус бэкенда, FFmpeg/FFprobe и CUDA.
- `GET /api/system/gpu` — живая телеметрия GPU и VRAM (`MB`).
- `POST /api/system/gpu/release` — принудительная очистка VRAM (`torch.cuda.empty_cache()`).
- `GET /api/system/encoders` — проверка аппаратных энкодеров (`h264_nvenc`, `hevc_nvenc`, `h264_amf`, `h264_qsv`, `libx264`).
- `GET / PUT /api/system/settings` — чтение и сохранение конфигурации.
- `POST /api/system/test-llm` — проверка соединения с Cloud LLM API.

### Projects & Queue (`/api/projects`, `/api/jobs`)
- `GET /api/projects`, `GET /api/projects/{id}`, `DELETE /api/projects/{id}`
- `POST /api/projects/import-local` — импорт локального видеофайла (probe + thumbnail + 720p proxy).
- `POST /api/projects/import-url` — скачивание видео по ссылке через `yt-dlp`.
- `POST /api/projects/{id}/basic-cut` — быстрая резка отрезка по таймкодам.
- `POST /api/projects/{id}/analyze` — запуск AI-пайплайна (Audio Peaks + PySceneDetect + Whisper CUDA + LLM/Heuristic).
- `POST /api/projects/{id}/export-batch` — батч-экспорт выбранных клипов (`clip_01.mp4` + `clip_01.txt`).
- `GET /api/jobs`, `POST /api/jobs/{id}/cancel`, `DELETE /api/jobs/completed`
- `WS /ws` — поток событий (`job_update`, `log`, `gpu_status`, `project_update`, `clip_update`).

### Clips, Presets & Media (`/api/clips`, `/api/presets`, `/api/media`)
- `GET / POST /api/clips`, `GET / PATCH / DELETE /api/clips/{id}`
- `POST /api/clips/{id}/resync-subtitles` — пересборка субтитров из сохранённого транскрипта.
- `POST /api/clips/{id}/track` — запуск YOLOv8 трекинга на GPU.
- `POST /api/clips/{id}/render-preview` — рендер 9:16 превью.
- `POST /api/clips/{id}/export` — финальный экспорт `.mp4` + `.txt`.
- `GET / POST / PATCH / DELETE /api/presets/subtitles`
- `GET / POST / POST(/upload) / PATCH / DELETE /api/presets/ads`
- `GET / POST / POST(/scan-folder) / DELETE /api/presets/backgrounds`
- `GET /api/media/stream?path=...` — HTTP Range (206) стриминг с валидацией путей.

### Live Monitor & Auto-Posting (`/api/live`, `/api/publishing`)
- `GET / POST / PATCH / DELETE /api/live/channels` — управление отслеживаемыми каналами Twitch / Kick / YouTube.
- `POST /api/live/channels/{id}/start`, `/stop`, `/check`, `/clip-now` — запуск/остановка буфера, проверка онлайна и ручная нарезка последних N секунд.
- `GET /api/live/clips` — живая лента клипов, нарезанных в реальном времени.
- `GET / POST / PATCH / DELETE /api/publishing/accounts` — подключённые аккаунты (без утечки `credentials_json` в API-ответах).
- `GET /api/publishing/oauth/{platform}/url` и `/callback` — OAuth2-авторизация YouTube / TikTok / Instagram.
- `GET /api/publishing/queue`, `POST /api/publishing/enqueue`, `/cancel`, `/retry`, `DELETE` — очередь отложенного постинга.
- `GET /api/publishing/public/{token}.mp4` — подписанная HMAC-ссылка на экспортированный клип для Instagram Reels.
- Новые события `WS /ws`: `live_update`, `publish_update`.

---

## Инструкция по настройке Live-мониторинга и автопостинга на Windows

1. **Предусловия (Windows 10/11):**
   - Используется штатное окружение `backend\.venv` (`yt-dlp`, `httpx`, `websockets`, `numpy`) и бинарники `backend\bin\ffmpeg.exe` / `ffprobe.exe` (при желании можно дополнительно установить `streamlink` в PATH как резервный резолвер потоков). Все фоновые вызовы на Windows запускаются с `CREATE_NO_WINDOW` без всплывающих консолей.
2. **Подключение стримов во вкладке `Live Monitor`:**
   - Вставьте ссылку вида `https://www.twitch.tv/channel`, `https://kick.com/channel` или `https://www.youtube.com/@channel/live`.
   - Выберите размер **кольцевого буфера** (`buffer_minutes`, по умолчанию 20 мин) — хранится в `backend\data\live_buffers\<channel_id>\` в виде 6-секундных `.ts` сегментов; старые сегменты автоматически удаляются.
   - Выберите режим детекции:
     - **`Zero-AI (Чат + Аудио пики)`** — работает без LLM-ключей: триггер срабатывает по всплеску уникальных авторов/эмодзи в чате, аудио-скачку RMS dBFS и зрительским командам `!clip` / `клип`.
     - **`Auto` / `LLM`** — использует всплески чата и транскрипт Whisper для генерации заголовка, 3-секундного хука и тегов через Cloud LLM API.
   - Выберите формат **9:16** (`YOLOv8 Smart Crop`, `Cinema Blur` или `Split-Screen`), стиль субтитров `.ass`, пресет микрорекламы и флаг `Уникализация (Anti-CID)`.
3. **Настройка публикаций (`Настройки` → `Публикация и Автопостинг`):**
   - **Безопасный режим по умолчанию — «Только локальный экспорт» (`local_only`):** готовые клипы сохраняются только локально в `.mp4` + `.txt`, без автоотправки в соцсети.
   - Для автоматической отправки выберите **«Автопостинг по расписанию» (`auto_publish`)** и (по необходимости) включите **«Автоматически публиковать лайв-клипы после рендера»**.
   - Подключите аккаунты через **OAuth2** (заполнив Client ID / Secret и нажав `OAuth`) или добавьте вручную **API Token / Refresh Token / Cookies** (указав паузу `post_gap_minutes`, например `120` мин = 1 ролик раз в 2 часа, и суточный лимит `daily_cap` для защиты от теневого бана).
   - Для **Instagram Reels** API требует публично доступного `HTTPS`-URL клипа: укажите адрес туннеля (например, `cloudflared tunnel --url http://127.0.0.1:8765`) в поле `Public HTTPS URL`.

### Верификация после интеграции Live Monitor & Auto-Posting (2026-11-20)
- **Backend (`pytest backend/tests -v`):** **`34 passed in 75.16s`** (`14` M1–M6 + `14` регрессионных `BUG-XX` + `6` новых тестов в `test_live_and_publishing.py`, `0` ошибок, `0` пропусков).
- **Frontend (`npm.cmd run lint`, `npx.cmd tsc --noEmit`, `npm.cmd run build`):** `eslint .` (`0` errors, `0` warnings), `tsc` (`0` ошибок), `vite build` (`built in 3.72s`).
- **Tauri / Rust (`cargo check` в `src-tauri/`):** `Finished dev profile` (`0` ошибок).

---

## Стабилизация Раунд 2 (`BUG-21`..`BUG-34`)

Все 14 багов второго раунда аудита из [BUG_REPORT.md](file:///f:/projects/narezki/BUG_REPORT.md) (3 `CRITICAL`, 9 `MAJOR`, 2 `MINOR`) исправлены атомарными коммитами и проверены реальным запуском:

- **`CRITICAL` (`BUG-21`..`BUG-23`):**
  - `BUG-21`: пропуск `loudnorm` на коротких (< 1.5с) и беззвучных клипах/рекламе (устранён краш AAC `NaN/+-Inf`), валидация границ клипа относительно длительности исходника и пост-рендер проверка видеопотока.
  - `BUG-22`: безопасное извлечение окна в `RollingBufferManager.extract_window()` (`-ss` до `-i`, условный `-bsf:a aac_adtstoasc`, проверка видеопотока + фолбэк на быстрое перекодирование `libx264 ultrafast`).
  - `BUG-23`: добавление `requirements-cpu.txt` в `bundle.resources` (`tauri.conf.json`), передача пользовательской `app_data_dir()` через `NAREZKI_DATA_DIR` в release-сборках Tauri и привязка `MODELS_DIR` к `DATA_DIR`.
- **`MAJOR` (`BUG-24`..`BUG-32`):**
  - `BUG-24`: детерминированный порт `8765` (с фолбэком на свободный), приоритет `publish_public_base_url` в OAuth2 `redirect_uri`, отображение callback URI в UI и открытие внешних ссылок в системном браузере через Rust-команду `open_external_url`.
  - `BUG-25`: отправка `emit_project_update` при создании Live-проекта, обновление списка проектов перед открытием Студии и защита `refreshClips` от перезаписи клипов другого проекта.
  - `BUG-26`: гарантированное `proc.kill()` + `await proc.wait()` при таймаутах и `asyncio.CancelledError` в `live_watcher.py`, `ffmpeg_runner.py`, `audio_peaks.py`, `proxy.py`.
  - `BUG-27`: проверка наличия аудиодорожки в Live-потоке перед добавлением PCM-выхода (video-only стримы больше не роняют FFmpeg) и логирование ошибок авто-клиппинга без сжигания кулдауна.
  - `BUG-28`: ограничение роста `segments.csv` через `-segment_list_size`, вынос сканирования/очистки буфера и `shutil.rmtree` в `asyncio.to_thread` с кэшированным `snapshot()`.
  - `BUG-29`: явное исключение при таймауте поллинга TikTok/Instagram, защита от перезаписи `cancelled` (и HTTP 409 при отмене `posting`), безопасный парсер `expires_at=None` и проверка непустого файла.
  - `BUG-30`: единый `filter_non_nullable_updates()` через SQLAlchemy `inspect()` во всех `PATCH`-эндпоинтах (`live`, `publishing`, `presets`, `clips`).
  - `BUG-31`: ожидание кооперативного завершения активных `handler_task` в `QueueManager.stop()` до `release_vram_async("shutdown")` и использование `run_cancellable_thread` в `downloader.py`.
  - `BUG-32`: сброс PTS (`setpts=PTS-STARTPTS` / `asetpts=PTS-STARTPTS`) до `trim`/`atrim` и в `build_overlay_filtergraph()`, а также `require_video=True` для `video_insert`.
- **`MINOR` (`BUG-33`..`BUG-34`):**
  - `BUG-33`: монотонный агрегированный прогресс в `handle_export_batch` и обязательная валидация трекинга в `_render_or_export_single`.
  - `BUG-34`: обработка ошибок очереди публикаций в `SettingsPage.tsx`, корректный `restored_status` при отмене анализа, статус `"error"` и логирование в `_poll_loop`, очистка старого файла рекламы в `ADS_DIR` и корректное экранирование `/select,"..."` через `raw_arg` для `explorer.exe` на Windows.

### Итоговая верификация Раунда 2 (2026-11-20)
- **Backend (`pytest -v`):** **`34 passed in 68.70s`** (0 ошибок, 0 пропусков).
- **Frontend (`tsc --noEmit`, `eslint .`, `vite build`):** 0 ошибок, 0 предупреждений (`dist/assets/index-Cax_M8Mv.js`, `308.58 kB`, собрано за `2.99s`).
- **Tauri / Rust (`cargo check` & `cargo check --release`):** 0 ошибок.
- **Сквозной Smoke Test (живой FastAPI на `127.0.0.1:18766`, русский язык):**
  - `import-local` → создание клипа с кириллическими субтитрами `.ass` (караоке), 3-секундным русским хуком и Anti-CID → `export` в `.mp4` + `.txt`.
  - Проверка через `backend/bin/ffprobe.exe`: `1080×1920`, видео `h264`, аудио `aac` `48000 Hz`, длительность `6.500s` (> 5с), размер `6,782,692` байт, сопутствующий UTF-8 `.txt` создан.
  - Лог `uvicorn`: **0 ошибок, 0 traceback**.

---

## Команды запуска и проверки (Windows)

```powershell
# 0. Первый запуск (установка зависимостей)
cd F:\projects\narezki
npm.cmd install
cd F:\projects\narezki\backend
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt

# 1. Запуск десктопного приложения (Tauri v2 + Vite + автозапуск FastAPI из .venv)
cd F:\projects\narezki
npm.cmd run tauri dev

# 2. Сборка продакшен-фронтенда, проверка типов TypeScript и ESLint
cd F:\projects\narezki
npx.cmd tsc --noEmit
npm.cmd run lint
npm.cmd run build

# 3. Проверка/сборка Tauri (Rust)
cd F:\projects\narezki\src-tauri
cargo check

# 4. Автономный запуск Python FastAPI бэкенда (UTF-8, порт 8765)
cd F:\projects\narezki\backend
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8765

# 5. Запуск всех интеграционных и регрессионных тестов бэкенда
cd F:\projects\narezki\backend
.\.venv\Scripts\pytest.exe -v
```
