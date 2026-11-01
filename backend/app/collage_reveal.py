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
    "is_reveal",
    "reveal_length",
    "reveal_progress",
    "reveal_alpha",
    "reveal_coverage",
    "geq_alpha",
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


def geq_alpha(kind: str, p: float, direction: int = 0) -> str:
    """The ``geq`` alpha expression for one frame of one reveal.

    ``p`` is a constant here: FFmpeg's ``geq`` cannot sample a value from another
    filter per pixel, so the render holds one mask per sampled step of the sweep
    and switches them on with ``enable='between(t,…)'`` — the same ladder the
    scaling motions already use. ``u`` and ``v`` are written as ``X/W`` and ``Y/H``
    because the mask is cut to the mat's own box.
    """
    geom = REVEAL_GEOM.get(kind)
    if geom is None:
        return ""
    t = _clamp01(float(p))
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
        return ""
    test = f"gte({q},1-{_num(t)})" if geom.get("out") else f"lte({q},{_num(t)})"
    if geom.get("out"):
        test = f"gt({_num(t)},0)*{test}" if t <= 0 else test
    # Multiplying the input alpha, not replacing it: a polaroid's transparent
    # corners and any shape mask already on this mat stay transparent through the
    # sweep, which is what the preview's CSS mask does to the same element.
    return f"alpha(X,Y)*{test}"


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
