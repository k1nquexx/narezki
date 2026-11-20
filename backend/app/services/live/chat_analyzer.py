from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import logging
import random
import re
import ssl
from dataclasses import asdict, dataclass
from typing import Any, Callable, Optional

import httpx
import websockets

logger = logging.getLogger("narezki.live.chat")

_TWITCH_PRIVMSG_RE = re.compile(r"^:([A-Za-z0-9_]{2,25})![^\s]+ PRIVMSG #[^\s]+ :(.*)$")
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF]+",
    flags=re.UNICODE,
)
_CLIP_TRIGGER_RE = re.compile(
    r"(?:^|\s)(?:!clip\b|clip\s+(?:it|that|this)\b|клип(?:\s+это)?\b)",
    flags=re.IGNORECASE,
)

HYPE_TOKENS = {
    "kekw",
    "pog",
    "poggers",
    "pogchamp",
    "lul",
    "lulw",
    "omegalul",
    "gg",
    "w",
    "clutch",
    "clip",
    "wtf",
    "omg",
    "insane",
    "monkas",
    "pepeclap",
    "ахах",
    "ахаха",
    "хаха",
    "жесть",
    "имба",
    "кринж",
    "клип",
    "легенда",
    "шок",
    "ура",
}


def count_hype_and_emotes(text: str) -> int:
    """Count Unicode emojis and hype tokens/emotes inside a chat message."""
    if not text:
        return 0
    emoji_hits = len(_EMOJI_RE.findall(text))
    tokens = re.findall(r"[A-Za-zА-Яа-яЁё0-9_]{1,24}", text.lower())
    token_hits = sum(1 for tok in tokens if tok in HYPE_TOKENS)
    exclaim_bonus = 1 if text.count("!") >= 3 or text.count("?") >= 3 else 0
    return emoji_hits + token_hits + exclaim_bonus


def detect_clip_trigger(text: str) -> bool:
    """Return True when chat asks to clip the moment."""
    return bool(text and _CLIP_TRIGGER_RE.search(text))


@dataclass
class ChatSpikeEvent:
    timestamp: float
    start: float
    end: float
    rate: float
    baseline_rate: float
    ratio: float
    unique_authors: int
    emote_hits: int
    score: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ChatPulseAnalyzer:
    """Tracks live chat density, unique-author anti-spam ratio, and emote/hype spikes."""

    def __init__(self, keep_seconds: int = 600, warmup_seconds: int = 45) -> None:
        self.keep_seconds = max(120, int(keep_seconds))
        self.warmup_seconds = max(10, int(warmup_seconds))
        self.messages: collections.Counter[int] = collections.Counter()
        self.emotes: collections.Counter[int] = collections.Counter()
        self.authors_by_sec: dict[int, set[str]] = {}
        self.total_messages: int = 0
        self.spikes: list[ChatSpikeEvent] = []
        self._last_pruned_sec: int = 0
        self._last_spike_sec: float = -999.0
        self.connected: bool = False

    def _prune_old(self, current_second: int) -> None:
        if current_second - self._last_pruned_sec < 60:
            return
        cutoff = current_second - self.keep_seconds
        for sec in [k for k in self.authors_by_sec if k < cutoff]:
            del self.authors_by_sec[sec]
        for sec in [k for k in self.messages if k < cutoff]:
            del self.messages[sec]
        for sec in [k for k in self.emotes if k < cutoff]:
            del self.emotes[sec]
        self._last_pruned_sec = current_second

    def record_message(self, second: int, author: str, text: str) -> int:
        """Record a chat message at stream second and return its hype/emote hit count."""
        sec = max(0, int(second))
        self.messages[sec] += 1
        self.total_messages += 1
        clean_author = (author or "").strip().lower()
        if clean_author:
            self.authors_by_sec.setdefault(sec, set()).add(clean_author)
        hits = count_hype_and_emotes(text)
        if hits > 0:
            self.emotes[sec] += hits
        self._prune_old(sec)
        return hits

    def get_recent_series(self, current_second: int, window_sec: int = 60) -> list[int]:
        """Return per-second message counts over the trailing window."""
        end_s = max(0, int(current_second))
        start_s = max(0, end_s - max(1, int(window_sec)) + 1)
        return [int(self.messages.get(s, 0)) for s in range(start_s, end_s + 1)]

    def current_rates(self, current_second: int) -> tuple[float, float, int]:
        """Return (baseline_rate, recent_8s_rate, recent_unique_authors)."""
        sec = max(0, int(current_second))
        base_start = max(0, sec - 190)
        base_end = max(base_start, sec - 10)
        base_span = max(1, base_end - base_start + 1)

        rec_start = max(0, sec - 7)
        rec_span = max(1, sec - rec_start + 1)

        use_authors = bool(self.authors_by_sec)
        if use_authors:
            base_total = sum(len(self.authors_by_sec.get(s, ())) for s in range(base_start, base_end + 1))
            rec_authors: set[str] = set()
            for s in range(rec_start, sec + 1):
                rec_authors.update(self.authors_by_sec.get(s, ()))
            rec_total = sum(len(self.authors_by_sec.get(s, ())) for s in range(rec_start, sec + 1))
            unique_count = len(rec_authors)
        else:
            base_total = sum(self.messages.get(s, 0) for s in range(base_start, base_end + 1))
            rec_total = sum(self.messages.get(s, 0) for s in range(rec_start, sec + 1))
            unique_count = rec_total

        return base_total / base_span, rec_total / rec_span, unique_count

    def evaluate_pulse(
        self,
        current_second: int,
        sensitivity: float = 1.6,
        min_gap_sec: float = 60.0,
        min_recent_rate: float = 1.25,
    ) -> Optional[ChatSpikeEvent]:
        """Detect abnormal chat burst using PeakCut's rolling baseline + unique-author anti-spam."""
        sec = max(0, int(current_second))
        if sec < self.warmup_seconds or self.total_messages < 5:
            return None
        if sec - self._last_spike_sec < min_gap_sec:
            return None

        baseline_rate, recent_rate, unique_authors = self.current_rates(sec)
        if self.authors_by_sec and unique_authors < 3:
            # One spamming user/bot must not trigger a highlight spike
            return None

        threshold = max(min_recent_rate, baseline_rate * max(1.1, float(sensitivity)))
        if recent_rate < threshold:
            return None

        rec_start = max(0, sec - 7)
        emote_hits = sum(self.emotes.get(s, 0) for s in range(rec_start, sec + 1))
        ratio = round(recent_rate / max(baseline_rate, 0.1), 2)
        emote_bonus = min(18.0, emote_hits * 1.5)
        score = round(min(98.0, max(55.0, 48.0 + ratio * 15.0 + emote_bonus)), 1)

        event = ChatSpikeEvent(
            timestamp=float(max(0, sec - 2)),
            start=float(max(0, sec - 8)),
            end=float(sec + 2),
            rate=round(recent_rate, 2),
            baseline_rate=round(baseline_rate, 2),
            ratio=ratio,
            unique_authors=unique_authors,
            emote_hits=emote_hits,
            score=score,
            reason=f"Чат x{ratio:.1f} ({unique_authors} авторов, {emote_hits} эмодзи/хайп)",
        )
        self.spikes.append(event)
        if len(self.spikes) > 200:
            self.spikes = self.spikes[-200:]
        self._last_spike_sec = float(sec)
        return event

    def export_spikes(self) -> list[dict[str, Any]]:
        return [sp.to_dict() for sp in self.spikes]


# --- Platform Chat Parsers & Async Readers ---


def parse_twitch_irc_line(line: str) -> Optional[tuple[str, str]]:
    """Parse a Twitch IRC PRIVMSG line into (username, message_text) or None."""
    clean = (line or "").rstrip("\r\n")
    if clean.startswith("@"):
        # Strip IRCv3 tags if present
        parts = clean.split(" ", 1)
        clean = parts[1] if len(parts) > 1 else ""
    m = _TWITCH_PRIVMSG_RE.match(clean)
    if not m:
        return None
    return m.group(1), m.group(2)


def parse_kick_pusher_message(raw_json: str) -> Optional[tuple[str, str]]:
    """Parse a Kick Pusher WebSocket JSON frame into (username, message_text) or None."""
    try:
        frame = json.loads(raw_json)
    except (TypeError, ValueError):
        return None
    event_name = str(frame.get("event") or "")
    if "ChatMessage" not in event_name:
        return None
    data = frame.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    sender = data.get("sender") or {}
    username = str(sender.get("username") or sender.get("slug") or "")
    content = str(data.get("content") or "")
    if not content:
        return None
    return username, content


def parse_youtube_chat_actions(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """Extract (author, message) tuples from a YouTube Live Chat get_live_chat response."""
    results: list[tuple[str, str]] = []
    actions = (
        payload.get("continuationContents", {})
        .get("liveChatContinuation", {})
        .get("actions", [])
    )
    for act in actions:
        item = act.get("addChatItemAction", {}).get("item", {})
        renderer = item.get("liveChatTextMessageRenderer")
        if not isinstance(renderer, dict):
            continue
        author = str(renderer.get("authorName", {}).get("simpleText") or "")
        runs = renderer.get("message", {}).get("runs") or []
        parts: list[str] = []
        for r in runs:
            if isinstance(r, dict):
                if "text" in r:
                    parts.append(str(r["text"]))
                elif "emoji" in r:
                    shortcuts = r["emoji"].get("shortcuts") or []
                    parts.append(str(shortcuts[0]) if shortcuts else "🔥")
        msg = "".join(parts).strip()
        if msg:
            results.append((author, msg))
    return results


async def run_twitch_chat_reader(
    channel_login: str,
    get_second: Callable[[], int],
    analyzer: ChatPulseAnalyzer,
    stop_event: asyncio.Event,
    on_clip_trigger: Optional[Callable[[int, str], None]] = None,
) -> None:
    """Connect anonymously via TLS to Twitch IRC and feed ChatPulseAnalyzer."""
    login = channel_login.strip().lower().lstrip("#")
    if not login:
        return
    ssl_ctx = ssl.create_default_context()

    while not stop_event.is_set():
        writer: Optional[asyncio.StreamWriter] = None
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection("irc.chat.twitch.tv", 6697, ssl=ssl_ctx),
                timeout=15.0,
            )
            nick = f"justinfan{random.randint(10000, 99999)}"
            writer.write(f"NICK {nick}\r\nJOIN #{login}\r\n".encode("utf-8"))
            await writer.drain()
            analyzer.connected = True

            while not stop_event.is_set():
                try:
                    raw_line = await asyncio.wait_for(reader.readline(), timeout=20.0)
                except asyncio.TimeoutError:
                    continue
                if not raw_line:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if line.startswith("PING"):
                    writer.write(b"PONG :tmi.twitch.tv\r\n")
                    await writer.drain()
                    continue
                parsed = parse_twitch_irc_line(line)
                if parsed:
                    author, msg = parsed
                    sec = get_second()
                    analyzer.record_message(sec, author, msg)
                    if on_clip_trigger and detect_clip_trigger(msg):
                        on_clip_trigger(sec, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Twitch IRC reconnect for %s: %s", login, exc)
        finally:
            analyzer.connected = False
            if writer is not None:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass


async def run_kick_chat_reader(
    slug: str,
    get_second: Callable[[], int],
    analyzer: ChatPulseAnalyzer,
    stop_event: asyncio.Event,
    on_clip_trigger: Optional[Callable[[int, str], None]] = None,
) -> None:
    """Connect to Kick chatroom Pusher WebSocket and feed ChatPulseAnalyzer."""
    clean_slug = slug.strip().lower()
    if not clean_slug:
        return
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
        "Accept": "application/json",
    }

    while not stop_event.is_set():
        try:
            async with httpx.AsyncClient(timeout=10.0, headers=headers) as client:
                r = await client.get(f"https://kick.com/api/v2/channels/{clean_slug}")
                r.raise_for_status()
                chatroom_id = (r.json().get("chatroom") or {}).get("id")
            if not chatroom_id:
                await asyncio.sleep(10.0)
                continue

            pusher_url = (
                "wss://ws-us2.pusher.com/app/32cbd69e4b950bf97679"
                "?protocol=7&client=js&version=7.6.0&flash=false"
            )
            async with websockets.connect(pusher_url, open_timeout=15.0) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "event": "pusher:subscribe",
                            "data": {"auth": "", "channel": f"chatrooms.{chatroom_id}.v2"},
                        }
                    )
                )
                analyzer.connected = True
                while not stop_event.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=20.0)
                    except asyncio.TimeoutError:
                        continue
                    parsed = parse_kick_pusher_message(str(raw))
                    if parsed:
                        author, msg = parsed
                        sec = get_second()
                        analyzer.record_message(sec, author, msg)
                        if on_clip_trigger and detect_clip_trigger(msg):
                            on_clip_trigger(sec, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("Kick chat reconnect for %s: %s", clean_slug, exc)
        finally:
            analyzer.connected = False
        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=6.0)
            except asyncio.TimeoutError:
                pass


async def run_youtube_chat_reader(
    video_id: str,
    get_second: Callable[[], int],
    analyzer: ChatPulseAnalyzer,
    stop_event: asyncio.Event,
    on_clip_trigger: Optional[Callable[[int, str], None]] = None,
) -> None:
    """Poll YouTube Live Chat innertube continuation endpoint and feed ChatPulseAnalyzer."""
    vid = (video_id or "").strip()
    if not vid:
        return
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    while not stop_event.is_set():
        try:
            async with httpx.AsyncClient(timeout=12.0, headers=headers) as client:
                page = await client.get(f"https://www.youtube.com/live_chat?is_popout=1&v={vid}")
                page.raise_for_status()
                html = page.text
                key_m = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
                cont_m = re.search(r'"continuation":"([^"]+)"', html)
                if not key_m or not cont_m:
                    await asyncio.sleep(15.0)
                    continue
                api_key = key_m.group(1)
                continuation = cont_m.group(1)
                analyzer.connected = True

                while not stop_event.is_set():
                    resp = await client.post(
                        f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={api_key}",
                        json={
                            "context": {
                                "client": {"clientName": "WEB", "clientVersion": "2.20240726.00.00"}
                            },
                            "continuation": continuation,
                        },
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    for author, msg in parse_youtube_chat_actions(payload):
                        sec = get_second()
                        analyzer.record_message(sec, author, msg)
                        if on_clip_trigger and detect_clip_trigger(msg):
                            on_clip_trigger(sec, msg)

                    conts = (
                        payload.get("continuationContents", {})
                        .get("liveChatContinuation", {})
                        .get("continuations", [])
                    )
                    next_cont = None
                    wait_ms = 4000
                    for c in conts:
                        for kind in ("invalidationContinuationData", "timedContinuationData"):
                            if kind in c:
                                next_cont = c[kind].get("continuation")
                                wait_ms = int(c[kind].get("timeoutMs", 4000))
                                break
                        if next_cont:
                            break
                    if not next_cont:
                        break
                    continuation = next_cont
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=max(1.5, wait_ms / 1000.0))
                    except asyncio.TimeoutError:
                        pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("YouTube chat reconnect for %s: %s", vid, exc)
        finally:
            analyzer.connected = False
        if not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass
