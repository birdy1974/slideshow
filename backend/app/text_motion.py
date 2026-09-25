"""Stackable text effects ("text motion"), compiled to libass.

A caption carries an ordered stack of layers (``MediaItem.textFx``); every
layer is one effect from ``registry/text-motion.json`` plus per-layer
overrides (unit, timing, order, intensity, parameters, word range, sync)::

    [{"effect": "pop-in", "unit": "word"}, {"effect": "wave"}, {"effect": "fade-out"}]

Effects are data: keyframe tracks per *channel* over the unit's local time
``u`` in [0, 1]. The engine never emits "one tag per effect" (libass honours
only the first ``\\fad``/``\\move`` of an event and later tags overwrite earlier
ones - that is why the v1 engine could not combine effects). Instead every
channel is *composed* across the stack at every video frame::

    opacity, scale, sx, sy, fill, squash   multiply
    x, y, vx, vy, rz, rx, ry, skew, blur,   add
    spacing, line, gather
    glow, depth, rgb, orbit                max
    colour                                 folded bottom -> top ("base" = colour below)
    clip                                   intersected, only while the layer runs
    content (text rewriting)               exclusive per phase, conflicts are reported

and the composed per-frame values are written as piecewise-linear ``\\t``
chains (within a tolerance). Events are only split where libass needs it:
moving units (``\\move`` is linear), changing glyphs and changing clip shapes.
Any easing (overshoot, elastic, bounce) survives because the curve itself is
sampled.

Units nest (letter < word < [word range] < line < text): a coarser layer moves
and turns its finer children around its own centre, so "Pop in · words" +
"Wave · letters" really compose.

Letters and words are drawn *in line*: each event holds the whole line with
every other unit transparent, so libass shapes the line exactly as it would
without effects - kerning, ligatures and the connections of script fonts stay
intact, and the letter sits where libass itself puts it. HarfBuzz (the shaper
libass uses) provides the same layout here for pivots, clips and the browser
preview; ``Kerning: yes`` makes libass kern like HarfBuzz and the browser.

Text frames with a colour change (background A -> B through the transition
catalogue) expose that change to the stack: ``bg`` effects follow its exact
shape and timing (wipes, slides, circles ... reproduced from FFmpeg's xfade
maths, a crossfade otherwise) and any layer can be timed to it (``sync``).

``src/textMotion.ts`` is the JavaScript twin of this module (the live
preview); ``tests/test_text_motion_twin.py`` runs both on the same input and
compares every channel.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Iterable

from .filter_values import quote_filter_value
from .font_registry import available_style, font_metrics, font_path

log = logging.getLogger(__name__)

# ============================================================================
# Registry
# ============================================================================


def _registry_candidates() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SLIDESHOW_TEXT_MOTION_REGISTRY")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "registry" / "text-motion.json")
    cands.append(Path("/app/registry/text-motion.json"))
    cands.append(Path.cwd() / "registry" / "text-motion.json")
    return cands


@lru_cache(maxsize=1)
def motion_registry() -> dict[str, Any]:
    for path in _registry_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("effects"), list):
            return data
    log.warning("registry/text-motion.json not found; stacked text effects are unavailable")
    return {"version": 2, "effects": [], "presets": [], "categories": []}


@lru_cache(maxsize=1)
def _effects_by_id() -> dict[str, dict[str, Any]]:
    return {str(e["id"]): e for e in motion_registry()["effects"] if isinstance(e, dict) and e.get("id")}


def effect(effect_id: str) -> dict[str, Any] | None:
    return _effects_by_id().get(str(effect_id or ""))


def effects() -> dict[str, dict[str, Any]]:
    return _effects_by_id()


@lru_cache(maxsize=1)
def legacy_index() -> dict[tuple[str, str], str]:
    """(v1 slot, lower-cased v1 label) -> v2 effect id."""
    out: dict[tuple[str, str], str] = {}
    for e in motion_registry()["effects"]:
        legacy = e.get("legacy")
        if isinstance(legacy, dict) and legacy.get("label"):
            out[(str(legacy.get("slot")), str(legacy["label"]).strip().lower())] = str(e["id"])
    return out


# ============================================================================
# Maths shared with src/textMotion.ts (keep both in step)
# ============================================================================

LEVEL_ORDER = ("char", "word", "line", "text")      # finest -> coarsest (groups sit after "word")
MUL = ("opacity", "scale", "sx", "sy", "fill", "squash")
ADD = ("x", "y", "vx", "vy", "px", "py", "rz", "rx", "ry", "skew", "blur", "spacing", "line", "gather")
MAXC = ("glow", "depth", "rgb", "orbit")
CHANNELS = MUL + ADD + MAXC
NEUTRAL: dict[str, float] = {**{c: 1.0 for c in MUL}, **{c: 0.0 for c in ADD + MAXC}}
PHASES = ("in", "hold", "out")


def _out_bounce(x: float) -> float:
    n1, d1 = 7.5625, 2.75
    if x < 1 / d1:
        return n1 * x * x
    if x < 2 / d1:
        x -= 1.5 / d1
        return n1 * x * x + 0.75
    if x < 2.5 / d1:
        x -= 2.25 / d1
        return n1 * x * x + 0.9375
    x -= 2.625 / d1
    return n1 * x * x + 0.984375


EASE: dict[str, Callable[[float], float]] = {
    "linear": lambda x: x,
    "inQuad": lambda x: x * x,
    "outQuad": lambda x: 1 - (1 - x) ** 2,
    "inOutQuad": lambda x: 2 * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 2 / 2,
    "inCubic": lambda x: x ** 3,
    "outCubic": lambda x: 1 - (1 - x) ** 3,
    "inOutCubic": lambda x: 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2,
    "inQuart": lambda x: x ** 4,
    "outQuart": lambda x: 1 - (1 - x) ** 4,
    "inOutSine": lambda x: -(math.cos(math.pi * x) - 1) / 2,
    "outSine": lambda x: math.sin(x * math.pi / 2),
    "inBack": lambda x: 2.70158 * x ** 3 - 1.70158 * x ** 2,
    "outBack": lambda x: 1 + 2.70158 * (x - 1) ** 3 + 1.70158 * (x - 1) ** 2,
    "outElastic": lambda x: 0.0 if x <= 0 else 1.0 if x >= 1 else
        2 ** (-10 * x) * math.sin((x * 10 - 0.75) * (2 * math.pi / 3)) + 1,
    "outBounce": _out_bounce,
}

# The motion path editor's easing names (textMoveEasing).
PATH_EASE: dict[str, Callable[[float], float]] = {
    "linear": lambda p: p,
    "ease-in": lambda p: p * p,
    "ease-out": lambda p: 1 - (1 - p) * (1 - p),
    "ease-in-out": lambda p: 2 * p * p if p < 0.5 else 1 - 2 * (1 - p) * (1 - p),
    "smooth": lambda p: 4 * p * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2,
}


def hash01(*values: int) -> float:
    """Deterministic pseudo-random number in [0, 1), bit-identical to the TS twin."""
    h = 2166136261
    for v in values:
        h ^= int(v) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0x5BD1E995) & 0xFFFFFFFF
    h ^= h >> 15
    return h / 4294967296.0


HEX = re.compile(r"#[0-9a-fA-F]{6}")


def rgb(hex_colour: str) -> tuple[float, float, float]:
    h = str(hex_colour).lstrip("#")
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))


def hex_of(c: Iterable[float]) -> str:
    return "#" + "".join(f"{int(round(max(0.0, min(255.0, v)))):02x}" for v in c)


def _mix(a: Any, b: Any, f: float) -> Any:
    if isinstance(a, (tuple, list)):
        return tuple(x + (y - x) * f for x, y in zip(a, b))
    return a + (b - a) * f


def _clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else hi if v > hi else v


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _luminance(c: tuple[float, float, float]) -> float:
    def ch(v: float) -> float:
        v /= 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast_ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def contrast_colour(background: tuple[float, float, float], preferred: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    """A readable text colour on ``background``: the preferred colour when it
    already reaches 4.5:1 (WCAG AA), otherwise near-white or near-black."""
    if preferred is not None and contrast_ratio(preferred, background) >= 4.5:
        return preferred
    light, dark = (255.0, 255.0, 255.0), (17.0, 17.0, 17.0)
    return light if contrast_ratio(light, background) >= contrast_ratio(dark, background) else dark


# ============================================================================
# Layers
# ============================================================================

UNITS = ("char", "word", "line", "text")
ORDERS = ("forward", "reverse", "center", "edges", "random")
LOOPS = ("loop", "pingpong", "once")


@dataclass
class Layer:
    """One row of a caption's effect stack (what the editor's lanes edit)."""
    effect: str
    unit: str | None = None
    duration: float | None = None
    delay: float = 0.0
    stagger: float | None = None
    order: str | None = None
    loop: str | None = None
    intensity: float = 1.0
    params: dict[str, Any] = field(default_factory=dict)
    muted: bool = False
    sync: str | None = None               # "bg": timed to the frame's colour change
    range: tuple[int, int] | None = None  # word indices (inclusive) the layer applies to
    id: str = ""

    @property
    def fx(self) -> dict[str, Any]:
        return effect(self.effect) or {}

    @property
    def phase(self) -> str:
        return str(self.fx.get("phase", "hold"))

    def param(self, name: str, default: Any = None) -> Any:
        if name in self.params:
            return self.params[name]
        for p in self.fx.get("params") or []:
            if p.get("name") == name:
                return p.get("default", default)
        return default


def _num(value: Any, default: float | None) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return default
    return n if math.isfinite(n) else default


def parse_stack(raw: Any) -> list[Layer]:
    """Validate a stored ``textFx`` list. Unknown effects are dropped (with a
    log line), numbers are clamped to what the editor offers."""
    if not isinstance(raw, list):
        return []
    out: list[Layer] = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("effect") or "")
        fx = effect(eid)
        if not fx:
            log.warning("unknown text effect %r ignored", eid)
            continue
        unit = entry.get("unit")
        unit = unit if unit in UNITS else None
        duration = _num(entry.get("duration"), None)
        duration = None if duration is None or duration <= 0 else _clamp(duration, 0.02, 120.0)
        stagger = _num(entry.get("stagger"), None)
        stagger = None if stagger is None else _clamp(stagger, 0.0, 4.0)
        order = entry.get("order") if entry.get("order") in ORDERS else None
        loop = entry.get("loop") if entry.get("loop") in LOOPS else None
        rng = entry.get("range")
        word_range = None
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            a, b = _num(rng[0], None), _num(rng[1], None)
            if a is not None and b is not None:
                a, b = int(max(0, a)), int(max(0, b))
                word_range = (min(a, b), max(a, b))
        params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
        out.append(Layer(
            effect=eid, unit=unit, duration=duration,
            delay=_clamp(_num(entry.get("delay"), 0.0) or 0.0, 0.0, 120.0),
            stagger=stagger, order=order, loop=loop,
            intensity=_clamp(_num(entry.get("intensity"), 1.0) or 0.0, 0.0, 3.0),
            params=dict(params), muted=bool(entry.get("muted")),
            sync="bg" if entry.get("sync") == "bg" else None,
            range=word_range, id=str(entry.get("id") or f"l{i}"),
        ))
    return out


# ============================================================================
# Fonts and shaping
# ============================================================================


@dataclass
class FontSpec:
    family: str
    bold: bool
    italic: bool
    path: Path | None
    upm: float
    win_ascent: float
    win_descent: float

    @property
    def win_height(self) -> float:
        return self.win_ascent + self.win_descent

    def ass_size(self, em: float) -> float:
        """ASS font size that makes libass draw an em of ``em`` px (libass maps
        the size to usWinAscent + usWinDescent, CSS and drawtext to the em)."""
        return em * self.win_height / self.upm

    def line_height(self, em: float) -> float:
        """libass advances lines by the ASS font size, i.e. the win height."""
        return em * self.win_height / self.upm


def resolve_font(family: str, bold: bool, italic: bool, fonts_dir: Path | str | None) -> FontSpec:
    bold, italic = available_style(family, bold, italic)
    metrics = font_metrics(family, bold, italic) or {}
    path = font_path(family, bold, italic, Path(fonts_dir)) if fonts_dir else None
    upm = float(metrics.get("upm") or 1000)
    wa = float(metrics.get("winAscent") or upm * 0.95)
    wd = float(metrics.get("winDescent") or upm * 0.27)
    if path is not None and not metrics:
        try:  # an unregistered file: read the numbers ourselves
            from fontTools.ttLib import TTFont
            tt = TTFont(str(path), lazy=True)
            upm = float(tt["head"].unitsPerEm)
            wa, wd = float(tt["OS/2"].usWinAscent), float(tt["OS/2"].usWinDescent)
        except Exception:  # noqa: BLE001 - keep the estimate
            pass
    return FontSpec(family, bold, italic, path, upm, wa, wd)


class Shaper:
    """Per-character advances of a line, as libass would lay it out.

    HarfBuzz (``uharfbuzz``) with kerning, ligatures and contextual
    alternates on; cluster advances are attributed to the first character of
    the cluster. Without uharfbuzz (or without the font file) a width estimate
    keeps the engine working; only pivots and clips are then approximate,
    because the in-line events leave the actual glyph placement to libass.
    """

    def __init__(self, font: FontSpec):
        self.font = font
        self.hb = None
        self.hb_font = None
        if font.path is not None:
            try:
                import uharfbuzz as hb
                self.hb = hb
                self.hb_font = hb.Font(hb.Face(hb.Blob.from_file_path(str(font.path))))
            except Exception:  # noqa: BLE001 - optional dependency
                self.hb = None

    def advances(self, text: str, em: float) -> list[float]:
        if not text:
            return []
        if self.hb is not None and self.hb_font is not None:
            buf = self.hb.Buffer()
            buf.add_str(text)
            buf.guess_segment_properties()
            self.hb.shape(self.hb_font, buf, {"kern": True, "liga": True, "calt": True})
            scale = em / self.font.upm
            adv = [0.0] * len(text)
            infos, positions = buf.glyph_infos, buf.glyph_positions
            for info, pos in zip(infos, positions):
                if 0 <= info.cluster < len(adv):
                    adv[info.cluster] += pos.x_advance * scale
            return adv
        widths = {" ": 0.28, "i": 0.28, "l": 0.28, "j": 0.3, "t": 0.36, "f": 0.34, "r": 0.4, "m": 0.86,
                  "w": 0.78, "M": 0.86, "W": 0.96, ".": 0.26, ",": 0.26, "'": 0.2, "!": 0.3}
        return [em * widths.get(ch, 0.62 if ch.isupper() else 0.55) for ch in text]


# ============================================================================
# Layout: rest boxes of letters, words, lines and the whole text
# ============================================================================


@dataclass
class Unit:
    level: str
    index: int
    text: str
    cx: float
    cy: float
    w: float
    h: float
    anc: dict[str, int] = field(default_factory=dict)   # level -> index of the ancestor (self included)
    line: int = 0
    c0: int = 0          # first character index inside its line
    c1: int = 0          # one past the last character
    col: int = 0         # glyph column inside the line (letter spacing)
    ncol: int = 1
    word: int = -1       # word index (reading order), -1 for line / text

    def box(self) -> tuple[float, float, float, float]:
        return (self.cx - self.w / 2, self.cy - self.h / 2, self.cx + self.w / 2, self.cy + self.h / 2)


@dataclass
class Layout:
    units: dict[str, list[Unit]]
    lines: list[str]
    line_h: float
    em: float
    align: str


def clean_lines(text: str) -> list[str]:
    """The lines libass draws: split at newlines, surrounding blanks trimmed
    (libass ignores them for alignment, so the preview must too)."""
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    while lines and not lines[0]:
        lines.pop(0)
    return lines or [""]


def build_layout(lines: list[str], advances: Callable[[str], list[float]], em: float, line_h: float,
                 cx: float, cy: float, align: str = "center") -> Layout:
    """Rest geometry. ``advances(line)`` returns one advance per character.

    The block is centred on (cx, cy); lines are centred on cx (``center``) or
    share the block's left edge (``left``, text frames without "centre text").
    Shared verbatim with the TS twin, which feeds browser measurements in.
    """
    units: dict[str, list[Unit]] = {lvl: [] for lvl in LEVEL_ORDER}
    adv_per_line = [advances(line) for line in lines]
    widths = [sum(a) for a in adv_per_line]
    block_w = max(widths or [0.0])
    n = len(lines)
    top = cy - line_h * n / 2
    whole = Unit("text", 0, "\n".join(lines), cx, cy, block_w, line_h * n)
    whole.anc = {"text": 0}
    whole.c1 = 0
    units["text"].append(whole)
    for li, line in enumerate(lines):
        adv = adv_per_line[li]
        width = widths[li]
        left = cx - width / 2 if align != "left" else cx - block_w / 2
        lcy = top + (li + 0.5) * line_h
        lu = Unit("line", li, line, left + width / 2, lcy, width, line_h, line=li, c0=0, c1=len(line))
        lu.anc = {"line": li, "text": 0}
        units["line"].append(lu)
        pen = 0.0
        word_chars: list[int] = []
        pens: list[float] = []
        for i, ch in enumerate(line):
            pens.append(pen)
            pen += adv[i] if i < len(adv) else 0.0

        def close_word() -> None:
            if not word_chars:
                return
            i0, i1 = word_chars[0], word_chars[-1]
            x0 = left + pens[i0]
            x1 = left + pens[i1] + adv[i1]
            wi = len(units["word"])
            wu = Unit("word", wi, line[i0:i1 + 1], (x0 + x1) / 2, lcy, x1 - x0, line_h, line=li, c0=i0, c1=i1 + 1, word=wi)
            wu.anc = {"word": wi, "line": li, "text": 0}
            units["word"].append(wu)
            for ci in word_chars:
                cu = units["char"][char_ids[ci]]
                cu.anc.update(word=wi, line=li, text=0)
                cu.word = wi
            word_chars.clear()

        char_ids: dict[int, int] = {}
        for i, ch in enumerate(line):
            if ch.isspace():
                close_word()
                continue
            a = adv[i] if i < len(adv) else 0.0
            idx = len(units["char"])
            cu = Unit("char", idx, ch, left + pens[i] + a / 2, lcy, a, line_h, line=li, c0=i, c1=i + 1, col=i, ncol=len(line))
            cu.anc = {"char": idx}
            char_ids[i] = idx
            units["char"].append(cu)
            word_chars.append(i)
        close_word()
    return Layout(units, lines, line_h, em, align)


# ============================================================================
# Background colour change (text frames): the exact shape of the xfade
# ============================================================================


@dataclass
class BgChange:
    colour_a: str
    colour_b: str
    transition: str      # xfade id (wipeleft, circleopen, gl_*, ...)
    start: float         # seconds, same timeline as the caption
    time: float

    @property
    def end(self) -> float:
        return self.start + self.time


# Friendly transition labels -> xfade ids (renderer.XFADE, duplicated to keep
# this module importable without the renderer).
XFADE_IDS = {
    "fade": "fade", "fade black": "fadeblack", "fade white": "fadewhite", "fade grays": "fadegrays", "fade fast": "fadefast",
    "fade slow": "fadeslow", "dissolve": "dissolve", "distance": "distance", "pixelize": "pixelize", "h blur": "hblur",
    "wipe left": "wipeleft", "wipe right": "wiperight", "wipe up": "wipeup", "wipe down": "wipedown", "wipe top-left": "wipetl",
    "wipe top-right": "wipetr", "wipe bottom-left": "wipebl", "wipe bottom-right": "wipebr", "slide left": "slideleft",
    "slide right": "slideright", "slide up": "slideup", "slide down": "slidedown", "smooth left": "smoothleft",
    "smooth right": "smoothright", "smooth up": "smoothup", "smooth down": "smoothdown", "circle crop": "circlecrop",
    "rectangle crop": "rectcrop", "circle open": "circleopen", "circle close": "circleclose", "vertical open": "vertopen",
    "vertical close": "vertclose", "horizontal open": "horzopen", "horizontal close": "horzclose", "radial": "radial",
    "diagonal top-left": "diagtl", "diagonal top-right": "diagtr", "diagonal bottom-left": "diagbl",
    "diagonal bottom-right": "diagbr", "horizontal left slice": "hlslice", "horizontal right slice": "hrslice",
    "vertical up slice": "vuslice", "vertical down slice": "vdslice", "squeeze horizontal": "squeezeh",
    "squeeze vertical": "squeezev", "zoom in": "zoomin", "horizontal left wind": "hlwind", "horizontal right wind": "hrwind",
    "vertical up wind": "vuwind", "vertical down wind": "vdwind", "cover left": "coverleft", "cover right": "coverright",
    "cover up": "coverup", "cover down": "coverdown", "reveal left": "revealleft", "reveal right": "revealright",
    "reveal up": "revealup", "reveal down": "revealdown",
}


def xfade_id(label: str) -> str:
    raw = str(label or "").strip()
    base = raw.split("(", 1)[0].strip()
    return XFADE_IDS.get(base.lower(), base.lower() if base.lower() in XFADE_IDS.values() else ("gl" if base.lower().startswith(("gl", "gl_")) else "fade"))


def bg_shape_kind(transition: str) -> str:
    """How the text follows this transition: rect | circle | poly | mix."""
    t = xfade_id(transition)
    if t in ("circleopen", "circleclose"):
        return "circle"
    if t in ("radial", "diagtl", "diagtr", "diagbl", "diagbr"):
        return "poly"
    if t in ("fade", "fadeblack", "fadewhite", "fadegrays", "fadefast", "fadeslow", "dissolve", "distance", "pixelize",
             "hblur", "circlecrop", "rectcrop", "zoomin", "gl"):
        return "mix"
    return "rect"


def bg_region(transition: str, p: float, w: float, h: float) -> dict[str, Any]:
    """Where colour B is on screen at transition progress ``p`` (0 -> 1).

    Mirrors ffmpeg-patch/vf_xfade.c (``progress`` there runs 1 -> 0, the
    ``mix(a, b, m)`` there is ``a*m + b*(1-m)``). Returns one of::

        {"kind": "rect", "rect": [x0, y0, x1, y1], "inside": True}   B inside (False: B outside)
        {"kind": "circle", "cx", "cy", "r", "inside": bool}
        {"kind": "poly", "points": [[x, y], ...], "inside": True}
        {"kind": "mix", "f": weight of B}

    Soft edges (smooth*, circle*, open/close) are split at their midpoint.
    """
    t = xfade_id(transition)
    p = _clamp01(p)
    pf = 1.0 - p                     # xfade's own progress
    full = [0.0, 0.0, w, h]
    if p <= 0:
        return {"kind": "none"}
    if p >= 1:
        return {"kind": "all"}
    if t in ("wipeleft", "slideleft", "coverleft", "revealleft"):
        return {"kind": "rect", "rect": [w * pf, 0.0, w, h], "inside": True}
    if t in ("wiperight", "slideright", "coverright", "revealright"):
        return {"kind": "rect", "rect": [0.0, 0.0, w * p, h], "inside": True}
    if t in ("wipeup", "slideup", "coverup", "revealup"):
        return {"kind": "rect", "rect": [0.0, h * pf, w, h], "inside": True}
    if t in ("wipedown", "slidedown", "coverdown", "revealdown"):
        return {"kind": "rect", "rect": [0.0, 0.0, w, h * p], "inside": True}
    if t == "wipetl":   # A keeps the shrinking top-left rectangle
        return {"kind": "rect", "rect": [0.0, 0.0, w * pf, h * pf], "inside": False}
    if t == "wipetr":
        return {"kind": "rect", "rect": [w * p, 0.0, w, h * pf], "inside": False}
    if t == "wipebl":
        return {"kind": "rect", "rect": [0.0, h * p, w * pf, h], "inside": False}
    if t == "wipebr":
        return {"kind": "rect", "rect": [w * p, h * p, w, h], "inside": False}
    if t == "smoothleft" or t == "hlwind":
        return {"kind": "rect", "rect": [w * _clamp01(1.5 - 2 * p), 0.0, w, h], "inside": True}
    if t == "smoothright" or t == "hrwind":
        return {"kind": "rect", "rect": [0.0, 0.0, w * _clamp01(2 * p - 0.5), h], "inside": True}
    if t == "smoothup" or t == "vuwind":
        return {"kind": "rect", "rect": [0.0, h * _clamp01(1.5 - 2 * p), w, h], "inside": True}
    if t == "smoothdown" or t == "vdwind":
        return {"kind": "rect", "rect": [0.0, 0.0, w, h * _clamp01(2 * p - 0.5)], "inside": True}
    if t == "hlslice":
        return {"kind": "rect", "rect": [w * _clamp01(1.25 - 1.5 * p), 0.0, w, h], "inside": True}
    if t == "hrslice":
        return {"kind": "rect", "rect": [0.0, 0.0, w * _clamp01(1.5 * p - 0.25), h], "inside": True}
    if t == "vuslice":
        return {"kind": "rect", "rect": [0.0, h * _clamp01(1.25 - 1.5 * p), w, h], "inside": True}
    if t == "vdslice":
        return {"kind": "rect", "rect": [0.0, 0.0, w, h * _clamp01(1.5 * p - 0.25)], "inside": True}
    if t == "vertopen":
        hw = w / 2 * _clamp01(2 * p - 0.5)
        return {"kind": "rect", "rect": [w / 2 - hw, 0.0, w / 2 + hw, h], "inside": True}
    if t == "vertclose":
        hw = w / 2 * _clamp01(1.5 - 2 * p)
        return {"kind": "rect", "rect": [w / 2 - hw, 0.0, w / 2 + hw, h], "inside": False}
    if t == "horzopen":
        hh = h / 2 * _clamp01(2 * p - 0.5)
        return {"kind": "rect", "rect": [0.0, h / 2 - hh, w, h / 2 + hh], "inside": True}
    if t == "horzclose":
        hh = h / 2 * _clamp01(1.5 - 2 * p)
        return {"kind": "rect", "rect": [0.0, h / 2 - hh, w, h / 2 + hh], "inside": False}
    if t == "squeezeh":   # A squeezed into a shrinking horizontal band
        hh = h / 2 * pf
        return {"kind": "rect", "rect": [0.0, h / 2 - hh, w, h / 2 + hh], "inside": False}
    if t == "squeezev":
        hw = w / 2 * pf
        return {"kind": "rect", "rect": [w / 2 - hw, 0.0, w / 2 + hw, h], "inside": False}
    if t == "circleopen":
        z = math.hypot(w / 2, h / 2)
        return {"kind": "circle", "cx": w / 2, "cy": h / 2, "r": max(0.0, z * (3 * p - 1)), "inside": True}
    if t == "circleclose":
        z = math.hypot(w / 2, h / 2)
        return {"kind": "circle", "cx": w / 2, "cy": h / 2, "r": max(0.0, z * (2 - 3 * p)), "inside": False}
    if t == "radial":
        theta = 0.5 + (0.5 - p) * 2.5 * math.pi    # B where atan2(dx, dy) > theta
        if theta >= math.pi:
            return {"kind": "none"}
        start = max(-math.pi, theta)
        big = math.hypot(w, h)
        pts = [[w / 2, h / 2]]
        steps = 48
        for i in range(steps + 1):
            a = start + (math.pi - start) * i / steps
            pts.append([w / 2 + big * math.sin(a), h / 2 + big * math.cos(a)])
        return {"kind": "poly", "points": pts, "inside": True}
    if t in ("diagtl", "diagtr", "diagbl", "diagbr"):
        c = 1.5 - 2 * p                  # B where u*v > c (u, v = distance fractions from the corner)
        if c >= 1:
            return {"kind": "none"}
        if c <= 0:
            return {"kind": "all"}
        steps = 40
        pts_uv = [[1.0, 1.0], [c, 1.0]]
        for i in range(1, steps):
            uu = c + (1 - c) * i / steps
            pts_uv.append([uu, c / uu])
        pts_uv.append([1.0, c])
        flip_x = t in ("diagtr", "diagbr")
        flip_y = t in ("diagbl", "diagbr")
        pts = [[w * (1 - u if flip_x else u), h * (1 - v if flip_y else v)] for u, v in pts_uv]
        return {"kind": "poly", "points": pts, "inside": True}
    if t == "zoomin":
        return {"kind": "mix", "f": 1 - _smoothstep(0.0, 0.5, pf)}
    if t in ("fadeblack", "fadewhite", "fadegrays"):
        return {"kind": "mix", "f": _smoothstep(0.35, 0.65, p)}
    if t in ("circlecrop", "rectcrop"):
        return {"kind": "mix", "f": 1.0 if p >= 0.5 else 0.0}
    return {"kind": "mix", "f": p}


def _smoothstep(e0: float, e1: float, x: float) -> float:
    t = _clamp01((x - e0) / (e1 - e0)) if e1 != e0 else (1.0 if x >= e1 else 0.0)
    return t * t * (3 - 2 * t)


def region_contains(region: dict[str, Any], x: float, y: float) -> bool:
    """Is (x, y) in colour B? (used for tests and the preview's colour picks)"""
    kind = region.get("kind")
    if kind == "all":
        return True
    if kind in ("none", "mix"):
        return False
    if kind == "rect":
        x0, y0, x1, y1 = region["rect"]
        inside = x0 <= x <= x1 and y0 <= y <= y1
        return inside if region.get("inside", True) else not inside
    if kind == "circle":
        inside = math.hypot(x - region["cx"], y - region["cy"]) <= region["r"]
        return inside if region.get("inside", True) else not inside
    if kind == "poly":
        pts = region["points"]
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi:
                inside = not inside
            j = i
        return inside
    return False


# ============================================================================
# Compilation (shared semantics with src/textMotion.ts)
# ============================================================================


@dataclass
class MotionPath:
    """The motion path editor's path, in percent of the frame, plus easing."""
    points: list[tuple[float, float]]
    easing: str = "linear"


@dataclass
class Caption:
    text: str
    x: float                      # block centre, px
    y: float
    em: float                     # font size (em) in px
    stack: list[Layer]
    start: float = 0.0            # text window, seconds
    end: float = 4.0
    family: str = "Montserrat"
    bold: bool = True
    italic: bool = False
    underline: bool = False
    colour: str = "#ffffff"
    outline: bool = False         # dark outline + shadow behind captions
    align: str = "center"         # center | left (lines inside the block)
    frame_w: int = 1920
    frame_h: int = 1080
    fps: float = 25.0
    steady: float = 0.0           # seconds the once-layers finish early
    bg: BgChange | None = None
    motion: MotionPath | None = None
    layer: int = 5                # ASS layer of the main text; copies go below


@dataclass
class _L:
    """A resolved layer (stack order preserved)."""
    src: int
    layer: Layer
    fx: dict[str, Any]
    phase: str
    unit: str                     # level name ("char" ... "text" or a group "g<i>")
    duration: float | None
    delay: float
    stagger: float
    order: str
    loop: str
    intensity: float
    synced: bool
    seed: int
    ranks: dict[int, float] = field(default_factory=dict)   # unit index -> rank (in-range units only)
    max_rank: float = 0.0

    def param(self, name: str, default: Any = None) -> Any:
        return self.layer.param(name, default)


@dataclass
class Ctx:
    cap: Caption
    layout: Layout
    stack: list[_L]
    gran: str
    levels: list[str]                  # levels at or above gran, finest first (groups included)
    units: dict[str, list[Unit]]
    anc: dict[str, dict[int, int]]     # group level -> {word index -> group unit index}
    content: dict[str, _L]
    warnings: list[str]
    conflicts: dict[int, str]
    base: tuple[float, float, float]
    org_level: str | None


def _ranks(n: int, order: str, seed: int) -> list[float]:
    idx = list(range(n))
    if order == "reverse":
        return [float(n - 1 - i) for i in idx]
    if order in ("center", "edges"):
        c = (n - 1) / 2
        dist = [abs(i - c) for i in idx]
        return dist if order == "center" else [max(dist) - d for d in dist]
    if order == "random":
        perm = sorted(idx, key=lambda i: (hash01(seed, i), i))
        pos = {v: k for k, v in enumerate(perm)}
        return [float(pos[i]) for i in idx]
    return [float(i) for i in idx]


def _level_rank(level: str) -> float:
    if level.startswith("g"):
        return 1.5
    return float(LEVEL_ORDER.index(level))


def compile_stack(cap: Caption, layout: Layout) -> Ctx:
    warnings: list[str] = []
    conflicts: dict[int, str] = {}
    resolved: list[_L] = []
    units = {k: list(v) for k, v in layout.units.items()}
    group_anc: dict[str, dict[int, int]] = {}
    n_words = len(units["word"])
    for i, layer in enumerate(cap.stack):
        if layer.muted:
            continue
        fx = layer.fx
        if not fx:
            continue
        phase = str(fx.get("phase", "hold"))
        unit = layer.unit or str(fx.get("unit", "text"))
        allowed = fx.get("units")
        if isinstance(allowed, list) and allowed and unit not in allowed:
            unit = str(fx.get("unit", "text"))
        needs_bg = bool(fx.get("needsBg")) or bool(fx.get("bg"))
        synced = (layer.sync == "bg" or fx.get("sync") == "bg" or needs_bg) and cap.bg is not None
        if needs_bg and cap.bg is None:
            warnings.append(f"'{fx.get('label')}' needs a text frame with a colour change (background A → B)")
            conflicts[i] = "needs a colour change"
            continue
        if layer.sync == "bg" and cap.bg is None and not needs_bg:
            conflicts[i] = "no colour change to sync to"
        loop = layer.loop or str(fx.get("loop", "loop"))
        if phase != "hold":
            loop = "once"
        rng = layer.range
        if rng is not None and n_words == 0:
            rng = None
        if rng is not None:
            rng = (min(rng[0], n_words - 1), min(rng[1], n_words - 1))
            if unit in ("line", "text"):
                gname = f"g{i}"
                ws = [units["word"][k] for k in range(rng[0], rng[1] + 1)]
                x0 = min(u.cx - u.w / 2 for u in ws)
                x1 = max(u.cx + u.w / 2 for u in ws)
                y0 = min(u.cy - u.h / 2 for u in ws)
                y1 = max(u.cy + u.h / 2 for u in ws)
                g = Unit(gname, 0, " ".join(u.text for u in ws), (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0,
                         line=ws[0].line)
                g.anc = {gname: 0, "line": ws[0].line, "text": 0}
                units[gname] = [g]
                group_anc[gname] = {k: 0 for k in range(rng[0], rng[1] + 1)}
                unit = gname
        duration = layer.duration if layer.duration is not None else _num(fx.get("duration"), None)
        stagger = layer.stagger if layer.stagger is not None else float(fx.get("stagger", 0.0) or 0.0)
        order = layer.order or str(fx.get("order", "forward"))
        L = _L(src=i, layer=layer, fx=fx, phase=phase, unit=unit, duration=duration, delay=layer.delay,
               stagger=stagger, order=order, loop=loop, intensity=layer.intensity, synced=synced,
               seed=len(resolved) * 104729 + 13)
        L.layer = dataclasses.replace(layer, range=rng)
        resolved.append(L)

    # content (text rewriting) is exclusive per phase: the top-most wins
    content: dict[str, _L] = {}
    for L in resolved:
        if "content" in L.fx:
            prev = content.get(L.phase)
            if prev is not None:
                warnings.append(f"'{L.fx.get('label')}' replaces '{prev.fx.get('label')}' "
                                f"(both rewrite the text in the {L.phase} phase)")
                conflicts[prev.src] = f"replaced by {L.fx.get('label')}"
            content[L.phase] = L
    whole = [L for L in content.values() if L.fx["content"].get("type") in ("count", "countdown")]
    if whole:
        for L in resolved:
            if L.unit != "text":
                warnings.append(f"'{whole[0].fx.get('label')}' rewrites the whole text, so '{L.fx.get('label')}' "
                                f"runs on the whole text instead of per {L.unit}")
                conflicts[L.src] = f"whole text ({whole[0].fx.get('label')})"
                L.unit = "text"
    need: list[str] = ["line"]
    for L in resolved:
        need.append(L.unit if not L.unit.startswith("g") else "word")
    for L in content.values():
        if L.fx["content"].get("type") in ("scramble", "flap"):
            need.append("char")
    if any("caret" in L.fx for L in resolved):
        need.append("char")
    if whole:
        # a counter replaces the whole text: one event for the whole block
        need = ["text"]
    gran = min(need, key=_level_rank)
    groups = sorted(k for k in units if k.startswith("g"))
    levels = [lvl for lvl in ("char", "word") if _level_rank(lvl) >= _level_rank(gran)]
    levels += [g for g in groups if _level_rank(g) >= _level_rank(gran)]
    levels += [lvl for lvl in ("line", "text") if _level_rank(lvl) >= _level_rank(gran)]
    # ranks per layer, among the units the layer applies to
    for L in resolved:
        lvl_units = units[L.unit]
        idx = [u.index for u in lvl_units if _in_range(L, u)]
        r = _ranks(len(idx), L.order, L.seed)
        L.ranks = {ui: r[k] for k, ui in enumerate(idx)}
        L.max_rank = max(r) if r else 0.0
    org_level = None
    for L in resolved:
        tracks = L.fx.get("tracks") or {}
        if ("rx" in tracks or "ry" in tracks) and _level_rank(L.unit) > _level_rank(gran):
            if org_level is None or _level_rank(L.unit) > _level_rank(org_level):
                org_level = L.unit
    return Ctx(cap=cap, layout=layout, stack=resolved, gran=gran, levels=levels, units=units, anc=group_anc,
               content=content, warnings=warnings, conflicts=conflicts, base=rgb(cap.colour), org_level=org_level)


def _in_range(L: _L, u: Unit) -> bool:
    rng = L.layer.range
    if rng is None or L.unit.startswith("g"):
        return True
    if u.level in ("char", "word"):
        return rng[0] <= u.word <= rng[1]
    return True


def _ancestor(ctx: Ctx, e: Unit, level: str) -> Unit | None:
    if level == e.level:
        return e
    if level.startswith("g"):
        w = e.word if e.level in ("char", "word") else -1
        gi = ctx.anc.get(level, {}).get(w)
        return None if gi is None else ctx.units[level][gi]
    idx = e.anc.get(level)
    return None if idx is None else ctx.units[level][idx]


def layer_window(ctx: Ctx, L: _L) -> tuple[float, float]:
    if L.synced and ctx.cap.bg is not None:
        return ctx.cap.bg.start, ctx.cap.bg.end
    return ctx.cap.start, ctx.cap.end


def local_u(ctx: Ctx, L: _L, rank: float, t: float) -> tuple[float, float]:
    """(u, seconds since the unit's local start) of layer L for one unit."""
    ws, we = layer_window(ctx, L)
    k = L.stagger
    R = L.max_rank
    if L.phase in ("in", "out"):
        total = L.duration if L.duration is not None else (we - ws if L.synced else 0.5)
        total = max(total, 1e-3)
        unit_d = total / (1 + k * R)
        if L.phase == "in":
            t0 = ws + L.delay + rank * k * unit_d
        else:
            t0 = we - L.delay - (R - rank) * k * unit_d - unit_d
        return _clamp01((t - t0) / unit_d), t - t0
    if L.loop == "once":
        span_end = we - (0.0 if L.synced else min(ctx.cap.steady, max(0.0, we - ws - 0.05)))
        span = L.duration if L.duration is not None else span_end - ws - L.delay
        span = max(span, 1e-3)
        unit_d = span / (1 + k * R)
        t0 = ws + L.delay + rank * k * unit_d
        return _clamp01((t - t0) / unit_d), t - t0
    period = max(L.duration if L.duration is not None else 2.0, 0.05)
    t0 = ws + L.delay + rank * k * period
    if t < t0 or (L.synced and t > we):
        return 0.0, t - t0
    w = (t - t0) / period
    if L.loop == "pingpong":
        w = w % 2.0
        return (w if w <= 1 else 2 - w), t - t0
    return w % 1.0, t - t0


def _resolve(ctx: Ctx, L: _L, value: Any, base: tuple[float, float, float], useed: int, key: int) -> Any:
    if isinstance(value, dict) and "rand" in value:
        a, b = value["rand"]
        return a + (b - a) * hash01(useed, L.seed, key, 99)
    if isinstance(value, str):
        if value.startswith("$"):
            value = L.param(value[1:], 0)
            if not isinstance(value, str):
                return _num(value, 0.0)
        return colour_token(ctx, value, base)
    return value


def colour_token(ctx: Ctx, value: str, base: tuple[float, float, float]) -> tuple[float, float, float]:
    v = str(value or "").strip()
    if v == "base" or not v:
        return base
    bg = ctx.cap.bg
    if v in ("bgA", "bgB", "contrastA", "contrastB"):
        if bg is None:
            return base
        a, b = rgb(bg.colour_a), rgb(bg.colour_b)
        return {"bgA": a, "bgB": b, "contrastA": contrast_colour(a, base), "contrastB": contrast_colour(b, base)}[v]
    if HEX.fullmatch(v):
        return rgb(v)
    return base


def _keys(ctx: Ctx, L: _L, keys: list, u: float, base: tuple[float, float, float], useed: int) -> Any:
    first = keys[0]
    if u <= first[0]:
        return _resolve(ctx, L, first[1], base, useed, 0)
    for k in range(1, len(keys)):
        u0, v0 = keys[k - 1][0], keys[k - 1][1]
        u1, v1 = keys[k][0], keys[k][1]
        if u <= u1:
            f = 0.0 if u1 <= u0 else (u - u0) / (u1 - u0)
            if len(keys[k]) > 2:
                f = EASE.get(keys[k][2], EASE["linear"])(f)
            a = _resolve(ctx, L, v0, base, useed, k - 1)
            b = _resolve(ctx, L, v1, base, useed, k)
            return _mix(a, b, f)
    return _resolve(ctx, L, keys[-1][1], base, useed, len(keys) - 1)


def _pnum(ctx: Ctx, L: _L, v: Any, default: float) -> float:
    if isinstance(v, str) and v.startswith("$"):
        v = L.param(v[1:], default)
    n = _num(v, default)
    return default if n is None else n


def _generator(ctx: Ctx, L: _L, spec: dict, u: float, tl: float, useed: int) -> float:
    if "sine" in spec:
        g = spec["sine"]
        v = _pnum(ctx, L, g.get("base", 0.0), 0.0) + _pnum(ctx, L, g.get("amp"), 0.0) * math.sin(2 * math.pi * (u + float(g.get("phase", 0.0))))
    elif "noise" in spec:
        g = spec["noise"]
        step = math.floor(tl * float(g["rate"]))
        v = float(spec.get("base", 0.0)) + _pnum(ctx, L, g.get("amp"), 0.0) * (2 * hash01(step, int(g.get("seed", 1)), useed, L.seed) - 1)
    elif "flicker" in spec:
        g = spec["flicker"]
        ramp = g.get("ramp")
        if ramp == "up" and u <= 0:
            return 0.0
        if ramp == "up" and u >= 1:
            return 1.0
        if ramp == "down" and u <= 0:
            return 1.0
        if ramp == "down" and u >= 1:
            return 0.0
        on = float(g["on"])
        if ramp == "up":
            on = on + (1 - on) * u * u
        elif ramp == "down":
            on = on * (1 - u) ** 1.5
        step = math.floor(tl * float(g["rate"]))
        v = 1.0 if hash01(step, useed, L.seed, 5) < on else float(g.get("low", 0.0))
    elif "bounce" in spec:
        g = spec["bounce"]
        height = _clamp(_pnum(ctx, L, g.get("height"), 12.0), 0.0, 30.0) / 100.0
        n = int(math.floor(_clamp(_pnum(ctx, L, g.get("bounces"), 3.0), 1.0, 8.0) + 0.5))
        d = _clamp(_pnum(ctx, L, g.get("damping"), 0.35), 0.0, 0.95)
        pu = _clamp01(u)
        idx = min(n - 1, int(math.floor(pu * n)))
        s = (pu * n) - idx if pu < 1 else 1.0
        v = -height * (1 - d) ** idx * 4 * s * (1 - s)
    else:
        v = 0.0
    if "env" in spec:
        v *= _keys(ctx, L, spec["env"], u, ctx.base, useed)
    return v


def path_point(points: list[tuple[float, float]], f: float) -> tuple[float, float]:
    """Point at arc-length fraction f along a polyline (percent coordinates)."""
    if not points:
        return (50.0, 50.0)
    if len(points) == 1:
        return points[0]
    seg = [math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1]) for i in range(len(points) - 1)]
    total = sum(seg)
    if total <= 1e-9:
        return points[0]
    target = _clamp01(f) * total
    for i, s in enumerate(seg):
        if target <= s or i == len(seg) - 1:
            g = 0.0 if s <= 0 else min(1.0, target / s)
            (x0, y0), (x1, y1) = points[i], points[i + 1]
            return (x0 + (x1 - x0) * g, y0 + (y1 - y0) * g)
        target -= s
    return points[-1]


def _content(ctx: Ctx, L: _L, e: Unit, u: float, tl: float, t: float, useed: int) -> tuple[str, dict[str, float]]:
    c = L.fx["content"]
    kind = c.get("type")
    if kind == "scramble":
        mode = c.get("mode", "during")
        active = (0 < u < 1) if mode == "during" else (u <= 0) if mode == "pending" else (u > 0)
        if active and not e.text.isspace():
            cs = c.get("charset") or "#@$%&*"
            step = math.floor(tl * float(c.get("rate", 18)))
            out = "".join(cs[int(hash01(step, useed, 3, k) * len(cs))] if not ch.isspace() else ch for k, ch in enumerate(e.text))
            return out, {}
        return e.text, {}
    if kind == "flap":
        if 0 < u < 1:
            flips = int(c.get("flips", 8))
            pos = u * flips
            n, p = int(pos), pos - int(pos)
            cs = c.get("charset") or "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ch = cs[int(hash01(n, useed, 11) * len(cs))]
            return ch, {"sy": max(0.12, abs(math.cos(math.pi * p)) ** 0.7)}
        return e.text, {}
    if kind == "count":
        easing = str(L.param("easing", c.get("easing", "linear")) or "linear")
        f = EASE.get(easing, EASE["linear"])(u)
        a = _pnum(ctx, L, L.param("from", 0), 0.0)
        b = _pnum(ctx, L, L.param("to", 100), 100.0)
        n = int(math.floor(a + (b - a) * f + 0.5))
        sep = str(L.param("separator", "") or "")
        s = f"{abs(n):,}".replace(",", sep) if sep else str(abs(n))
        if n < 0:
            s = "-" + s
        return f"{L.param('prefix', '') or ''}{s}{L.param('suffix', '') or ''}", {}
    if kind == "countdown":
        ws, we = layer_window(ctx, L)
        start = int(_clamp(_pnum(ctx, L, L.param("from", 3), 3.0), 1, 10))
        span = max(1e-3, (we - ws) / (start + 1))
        k = int(_clamp(math.floor((t - ws) / span), 0, start))
        p = _clamp01(((t - ws) - k * span) / span)
        label = str(start - k) if start - k > 0 else "GO!"
        pop = 1 + 0.45 * (1 - min(1.0, p / 0.35)) ** 3
        return label, {"scale": pop, "opacity": 1.0 if p < 0.82 else max(0.0, 1 - (p - 0.82) / 0.18)}
    return e.text, {}


def evaluate(ctx: Ctx, e: Unit, t: float) -> dict[str, Any]:
    """Composed state of unit ``e`` (at level ctx.gran, or a coarser level for
    box copies) at time t. Identical to evaluate() in src/textMotion.ts."""
    cap = ctx.cap
    em = cap.em
    start_rank = _level_rank(e.level)
    levels = [lvl for lvl in ctx.levels if _level_rank(lvl) >= start_rank]
    if e.level not in levels:
        levels = [e.level] + levels
    acc: dict[str, dict[str, float]] = {lvl: dict(NEUTRAL) for lvl in levels}
    colour = ctx.base
    split: dict[str, Any] | None = None      # {"a": colour, "b": colour, "region": ..., "kind": "bg"|"wipe", "L": src}
    mask: str | None = None                  # "in" | "out": visible only inside / outside the bg region
    mask_mix = 1.0
    clip = None
    text = e.text
    mods: dict[str, float] = {}
    bg = cap.bg
    for L in ctx.stack:
        lvl = L.unit
        if lvl not in acc:
            continue
        a = _ancestor(ctx, e, lvl)
        if a is None or not _in_range(L, a):
            continue
        rank = L.ranks.get(a.index)
        if rank is None:
            continue
        u, tl = local_u(ctx, L, rank, t)
        useed = a.index * 7919 + int(_level_rank(lvl) * 2)
        fx = L.fx
        for ch, spec in (fx.get("tracks") or {}).items():
            if ch == "path":
                if cap.motion is None or not cap.motion.points:
                    continue
                f = PATH_EASE.get(cap.motion.easing, PATH_EASE["linear"])(u)
                px, py = path_point(cap.motion.points, f)
                acc[lvl]["px"] += px / 100 * cap.frame_w - a.cx
                acc[lvl]["py"] += py / 100 * cap.frame_h - a.cy
                continue
            if ch == "clip":
                running = (L.phase == "in" and u < 1) or (L.phase == "out" and u > 0) or L.phase == "hold"
                if not running:
                    continue
                l, tp, r, b = _keys(ctx, L, spec, u, ctx.base, useed)
                pad = fx.get("clipPad") or [0.3, 0.3]
                x0, y0, x1, y1 = a.box()
                x0 -= pad[0] * em
                x1 += pad[0] * em
                y0 -= pad[1] * em
                y1 += pad[1] * em
                # a mask fixed at the unit's rest place (text slides inside it);
                # the motion path moves it along, see below
                rect = (x0 + (x1 - x0) * l, y0 + (y1 - y0) * tp, x0 + (x1 - x0) * r, y0 + (y1 - y0) * b)
                clip = rect if clip is None else (max(clip[0], rect[0]), max(clip[1], rect[1]),
                                                  min(clip[2], rect[2]), min(clip[3], rect[3]))
                continue
            if ch == "colour":
                colour = _keys(ctx, L, spec, u, colour, useed)
                if split is not None:
                    split = {**split, "a": _keys(ctx, L, spec, u, split["a"], useed), "b": _keys(ctx, L, spec, u, split["b"], useed)}
                continue
            if isinstance(spec, dict):
                v = _generator(ctx, L, spec, u, tl, useed)
            else:
                v = _keys(ctx, L, spec, u, ctx.base, useed)
                if isinstance(v, tuple):
                    continue
            if L.intensity != 1.0:
                v = NEUTRAL[ch] + (v - NEUTRAL[ch]) * L.intensity
            if ch in MUL:
                acc[lvl][ch] *= v
            elif ch in MAXC:
                acc[lvl][ch] = max(acc[lvl][ch], v)
            elif ch in NEUTRAL:
                acc[lvl][ch] += v
        # colour swept across the unit (karaoke)
        wipe = fx.get("colourWipe")
        if wipe:
            ca = colour_token(ctx, _param_str(L, wipe.get("from", "base")), colour)
            cb = colour_token(ctx, _param_str(L, wipe.get("to", "base")), colour)
            if u <= 0:
                colour = ca
            elif u >= 1:
                colour = cb
            else:
                x0, y0, x1, y1 = a.box()
                pad = 0.12 * em
                x0, x1, y0, y1 = x0 - pad, x1 + pad, y0 - em, y1 + em
                d = wipe.get("dir", "ltr")
                if d == "rtl":
                    rect = [x1 - (x1 - x0) * u, y0, x1, y1]
                elif d == "ttb":
                    rect = [x0, y0, x1, y0 + (y1 - y0) * u]
                elif d == "btt":
                    rect = [x0, y1 - (y1 - y0) * u, x1, y1]
                else:
                    rect = [x0, y0, x0 + (x1 - x0) * u, y1]
                split = {"a": ca, "b": cb, "region": {"kind": "rect", "rect": rect, "inside": True}, "kind": "wipe", "f": u}
                colour = ca
        # the frame's colour change
        bgspec = fx.get("bg")
        if bgspec and bg is not None:
            p = _clamp01((t - bg.start) / max(bg.time, 1e-6))
            region = bg_region(bg.transition, p, cap.frame_w, cap.frame_h)
            mode = bgspec.get("mode")
            if mode == "follow":
                ca = colour_token(ctx, _param_str(L, bgspec.get("from", "base")), colour)
                cb = colour_token(ctx, _param_str(L, bgspec.get("to", "contrastB")), colour)
                kind = region["kind"]
                if kind == "none":
                    colour = ca
                elif kind == "all":
                    colour = cb
                elif kind == "mix":
                    colour = _mix(ca, cb, region["f"])
                else:
                    split = {"a": ca, "b": cb, "region": region, "kind": "bg", "f": p}
                    colour = ca
            elif mode in ("arrive", "leave"):
                kind = region["kind"]
                visible_in = mode == "arrive"
                if kind == "none":
                    mask_mix *= 0.0 if visible_in else 1.0
                elif kind == "all":
                    mask_mix *= 1.0 if visible_in else 0.0
                elif kind == "mix":
                    mask_mix *= region["f"] if visible_in else 1 - region["f"]
                else:
                    mask = "in" if visible_in else "out"
                    if split is not None and split.get("kind") == "wipe":
                        colour = _mix(split["a"], split["b"], split["f"])
                        split = None
                    if split is None:
                        split = {"a": colour, "b": colour, "region": region, "kind": "mask", "f": p}
        if "content" in fx and ctx.content.get(L.phase) is L:
            text, extra = _content(ctx, L, e, u, tl, t, useed)
            for k2, v2 in extra.items():
                mods[k2] = mods.get(k2, 1.0) * v2

    tot = dict(NEUTRAL)
    for lvl in levels:
        for ch, v in acc[lvl].items():
            if ch in MUL:
                tot[ch] *= v
            elif ch in MAXC:
                tot[ch] = max(tot[ch], v)
            else:
                tot[ch] += v
    for k2, v2 in mods.items():
        tot[k2] = tot.get(k2, 1.0) * v2
    tot["opacity"] *= mask_mix
    # squash: < 1 flat and wide, like a stamp press
    sq = tot["squash"]
    tot["sy"] *= sq
    tot["sx"] *= 1 + (1 - sq) * 0.5

    x, y = e.cx, e.cy
    if ctx.gran == "char" and e.level == "char" and tot["spacing"]:
        x += tot["spacing"] * em * (e.col - (e.ncol - 1) / 2)
    whole = ctx.units["text"][0]
    for lvl in levels:
        a = _ancestor(ctx, e, lvl)
        if a is None:
            continue
        s = acc[lvl]
        if s["gather"]:
            x += (whole.cx - x) * s["gather"]
            y += (whole.cy - y) * s["gather"]
        if lvl != e.level:
            dx, dy = x - a.cx, y - a.cy
            dx *= s["scale"] * s["sx"] * (1 + (1 - s["squash"]) * 0.5)
            dy *= s["scale"] * s["sy"] * s["squash"]
            th = math.radians(s["rz"])              # clockwise on screen (y down)
            dx, dy = dx * math.cos(th) - dy * math.sin(th), dx * math.sin(th) + dy * math.cos(th)
            x, y = a.cx + dx, a.cy + dy
        x += s["x"] * em + s["vx"] * cap.frame_w + s["px"]
        y += s["y"] * em + s["vy"] * cap.frame_h + s["py"]
    if clip is not None:
        # the motion path places the whole text: masks travel with it
        path_dx = sum(acc[lvl]["px"] for lvl in levels)
        path_dy = sum(acc[lvl]["py"] for lvl in levels)
        if path_dx or path_dy:
            clip = (clip[0] + path_dx, clip[1] + path_dy, clip[2] + path_dx, clip[3] + path_dy)
    if split is not None and split.get("kind") == "wipe":
        # a colour wipe is drawn across the unit where it is now
        dx, dy = x - e.cx, y - e.cy
        r0 = split["region"]["rect"]
        split = {**split, "region": {"kind": "rect", "rect": [r0[0] + dx, r0[1] + dy, r0[2] + dx, r0[3] + dy], "inside": True}}
    org = None
    if ctx.org_level is not None and ctx.org_level in acc:
        a = _ancestor(ctx, e, ctx.org_level)
        if a is not None:
            s = acc[ctx.org_level]
            org = (a.cx + s["x"] * em + s["vx"] * cap.frame_w + s["px"], a.cy + s["y"] * em + s["vy"] * cap.frame_h + s["py"])
    if split is not None and mask is not None:
        split["mask"] = mask
    return {"x": x, "y": y, "text": text, "tot": tot, "colour": colour, "clip": clip, "org": org, "split": split}


def ambient_opacity(ctx: Ctx, t: float) -> float:
    """Opacity of the text-wide layers only (a caret fades with a Fade out)."""
    op = 1.0
    for L in ctx.stack:
        spec = (L.fx.get("tracks") or {}).get("opacity")
        if spec is None or "caret" in L.fx or L.unit != "text":
            continue
        u, tl = local_u(ctx, L, L.ranks.get(0, 0.0), t)
        v = _generator(ctx, L, spec, u, tl, 6) if isinstance(spec, dict) else _keys(ctx, L, spec, u, ctx.base, 6)
        op *= float(v)
    return _clamp01(op)


_ambient_opacity_ctx = ambient_opacity


def _param_str(L: _L, v: Any) -> str:
    if isinstance(v, str) and v.startswith("$"):
        return str(L.param(v[1:], "base") or "base")
    return str(v)


def phase_spans(ctx: Ctx) -> dict[str, float]:
    """Seconds each lane occupies (for timeline bars)."""
    out = {"in": 0.0, "out": 0.0}
    for L in ctx.stack:
        if L.phase in out and not L.synced:
            out[L.phase] = max(out[L.phase], L.delay + (L.duration or 0.5))
    return out


# ============================================================================
# State -> libass
# ============================================================================

FRACT = 1.0

OUTLINE_RGB = (0.0, 0.0, 0.0)
OUTLINE_ALPHA = 0x26
SHADOW_ALPHA = 0x73


def _escape(text: str) -> str:
    return str(text).replace("\\", "\\\u2060").replace("{", "(").replace("}", ")")


def _ass_colour(c: Iterable[float]) -> str:
    r, g, b = (int(round(_clamp(v, 0, 255))) for v in c)
    return f"&H{b:02X}{g:02X}{r:02X}&"


def _alpha_hex(v: float) -> str:
    return f"&H{int(round(_clamp(v, 0, 255))):02X}&"


@dataclass
class _Draw:
    """One drawn copy of a unit at one frame (what _emit turns into events)."""
    x: float
    y: float
    text: str            # escaped visible text (or a drawing)
    pre: str             # escaped hidden text before it on the line (in-line events)
    post: str            # escaped hidden text after it
    a1: float
    a3: float
    a4: float
    fscx: float
    fscy: float
    frz: float
    frx: float
    fry: float
    fax: float
    blur: float
    bord: float
    shad: float
    fsp: float
    c1: tuple[float, float, float]
    c3: tuple[float, float, float]
    clip: tuple[float, float, float, float] | None
    iclip: bool
    vclip: str | None    # vector clip drawing (static per frame)
    org: tuple[float, float] | None
    vis: bool
    an: int = 5
    drawing: bool = False


def _round_half(v: float) -> float:
    return round(v * 2) / 2


class Compiler:
    """Compiles one caption to ASS events (plus the style they use)."""

    def __init__(self, cap: Caption, fonts_dir: Path | str | None):
        self.cap = cap
        self.font = resolve_font(cap.family, cap.bold, cap.italic, fonts_dir)
        self.shaper = Shaper(self.font)
        em = cap.em
        self.line_h = self.font.line_height(em)
        lines = clean_lines(cap.text)
        self.layout = build_layout(lines, lambda s: self.shaper.advances(s, em), em, self.line_h,
                                   cap.x, cap.y, cap.align)
        self.ctx = compile_stack(cap, self.layout)
        self.outline_px = float(max(1, round(em / 16))) if cap.outline else 0.0
        self.shadow_px = 2.0 if cap.outline else 0.0
        self.style_name = "TM"
        k = em / self.font.upm
        metrics = font_metrics(self.font.family, self.font.bold, self.font.italic) or {}
        # baseline below the line-box centre, cap height (px) - for boxes
        self.baseline = (self.font.win_ascent - self.font.win_descent) / 2 * k
        self.cap_h = float(metrics.get("capHeight") or self.font.upm * 0.7) * k

    # -------------------------------------------------------------- style
    def style_line(self) -> str:
        f = self.font
        size = f.ass_size(self.cap.em)
        return (f"Style: {self.style_name},{f.family.replace(',', ' ')},{size:.2f},&H00FFFFFF,&H000000FF,&H26000000,&H73000000,"
                f"{-1 if f.bold else 0},{-1 if f.italic else 0},{-1 if self.cap.underline else 0},0,100,100,0,0,1,"
                f"{self.outline_px:.2f},{self.shadow_px:.2f},5,0,0,0,1")

    # -------------------------------------------------------------- frames
    def frame_times(self) -> list[float]:
        cap = self.cap
        fps = max(1.0, float(cap.fps))
        k0 = int(math.ceil(cap.start * fps - 1e-6))
        k1 = int(math.ceil(cap.end * fps - 1e-6)) - 1
        if k1 < k0:
            return []
        return [k / fps for k in range(k0, k1 + 1)]

    def split_text(self, e: Unit, text: str) -> tuple[str, str, str]:
        """(hidden before, visible, hidden after) of the in-line event of e."""
        lines = self.layout.lines
        if e.level == "text":
            return "", _escape(text).replace("\n", r"\N"), ""
        if e.level == "line" or e.level.startswith("g"):
            return "", _escape(text), ""
        line = lines[e.line]
        return _escape(line[:e.c0]), _escape(text), _escape(line[e.c1:])

    def draw_from_state(self, e: Unit, s: dict[str, Any], colour: tuple[float, float, float] | None = None,
                        clip: Any = "state", iclip: bool = False, vclip: str | None = None,
                        extra_alpha: float = 1.0) -> _Draw:
        tot = s["tot"]
        em = self.cap.em
        op = _clamp01(tot["opacity"]) * extra_alpha
        fill = _clamp01(tot["fill"])
        line_px = max(0.0, tot["line"] * em)
        c1 = colour if colour is not None else s["colour"]
        w = _clamp01(line_px) if line_px > 0 else 0.0
        if self.outline_px:
            c3 = tuple(OUTLINE_RGB[i] + (c1[i] - OUTLINE_RGB[i]) * w for i in range(3))
            a3_op = (1 - OUTLINE_ALPHA / 255.0) * (1 - w) + w
        else:
            c3, a3_op = c1, 1.0
        fscx = 100 * tot["scale"] * tot["sx"]
        fscy = 100 * tot["scale"] * tot["sy"]
        rotating = abs(tot["rz"]) > 1e-4 or abs(tot["rx"]) > 1e-4 or abs(tot["ry"]) > 1e-4
        if e.level in ("char", "word"):
            line = self.layout.units["line"][e.line]
            x = s["x"] - (e.cx - line.cx) * fscx / 100.0
            org = s["org"] if s["org"] is not None else ((_round_half(s["x"]), _round_half(s["y"])) if rotating else None)
        else:
            x = s["x"]
            org = s["org"]
        pre, text, post = self.split_text(e, s["text"])
        shadow_a = (1 - SHADOW_ALPHA / 255.0) if self.shadow_px else 0.0
        return _Draw(
            x=x, y=s["y"], text=text, pre=pre, post=post,
            a1=255 * (1 - op * fill), a3=255 * (1 - op * a3_op), a4=255 * (1 - op * fill * shadow_a),
            fscx=fscx, fscy=fscy, frz=-tot["rz"], frx=tot["rx"], fry=tot["ry"], fax=tot["skew"],
            blur=max(0.0, tot["blur"] * em), bord=self.outline_px + line_px, shad=self.shadow_px,
            fsp=tot["spacing"] * em if e.level in ("line", "text") else 0.0,
            c1=tuple(c1), c3=tuple(c3), clip=s["clip"] if clip == "state" else clip, iclip=iclip, vclip=vclip,
            org=org, vis=op > 0.004 and (fill > 0.004 or line_px > 0.05 or self.outline_px > 0),
        )

    # -------------------------------------------------------------- compile
    def compile(self) -> list[str]:
        ctx = self.ctx
        times = self.frame_times()
        if not times or not any(self.layout.lines):
            return []
        events: list[str] = []
        base_layer = self.cap.layer
        copies = [(L, spec) for L in ctx.stack for spec in (L.fx.get("copies") or []) if spec.get("type") != "box"]
        boxes = [(L, spec) for L in ctx.stack for spec in (L.fx.get("copies") or []) if spec.get("type") == "box"]
        for L, spec in boxes:
            events += self._box_events(L, spec, times, base_layer - 2 if spec.get("below", True) else base_layer + 1)
        if ctx.gran in ("char", "word"):
            # Letters / words get their own events only while a letter-level
            # layer makes them differ from their line; the rest of the time the
            # whole line is one event (a 20 s caption with a 1 s letter reveal
            # would otherwise carry every letter through the whole hold).
            members: dict[int, list[Unit]] = {}
            for e in ctx.units[ctx.gran]:
                if e.text.strip():
                    members.setdefault(e.line, []).append(e)
            for line_unit in ctx.units["line"]:
                group = members.get(line_unit.index, [])
                line_states = [evaluate(ctx, line_unit, t) for t in times]
                states = {e.index: [evaluate(ctx, e, t) for t in times] for e in group}
                coarse = [bool(group) and all(self._same_as_line(line_unit, line_states[k], e, states[e.index][k]) for e in group)
                          for k in range(len(times))]
                coarse = _debounce(coarse, 3)
                events += self._unit_events(line_unit, line_states, times, copies, base_layer, coarse)
                fine = [not c for c in coarse]
                for e in group:
                    events += self._unit_events(e, states[e.index], times, copies, base_layer, fine)
        else:
            for e in ctx.units[ctx.gran]:
                states = [evaluate(ctx, e, t) for t in times]
                events += self._unit_events(e, states, times, copies, base_layer, None)
        if any("caret" in L.fx for L in ctx.stack) and ctx.gran == "char":
            events += self._caret_events(times, base_layer + 1)
        return events

    def _unit_events(self, e: Unit, states: list[dict[str, Any]], times: list[float], copies: list, base_layer: int,
                     show: list[bool] | None) -> list[str]:
        if show is not None and not any(show):
            return []
        main: list[_Draw] = []
        other: list[_Draw] = []
        for k, s in enumerate(states):
            a, b = self._split_draws(e, s)
            if show is not None and not show[k]:
                a, b = dataclasses.replace(a, vis=False), dataclasses.replace(b, vis=False)
            main.append(a)
            other.append(b)
        events: list[str] = []
        for L, spec in copies:
            for cdraws in self._copy_draws(spec, L, states, e, times):
                if show is not None:
                    cdraws = [d if show[k] else dataclasses.replace(d, vis=False) for k, d in enumerate(cdraws)]
                events += self._emit(cdraws, times, base_layer - 1)
        events += self._emit(main, times, base_layer)
        if any(d.vis for d in other):
            events += self._emit(other, times, base_layer)
        return events

    def _same_as_line(self, line: Unit, ls: dict[str, Any], e: Unit, s: dict[str, Any]) -> bool:
        """Would the whole-line event draw unit e exactly like its own event?"""
        if s["text"] != e.text or s["org"] != ls["org"]:
            return False
        if (s["clip"] is None) != (ls["clip"] is None):
            return False
        if s["clip"] is not None and any(abs(a - b) > 0.25 for a, b in zip(s["clip"], ls["clip"])):
            return False
        sp, lp = s.get("split"), ls.get("split")
        if (sp is None) != (lp is None):
            return False
        if sp is not None and (sp.get("kind") != lp.get("kind") or sp.get("region") != lp.get("region")
                               or sp.get("a") != lp.get("a") or sp.get("b") != lp.get("b") or sp.get("mask") != lp.get("mask")):
            return False
        if any(abs(a - b) > 0.01 for a, b in zip(s["colour"], ls["colour"])):
            return False
        st, lt = s["tot"], ls["tot"]
        for ch in CHANNELS:
            if abs(st[ch] - lt[ch]) > 1e-5:
                return False
        if abs(lt["spacing"]) > 1e-6:
            return False
        sx = lt["scale"] * lt["sx"]
        th = math.radians(lt["rz"])
        d = e.cx - line.cx
        ex = ls["x"] + d * sx * math.cos(th)
        ey = ls["y"] + d * sx * math.sin(th)
        return abs(s["x"] - ex) < 0.3 and abs(s["y"] - ey) < 0.3

    def _split_draws(self, e: Unit, s: dict[str, Any]) -> tuple[_Draw, _Draw]:
        """Main draw (colour A / the whole unit) and the colour-B draw of a split."""
        split = s.get("split")
        if not split:
            d = self.draw_from_state(e, s)
            return d, dataclasses.replace(d, vis=False)
        region = split["region"]
        mask = split.get("mask")
        kind = split.get("kind")
        f = float(split.get("f", 0.5))
        if s["clip"] is not None or region.get("kind") not in ("rect", "circle", "poly"):
            # a unit clip and a region cannot share one event: crossfade instead
            if kind == "mask":
                d = self.draw_from_state(e, s, extra_alpha=f if mask == "in" else 1 - f)
            else:
                extra = f if mask == "in" else (1 - f) if mask == "out" else 1.0
                d = self.draw_from_state(e, s, colour=_mix(split["a"], split["b"], f), extra_alpha=extra)
            return d, dataclasses.replace(d, vis=False)
        inside = region.get("inside", True)
        clip_b, iclip_b, vclip_b = self._region_clip(region, inside)
        clip_a, iclip_a, vclip_a = self._region_clip(region, not inside)
        draw_a = self.draw_from_state(e, s, colour=split["a"], clip=clip_a, iclip=iclip_a, vclip=vclip_a)
        draw_b = self.draw_from_state(e, s, colour=split["b"], clip=clip_b, iclip=iclip_b, vclip=vclip_b)
        if kind == "mask":        # arrive / leave on their own: one clipped draw
            d = draw_b if mask == "in" else draw_a
            return d, dataclasses.replace(d, vis=False)
        if mask == "in":
            draw_a = dataclasses.replace(draw_a, vis=False)
        elif mask == "out":
            draw_b = dataclasses.replace(draw_b, vis=False)
        return draw_a, draw_b

    def _region_clip(self, region: dict[str, Any], inside: bool) -> tuple[Any, bool, str | None]:
        """Clip showing the region (inside=True) or everything but it."""
        kind = region["kind"]
        if kind == "rect":
            return tuple(region["rect"]), not inside, None
        if kind == "circle":
            n = 48
            r = max(0.5, region["r"])
            pts = [(region["cx"] + r * math.cos(2 * math.pi * i / n), region["cy"] + r * math.sin(2 * math.pi * i / n)) for i in range(n)]
        else:
            pts = [tuple(p) for p in region["points"]]
        return None, not inside, "m " + " l ".join(f"{x:.0f} {y:.0f}" for x, y in pts)

    # -------------------------------------------------------------- copies
    def _copy_draws(self, spec: dict, L: _L, states: list[dict], e: Unit, times: list[float]):
        kind = spec.get("type")
        em = self.cap.em
        ctx = self.ctx
        plain = {"a3": 255.0, "a4": 255.0, "shad": 0.0, "bord": 0.0}

        def colour(v: Any, s: dict) -> tuple[float, float, float]:
            return colour_token(ctx, _param_str(L, v), s["colour"])

        if kind == "glow":
            out = []
            for s in states:
                d = self.draw_from_state(e, s)
                g = s["tot"]["glow"]
                op = _clamp01(s["tot"]["opacity"])
                out.append(dataclasses.replace(d, a1=255.0, a4=255.0, shad=0.0, a3=255 * (1 - op),
                                               bord=float(spec.get("bord", 0.07)) * em * g,
                                               blur=d.blur + float(spec.get("blur", 0.16)) * em * g,
                                               c3=colour(spec.get("colour", "base"), s), vis=g > 0.01 and op > 0.004))
            yield out
        elif kind == "trail":
            n = int(spec.get("count", 4))
            for k in range(n, 0, -1):
                out = []
                for t in times:
                    s = evaluate(ctx, e, t - k * float(spec.get("delay", 0.05)))
                    d = self.draw_from_state(e, s)
                    op = _clamp01(s["tot"]["opacity"]) * float(spec.get("alpha", 0.5)) * (1 - (k - 1) / n)
                    out.append(dataclasses.replace(d, a1=255 * (1 - op), c1=colour(spec.get("colour", "base"), s),
                                                   vis=op > 0.004, **plain))
                yield out
        elif kind == "extrude":
            n = int(spec.get("count", 8))
            base = colour(spec.get("colour", "#6a4de0"), {"colour": ctx.base})
            for k in range(n, 0, -1):
                shade = 1 - float(spec.get("shade", 0.5)) * k / n
                rgbc = tuple(c * shade for c in base)
                out = []
                for s in states:
                    d = self.draw_from_state(e, s)
                    dep = s["tot"]["depth"]
                    out.append(dataclasses.replace(d, x=d.x + k * float(spec["dx"]) * em * dep, y=d.y + k * float(spec["dy"]) * em * dep,
                                                   c1=rgbc, vis=d.vis and dep > 0.01, **plain))
                yield out
        elif kind == "mirror":
            whole = ctx.units["text"][0]
            axis = whole.cy + whole.h / 2 + float(spec.get("gap", 0.06)) * em
            out = []
            for s in states:
                d = self.draw_from_state(e, s)
                op = _clamp01(s["tot"]["opacity"]) * float(spec.get("alpha", 0.3))
                out.append(dataclasses.replace(d, y=2 * axis - d.y, frx=d.frx + 180, org=None, a1=255 * (1 - op),
                                               blur=d.blur + float(spec.get("blur", 0.03)) * em, fscy=d.fscy * 0.9,
                                               vis=op > 0.004, **plain))
            yield out
        elif kind == "rgb":
            for sign, hexc in zip((-1, 1), spec.get("colours", ["#ff2a6d", "#05d9e8"])):
                c = rgb(hexc)
                out = []
                for s in states:
                    d = self.draw_from_state(e, s)
                    amount = s["tot"]["rgb"]
                    op = _clamp01(s["tot"]["opacity"]) * 0.85
                    out.append(dataclasses.replace(d, x=d.x + sign * float(spec["dx"]) * em * amount, c1=c, a1=255 * (1 - op),
                                                   vis=op > 0.004 and amount > 0.01, **plain))
                yield out
        elif kind == "shadow":
            cols = spec.get("colours", ["#ff4f8b", "#3fd0ff"])
            for i, hexc in enumerate(cols):
                c = rgb(hexc)
                out = []
                for s in states:
                    d = self.draw_from_state(e, s)
                    ang = 2 * math.pi * (s["tot"]["orbit"] + i / len(cols))
                    r = float(spec.get("radius", 0.06)) * em
                    out.append(dataclasses.replace(d, x=d.x + r * math.cos(ang), y=d.y + r * math.sin(ang), c1=c, **plain))
                yield out

    def box_geometry(self, u: Unit, spec: dict) -> tuple[float, float, float]:
        """(width, height, vertical offset of the box centre from the unit centre)."""
        em = self.cap.em
        pw = float((spec.get("pad") or [0.1, 0.0])[0])
        w = u.w + 2 * pw * em
        h = float(spec.get("height", 1.0)) * em
        valign = spec.get("valign", "center")
        off = float(spec.get("offsetY", 0.0)) * em
        if valign == "baseline":
            off += self.baseline
        elif valign == "cap":
            off += self.baseline - self.cap_h / 2
        return w, h, off

    def _box_events(self, L: _L, spec: dict, times: list[float], layer: int) -> list[str]:
        """Boxes (bars, highlighter, split-flap tiles): one per unit of the layer's level."""
        ctx = self.ctx
        events: list[str] = []
        colour_v = _param_str(L, spec.get("colour", "#000000"))
        box_opacity = _clamp01(float(spec.get("alpha", 1.0)))
        for u in ctx.units.get(L.unit, []):
            if not _in_range(L, u) or L.ranks.get(u.index) is None:
                continue
            if u.level in ("char", "word") and not u.text.strip():
                continue
            w, h, off = self.box_geometry(u, spec)
            draws: list[_Draw] = []
            gaps: list[_Draw] = []
            anchor_left = spec.get("anchor") == "left"
            for t in times:
                s = evaluate(ctx, u, t)
                lu, _ = local_u(ctx, L, L.ranks[u.index], t)
                col = colour_token(ctx, colour_v, s["colour"])
                op = _clamp01(s["tot"]["opacity"]) * box_opacity
                sx = 1.0
                if "sx" in (spec.get("tracks") or {}):
                    sx = float(_keys(ctx, L, spec["tracks"]["sx"], lu, ctx.base, 0))
                cx, cy = s["x"], s["y"] + off
                clip = None
                if spec.get("clip") and lu < 1:
                    l, tp, r, b = _keys(ctx, L, spec["clip"], lu, ctx.base, 0)
                    clip = (cx - w / 2 + w * l, cy - h / 2 + h * tp, cx - w / 2 + w * r, cy - h / 2 + h * b)
                drawing = f"m 0 0 l {w:.1f} 0 {w:.1f} {h:.1f} 0 {h:.1f}"
                d = _Draw(x=(cx - w / 2) if anchor_left else cx, y=cy, text="{\\p1}" + drawing + "{\\p0}", pre="", post="",
                          a1=255 * (1 - op), a3=255.0, a4=255.0, fscx=100.0 * sx, fscy=100.0, frz=-s["tot"]["rz"],
                          frx=0.0, fry=0.0, fax=0.0, blur=0.0, bord=0.0, shad=0.0, fsp=0.0, c1=tuple(col), c3=tuple(col),
                          clip=clip, iclip=False, vclip=None, org=None, vis=op > 0.004 and sx > 0.002,
                          an=4 if anchor_left else 5, drawing=True)
                draws.append(d)
                if spec.get("split"):
                    gh = max(1.0, h * 0.03)
                    gaps.append(dataclasses.replace(d, text="{\\p1}" + f"m 0 0 l {w:.1f} 0 {w:.1f} {gh:.1f} 0 {gh:.1f}" + "{\\p0}",
                                                    c1=rgb(spec["split"]), c3=rgb(spec["split"])))
            events += self._emit(draws, times, layer)
            if gaps:
                events += self._emit(gaps, times, layer)
        return events

    # -------------------------------------------------------------- caret
    def _caret_events(self, times: list[float], layer: int) -> list[str]:
        ctx = self.ctx
        em = self.cap.em
        chars = [c for c in ctx.units["char"] if c.text.strip()]
        if not chars:
            return []
        blink = 1.0
        rows = []
        last_change = times[0]
        prev = None
        for t in times:
            visible = []
            for c in chars:
                s = evaluate(ctx, c, t)
                if s["tot"]["opacity"] > 0.5 and (s["clip"] is None or s["clip"][2] > s["clip"][0] + 1):
                    visible.append((c, s))
            if len(visible) != prev:
                last_change, prev = t, len(visible)
            if visible:
                c, s = visible[-1]
                x, y = s["x"] + c.w / 2 + 0.07 * em, s["y"]
            else:
                c = chars[0]
                x, y = c.cx - c.w / 2, c.cy
            typing = (t - last_change) < 0.35 and t > times[0] + 1e-6
            on = typing or ((t - last_change) % blink) < blink / 2
            op = self._ambient_opacity(t) if on else 0.0
            rows.append((round(x, 1), round(y, 1), op))
        events = []
        k0 = 0
        fps = max(1.0, float(self.cap.fps))
        colour = _ass_colour(ctx.base)
        for k in range(1, len(rows) + 1):
            if k == len(rows) or rows[k][:2] != rows[k0][:2] or (rows[k][2] > 0.004) != (rows[k0][2] > 0.004):
                x, y, op = rows[k0]
                if op > 0.004:
                    t0 = times[k0] - 0.5 / fps
                    t1 = times[k - 1] + 0.5 / fps
                    fade = ""
                    op_end = rows[k - 1][2]
                    if abs(op_end - op) > 0.01 and k - 1 > k0:
                        fade = (f"\\t({int(round((times[k0] - t0) * 1000))},{int(round((times[k - 1] - t0) * 1000))},"
                                f"\\1a{_alpha_hex(255 * (1 - op_end))})")
                    events.append(f"Dialogue: {layer},{ts(t0)},{ts(t1)},{self.style_name},,0,0,0,,"
                                  f"{{\\an5\\pos({x:.1f},{y:.1f})\\bord0\\shad0\\1c{colour}\\1a{_alpha_hex(255 * (1 - op))}{fade}}}|")
                k0 = k
        return events

    def _ambient_opacity(self, t: float) -> float:
        return ambient_opacity(self.ctx, t)

    # -------------------------------------------------------------- emission
    _NUM = ("a1", "a3", "a4", "fscx", "fscy", "frz", "frx", "fry", "fax", "blur", "bord", "fsp")
    _ALPHA = {0, 1, 2}
    _TOL = {"a1": 2.0, "a3": 2.0, "a4": 2.0, "fscx": 0.4, "fscy": 0.4, "frz": 0.3, "frx": 0.5, "fry": 0.5, "fax": 0.004,
            "blur": 0.15, "bord": 0.1, "fsp": 0.3}
    _DEFAULT: dict[str, float | None] = {"a1": None, "a3": None, "a4": None, "fscx": 100.0, "fscy": 100.0, "frz": 0.0, "frx": 0.0,
                                         "fry": 0.0, "fax": 0.0, "blur": 0.0, "bord": None, "fsp": 0.0}

    def _vector(self, d: _Draw) -> list[float]:
        v = [getattr(d, k) for k in self._NUM]
        v += list(d.c1) + list(d.c3)
        v += list(d.clip) if d.clip is not None else [0.0, 0.0, 0.0, 0.0]
        return v

    def _tolerances(self) -> list[float]:
        return [self._TOL[k] for k in self._NUM] + [3.0] * 6 + [0.6] * 4

    def _tags(self, d: _Draw, which: set[int]) -> tuple[str, str]:
        """(event tags, unit tags): alphas go right before the visible unit."""
        head, unit = [], []
        n = len(self._NUM)
        fmt = {
            "a1": lambda v: f"\\1a{_alpha_hex(v)}", "a3": lambda v: f"\\3a{_alpha_hex(v)}", "a4": lambda v: f"\\4a{_alpha_hex(v)}",
            "fscx": lambda v: f"\\fscx{v:.1f}", "fscy": lambda v: f"\\fscy{v:.1f}", "frz": lambda v: f"\\frz{v:.2f}",
            "frx": lambda v: f"\\frx{v:.2f}", "fry": lambda v: f"\\fry{v:.2f}", "fax": lambda v: f"\\fax{v:.3f}",
            "blur": lambda v: f"\\blur{v:.2f}", "bord": lambda v: f"\\bord{v:.2f}", "fsp": lambda v: f"\\fsp{v:.2f}",
        }
        for i, key in enumerate(self._NUM):
            if i in which:
                (unit if i in self._ALPHA else head).append(fmt[key](getattr(d, key)))
        if any(i in which for i in range(n, n + 3)):
            head.append(f"\\1c{_ass_colour(d.c1)}")
        if any(i in which for i in range(n + 3, n + 6)):
            head.append(f"\\3c{_ass_colour(d.c3)}")
        if d.clip is not None and any(i in which for i in range(n + 6, n + 10)):
            x0, y0, x1, y1 = d.clip
            tag = "\\iclip" if d.iclip else "\\clip"
            head.append(f"{tag}({x0:.0f},{y0:.0f},{max(x0, x1):.0f},{max(y0, y1):.0f})")
        return "".join(head), "".join(unit)

    def _emit(self, draws: list[_Draw], times: list[float], layer: int) -> list[str]:
        """Per-frame draws -> as few Dialogue events as libass allows."""
        n = len(draws)
        if n == 0:
            return []
        fps = max(1.0, float(self.cap.fps))
        hard = [0]
        for k in range(1, n):
            a, b = draws[k - 1], draws[k]
            if (a.text != b.text or a.pre != b.pre or a.post != b.post or (a.clip is None) != (b.clip is None)
                    or a.iclip != b.iclip or a.vclip != b.vclip or a.org != b.org or a.vis != b.vis or a.an != b.an
                    or a.shad != b.shad):
                hard.append(k)
        hard.append(n)
        pos = [[d.x, d.y] for d in draws]
        vec = [self._vector(d) for d in draws]
        tol = self._tolerances()
        ncols = len(self._NUM)
        events: list[str] = []
        for a, b in zip(hard, hard[1:]):
            if not draws[a].vis:
                continue
            last = b - 1
            cuts = _linear_breaks(pos, [0.35, 0.35], a, last)
            pieces = list(zip(cuts, cuts[1:])) or [(a, a)]
            for pi, (p0, p1) in enumerate(pieces):
                final = pi == len(pieces) - 1
                seg_end = p1 if final else max(p0, p1 - 1)
                d0 = draws[p0]
                ev_t0 = times[p0] - 0.5 / fps
                ev_t1 = times[seg_end] + 0.5 / fps
                x0, y0 = pos[p0]
                x1, y1 = pos[p1]
                if p1 == p0 or (abs(x1 - x0) < 0.05 and abs(y1 - y0) < 0.05):
                    anchor = f"\\pos({x0:.1f},{y0:.1f})"
                else:
                    ms0 = int(round((times[p0] - ev_t0) * 1000))
                    ms1 = int(round((times[p1] - ev_t0) * 1000))
                    anchor = f"\\move({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f},{ms0},{ms1})"
                head = f"\\an{d0.an}" + anchor
                if d0.org is not None:
                    head += f"\\org({d0.org[0]:.1f},{d0.org[1]:.1f})"
                if d0.shad != self.shadow_px:
                    head += f"\\shad{d0.shad:.2f}"
                span = range(p0, p1 + 1)
                varying = [c for c in range(len(vec[p0]))
                           if max(vec[k][c] for k in span) - min(vec[k][c] for k in span) > tol[c] * 0.5]
                static = set(varying)
                for i, key in enumerate(self._NUM):
                    dflt = self._DEFAULT[key]
                    if dflt is None or abs(getattr(d0, key) - dflt) > 1e-3:
                        static.add(i)
                static |= set(range(ncols, ncols + 6))
                if d0.clip is not None:
                    static |= set(range(ncols + 6, ncols + 10))
                h_tags, u_tags = self._tags(d0, static)
                if d0.vclip is not None:
                    h_tags += ("\\iclip(" if d0.iclip else "\\clip(") + d0.vclip + ")"
                if varying:
                    sub = [[vec[k][c] for c in varying] for k in range(len(vec))]
                    subtol = [tol[c] for c in varying]
                    breaks = _linear_breaks(sub, subtol, p0, p1)
                    for q0, q1 in zip(breaks, breaks[1:]):
                        changed = {varying[i] for i in range(len(varying)) if abs(sub[q1][i] - sub[q0][i]) > 1e-6}
                        if not changed:
                            continue
                        t0 = int(round((times[q0] - ev_t0) * 1000))
                        t1 = int(round((times[q1] - ev_t0) * 1000))
                        th, tu = self._tags(draws[q1], changed)
                        if th:
                            h_tags += f"\\t({t0},{t1},{th})"
                        if tu:
                            u_tags += f"\\t({t0},{t1},{tu})"
                if d0.drawing:
                    body = "{" + head + h_tags + u_tags + "}" + d0.text
                else:
                    body = "{" + head + h_tags + "}"
                    if d0.pre:
                        body += "{\\alpha&HFF&}" + d0.pre
                    body += "{" + u_tags + "}" + d0.text
                    if d0.post:
                        body += "{\\alpha&HFF&}" + d0.post
                events.append(f"Dialogue: {layer},{ts(ev_t0)},{ts(ev_t1)},{self.style_name},,0,0,0,,{body}")
        return events


def _debounce(flags: list[bool], min_run: int) -> list[bool]:
    """Drop True runs shorter than ``min_run`` frames (no flicker between the
    whole-line and per-letter drawing)."""
    out = list(flags)
    k = 0
    n = len(out)
    while k < n:
        if out[k]:
            j = k
            while j < n and out[j]:
                j += 1
            if j - k < min_run:
                for m in range(k, j):
                    out[m] = False
            k = j
        else:
            k += 1
    return out


def _linear_breaks(vals: list[list[float]], tol: list[float], lo: int, hi: int) -> list[int]:
    """Greedy breakpoints so linear interpolation stays within ``tol``.

    Identical consecutive frames are skipped in O(1), so long static holds do
    not cost quadratic time."""
    if hi <= lo:
        return [lo]
    out = [lo]
    i = lo
    while i < hi:
        j = i + 1
        while j < hi and vals[j + 1] == vals[j] == vals[i]:
            j += 1
        while j < hi and _fits(vals, tol, i, j + 1):
            j += 1
        out.append(j)
        i = j
    return out


def _fits(vals: list[list[float]], tol: list[float], i: int, j: int) -> bool:
    vi, vj = vals[i], vals[j]
    span = j - i
    for k in range(i + 1, j):
        f = (k - i) / span
        vk = vals[k]
        for c in range(len(vi)):
            if abs(vk[c] - (vi[c] + (vj[c] - vi[c]) * f)) > tol[c]:
                return False
    return True


def ts(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def document(width: int, height: int, styles: list[str], events: list[str]) -> str:
    return "\n".join([
        "[Script Info]",
        "; Stacked text effects - generated by backend/app/text_motion.py",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "ScaledBorderAndShadow: yes",
        "WrapStyle: 2",
        # HarfBuzz, the browser preview and the in-line layout all kern; libass
        # only does with this line (it defaults to VSFilter's no-kerning).
        "Kerning: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        *styles,
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        *events,
    ]) + "\n"


def compile_caption(cap: Caption, fonts_dir: Path | str | None) -> tuple[str, list[str]]:
    """ASS document + warnings for one caption."""
    comp = Compiler(cap, fonts_dir)
    events = comp.compile()
    return document(cap.frame_w, cap.frame_h, [comp.style_line()], events), comp.ctx.warnings


# ============================================================================
# Item -> Caption (the renderer's entry point)
# ============================================================================


def has_stack(item: dict[str, Any]) -> bool:
    return isinstance(item.get("textFx"), list)


def _motion_for(item: dict[str, Any], def_x: float, def_y: float) -> MotionPath | None:
    from .text_effects import _effective_motion_points, _parse_move_path
    fx = _clamp(_num(item.get("textMoveFromX"), def_x) or def_x, 0.0, 100.0)
    fy = _clamp(_num(item.get("textMoveFromY"), def_y) or def_y, 0.0, 100.0)
    tx = _clamp(_num(item.get("textMoveToX"), fx) or fx, 0.0, 100.0)
    ty = _clamp(_num(item.get("textMoveToY"), fy) or fy, 0.0, 100.0)
    path = _parse_move_path(item.get("textMovePath"))
    ptype = str(item.get("textMovePathType") or ("freehand" if path else "straight")).lower()
    pts = _effective_motion_points(
        fx, fy, tx, ty, path, ptype,
        _num(item.get("textMoveCircleRadius"), None), _clamp(_num(item.get("textMoveCircleTurns"), 1.0) or 1.0, 0.1, 4.0),
        _clamp(_num(item.get("textMoveSineAmplitude"), 8.0) or 0.0, 0.0, 40.0), _clamp(_num(item.get("textMoveSineFrequency"), 2.0) or 2.0, 0.1, 10.0),
        _clamp(_num(item.get("textMoveStarPoints"), 5.0) or 5.0, 3, 10), _clamp(_num(item.get("textMoveStarInnerRatio"), 0.45) or 0.45, 0.2, 0.85),
        _clamp(_num(item.get("textMoveSymbolRotation"), 0.0) or 0.0, 0, 360),
        bool(item.get("textMoveSinusUpDownEnabled")), _clamp(_num(item.get("textMoveSinusAmplitude"), 6.0) or 0.0, 0, 20),
        _clamp(_num(item.get("textMoveSinusFrequency"), 2.0) or 2.0, 0.1, 10),
        _clamp(_num(item.get("textMoveBounceHeight"), 14.0) or 0.0, 0, 30), _clamp(_num(item.get("textMoveBounceCount"), 4.0) or 4.0, 1, 8),
        _clamp(_num(item.get("textMoveBounceDamping"), 0.35) or 0.0, 0, 0.9),
    )
    easing = str(item.get("textMoveEasing") or "linear").lower()
    return MotionPath(points=[(float(x), float(y)) for x, y in pts], easing=easing if easing in PATH_EASE else "linear")


def caption_for_item(item: dict[str, Any], defaults: dict[str, Any], width: int, height: int, fps: float,
                     start: float, end: float, bg: BgChange | None = None) -> Caption | None:
    """The stacked-effect caption of a media item (``textFx`` present)."""
    text = str(item.get("text") or "")
    if not text.strip():
        return None
    if item.get("type") != "title" and item.get("textEnabled") is False:
        return None
    stack = parse_stack(item.get("textFx"))
    title = item.get("type") == "title"

    def pick(key: str, dkey: str, default: Any) -> Any:
        if key in item and item.get(key) is not None:
            return item.get(key)
        if title:
            return default
        return defaults.get(dkey, default)

    size_pt = _num(pick("fontSize", "fontSize", 48), 48.0) or 48.0
    colour = str(pick("fontColor", "fontColor", "#ffffff") or "#ffffff")
    colour = colour if HEX.fullmatch(colour) else "#ffffff"
    family = str(pick("fontFamily", "fontFamily", "Montserrat") or "Montserrat")
    bold = bool(pick("textBold", "bold", True))
    italic = bool(pick("textItalic", "italic", False))
    underline = bool(pick("textUnderline", "underline", False))
    if title:
        outline = item.get("textOutline") is True
    else:
        outline = (item["textOutline"] is not False) if "textOutline" in item else defaults.get("outline", True) is not False
    def_x = _clamp(_num(pick("textX", "textX", 50.0), 50.0) or 0.0, 0.0, 100.0)
    def_y = _clamp(_num(pick("textY", "textY", 50.0 if title else 72.0), 72.0) or 0.0, 0.0, 100.0)
    em = max(4.0, float(size_pt) * width / 1920.0)
    steady = _clamp(_num(item.get("textSteadySeconds"), 0.0) or 0.0, 0.0, 600.0)
    motion = None
    if any(layer.effect == "motion-path" and not layer.muted for layer in stack):
        motion = _motion_for(item, def_x, def_y)
    align = "left" if title and item.get("textCentered") is not True else "center"
    return Caption(
        text=text, x=width * def_x / 100.0, y=height * def_y / 100.0, em=em, stack=stack,
        start=start, end=end, family=family, bold=bold, italic=italic, underline=underline, colour=colour,
        outline=outline, align=align, frame_w=width, frame_h=height, fps=fps, steady=steady, bg=bg, motion=motion,
    )


def build_text_motion_overlay(item: dict[str, Any], defaults: dict[str, Any], width: int, height: int, fps: float,
                              start: float, end: float, fonts_dir: Path | str, ass_path: Path,
                              bg: BgChange | None = None) -> str | None:
    """Write the caption's .ass file and return the ``ass=`` filter, or None."""
    cap = caption_for_item(item, defaults, width, height, fps, start, end, bg)
    if cap is None:
        return None
    doc, warnings = compile_caption(cap, fonts_dir)
    for warning in warnings:
        log.info("text effects: %s", warning)
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(doc, encoding="utf-8")

    def posix(path: Path | str) -> str:
        return str(path).replace("\\", "/")
    return f"ass=filename={quote_filter_value(posix(ass_path))}:fontsdir={quote_filter_value(posix(fonts_dir))}"


def stack_fallback_fields(item: dict[str, Any]) -> dict[str, Any]:
    """v1 fields approximating a stack on FFmpeg builds without libass: a fade
    in/out as long as the stack's enter/exit lanes (or none if a lane is empty)."""
    stack = parse_stack(item.get("textFx"))
    spans = {"in": 0.0, "out": 0.0}
    for layer in stack:
        if layer.muted or layer.phase not in spans:
            continue
        spans[layer.phase] = max(spans[layer.phase], layer.delay + (layer.duration or _num(layer.fx.get("duration"), 0.5) or 0.5))
    return {
        "textFxEnter": "Fade" if spans["in"] > 0 else "None",
        "textEnterDuration": spans["in"] or 0.5,
        "textFxWhile": "None (static)",
        "textFxExit": "Fade out" if spans["out"] > 0 else "None (hold)",
        "textExitDuration": spans["out"] or 0.5,
        "textScaleEnabled": False, "textRotateEnabled": False, "textSquishEnabled": False,
        "textColorAnimEnabled": False, "textBouncyEnabled": False, "textMoveEnabled": False,
    }


# ============================================================================
# Legacy (v1) fields -> stack, mirrored by migrateLegacyTextFx() in src/textFx.ts
# ============================================================================

_V1_DEFAULTS = {"enter": "Fade", "while": "None (static)", "exit": "Fade out"}
_V1_NONE = {"enter": ("none",), "while": ("none (static)", "none"), "exit": ("none (hold)", "none")}


def legacy_to_stack(item: dict[str, Any], defaults: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The v1 fields (three single-effect slots + the animation toggles) as a
    textFx stack. Used for items saved before the stack existed."""
    defaults = defaults or {}
    title = item.get("type") == "title"
    enter = str(item.get("textFxEnter") or item.get("textEnter") or ("Fade" if title else defaults.get("fxEnter") or "Fade"))
    while_ = str(item.get("textFxWhile") or ("None (static)" if title else defaults.get("fxWhile") or "None (static)"))
    exit_ = str(item.get("textFxExit") or item.get("textExit") or ("Fade out" if title else defaults.get("fxExit") or "Fade out"))
    di = _clamp(_num(item.get("textEnterDuration"), 0.5) or 0.5, 0.05, 30.0)
    do = _clamp(_num(item.get("textExitDuration"), 0.5) or 0.5, 0.05, 30.0)
    speed_default = 2.0 if title else (_num(defaults.get("fxWhileSpeed"), 2.0) or 2.0)
    speed = _clamp(_num(item.get("textFxWhileSpeed"), speed_default) or speed_default, 0.4, 12.0)
    raw_params = item.get("textFxParams") if isinstance(item.get("textFxParams"), dict) else {}
    idx = legacy_index()
    stack: list[dict[str, Any]] = []

    def add(slot: str, label: str, extra: dict[str, Any]) -> None:
        key = label.strip().lower()
        if key in _V1_NONE[slot]:
            return
        eid = idx.get((slot, key)) or idx.get((slot, _V1_DEFAULTS[slot].lower()))
        if eid:
            stack.append({"effect": eid, **extra})

    add("enter", enter, {"duration": di})
    fx_while = idx.get(("while", while_.strip().lower()))
    if fx_while and while_.strip().lower() not in _V1_NONE["while"]:
        entry: dict[str, Any] = {"effect": fx_while}
        e = effect(fx_while) or {}
        if e.get("loop", "loop") != "once":
            entry["duration"] = speed
        names = {p.get("name") for p in e.get("params") or []}
        params = {k: _num(v, v) if k not in ("from", "to") or fx_while != "colour-morph" else v for k, v in raw_params.items() if k in names}
        if params:
            entry["params"] = params
        stack.append(entry)
    # the former toggles
    if item.get("textScaleEnabled"):
        stack.append({"effect": "resize", "params": {"from": _num(item.get("textScaleFrom"), 1.0), "to": _num(item.get("textScaleTo"), 1.45)}})
    if item.get("textRotateEnabled"):
        entry = {"effect": "rotate", "params": {"from": _num(item.get("textRotateFrom"), -10.0), "to": _num(item.get("textRotateTo"), 0.0)}}
        speed_r = _num(item.get("textRotateSpeed"), 0.0) or 0.0
        if speed_r > 0:
            entry["duration"] = speed_r
        stack.append(entry)
    if item.get("textSquishEnabled"):
        stack.append({"effect": "squash", "params": {"from": _num(item.get("textSquishFrom"), 0.5), "to": _num(item.get("textSquishTo"), 1.0)}})
    if item.get("textColorAnimEnabled"):
        c_from = str(item.get("textColorFrom") or item.get("fontColor") or "#ffffff")
        c_to = str(item.get("textColorTo") or c_from)
        if c_to.lower() == "#ffcc33" and c_from.lower() != "#ffcc33":
            c_to = c_from
        if not any(layer["effect"] == "colour-morph" for layer in stack):
            stack.append({"effect": "colour-morph", "params": {"from": c_from, "to": c_to}})
    if item.get("textBouncyEnabled") and not any(layer["effect"] == "bouncy" for layer in stack):
        stack.append({"effect": "bouncy", "params": {"height": _num(item.get("textBouncyHeight"), 12.0),
                                                     "bounces": _num(item.get("textBouncyBounces"), 3.0),
                                                     "damping": _num(item.get("textBouncyDamping"), 0.35)}})
    if item.get("textMoveEnabled"):
        stack.append({"effect": "motion-path"})
    add("exit", exit_, {"duration": do})
    return stack
