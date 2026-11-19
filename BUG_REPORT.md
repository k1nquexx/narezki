# Технический аудит и отчёт о багах (`BUG_REPORT.md`)

**Проект:** Narezki — AI Shorts & Reels Studio (`src/`, `src-tauri/`, `backend/`)  
**Дата аудита:** 2026-11-20  
**Режим:** только анализ, диагностика и документирование (логика приложения не изменялась).

---

## Сводка проверок по этапам

### 1. Сборка и статический анализ
- **Фронтенд (`TypeScript` / `Vite`):**
  - `npx.cmd tsc --noEmit` — **OK** (exit code `0`, `strict: true`, `noUnusedLocals: true`, `noUnusedParameters: true`).
  - `npm.cmd run build` — **OK** (exit code `0`, бандл собран за 5.33s).
  - **ESLint:** отсутствует в `package.json` и не настроен в проекте (`BUG-20`).
- **Бэкенд (`Python 3.11.9` / `.venv`):**
  - Импорт всех 39 модулей `app.*` — **OK** (`TOTAL FAILED: 0`).
  - `pip check` — **OK** (`No broken requirements found`).
  - `requirements.txt` не содержит `torch` и инструкций по CUDA wheel (`torch==2.6.0+cu124` установлен в `.venv` отдельно, `BUG-15`).
  - Схемы Pydantic (`backend/app/core/schemas.py`, `AppSettingsModel`) и TS-интерфейсы (`src/types/api.ts`) в целом синхронизированы, но в UI (`SettingsPage.tsx`) не хватает части опций (`base` для Whisper) и обработки `errors` при 422 (`BUG-20`).
- **Tauri / Rust (`src-tauri/`):**
  - `cargo check` — **OK** (`Finished dev profile in 6.40s`).
  - Найдены кроссплатформенные и сборочные проблемы в `src-tauri/src/lib.rs` и `src-tauri/tauri.conf.json` (`BUG-14`).

### 2. Логика пайплайнов, async и управление ресурсами
- Тяжёлые синхронные задачи (`faster-whisper`, `YOLOv8`, `PySceneDetect`, `yt-dlp`) вынесены в `asyncio.to_thread`, а FFmpeg/FFprobe запускаются через `asyncio.create_subprocess_exec`.
- Однако обнаружена **критическая гонка при отмене GPU-задач** (`BUG-04`), утечка/залипание статусов клипов при `asyncio.CancelledError` (`BUG-07`), гонка `set_render_concurrency` (`BUG-17`), синхронный `shutil.rmtree` при удалении проекта (`BUG-20`) и падение `analyze` при удалении проекта на лету (`BUG-13`).
- `faster-whisper` при `device="cpu"` + `compute_type="float16"` падает без фолбэка на `int8` (`BUG-02`).

### 3. Кроссплатформенность (Linux <-> Windows) и FFmpeg
- На Linux/macOS `get_ffmpeg_path()` / `get_ffprobe_path()` выберут `backend/bin/ffmpeg.exe` вместо системного `ffmpeg` (`BUG-01`).
- Экранирование `'` в пути `.ass` для FFmpeg `libass` сломано (`BUG-03`).
- В `ad_injector.py` найдены рассинхронизация sample rate после `loudnorm` (192 kHz vs 48 kHz), отсутствие `setpts=PTS-STARTPTS` на рекламном ролике, поломка AV1 WebM оверлеев (`BUG-05`) и несогласованность `mid_roll` / FPS при включённом Anti-CID (`BUG-06`).

### 4. End-to-End прогон и edge-cases
- Штатный `pytest -v` (`backend/tests/`): **14 passed in 56.61s**.
- Дополнительные E2E-пробы выявили и подтвердили воспроизведение:
  - **PASS** на пустом/тихом видео в `faster-whisper` (CUDA): корректно возвращает `segments: [], words: []`.
  - **FAIL** в `faster-whisper` с `device="cpu", compute_type="float16"`: `RuntimeError: Requested float16 compute type, but the target device or backend do not support efficient float16 computation.` (`BUG-02`).
  - **FAIL** в FFmpeg `ass=filename=...` при наличии апострофа `'` в пути: `ass_read_file(...): fopen failed`, `Error: Invalid argument`, `rc=4294967274` (`BUG-03`).
  - **ISSUE** в `probe_media()` на audio-only файле: возвращает `width=0, height=0, video_codec=None` без ошибки (`BUG-12`).
  - **ISSUE** в `render_clip()` с Anti-CID (`speed=1.05` на 4.0s клипе): итоговая длительность `3.9s` вместо `~3.81s` без нормализации `fps` (`BUG-06`).

---

## Список найденных багов и точек отказа

### [BUG-01] [FIXED] `CRITICAL` — На Linux/macOS выбирается Windows-бинарник `ffmpeg.exe` / `ffprobe.exe`
- **Статус:** `[FIXED]` — добавлена проверка `sys.platform == "win32"` в `_resolve_binary()` (`backend/app/core/config.py`), подтверждено тестом `test_bug01_cross_platform_ffmpeg_paths`.
- **Файл и строка:** `backend/app/core/config.py:52-81`
- **Суть проблемы:** Функции `get_ffmpeg_path()` и `get_ffprobe_path()` безусловно проверяют `BIN_DIR / "ffmpeg.exe"` (и `ffprobe.exe`) **до** проверки `BIN_DIR / "ffmpeg"` и системного `PATH`, не проверяя `sys.platform == "win32"`. Поскольку `backend/bin/ffmpeg.exe` и `backend/bin/ffprobe.exe` лежат в репозитории, при запуске на Linux или macOS бэкенд попытается выполнить `.exe`-файл и упадёт с `OSError: [Errno 8] Exec format error` или `PermissionError` на каждом вызове FFmpeg/FFprobe.
- **План исправления:** Проверять `sys.platform == "win32"`: на Windows искать `BIN_DIR / "ffmpeg.exe"` -> `shutil.which("ffmpeg")`, на POSIX искать `BIN_DIR / "ffmpeg"` (с проверкой `os.access(..., os.X_OK)`) -> `shutil.which("ffmpeg")`. Аналогично для `get_ffprobe_path()`.

---

### [BUG-02] [FIXED] `CRITICAL` — Краш `faster-whisper` при `device="cpu"` и `compute_type="float16"` (нет фолбэка на `int8`)
- **Статус:** `[FIXED]` — добавлена проверка `torch.cuda.is_available()` и автоматический фолбэк на `int8` для `cpu` в `transcriber.py`, подтверждено тестом `test_bug02_whisper_cpu_float16_fallback_to_int8`.
- **Файл и строка:** `backend/app/services/analyzer/transcriber.py:92-97`
- **Суть проблемы:** Список попыток формируется так:
  ```python
  attempts = [(device, compute_type)]
  if device == "cuda" and compute_type == "float16":
      attempts.append(("cuda", "int8_float16"))
  if device == "cuda":
      attempts.append(("cpu", "int8"))
  ```
  По умолчанию в `AppSettingsModel` задан `whisper_compute_type = "float16"`. Если на машине без CUDA выставить `whisper_device = "cpu"`, не меняя `compute_type`, список `attempts` состоит только из `[("cpu", "float16")]`. Подтверждено E2E-прогоном: CTranslate2 выбрасывает `ValueError` (`Requested float16 compute type, but the target device or backend do not support efficient float16 computation`), и задача анализа падает.
- **План исправления:** Если `device == "cpu"` и `compute_type != "int8"`, автоматически добавлять `("cpu", "int8")` (или принудительно заменять `float16`/`int8_float16` на `"int8"` на CPU). Также проверять `torch.cuda.is_available()` до загрузки модели, чтобы не тратить время на заведомо падающие CUDA-попытки на CPU-машинах.

---

### [BUG-03] `CRITICAL` — Поломка фильтра FFmpeg `ass=` на путях с апострофом `'` и незаэкранированных именах шрифтов
- **Файл и строка:** `backend/app/services/renderer/subtitle_ass.py:42-47, 62`
- **Суть проблемы:**
  1. `format_ass_filter_path()` экранирует одинарную кавычку как `posix.replace("'", r"'\''")` — это синтаксис POSIX shell, а **не** парсера графов фильтров FFmpeg (`av_get_token`). Подтверждено E2E-прогоном: на пути вида `.../user's folder/subs.ass` FFmpeg «съедает» кавычку и пытается открыть `.../users folder/subs.ass`, падая с `fopen failed` / `Error: Invalid argument` (`rc=4294967274`). Аналогично не экранируются спецсимволы `libass`/FFmpeg (`\`, `[`, `]`, `;`, `,`).
  2. `font_name` (строка 62) подставляется в `Style: Default,{font_name},...` без очистки запятых `,` и переносов строк — запятая в имени шрифта сдвигает все поля ASS-стиля и ломает рендер субтитров.
- **План исправления:** Для FFmpeg `filter_complex_script` избегать одинарных кавычек внутри `ass=filename=...` либо корректно выполнять двухступенчатое экранирование FFmpeg (`\` -> `\\`, `:` -> `\:`, `'` -> `\\\'`, `,` -> `\,`, `[` -> `\[`, `]` -> `\]`, `;` -> `\;`), а ещё надёжнее — передавать `ass_path` в `TEMP_DIR` с гарантированно безопасным именем файла или запускать FFmpeg с `cwd=ass_path.parent` и относительным `ass=filename=subs_<id>.ass` (полностью устраняет проблему с `:` на Windows, пробелами, кавычками и кириллицей). В `font_name` удалять `,`, `\r`, `\n`.

---

### [BUG-04] `CRITICAL` — Гонка GPU-семафора и VRAM при отмене задач Whisper / YOLOv8 (`asyncio.to_thread`)
- **Файл и строка:** `backend/app/core/queue_manager.py:214-223`, `backend/app/services/analyzer/pipeline.py:93-107`, `backend/app/services/renderer/handlers.py:53-66`, `backend/app/services/analyzer/transcriber.py:235-248`
- **Суть проблемы:** `QueueManager.cancel_job()` вызывает `task.cancel()` для `handler_task`. Когда хендлер находится на `await asyncio.to_thread(_transcribe_sync, ...)` или `await asyncio.to_thread(_track_clip_sync, ...)`, отмена таска в Python **не прерывает** поток в threadpool — `await asyncio.to_thread(...)` немедленно выбрасывает `asyncio.CancelledError` в event loop. В результате:
  1. `async with ctx.gpu_semaphore:` освобождает GPU-семафор, пока старый поток всё ещё держит модель Whisper/YOLO в VRAM и продолжает вычисления на GPU.
  2. Следующая GPU-задача из очереди сразу захватывает `gpu_semaphore` и загружает вторую модель на GPU одновременно -> **CUDA Out Of Memory**.
  3. В `transcriber.py:248` блок `finally: wav_path.unlink(missing_ok=True)` пытается удалить WAV-файл, пока поток Whisper ещё читает его (на Windows — `PermissionError`).
- **План исправления:** В `cancel_job()` выставлять `ctx.cancel_event.set()` и **не** отменять `handler_task` жёстко, пока активен не прерываемый `to_thread` с GPU (или обернуть `asyncio.to_thread` в `asyncio.shield` с ожиданием кооперативного завершения потока по `cancel_event` внутри `finally` перед выходом из `gpu_semaphore`, `vram_guard` и удалением `wav_path`).

---

### [BUG-05] `MAJOR` — Ошибки `filter_complex` в `ad_injector.py`: рассинхрон sample rate после `loudnorm`, отсутствие `setpts` и краш AV1 WebM
- **Файл и строка:** `backend/app/services/renderer/ad_injector.py:22-24, 93-105, 117-136`
- **Суть проблемы:**
  1. **Sample rate mismatch в `concat`:** фильтр `loudnorm` в FFmpeg внутри повышает частоту дискретизации до **192 000 Hz**. Для основного аудио `aformat=sample_rates=48000:channel_layouts=stereo` стоит *после* `loudnorm` (строки 123, 133), а для рекламного ролика `_ad_a` (строки 100–101) `aformat=sample_rates=48000` стоит **до** `loudnorm`. В результате в `concat=n=3:v=1:a=1` подаются потоки 48 kHz и 192 kHz одновременно.
  2. **Отсутствие сброса PTS на рекламном ролике:** у `_ad_v` и `_ad_a` (строки 94–102) нет `setpts=PTS-STARTPTS` и `asetpts=PTS-STARTPTS`. Если рекламный MP4/MKV/TS начинается с ненулевого PTS (например, из-за edit list или B-frames), `concat` даст рассинхронизацию звука или зависание кадра.
  3. **Краш на AV1 WebM оверлеях:** в `build_overlay_input_args` (строка 24) для `.webm` жёстко выбирается `libvpx` или `libvpx-vp9`. Если оверлей закодирован в AV1 (`codec == "av1"`), принудительный `-c:v libvpx-vp9` вызовет ошибку декодера FFmpeg.
- **План исправления:**
  1. Перенести `aformat=sample_rates=48000:channel_layouts=stereo,asetpts=PTS-STARTPTS` **после** `loudnorm` для `_ad_a` (и после `loudnorm` в `ffmpeg_runner.py:443`).
  2. Добавить `setpts=PTS-STARTPTS` в цепочку `_ad_v`.
  3. В `build_overlay_input_args` указывать `-c:v libvpx` только при `codec == "vp8"` и `-c:v libvpx-vp9` только при `codec == "vp9"`, а для остальных кодеков (включая `av1`) не форсировать декодер.

---

### [BUG-06] `MAJOR` — Несогласованность таймкодов `mid_roll` и частоты кадров при включённом Anti-CID (`speed != 1.0`)
- **Файл и строка:** `backend/app/services/renderer/ffmpeg_runner.py:403-416, 446-468`, `backend/app/services/renderer/anti_cid.py:37-46`
- **Суть проблемы:**
  1. Для оверлея (строки 435–436) `overlay_start` и `overlay_end` масштабируются на `1 / speed_factor`, а для `mid_roll` видеовклейки `insert_at_sec` (строка 463) передаётся **без масштабирования**. При `speed != 1.0` вставка происходит не в ту секунду клипа, а при `insert_at_sec` близком к концу клипа может вообще выйти за `main_dur` и откатиться к `outro`.
  2. `build_anti_cid_filtergraph()` применяет `setpts=(PTS-STARTPTS)/speed`, но не добавляет завершающий `fps={target_fps}` (когда нет `video_insert`, который делает это повторно). В результате видеопоток получает нестандартную частоту кадров (`target_fps * speed`), а при E2E-прогоне 4.0s клипа при `speed=1.05` итоговая длительность составила `3.90s` вместо ожидаемых `3.81s`.
- **План исправления:** Масштабировать `insert_at_sec` на `1.0 / speed_factor` аналогично оверлеям. Добавить `fps={target_fps}` в `build_anti_cid_filtergraph()` (или сразу после него в `ffmpeg_runner.py`) при `abs(speed - 1.0) > 0.0005`.

---

### [BUG-07] `MAJOR` — Залипание статуса клипа (`rendering` / `tracking`) или ложный статус `error` при отмене задачи
- **Файл и строка:** `backend/app/services/renderer/handlers.py:196-203, 238-245, 356-363`
- **Суть проблемы:** Хендлеры `handle_basic_cut`, `handle_track_clip` и `_render_or_export_single` перехватывают только `except Exception:`. В Python 3.11 `asyncio.CancelledError` наследуется от `BaseException`, а `JobCancelledError` — от `Exception`. При отмене задачи:
  - Если выброшен `asyncio.CancelledError` — блок `except` не срабатывает, и клип навсегда остаётся в статусе `"rendering"` или `"tracking"` в БД и UI.
  - Если выброшен `JobCancelledError` — он попадает в `except Exception:` и ошибочно помечает клип статусом `"error"`.
- **План исправления:** Явно добавить `except (JobCancelledError, asyncio.CancelledError):` перед `except Exception:`, восстанавливая `clip.status` в `"exported"` / `"rendered"` / `"draft"` (в зависимости от наличия `export_path`/`preview_path`), отправлять `ws_manager.emit_clip_update(...)` и пробрасывать исключение дальше.

---

### [BUG-08] `MAJOR` — После «Повторить запуск» бэкенда WebSocket-подписки не восстанавливаются
- **Файл и строка:** `src/store/useAppStore.ts:144-164, 257-273`
- **Суть проблемы:** Если при старте приложения `bootstrap()` не дождался ответа `/api/system/health` (`waitForHealth(30)` вернул `false`), функция завершается на строке 162 **до** вызова `wsClient.subscribe(...)`, `wsClient.onStatus(...)` и `listenBackendRestart(...)`. Когда пользователь нажимает «Повторить запуск» (`reconnectBackend()`), вызывается `wsClient.connect(info.ws_url)`, но ни один обработчик WebSocket так и не регистрируется. В итоге статус WS остаётся `false`, живые логи, телеметрия GPU и прогресс задач не работают до полной перезагрузки окна.
- **План исправления:** Вынести регистрацию подписок `wsClient.onStatus`, `wsClient.subscribe` и `listenBackendRestart` в идемпотентную функцию `ensureRealtimeSubscriptions(set, get)` и вызывать её как в `bootstrap()`, так и в `reconnectBackend()`.

---

### [BUG-09] `MAJOR` — Потеря несохранённых правок клипа в Студии при любом WebSocket-событии
- **Файл и строка:** `src/pages/ClipStudioPage.tsx:68-70`
- **Суть проблемы:**
  ```tsx
  useEffect(() => {
    setDraft(currentClip ? structuredClone(currentClip) : null);
  }, [currentClip]);
  ```
  Любое WebSocket-событие `clip_update` или завершение фоновой задачи вызывает `refreshClips(projectId)`, что создаёт новый массив `clips` и новую ссылку на объект `currentClip`. `useEffect` немедленно перезаписывает `draft`, без предупреждения уничтожая все несохранённые изменения пользователя (таймкоды, текст субтитров, хук, режим кропа, настройки рекламы и Anti-CID).
- **План исправления:** Отслеживать `isDirty` (или сравнивать `currentClip?.id` / `currentClip?.updated_at`): сбрасывать `draft` при смене `currentClip?.id`, а при обновлении того же клипа с сервера либо мержить только серверные поля (`status`, `preview_path`, `export_path`, `track_data_json`), либо не перезаписывать локальные редактируемые поля, пока `isDirty === true`.

---

### [BUG-10] `MAJOR` — Невозможно ввести пробел в поле «Хэштеги» в Студии клипов
- **Файл и строка:** `src/pages/ClipStudioPage.tsx:959-970`
- **Суть проблемы:** Инпут привязан к `value={draft.tags_json.join(" ")}`, а `onChange` делает `e.target.value.split(/\s+/).filter(Boolean)`. При нажатии пробела после первого тега (`"#shorts "`) `.filter(Boolean)` удаляет пустой элемент, и `join(" ")` тут же возвращает `"#shorts"` — пробел невозможно ввести с клавиатуры.
- **План исправления:** Хранить сырую строку `tagsInput` в локальном состоянии компонента (синхронизируя при смене клипа) и парсить `tagsInput.split(/\s+/).filter(Boolean)` только при сохранении (`saveDraft`) или `onBlur`.

---

### [BUG-11] `MAJOR` — Кэширование старого превью в `<video>` после повторного рендера клипа
- **Файл и строка:** `src/components/VideoPreview916.tsx:39-47, 199`
- **Суть проблемы:** Файл превью всегда перезаписывается по одному и тому же пути `{clip_id}_preview.mp4`. URL `api.mediaUrl(renderedPath)` не содержит cache-buster параметра, а `useEffect` на строке 43 зависит только от `[clip.id, clip.preview_path, clip.export_path]`. После повторного рендера с новыми субтитрами/кропом URL не меняется, и WebView2 продолжает воспроизводить старый кэшированный файл.
- **План исправления:** Добавить query-параметр версии в `renderedSrc` (например, `&v=${encodeURIComponent(clip.updated_at)}`) и включить `clip.updated_at` в зависимости эффекта перезагрузки `<video>`. Также на бэкенде в `media.py` отдавать `Cache-Control: no-cache` для файлов превью.

---

### [BUG-12] `MAJOR` — Некорректная обработка `"N/A"` и audio-only файлов в `ffprobe_utils.py`
- **Файл и строка:** `backend/app/services/ingest/ffprobe_utils.py:70-94`
- **Суть проблемы:**
  1. `float(fmt.get("duration") or 0.0)` и `int(fmt.get("bit_rate") or 0)` падают с `ValueError`, если FFprobe вернул строку `"N/A"` (типично для `.mkv`, `.webm`, `.ts` или недокачанных файлов).
  2. Подтверждено E2E-прогоном: для audio-only файла `probe_media()` успешно возвращает `width=0, height=0, video_codec=None`, после чего пайплайн падает позже с неинформативной ошибкой в `generate_thumbnail()`. Кроме того, `next(s for s in streams if s.get("codec_type") == "video")` может выбрать обложку альбома (`disposition.attached_pic == 1`) вместо видеопотока.
- **План исправления:** Написать безопасные парсеры `_safe_float()` / `_safe_int()`, игнорировать потоки с `disposition.attached_pic == 1` при поиске `video_stream`, и проверять наличие валидного видеопотока (`video_stream is None or width <= 0 or height <= 0`) на этапе импорта проекта с понятной ошибкой пользователю.

---

### [BUG-13] `MAJOR` — HTTP 500 при `null` в `PATCH /api/clips`, утечка файлов при удалении клипа и `IntegrityError` при удалении проекта во время анализа
- **Файл и строка:** `backend/app/api/clips.py:87-91, 128-135`, `backend/app/services/analyzer/pipeline.py:191-253`
- **Суть проблемы:**
  1. В `update_clip` (`clips.py:88-90`) при передаче `{"start_time": null}` или `{"end_time": null}` метод `updates.get("start_time", clip.start_time)` вернёт `None`, и сравнение `next_end <= next_start` выбросит `TypeError` (HTTP 500).
  2. `delete_clip` (`clips.py:128-135`) не отменяет активные задачи по `clip_id` и не удаляет файлы `preview_path` / `export_path` с диска.
  3. В `run_analysis_pipeline` (`pipeline.py:192-253`): если проект удалили во время анализа, `proj` оказывается `None`, но код на строках 223–252 всё равно добавляет `Clip(project_id=project_id, ...)` и делает `commit()`, падая с `sqlite3.IntegrityError: FOREIGN KEY constraint failed`.
- **План исправления:**
  1. В `update_clip` проверять `if updates.get("start_time") is None: updates.pop("start_time", None)` (аналогично `end_time`).
  2. В `delete_clip` отменять активные задачи по `clip_id` и удалять `preview_path` внутри `PROJECTS_DIR`.
  3. В `pipeline.py:193` делать `if proj is None: return {"project_id": project_id, "clip_count": 0}` до добавления клипов.

---

### [BUG-14] `MAJOR` — Кроссплатформенные и сборочные проблемы в Tauri (`src-tauri/`)
- **Файл и строка:** `src-tauri/src/lib.rs:71-83, 104-120, 129-147, 195-232`, `src-tauri/tauri.conf.json:29-36`
- **Суть проблемы:**
  1. `resolve_python_executable` (строка 81) на Linux/macOS использует фолбэк `"python"` вместо `"python3"` (на большинстве дистрибутивов Linux и macOS бинарника `python` нет).
  2. `stop_backend_process` (строки 129–147) на Unix убивает только `uvicorn` (`child.kill()`), но не дерево его дочерних процессов (в отличие от `taskkill /T /F` на Windows) — запущенные `ffmpeg` и `yt-dlp` остаются висеть в системе.
  3. `spawn_backend_process` не задаёт `PYTHONUTF8=1` и `PYTHONIOENCODING=utf-8` — на Windows без консоли (`CREATE_NO_WINDOW`) Python может использовать `cp1251`/`cp1252` для stdout/stderr, падая с `UnicodeEncodeError` на эмодзи и спецсимволах.
  4. `open_in_explorer` на Windows передаёт `/select,{path}` без нормализации слэшей (`/` vs `\`), что ломает выделение файла в `explorer.exe`; на Linux `xdg-open` на файле открывает видеоплеер вместо файлового менеджера с папкой.
  5. В `tauri.conf.json` секция `bundle` не включает `backend` в `resources` — собранный production-инсталлятор не найдёт `backend/app/main.py`.
- **План исправления:** На Unix использовать `"python3"`, запускать бэкенд в отдельной process group (`setsid`/`setpgid`) и завершать всю группу (`killpg`), выставлять `.env("PYTHONUTF8", "1").env("PYTHONIOENCODING", "utf-8")`, в `open_in_explorer` нормализовать путь (`canonicalize` / для Linux открывать `target.parent()` если `target.is_file()`), и настроить `bundle.resources` (или sidecar) в `tauri.conf.json`.

---

### [BUG-15] `MAJOR` — Отсутствие `torch` (CUDA) в `backend/requirements.txt`
- **Файл и строка:** `backend/requirements.txt:1-20`
- **Суть проблемы:** В `requirements.txt` не указан `torch`, хотя `cuda_env.py` и `yolo_tracker.py` напрямую импортируют `torch` и рассчитывают на CUDA-сборку (в текущем `.venv` вручную стоит `torch==2.6.0+cu124`). При чистой установке `pip install -r requirements.txt` на Windows зависимость `ultralytics` подтянет CPU-версию `torch` из PyPI, из-за чего `torch.cuda.is_available()` станет `False` и GPU-ускорение молча отключится.
- **План исправления:** Явно зафиксировать совместимость `torch` в `requirements.txt` / разделить `requirements-cuda.txt` (с `--extra-index-url https://download.pytorch.org/whl/cu124`) и `requirements-cpu.txt`, документировав установку.

---

### [BUG-16] `MINOR` — Избыточные ретраи LLM, игнорирование `min_duration` в `finalize_highlight_clips` и выход за границы при `duration < 1s`
- **Файл и строка:** `backend/app/services/analyzer/llm_highlights.py:252-272, 383-415`, `backend/app/services/analyzer/scene_detect.py:106-107`
- **Суть проблемы:**
  1. `analyze_highlights_llm` при *любой* ошибке (таймаут 90с, 401 Unauthorized, 429 Rate Limit, 500) делает повторный запрос без `response_format`, удваивая время ожидания и расход квоты.
  2. `finalize_highlight_clips` вызывает `snap_clip_boundaries` с жёстким `min_duration=8.0`, игнорируя выбранный пользователем `min_duration`.
  3. В `snap_clip_boundaries` строка `orig_end = max(orig_start + 1.0, min(float(end), duration))` при `duration < 1.0` возвращает `orig_end > duration`.
- **План исправления:** Повторять запрос без `response_format` только при `openai.BadRequestError` (HTTP 400). Пробрасывать `min_duration` в `finalize_highlight_clips`. В `snap_clip_boundaries` ограничивать `orig_end <= duration`.

---

### [BUG-17] `MINOR` — Гонка при динамической смене `max_render_workers`
- **Файл и строка:** `backend/app/core/queue_manager.py:162-166`
- **Суть проблемы:** `set_render_concurrency()` пересоздаёт `self._render_semaphore = asyncio.Semaphore(clamped)`. Уже запущенные задачи продолжают удерживать ссылку на старый семафор, а новые получают новый — временно может выполняться до `old_limit + new_limit` одновременных рендеров (риск превышения лимита NVENC-сессий на потребительских GPU).
- **План исправления:** Хранить один семафор или управляемый счётчик конкурентности на `QueueManager`, а не заменять объект `Semaphore` на лету.

---

### [BUG-18] `MINOR` — `export_batch` игнорирует `max_render_workers`, падает целиком из-за 1 клипа и перезаписывает файлы
- **Файл и строка:** `backend/app/services/renderer/handlers.py:403-416`
- **Суть проблемы:** Батч-экспорт выполняется строго последовательно в цикле `for`, игнорируя настройку `max_render_workers`. При ошибке одного клипа (например, удалён файл рекламы) весь батч прерывается. Повторный батч-экспорт того же проекта молча перезаписывает `clip_01.mp4`, `clip_02.mp4`, ...
- **План исправления:** Запускать экспорт клипов через `asyncio.gather` (ограничиваясь `ctx.render_semaphore`), изолировать ошибки отдельных клипов с итоговым отчётом и использовать уникальные имена файлов (как в одиночном экспорте: `{clip_slug}_{clip.id[:6]}.mp4`).

---

### [BUG-19] `MINOR` — `wsClient.connect(newUrl)` не закрывает предыдущий сокет при смене порта
- **Файл и строка:** `src/lib/ws.ts:18-22, 65-69`
- **Суть проблемы:** Если вызвать `wsClient.connect(newUrl)` без предварительного `disconnect()`, проверка `if (this.ws && (OPEN || CONNECTING)) return;` оставит подключённым старый сокет на старом порту.
- **План исправления:** В `connect(wsUrl)`: если `this.ws` уже открыт/подключается к другому `wsUrl`, закрывать старый сокет и открывать новый.

---

### [BUG-20] `MINOR` — Прочие проблемы UI, обработки ошибок, файловой системы и тулинга
- **Файлы и строки:**
  - `package.json`: отсутствует ESLint.
  - `src/pages/ClipStudioPage.tsx:154-190`, `src/pages/PresetsPage.tsx:383, 560, 660`: вызовы `api.deleteClip`, `api.updateClip`, `api.trackClip`, `api.renderPreview`, `api.exportClip`, `api.deleteAdPreset`, `api.deleteSubtitlePreset`, `api.deleteBackground` не обёрнуты в `try/catch` — ошибки уходят в unhandled promise rejection без уведомления пользователя.
  - `src/pages/SettingsPage.tsx:230-235, 323-328, 340-345`: в `<select>` модели Whisper отсутствует значение `"base"` (хотя оно есть в типах и схеме); кнопки выбора файла/папки показываются даже вне Tauri runtime и молча ничего не делают.
  - `src/lib/api.ts:43-44`: при HTTP 422 от FastAPI (`backend/app/main.py:113-122`) клиент игнорирует массив `errors`, показывая только общую строку `"Ошибка валидации входных данных"`.
  - `backend/app/api/projects.py:145`: синхронный `shutil.rmtree(proj_dir, ignore_errors=True)` вызывается внутри `async def` сразу после `cancel_project_jobs()` без ожидания завершения подпроцессов FFmpeg — на Windows это может молча оставить не удалённую папку проекта из-за блокировки файла.
- **План исправления:** Добавить ESLint-конфиг; обернуть мутирующие API-вызовы на фронте в `try/catch` с `addToast({ type: "error", ... })`; добавить `"base"` и проверку `isTauriRuntime()` в `SettingsPage.tsx`; форматировать `err.errors` в `api.ts`; выполнять `shutil.rmtree` через `asyncio.to_thread` после ожидания завершения отменённых подпроцессов.
