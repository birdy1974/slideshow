"""Picture looks (filters/effects) — the FFmpeg half of the shared catalogue.

Presets live in ``registry/picture-filters.json``, the very same file the
frontend imports in ``src/pictureFilters.ts``, so a preset can never drift
between what the editor shows and what the renderer produces.

Every parameter has two implementations, built from the same numbers:

======================  ==================================  =====================================
parameter               browser (CSS)                       render (FFmpeg, this module)
======================  ==================================  =====================================
brightness / contrast   ``brightness()`` ``contrast()``     ``eq=brightness/contrast``
saturation              ``saturate()``                      ``eq=saturation``
grayscale / sepia /     ``grayscale()`` ``sepia()``         one ``colorchannelmixer`` holding the
hue-rotate / warmth     ``hue-rotate()`` ``url(#warmth)``   product of the four CSS spec matrices
invert                  ``invert(1)``                       ``negate`` (forced through RGB)
softness                ``blur(Npx)``                       ``gblur=sigma=N/2``
sharpen                 contrast bump (approximation only)  ``unsharp`` (the real thing)
vignette                radial-gradient overlay             ``vignette=angle``
pixelate                nearest-neighbour upscaled proxy    ``scale`` down and up, ``neighbor``
======================  ==================================  =====================================

Known approximations (also documented in docs/picture-filters.md): brightness is
additive in ``eq`` and multiplicative in CSS (they agree exactly at mid-grey),
softness needs the CSS-px → sigma factor, and sharpen/pixelate/vignette can only
be hinted at in the browser.

The chain is inserted **after** zoompan (so it runs on output-sized frames, not
on 24 MP sources) and **before** drawtext (so captions keep their own colour).
Because it sits after the fit/fill stage, the blurred letterbox backdrop picks
up the same look as the picture itself.
"""
from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Neutral values: a look whose parameters all equal these renders nothing at all.
IDENTITY: dict[str, float] = {
    "brightness": 1.0, "contrast": 1.0, "saturation": 1.0, "grayscale": 0.0,
    "sepia": 0.0, "hueRotate": 0.0, "warmth": 0.0, "vignette": 0.0,
    "softness": 0.0, "sharpen": 0.0, "pixelate": 0.0, "invert": 0.0,
}

# Slider bounds. The registry's `ranges` block is the source of truth (the
# frontend reads the same numbers); these literals are the fallback so a broken
# or missing registry can never push a value out of an FFmpeg filter's range.
FALLBACK_LIMITS: dict[str, tuple[float, float]] = {
    "brightness": (0.5, 1.5), "contrast": (0.5, 1.8), "saturation": (0.0, 2.5),
    "grayscale": (0.0, 1.0), "sepia": (0.0, 1.0), "hueRotate": (-180.0, 180.0),
    "warmth": (-3.0, 3.0), "vignette": (0.0, 1.0), "softness": (0.0, 4.0),
    "sharpen": (0.0, 1.0), "pixelate": (0.0, 1.0), "invert": (0.0, 1.0),
}

# Relative pixelate block size: 1/120 of the frame width at pixelate=1
# (16 px blocks on a 1920 px wide render). The browser preview downscales to
# 120 px and lets `image-rendering: pixelated` do the same thing.
PIXELATE_DIVISOR = 120

# CSS `blur(r)` and FFmpeg `gblur=sigma` do not use the same unit; Chromium's
# blur radius is about twice the Gaussian standard deviation.
BLUR_SIGMA_FACTOR = 0.5

# Warmth is a plain RGB gain (1 + 0.045w on red, 1 - 0.045w on blue) so the
# browser can apply the identical matrix through an SVG feColorMatrix.
WARMTH_RED = 0.045
WARMTH_GREEN = 0.012


def _registry_candidates() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SLIDESHOW_FILTER_REGISTRY")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "registry" / "picture-filters.json")  # repo checkout
    cands.append(Path("/app/registry/picture-filters.json"))             # Docker image
    cands.append(Path.cwd() / "registry" / "picture-filters.json")
    return cands


def _load_registry() -> tuple[dict[str, dict[str, float]], dict[str, tuple[float, float]]]:
    """id → parameter overrides, plus the slider ranges, from the shared registry."""
    limits = dict(FALLBACK_LIMITS)
    for path in _registry_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.get("presets") if isinstance(data, dict) else None
        if not entries:
            continue
        ranges = data.get("ranges") if isinstance(data.get("ranges"), dict) else {}
        for name, spec in ranges.items():
            if not isinstance(spec, dict) or name not in IDENTITY:
                continue
            low, high = _number(spec.get("min")), _number(spec.get("max"))
            if low is not None and high is not None and high >= low:
                limits[name] = (low, high)
        presets: dict[str, dict[str, float]] = {}
        for entry in entries:
            pid = str(entry.get("id", "")).strip()
            params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
            if not pid or pid == "none":
                continue
            clean = {k: float(v) for k, v in params.items() if k in IDENTITY and _number(v) is not None}
            if clean:
                presets[pid] = clean
        if presets:
            log.info("Loaded %d picture looks from %s", len(presets), path)
            return presets, limits
    log.warning("registry/picture-filters.json not found; picture filters unavailable")
    return {}, limits


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


PRESETS, LIMITS = _load_registry()


def clamp_param(name: str, value: float) -> float:
    low, high = LIMITS.get(name, (-math.inf, math.inf))
    return max(low, min(high, value))


def num(value: float) -> str:
    """Stable FFmpeg numeric literal — strips binary float noise (4.999999 → 5)."""
    return f"{round(float(value), 6):g}"


def resolve_look(item: dict[str, Any] | None) -> dict[str, float]:
    """Flatten an item's look into concrete parameter values.

    ``filter`` picks a preset, ``filterAmount`` (0..1) fades it toward the
    original, and ``filterAdjust`` stacks the manual sliders on top. Anything
    missing, unknown or malformed falls back to its identity value, so a
    project saved by an older version simply renders untouched.
    """
    item = item if isinstance(item, dict) else {}
    preset = PRESETS.get(str(item.get("filter") or "").strip(), {})
    amount = _number(item.get("filterAmount"))
    amount = 1.0 if amount is None else max(0.0, min(1.0, amount))

    params = dict(IDENTITY)
    for name, neutral in IDENTITY.items():
        if name in preset:
            params[name] = neutral + (clamp_param(name, preset[name]) - neutral) * amount

    adjust = item.get("filterAdjust")
    adjust = adjust if isinstance(adjust, dict) else {}
    for name in ("brightness", "contrast", "saturation"):
        factor = _number(adjust.get(name))
        if factor is not None:
            params[name] = clamp_param(name, params[name] * factor)
    for name in ("warmth", "vignette", "softness"):
        delta = _number(adjust.get(name))
        if delta is not None:
            params[name] = clamp_param(name, params[name] + delta)
    return params


def _multiply(left: list[list[float]], right: list[list[float]]) -> list[list[float]]:
    return [[sum(left[i][k] * right[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _grayscale_matrix(amount: float) -> list[list[float]]:
    """The exact matrix the CSS `grayscale()` spec interpolates toward."""
    keep = 1.0 - amount
    return [
        [0.2126 + 0.7874 * keep, 0.7152 - 0.7152 * keep, 0.0722 - 0.0722 * keep],
        [0.2126 - 0.2126 * keep, 0.7152 + 0.2848 * keep, 0.0722 - 0.0722 * keep],
        [0.2126 - 0.2126 * keep, 0.7152 - 0.7152 * keep, 0.0722 + 0.9278 * keep],
    ]


def _sepia_matrix(amount: float) -> list[list[float]]:
    """The exact matrix the CSS `sepia()` spec interpolates toward."""
    keep = 1.0 - amount
    return [
        [0.393 + 0.607 * keep, 0.769 - 0.769 * keep, 0.189 - 0.189 * keep],
        [0.349 - 0.349 * keep, 0.686 + 0.314 * keep, 0.168 - 0.168 * keep],
        [0.272 - 0.272 * keep, 0.534 - 0.534 * keep, 0.131 + 0.869 * keep],
    ]


def _hue_matrix(degrees: float) -> list[list[float]]:
    """The matrix the CSS `hue-rotate()` spec defines (not a true HSV rotation,
    which is exactly why the browser and the render agree when we use it too)."""
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return [
        [0.213 + 0.787 * cos - 0.213 * sin, 0.715 - 0.715 * cos - 0.715 * sin, 0.072 - 0.072 * cos + 0.928 * sin],
        [0.213 - 0.213 * cos + 0.143 * sin, 0.715 + 0.285 * cos + 0.140 * sin, 0.072 - 0.072 * cos - 0.283 * sin],
        [0.213 - 0.213 * cos - 0.787 * sin, 0.715 - 0.715 * cos + 0.715 * sin, 0.072 + 0.928 * cos - 0.072 * sin],
    ]


def _warmth_matrix(amount: float) -> list[list[float]]:
    """Plain RGB gain, identical to the SVG feColorMatrix the preview uses."""
    return [
        [1.0 + WARMTH_RED * amount, 0.0, 0.0],
        [0.0, 1.0 + WARMTH_GREEN * amount, 0.0],
        [0.0, 0.0, 1.0 - WARMTH_RED * amount],
    ]


def colour_matrix(params: dict[str, float]) -> list[list[float]] | None:
    """grayscale → sepia → hue-rotate → warmth, folded into one 3×3 matrix.

    CSS applies its filter functions left to right, so the combined matrix is
    the product in reverse order. Returns None when it would be the identity.
    """
    matrix = _grayscale_matrix(params.get("grayscale", 0.0))
    matrix = _multiply(_sepia_matrix(params.get("sepia", 0.0)), matrix)
    matrix = _multiply(_hue_matrix(params.get("hueRotate", 0.0)), matrix)
    matrix = _multiply(_warmth_matrix(params.get("warmth", 0.0)), matrix)
    identity = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    if all(abs(matrix[i][j] - identity[i][j]) < 1e-4 for i in range(3) for j in range(3)):
        return None
    return matrix


def filter_chain(params: dict[str, float], width: int, height: int) -> str:
    """The FFmpeg filter chain for a resolved look ('' when nothing to do)."""
    parts: list[str] = []

    pixelate = params.get("pixelate", 0.0)
    if pixelate > 0.001 and width > 0 and height > 0:
        block = max(2, round(width / PIXELATE_DIVISOR * pixelate))
        small_w = max(2, int(width) // block)
        small_h = max(2, int(height) // block)
        parts.append(f"scale={small_w}:{small_h}:flags=neighbor,scale={width}:{height}:flags=neighbor,setsar=1")

    # CSS multiplies brightness, eq adds it; they agree exactly at mid-grey.
    brightness = params.get("brightness", 1.0)
    contrast = params.get("contrast", 1.0)
    saturation = params.get("saturation", 1.0)
    eq_parts: list[str] = []
    if abs(brightness - 1.0) > 0.001:
        eq_parts.append(f"brightness={num((brightness - 1.0) * 0.5)}")
    if abs(contrast - 1.0) > 0.001:
        eq_parts.append(f"contrast={num(contrast)}")
    if abs(saturation - 1.0) > 0.001:
        eq_parts.append(f"saturation={num(saturation)}")
    if eq_parts:
        parts.append("eq=" + ":".join(eq_parts))

    matrix = colour_matrix(params)
    if matrix is not None:
        keys = ("rr", "rg", "rb", "gr", "gg", "gb", "br", "bg", "bb")
        values = [matrix[i][j] for i in range(3) for j in range(3)]
        parts.append("colorchannelmixer=" + ":".join(f"{k}={num(v)}" for k, v in zip(keys, values)))

    if params.get("invert", 0.0) > 0.5:
        # negate on YUV is not the same operation as CSS invert(), so go via RGB.
        parts.append("format=rgb24,negate,format=yuv420p")

    softness = params.get("softness", 0.0)
    if softness > 0.01:
        parts.append(f"gblur=sigma={num(softness * BLUR_SIGMA_FACTOR)}")

    sharpen = params.get("sharpen", 0.0)
    if sharpen > 0.01:
        parts.append(f"unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount={num(0.3 + 0.7 * sharpen)}")

    vignette = params.get("vignette", 0.0)
    if vignette > 0.01:
        # vignette=angle takes radians in [0, PI/2]; the slider maps to [0, PI/4].
        parts.append(f"vignette=angle={num(vignette * math.pi / 4)}")

    return ",".join(parts)


def picture_look(item: dict[str, Any] | None, width: int, height: int) -> str:
    """Renderer entry point: resolve an item's look and return its chain."""
    return filter_chain(resolve_look(item), width, height)


def has_look(item: dict[str, Any] | None) -> bool:
    """True when an item would render differently from the untouched original."""
    params = resolve_look(item)
    return any(abs(params[name] - neutral) > 0.001 for name, neutral in IDENTITY.items())
