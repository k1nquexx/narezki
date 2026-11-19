from __future__ import annotations

from pathlib import Path
from typing import Any


def hex_to_ass_color(hex_color: str, alpha: int = 0) -> str:
    """Convert #RRGGBB hex string to ASS &HAABBGGRR format."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) != 6:
        cleaned = "FFFFFF"
    try:
        r = int(cleaned[0:2], 16)
        g = int(cleaned[2:4], 16)
        b = int(cleaned[4:6], 16)
    except ValueError:
        r, g, b = 255, 255, 255
    a = max(0, min(255, int(alpha)))
    return f"&H{a:02X}{b:02X}{g:02X}{r:02X}"


def _fmt_ass_time(seconds: float) -> str:
    total_cs = int(round(max(0.0, float(seconds)) * 100.0))
    cs = total_cs % 100
    total_sec = total_cs // 100
    sec = total_sec % 60
    m = (total_sec // 60) % 60
    h = total_sec // 3600
    return f"{h}:{m:02d}:{sec:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", r"\\")
        .replace("{", r"\{")
        .replace("}", r"\}")
        .replace("\r\n", r"\N")
        .replace("\n", r"\N")
    )


def format_ass_filter_path(ass_path: Path) -> str:
    """Escape a Windows/POSIX path for FFmpeg's ass=filename='...' filter inside filter_complex_script."""
    posix = ass_path.resolve().as_posix().replace("\\", "/")
    posix = posix.replace(":", r"\:")
    posix = posix.replace("'", r"'\''")
    return f"ass=filename='{posix}'"


def generate_ass_file(
    cues: list[dict[str, Any]],
    preset: dict[str, Any],
    output_path: Path,
    subtitles_enabled: bool = True,
    hook_enabled: bool = False,
    hook_text: str = "",
    clip_duration: float = 15.0,
) -> Path:
    """Write a stylized 1080x1920 ASS subtitle script with karaoke/word-highlight + optional 3s hook."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_name = str(preset.get("font_name") or "Arial")
    font_size = int(preset.get("font_size") or 68)
    primary_ass = hex_to_ass_color(str(preset.get("primary_color") or "#FFFFFF"))
    highlight_ass = hex_to_ass_color(str(preset.get("highlight_color") or "#FFE600"))
    outline_ass = hex_to_ass_color(str(preset.get("outline_color") or "#000000"))
    back_ass = "&H80000000"
    outline_w = int(preset.get("outline_width") if preset.get("outline_width") is not None else 5)
    shadow_d = int(preset.get("shadow_depth") if preset.get("shadow_depth") is not None else 3)
    bold_flag = -1 if preset.get("bold", True) else 0
    uppercase = bool(preset.get("uppercase", True))
    margin_v = max(80, min(950, int(preset.get("margin_bottom") or 380)))
    karaoke_mode = str(preset.get("karaoke_mode") or "karaoke_fill")

    hook_bg_ass = hex_to_ass_color(str(preset.get("hook_bg_color") or "#7C3AED"), alpha=20)
    hook_fg_ass = hex_to_ass_color(str(preset.get("hook_text_color") or "#FFFFFF"))

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "WrapStyle: 1",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        # Karaoke fills from SecondaryColour -> PrimaryColour! So SecondaryColour=primary_ass, PrimaryColour=highlight_ass
        (
            f"Style: Default,{font_name},{font_size},{highlight_ass},{primary_ass},{outline_ass},{back_ass},"
            f"{bold_flag},0,0,0,100,100,0,0,1,{outline_w},{shadow_d},2,80,140,{margin_v},1"
        )
        if karaoke_mode == "karaoke_fill"
        else (
            f"Style: Default,{font_name},{font_size},{primary_ass},{highlight_ass},{outline_ass},{back_ass},"
            f"{bold_flag},0,0,0,100,100,0,0,1,{outline_w},{shadow_d},2,80,140,{margin_v},1"
        ),
        (
            f"Style: Hook,{font_name},{max(48, int(font_size * 0.85))},{hook_fg_ass},{hook_fg_ass},"
            f"{hook_bg_ass},{hook_bg_ass},-1,0,0,0,100,100,0,0,3,22,0,8,70,70,185,1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    if hook_enabled and hook_text.strip():
        hook_end = min(3.2, max(1.0, clip_duration))
        clean_hook = _escape_ass_text(hook_text.strip().upper())
        lines.append(
            f"Dialogue: 2,{_fmt_ass_time(0.0)},{_fmt_ass_time(hook_end)},Hook,,0,0,0,,"
            f"{{\\fad(180,260)}}{clean_hook}"
        )

    if subtitles_enabled and cues:
        for cue in cues:
            c_start = max(0.0, float(cue.get("start", 0.0)))
            c_end = max(c_start + 0.08, float(cue.get("end", c_start + 0.5)))
            words = cue.get("words") or []
            raw_text = str(cue.get("text") or "").strip()

            if not words or karaoke_mode == "phrase":
                txt = raw_text.upper() if uppercase else raw_text
                if txt:
                    lines.append(
                        f"Dialogue: 1,{_fmt_ass_time(c_start)},{_fmt_ass_time(c_end)},Default,,0,0,0,,"
                        f"{_escape_ass_text(txt)}"
                    )
                continue

            if karaoke_mode == "karaoke_fill":
                parts: list[str] = []
                cursor = c_start
                for idx, w in enumerate(words):
                    w_txt = str(w.get("word", "")).strip()
                    if uppercase:
                        w_txt = w_txt.upper()
                    ws = max(cursor, float(w.get("start", cursor)))
                    we = max(ws + 0.05, float(w.get("end", ws + 0.15)))
                    if ws - cursor >= 0.03:
                        gap_cs = int(round((ws - cursor) * 100))
                        if gap_cs > 0:
                            parts.append(f"{{\\k{gap_cs}}}")
                    dur_cs = max(5, int(round((we - ws) * 100)))
                    prefix = " " if idx > 0 else ""
                    parts.append(f"{{\\kf{dur_cs}}}{prefix}{_escape_ass_text(w_txt)}")
                    cursor = we
                lines.append(
                    f"Dialogue: 1,{_fmt_ass_time(c_start)},{_fmt_ass_time(c_end)},Default,,0,0,0,,"
                    + "".join(parts)
                )
            else:
                # word_highlight: emit separate dialogue line per active word interval
                for active_idx, w in enumerate(words):
                    ws = max(c_start, float(w.get("start", c_start)))
                    if active_idx + 1 < len(words):
                        we = max(ws + 0.05, float(words[active_idx + 1].get("start", w.get("end", ws + 0.15))))
                    else:
                        we = max(ws + 0.05, c_end)
                    tokens: list[str] = []
                    for j, wj in enumerate(words):
                        w_txt = str(wj.get("word", "")).strip()
                        if uppercase:
                            w_txt = w_txt.upper()
                        esc = _escape_ass_text(w_txt)
                        if j == active_idx:
                            tokens.append(
                                f"{{\\1c{highlight_ass}\\fscx108\\fscy108}}{esc}{{\\1c{primary_ass}\\fscx100\\fscy100}}"
                            )
                        else:
                            tokens.append(esc)
                    lines.append(
                        f"Dialogue: 1,{_fmt_ass_time(ws)},{_fmt_ass_time(we)},Default,,0,0,0,,"
                        + " ".join(tokens)
                    )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    return output_path
