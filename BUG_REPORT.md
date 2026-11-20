# Технический аудит и отчёт о багах (`BUG_REPORT.md`)

**Проект:** Narezki — AI Shorts & Reels Studio (`src/`, `src-tauri/`, `backend/`)  
**Дата аудита:** 2026-11-20 (Раунд 2 — полный аудит кодовой базы, включая модули Live Monitor и Auto-Publishing)  
**Режим:** только анализ, запуск проверок и документирование (логика приложения не изменялась).

---

## Сводка проверок по этапам

### 1. Сборка и статический анализ
- **Фронтенд (`TypeScript` / `Vite` / `ESLint`):**
  - `npx.cmd tsc --noEmit` — **OK** (`exit code 0`).
  - `npm.cmd run build` — **OK** (`exit code 0`, бандл собран за `4.39s`).
  - `npm.cmd run lint` (`eslint .`) — **OK** (`exit code 0`, 0 ошибок и 0 предупреждений).
- **Бэкенд (`Python 3.11.9` / `backend/.venv`):**
  - Рекурсивный импорт всех модулей `app.*` — **OK** (`Errors: 0`).
  - `pip check` — **OK** (`No broken requirements found`).
  - `requirements.txt` ссылается на `-r requirements-cpu.txt`, однако `requirements-cpu.txt` не включён в `src-tauri/tauri.conf.json -> bundle.resources` (`BUG-23`).
  - Схемы Pydantic (`backend/app/core/schemas.py`) и TypeScript-интерфейсы (`src/types/api.ts`) согласованы по полям, но в `PATCH`-эндпоинтах (`live.py`, `publishing.py`, `presets.py`, частично `clips.py`) передача явного `null` для `NOT NULL` колонок вызывает HTTP 500 `IntegrityError` (`BUG-30`).
- **Tauri / Rust (`src-tauri/`):**
  - `cargo check` — **OK** (`Finished dev profile in 1.38s`).
  - Выявлены проблемы production-сборки (`bundle.resources`, запись `backend/data/` в read-only каталог установки — `BUG-23`), несовместимость случайного порта бэкенда с OAuth2 `redirect_uri` (`BUG-24`) и экранирование пробелов в `explorer.exe /select,...` на Windows (`BUG-34`).

### 2. Логика пайплайнов, async и управление ресурсами
- Тяжёлые GPU-задачи (`faster-whisper`, `YOLOv8`) сериализованы через `gpu_semaphore` и `run_cancellable_thread()`, однако:
  - При `QueueManager.stop()` (`shutdown`) не ожидается завершение `handler_task` перед `release_vram_async("shutdown")`, а `download_video_url` использует голый `asyncio.to_thread` без ожидания при удалении проекта (`BUG-31`).
  - В `live_watcher.py` (`snapshot()`, `prune_old_segments()`) синхронное чтение и парсинг бесконечно растущего `segments.csv` + `stat()` всех `.ts` выполняются на event loop каждые 2 секунды и на каждый `GET /api/live/channels`; `delete_live_channel()` вызывает синхронный `shutil.rmtree` (`BUG-28`).
  - При таймаутах `asyncio.wait_for(proc.communicate(), ...)` в `live_watcher.py` и при `asyncio.CancelledError` в `ffmpeg_runner.py`, `audio_peaks.py`, `proxy.py` дочерние процессы `ffmpeg`/`yt-dlp`/`streamlink` не завершаются в `finally` (`BUG-26`).
  - В `publisher.py` при таймауте поллинга TikTok/Instagram задача молча считается успешной (`posted`), а отмена элемента в статусе `posting` перезаписывается обратно (`BUG-29`).

### 3. Кроссплатформенность (Linux <-> Windows) и FFmpeg
- `render_clip()` (`ffmpeg_runner.py`) передаёт аудио в `loudnorm=I=-14:TP=-1.5:LRA=11` даже для коротких (< 1с) и беззвучных клипов — подтверждено E2E-прогоном: `loudnorm` генерирует `NaN/+-Inf` и роняет AAC-энкодер (`BUG-21`).
- `RollingBufferManager.extract_window()` (`live_watcher.py`) ставит `-ss` после `-i` с `-c copy` — подтверждено E2E-прогоном: при длинном GOP или промахе мимо ключевого кадра создаётся audio-only MP4 без видеопотока (`BUG-22`).
- `LiveStreamSession._run()` (`live_watcher.py`) безусловно добавляет 2-й выход PCM (`-vn -ac 1 -ar 16000 -f s16le pipe:1`) — подтверждено E2E-прогоном: на видеопотоке без аудио FFmpeg немедленно падает (`BUG-27`).
- В `ffmpeg_runner.py` фильтры `trim`/`atrim` стоят до `setpts=PTS-STARTPTS`/`asetpts=PTS-STARTPTS`, а `build_overlay_filtergraph` не сбрасывает PTS оверлея (`BUG-32`).

### 4. End-to-End прогон и edge-cases
- Штатный `pytest -v` (`backend/tests/`): **34 passed in 73.59s**.
- Дополнительные E2E-пробы воспроизвели и подтвердили следующие сбои:
  - **FAIL (`BUG-21`)** `render_clip()` на 0.3s клипе с тишиной и на клипе с `start_time == end_time`: `RuntimeError: FFmpeg завершился с кодом 4294967274: [aac] Input contains (near) NaN/+-Inf / Error submitting audio frame to the encoder`.
  - **ISSUE (`BUG-21`)** `render_clip()` при `start_time >= video_duration` (10s..12s на 5s видео): возвращает успех, но создаёт пустой MP4 с `duration=0.0`.
  - **FAIL (`BUG-22`)** `RollingBufferManager.extract_window(1.0, 4.5, ...)` на реальном буфере FFmpeg `segment`: `ValueError: Файл extracted.mp4 не содержит видеопотока` (audio-only MP4 из-за `-ss` после `-i` + `-c copy`).
  - **FAIL (`BUG-27`)** Двухвыходная команда FFmpeg из `LiveStreamSession._run()` на видеопотоке без аудио: `rc=4294967274`, `Output file does not contain any stream / Error opening output file pipe:1`.

---

## Актуальные найденные баги (Раунд 2: `BUG-21` — `BUG-34`)

### [BUG-21] `CRITICAL` `[FIXED]` — Краш AAC-энкодера (`Input contains NaN/+-Inf`) из-за `loudnorm` на коротких клипах и пустой MP4 при выходе `start_time` за длительность
- **Файл и строка:** `backend/app/services/renderer/ffmpeg_runner.py:284-286, 376-384, 443-448`, `backend/app/services/renderer/ad_injector.py:110-114`
- **Суть проблемы:**
  1. Фильтр `loudnorm=I=-14:TP=-1.5:LRA=11` (EBU R128 с окном анализа ≥ 400 мс / 3 с) применяется безусловно ко всем клипам, включая короткие отрезки (< 1 с), клипы с `start_time == end_time` (принудительно расширяемые до `0.2s` на строке 285) и клипы с синтезированной тишиной (`anullsrc`). Подтверждено E2E-прогоном: на короткой тишине `loudnorm` выдаёт сэмплы `NaN/+-Inf`, после чего AAC-энкодер падает с `Input contains (near) NaN/+-Inf` (`rc=4294967274`). Аналогичный риск существует для коротких (< 1 с) рекламных роликов в `ad_injector.py:112`.
  2. Если `start_time` превышает реальную длительность исходного видео (например, устаревший клип или ручной ввод), `render_clip()` не валидирует границы против `duration` исходника — FFmpeg завершается с кодом `0`, но создаёт битый файл с `duration=0.0` и нулём видеокадров.
- **План исправления:**
  1. Пропускать `loudnorm` (использовать `aformat=sample_rates=48000:channel_layouts=stereo` / `anull`), если `not source_has_audio` или эффективная длительность `main_dur < 1.5` (и аналогично в `ad_injector.py` при `ad_dur < 1.5`).
  2. В начале `render_clip()` валидировать `end_time > start_time` и проверять, что `start_time < source_duration - 0.1` (подгружая `duration` из `probe_media`, если нужно), а после рендера проверять ненулевую длительность / наличие видеопотока.

---

### [BUG-22] `CRITICAL` `[FIXED]` — Потеря видеопотока (audio-only MP4) в `RollingBufferManager.extract_window()` при нарезке из live-буфера
- **Файл и строка:** `backend/app/services/live/live_watcher.py:472-505`
- **Суть проблемы:** В `extract_window()` команда FFmpeg использует `-f concat -i segments.concat.txt -ss <offset> -t <clip_duration> -c copy output.mp4` (`-ss` после `-i` в режиме `stream copy`). При stream copy FFmpeg отбрасывает все видеопакеты после `offset` до ближайшего IDR-ключевого кадра. Если ключевой кадр в сегментах редкий (или приходится после `offset + clip_duration`), в итоговый MP4 попадает **только аудиодорожка без единого видеокадра**. Подтверждено E2E-прогоном на реальных `.ts` сегментах: `probe_media(slice_path, require_video=True)` падает с `ValueError: Файл extracted.mp4 не содержит видеопотока`. Кроме того, копирование MPEG-TS (AAC ADTS) в MP4 без `-bsf:a aac_adtstoasc` может давать невалидный аудиопоток на части сборок FFmpeg.
- **План исправления:**
  1. Ставить `-ss` **до** `-i` (для быстрого keyframe-выровненного поиска по concat-списку) либо не обрезать `offset` внутри первого сегмента при `-c copy`, передавая точный `clip.start_time = offset` / `clip.end_time = offset + clip_duration` уже в `render_clip()` (который и так перекодирует клип в 9:16 с покадровой точностью).
  2. Добавить `-bsf:a aac_adtstoasc` (при наличии AAC) и проверять наличие видеопотока в извлечённом фрагменте с автоматическим фолбэком на быстрое перекодирование (`-c:v libx264 -preset ultrafast`), если stream copy дал пустое видео.

---

### [BUG-23] `CRITICAL` `[FIXED]` — Сломанная production-сборка Tauri: отсутствует `requirements-cpu.txt` в ресурсах и запись данных в read-only каталог установки
- **Файл и строка:** `src-tauri/tauri.conf.json:29-35`, `src-tauri/src/lib.rs:110-121, 129-144`, `backend/requirements.txt:6-8`, `backend/app/core/config.py:14-23`
- **Суть проблемы:**
  1. `backend/requirements.txt` делегирует зависимости через `-r requirements-cpu.txt`, но в `src-tauri/tauri.conf.json` в `bundle.resources` указан только `"../backend/requirements.txt"` — файл `requirements-cpu.txt` не попадёт в собранный дистрибутив.
  2. `spawn_backend_process()` в `src-tauri/src/lib.rs` не передаёт переменную окружения `NAREZKI_DATA_DIR`, а `open_backend_log_stdio()` пишет лог в `backend_dir.join("data").join("logs")`. После установки приложения в системную директорию (`C:\Program Files\Narezki\` на Windows или `/usr/lib/narezki/` на Linux) каталог `backend/` доступен только на чтение — SQLite (`narezki.db`), логи, прокси и временные файлы не смогут создаваться (`PermissionError`).
- **План исправления:**
  1. Добавить `"../backend/requirements-cpu.txt"` (и при необходимости платформенные бинарники) в `bundle.resources` в `tauri.conf.json`.
  2. В `src-tauri/src/lib.rs` в production-сборке (`#[cfg(not(debug_assertions))]`) вычислять пользовательскую директорию через `app.path().app_data_dir()`, передавать её бэкенду через `.env("NAREZKI_DATA_DIR", &data_dir)` и писать `backend.log` в `data_dir.join("logs")`.

---

### [BUG-24] `MAJOR` — Сломанный OAuth2 (YouTube / TikTok / Instagram) в Tauri: случайный порт в `redirect_uri` и `window.open()` вместо системного браузера
- **Файл и строка:** `backend/app/api/publishing.py:139, 179`, `src-tauri/src/lib.rs:37-42`, `src/pages/SettingsPage.tsx:129-138`
- **Суть проблемы:**
  1. Tauri при каждом запуске выбирает случайный свободный порт (`TcpListener::bind("127.0.0.1:0")`). Эндпоинты `/api/publishing/oauth/{platform}/url` и `/callback` формируют `redirect_uri` из `str(request.base_url)` (например, `http://127.0.0.1:54321/...`), игнорируя `settings.publish_public_base_url`. Поскольку YouTube, TikTok и Instagram требуют точного совпадения заранее зарегистрированного `redirect_uri` (а TikTok/Instagram — ещё и HTTPS), OAuth2-авторизация будет стабильно падать с `redirect_uri_mismatch`.
  2. На фронтенде `SettingsPage.tsx:133` открывает OAuth-ссылку через `window.open(url, "_blank", "noopener,noreferrer")`. В Tauri WebView2 это открывает встроенное окно WebView, в котором Google OAuth блокирует вход с ошибкой `403: disallowed_useragent`.
- **План исправления:**
  1. Формировать `redirect_uri` на основе `settings.publish_public_base_url` (если он задан и не дефолтный) или фиксированного настраиваемого OAuth-порта, и отображать точный `redirect_uri` в UI настроек для копирования в консоль разработчика.
  2. Для открытия внешних ссылок (OAuth и `item.remote_url`) в Tauri использовать команду Rust (`open_external_url` / `@tauri-apps/plugin-opener`), открывающую системный браузер по умолчанию, с фолбэком на `window.open` только вне Tauri.

---

### [BUG-25] `MAJOR` — Кнопка «В Студию» в Live Monitor открывает пустую Студию (`project === null`), а завершение фоновой задачи подменяет клипы чужого проекта
- **Файл и строка:** `backend/app/services/live/live_watcher.py:1080-1085`, `src/pages/LiveMonitorPage.tsx:256-260`, `src/store/useAppStore.ts:147-161, 395-405`
- **Суть проблемы:**
  1. `capture_clip_window()` создаёт новый `Project(source_type="live")` в БД, но вызывает только `emit_clip_update` и `emit_live_update`, **не вызывая `emit_project_update`**. В результате массив `projects` в Zustand не содержит нового Live-проекта. Когда пользователь нажимает «В Студию» (`handleOpenClipInStudio`), `ClipStudioPage` ищет `projects.find(p => p.id === selectedProjectId)`, получает `null` и показывает заглушку «Выберите проект» вместо клипа.
  2. В `useAppStore.ts:155-156` обработчик `job_update` (`status === "completed"`) для `live_process_clip`, `render_preview`, `export_clip` и т.д. делает `const pid = job.project_id || get().selectedProjectId; if (pid) void get().refreshClips(pid);` **без проверки `pid === get().selectedProjectId`**. Если пользователь редактирует в Студии проект A, а в фоне завершился `live_process_clip` проекта B, `state.clips` перезапишется клипами проекта B поверх видео проекта A.
  3. В `refreshClips(projectId)` нет защиты от гонки ответов: при быстром переключении проектов A -> B запоздалый ответ `listClips(A)` перезапишет клипы проекта B.
- **План исправления:**
  1. В `live_watcher.py` после создания Live-проекта вызывать `await ws_manager.emit_project_update(project_id, "ready")`, а в `handleOpenClipInStudio()` предварительно вызывать `await refreshProjects()` (если `projectId` отсутствует в `projects`).
  2. В `useAppStore.ts:155` вызывать `refreshClips(pid)` только если `pid === get().selectedProjectId`.
  3. В `refreshClips(projectId)` после `await api.listClips(projectId)` проверять `if (get().selectedProjectId !== projectId) return;`.

---

### [BUG-26] `MAJOR` — Утечка дочерних процессов (`ffmpeg` / `yt-dlp` / `streamlink`) при таймаутах и `asyncio.CancelledError`
- **Файл и строка:** `backend/app/services/live/live_watcher.py:156-172, 300-335, 492-501`, `backend/app/services/renderer/ffmpeg_runner.py:131-168`, `backend/app/services/analyzer/audio_peaks.py:82-120`, `backend/app/services/ingest/proxy.py:140-171`
- **Суть проблемы:**
  1. В `live_watcher.py` (`_run_yt_dlp_json`, `resolve_stream_urls`, `extract_window`) используется `await asyncio.wait_for(proc.communicate(), timeout=...)`. При срабатывании `asyncio.TimeoutError` корутина `communicate()` отменяется, но сам процесс `proc` **не убивается и не ожидается** (`proc.kill()` / `await proc.wait()` отсутствуют) — зависшие процессы `yt-dlp`, `streamlink` и `ffmpeg` остаются висеть в системе.
  2. В `run_ffmpeg_with_progress()` (`ffmpeg_runner.py`), `analyze_audio_peaks()` (`audio_peaks.py`) и `generate_proxy()` (`proxy.py`) проверка `cancel_event` стоит только в начале цикла `while`. Если `asyncio.CancelledError` приходит во время ожидания `await proc.stdout.readline()` / `read()`, блок `finally:` не убивает `proc` и не вызывает `await proc.wait()`, оставляя зомби-процессы (на Linux) или заблокированный выходной файл (на Windows).
- **План исправления:** Во всех перечисленных местах добавить в `except (asyncio.TimeoutError, asyncio.CancelledError):` (или в `finally:` при `proc.returncode is None`) гарантированное `with suppress(Exception): proc.kill()` и `with suppress(Exception): await proc.wait()`.

---

### [BUG-27] `MAJOR` — Краш Live Monitor на потоке без аудиодорожки и молчаливое проглатывание всех ошибок авто-клиппинга
- **Файл и строка:** `backend/app/services/live/live_watcher.py:667-683, 928-941`
- **Суть проблемы:**
  1. В `LiveStreamSession._run()` FFmpeg всегда запускается с двумя выходами: сегментный MPEG-TS буфер и сырой PCM-поток (`-vn -ac 1 -ar 16000 -f s16le pipe:1`). Подтверждено E2E-прогоном: если стрим (или тестовый источник) не содержит аудиодорожки, FFmpeg немедленно падает с `Output file does not contain any stream / Error opening output file pipe:1`.
  2. В `_detector_loop()` (строки 931–940) `self._last_clip_sec = latest_buf_sec` обновляется **до** вызова `capture_clip_window()`, а сам вызов обёрнут в `with contextlib.suppress(Exception):`. Любой сбой вырезки (ошибка FFmpeg, нехватка места, ошибка БД) полностью скрывается без единой записи в логах, при этом кулдаун `min_clip_gap_sec` блокирует следующие попытки клиппинга.
- **План исправления:**
  1. Использовать `-map 0:a:0?` или предварительно проверять наличие аудио в потоке, чтобы на video-only стримах отключать второй выход PCM без падения записи буфера.
  2. Заменить `contextlib.suppress(Exception)` на `try / except Exception as exc:` с логированием через `logger.warning` и `ws_manager.log(..., level="warning", stage="live")`, а `self._last_clip_sec` обновлять только после успешного `capture_clip_window()`.

---

### [BUG-28] `MAJOR` — Блокировка asyncio event loop синхронным I/O и неограниченный рост `segments.csv` в Live Monitor
- **Файл и строка:** `backend/app/services/live/live_watcher.py:357-420, 556-571, 865-866`, `backend/app/api/live.py:123`
- **Суть проблемы:**
  1. `RollingBufferManager.prune_old_segments()` удаляет старые `.ts`-файлы с диска, но **никогда не очищает** файл `segments.csv` (FFmpeg дописывает туда строки бесконечно). При длительном стриме `segments.csv` разрастается до тысяч строк.
  2. `LiveStreamSession.snapshot()` синхронно вызывает `get_latest_time()`, `get_earliest_time()` (дважды полностью перечитывая `segments.csv` и вызывая `seg_path.exists()` для каждой удалённой строки!) и `get_disk_usage_mb()` (`stat()` по всем `.ts`) прямо в главном потоке asyncio каждые 2 секунды (`_detector_loop`) и на каждый HTTP-запрос `GET /api/live/channels`.
  3. В `backend/app/api/live.py:123` удаление буфера канала выполняется синхронным `shutil.rmtree(buf_dir, ignore_errors=True)` внутри `async def delete_live_channel`, блокируя event loop на гигабайтных буферах.
- **План исправления:**
  1. Добавить в `FFmpeg` флаг `-segment_list_size` (например, `(buffer_minutes * 60 // segment_seconds) + 20`), чтобы FFmpeg сам ограничивал размер списка сегментов, либо кэшировать/обновлять индекс сегментов в памяти в фоне через `asyncio.to_thread`.
  2. В `snapshot()` вызывать `scan_segments()` не более одного раза (или читать кэшированный снапшот, обновляемый в фоне).
  3. В `api/live.py:123` заменить синхронный `shutil.rmtree` на `await asyncio.to_thread(shutil.rmtree, buf_dir, True)`.

---

### [BUG-29] `MAJOR` — Ложный статус `posted` при таймауте TikTok/Instagram, перезапись отмены (`cancelled`) и `TypeError` на `expires_at=None`
- **Файл и строка:** `backend/app/services/live/publisher.py:297, 438-440, 490-507, 589-605, 845-884`, `backend/app/api/publishing.py:259-270`
- **Суть проблемы:**
  1. В `upload_tiktok_video()` (строки 490–502) цикл опроса статуса делает 60 итераций по 2 секунды; если за 120 секунд статус так и не стал `PUBLISH_COMPLETE`, цикл завершается **без исключения**, и элемент очереди ошибочно помечается как `"posted"`. Аналогично в `upload_instagram_reel()` (строки 589–605): при истечении 120 секунд ожидания обработки код без проверки пытается вызвать `media_publish`, падая с неинформативной ошибкой API.
  2. Если пользователь отменяет публикацию (`POST /api/publishing/queue/{id}/cancel`), пока элемент находится в статусе `"posting"`, `_publish_one()` после завершения загрузки или ошибки (строки 845–884) не проверяет текущий статус строки в БД и перезаписывает `"cancelled"` обратно в `"posted"` или `"scheduled"`.
  3. В `upload_youtube_short()` (строка 297) и `upload_tiktok_video()` (строка 395) выражение `float(creds.get("expires_at", 0))` падает с `TypeError`, если в `credentials_json` передано `"expires_at": null` или `""`. При `size == 0` (пустой файл) в `upload_tiktok_video()` формируется некорректный заголовок `Content-Range: bytes 0--1/0`.
- **План исправления:**
  1. Явно отслеживать флаг завершения обработки в циклах поллинга TikTok и Instagram и выбрасывать `RuntimeError("Таймаут обработки видео на стороне платформы")` по истечении лимита.
  2. Перед записью статуса в `_publish_one()` проверять `if row.status == "cancelled": return` (или запрещать отмену элемента в статусе `posting` с возвратом HTTP 409).
  3. Использовать безопасный парсер `float(creds.get("expires_at") or 0)` и проверять `size > 0` перед загрузкой.

---

### [BUG-30] `MAJOR` — HTTP 500 (`IntegrityError`) при передаче `null` в `PATCH`-эндпоинтах `live`, `publishing`, `presets` и `clips`
- **Файл и строка:** `backend/app/api/live.py:93-105`, `backend/app/api/publishing.py:106-113`, `backend/app/api/presets.py:72-80, 237-252`, `backend/app/api/clips.py:81-93`
- **Суть проблемы:** В `PATCH /api/live/channels/{id}`, `PATCH /api/publishing/accounts/{id}`, `PATCH /api/presets/subtitles/{id}` и `PATCH /api/presets/ads/{id}` используется `updates = body.model_dump(exclude_unset=True)` с последующим `setattr(obj, key, val)` без фильтрации `None` для не-nullable столбцов SQLite (`name`, `enabled`, `font_size`, `ad_type`, `post_gap_minutes` и др.). Передача явного `null` приводит к `sqlalchemy.exc.IntegrityError` (HTTP 500). В `backend/app/api/clips.py:81-93` список `_NON_NULLABLE_UPDATE_FIELDS` неполный — в нём пропущены `NOT NULL` поля `virality_score`, `category`, `reason` и `subtitles_json`.
- **План исправления:** Во всех `PATCH`-эндпоинтах отбрасывать ключи со значением `None` для всех не-nullable колонок (либо инспектировать `col.nullable` из SQLAlchemy `inspect(model).columns`), дополнив `_NON_NULLABLE_UPDATE_FIELDS` в `clips.py`.

---

### [BUG-31] `MAJOR` — Гонка завершения GPU-потоков при `QueueManager.stop()` и удаления проекта при активном `yt-dlp`
- **Файл и строка:** `backend/app/core/queue_manager.py:198-211, 382-395`, `backend/app/services/ingest/downloader.py:93`
- **Суть проблемы:**
  1. При остановке приложения (`QueueManager.stop()`) отменяются и `_active_tasks`, и `_worker_tasks`. В `_execute_job()` блок `except (JobCancelledError, asyncio.CancelledError):` не ожидает завершения `handler_task`, поэтому `stop()` возвращает управление до того, как `run_cancellable_thread()` дождётся кооперативного выхода Whisper/YOLOv8. Следующая строка в `main.py:111` вызывает `release_vram_async("shutdown")` параллельно с активным GPU-потоком.
  2. `download_video_url()` запускает `yt-dlp` через `asyncio.to_thread(_run_download)` вместо `run_cancellable_thread`. При удалении проекта во время скачивания `delete_project` сразу запускает `shutil.rmtree(proj_dir)`, пока поток `yt-dlp` всё ещё пишет `source.mp4` в эту же папку (на Windows — `PermissionError` и остатки недокачанных файлов).
- **План исправления:**
  1. В `QueueManager.stop()` после `task.cancel()` дожидаться завершения активных `handler_task` через `await asyncio.gather(*active, return_exceptions=True)` (с разумным таймаутом).
  2. В `downloader.py` использовать `run_cancellable_thread(_run_download, cancel_event=cancel_event)`.

---

### [BUG-32] `MAJOR` — Несброшенный PTS до `trim`/`overlay` и отсутствие проверки видеопотока у `video_insert` в `ffmpeg_runner.py`
- **Файл и строка:** `backend/app/services/renderer/ffmpeg_runner.py:369-384`, `backend/app/services/renderer/ad_injector.py:71-75`
- **Суть проблемы:**
  1. В `ffmpeg_runner.py:376-380` фильтры `trim=duration={clip_dur:.3f}` и `atrim=duration={clip_dur:.3f}` стоят **до** `setpts=PTS-STARTPTS` / `asetpts=PTS-STARTPTS`. Если входной файл после `-ss` имеет ненулевой начальный PTS (типично для MPEG-TS / live-записей), `trim=duration=...` обрежет клип раньше времени.
  2. В `ad_injector.py:71-75` цепочка `ov_chain` для рекламного оверлея не содержит `setpts=PTS-STARTPTS`, из-за чего у `.webm`/`.mp4` оверлеев с ненулевым начальным PTS сдвигаются тайминги `fade` и `overlay`.
  3. В `ffmpeg_runner.py:370` вызов `ad_meta = await probe_media(ad_file)` для `video_insert` не передаёт `require_video=True` — при подстановке битого или audio-only файла FFmpeg падает с неочевидной ошибкой графа фильтров.
- **План исправления:**
  1. Ставить `setpts=PTS-STARTPTS` / `asetpts=PTS-STARTPTS` **до** `trim`/`atrim` (или использовать `setpts=PTS-STARTPTS,trim=duration=...,setpts=PTS-STARTPTS`).
  2. Добавить `setpts=PTS-STARTPTS` в начало `ov_chain` в `build_overlay_filtergraph()`.
  3. Передавать `require_video=True` в `probe_media(ad_file, require_video=True)` для `video_insert`.

---

### [BUG-33] `MINOR` — Прыгающий прогресс-бар в `handle_export_batch` и пропуск валидации устаревшего трекинга в `_render_or_export_single`
- **Файл и строка:** `backend/app/services/renderer/handlers.py:297, 464-487`
- **Суть проблемы:**
  1. В `handle_export_batch()` клипы экспортируются параллельно через `asyncio.gather()`, но каждый `_export_one` передаёт в `_render_or_export_single` свой `progress_offset = idx * span`. В результате при 2+ воркерах `ctx.update_progress()` получает немонотонные значения (`12% -> 58% -> 18% -> 64%`), и прогресс-бар в UI скачет вперёд-назад.
  2. В `_render_or_export_single()` строка 297 проверяет только `not clip_dict.get("track_data_json")`. Если `track_data_json` присутствует, но содержит пустой `points: []` или смещённые границы `clip_start`/`clip_end` (на 0.3–0.5 с), проверка внутри `_ensure_clip_tracking()` вообще не вызывается.
- **План исправления:**
  1. В `handle_export_batch()` хранить массив `per_clip_progress = [0.0] * total` и передавать в `_render_or_export_single` колбэк, который обновляет `per_clip_progress[idx]` и отправляет `sum(per_clip_progress) / total`.
  2. В `_render_or_export_single()` всегда делегировать проверку валидности трекинга в `_ensure_clip_tracking()` при `clip.crop_mode == "auto_track"`.

---

### [BUG-34] `MINOR` — Необработанные промисы в `SettingsPage.tsx`, мелкие расхождения статусов, утечка старых файлов рекламы и кавычки `explorer.exe` на Windows
- **Файлы и строки:**
  - `src/pages/SettingsPage.tsx:932-957`: вызовы `api.retryPublishItem`, `api.cancelPublishItem` и `api.deletePublishItem` не имеют `.catch(...)` — ошибки уходят в unhandled promise rejection без уведомления пользователя.
  - `backend/app/services/analyzer/pipeline.py:286-289`: при отмене анализа БД восстанавливает `proj.status = "analyzed" if proj.analysis_json else "ready"`, но WebSocket-событие на строке 289 всегда отправляет `"ready"`.
  - `backend/app/services/live/live_watcher.py:1218-1231`: при сетевой/API ошибке в `_poll_loop` статус канала выставляется в `"offline"` вместо `"error"` (вразрез с `check_channel_now` на строке 1176), а исключения молча глотаются через `contextlib.suppress(Exception)`.
  - `backend/app/api/presets.py:242-248`: при обновлении `asset_path` в `update_ad_preset` старый файл рекламы в `ADS_DIR` остаётся на диске навсегда.
  - `src-tauri/src/lib.rs:250`: `cmd.arg(format!("/select,{}", clean))` на Windows при наличии пробелов в пути оборачивается Rust в `"/select,C:\path with spaces\file.mp4"`, что `explorer.exe` не распознаёт (открывает «Документы» вместо выделения файла).
- **План исправления:**
  - Добавить `try/catch` с `addToast({ type: "error", ... })` для действий очереди публикаций в `SettingsPage.tsx`.
  - В `pipeline.py:289` передавать фактический `restored_status` в `ws_manager.emit_project_update`.
  - В `live_watcher.py:1224` выставлять `"error"` при наличии `probe.get("error")` и логировать ошибки поллинга.
  - В `presets.py:242` удалять предыдущий файл в `ADS_DIR` после успешного коммита нового `asset_path`.
  - В `src-tauri/src/lib.rs:250` использовать `cmd.raw_arg(format!("/select,\"{}\"", clean))` на Windows (из `std::os::windows::process::CommandExt`).

---

## Архив исправленных багов (Раунд 1: `BUG-01` — `BUG-20` — `[FIXED]`)

Все 20 проблем первого аудита исправлены и покрыты регрессионными тестами (`backend/tests/test_bug_fixes_regression.py` — 14/14 PASSED):
- **`BUG-01` `[FIXED]`** (`CRITICAL`): выбор `.exe` на Linux/macOS в `config.py` — добавлена проверка `sys.platform == "win32"`.
- **`BUG-02` `[FIXED]`** (`CRITICAL`): краш `faster-whisper` при `device="cpu"` + `compute_type="float16"` — добавлен автофолбэк на `int8`.
- **`BUG-03` `[FIXED]`** (`CRITICAL`): экранирование `'` в пути `.ass` и запятых в `font_name` — исправлено в `subtitle_ass.py`.
- **`BUG-04` `[FIXED]`** (`CRITICAL`): гонка GPU-семафора при отмене `to_thread` — добавлен `run_cancellable_thread()` в `cuda_env.py`.
- **`BUG-05` `[FIXED]`** (`MAJOR`): рассинхрон sample rate после `loudnorm`, `setpts` на рекламе и AV1 WebM — исправлено в `ad_injector.py`.
- **`BUG-06` `[FIXED]`** (`MAJOR`): масштабирование `mid_roll` и `fps` при Anti-CID `speed != 1.0` — исправлено в `anti_cid.py` / `ffmpeg_runner.py`.
- **`BUG-07` `[FIXED]`** (`MAJOR`): залипание статуса клипа при отмене — добавлен `_restore_clip_status_on_cancel()` в `handlers.py`.
- **`BUG-08` `[FIXED]`** (`MAJOR`): потеря WS-подписок после рестарта бэкенда — вынесено в `ensureRealtimeSubscriptions()` (`useAppStore.ts`).
- **`BUG-09` `[FIXED]`** (`MAJOR`): сброс несохранённых правок в Студии при WS-событиях — добавлен `loadedClipIdRef` (`ClipStudioPage.tsx`).
- **`BUG-10` `[FIXED]`** (`MAJOR`): невозможность ввести пробел в хэштегах — добавлено состояние `tagsInput` (`ClipStudioPage.tsx`).
- **`BUG-11` `[FIXED]`** (`MAJOR`): кэширование старого превью в `<video>` — добавлен `&v=updated_at` и `Cache-Control: no-cache`.
- **`BUG-12` `[FIXED]`** (`MAJOR`): `"N/A"` и audio-only файлы в `ffprobe_utils.py` — добавлены `_safe_float`/`_safe_int` и `require_video=True`.
- **`BUG-13` `[FIXED]`** (`MAJOR`): `null` в `PATCH /api/clips` (частично, см. `BUG-30`), очистка файлов клипа и удаление проекта во время анализа.
- **`BUG-14` `[FIXED]`** (`MAJOR`): кроссплатформенный запуск/остановка Python в Tauri (`python3`, `PYTHONUTF8`, `process_group`).
- **`BUG-15` `[FIXED]`** (`MAJOR`): добавление `torch` (CUDA 12.4) в `requirements.txt` и выделение `requirements-cpu.txt`.
- **`BUG-16` `[FIXED]`** (`MINOR`): ретраи LLM только на `BadRequestError`, проброс `min_duration`, клампинг `duration < 1s`.
- **`BUG-17` `[FIXED]`** (`MINOR`): гонка `set_render_concurrency` — заменён на `_DynamicSemaphore`.
- **`BUG-18` `[FIXED]`** (`MINOR`): параллельный `export_batch` с изоляцией ошибок и уникальными именами.
- **`BUG-19` `[FIXED]`** (`MINOR`): закрытие предыдущего WebSocket при смене порта в `WSClient.connect()`.
- **`BUG-20` `[FIXED]`** (`MINOR`): настройка ESLint, `try/catch` в Студии/Пресетах, опция `"base"` в Whisper, разбор `errors` при 422, асинхронный `shutil.rmtree` в `projects.py`.
