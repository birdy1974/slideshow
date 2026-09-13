"""Dynamic text effects — the dual engine behind the three animation slots.

Option 1 from docs/text-effects-options.md: every effect renders either as
**animated drawtext expressions** (alpha/x/y maths on the filter the renderer
already used) or as a **per-clip .ass subtitle file** burned in by libass
(``ass=filename=...:fontsdir=...``; libass has been compiled into the custom
FFmpeg build since the xfade-easing work). ``build_text_overlay`` is the
dispatcher the renderer calls; it returns the filter string or ``None``.

The three slots stored on each media item are:

* ``textFxEnter`` — how the text appears     (duration: ``textEnterDuration``)
* ``textFxWhile`` — what it does while shown (loop period: ``textFxWhileSpeed``)
* ``textFxExit``  — how it disappears        (duration: ``textExitDuration``)

Values are friendly labels from ``registry/text-effects.json`` — the same file
the GUI reads, so the two sides can never drift. Labels from projects saved
before this feature existed resolve to the historic behaviour (Fade in /
Fade out / static) rendered by the exact drawtext filter this module replaces,
so old projects render exactly as they always did.

Composition rules (kept simple so a render can never disagree with the editor):

* Tag-based effects (fades, blur, zoom, rotate, wipes, split fades, karaoke)
  stack: enter tags + while tags + exit tags live in one override block.
* The frame-sliced "while" effects (Shake, Glitch flicker, Count up) own the
  whole timeline: they draw their own stepped enter/exit fades and demote the
  other two slots to plain fades.
* Sliced enter/exit effects (Typewriter + caret, Decrypted scramble,
  Typewriter delete, Scramble out) trim the running events and append their
  slices; they compose with tag-based effects in the other slots.
* The engine is ``ass`` when any chosen effect needs libass, otherwise the
  animated drawtext path runs (cheaper, and byte-compatible with the legacy
  filter when every slot is at its historic default).
"""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Registry (mirrors the transition registry loading in renderer.py)
# --------------------------------------------------------------------------


def _registry_candidates() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SLIDESHOW_REGISTRY")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "registry" / "text-effects.json")  # repo checkout
    cands.append(Path("/app/registry/text-effects.json"))             # Docker image
    cands.append(Path.cwd() / "registry" / "text-effects.json")
    return cands


def text_effect_catalog() -> list[dict[str, Any]]:
    """Every catalogue entry in registry order (id/label/group/slot/engine/...)."""
    for path in _registry_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.get("effects") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            slot = str(entry.get("slot", "")).strip()
            label = str(entry.get("label", "")).strip()
            engine = str(entry.get("engine", "ass")).strip() or "ass"
            if slot not in ("enter", "while", "exit") or not label:
                continue
            cleaned.append({**entry, "slot": slot, "label": label, "engine": engine})
        if cleaned:
            return cleaned
    log.warning("registry/text-effects.json not found; text effects degrade to the legacy fade")
    return []


_CATALOG: list[dict[str, Any]] | None = None
_BY_LABEL: dict[str, dict[str, Any]] | None = None


def _by_label() -> dict[str, dict[str, Any]]:
    global _CATALOG, _BY_LABEL
    if _BY_LABEL is None:
        _CATALOG = text_effect_catalog()
        _BY_LABEL = {str(entry["label"]): entry for entry in _CATALOG}
    return _BY_LABEL


def effect_for(label: str | None, slot: str) -> dict[str, Any] | None:
    """Registry entry for a stored label, or the slot's historic default.

    Unknown labels (renamed catalogue, damaged project) degrade to the slot
    default — Fade for enter/exit, static for while — the same way unknown
    transition labels degrade to Fade.
    """
    if label:
        entry = _by_label().get(str(label).strip())
        if entry and entry.get("slot") == slot:
            return entry
    return _by_label().get({"enter": "Fade", "exit": "Fade out", "while": "None (static)"}[slot])


# --------------------------------------------------------------------------
# Small shared helpers
# --------------------------------------------------------------------------


def _n(value: float) -> str:
    """Compact number for FFmpeg filter expressions."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _num(item: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(item.get(key, default))
    except (TypeError, ValueError):
        return default
    return default if value != value else value  # NaN guard


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# Keep the renderer's text window inside the clip's visible hold.  The
# storyline uses the same 0.1 s minimum for its draggable handles; keeping the
# rule here (rather than relying on the UI having sanitised a saved project) is
# important because the outgoing transition starts immediately after this
# hold.  A text event that leaks past the hold is already baked into the
# outgoing segment when xfade begins, so the next picture can cover it.
TEXT_TIMING_MIN_SECONDS = 0.1
TEXT_TIMING_MIN_CLIP_SECONDS = 0.2


def normalize_text_window(item: dict[str, Any]) -> tuple[float, float]:
    """Return ``textStart``/``textEnd`` clamped to the item's visible hold.

    ``textStart`` and ``textEnd`` are relative to the clip's hold, not its
    incoming/outgoing transition handles.  The renderer may temporarily add an
    incoming handle to ``duration`` before calling this helper, which lets it
    shift a valid window without moving it into the outgoing handle.
    """
    try:
        clip_duration = float(item.get("duration", 5.0))
    except (TypeError, ValueError):
        clip_duration = 5.0
    if not math.isfinite(clip_duration):
        clip_duration = 5.0
    clip_duration = max(TEXT_TIMING_MIN_CLIP_SECONDS, clip_duration)
    minimum = min(TEXT_TIMING_MIN_SECONDS, clip_duration)

    start_value = _num(item, "textStart", 0.0)
    start = _clamp(start_value, 0.0, max(0.0, clip_duration - minimum))
    end_value = _num(item, "textEnd", clip_duration)
    end = _clamp(end_value, start + minimum, clip_duration)
    return start, end


def ass_time(seconds: float) -> str:
    """ASS timestamp ``H:MM:SS.CS`` (centiseconds, clamped at zero)."""
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def ass_ms(seconds: float) -> int:
    """Milliseconds from an event-relative offset, clamped at zero."""
    return int(round(max(0.0, seconds) * 1000))


def ass_colour(hex_colour: str) -> str:
    """``#RRGGBB`` → ASS ``&HBBGGRR&``."""
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hex_colour or "").strip())
    if not match:
        return "&HFFFFFF&"
    r, g, b = match.group(1)[0:2], match.group(1)[2:4], match.group(1)[4:6]
    return f"&H{b}{g}{r}&".upper()


def escape_ass(text: str) -> str:
    """Defuse override-block braces in user text; everything else passes through."""
    return str(text).replace("{", "(").replace("}", ")")


def ff_escape_drawtext(value: str) -> str:
    """Mirror of the renderer's historic drawtext escaping (legacy parity)."""
    return (
        str(value)
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


# --------------------------------------------------------------------------
# Shared geometry for one text overlay
# --------------------------------------------------------------------------


@dataclass
class TextGeometry:
    width: int
    height: int
    size: int                # font size in output pixels (matches the legacy drawtext)
    cx: float                # anchor point in pixels (centre of the text block)
    cy: float
    start: float             # text window inside the clip (seconds)
    end: float
    di: float                # enter effect duration
    do: float                # exit effect duration
    speed: float             # "while" loop period (seconds)
    text: str
    lines: list[str]
    family: str
    bold: bool
    italic: bool
    colour: str              # #RRGGBB
    params: dict[str, str]   # per-effect parameters (Count up from/to, ...)
    outline: bool = True     # dark outline + shadow behind picture captions


def _geometry(item: dict[str, Any], defaults: dict[str, Any], width: int, height: int) -> TextGeometry | None:
    text = str(item.get("text", "")).strip()
    if not text:
        return None
    # Per-slide opt-out: captions can be disabled without deleting the text.
    # Title frames are the text itself, so the flag never applies to them.
    if item.get("type") != "title" and item.get("textEnabled") is False:
        return None
    start, end = normalize_text_window(item)
    hold = end - start
    di = _clamp(_num(item, "textEnterDuration", 0.5), 0.05, hold * 0.9)
    do = _clamp(_num(item, "textExitDuration", 0.5), 0.05, hold * 0.9)
    speed = _clamp(_num(item, "textFxWhileSpeed", 2.0), 0.4, 12.0)
    # Title frames carry their own type settings. Picture captions use the
    # project-wide defaults so changing "Default text style" never restyles a
    # standalone text card — the same rule the legacy filter applied.
    if item.get("type") == "title":
        size_pt = _num(item, "fontSize", 48)
        colour_raw = str(item.get("fontColor") or "#ffffff")
        bold = bool(item.get("textBold", True))
        italic = bool(item.get("textItalic", False))
        family = str(item.get("fontFamily") or "Montserrat")
        # Text frames sit on a flat colour bed of the user's choosing: no
        # outline, exactly as before.
        outline = False
    else:
        size_pt = _num(defaults, "fontSize", 48)
        colour_raw = str(defaults.get("fontColor") or "#ffffff")
        bold = bool(defaults.get("bold", True))
        italic = bool(defaults.get("italic", False))
        family = str(defaults.get("fontFamily") or "Montserrat")
        # "Outline & shadow" in Default text style (on unless switched off):
        # keeps white captions readable on bright photos.
        outline = defaults.get("outline", True) is not False
    raw_params = item.get("textFxParams")
    params = {str(k): str(v) for k, v in raw_params.items()} if isinstance(raw_params, dict) else {}
    colour = colour_raw if re.fullmatch(r"#[0-9a-fA-F]{6}", colour_raw or "") else "#ffffff"
    return TextGeometry(
        width=width, height=height,
        size=max(8, int(float(size_pt) * width / 1920)),
        cx=width * _clamp(_num(item, "textX", 50.0), 0.0, 100.0) / 100.0,
        cy=height * _clamp(_num(item, "textY", 72.0), 0.0, 100.0) / 100.0,
        start=start, end=end, di=di, do=do, speed=speed,
        text=text, lines=text.split("\n"),
        family=family, bold=bold, italic=italic, colour=colour, params=params,
        outline=outline,
    )


@dataclass
class FxPlan:
    enter: dict[str, Any]
    while_: dict[str, Any]
    exit_: dict[str, Any]

    def ids(self) -> tuple[str, str, str]:
        return str(self.enter.get("id")), str(self.while_.get("id")), str(self.exit_.get("id"))


# --------------------------------------------------------------------------
# Engine 1 — animated drawtext expressions
# --------------------------------------------------------------------------


def _dt_alpha(g: TextGeometry) -> str:
    si, ei = g.start + g.di, g.end - g.do
    return (
        f"if(lt(t,{_n(g.start)}),0,if(lt(t,{_n(si)}),min(1,(t-{_n(g.start)})/{_n(g.di)}),"
        f"if(lt(t,{_n(ei)}),1,if(lt(t,{_n(g.end)}),max(0,({_n(g.end)}-t)/{_n(g.do)}),0))))"
    )


def _dt_offsets(g: TextGeometry, enter: str, exit_: str, while_: str) -> tuple[str, str]:
    """Enter/while/exit positional maths as (x_suffix, y_suffix) for drawtext."""
    xs: list[str] = []
    ys: list[str] = []
    u_in = f"clip((t-{_n(g.start)})/{_n(g.di)},0,1)"
    u_out = f"clip((t-({_n(g.end)}-{_n(g.do)}))/{_n(g.do)},0,1)"
    u_loop = f"mod((t-{_n(g.start)})/{_n(max(0.4, g.speed))},1)"
    slide = g.width * 0.3
    drop = g.height * 0.24
    if enter == "Slide from left":
        xs.append(f"{_n(slide)}*(1-{u_in})")
    elif enter == "Slide from right":
        xs.append(f"-{_n(slide)}*(1-{u_in})")
    elif enter == "Slide from top":
        ys.append(f"-{_n(g.height * 0.18)}*(1-{u_in})")
    elif enter == "Slide from bottom":
        ys.append(f"{_n(g.height * 0.18)}*(1-{u_in})")
    elif enter == "Rise & settle":
        # Damped spring: rises from below, overshoots slightly, settles.
        u = f"clip((t-{_n(g.start)})/{_n(g.di)},0,2)"
        ys.append(f"{_n(g.height * 0.08)}*exp(-5*{u})*cos(7*{u})")
    elif enter == "Drop & bounce":
        # Accelerating fall (half the window), then two damped bounces.
        ys.append(
            f"if(lt({u_in},0.55),-{_n(drop)}*(1-pow({u_in}/0.55,2)),"
            f"-{_n(drop * 0.22)}*abs(sin(({u_in}-0.55)*12.566))*(1-{u_in}))"
        )
    elif enter == "Slide & overshoot":
        # easeOutBack: approaches from one side, runs past, eases back.
        c1, c3 = 1.70158, 2.70158
        u = f"clip((t-{_n(g.start)})/{_n(g.di)},0,1)"
        xs.append(f"-{_n(slide * 0.8)}*({c3}*pow({u}-1,3)+{c1}*pow({u}-1,2)+1)")
    if while_ == "Gentle float":
        ys.append(f"-{_n(g.height * 0.012)}*sin(2*PI*{u_loop})")
    elif while_ == "Horizontal drift":
        xs.append(f"{_n(g.width * 0.02)}*sin(2*PI*{u_loop})")
    if exit_ == "Slide out left":
        xs.append(f"-{_n(slide)}*{u_out}")
    elif exit_ == "Slide out right":
        xs.append(f"{_n(slide)}*{u_out}")
    elif exit_ == "Slide out top":
        ys.append(f"-{_n(g.height * 0.18)}*{u_out}")
    elif exit_ == "Slide out bottom":
        ys.append(f"{_n(g.height * 0.18)}*{u_out}")
    elif exit_ == "Sink & fade":
        ys.append(f"{_n(g.height * 0.05)}*{u_out}*{u_out}")
    return (f"+({'+'.join(xs)})" if xs else "", f"+({'+'.join(ys)})" if ys else "")


def _drawtext_filter(g: TextGeometry, font: str, enter: str, exit_: str, while_: str) -> str:
    """The historic filter when every slot is at its default, animated otherwise."""
    if enter == "Fade" and while_ in ("", "None (static)") and exit_ == "Fade out":
        # Byte-compatible with the pre-effects renderer (docs parity).
        fade_in = max(0.01, g.di)
        fade_out = max(0.01, g.do)
        alpha = (
            f"if(lt(t,{_n(g.start)}),0,if(lt(t,{_n(g.start + fade_in)}),(t-{_n(g.start)})/{_n(fade_in)},"
            f"if(lt(t,{_n(g.end - fade_out)}),1,if(lt(t,{_n(g.end)}),({_n(g.end)}-t)/{_n(fade_out)},0))))"
        )
        x_expr = f"(w-text_w)*{_n(g.cx / g.width)}"
        y_expr = f"(h-text_h)*{_n(g.cy / g.height)}"
    else:
        alpha = _dt_alpha(g)
        dx, dy = _dt_offsets(g, enter, exit_, while_)
        x_expr = f"(w-text_w)*{_n(g.cx / g.width)}{dx}"
        y_expr = f"(h-text_h)*{_n(g.cy / g.height)}{dy}"
    return (
        f"drawtext=fontfile='{font}':text='{ff_escape_drawtext(g.text)}':fontsize={g.size}"
        f":fontcolor=0x{g.colour[1:]}:alpha='{alpha}':x='{x_expr}':y='{y_expr}'"
        f":shadowcolor=black@0.55:shadowx=2:shadowy=2{_dt_outline(g)}:enable='between(t,{_n(g.start)},{_n(g.end)})'"
    )


def outline_width(g: TextGeometry) -> int:
    """Outline thickness in pixels: ~1/16 em, at least 1 px, 0 when off."""
    return max(1, round(g.size / 16)) if g.outline else 0


def _dt_outline(g: TextGeometry) -> str:
    w = outline_width(g)
    return f":borderw={w}:bordercolor=black@0.85" if w else ""


# --------------------------------------------------------------------------
# Engine 2 — libass (.ass file per clip)
# --------------------------------------------------------------------------

CHAR_W = 0.66      # average glyph width estimate (em) — used for clip/bar boxes only
LINE_H = 1.3       # line height estimate (em)
SCRAMBLE_GLYPHS = "#@$%&*+=<>?/\\|"
SHADOW_STYLE = "&H73000000&"   # black @ 0.55, the drawtext shadow
OUTLINE_STYLE = "&H26000000&"  # black @ 0.85, the drawtext bordercolor


def _header(g: TextGeometry) -> str:
    style = (
        f"Style: FX,{g.family.replace(',', ' ')},{g.size},{ass_colour(g.colour)},&HFFFFFF&,{OUTLINE_STYLE},{SHADOW_STYLE},"
        f"{'-1' if g.bold else '0'},{'-1' if g.italic else '0'},0,0,100,100,0,0,1,{outline_width(g)},2,5,0,0,0,1"
    )
    return (
        "[Script Info]\n"
        "; Slideshow dynamic text effects — generated by backend/app/text_effects.py\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {g.width}\n"
        f"PlayResY: {g.height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    )


def _ev(layer: int, start: float, end: float, tags: str, text: str) -> str:
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(max(end, start + 0.02))},FX,,0,0,0,,{{{tags}}}{text}"


def _multiline(g: TextGeometry) -> str:
    return r"\N".join(escape_ass(line) for line in g.lines)


def _pos_tags(g: TextGeometry) -> str:
    return f"\\an5\\pos({g.cx:.0f},{g.cy:.0f})"


def _text_bbox(g: TextGeometry) -> tuple[int, int, int, int]:
    """Estimated playres-space box around the text block (centre-anchored)."""
    longest = max((len(line) for line in g.lines), default=1)
    w = longest * g.size * CHAR_W + g.size * 0.4
    h = len(g.lines) * g.size * LINE_H
    return int(g.cx - w / 2), int(g.cy - h / 2), int(g.cx + w / 2), int(g.cy + h / 2)


# ---- while-loop tag builders ---------------------------------------------


def _loop_tags(g: TextGeometry, pattern: Callable[[int, int, int], str]) -> str:
    """Chained \\t segments repeating `pattern(cycle, t0_ms, period_ms)`."""
    hold_ms = int(round((g.end - g.start) * 1000))
    period = max(200, int(round(g.speed * 1000)))
    count = max(1, min(30, int(math.ceil(hold_ms / period))))
    return "".join(pattern(cycle, cycle * period, period) for cycle in range(count))


def _while_tags(g: TextGeometry, while_id: str) -> str:
    """Tag-based "while shown" effects (structural ones never reach this)."""
    if while_id == "slow-zoom":
        hold_ms = int(round((g.end - g.start) * 1000))
        return f"\\t(0,{hold_ms},\\fscx110\\fscy110)"
    if while_id == "pulse":
        def pulse(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\fscx112\\fscy112)\\t({t0 + half},{t0 + period},\\fscx100\\fscy100)"
        return _loop_tags(g, pulse)
    if while_id == "neon-glow":
        def breathe(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\blur6)\\t({t0 + half},{t0 + period},\\blur1)"
        return f"\\bord3\\3c{ass_colour(g.colour)}" + _loop_tags(g, breathe)
    if while_id == "colour-cycle":
        palette = ["&H55D7FF&", "&H66FFB2&", "&HFF9C66&", "&HB29CFF&"]
        seg = max(100, int(round(g.speed * 1000)) // len(palette))

        def cycle_colours(cycle: int, t0: int, period: int) -> str:
            parts = []
            for k in range(len(palette)):
                parts.append(f"\\t({t0 + k * seg},{t0 + (k + 1) * seg},\\1c{palette[(cycle + k) % len(palette)]})")
            return "".join(parts)
        return _loop_tags(g, cycle_colours)
    if while_id == "gentle-float":
        # libass fallback of the drawtext float: a slow breathing bob.
        def float_bob(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\fscy103\\fscx99)\\t({t0 + half},{t0 + period},\\fscy100\\fscx100)"
        return _loop_tags(g, float_bob)
    if while_id == "horizontal-drift":
        def sway(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\frz0.8)\\t({t0 + half},{t0 + period},\\frz-0.8)"
        return _loop_tags(g, sway)
    return ""


def _stagger_loop_tags(g: TextGeometry, count: int, pattern: Callable[[int, int, int, int], str]) -> list[str]:
    """Per-unit looping transforms with a phase stagger: `pattern(cycle, t0, period, phase)`."""
    hold_ms = int(round((g.end - g.start) * 1000))
    period = max(200, int(round(g.speed * 1000)))
    cycles = max(1, min(30, int(math.ceil(hold_ms / period))))
    phase = period / max(1, count)
    out = []
    for unit in range(count):
        offset = int(round((unit % max(1, count)) * phase)) % period
        out.append("".join(pattern(cycle, cycle * period, period, offset) for cycle in range(cycles)))
    return out


# ---- per-char / per-word scaffolding --------------------------------------


def _units(g: TextGeometry, by_word: bool) -> list[str]:
    """Characters (or words) of the text; line breaks become newline markers."""
    units: list[str] = []
    for line in g.lines:
        if by_word:
            words = line.split(" ")
            units.extend(word + " " for word in words)
            if units:
                units[-1] = units[-1].rstrip(" ") or "\n"
        else:
            units.extend(line)
        units.append("\n")
    return [unit for unit in units if unit != ""]


def _visible_units(units: list[str]) -> list[int]:
    return [i for i, unit in enumerate(units) if unit not in ("\n", " ")]


def _inline_units(units: list[str], tag_for: Callable[[int, str], str]) -> str:
    """Wrap each unit in its own override block: {tags}unit (newlines → \\N)."""
    parts: list[str] = []
    for index, unit in enumerate(units):
        if unit == "\n":
            parts.append(r"\N")
        elif unit == " ":
            parts.append(" ")
        else:
            tags = tag_for(index, unit)
            parts.append(f"{{{tags}}}{escape_ass(unit)}" if tags else escape_ass(unit))
    return "".join(parts)


def _hidden_line_text(units: list[str], visible_index: int, visible_tags: str) -> str:
    """Render the full text with exactly one unit visible.

    Every event for the same text lays out identically, so libass centres all
    of them at the same point — true per-character motion with zero glyph
    measuring. The invisible units ride along at alpha FF; the visible one
    carries `visible_tags`.
    """
    parts = ["{\\alpha&HFF&}"]
    for index, unit in enumerate(units):
        if unit == "\n":
            parts.append(r"\N")
        elif index == visible_index:
            parts.append(f"{{\\alpha&H00&{visible_tags}}}{escape_ass(unit)}")
        else:
            parts.append(escape_ass(unit) if unit != " " else " ")
    return "".join(parts)


# ---- structural generators ------------------------------------------------


def _alpha_at(g: TextGeometry, t: float) -> str:
    """Stepped opacity for sliced events (quantised version of the fade window)."""
    si, ei = g.start + g.di, g.end - g.do
    if t < g.start or t >= g.end:
        return "&HFF&"
    if si > g.start and t < si:
        return f"&H{int(0xFF * (1 - (t - g.start) / max(1e-6, g.di))):02X}&"
    if g.end > ei and t > ei:
        return f"&H{int(0xFF * min(1.0, (t - ei) / max(1e-6, g.do))):02X}&"
    return "&H00&"


def _structural_while_events(g: TextGeometry, while_id: str) -> list[str]:
    """Shake / Glitch flicker / Count up: one event per short slice.

    These own the timeline — the stepped enter/exit fades are baked into every
    slice, so the other slots' choices are intentionally demoted to fades.
    """
    hold = g.end - g.start
    tick = max(1.0 / 10.0, hold / 240.0)
    seed = int(g.start * 1000) + len(g.text) * 7919 + sum(ord(ch) for ch in g.text[:24])
    rng = random.Random(seed)
    events: list[str] = []
    if while_id == "count-up":
        try:
            lo = float(str(g.params.get("from", "0")).strip() or 0)
        except ValueError:
            lo = 0.0
        try:
            hi = float(str(g.params.get("to", "100")).strip() or 0)
        except ValueError:
            hi = 100.0
        steps = max(2, min(240, int(round(hold / tick))))
        for i in range(steps):
            t0 = g.start + hold * i / steps
            value = int(round(lo + (hi - lo) * (i / (steps - 1))))
            events.append(_ev(0, t0, g.start + hold * (i + 1) / steps,
                              f"{_pos_tags(g)}\\alpha{_alpha_at(g, t0 + tick / 2)}", str(value)))
        return events
    amp = max(2.0, g.size * 0.05)
    t = g.start
    while t < g.end - 1e-3:
        end = min(t + tick, g.end)
        mid = t + tick / 2
        alpha = _alpha_at(g, mid)
        if while_id == "shake":
            dx, dy = rng.uniform(-amp, amp), rng.uniform(-amp, amp)
            events.append(_ev(0, t, end, f"\\an5\\pos({g.cx + dx:.0f},{g.cy + dy:.0f})\\alpha{alpha}", _multiline(g)))
        else:  # glitch-flicker
            roll = rng.random()
            if roll >= 0.12:  # the rest of the rolls keep the text flicked off
                jitter = max(1, int(g.size * 0.04))
                dx, dy = rng.randint(-jitter, jitter), rng.randint(-jitter // 2, jitter // 2)
                tags = f"\\an5\\pos({g.cx + dx:.0f},{g.cy + dy:.0f})\\alpha{alpha}"
                if roll > 0.78:  # occasional colour ghost
                    tags += f"\\3c{'&H5533FF&' if roll > 0.89 else '&HFF6644&'}\\bord{max(2, g.size // 26)}"
                events.append(_ev(0, t, end, tags, _multiline(g)))
        t = end
    if not events:
        events.append(_ev(0, g.start, g.end, _pos_tags(g), _multiline(g)))
    return events


def _char_stream(g: TextGeometry) -> list[str]:
    """Every character of the text with newline markers between lines."""
    chars: list[str] = []
    for li, line in enumerate(g.lines):
        chars.extend(line)
        if li < len(g.lines) - 1:
            chars.append("\n")
    return chars


def _join_chars(chars: list[str]) -> str:
    """Event text for a character slice; newline markers become \\N."""
    return escape_ass("".join(chars)).replace("\n", r"\N")


def _sliced_enter_events(g: TextGeometry, fx: FxPlan, while_tags: str, exit_tags: str, end_at: float | None = None) -> list[str]:
    """Typewriter + caret / Decrypted scramble: one event per reveal slice."""
    enter_id = str(fx.enter.get("id"))
    chars = _char_stream(g)
    total = len(chars)
    if not total:
        return [_ev(0, g.start, end_at or g.end, _pos_tags(g) + while_tags + exit_tags, _multiline(g))]
    step = max(0.03, g.di / total)
    seed = int(g.di * 1000) + total * 31 + sum(ord(ch) for ch in g.text[:24])
    rng = random.Random(seed)
    events: list[str] = []
    final_end = end_at or g.end
    for i in range(total):
        t0 = g.start + i * step
        # Each slice hands over to the next one; only the last runs to the end.
        t1 = t0 + step if i < total - 1 else final_end
        if enter_id == "typewriter-caret":
            text = _join_chars(chars[: i + 1]) + "{\\alpha&H77&}|"
        else:  # decrypted-scramble
            remaining = [c for c in chars[i + 1:] if c != "\n"]
            noise = "".join(rng.choice(SCRAMBLE_GLYPHS) for _ in remaining)
            text = _join_chars(chars[: i + 1]) + escape_ass(noise)
        events.append(_ev(0, t0, t1, _pos_tags(g) + while_tags + exit_tags, text))
    return events


def _sliced_exit_appends(g: TextGeometry, exit_id: str) -> tuple[float, list[str]]:
    """Typewriter delete / Scramble out → (base end time, trailing slice events)."""
    chars = _char_stream(g)
    total = max(1, len(chars))
    hold_end = max(g.start + 0.1, g.end - g.do)
    step = max(0.03, g.do / total)
    seed = int(g.do * 1000) + total * 17 + sum(ord(ch) for ch in g.text[:24])
    rng = random.Random(seed)
    events: list[str] = []
    for i in range(total):
        t0 = hold_end + i * step
        # Each slice hands over to the next one; only the last runs to the end.
        t1 = t0 + step if i < total - 1 else g.end
        if exit_id == "typewriter-delete":
            text = _join_chars(chars[: max(0, total - 1 - i)])
        else:  # scramble-out
            keep = max(0, total - 1 - i)
            noise_len = max(0, total - keep - "".join(chars[keep:]).count("\n"))
            noise = "".join(rng.choice(SCRAMBLE_GLYPHS) for _ in range(noise_len))
            text = _join_chars(chars[:keep]) + escape_ass(noise)
        if not text.replace(r"\N", "").strip():
            continue
        events.append(_ev(0, t0, t1, _pos_tags(g), text))
    return hold_end - 0.02, events


def _split_rise_events(g: TextGeometry, while_tags: str, exit_tags: str, end_at: float) -> list[str]:
    """Split rise · chars: one event per character (hidden-line trick, no glyph maths)."""
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    stagger = g.di / count
    move_ms = ass_ms(max(0.22, min(0.45, stagger * 3)))
    rise = int(g.size * 0.9)
    events: list[str] = []
    for order, index in enumerate(visible):
        offset_ms = ass_ms(stagger * order)
        tags = (
            f"{_pos_tags(g)}\\move({g.cx:.0f},{g.cy + rise},{g.cx:.0f},{g.cy:.0f},{offset_ms},{offset_ms + move_ms})"
            f"{while_tags}{exit_tags}"
        )
        char_tags = f"\\alpha&HFF&\\t({offset_ms},{offset_ms + 40},\\alpha&H00&)"
        events.append(_ev(0, g.start, end_at, tags, _hidden_line_text(units, index, char_tags)))
    return events


def _split_centre_events(g: TextGeometry, while_tags: str, exit_tags: str, end_at: float) -> list[str]:
    """Split from centre: each character unfolds outward from the line centre."""
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    stagger = g.di / count
    grow_ms = ass_ms(max(0.25, min(0.5, stagger * 3)))
    events: list[str] = []
    for order, index in enumerate(visible):
        offset_ms = ass_ms(stagger * order)
        tags = f"{_pos_tags(g)}\\org({g.cx:.0f},{g.cy:.0f}){while_tags}{exit_tags}"
        char_tags = (
            f"\\alpha&HFF&\\fscx0\\fscy0\\t({offset_ms},{offset_ms + 40},\\alpha&H00&)"
            f"\\t({offset_ms},{offset_ms + grow_ms},\\fscx100\\fscy100)"
        )
        events.append(_ev(0, g.start, end_at, tags, _hidden_line_text(units, index, char_tags)))
    return events


def _wipe_tags(g: TextGeometry, effect: dict[str, Any], enter: bool) -> str:
    """Animated \\clip reveal/retract shared by the 16 wipe effects."""
    left, top, right, bottom = _text_bbox(g)
    pad = max(4, g.size // 4)
    left, top, right, bottom = left - pad, top - pad, right + pad, bottom + pad
    direction = str(effect.get("id", "")).replace("wipe-from-", "").replace("wipe-out-", "")
    span_ms = int(round((g.end - g.start) * 1000))
    ms = max(60, int(round((g.di if enter else g.do) * 1000)))
    t0, t1 = (0, ms) if enter else (max(0, span_ms - ms), span_ms)

    def box(x1: int, y1: int, x2: int, y2: int) -> str:
        return f"\\clip({min(x1, x2)},{min(y1, y2)},{max(x1, x2)},{max(y1, y2)})"

    closed, full = {
        "left": (box(left, top, left, bottom), box(left, top, right, bottom)),
        "right": (box(right, top, right, bottom), box(left, top, right, bottom)),
        "top": (box(left, top, right, top), box(left, top, right, bottom)),
        "bottom": (box(left, bottom, right, bottom), box(left, top, right, bottom)),
        "top-left": (box(left, top, left, top), box(left, top, right, bottom)),
        "top-right": (box(right, top, right, top), box(left, top, right, bottom)),
        "bottom-left": (box(left, bottom, left, bottom), box(left, top, right, bottom)),
        "bottom-right": (box(right, bottom, right, bottom), box(left, top, right, bottom)),
    }.get(direction, (box(left, top, left, bottom), box(left, top, right, bottom)))
    if enter:
        return f"{closed}\\t({t0},{t1},{full})"
    return f"{full}\\t({t0},{t1},{closed})"


def _lower_third_events(g: TextGeometry, enter_id: str, exit_id: str, while_tags: str, extra_tags: str) -> list[str]:
    """News-style bar behind the text; the bar slides in and (optionally) retracts."""
    left, top, right, bottom = _text_bbox(g)
    pad = max(4, g.size // 3)
    bar_w = (right - left) + pad * 2
    bar_h = int(g.size * 1.55)
    bx, by = int(g.cx - bar_w / 2), int(g.cy - bar_h / 2)
    slide = int(bar_w * 1.2 + g.size * 2)
    enter_ms = ass_ms(g.di) if enter_id == "lower-third" else 240
    retract = exit_id == "lower-third-retract"
    exit_ms = ass_ms(g.do) if retract else 240
    span_ms = int(round((g.end - g.start) * 1000))
    exit_t0 = max(enter_ms + 10, span_ms - exit_ms)
    # libass cannot chain \move, so the retract collapses the bar horizontally
    # toward its leading edge (\org at the bar's left rim + a scale-out).
    # \p1 switches the event to vector drawing; \1c paints the bar black (the
    # drawing body is filled with the primary colour, which is the text
    # colour), and \move alone positions it (\pos would fight the \move).
    bar_tags = f"\\an7\\p1\\1c&H000000&\\bord0\\move({bx - slide},{by},{bx},{by},0,{enter_ms})\\alpha&H4D&"
    if retract:
        bar_tags += f"\\org({bx},{by + bar_h // 2})\\t({exit_t0},{span_ms},\\fscx0\\alpha&HFF&)"
    else:
        bar_tags += f"\\fad(0,{exit_ms})"
    bar_body = f"m 0 0 l {bar_w} 0 l {bar_w} {bar_h} l 0 {bar_h}{{\\p0}}"
    text_tags = f"\\move({g.cx - slide:.0f},{g.cy:.0f},{g.cx:.0f},{g.cy:.0f},0,{enter_ms})" if enter_id == "lower-third" else _pos_tags(g)
    return [
        _ev(0, g.start, g.end, bar_tags, bar_body),
        _ev(1, g.start, g.end, text_tags + extra_tags + while_tags, _multiline(g)),
    ]


# ---- tag builders for enter / exit ----------------------------------------


def _enter_tags(g: TextGeometry, effect: dict[str, Any]) -> tuple[str, str | None]:
    """(override tags, inline body) for tag-based enter effects.

    A non-None body replaces the event text (split fades need per-char blocks).
    """
    enter_id = str(effect.get("id"))
    di = ass_ms(g.di)
    tags = ""
    body: str | None = None
    if enter_id == "fade":
        tags += f"\\fad({di},0)"
    elif enter_id in ("slide-from-left", "slide-from-right", "slide-from-top", "slide-from-bottom",
                      "rise-settle", "drop-bounce", "slide-overshoot"):
        # libass twins of the drawtext slides: a \move over the enter window
        # plus a fade. Springs/bounces are approximated by a two-stage move
        # (overshoot past the target, then back), which reads the same at
        # caption sizes. The event's \pos is replaced by the \move.
        dx, dy = {
            "slide-from-left": (-g.width * 0.3, 0.0),
            "slide-from-right": (g.width * 0.3, 0.0),
            "slide-from-top": (0.0, -g.height * 0.18),
            "slide-from-bottom": (0.0, g.height * 0.18),
            "rise-settle": (0.0, g.height * 0.08),
            "drop-bounce": (0.0, -g.height * 0.24),
            "slide-overshoot": (-g.width * 0.24, 0.0),
        }[enter_id]
        if enter_id in ("rise-settle", "drop-bounce", "slide-overshoot"):
            # Overshoot: move most of the way in an accelerated first leg, the
            # remainder is a \t-scaled settle. libass has a single \move per
            # event, so the settle is expressed by a slight scale bounce.
            tags += (f"\\move({g.cx + dx:.0f},{g.cy + dy:.0f},{g.cx:.0f},{g.cy:.0f},0,{di})"
                     f"\\fad({max(1, di // 2)},0)"
                     f"\\t({int(di * 0.6)},{di},\\fscx106\\fscy106)\\t({di},{int(di * 1.35) + 1},\\fscx100\\fscy100)")
        else:
            tags += f"\\move({g.cx + dx:.0f},{g.cy + dy:.0f},{g.cx:.0f},{g.cy:.0f},0,{di})\\fad({di},0)"
    elif enter_id == "blur-in":
        tags += f"\\blur10\\alpha&HFF&\\t(0,{di},\\blur0\\alpha&H00&)"
    elif enter_id == "flicker-in":
        seg = max(1, di // 5)
        jump = max(1, di // 12)
        tags += "\\alpha&HFF&"
        for k, alpha in enumerate(("&H55&", "&HAA&", "&H22&", "&H66&", "&H00&")):
            tags += f"\\t({k * seg},{k * seg + jump},\\alpha{alpha})"
    elif enter_id == "pop-in":
        peak = max(1, int(di * 0.65))
        tags += f"\\fscx18\\fscy18\\alpha&H40&\\t(0,{peak},\\fscx114\\fscy114\\alpha&H00&)\\t({peak},{di},\\fscx100\\fscy100)"
    elif enter_id == "zoom-down":
        tags += f"\\fscx300\\fscy300\\alpha&HFF&\\t(0,{di},\\fscx100\\fscy100\\alpha&H00&)"
    elif enter_id == "flip-in":
        tags += f"\\fscy12\\alpha&H55&\\t(0,{di},\\fscy100\\alpha&H00&)"
    elif enter_id == "rotate-in":
        tags += f"\\frz-84\\alpha&HFF&\\t(0,{di},\\frz0\\alpha&H00&)"
    elif enter_id.startswith("wipe-from-"):
        tags += _wipe_tags(g, effect, enter=True)
    elif enter_id in ("split-fade-chars", "split-fade-words"):
        by_word = enter_id == "split-fade-words"
        units = _units(g, by_word)
        visible = _visible_units(units)
        stagger = g.di / max(1, len(visible))
        fade_ms = max(60 if not by_word else 80, ass_ms(stagger * 2))

        def staggered(index: int, unit: str) -> str:
            order = visible.index(index)
            t0 = ass_ms(stagger * order)
            return f"\\alpha&HFF&\\t({t0},{t0 + fade_ms},\\alpha&H00&)"
        body = _inline_units(units, staggered)
    elif enter_id == "typewriter":
        # Karaoke \k fills each character in from the transparent secondary
        # colour — the classic typewriter, still perfectly centred. The
        # secondary-alpha tag belongs in the override block, not the body.
        total = max(1, sum(len(line) for line in g.lines))
        step_cs = max(1, int(round(g.di * 100 / total)))
        tags += "\\2a&HFF&"
        pieces: list[str] = []
        for li, line in enumerate(g.lines):
            for ch in line:
                pieces.append(f"{{\\k{step_cs}}}{escape_ass(ch)}" if ch != " " else " ")
            if li < len(g.lines) - 1:
                pieces.append(r"\N")
        body = "".join(pieces)
    return tags, body


def _exit_tags(g: TextGeometry, effect: dict[str, Any]) -> str:
    """Override tags for tag-based exit effects."""
    exit_id = str(effect.get("id"))
    do = ass_ms(g.do)
    span = int(round((g.end - g.start) * 1000))
    t0 = max(0, span - do)
    if exit_id in ("fade-out", "none"):
        return f"\\fad(0,{do})"
    if exit_id == "blur-out":
        return f"\\t({t0},{span},\\blur10\\alpha&HFF&)"
    if exit_id == "pop-out":
        return f"\\t({t0},{span},\\fscx0\\fscy0\\alpha&HFF&)"
    if exit_id == "rotate-out":
        return f"\\t({t0},{span},\\frz84\\alpha&HFF&)"
    if exit_id.startswith("wipe-out-"):
        return _wipe_tags(g, effect, enter=False)
    if exit_id in ("slide-out-left", "slide-out-right", "slide-out-top", "slide-out-bottom", "sink-fade"):
        dx, dy = {
            "slide-out-left": (-g.width * 0.3, 0.0),
            "slide-out-right": (g.width * 0.3, 0.0),
            "slide-out-top": (0.0, -g.height * 0.18),
            "slide-out-bottom": (0.0, g.height * 0.18),
            "sink-fade": (0.0, g.height * 0.06),
        }[exit_id]
        return f"\\move({g.cx:.0f},{g.cy:.0f},{g.cx + dx:.0f},{g.cy + dy:.0f},{t0},{span})\\fad(0,{do})"
    if exit_id == "split-out-chars":
        return "\\fad(0," + str(do) + ")"
    return f"\\fad(0,{do})"


def _karaoke_sweep_body(g: TextGeometry) -> tuple[str, str]:
    """Karaoke sweep: \\kf recolours through the line once while it is shown.

    Returns (tags, body) — the highlight colour is an override tag.
    """
    total = max(1, sum(len(line) for line in g.lines))
    step_cs = max(1, int(round((g.end - g.start) * 100 / total)))
    pieces: list[str] = []
    for li, line in enumerate(g.lines):
        for ch in line:
            pieces.append(f"{{\\kf{step_cs}}}{escape_ass(ch)}" if ch != " " else " ")
        if li < len(g.lines) - 1:
            pieces.append(r"\N")
    return "\\2c&H40C8FF&", "".join(pieces)


def _wave_shimmer_body(g: TextGeometry, while_id: str) -> str:
    """Per-character looping transforms inside a single centred event."""
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    if while_id == "wave":
        def pattern(cycle: int, t0: int, period: int, phase: int) -> str:
            half = max(1, period // 4)
            up0, up1 = t0 + phase, t0 + phase + half * 2
            return f"\\t({up0},{up1},\\fscy128)\\t({up1},{up1 + half * 2},\\fscy100)"
        loops = _stagger_loop_tags(g, count, pattern)
        base = ""
    else:  # shimmer
        def pattern(cycle: int, t0: int, period: int, phase: int) -> str:
            third = max(1, period // 3)
            a, b = t0 + phase, t0 + phase + third
            return f"\\t({a},{b},\\1c&HFFFFFF&)\\t({b},{b + third},\\1c&HC8C8C8&)"
        loops = _stagger_loop_tags(g, count, pattern)
        base = "\\1c&HC8C8C8&"
    order = {index: k for k, index in enumerate(visible)}

    def tag_for(index: int, unit: str) -> str:
        if index not in order:
            return ""
        return base + loops[order[index]]
    return _inline_units(units, tag_for)


def _split_out_body(g: TextGeometry) -> str:
    """Split out · chars: characters shrink away in a stagger, left to right."""
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    stagger_ms = int(round(g.do * 1000 / count))
    span = int(round((g.end - g.start) * 1000))
    t0_base = max(0, span - ass_ms(g.do))

    def shrink(index: int, unit: str) -> str:
        order = visible.index(index)
        t0 = t0_base + stagger_ms * order
        return f"\\t({t0},{span},\\alpha&HFF&\\fscy84\\fscx90)"
    return _inline_units(units, shrink)


# --------------------------------------------------------------------------
# The .ass document for one clip
# --------------------------------------------------------------------------


def build_ass_document(g: TextGeometry, plan: FxPlan) -> str:
    enter_id, while_id, exit_id = plan.ids()
    while_structural = while_id in ("shake", "glitch-flicker", "count-up")

    # Structural "while" effects own the timeline; other slots demote to fades.
    if while_structural:
        events = _structural_while_events(g, while_id)
        return _header(g) + "\n" + "\n".join(events) + "\n"

    exit_sliced = exit_id in ("typewriter-delete", "scramble-out")
    end_at = g.end
    tail_events: list[str] = []
    if exit_sliced:
        end_at, tail_events = _sliced_exit_appends(g, exit_id)

    while_tags = "" if while_id in ("none", "karaoke-sweep", "wave", "shimmer") else _while_tags(g, while_id)
    if while_id == "karaoke-sweep" and enter_id == "typewriter":
        while_id = "none"  # two karaoke streams cannot stack; the typewriter wins
    karaoke_tags = ""

    # Special composed layout: news-style bar (enter and/or exit live here).
    if enter_id == "lower-third" or exit_id == "lower-third-retract":
        enter_extra = ""
        if enter_id != "lower-third":
            enter_extra, _ = _enter_tags(g, plan.enter)
        events = _lower_third_events(g, enter_id, exit_id, while_tags, enter_extra + _exit_tags(g, plan.exit_))
        return _header(g) + "\n" + "\n".join(events) + "\n"

    # Sliced enter effects generate their own event families.
    if enter_id in ("typewriter-caret", "decrypted-scramble"):
        events = _sliced_enter_events(g, plan, while_tags, _exit_tags(g, plan.exit_), end_at=end_at if exit_sliced else None)
        return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"

    # Per-character motion entrances (hidden-line trick keeps them centred).
    if enter_id == "split-rise-chars":
        # A \move is already busy on these events — strip move-based exits.
        events = _split_rise_events(g, while_tags, _degrade_move_exit(_exit_tags(g, plan.exit_)), end_at)
        return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"
    if enter_id == "split-from-centre":
        events = _split_centre_events(g, while_tags, _degrade_move_exit(_exit_tags(g, plan.exit_)), end_at)
        return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"

    # Default: one centred event carrying enter + while + exit tags.
    enter_tags, enter_body = _enter_tags(g, plan.enter)
    if while_id == "karaoke-sweep":
        karaoke_tags, body = _karaoke_sweep_body(g)
    elif while_id in ("wave", "shimmer"):
        body = _wave_shimmer_body(g, while_id)
    else:
        body = enter_body if enter_body is not None else _multiline(g)
    if exit_id == "split-out-chars":
        body = _split_out_body(g)
    exit_tags = _exit_tags(g, plan.exit_)
    if "\\move(" in enter_tags:
        # One \move per event: the enter owns it, a move-based exit keeps its fade.
        pos = "\\an5"
        exit_tags = _degrade_move_exit(exit_tags)
    else:
        pos = _pos_tags(g)
    events = [_ev(0, g.start, end_at, pos + enter_tags + while_tags + karaoke_tags + exit_tags, body)]
    return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"


def _degrade_move_exit(exit_tags: str) -> str:
    """Keep only the fade of a move-based exit (the event's \\move is taken)."""
    if "\\move(" in exit_tags:
        match = re.search(r"\\fad\(\d+,\d+\)", exit_tags)
        return match.group(0) if match else ""
    return exit_tags


# --------------------------------------------------------------------------
# Dispatcher
# --------------------------------------------------------------------------


def overlay_plan(item: dict[str, Any]) -> FxPlan | None:
    """Resolve the three slots of an item, or None when there is no text to draw."""
    probe = _geometry(item, {}, 1920, 1080)
    if probe is None:
        return None
    return FxPlan(
        enter=effect_for(item.get("textFxEnter"), "enter") or effect_for("Fade", "enter"),
        while_=effect_for(item.get("textFxWhile"), "while") or effect_for("None (static)", "while"),
        exit_=effect_for(item.get("textFxExit"), "exit") or effect_for("Fade out", "exit"),
    )


def plan_engine(plan: FxPlan) -> str:
    engines = {str(plan.enter.get("engine")), str(plan.while_.get("engine")), str(plan.exit_.get("engine"))}
    return "dt" if engines <= {"dt", "none"} else "ass"


def build_text_overlay(
    item: dict[str, Any],
    defaults: dict[str, Any],
    width: int,
    height: int,
    fonts_dir: Path | str,
    ass_path: Path | None,
    font_resolver: Callable[[str, bool, bool, Path], str] | None = None,
    force_ass: bool = False,
) -> str | None:
    """The renderer's text filter for one item, or None when there is no text.

    ``ass_path`` is where the per-clip .ass file is written (the renderer's
    per-job work dir keeps concurrent renders apart). ``font_resolver`` is the
    renderer's ``font_file`` — only the drawtext path needs it. ``force_ass``
    routes even drawtext-expressible plans through libass — for FFmpeg builds
    that have libass but no drawtext (stock distro/NAS binaries).
    """
    plan = overlay_plan(item)
    if plan is None:
        return None
    g = _geometry(item, defaults, width, height)
    if g is None:
        return None
    if plan_engine(plan) == "dt" and not (force_ass and ass_path is not None):
        resolver = font_resolver or (lambda family, bold, italic, fonts: str(fonts))
        font = resolver(g.family, g.bold, g.italic, Path(fonts_dir))
        return _drawtext_filter(
            g, font,
            str(plan.enter.get("label")), str(plan.exit_.get("label")), str(plan.while_.get("label")),
        )
    document = build_ass_document(g, plan)
    if ass_path is None:
        log.warning("Chosen text effects need libass but no ass_path was given; drawing no text")
        return None
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(document, encoding="utf-8")
    escaped_path = str(ass_path).replace("\\", "/").replace("'", r"\'")
    escaped_fonts = str(fonts_dir).replace("\\", "/").replace("'", r"\'")
    return f"ass=filename='{escaped_path}':fontsdir='{escaped_fonts}'"
