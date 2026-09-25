#!/usr/bin/env python3
"""Prototype: *stackable* text effects compiled to libass (ASS) events.

Documentation artifact for docs/text-motion-references.md, not app code. It
proves the proposed architecture end to end with the same engine the app
renders with (libass inside FFmpeg), so every frame in the demo MP4 is real.

Model
-----
A caption carries an ordered **stack of layers**. A layer is one effect from
``text-motion-effects.json`` plus optional overrides::

    Layer("pop-in", unit="char", stagger=0.04)   # enter, one letter at a time
    Layer("wave")                                # while shown
    Layer("pop-out", unit="char", order="reverse")

Every effect is declarative: keyframe tracks per *channel* over its local time
u in [0, 1] (see the ``$comment`` block in the JSON for the channel list).

Why stacking works: composition per channel
-------------------------------------------
At time t every layer contributes a value per channel for every text unit, and
contributions are *combined* instead of overwriting each other:

    opacity, scale, sx, sy, fill  multiply     x, y, blur, spacing, line  add
    rz, rx, ry, skew              add          glow, depth, rgb, orbit    max
    colour   folded bottom -> top ("base" = colour below this layer)
    clip     intersected (only while the effect runs)
    content  exclusive: one text-rewriting effect wins, the rest is REPORTED

Units nest (letter < word < line < text): a coarser layer transforms its finer
children around its own centre, so "Pop in · words" + "Wave · letters" really
compose instead of one of them silently disappearing.

Compilation to ASS
------------------
libass cannot compose by itself: only the first ``\\fad``/``\\pos``/``\\move`` of
an event counts and later absolute tags overwrite earlier ones. So the compiler
never emits "one tag per effect". Instead it samples the *composed* value of
every channel at every video frame and writes the result as piecewise-linear
``\\t(...)`` chains (simplified within a tolerance). Events are only split
where libass needs it: moving units (``\\move`` is linear) and changing glyphs
(scramble, counters). Any easing (overshoot, elastic, bounce) survives,
because the curve itself is sampled.

Per-unit layout uses HarfBuzz (``uharfbuzz``), the shaper libass uses
internally, so letters land where libass would put them in a whole line.

    pip install uharfbuzz fonttools
"""
from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FONT_DIR = REPO / "public" / "fonts"
EFFECTS_PATH = HERE / "text-motion-effects.json"

FPS = 25
FRAME = 1.0 / FPS

# Family name used in the ASS style -> file measured by HarfBuzz. libass picks
# the same file from fontsdir by family + bold flag.
FONT_FILES = {
    ("Montserrat", True): "Montserrat-Bold.ttf",
    ("Montserrat", False): "Montserrat-Regular.ttf",
    ("Anton", False): "Anton-Regular.ttf",
    ("Bebas Neue", False): "BebasNeue-Regular.ttf",
    ("Oswald", True): "Oswald-Bold.ttf",
    ("Playfair Display", True): "PlayfairDisplay-Bold.ttf",
    ("Caveat", True): "Caveat-Bold.ttf",
}

LEVELS = ("char", "word", "line", "text")          # finest -> coarsest
LEVEL_RANK = {name: i for i, name in enumerate(LEVELS)}

MUL = {"opacity", "scale", "sx", "sy", "fill"}
MAXC = {"glow", "depth", "rgb", "orbit"}
ADD = {"x", "y", "px", "py", "rz", "rx", "ry", "skew", "blur", "spacing", "line"}
NEUTRAL: dict[str, float] = {**{c: 1.0 for c in MUL}, **{c: 0.0 for c in MAXC | ADD}}


# --------------------------------------------------------------------------
# Easing (the same names are used by the JavaScript twin in the mockup)
# --------------------------------------------------------------------------
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


EASE = {
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


def _hash01(*values: int) -> float:
    """Deterministic pseudo-random number in [0, 1): renders are reproducible."""
    h = 2166136261
    for v in values:
        h ^= int(v) & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0x5BD1E995) & 0xFFFFFFFF
    h ^= h >> 15
    return h / 4294967296.0


def _rgb(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _mix(a, b, f: float):
    if isinstance(a, tuple):
        return tuple(x + (y - x) * f for x, y in zip(a, b))
    if isinstance(a, list):
        return [x + (y - x) * f for x, y in zip(a, b)]
    return a + (b - a) * f


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
@lru_cache(maxsize=1)
def effects() -> dict[str, dict[str, Any]]:
    data = json.loads(EFFECTS_PATH.read_text(encoding="utf-8"))
    return {e["id"]: e for e in data["effects"]}


@dataclass
class Layer:
    """One entry of a caption's effect stack (what the GUI's lane rows edit)."""
    effect: str
    unit: str | None = None        # override the effect's default unit
    duration: float | None = None  # per unit (in/out) or loop period (hold)
    delay: float = 0.0             # offset inside the phase
    stagger: float | None = None
    order: str | None = None
    loop: str | None = None
    intensity: float = 1.0         # scales every deviation from neutral
    params: dict[str, Any] = field(default_factory=dict)
    muted: bool = False

    @property
    def fx(self) -> dict[str, Any]:
        return effects()[self.effect]

    @property
    def phase(self) -> str:
        return self.fx["phase"]

    @property
    def unit_(self) -> str:
        return self.unit or self.fx.get("unit", "text")

    def param(self, name: str, default: Any = None) -> Any:
        return self.params.get(name, self.fx.get("params", {}).get(name, default))


# --------------------------------------------------------------------------
# Layout: HarfBuzz advances -> rest boxes for letters, words, lines, the text
# --------------------------------------------------------------------------
class _Font:
    def __init__(self, path: Path):
        import uharfbuzz as hb
        from fontTools.ttLib import TTFont
        self.hb = hb
        self.font = hb.Font(hb.Face(hb.Blob.from_file_path(str(path))))
        tt = TTFont(str(path))
        os2 = tt["OS/2"]
        # libass sizes fonts like VSFilter: the ASS font size maps to
        # usWinAscent + usWinDescent (OS/2 table), not to the em square.
        self.win = os2.usWinAscent + os2.usWinDescent

    def advances(self, text: str, size: float) -> list[float]:
        """Per-character advance in px, kerning included (cluster mapped).

        Matches libass only with ``Kerning: yes`` in [Script Info] (see
        Doc.text): libass leaves kerning OFF by default for VSFilter
        compatibility. Verified to 0-1 px per letter against whole-line
        libass renders (Montserrat, Bebas Neue, Caveat; kerning-heavy
        "AVATAR Toy" included).
        """
        buf = self.hb.Buffer()
        buf.add_str(text)
        buf.guess_segment_properties()
        self.hb.shape(self.font, buf, {"kern": True, "liga": True})
        scale = size / self.win
        adv = [0.0] * len(text)
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            adv[info.cluster] += pos.x_advance * scale
        return adv

    def line_height(self, size: float) -> float:
        # libass advances multi-line text by exactly the font size (the
        # win-metric height above), measured: 0 px error on two-line text.
        return size


@lru_cache(maxsize=16)
def _font(family: str, bold: bool) -> _Font:
    return _Font(FONT_DIR / FONT_FILES[(family, bold)])


@dataclass
class Unit:
    level: str
    index: int                     # reading order within its level
    text: str
    cx: float                      # rest centre, px
    cy: float
    w: float
    h: float
    anc: dict[str, "Unit"] = field(default_factory=dict)   # level -> ancestor (self included)
    col: int = 0                   # glyph index inside its line (for letter spacing)
    ncol: int = 1

    @property
    def box(self) -> tuple[float, float, float, float]:
        return (self.cx - self.w / 2, self.cy - self.h / 2, self.cx + self.w / 2, self.cy + self.h / 2)


def layout(text: str, family: str, bold: bool, size: float, cx: float, cy: float) -> dict[str, list[Unit]]:
    font = _font(family, bold)
    lines = text.split("\n")
    lh = font.line_height(size)
    top = cy - lh * len(lines) / 2
    out: dict[str, list[Unit]] = {lvl: [] for lvl in LEVELS}
    widths = []
    for li, line in enumerate(lines):
        adv = font.advances(line, size)
        widths.append(sum(adv))
    whole = Unit("text", 0, text, cx, cy, max(widths or [0]), lh * len(lines))
    whole.anc = {"text": whole}
    out["text"].append(whole)
    for li, line in enumerate(lines):
        adv = font.advances(line, size)
        width = sum(adv)
        left = cx - width / 2
        lcy = top + (li + 0.5) * lh
        lu = Unit("line", li, line, cx, lcy, width, lh)
        lu.anc = {"line": lu, "text": whole}
        out["line"].append(lu)
        pen = 0.0
        word: list[tuple[int, float]] = []          # (char index in line, pen)

        def close_word():
            if not word:
                return
            i0, p0 = word[0]
            i1, p1 = word[-1]
            x0, x1 = left + p0, left + p1 + adv[i1]
            wu = Unit("word", len(out["word"]), line[i0:i1 + 1], (x0 + x1) / 2, lcy, x1 - x0, lh)
            wu.anc = {"word": wu, "line": lu, "text": whole}
            out["word"].append(wu)
            for ci, _ in word:
                out["char"][char_ids[ci]].anc.update(word=wu, line=lu, text=whole)
            word.clear()

        char_ids: dict[int, int] = {}
        for i, ch in enumerate(line):
            if ch == " ":
                close_word()
            else:
                cu = Unit("char", len(out["char"]), ch, left + pen + adv[i] / 2, lcy, adv[i], lh,
                          col=i, ncol=len(line))
                cu.anc = {"char": cu}
                char_ids[i] = cu.index
                out["char"].append(cu)
                word.append((i, pen))
            pen += adv[i]
        close_word()
    return out


# --------------------------------------------------------------------------
# Timing: which local time u does a layer have for one unit at time t?
# --------------------------------------------------------------------------
def _ranks(n: int, order: str, seed: int) -> list[float]:
    """rank[i] = how many stagger steps unit i waits (floats for centre/edges)."""
    idx = list(range(n))
    if order == "reverse":
        return [float(n - 1 - i) for i in idx]
    if order in ("center", "edges"):
        c = (n - 1) / 2
        dist = [abs(i - c) for i in idx]
        return dist if order == "center" else [max(dist) - d for d in dist]
    if order == "random":
        perm = sorted(idx, key=lambda i: _hash01(seed, i))
        return [float(perm.index(i)) for i in idx]
    return [float(i) for i in idx]


@dataclass
class _Timing:
    start: float     # text window
    end: float


def _local_u(layer: Layer, rank: float, max_rank: float, t: float, win: _Timing) -> tuple[float, float]:
    """(u, seconds since this unit's local start) for a layer."""
    fx = layer.fx
    dur = layer.duration if layer.duration is not None else float(fx.get("duration", 1.0))
    stagger = layer.stagger if layer.stagger is not None else float(fx.get("stagger", 0.0))
    if layer.phase == "in":
        t0 = win.start + layer.delay + stagger * rank
        return (min(1.0, max(0.0, (t - t0) / max(dur, 1e-6))), t - t0)
    if layer.phase == "out":
        t_end = win.end - layer.delay - stagger * (max_rank - rank)
        t0 = t_end - dur
        return (min(1.0, max(0.0, (t - t0) / max(dur, 1e-6))), t - t0)
    # hold: loops run for the whole window (also during enter/exit, like Jitter)
    loop = layer.loop or fx.get("loop", "loop")
    t0 = win.start + layer.delay + stagger * rank
    if loop == "once":
        span = max(1e-6, win.end - win.start - layer.delay)
        return (min(1.0, max(0.0, (t - win.start - layer.delay) / span)), t - t0)
    w = (t - t0) / max(dur, 1e-6)
    if loop == "pingpong":
        w = (w / 2) % 1 * 2
        return (w if w <= 1 else 2 - w, t - t0)
    return (w % 1.0, t - t0)


# --------------------------------------------------------------------------
# Track evaluation
# --------------------------------------------------------------------------
def _resolve(value: Any, base_colour, unit_seed: int, layer_seed: int, key: int):
    if isinstance(value, dict) and "rand" in value:
        a, b = value["rand"]
        return a + (b - a) * _hash01(unit_seed, layer_seed, key, 99)
    if isinstance(value, str):
        return base_colour if value == "base" else _rgb(value)
    return value


def _keys(keys: list, u: float, base_colour, unit_seed: int, layer_seed: int):
    first = keys[0]
    if u <= first[0]:
        return _resolve(first[1], base_colour, unit_seed, layer_seed, 0)
    for k in range(1, len(keys)):
        u0, v0 = keys[k - 1][0], keys[k - 1][1]
        u1, v1 = keys[k][0], keys[k][1]
        if u <= u1:
            f = 0.0 if u1 <= u0 else (u - u0) / (u1 - u0)
            if len(keys[k]) > 2:
                f = EASE[keys[k][2]](f)
            a = _resolve(v0, base_colour, unit_seed, layer_seed, k - 1)
            b = _resolve(v1, base_colour, unit_seed, layer_seed, k)
            return _mix(a, b, f)
    return _resolve(keys[-1][1], base_colour, unit_seed, layer_seed, len(keys) - 1)


def _generator(spec: dict, u: float, tl: float, phase: str, unit_seed: int, layer_seed: int) -> float:
    if "sine" in spec:
        g = spec["sine"]
        v = g.get("base", 0.0) + g["amp"] * math.sin(2 * math.pi * (u + g.get("phase", 0.0)))
    elif "noise" in spec:
        g = spec["noise"]
        step = math.floor(tl * g["rate"])
        v = spec.get("base", 0.0) + g["amp"] * (2 * _hash01(step, g.get("seed", 1), unit_seed, layer_seed) - 1)
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
        on = g["on"]
        if ramp == "up":
            on = on + (1 - on) * u * u
        elif ramp == "down":
            on = on * (1 - u) ** 1.5
        step = math.floor(tl * g["rate"])
        v = 1.0 if _hash01(step, unit_seed, layer_seed, 5) < on else g.get("low", 0.0)
    else:
        raise ValueError(f"unknown generator {spec}")
    if "env" in spec:
        v *= _keys(spec["env"], u, None, unit_seed, layer_seed)
    return v


def _path_point(points: list[list[float]], f: float) -> tuple[float, float]:
    """Point at arc-length fraction f along a polyline (points in % of frame)."""
    seg = [math.dist(points[i], points[i + 1]) for i in range(len(points) - 1)]
    total = sum(seg) or 1.0
    target = max(0.0, min(1.0, f)) * total
    for i, s in enumerate(seg):
        if target <= s or i == len(seg) - 1:
            g = 0.0 if s <= 0 else min(1.0, target / s)
            (x0, y0), (x1, y1) = points[i], points[i + 1]
            return (x0 + (x1 - x0) * g, y0 + (y1 - y0) * g)
        target -= s
    return tuple(points[-1])  # pragma: no cover


# --------------------------------------------------------------------------
# Caption compilation
# --------------------------------------------------------------------------
@dataclass
class Caption:
    text: str
    x: float                      # centre, px
    y: float
    size: float
    stack: list[Layer]
    start: float = 0.0            # text window, seconds (multiples of 1/FPS)
    end: float = 4.0
    family: str = "Montserrat"
    bold: bool = True
    colour: str = "#ffffff"
    layer: int = 5                # ASS layer of the main text; copies go below
    frame_w: int = 1280
    frame_h: int = 720


@dataclass
class Compiled:
    events: list[str]
    warnings: list[str]
    style: str


class Doc:
    """Collects styles and events for one ASS file."""

    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.styles: dict[tuple, str] = {}       # (family, bold, size, colour) -> style name
        self.style_lines: list[str] = []
        self.events: list[str] = []
        self.warnings: list[str] = []

    def style(self, family: str, bold: bool, size: float, colour: str) -> str:
        key = (family, bold, round(size, 2), colour)
        if key not in self.styles:
            name = f"S{len(self.styles)}"
            self.styles[key] = name
            r, g, b = _rgb(colour)
            # No outline / shadow in the style: outlines, glows and shadows are
            # channels or copies, so they can be animated and stacked.
            self.style_lines.append(
                f"Style: {name},{family},{size:.2f},&H00{b:02X}{g:02X}{r:02X},&H000000FF,&H00000000,&H00000000,"
                f"{-1 if bold else 0},0,0,0,100,100,0,0,1,0,0,5,0,0,0,1")
        return self.styles[key]

    def raw_style(self, line: str) -> None:
        self.style_lines.append(line)

    def add(self, caption: Caption) -> Compiled:
        compiled = compile_caption(caption, self)
        self.events.extend(compiled.events)
        self.warnings.extend(compiled.warnings)
        return compiled

    def text(self) -> str:
        return "\n".join([
            "[Script Info]",
            "; Stackable text motion prototype - generated by docs/demo/text_motion_stack.py",
            "ScriptType: v4.00+",
            f"PlayResX: {self.w}",
            f"PlayResY: {self.h}",
            "ScaledBorderAndShadow: yes",
            "WrapStyle: 2",
            # Kerning on: HarfBuzz layout (and the browser preview) kern, so
            # per-letter events line up with whole-line text only with this.
            "Kerning: yes",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
            "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            *self.style_lines,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *self.events,
        ]) + "\n"


def ts(seconds: float) -> str:
    cs = int(round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def compile_caption(cap: Caption, doc: Doc) -> Compiled:
    stack = [dataclasses.replace(layer) for layer in cap.stack if not layer.muted]
    warnings: list[str] = []
    units = layout(cap.text, cap.family, cap.bold, cap.size, cap.x, cap.y)
    style = doc.style(cap.family, cap.bold, cap.size, cap.colour)
    base_rgb = _rgb(cap.colour)
    win = _Timing(cap.start, cap.end)

    # ---- content is the only exclusive channel: keep the top-most per phase
    content_layers: dict[str, Layer] = {}
    for layer in stack:
        if "content" in layer.fx:
            prev = content_layers.get(layer.phase)
            if prev is not None:
                warnings.append(f"'{layer.fx['label']}' replaces '{prev.fx['label']}' "
                                f"(both rewrite the text in the {layer.phase} phase)")
            content_layers[layer.phase] = layer
    whole_text_content = [l for l in content_layers.values() if l.fx["content"]["type"] in ("count", "countdown")]

    # ---- event granularity = finest unit any layer (or content) needs
    need = [layer.unit_ for layer in stack]
    need += ["char" for l in content_layers.values() if l.fx["content"]["type"] in ("scramble", "flap")]
    if any("caret" in l.fx for l in stack):
        need.append("char")
    if whole_text_content:
        finer = [l for l in stack if LEVEL_RANK[l.unit_] < LEVEL_RANK["text"]]
        for l in finer:
            warnings.append(f"'{whole_text_content[0].fx['label']}' rewrites the whole text, so "
                            f"'{l.fx['label']}' runs on the whole text instead of per {l.unit_}")
            l.unit = "text"
        need = ["text"]
    gran = min(need or ["text"], key=lambda lvl: LEVEL_RANK[lvl])

    ev_units = units[gran]
    # Stagger ranks per layer (computed on the layer's own unit level)
    rank_cache: dict[int, tuple[list[float], float]] = {}
    for li, layer in enumerate(stack):
        n = len(units[layer.unit_])
        order = layer.order or layer.fx.get("order", "forward")
        r = _ranks(n, order, seed=li * 131 + 7)
        rank_cache[li] = (r, max(r) if r else 0.0)

    em = cap.size
    frames = int(round((cap.end - cap.start) * FPS))
    times = [cap.start + k * FRAME for k in range(frames + 1)]

    # 3D rotation origin: a coarser layer with rx/ry flips its children around its own centre
    org_level = None
    for layer in stack:
        if any(ch in layer.fx.get("tracks", {}) for ch in ("rx", "ry")):
            if LEVEL_RANK[layer.unit_] > LEVEL_RANK[gran]:
                if org_level is None or LEVEL_RANK[layer.unit_] > LEVEL_RANK[org_level]:
                    org_level = layer.unit_

    def evaluate(e: Unit, t: float) -> dict[str, Any]:
        """Composed state of event unit e at time t (all layers, all levels)."""
        acc = {lvl: dict(NEUTRAL) for lvl in LEVELS[LEVEL_RANK[gran]:]}
        colour = base_rgb
        clip = None
        text = e.text
        mods: dict[str, float] = {}
        for li, layer in enumerate(stack):
            lvl = layer.unit_
            anc = e.anc[lvl]
            ranks, max_rank = rank_cache[li]
            u, tl = _local_u(layer, ranks[anc.index], max_rank, t, win)
            useed = anc.index * 7919 + LEVEL_RANK[lvl]
            lseed = li * 104729 + 13
            fx = layer.fx
            for ch, spec in fx.get("tracks", {}).items():
                if ch == "path":
                    pts = layer.param("points")
                    f = u
                    px, py = _path_point(pts, f)
                    acc[lvl]["px"] += px / 100 * cap.frame_w - anc.cx
                    acc[lvl]["py"] += py / 100 * cap.frame_h - anc.cy
                    continue
                if ch == "clip":
                    running = (layer.phase == "in" and u < 1) or (layer.phase == "out" and u > 0) \
                        or layer.phase == "hold"
                    if not running:
                        continue
                    l, tp, r, b = _keys(spec, u, None, useed, lseed)
                    x0, y0, x1, y1 = anc.box
                    rect = (x0 + (x1 - x0) * l, y0 + (y1 - y0) * tp, x0 + (x1 - x0) * r, y0 + (y1 - y0) * b)
                    clip = rect if clip is None else (max(clip[0], rect[0]), max(clip[1], rect[1]),
                                                      min(clip[2], rect[2]), min(clip[3], rect[3]))
                    continue
                if ch == "colour":
                    colour = _keys(spec, u, colour, useed, lseed)
                    continue
                v = _generator(spec, u, tl, layer.phase, useed, lseed) if isinstance(spec, dict) \
                    else _keys(spec, u, None, useed, lseed)
                if layer.intensity != 1.0:
                    v = NEUTRAL[ch] + (v - NEUTRAL[ch]) * layer.intensity
                if ch in MUL:
                    acc[lvl][ch] *= v
                elif ch in MAXC:
                    acc[lvl][ch] = max(acc[lvl][ch], v)
                else:
                    acc[lvl][ch] += v
            # content (exclusive): only the winning layer of its phase writes text
            if "content" in fx and content_layers.get(layer.phase) is layer:
                text, extra = _content(layer, e, u, tl, t, win, useed)
                for k, v in extra.items():
                    mods[k] = mods.get(k, 1.0) * v

        # ---- combine levels: glyph gets every level's scale/rotation, the
        # position follows the hierarchy (coarse levels move fine children)
        tot = dict(NEUTRAL)
        for lvl, a in acc.items():
            for ch, v in a.items():
                if ch in MUL:
                    tot[ch] *= v
                elif ch in MAXC:
                    tot[ch] = max(tot[ch], v)
                else:
                    tot[ch] += v
        for k, v in mods.items():
            tot[k] = tot.get(k, 1.0) * v
        x, y = e.cx, e.cy
        if gran == "char" and tot["spacing"]:
            x += tot["spacing"] * em * (e.col - (e.ncol - 1) / 2)
        for lvl, a in acc.items():
            anc = e.anc[lvl]
            if lvl != gran:
                dx, dy = x - anc.cx, y - anc.cy
                dx *= a["scale"] * a["sx"]
                dy *= a["scale"] * a["sy"]
                th = math.radians(a["rz"])       # ASS \frz is counter-clockwise
                dx, dy = dx * math.cos(th) + dy * math.sin(th), -dx * math.sin(th) + dy * math.cos(th)
                x, y = anc.cx + dx, anc.cy + dy
            x += a["x"] * em + a["px"]
            y += a["y"] * em + a["py"]
        org = None
        if org_level is not None:
            anc = e.anc[org_level]
            a = acc[org_level]
            org = (anc.cx + a["x"] * em + a["px"], anc.cy + a["y"] * em + a["py"])
        return {
            "x": x, "y": y, "text": text, "tot": tot, "colour": colour, "clip": clip, "org": org,
        }

    def ambient_opacity(t: float) -> float:
        """Opacity of the text-wide layers only (a caret fades with a Fade out)."""
        op = 1.0
        for li, layer in enumerate(stack):
            spec = layer.fx.get("tracks", {}).get("opacity")
            if spec is None or "caret" in layer.fx or layer.unit_ != "text":
                continue
            ranks, max_rank = rank_cache[li]
            u, tl = _local_u(layer, ranks[0], max_rank, t, win)
            lseed = li * 104729 + 13
            op *= _generator(spec, u, tl, layer.phase, LEVEL_RANK["text"], lseed) if isinstance(spec, dict) \
                else _keys(spec, u, None, LEVEL_RANK["text"], lseed)
        return max(0.0, min(1.0, op))

    events: list[str] = []
    copies = [(layer, spec) for layer in stack for spec in layer.fx.get("copies", [])]
    for e in ev_units:
        states = [evaluate(e, t) for t in times]
        main = [_ass_state(s, em, gran) for s in states]
        # copies first, one ASS layer below the text (far copies are written first)
        for layer, spec in copies:
            for cstates in _copy_states(spec, layer, states, e, evaluate, times, em, cap, gran):
                events += _emit(cstates, times, cap.layer - 1, style)
        events += _emit(main, times, cap.layer, style)
    # caret (typewriter / delete) follows the last visible letter
    if any("caret" in l.fx for l in stack) and gran == "char":
        events += _caret_events(ev_units, evaluate, ambient_opacity, times, em, cap, style)
    return Compiled(events, warnings, style)


# --------------------------------------------------------------------------
# Content generators (exclusive channel)
# --------------------------------------------------------------------------
def _content(layer: Layer, e: Unit, u: float, tl: float, t: float, win: _Timing, useed: int):
    c = layer.fx["content"]
    kind = c["type"]
    if kind == "scramble":
        if 0 < u < 1:
            cs = c["charset"]
            step = math.floor(tl * c.get("rate", 18))
            return cs[int(_hash01(step, useed, 3) * len(cs))], {}
        return e.text, {}
    if kind == "flap":
        if 0 < u < 1:
            flips = c.get("flips", 8)
            pos = u * flips
            n, p = int(pos), pos - int(pos)
            cs = c["charset"]
            ch = cs[int(_hash01(n, useed, 11) * len(cs))]
            return ch, {"sy": max(0.12, abs(math.cos(math.pi * p)) ** 0.7)}
        return e.text, {}
    if kind == "count":
        f = EASE.get(c.get("easing", "linear"))(u)
        a, b = float(layer.param("from", 0)), float(layer.param("to", 100))
        n = int(round(a + (b - a) * f))
        sep = layer.param("separator", "")
        s = f"{n:,}".replace(",", sep) if sep else str(n)
        return f"{layer.param('prefix', '')}{s}{layer.param('suffix', '')}", {}
    if kind == "countdown":
        start = int(layer.param("from", 3))
        span = (win.end - win.start) / (start + 1)
        k = min(start, int((t - win.start) / span))
        p = ((t - win.start) - k * span) / span
        label = str(start - k) if start - k > 0 else "GO!"
        pop = 1 + 0.45 * (1 - min(1.0, p / 0.35)) ** 3
        return label, {"scale": pop, "opacity": 1.0 if p < 0.82 else max(0.0, 1 - (p - 0.82) / 0.18)}
    raise ValueError(kind)


# --------------------------------------------------------------------------
# State -> ASS numbers, copies, emission
# --------------------------------------------------------------------------
def _ass_state(s: dict, em: float, gran: str) -> dict:
    tot = s["tot"]
    op = max(0.0, min(1.0, tot["opacity"]))
    fill = max(0.0, min(1.0, tot["fill"]))
    line_px = max(0.0, tot["line"] * em)
    st = {
        "x": s["x"], "y": s["y"], "text": s["text"], "org": s["org"],
        "alpha": 255 * (1 - op),
        "a1": 255 * (1 - op * fill),
        "fscx": 100 * tot["scale"] * tot["sx"],
        "fscy": 100 * tot["scale"] * tot["sy"],
        "frz": tot["rz"], "frx": tot["rx"], "fry": tot["ry"], "fax": tot["skew"],
        "blur": max(0.0, tot["blur"] * em),
        "bord": line_px,
        "fsp": tot["spacing"] * em if gran != "char" else 0.0,
        "c1": s["colour"],
        "c3": s["colour"],            # outline drawn in the fill colour (outline→fill, breathe)
        "clip": s["clip"],
        "vis": op > 0.004 and (fill > 0.004 or line_px > 0.05),
    }
    return st


def _copy_states(spec: dict, layer: Layer, states, e: Unit, evaluate, times, em, cap, gran: str):
    """Yield one list of per-frame ASS states per drawn copy."""
    kind = spec["type"]

    def colour(v):
        if isinstance(v, str) and v.startswith("$"):
            v = layer.param(v[1:])
        return _rgb(v)

    if kind == "glow":
        rgb = colour(spec["colour"])
        out = []
        for s in states:
            st = _ass_state(s, em, gran)
            g = s["tot"]["glow"]
            st.update(a1=255.0, bord=spec["bord"] * em * g, blur=spec["blur"] * em * g, c3=rgb,
                      vis=st["alpha"] < 254 and g > 0.01)
            out.append(st)
        yield out
    elif kind == "trail":
        rgb = colour(spec["colour"])
        n = spec["count"]
        for k in range(n, 0, -1):
            out = []
            for t in times:
                s = evaluate(e, t - k * spec["delay"])
                st = _ass_state(s, em, gran)
                a = spec["alpha"] * (1 - (k - 1) / n)
                op = (1 - st["alpha"] / 255) * a
                st.update(alpha=255 * (1 - op), a1=255 * (1 - op), c1=rgb, vis=op > 0.004)
                out.append(st)
            yield out
    elif kind == "extrude":
        base = colour(spec["colour"])
        n = spec["count"]
        for k in range(n, 0, -1):
            shade = 1 - spec.get("shade", 0.5) * k / n
            rgb = tuple(c * shade for c in base)
            out = []
            for s in states:
                st = _ass_state(s, em, gran)
                d = s["tot"]["depth"]
                st.update(x=st["x"] + k * spec["dx"] * em * d, y=st["y"] + k * spec["dy"] * em * d,
                          c1=rgb, vis=st["vis"] and d > 0.01)
                out.append(st)
            yield out
    elif kind == "mirror":
        axis = cap.y + _font(cap.family, cap.bold).line_height(cap.size) * (cap.text.count("\n") + 1) / 2 \
            + spec["gap"] * em
        out = []
        for s in states:
            st = _ass_state(s, em, gran)
            op = (1 - st["alpha"] / 255) * spec["alpha"]
            st.update(y=2 * axis - st["y"], frx=st["frx"] + 180, alpha=255 * (1 - op), a1=255 * (1 - op), org=None,
                      blur=st["blur"] + spec["blur"] * em, fscy=st["fscy"] * 0.9, vis=op > 0.004)
            out.append(st)
        yield out
    elif kind == "rgb":
        for sign, hexc in zip((-1, 1), spec["colours"]):
            rgb = _rgb(hexc)
            out = []
            for s in states:
                st = _ass_state(s, em, gran)
                d = s["tot"]["rgb"]
                op = (1 - st["alpha"] / 255) * 0.85
                st.update(x=st["x"] + sign * spec["dx"] * em * d, c1=rgb, alpha=255 * (1 - op),
                          a1=255 * (1 - op), vis=op > 0.004 and d > 0.01)
                out.append(st)
            yield out
    elif kind == "shadow":
        cols = spec["colours"]
        for i, hexc in enumerate(cols):
            rgb = _rgb(hexc)
            out = []
            for s in states:
                st = _ass_state(s, em, gran)
                ang = 2 * math.pi * (s["tot"]["orbit"] + i / len(cols))
                r = spec["radius"] * em
                st.update(x=st["x"] + r * math.cos(ang), y=st["y"] + r * math.sin(ang), c1=rgb)
                out.append(st)
            yield out
    elif kind == "box":
        pw, ph = spec["pad"]
        for part, hexc, hfrac in (("tile", spec["colour"], 0.86), ("split", spec["split"], 0.035)):
            rgb = _rgb(hexc)
            out = []
            w = e.w + 2 * pw * em
            h = e.h * hfrac
            for s in states:
                st = _ass_state(s, em, gran)
                st.update(text=f"{{\\p1}}m 0 0 l {w:.1f} 0 {w:.1f} {h:.1f} 0 {h:.1f}{{\\p0}}", c1=rgb,
                          fscx=100.0, fscy=100.0, frx=0.0, blur=0.0, fsp=0.0, drawing=True)
                out.append(st)
            yield out
    else:
        raise ValueError(kind)


# numeric channels written into \t chains: (key, tolerance, formatter)
def _alpha_hex(v: float) -> str:
    return f"{int(round(max(0.0, min(255.0, v)))):02X}"


_CHANNELS: list[tuple[str, float]] = [
    ("alpha", 2.0), ("a1", 2.0), ("fscx", 0.4), ("fscy", 0.4), ("frz", 0.3), ("frx", 0.5),
    ("fry", 0.5), ("fax", 0.004), ("blur", 0.15), ("bord", 0.1), ("fsp", 0.3),
]


def _vector(st: dict) -> list[float]:
    v = [st[k] for k, _ in _CHANNELS]
    v += list(st["c1"]) + list(st["c3"])
    v += list(st["clip"]) if st["clip"] is not None else [0.0, 0.0, 0.0, 0.0]
    return v


_TOL = [tol for _, tol in _CHANNELS] + [3.0] * 6 + [0.6] * 4


def _tags(st: dict, which: set[int], static: bool) -> str:
    """ASS override tags for the channels in `which` (indices into _vector)."""
    out = []
    n = len(_CHANNELS)
    # alpha group: \alpha resets \1a, so the fill alpha is always re-emitted after it
    if 0 in which or 1 in which:
        out.append(f"\\alpha&H{_alpha_hex(st['alpha'])}&")
        if abs(st["a1"] - st["alpha"]) > 0.5 or not static:
            out.append(f"\\1a&H{_alpha_hex(st['a1'])}&")
    fmt = {
        "fscx": lambda v: f"\\fscx{v:.1f}", "fscy": lambda v: f"\\fscy{v:.1f}",
        "frz": lambda v: f"\\frz{v:.2f}", "frx": lambda v: f"\\frx{v:.2f}", "fry": lambda v: f"\\fry{v:.2f}",
        "fax": lambda v: f"\\fax{v:.3f}", "blur": lambda v: f"\\blur{v:.2f}", "bord": lambda v: f"\\bord{v:.2f}",
        "fsp": lambda v: f"\\fsp{v:.2f}",
    }
    for i, (key, _) in enumerate(_CHANNELS[2:], start=2):
        if i in which:
            out.append(fmt[key](st[key]))
    if any(i in which for i in range(n, n + 3)):
        r, g, b = (int(round(max(0, min(255, c)))) for c in st["c1"])
        out.append(f"\\1c&H{b:02X}{g:02X}{r:02X}&")
    if any(i in which for i in range(n + 3, n + 6)):
        r, g, b = (int(round(max(0, min(255, c)))) for c in st["c3"])
        out.append(f"\\3c&H{b:02X}{g:02X}{r:02X}&")
    if st["clip"] is not None and any(i in which for i in range(n + 6, n + 10)):
        x0, y0, x1, y1 = st["clip"]
        out.append(f"\\clip({x0:.0f},{y0:.0f},{max(x0, x1):.0f},{max(y0, y1):.0f})")
    return "".join(out)


_DEFAULTS = {"alpha": 0.0, "a1": 0.0, "fscx": 100.0, "fscy": 100.0, "frz": 0.0, "frx": 0.0, "fry": 0.0,
             "fax": 0.0, "blur": 0.0, "bord": 0.0, "fsp": 0.0}


def _linear_breaks(vals: list[list[float]], tol: list[float], lo: int, hi: int) -> list[int]:
    """Greedy breakpoints so linear interpolation stays within `tol` everywhere."""
    out = [lo]
    i = lo
    while i < hi:
        j = i + 1
        while j < hi and _fits(vals, tol, i, j + 1):
            j += 1
        out.append(j)
        i = j
    return out


def _fits(vals, tol, i: int, j: int) -> bool:
    vi, vj = vals[i], vals[j]
    span = j - i
    for k in range(i + 1, j):
        f = (k - i) / span
        vk = vals[k]
        for c in range(len(vi)):
            if abs(vk[c] - (vi[c] + (vj[c] - vi[c]) * f)) > tol[c]:
                return False
    return True


def _emit(states: list[dict], times: list[float], layer: int, style: str) -> list[str]:
    """Turn per-frame states into as few Dialogue events as libass allows."""
    n = len(states)
    if n < 2:
        return []
    # 1) hard breaks: glyph/content changes, clip appearing/disappearing, origin
    hard = [0]
    for k in range(1, n - 1):
        a, b = states[k - 1], states[k]
        if a["text"] != b["text"] or (a["clip"] is None) != (b["clip"] is None) or a["org"] != b["org"] \
                or a["vis"] != b["vis"]:
            hard.append(k)
    hard.append(n - 1)
    # 2) inside each run: linear pieces for the position (\move is linear)
    pos = [[s["x"], s["y"]] for s in states]
    vec = [_vector(s) for s in states]
    events = []
    for a, b in zip(hard, hard[1:]):
        if not any(states[k]["vis"] for k in range(a, b)):
            continue
        cuts = _linear_breaks(pos, [0.35, 0.35], a, b)
        for p0, p1 in zip(cuts, cuts[1:]):
            s0 = states[p0]
            x0, y0 = pos[p0]
            x1, y1 = pos[p1]
            anchor = f"\\pos({x0:.1f},{y0:.1f})" if abs(x1 - x0) < 0.05 and abs(y1 - y0) < 0.05 \
                else f"\\move({x0:.1f},{y0:.1f},{x1:.1f},{y1:.1f})"
            head = "\\an5" + anchor
            if s0["org"] is not None:
                head += f"\\org({s0['org'][0]:.1f},{s0['org'][1]:.1f})"
            # channels that vary inside this piece go into the \t chain
            varying = [c for c in range(len(vec[p0]))
                       if max(vec[k][c] for k in range(p0, p1 + 1)) - min(vec[k][c] for k in range(p0, p1 + 1)) > _TOL[c] * 0.5]
            static = set(varying)
            for i, (key, _) in enumerate(_CHANNELS):
                if abs(s0[key] - _DEFAULTS[key]) > 1e-3:
                    static.add(i)
            static |= set(range(len(_CHANNELS), len(_CHANNELS) + 3))      # colour always
            if s0["bord"] > 0.01 or any(states[k]["bord"] > 0.01 for k in range(p0, p1 + 1)):
                static |= set(range(len(_CHANNELS) + 3, len(_CHANNELS) + 6))
            if s0["clip"] is not None:
                static |= set(range(len(_CHANNELS) + 6, len(_CHANNELS) + 10))
            tags = head + _tags(s0, static, True)
            if varying:
                sub = [[vec[k][c] for c in varying] for k in range(len(vec))]
                subtol = [_TOL[c] for c in varying]
                breaks = _linear_breaks(sub, subtol, p0, p1)
                for q0, q1 in zip(breaks, breaks[1:]):
                    changed = {varying[i] for i in range(len(varying)) if abs(sub[q1][i] - sub[q0][i]) > 1e-6}
                    if not changed:
                        continue
                    t0 = int(round((times[q0] - times[p0]) * 1000))
                    t1 = int(round((times[q1] - times[p0]) * 1000))
                    tags += f"\\t({t0},{t1},{_tags(states[q1], changed, False)})"
            body = s0["text"] if s0.get("drawing") else _escape(s0["text"])
            events.append(f"Dialogue: {layer},{ts(times[p0])},{ts(times[p1])},{style},,0,0,0,,{{{tags}}}{body}")
    return events


def _caret_events(chars, evaluate, ambient, times, em, cap, style) -> list[str]:
    """Blinking caret after the last visible letter (typewriter / delete).

    Solid while letters are appearing or disappearing, blinking (1 s period)
    when idle, faded by text-wide layers. A caret jumps, it never glides, so
    every position change starts a new event.
    """
    blink = 1.0
    rows = []
    last_change = times[0]
    prev = None
    for t in times:
        visible = [(c, s) for c in chars for s in (evaluate(c, t),) if s["tot"]["opacity"] > 0.5]
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
        op = ambient(t) if on else 0.0
        rows.append((round(x, 1), round(y, 1), op))
    events = []
    k0 = 0
    for k in range(1, len(rows) + 1):
        if k == len(rows) or rows[k][:2] != rows[k0][:2] or (rows[k][2] > 0.004) != (rows[k0][2] > 0.004):
            x, y, op = rows[k0]
            if op > 0.004:
                end = times[min(k, len(times) - 1)]
                alpha = _alpha_hex(255 * (1 - op))
                # a slow fade (text-wide Fade out) is interpolated inside the event
                fade = ""
                op_end = rows[k - 1][2]
                if abs(op_end - op) > 0.01 and k - 1 > k0:
                    fade = f"\\t(0,{int(round((times[k - 1] - times[k0]) * 1000))},\\alpha&H{_alpha_hex(255 * (1 - op_end))}&)"
                events.append(f"Dialogue: {cap.layer + 1},{ts(times[k0])},{ts(end)},{style},,0,0,0,,"
                              f"{{\\an5\\pos({x:.1f},{y:.1f})\\alpha&H{alpha}&{fade}}}|")
            k0 = k
    return events
