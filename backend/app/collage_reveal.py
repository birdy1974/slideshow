"""The mask-reveal geometry of a collage photo, in Python.

This is the render's half of ``src/collageReveal.ts``: the same seven families,
the same measure per point, the same easing, no translation layer between them.
``test_collage_reveal.py`` runs a Node driver over the TypeScript and compares
the sampled masks point by point, and :func:`geq_alpha` turns the same measure
into the ``geq`` expression FFmpeg evaluates on every pixel of every frame.

Imported by ``collage.py``; kept free of FFmpeg and Fastapi so it can be unit
tested like the karaoke model.
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = [
    "REVEALS",
    "REVEAL_GEOM",
    "REVEAL_LENGTH",
    "EASE_EXPR",
    "is_reveal",
    "reveal_length",
    "reveal_progress",
    "reveal_alpha",
    "reveal_coverage",
    "geq_alpha",
    "geq_alpha_expr",
    "reveal_step_expr",
]

REVEALS: tuple[str, ...] = (
    "wipeUp", "wipeDown", "wipeLeft", "wipeRight",
    "diagDownRight", "diagDownLeft",
    "iris", "irisOut", "clock", "pinwheel",
    "blindsDown", "blindsRight", "mosaic", "mosaicOut",
    "diamond", "curtain",
)

# family, mirror axis, n, out, angle — the numbers, in the same order as the
# TypeScript table, so a side-by-side read is a diff and not a translation.
REVEAL_GEOM: dict[str, dict[str, Any]] = {
    "wipeUp": {"fam": "linear", "mirror": "y", "angle": 0.0},
    "wipeDown": {"fam": "linear", "mirror": "y", "angle": 180.0},
    "wipeLeft": {"fam": "linear", "mirror": "x", "angle": 270.0},
    "wipeRight": {"fam": "linear", "mirror": "x", "angle": 90.0},
    "diagDownRight": {"fam": "linear", "mirror": "both", "angle": 135.0},
    "diagDownLeft": {"fam": "linear", "mirror": "both", "angle": 225.0},
    "iris": {"fam": "radial", "mirror": "none"},
    "irisOut": {"fam": "radial", "mirror": "none", "out": True},
    "clock": {"fam": "wedge", "mirror": "x", "n": 1},
    "pinwheel": {"fam": "wedge", "mirror": "x", "n": 4},
    "blindsDown": {"fam": "repeat", "mirror": "y", "n": 6},
    "blindsRight": {"fam": "repeat", "mirror": "x", "n": 6},
    "mosaic": {"fam": "mosaic", "mirror": "both", "n": 4},
    "mosaicOut": {"fam": "mosaic", "mirror": "both", "n": 4, "out": True},
    "diamond": {"fam": "diamond", "mirror": "none"},
    "curtain": {"fam": "curtain", "mirror": "x"},
}

REVEAL_LENGTH: dict[str, float] = {
    "wipeUp": 0.5, "wipeDown": 0.5, "wipeLeft": 0.5, "wipeRight": 0.5,
    "diagDownRight": 0.55, "diagDownLeft": 0.55,
    "iris": 0.6, "irisOut": 0.6, "clock": 0.75, "pinwheel": 0.75,
    "blindsDown": 0.7, "blindsRight": 0.7, "mosaic": 0.75, "mosaicOut": 0.75,
    "diamond": 0.6, "curtain": 0.6,
}

# Eased in and out for the plane and round kinds (a wipe that starts and stops
# decisively); the tiled ones accelerate, which reads as paper rather than as a
# machine. Mirrors EASE in the TypeScript file.
_SMOOTH = lambda q: q * q * (3.0 - 2.0 * q)  # noqa: E731
_ACCEL = lambda q: q * (2.0 - q)  # noqa: E731
_LINEAR = lambda q: q  # noqa: E731
EASE: dict[str, Any] = {
    "linear": _SMOOTH, "curtain": _SMOOTH, "diamond": _SMOOTH, "radial": _SMOOTH,
    "wedge": _LINEAR, "repeat": _ACCEL, "mosaic": _ACCEL,
}

#: The same three curves in FFmpeg's expression syntax, `{q}` where the progress goes.
#: A family with no entry here cannot have its sweep written as a function of frame
#: time, and falls back to one baked still per step (see :func:`reveal_step_expr`).
#: `q` is expected clamped to 0…1 by the caller, exactly as `reveal_progress` does.
#: Written as multiplications, never `pow(q, 2)`: the preview evaluates the same
#: polynomial in JavaScript and the step is `floor` of the result, so a last-ulp
#: difference between the two forms is a whole step of mask on the frames where the
#: sweep crosses a boundary. Both engines must do the same arithmetic to agree.
EASE_EXPR: dict[str, str] = {
    "linear": "({q})*({q})*(3-2*({q}))",
    "curtain": "({q})*({q})*(3-2*({q}))",
    "diamond": "({q})*({q})*(3-2*({q}))",
    "radial": "({q})*({q})*(3-2*({q}))",
    "wedge": "{q}",
    "repeat": "({q})*(2-({q}))",
    "mosaic": "({q})*(2-({q}))",
}


def is_reveal(value: Any) -> bool:
    return isinstance(value, str) and value in REVEAL_GEOM


def reveal_length(kind: Any) -> float:
    return float(REVEAL_LENGTH.get(kind, 0.0)) if is_reveal(kind) else 0.0


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def reveal_progress(kind: str, q: float) -> float:
    """Entrance progress → the mask's own p, with the family's easing."""
    geom = REVEAL_GEOM.get(kind)
    t = _clamp01(float(q))
    return EASE[geom["fam"]](t) if geom else t


def reveal_measure(geom: dict[str, Any], x: float, y: float) -> float:
    """How far into the sweep the point (x, y) of the mat sits, 0…1.

    The same seven branches as ``revealMeasure`` in the TypeScript, and the only
    place the shape of a reveal is decided.
    """
    fam = geom["fam"]
    if fam == "linear":
        angle = math.radians(float(geom.get("angle") or 0.0))
        sx = math.sin(angle)
        sy = -math.cos(angle)
        extent = abs(sx) + abs(sy)
        if extent <= 0:
            return 1.0
        return _clamp01(0.5 + ((x - 0.5) * sx + (y - 0.5) * sy) / extent)
    if fam == "curtain":
        return abs(x - 0.5) * 2.0
    if fam == "diamond":
        return abs(x - 0.5) + abs(y - 0.5)
    if fam == "radial":
        return min(1.0, math.hypot((x - 0.5) * 2.0, (y - 0.5) * 2.0) / math.sqrt(2.0))
    if fam == "wedge":
        n = max(1, int(round(geom.get("n") or 1)))
        a = math.atan2(x - 0.5, 0.5 - y) / (2.0 * math.pi)
        if a < 0:
            a += 1.0
        return a if n == 1 else (a * n) % 1.0
    if fam == "repeat":
        n = max(1, int(round(geom.get("n") or 6)))
        along = y if geom.get("mirror") == "y" else x
        idx = min(n - 1, max(0, int(math.floor(along * n))))
        lead = idx / (n - 1) * 0.6 if n > 1 else 0.0
        across = (along * n) % 1.0
        return min(1.0, lead + across * 0.4)
    if fam == "mosaic":
        n = max(1, int(round(geom.get("n") or 4)))
        cx = min(n - 1, int(math.floor(x * n)))
        cy = min(n - 1, int(math.floor(y * n)))
        lead = (cx + cy) / (2 * n - 2) * 0.55 if n > 1 else 0.0
        in_x = abs((x * n) % 1.0 - 0.5) * 2.0
        in_y = abs((y * n) % 1.0 - 0.5) * 2.0
        return min(1.0, lead + max(in_x, in_y) * 0.45)
    return 1.0


def reveal_alpha(kind: str, u: float, v: float, p: float, direction: int = 0) -> int:
    """0 or 1: is this point of the mat uncovered yet?"""
    geom = REVEAL_GEOM.get(kind)
    if geom is None:
        return 1
    x, y = float(u), float(v)
    mirror = geom.get("mirror")
    if int(direction or 0) == 1:
        if mirror == "x":
            x = 1.0 - x
        elif mirror == "y":
            y = 1.0 - y
        elif mirror == "both":
            x, y = 1.0 - x, 1.0 - y
    t = _clamp01(float(p))
    q = reveal_measure(geom, x, y)
    if geom.get("out"):
        # Read from the far side, but still an entrance: nothing at t = 0.
        return 1 if (t > 0.0 and q >= 1.0 - t) else 0
    return 1 if q <= t else 0


def reveal_coverage(kind: str, p: float, direction: int = 0, grid: int = 16) -> float:
    """Uncovered fraction of the mat, sampled on a grid — what the tests read."""
    hit = 0
    for r in range(grid):
        for c in range(grid):
            hit += reveal_alpha(kind, (c + 0.5) / grid, (r + 0.5) / grid, p, direction)
    return hit / float(grid * grid)


def _num(value: float) -> str:
    """A constant in an FFmpeg expression: shortest form that stays exact."""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _num_full(value: float) -> str:
    """A constant written so that it reads back as the same double — and never in exponent
    form, which FFmpeg's expression lexer has no use for.

    `repr` is the shortest text with that property. It matters wherever a value is then
    `floor`ed: a window rounded to six decimals shifts the progress by one part in 10⁷,
    and on the frames where a sweep crosses a step that shift is a whole step of mask.
    """
    text = repr(float(value))
    if "e" in text or "E" in text:
        text = f"{float(value):.17f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _mask_test(geom: dict[str, Any], step: str, direction: int) -> str | None:
    """The test that decides, for one pixel, whether the sweep has reached it.

    `step` is the sweep's progress already rendered as expression text: a literal for a
    baked still, a `T`-driven expression for a merged ladder.
    """
    x = "X/W"
    y = "Y/H"
    mirror = geom.get("mirror")
    if int(direction or 0) == 1:
        if mirror == "x":
            x = f"(1-{x})"
        elif mirror == "y":
            y = f"(1-{y})"
        elif mirror == "both":
            x, y = f"(1-{x})", f"(1-{y})"
    q = _geq_measure(geom, x, y)
    if q is None:
        return None
    return f"gte({q},1-{step})" if geom.get("out") else f"lte({q},{step})"


def geq_alpha(kind: str, p: float, direction: int = 0) -> str:
    """The ``geq`` alpha expression for one frame of one reveal.

    ``p`` is a constant here: FFmpeg's ``geq`` cannot sample a value from another
    filter per pixel, so the render holds one mask per sampled step of the sweep
    and switches them on with ``enable='between(t,…)`` — the same ladder the
    scaling motions already use. :func:`geq_alpha_expr` is the form that needs no
    still per step. ``u`` and ``v`` are written as ``X/W`` and ``Y/H`` because the
    mask is cut to the mat's own box.
    """
    geom = REVEAL_GEOM.get(kind)
    if geom is None:
        return ""
    t = _clamp01(float(p))
    test = _mask_test(geom, _num(t), direction)
    if test is None:
        return ""
    if geom.get("out"):
        test = f"gt({_num(t)},0)*{test}" if t <= 0 else test
    # Multiplying the input alpha, not replacing it: a polaroid's transparent
    # corners and any shape mask already on this mat stay transparent through the
    # sweep, which is what the preview's CSS mask does to the same element.
    return f"alpha(X,Y)*{test}"


def geq_alpha_expr(kind: str, step_expr: str, direction: int = 0) -> str:
    """:func:`geq_alpha` with the step as an expression rather than a number.

    The expression is evaluated for every pixel of every frame the level is on screen
    for, so it has to stay short: the win is one filter chain instead of one per step,
    not a cheaper inner loop.
    """
    geom = REVEAL_GEOM.get(kind)
    if geom is None:
        return ""
    test = _mask_test(geom, f"({step_expr})", direction)
    return "" if test is None else f"alpha(X,Y)*{test}"


def reveal_step_expr(kind: str, start: float, span: float, fps: float = 0.0,
                     steps: int | None = None) -> str | None:
    """The sweep's quantised step, as a `geq` expression in the frame's own time.

    The per-step ladder exists because a `geq` mask was thought to need a constant: one
    baked still per step of the sweep, switched by `enable`. But the step is a *pure
    function of time* — the photo's arrival window and the family's easing are both
    known when the graph is written — and `geq` exposes the frame timestamp as `T` to
    every one of its expressions. So a couple of hundred characters, written once,
    reproduce the same image on the same frames as thirty-odd chains, and the fan-out
    that fed them disappears with them.

    Three things are what make it exact rather than nearly so:

    * `reveal_quantise` returns exactly 1.0 once the sweep is over, while the
      quantisation alone stops at 31.5/32 and would leave a sliver of the photo masked
      for the rest of the slide. The closing of that gap has to be read off the *eased*
      progress, which is what the sampler tests: a frame can sit a hair below 1 before
      the curve and land on exactly 1 after it, and asking the raw progress instead
      left that frame with 31.5/32 of mask on — measured on `mosaic` at 25 fps in a
      19-frame sweep, one frame of the hold. `min(1, …)` over the quantised value is
      both the correction and the shorter way to write it, since the quantisation of a
      progress inside 0…1 cannot exceed 31.5/32 and only a completed sweep reaches it.
    * The arithmetic is the preview's: the easing is multiplied out rather than written
      with `pow`, because the step is `floor` of the result and a last-ulp difference in
      how the square was taken is a whole step of mask on a boundary frame.
    * `T` is snapped back to the frame index and divided again. FFmpeg forms `T` from
      the presentation timestamp (`pts * (1/rate)`) while the ladder's sampler and the
      preview both form it as `k / fps`; the two are the same number by another route and
      can land an ulp apart. That is harmless until the sweep crosses a quantisation
      boundary on exactly that frame, where an ulp chooses which step the frame shows —
      measured once, on the wedge of a `clock` reveal, where it cost one pixel. An
      epsilon on the `floor` would not fix that, it would only move the disagreement to
      a different frame; recovering `k` and dividing gives the sampler's double back.
    """
    geom = REVEAL_GEOM.get(kind)
    if geom is None or span <= 0:
        return None
    tmpl = EASE_EXPR.get(geom["fam"])
    if tmpl is None:
        return None
    n = max(1, int(REVEAL_STEPS if steps is None else steps))
    # The level this feeds keeps its frames' own timestamps — renumbering them would
    # make every frame of the level show one step — so its `T` is the frame's place on
    # the slide, which is how `start` is measured too.
    # The frame index this recovers is only as good as the rate being whole, which is
    # what the renderer passes (`fps` is read with `int()`); a rate that does not divide
    # 1e6 still recovers its index, since the rounding is worth microseconds and the
    # test is worth half a frame.
    at = f"(floor(T*{_num_full(fps)}+0.5)/{_num_full(fps)})" if fps else "T"
    raw = f"min(1,max(0,({at}-{_num_full(start)})/{_num_full(span)}))"
    eased = tmpl.replace("{q}", raw)
    return f"min(1,(min({n},max(0,floor(({eased})*{n})))+0.5)/{n})"


def _geq_measure(geom: dict[str, Any], x: str, y: str) -> str | None:
    """The FFmpeg-expression twin of :func:`reveal_measure`."""
    fam = geom["fam"]
    if fam == "linear":
        angle = math.radians(float(geom.get("angle") or 0.0))
        sx, sy = math.sin(angle), -math.cos(angle)
        extent = abs(sx) + abs(sy)
        if extent <= 0:
            return None
        # sin/cos of the table's fixed angles are exact enough to bake in.
        return f"min(1,max(0,0.5+(({x}-0.5)*{_num(sx)}+({y}-0.5)*{_num(sy)})/{_num(extent)}))"
    if fam == "curtain":
        return f"abs({x}-0.5)*2"
    if fam == "diamond":
        return f"abs({x}-0.5)+abs({y}-0.5)"
    if fam == "radial":
        return f"min(1,hypot(({x}-0.5)*2,({y}-0.5)*2)/{_num(math.sqrt(2.0))})"
    if fam == "wedge":
        n = max(1, int(round(geom.get("n") or 1)))
        a = f"atan2({x}-0.5,0.5-{y})/{_num(2.0 * math.pi)}"
        a = f"mod({a}+1,1)"
        return a if n == 1 else f"mod({a}*{n},1)"
    if fam == "repeat":
        n = max(1, int(round(geom.get("n") or 6)))
        along = y if geom.get("mirror") == "y" else x
        idx = f"min({n - 1},max(0,floor({along}*{n})))"
        lead = f"{idx}/{_num(n - 1)}*0.6" if n > 1 else "0"
        across = f"mod(mod({along}*{n},1)+1,1)"
        return f"min(1,{lead}+{across}*0.4)"
    if fam == "mosaic":
        n = max(1, int(round(geom.get("n") or 4)))
        cx = f"min({n - 1},floor({x}*{n}))"
        cy = f"min({n - 1},floor({y}*{n}))"
        lead = f"({cx}+{cy})/{_num(2 * n - 2)}*0.55" if n > 1 else "0"
        in_x = f"abs(mod({x}*{n},1)-0.5)*2"
        in_y = f"abs(mod({y}*{n},1)-0.5)*2"
        return f"min(1,{lead}+max({in_x},{in_y})*0.45)"
    return None


# The step count BOTH engines quantise a sweep to: the preview caches one mask
# image per step, the render holds one masked sprite per step, and the shared
# number is what makes them uncover the same pixels at the same moment.
REVEAL_STEPS = 32


def reveal_quantise(steps: int, p: float) -> float:
    """The centre of the step `p` falls in — how the ladder samples a sweep.

    Shared with the preview's own quantisation so both sides show exactly the same
    mask at exactly the same moment, to within one step of a sweep.
    """
    n = max(1, int(steps))
    t = _clamp01(float(p))
    if t >= 1.0:
        return 1.0
    return (min(n - 1, max(0, int(math.floor(t * n)))) + 0.5) / n


def normalize_reveal(value: Any) -> str | None:
    """The id if it is one of ours, else nothing (so the caller falls back)."""
    return value if is_reveal(value) else None


_ANGLE_RE = re.compile(r"[^0-9a-zA-Z]")


def safe_reveal_key(value: Any) -> str:
    """File-name-safe form of a reveal id, for preview cache keys."""
    return _ANGLE_RE.sub("", str(value or ""))[:32]
