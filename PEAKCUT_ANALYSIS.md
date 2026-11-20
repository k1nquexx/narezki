# Технический анализ репозитория PeakCut (`_ref_peakcut`)

**Источник:** `https://github.com/haziqahmedqasmi/PeakCut`  
**Цель анализа:** извлечь проверенные архитектурные паттерны Live-мониторинга, детекции всплесков чата и отложенного автопостинга для интеграции в Narezki (Tauri v2 + React/TS + FastAPI + SQLite + FFmpeg NVENC + YOLOv8).

---

## 1. Live Monitor и Rolling Buffer (`studio/live.py`, `studio/profiles.py`)

### Проверка онлайна каналов (`profiles.py`)
- **Twitch:** быстрый GraphQL-запрос к `https://gql.twitch.tv/gql` (публичный `Client-ID` веб-клиента) — ответ за ~1 секунду вместо 10+ секунд через `yt-dlp`. Возвращает `live`, `viewersCount`, `title`, `createdAt`, `login`; при сбое предусмотрен фолбэк на `yt-dlp`.
- **Kick:** прямой GET-запрос к публичному API `https://kick.com/api/v2/channels/{slug}` (`livestream`, `viewer_count`, `session_title`, `start_time`).
- **YouTube:** нормализация URL канала к `<base>/live`, быстрая проверка `"isLiveNow":true` в HTML страницы и получение `video_id` / `concurrent_view_count` через `yt-dlp` с защитой от bot-check пауз.
- **Дедупликация проверок (`live_shared`):** кэш на 75 секунд с блокировкой по ключу канала, чтобы не спамить внешние API.

### Захват и кольцевой буфер без переполнения диска (`live.py`)
1. `resolve(url)` через `yt-dlp` получает прямые URL медиапотоков (`format: b[height<=720]/bv*[height<=720]+ba/b`).
2. **Один FFmpeg-процесс с двумя выходами** (`-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 10`):
   - **Видеопоток (без перекодирования `-c copy`):** нарезается в MPEG-TS сегменты по 6 секунд (`-f segment -segment_time 6 -reset_timestamps 1 -segment_format mpegts -segment_list pXX.csv -segment_list_type csv pXX_%05d.ts`).
   - **Аудиопоток (pipe):** параллельно декодируется в `pipe:1` (`-vn -ac 1 -ar 16000 -f s16le`) для посекундного расчёта RMS-громкости (`20 * log10(sqrt(mean(x^2)))`) и анализа речи без чтения `.ts` файлов с диска.
3. **Очистка буфера (`_scan_segments`):** периодически дочитывает новые строки из `pXX.csv`, ведёт индекс `(path, start_sec, end_sec)` и удаляет `.ts`-куски старше `buffer_min * 60` секунд — объём на диске остаётся строго ограниченным.
4. **Нарезка клипа из буфера (`_render`):** отбирает сегменты, пересекающие `[start - PAD, end + PAD]`, и склеивает их через FFmpeg `-f concat -safe 0 -i list.txt -c copy clip.src.ts` за доли секунды.

---

## 2. Chat Spike Detection (`studio/live.py`, `studio/brain.py`)

### Подключение к чатам и защита от спама
- В оригинальном PeakCut реализован парсер чата **только для Twitch** (`_chat`): анонимное TLS-подключение к `irc.chat.twitch.tv:6697` под ником `justinfan<rand>` и `JOIN #<channel>` с обработкой `PING`/`PONG` и парсингом `PRIVMSG`.
- **Анти-бот фильтрация (`chat_users` + `_trim_names`):** для каждой секунды хранится `set` уникальных ников (`_NICK`), а старые записи старше `CHAT_KEEP = 600s` периодически очищаются. Это не даёт одному боту/пользователю, заспамившему чат, поднять ложный спайк.

### Алгоритм выявления всплесков (`_watch_spikes`, `_chat_spike`)
- Поддерживается скользящее окно базовой линии за ~3 минуты (`[-190:-10]` секунд, исключая последние 10с).
- **Аудио-скачок:** сравнивается линейная амплитуда `10 ** (db / 20)` последних 5 секунд со средним по базовому окну (`loud_jump >= 0.6`, т.е. +60%).
- **Чат-скачок:** сравнивается активность последних 8 секунд (уникальных авторов/сообщений в секунду) со средним по базовому окну (`chat_now >= max(1.5, chat_base * 1.6)`).
- **Триггер-слова в речи:** фразы вроде `"clip it"` / `"clip that"` или пользовательские триггер-слова.
- При интеграции в Narezki дополнительно расширяем эту логику:
  - Добавляем поддержку **Kick** (Pusher WebSocket по `chatroom.id`) и **YouTube Live Chat** (innertube continuation poller).
  - Добавляем учёт **плотности эмодзи и хайп-токенов** (`KEKW`, `POG`, `GG`, `CLUTCH`, `АХАХ`, `ЖЕСТЬ` и Unicode emoji).
  - Реализуем **Zero-AI режим** (нарезка напрямую по чат- и аудио-спайкам без вызова облачного LLM) и передачу `chat_spikes` в наш `llm_highlights.py` / `heuristic` как сигнал виральности.

---

## 3. Auto-Posting и Scheduler (`studio/posting.py`, `studio/accounts.py`)

### Планировщик публикаций (`posting.py`)
- **`_next_slot(account_id, gap_min, cap)`:** защита от теневого бана — вычисляет ближайший свободный слот не ранее `last_post_or_due + gap_min * 60`. Если за день уже достигнут лимит `cap`, переносит публикацию на `09:00` следующего дня (до 30 дней вперёд).
- **Идемпотентность и ретраи:** запрет повторной отправки пары `(clip, account)`; до 3 попыток с задержкой 10 минут при временных ошибках; восстановление прерванных загрузок (`posting` -> `failed`) после перезапуска сервера.

### Загрузчики платформ (`accounts.py`)
Вместо тяжёлых SDK или нестабильной браузерной автоматизации PeakCut использует прямые HTTP-вызовы через `httpx`:
1. **YouTube Shorts:** OAuth2 refresh token → `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` → `PUT` видеофайла на `Location` → ссылка `https://www.youtube.com/shorts/{id}` (тег `#Shorts` и категория Gaming `"20"`).
2. **TikTok:** OAuth2 (`video.publish`, с автосохранением обновляемого `refresh_token`) → запрос `/v2/post/publish/creator_info/query/` → `/v2/post/publish/video/init/` (кусками по 10 МБ при размере > 64 МБ, либо 1 чанком ≤ 64 МБ) → `PUT` чанков с `Content-Range` → опрос `/v2/post/publish/status/fetch/`. При ошибке `unaudited` автоматически откатывается в `SELF_ONLY` (приватный пост).
3. **Instagram Reels:** Meta Graph API (`graph.instagram.com`) → создание контейнера (`media_type=REELS`, через `video_url` при наличии публичного HTTPS либо через `upload_type=resumable` на `rupload.facebook.com/ig-api-upload/v21.0`) → опрос `status_code` → `media_publish`.

---

## 4. Зависимости и совместимость с Windows / окружением Narezki

| Зависимость в PeakCut | Назначение | Статус в `backend/.venv` (Narezki) и совместимость с Windows |
|---|---|---|
| `yt-dlp` | Проверка онлайна и извлечение URL потоков Twitch/Kick/YouTube | Уже установлен (`2026.10.26`), работает на Windows из коробки. Опционально можно поддерживать `streamlink` при наличии в PATH. |
| `httpx` | Запросы к Twitch GQL, Kick v2, YouTube/TikTok/Instagram API | Уже установлен (`0.28.1`), полностью асинхронный (`AsyncClient`), кроссплатформенный. |
| `numpy` | Расчёт RMS dBFS и z-score по 16kHz PCM | Уже установлен, работает на Windows. |
| `websockets` | Live-подписка на Kick Pusher chat WebSocket | Уже установлен (`17.4`), совместим с asyncio на Windows. |
| `ffmpeg` / `ffprobe` | Сегментация TS-буфера, конкат и рендер | В проекте уже есть `backend/bin/ffmpeg.exe` с `CREATE_NO_WINDOW` (и кроссплатформенный резолвер из `BUG-01`). |
| `psycopg`, `sherpa-onnx` | PostgreSQL и альтернативный ASR | **Не требуются** в Narezki: у нас используются `aiosqlite` (SQLite WAL) и `faster-whisper` на CUDA/CPU. |
