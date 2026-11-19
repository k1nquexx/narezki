from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Optional

from openai import AsyncOpenAI, BadRequestError

from ...core.schemas import LLMHighlightItem
from .scene_detect import snap_clip_boundaries

logger = logging.getLogger("narezki.llm")


def _fmt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    m = int(sec // 60)
    s = sec % 60.0
    return f"{m:02d}:{s:05.2f}"


def build_clip_subtitles(
    words: list[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    words_per_line: int = 3,
) -> list[dict[str, Any]]:
    """Build clip-relative subtitle cues with word timestamps from global transcript words."""
    clip_words: list[dict[str, Any]] = []
    for w in words:
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", 0.0))
        if we <= clip_start or ws >= clip_end:
            continue
        rel_s = round(max(0.0, ws - clip_start), 3)
        rel_e = round(min(clip_end - clip_start, max(rel_s + 0.05, we - clip_start)), 3)
        txt = str(w.get("word", "")).strip()
        if txt:
            clip_words.append({"word": txt, "start": rel_s, "end": rel_e})

    if not clip_words:
        return []

    cues: list[dict[str, Any]] = []
    buf: list[dict[str, Any]] = []
    max_words = max(1, int(words_per_line))

    for w in clip_words:
        if buf:
            gap = float(w["start"]) - float(buf[-1]["end"])
            if len(buf) >= max_words or gap > 0.65:
                cues.append(
                    {
                        "id": uuid.uuid4().hex[:8],
                        "start": float(buf[0]["start"]),
                        "end": float(buf[-1]["end"]),
                        "text": " ".join(x["word"] for x in buf),
                        "words": list(buf),
                    }
                )
                buf = []
        buf.append(w)

    if buf:
        cues.append(
            {
                "id": uuid.uuid4().hex[:8],
                "start": float(buf[0]["start"]),
                "end": float(buf[-1]["end"]),
                "text": " ".join(x["word"] for x in buf),
                "words": list(buf),
            }
        )
    return cues


def build_multimodal_chunks(
    duration: float,
    transcript: list[dict[str, Any]],
    audio_peaks: list[dict[str, Any]],
    action_bursts: list[dict[str, Any]],
    chunk_minutes: int = 10,
    overlap_sec: float = 60.0,
) -> list[tuple[float, float, str]]:
    """Chunk the video timeline into overlapping windows combining speech, audio peaks, and scene cuts."""
    chunk_len = max(120.0, float(chunk_minutes) * 60.0)
    chunks: list[tuple[float, float, str]] = []
    t0 = 0.0
    while t0 < max(duration, 1.0):
        t1 = min(duration, t0 + chunk_len)
        events: list[tuple[float, str]] = []

        for seg in transcript:
            ss = float(seg.get("start", 0.0))
            se = float(seg.get("end", 0.0))
            if se >= t0 and ss <= t1:
                txt = str(seg.get("text", "")).strip()
                if txt:
                    events.append((ss, f"[{ss:.2f}s - {se:.2f}s | {_fmt_ts(ss)}] SPEECH: {txt}"))

        for pk in audio_peaks:
            ps = float(pk.get("start", 0.0))
            pe = float(pk.get("end", 0.0))
            if pe >= t0 and ps <= t1:
                events.append(
                    (
                        ps,
                        f"[{ps:.2f}s - {pe:.2f}s | {_fmt_ts(ps)}] [AUDIO_SPIKE] "
                        f"громкость +{pk.get('delta_db', 0)}dB (peak={pk.get('peak_db', -10)}dBFS, score={pk.get('score', 50)})",
                    )
                )

        for ab in action_bursts:
            bs = float(ab.get("start", 0.0))
            be = float(ab.get("end", 0.0))
            if be >= t0 and bs <= t1:
                events.append(
                    (
                        bs,
                        f"[{bs:.2f}s - {be:.2f}s | {_fmt_ts(bs)}] [ACTION_MONTAGE] "
                        f"частые склейки сцен ({ab.get('cut_count', 4)} склеек/10с — возможен экшен/драка)",
                    )
                )

        events.sort(key=lambda x: x[0])
        header = f"Окно анализа: {t0:.1f}s – {t1:.1f}s (общая длительность видео: {duration:.1f}s)"
        body = "\n".join(line for _, line in events) if events else "(В этом фрагменте мало событий)"
        chunks.append((t0, t1, f"{header}\n{body}"))

        if t1 >= duration:
            break
        t0 = max(t0 + 30.0, t1 - overlap_sec)

    return chunks


def _extract_json_payload(raw_text: str) -> dict[str, Any]:
    cleaned = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*([\{\[].*?[\}\]])\s*```", cleaned, flags=re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        brace_start = cleaned.find("{")
        brace_end = cleaned.rfind("}")
        bracket_start = cleaned.find("[")
        bracket_end = cleaned.rfind("]")
        if brace_start != -1 and brace_end > brace_start and (
            bracket_start == -1 or brace_start < bracket_start
        ):
            cleaned = cleaned[brace_start : brace_end + 1]
        elif bracket_start != -1 and bracket_end > bracket_start:
            cleaned = cleaned[bracket_start : bracket_end + 1]
    data = json.loads(cleaned)
    if isinstance(data, list):
        return {"highlights": data}
    return data


def _temporal_iou(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    inter = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return inter / union if union > 0 else 0.0


def deduplicate_highlights(items: list[LLMHighlightItem], max_count: int) -> list[LLMHighlightItem]:
    """Deduplicate overlapping highlights across overlapping chunks, keeping highest virality."""
    sorted_items = sorted(items, key=lambda x: x.virality_score, reverse=True)
    kept: list[LLMHighlightItem] = []
    for cand in sorted_items:
        if any(_temporal_iou(cand.start_time, cand.end_time, k.start_time, k.end_time) > 0.40 for k in kept):
            continue
        kept.append(cand)
        if len(kept) >= max_count:
            break
    return sorted(kept, key=lambda x: x.virality_score, reverse=True)


SYSTEM_PROMPT = """Ты — элитный продюсер виральных коротких видео (TikTok, YouTube Shorts, Reels).
Твоя задача — найти в длинном видео (фильм, аниме, стрим, подкаст) самые цепляющие, смешные, драматичные или эпичные моменты длительностью от {min_dur:.0f} до {max_dur:.0f} секунд.

Учитывай ДВА типа контента:
1. Диалоговые/эмоциональные сцены (сильные фразы, конфликты, неожиданные повороты, панчлайны, реакции стримера).
2. Экшен-сцены БЕЗ слов (взрывы громкости [AUDIO_SPIKE] + быстрый монтаж [ACTION_MONTAGE] — драки в аниме, перестрелки, эпичные скримеры, кульминации).

Верни СТРОГО валидный JSON-объект без лишнего текста:
{{
  "highlights": [
    {{
      "start_time": 12.5,
      "end_time": 42.0,
      "title": "Короткий цепляющий заголовок на русском",
      "hook_text": "ДО 6 СЛОВ — ХУК НА ПЕРВЫЕ 3 СЕКУНДЫ",
      "description": "Описание для публикации (1-2 предложения)",
      "tags": ["#shorts", "#fyp", "#рек"],
      "virality_score": 88.5,
      "category": "dialogue" | "action" | "hybrid",
      "reason": "Почему этот момент удержит внимание зрителя"
    }}
  ]
}}
Используй ТОЛЬКО секунды из входного таймлайна (start_time < end_time, в пределах длительности видео)."""


async def analyze_highlights_llm(
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    duration: float,
    transcript: list[dict[str, Any]],
    audio_peaks: list[dict[str, Any]],
    action_bursts: list[dict[str, Any]],
    max_highlights: int = 10,
    chunk_minutes: int = 10,
    min_duration: float = 15.0,
    max_duration: float = 60.0,
    focus_prompt: str = "",
    cancel_event: Optional[asyncio.Event] = None,
) -> list[LLMHighlightItem]:
    """Send multimodal timeline chunks to an OpenAI-compatible Cloud LLM and validate JSON highlights."""
    if not api_key.strip():
        raise ValueError("API ключ LLM не задан.")

    chunks = build_multimodal_chunks(
        duration=duration,
        transcript=transcript,
        audio_peaks=audio_peaks,
        action_bursts=action_bursts,
        chunk_minutes=chunk_minutes,
    )
    sys_msg = SYSTEM_PROMPT.format(min_dur=min_duration, max_dur=max_duration)
    if focus_prompt.strip():
        sys_msg += f"\nДополнительный фокус от пользователя: {focus_prompt.strip()}"

    collected: list[LLMHighlightItem] = []
    per_chunk_limit = max(3, min(8, max_highlights))
    last_chunk_err: Optional[Exception] = None

    async with AsyncOpenAI(base_url=base_url.strip(), api_key=api_key.strip(), timeout=90.0) as client:
        for idx, (_t0, _t1, chunk_text) in enumerate(chunks):
            if cancel_event and cancel_event.is_set():
                raise asyncio.CancelledError("LLM анализ отменён")

            user_msg = (
                f"Фрагмент {idx + 1}/{len(chunks)}. Найди до {per_chunk_limit} лучших виральных моментов.\n\n"
                f"{chunk_text}"
            )
            try:
                try:
                    resp = await client.chat.completions.create(
                        model=model.strip(),
                        messages=[
                            {"role": "system", "content": sys_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        temperature=temperature,
                        response_format={"type": "json_object"},
                    )
                except BadRequestError as exc:
                    # Retry once without response_format in case provider rejects json_object
                    logger.warning("LLM json_object request failed (%s), retrying plain JSON mode", exc)
                    resp = await client.chat.completions.create(
                        model=model.strip(),
                        messages=[
                            {"role": "system", "content": sys_msg},
                            {"role": "user", "content": user_msg},
                        ],
                        temperature=temperature,
                    )

                if not resp.choices:
                    raise RuntimeError("LLM вернула пустой список choices")

                raw_content = resp.choices[0].message.content or "{}"
                parsed_dict = _extract_json_payload(raw_content)
                raw_list = parsed_dict.get("highlights")
                if not isinstance(raw_list, list):
                    raise ValueError("Ответ LLM не содержит список highlights")

                for raw_item in raw_list:
                    try:
                        item = LLMHighlightItem.model_validate(raw_item)
                    except Exception as item_exc:
                        logger.debug("Skipping invalid LLM highlight item: %s", item_exc)
                        continue
                    s = max(0.0, min(item.start_time, max(0.0, duration - 1.0)))
                    e = max(s + 1.0, min(item.end_time, duration))
                    if e - s < 3.0:
                        continue
                    item.start_time = s
                    item.end_time = e
                    collected.append(item)
            except asyncio.CancelledError:
                raise
            except Exception as chunk_exc:
                last_chunk_err = chunk_exc
                logger.warning("LLM chunk %d/%d failed: %s", idx + 1, len(chunks), chunk_exc)
                continue

    if not collected and last_chunk_err is not None:
        raise last_chunk_err

    return deduplicate_highlights(collected, max_highlights)


def analyze_highlights_heuristic(
    duration: float,
    transcript: list[dict[str, Any]],
    audio_peaks: list[dict[str, Any]],
    scenes: list[float],
    action_bursts: list[dict[str, Any]],
    min_duration: float = 15.0,
    max_duration: float = 60.0,
    max_highlights: int = 8,
) -> list[LLMHighlightItem]:
    """Offline multimodal highlight detector combining audio spikes, scene cut density, and speech."""
    if duration <= 2.0:
        return []

    target_dur = min(duration, max(min_duration, min(35.0, max_duration)))
    step = max(4.0, target_dur * 0.45)
    candidates: list[LLMHighlightItem] = []

    t = 0.0
    while t < duration:
        end_t = min(duration, t + target_dur)
        if end_t - t < min(8.0, duration * 0.8):
            if duration >= target_dur:
                t = max(0.0, duration - target_dur)
                end_t = duration
            else:
                break

        peak_hits = [p for p in audio_peaks if float(p["end"]) >= t and float(p["start"]) <= end_t]
        burst_hits = [b for b in action_bursts if float(b["end"]) >= t and float(b["start"]) <= end_t]
        cut_count = sum(1 for c in scenes if t <= c <= end_t)
        speech_segs = [s for s in transcript if float(s["end"]) >= t and float(s["start"]) <= end_t]
        speech_words = sum(len(str(s.get("text", "")).split()) for s in speech_segs)

        audio_score = max((float(p.get("score", 0.0)) for p in peak_hits), default=0.0)
        action_score = min(40.0, cut_count * 4.5 + len(burst_hits) * 18.0)
        speech_score = min(35.0, speech_words * 0.65)
        total_score = round(min(98.0, max(25.0, 20.0 + audio_score * 0.45 + action_score + speech_score)), 1)

        if burst_hits or (cut_count >= 4 and speech_words < 12):
            category = "action"
            reason = f"Экшен-пик: {cut_count} склеек сцен и всплеск громкости"
        elif peak_hits and speech_words >= 12:
            category = "hybrid"
            reason = f"Эмоциональный пик + активный диалог ({speech_words} слов)"
        else:
            category = "dialogue"
            reason = f"Насыщенный диалоговый фрагмент ({speech_words} слов)"

        snippet = " ".join(str(s.get("text", "")).strip() for s in speech_segs[:2]).strip()
        hook_words = snippet.split()[:5]
        hook_text = " ".join(hook_words).upper() if hook_words else "СМОТРИ ДО КОНЦА"
        title = (snippet[:48] + "...") if len(snippet) > 48 else (snippet or f"Момент #{len(candidates) + 1}")

        candidates.append(
            LLMHighlightItem(
                start_time=round(t, 2),
                end_time=round(end_t, 2),
                title=title,
                hook_text=hook_text,
                description=f"{reason}. Таймкод {_fmt_ts(t)}–{_fmt_ts(end_t)}.",
                tags=["#shorts", "#reels", "#fyp", "#narezki"],
                virality_score=total_score,
                category=category,
                reason=reason,
            )
        )
        if end_t >= duration:
            break
        t += step

    return deduplicate_highlights(candidates, max_highlights)


def finalize_highlight_clips(
    raw_items: list[LLMHighlightItem],
    scenes: list[float],
    words: list[dict[str, Any]],
    duration: float,
    words_per_line: int = 3,
    min_duration: float = 8.0,
) -> list[dict[str, Any]]:
    """Snap boundaries to scene cuts/pauses and attach clip-relative word cues."""
    finalized: list[dict[str, Any]] = []
    for item in raw_items:
        snapped_start, snapped_end = snap_clip_boundaries(
            start=item.start_time,
            end=item.end_time,
            scenes=scenes,
            words=words,
            duration=duration,
            min_duration=min_duration,
        )
        cues = build_clip_subtitles(words, snapped_start, snapped_end, words_per_line=words_per_line)
        finalized.append(
            {
                "title": item.title.strip() or "Виральный момент",
                "hook_text": item.hook_text.strip(),
                "description": item.description.strip(),
                "tags_json": item.tags or ["#shorts", "#fyp"],
                "virality_score": float(item.virality_score),
                "category": item.category,
                "reason": item.reason.strip(),
                "start_time": snapped_start,
                "end_time": snapped_end,
                "subtitles_json": cues,
            }
        )
    return finalized
