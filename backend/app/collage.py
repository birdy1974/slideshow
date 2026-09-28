"""Photo collages — layout, choreography and the FFmpeg composite graph.

The layout maths (placements, mat sizes, animation curves) is the Python twin
of src/collageCore.ts: same inputs, same outputs, so the editor preview and
the rendered MP4 place every photo identically. The graph builder below turns
those numbers into FFmpeg filter chains (polaroid mat + soft shadow + animated
rotate/scale/overlay per photo), composited onto the slide's background colour
stream with the caption drawn on top.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .media import source_path
from .picture_crop import crop_filters, normalize_crop
from .picture_filters import picture_look

MAX_COLLAGE_PHOTOS = 12


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


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _photo_aspect(shape: str) -> float:
    if shape == "square":
        return 1.0
    if shape == "3:4":
        return 3.0 / 4.0
    if shape == "16:9":
        return 16.0 / 9.0
    if shape == "9:16":
        return 9.0 / 16.0
    if shape == "3:2":
        return 3.0 / 2.0
    if shape == "2:3":
        return 2.0 / 3.0
    return 4.0 / 3.0


# Twin of COLLAGE_TEMPLATES in src/collageCore.ts — keep slot numbers identical.
# w / h are the slot BOX (% of frame width / height); the mat is scaled down
# to fit both in placements().
COLLAGE_TEMPLATES: list[dict[str, Any]] = [
    {"id": "split-v", "family": "magazine", "slots": [
        {"cx": 26, "cy": 50, "w": 46, "h": 88, "rot": 0}, {"cx": 74, "cy": 50, "w": 46, "h": 88, "rot": 0},
    ]},
    {"id": "split-h", "family": "magazine", "slots": [
        {"cx": 50, "cy": 27, "w": 70, "h": 42, "rot": 0}, {"cx": 50, "cy": 73, "w": 70, "h": 42, "rot": 0},
    ]},
    {"id": "triptych", "family": "magazine", "slots": [
        {"cx": 18, "cy": 50, "w": 30, "h": 88, "rot": 0}, {"cx": 50, "cy": 50, "w": 30, "h": 88, "rot": 0}, {"cx": 82, "cy": 50, "w": 30, "h": 88, "rot": 0},
    ]},
    {"id": "trio-left", "family": "magazine", "slots": [
        {"cx": 30, "cy": 50, "w": 54, "h": 88, "rot": 0}, {"cx": 78, "cy": 28, "w": 36, "h": 42, "rot": 0}, {"cx": 78, "cy": 72, "w": 36, "h": 42, "rot": 0},
    ]},
    {"id": "trio-right", "family": "magazine", "slots": [
        {"cx": 22, "cy": 28, "w": 36, "h": 42, "rot": 0}, {"cx": 22, "cy": 72, "w": 36, "h": 42, "rot": 0}, {"cx": 70, "cy": 50, "w": 54, "h": 88, "rot": 0},
    ]},
    {"id": "trio-top", "family": "magazine", "slots": [
        {"cx": 50, "cy": 27, "w": 88, "h": 46, "rot": 0}, {"cx": 26, "cy": 74, "w": 42, "h": 40, "rot": 0}, {"cx": 74, "cy": 74, "w": 42, "h": 40, "rot": 0},
    ]},
    {"id": "quad", "family": "magazine", "slots": [
        {"cx": 26, "cy": 28, "w": 44, "h": 42, "rot": 0}, {"cx": 74, "cy": 28, "w": 44, "h": 42, "rot": 0},
        {"cx": 26, "cy": 72, "w": 44, "h": 42, "rot": 0}, {"cx": 74, "cy": 72, "w": 44, "h": 42, "rot": 0},
    ]},
    {"id": "one-plus-three", "family": "magazine", "slots": [
        {"cx": 32, "cy": 50, "w": 56, "h": 88, "rot": 0}, {"cx": 80, "cy": 20, "w": 32, "h": 26, "rot": 0},
        {"cx": 80, "cy": 50, "w": 32, "h": 26, "rot": 0}, {"cx": 80, "cy": 80, "w": 32, "h": 26, "rot": 0},
    ]},
    {"id": "hero-row", "family": "magazine", "slots": [
        {"cx": 50, "cy": 30, "w": 90, "h": 52, "rot": 0}, {"cx": 18, "cy": 77, "w": 28, "h": 34, "rot": 0},
        {"cx": 50, "cy": 77, "w": 28, "h": 34, "rot": 0}, {"cx": 82, "cy": 77, "w": 28, "h": 34, "rot": 0},
    ]},
    {"id": "five-mosaic", "family": "magazine", "slots": [
        {"cx": 30, "cy": 50, "w": 54, "h": 88, "rot": 0}, {"cx": 68, "cy": 28, "w": 18, "h": 42, "rot": 0},
        {"cx": 88, "cy": 28, "w": 18, "h": 42, "rot": 0}, {"cx": 68, "cy": 72, "w": 18, "h": 42, "rot": 0},
        {"cx": 88, "cy": 72, "w": 18, "h": 42, "rot": 0},
    ]},
    {"id": "six-grid", "family": "magazine", "slots": [
        {"cx": 18, "cy": 28, "w": 30, "h": 42, "rot": 0}, {"cx": 50, "cy": 28, "w": 30, "h": 42, "rot": 0}, {"cx": 82, "cy": 28, "w": 30, "h": 42, "rot": 0},
        {"cx": 18, "cy": 72, "w": 30, "h": 42, "rot": 0}, {"cx": 50, "cy": 72, "w": 30, "h": 42, "rot": 0}, {"cx": 82, "cy": 72, "w": 30, "h": 42, "rot": 0},
    ]},
    {"id": "polaroid-pile", "family": "polaroid", "slots": [
        {"cx": 42, "cy": 48, "w": 34, "h": 78, "rot": -11}, {"cx": 58, "cy": 44, "w": 34, "h": 78, "rot": 8}, {"cx": 48, "cy": 56, "w": 36, "h": 78, "rot": 3},
        {"cx": 36, "cy": 40, "w": 30, "h": 78, "rot": -18}, {"cx": 64, "cy": 58, "w": 30, "h": 78, "rot": 14}, {"cx": 50, "cy": 38, "w": 28, "h": 78, "rot": -4},
    ]},
    {"id": "polaroid-diagonal", "family": "polaroid", "slots": [
        {"cx": 22, "cy": 28, "w": 32, "h": 78, "rot": -8}, {"cx": 40, "cy": 40, "w": 32, "h": 78, "rot": 4},
        {"cx": 58, "cy": 52, "w": 32, "h": 78, "rot": -5}, {"cx": 74, "cy": 66, "w": 32, "h": 78, "rot": 7},
        {"cx": 50, "cy": 24, "w": 26, "h": 78, "rot": 12},
    ]},
    {"id": "polaroid-rows", "family": "polaroid", "slots": [
        {"cx": 22, "cy": 32, "w": 30, "h": 78, "rot": -7}, {"cx": 50, "cy": 28, "w": 30, "h": 78, "rot": 5}, {"cx": 78, "cy": 34, "w": 30, "h": 78, "rot": -4},
        {"cx": 28, "cy": 70, "w": 30, "h": 78, "rot": 6}, {"cx": 56, "cy": 74, "w": 30, "h": 78, "rot": -8}, {"cx": 82, "cy": 68, "w": 30, "h": 78, "rot": 3},
    ]},
    {"id": "polaroid-stairs", "family": "polaroid", "slots": [
        {"cx": 20, "cy": 70, "w": 30, "h": 78, "rot": -6}, {"cx": 36, "cy": 56, "w": 30, "h": 78, "rot": 4},
        {"cx": 52, "cy": 42, "w": 30, "h": 78, "rot": -3}, {"cx": 68, "cy": 28, "w": 30, "h": 78, "rot": 7},
        {"cx": 82, "cy": 18, "w": 26, "h": 78, "rot": -10},
    ]},
    {"id": "polaroid-heart", "family": "polaroid", "slots": [
        {"cx": 32, "cy": 32, "w": 28, "h": 78, "rot": -14}, {"cx": 68, "cy": 32, "w": 28, "h": 78, "rot": 14}, {"cx": 22, "cy": 52, "w": 26, "h": 78, "rot": -8},
        {"cx": 78, "cy": 52, "w": 26, "h": 78, "rot": 8}, {"cx": 50, "cy": 48, "w": 30, "h": 78, "rot": 2}, {"cx": 50, "cy": 76, "w": 28, "h": 78, "rot": -3},
    ]},
    {"id": "polaroid-strip", "family": "polaroid", "slots": [
        {"cx": 16, "cy": 50, "w": 28, "h": 78, "rot": -6}, {"cx": 34, "cy": 46, "w": 28, "h": 78, "rot": 5},
        {"cx": 52, "cy": 52, "w": 28, "h": 78, "rot": -4}, {"cx": 70, "cy": 47, "w": 28, "h": 78, "rot": 7},
        {"cx": 86, "cy": 53, "w": 26, "h": 78, "rot": -5},
    ]},
    {"id": "polaroid-corners", "family": "polaroid", "slots": [
        {"cx": 20, "cy": 22, "w": 30, "h": 78, "rot": -10}, {"cx": 80, "cy": 22, "w": 30, "h": 78, "rot": 9},
        {"cx": 20, "cy": 78, "w": 30, "h": 78, "rot": 7}, {"cx": 80, "cy": 78, "w": 30, "h": 78, "rot": -8},
        {"cx": 50, "cy": 50, "w": 36, "h": 78, "rot": 3},
    ]},
]


def collage_template(tid: str | None) -> dict[str, Any]:
    for t in COLLAGE_TEMPLATES:
        if t["id"] == tid:
            return t
    return COLLAGE_TEMPLATES[0]


def _template_slots(tid: str | None, n: int) -> list[dict[str, float]]:
    slots = collage_template(tid)["slots"]
    out: list[dict[str, float]] = []
    for i in range(n):
        if i < len(slots):
            out.append(dict(slots[i]))
            continue
        base = slots[i % len(slots)]
        k = i // len(slots)
        extra = {
            "cx": max(8.0, min(92.0, base["cx"] + (hash01(i, 11) - 0.5) * 10 * k)),
            "cy": max(10.0, min(90.0, base["cy"] + (hash01(i, 13) - 0.5) * 10 * k)),
            "w": base["w"] * 0.85,
            "rot": base["rot"] + (hash01(i, 17) - 0.5) * 14,
        }
        if base.get("h") is not None:
            extra["h"] = base["h"] * 0.85
        out.append(extra)
    return out


def mat_height_per_width(shape: str, fr: dict[str, Any], aspect: float) -> float:
    """Height of a mat per 1 % of frame width, in % of frame HEIGHT — the pixel
    maths of the sprite (photo + border + polaroid caption strip) for the
    resolved frame `fr`, times the frame's W/H. Twin of matHeightPerWidth()."""
    b = frame_border_frac(fr)
    bottom = 0.205 if fr["shape"] == "polaroid" else b
    return ((1 - 2 * b) / _photo_aspect(shape) + b + bottom) * aspect


def _template_slot_width(slot: dict[str, float], spec: dict[str, Any], i: int, aspect: float) -> float:
    """Widest mat that fits the slot's box for photo i's frame — twin of templateSlotWidth()."""
    h = slot.get("h")
    if h is None or not h > 0:
        return slot["w"]
    photos = spec.get("photos") or []
    per = mat_height_per_width(str(spec.get("shape") or "4:3"), photo_frame(spec, photos[i] if i < len(photos) else None), aspect)
    return min(slot["w"], h / per)


def photo_frame(spec: dict[str, Any] | None, photo: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolved frame for a photo — twin of photoFrame()."""
    a = (spec or {}).get("frame") if isinstance((spec or {}).get("frame"), dict) else {}
    b = (photo or {}).get("frame") if isinstance((photo or {}).get("frame"), dict) else {}
    shape = b.get("shape") if b.get("shape") in FRAME_SHAPES else (a.get("shape") if a.get("shape") in FRAME_SHAPES else "polaroid")

    def _num(src: dict, key: str) -> float | None:
        v = src.get(key)
        if v is None or isinstance(v, bool):
            return None
        try:
            n = float(v)
        except (TypeError, ValueError):
            return None
        return n if math.isfinite(n) else None

    raw_w = _num(b, "width")
    if raw_w is None:
        raw_w = _num(a, "width")
    if raw_w is None:
        width = 4.5 if shape == "polaroid" else (0.0 if shape == "none" else 3.0)
    else:
        width = max(0.0, min(12.0, raw_w))
    color = b.get("color") if isinstance(b.get("color"), str) and len(b.get("color")) == 7 and b.get("color").startswith("#") else (
        a.get("color") if isinstance(a.get("color"), str) and len(a.get("color")) == 7 and a.get("color").startswith("#") else "#ffffff")
    raw_r = _num(b, "radius")
    if raw_r is None:
        raw_r = _num(a, "radius")
    radius = 12.0 if raw_r is None else max(0.0, min(50.0, raw_r))
    shadow = True
    if "shadow" in b:
        shadow = b.get("shadow") is not False
    elif "shadow" in a:
        shadow = a.get("shadow") is not False
    return {"shape": shape, "width": width, "color": color, "radius": radius, "shadow": shadow}


def frame_border_frac(frame: dict[str, Any]) -> float:
    if frame.get("shape") == "none":
        return 0.0
    return max(0.0, min(0.12, float(frame.get("width") or 0.0) / 100.0))


def mat_height(w: float, shape: str, aspect: float, frame: dict[str, Any] | None = None) -> float:
    """Mat height in % of frame height for a mat width of w % of frame width."""
    fr = photo_frame({"frame": frame} if frame else {}, None)
    border = frame_border_frac(fr) * w
    bottom = 0.205 * w if fr["shape"] == "polaroid" else border
    photo_w = max(1e-6, w - 2 * border)
    photo_h = photo_w / _photo_aspect(shape)
    return (photo_h + border + bottom) / aspect


def placements(spec: dict[str, Any], aspect: float) -> list[dict[str, float]]:
    """Resting placement of every photo — twin of placements() in collageCore.ts."""
    n = max(1, len(spec.get("photos") or []))
    seed = int(spec.get("seed") or 1)
    shape = str(spec.get("shape") or "4:3")
    out: list[dict[str, float]] = []

    def cells(count: int):
        cols = max(1, math.ceil(math.sqrt(count)))
        rows = math.ceil(count / cols)
        margin_x, margin_y = 7.0, 12.0
        return cols, rows, (100 - 2 * margin_x) / cols, (100 - 2 * margin_y) / rows

    def fit_width(cell_w: float, cell_h: float) -> float:
        k = 0.91 / _photo_aspect(shape) + 0.25
        return min(cell_w * 0.8, cell_h * 0.82 * aspect / k)

    layout = str(spec.get("layout") or "stack")
    # Per-photo size: each photo's mat is the layout width times its
    # multiplier (bigger photos overlap their neighbours — that is the point).
    def width_of(base: float, i: int) -> float:
        return base * photo_size(spec, i)

    if layout == "grid":
        cols, _, cell_w, cell_h = cells(n)
        w = fit_width(cell_w, cell_h)
        for i in range(n):
            col, row = i % cols, i // cols
            out.append({"cx": 7 + cell_w * (col + 0.5), "cy": 12 + cell_h * (row + 0.5), "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 10})
    elif layout == "scatter":
        cols, _, cell_w, cell_h = cells(n)
        w = fit_width(cell_w, cell_h) * 0.94
        for i in range(n):
            col, row = i % cols, i // cols
            cx = 7 + cell_w * (col + 0.28 + 0.44 * hash01(seed, i, 29))
            cy = 12 + cell_h * (row + 0.28 + 0.44 * hash01(seed, i, 31))
            out.append({"cx": cx, "cy": cy, "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 26})
    elif layout == "filmstrip":
        # A horizontal band of overlapping frames, like film frames edge to
        # edge — one row up to 5 photos, two rows beyond.
        rows = 1 if n <= 5 else 2
        per_row = math.ceil(n / rows)
        first_row = n - per_row * (rows - 1)
        k = 0.91 / _photo_aspect(shape) + 0.25
        for i in range(n):
            row = 0 if i < first_row else 1
            cols = first_row if row == 0 else per_row
            col = i if row == 0 else i - first_row
            cell_wr = (100 - 2 * 5) / cols
            cell_h = (100 - 2 * 12) / rows
            w = min(cell_wr * 1.1, cell_h * 0.82 * aspect / k)
            out.append({"cx": 5 + cell_wr * (col + 0.5), "cy": 12 + cell_h * (row + 0.5), "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 8})
    elif layout == "fan":
        # Cards fanned out from a point below the frame — each card tilts
        # along its spoke, like a hand of cards offered to the viewer.
        spoke = 46.0
        spread = min(48.0, 10 + 7 * n)
        a0 = 0.0 if n == 1 else (hash01(seed, 0, 19) - 0.5) * 10
        w = 36.0 if n <= 3 else (30.0 if n <= 6 else 26.0)
        for i in range(n):
            a = math.radians((0.0 if n == 1 else (spread * (i / (n - 1) - 0.5))) + a0)
            out.append({
                "cx": 50 + spoke * aspect * math.sin(a),
                "cy": 96 - spoke * math.cos(a),
                "w": width_of(w, i),
                "rot": math.degrees(a),
            })
    elif layout == "masonry":
        # Pinterest-style columns: seeded size variety, each photo stacked
        # into the shortest column (a single photo is simply centred).
        if n == 1:
            out.append({"cx": 50.0, "cy": 50.0, "w": width_of(40.0, 0), "rot": 0.0})
        else:
            cols = 2 if n <= 2 else (3 if n <= 9 else 4)
            margin_x = 6.0
            cell_w = (100 - 2 * margin_x) / cols
            k = 0.91 / _photo_aspect(shape) + 0.25
            base = cell_w * 0.88
            ws = [base * (0.82 + 0.36 * hash01(seed, i, 41)) * photo_size(spec, i) for i in range(n)]

            def simulate(scale: float):
                fills = [0.0] * cols
                res = []
                gap = 2.5 * scale
                for i in range(n):
                    c = 0
                    for j in range(1, cols):
                        if fills[j] < fills[c] - 1e-9:
                            c = j
                    mh = ws[i] * scale * k / aspect
                    res.append({"cx": margin_x + cell_w * (c + 0.5), "cy": 12 + fills[c] + gap / 2 + mh / 2})
                    fills[c] += gap + mh
                return res, max(fills)

            sim, max_fill = simulate(1.0)
            fit = 1.0
            if max_fill > 76:
                fit = 76 / max_fill
                sim, _ = simulate(fit)
            for i in range(n):
                out.append({"cx": sim[i]["cx"], "cy": sim[i]["cy"], "w": ws[i] * fit, "rot": (hash01(seed, i, 37) - 0.5) * 6})
    elif layout == "honeycomb":
        cols = n if n <= 2 else (3 if n <= 6 else 4)
        rows = math.ceil(n / cols)
        try:
            gap = float(spec.get("gap")) if spec.get("gap") is not None and not isinstance(spec.get("gap"), bool) else 1.6
        except (TypeError, ValueError):
            gap = 1.6
        gap = max(0.0, min(12.0, gap if math.isfinite(gap) else 1.6))
        cell_w = (100 - 8) / (cols + 0.5)
        cell_h = (100 - 16) / max(1, rows)
        k = 0.91 / _photo_aspect(shape) + 0.25
        w = min(cell_w - gap, cell_h * 0.82 * aspect / k)
        for i in range(n):
            row, col = divmod(i, cols)
            ox = (row % 2) * cell_w * 0.5
            out.append({"cx": 6 + ox + cell_w * (col + 0.5), "cy": 10 + cell_h * (row + 0.5),
                        "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 4})
    elif layout == "zigzag":
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)
        cell_w = (100 - 12) / cols
        cell_h = (100 - 18) / rows
        w = fit_width(cell_w, cell_h) * 0.92
        for i in range(n):
            col, row = i % cols, i // cols
            ox = (row % 2) * cell_w * 0.28
            sign = 1 if (row + col) % 2 else -1
            out.append({"cx": 6 + ox + cell_w * (col + 0.5), "cy": 10 + cell_h * (row + 0.5),
                        "w": width_of(w, i), "rot": sign * (6 + 4 * hash01(seed, i, 37))})
    elif layout == "arc":
        w = 28.0 if n <= 4 else 22.0
        for i in range(n):
            t = 0.5 if n == 1 else i / (n - 1)
            a = (12 + 156 * t) * math.pi / 180
            out.append({"cx": 50 + 40 * math.cos(a), "cy": 62 - 34 * math.sin(a),
                        "w": width_of(w, i), "rot": 90 - a * 180 / math.pi})
    elif layout == "photowall":
        cols = max(1, math.ceil(math.sqrt(n)))
        rows = math.ceil(n / cols)
        try:
            gap = float(spec.get("gap")) if spec.get("gap") is not None and not isinstance(spec.get("gap"), bool) else 0.7
        except (TypeError, ValueError):
            gap = 0.7
        gap = max(0.0, min(12.0, gap if math.isfinite(gap) else 0.7))
        cell_w = (100 - gap) / cols
        cell_h = (100 - gap) / rows
        k = 0.91 / _photo_aspect(shape) + 0.25
        w = min(cell_w - gap, cell_h * aspect / k)
        for i in range(n):
            col, row = i % cols, i // cols
            out.append({"cx": gap / 2 + cell_w * (col + 0.5), "cy": gap / 2 + cell_h * (row + 0.5),
                        "w": width_of(w, i), "rot": 0.0})
    elif layout == "booth":
        w = min(22.0, 90.0 / max(1, n))
        for i in range(n):
            out.append({"cx": 50.0, "cy": 14 + (72 / max(1, n)) * (i + 0.5),
                        "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 2})
    elif layout == "silhouette":
        w = 22.0 if n <= 4 else (16.0 if n <= 8 else 12.0)
        for i in range(n):
            t = (i / max(1, n)) * 2 * math.pi
            hx = 16 * math.sin(t) ** 3
            hy = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            out.append({
                "cx": 50 + hx * 2.1 + (hash01(seed, i, 29) - 0.5) * 3,
                "cy": 48 - hy * 1.7 + (hash01(seed, i, 31) - 0.5) * 3,
                "w": width_of(w, i),
                "rot": (hash01(seed, i, 37) - 0.5) * 14,
            })
    elif layout == "cube":
        faces = [
            {"cx": 48, "cy": 48, "w": 34, "rot": 0},
            {"cx": 70, "cy": 44, "w": 20, "rot": 8},
            {"cx": 48, "cy": 28, "w": 28, "rot": -6},
        ]
        for i in range(n):
            if i < 3:
                out.append({"cx": faces[i]["cx"], "cy": faces[i]["cy"], "w": width_of(faces[i]["w"], i), "rot": faces[i]["rot"]})
            else:
                k = i - 3
                m = n - 3
                out.append({"cx": 18 + (64 / max(1, m)) * (k + 0.5), "cy": 84, "w": width_of(16, i),
                            "rot": (hash01(seed, i, 37) - 0.5) * 6})
    elif layout == "free":
        w = 42.0 if n <= 3 else (34.0 if n <= 6 else 30.0)
        photos = spec.get("photos") or []
        for i in range(n):
            p = photos[i] if i < len(photos) else {}
            try:
                cx = float(p.get("cx")) if p.get("cx") is not None and not isinstance(p.get("cx"), bool) else 50.0
            except (TypeError, ValueError):
                cx = 50.0
            try:
                cy = float(p.get("cy")) if p.get("cy") is not None and not isinstance(p.get("cy"), bool) else 50.0
            except (TypeError, ValueError):
                cy = 50.0
            try:
                rot = float(p.get("rot")) if p.get("rot") is not None and not isinstance(p.get("rot"), bool) else 0.0
            except (TypeError, ValueError):
                rot = 0.0
            if not math.isfinite(cx):
                cx = 50.0
            if not math.isfinite(cy):
                cy = 50.0
            if not math.isfinite(rot):
                rot = 0.0
            out.append({"cx": max(0.0, min(100.0, cx)), "cy": max(0.0, min(100.0, cy)), "w": width_of(w, i), "rot": rot})
    elif layout == "template":
        # Each mat is scaled down to fit its slot's box (width and height), so
        # the mosaic keeps its rows and columns whatever the photo shape or
        # frame style; the per-photo size multiplier still applies on top.
        slots = _template_slots(spec.get("template"), n)
        for i in range(n):
            out.append({"cx": slots[i]["cx"], "cy": slots[i]["cy"], "w": width_of(_template_slot_width(slots[i], spec, i, aspect), i), "rot": slots[i]["rot"]})
    else:  # stack
        w = 42.0 if n <= 3 else (34.0 if n <= 6 else 30.0)
        for i in range(n):
            if i == 0:
                out.append({"cx": 50.0, "cy": 46.0, "w": width_of(w, i), "rot": (hash01(seed, i, 17) - 0.5) * 22})
                continue
            ang = hash01(seed, i, 11) * 2 * math.pi
            r = 5 + hash01(seed, i, 13) * 8
            out.append({
                "cx": 50 + math.cos(ang) * r * 0.9,
                "cy": 46 + math.sin(ang) * r / aspect,
                "w": width_of(w, i),
                "rot": (hash01(seed, i, 17) - 0.5) * 22,
            })
    return out


def default_delay(n: int, i: int) -> float:
    """Auto-stagger default for photo i's delay (seconds); photo 0 appears
    0.15 s after the hold starts, the rest spread over at most ~2.4 s."""
    if i <= 0:
        return 0.15
    return min(0.32, 2.4 / (n - 1)) if n > 1 else 0.0


def photo_delay(spec: dict[str, Any], i: int) -> float:
    """Photo i's delay in seconds — stored value or the auto default, 0..30."""
    photos = spec.get("photos") or []
    raw = photos[i].get("delay") if i < len(photos) else None
    d = float("nan")
    if raw is not None and not isinstance(raw, bool) and not (isinstance(raw, str) and raw.strip() == ""):
        try:
            d = float(raw)
        except (TypeError, ValueError):
            d = float("nan")
    if not math.isfinite(d):
        d = default_delay(len(photos), i)
    return max(0.0, min(30.0, d))


def photo_size(spec: dict[str, Any], i: int) -> float:
    """Photo i's mat size multiplier — stored value (clamped to 0.5..1.5) or 1.

    When randomSize is on and this photo has no explicit size, a seeded value
    between randomSizeMin and randomSizeMax is used (twin of photoSize()).
    """
    photos = spec.get("photos") or []
    raw = photos[i].get("size") if i < len(photos) else None
    v = float("nan")
    if raw is not None and not isinstance(raw, bool) and not (isinstance(raw, str) and raw.strip() == ""):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = float("nan")
    if not math.isfinite(v) and spec.get("randomSize") is True:
        try:
            lo = float(spec.get("randomSizeMin")) if spec.get("randomSizeMin") is not None and not isinstance(spec.get("randomSizeMin"), bool) else 0.7
        except (TypeError, ValueError):
            lo = 0.7
        try:
            hi = float(spec.get("randomSizeMax")) if spec.get("randomSizeMax") is not None and not isinstance(spec.get("randomSizeMax"), bool) else 1.3
        except (TypeError, ValueError):
            hi = 1.3
        if not math.isfinite(lo):
            lo = 0.7
        if not math.isfinite(hi):
            hi = 1.3
        lo = max(0.5, min(1.5, lo))
        hi = max(0.5, min(1.5, hi))
        a, b = min(lo, hi), max(lo, hi)
        v = a + (b - a) * hash01(int(spec.get("seed") or 1), i, 41)
    if not math.isfinite(v):
        v = 1.0
    return max(0.5, min(1.5, v))


def next_beat(beats: list[float], t: float) -> float | None:
    """The next stored beat at or after time t (hold clock), or None."""
    for b in beats:
        if b >= t - 1e-9:
            return b
    return None


def photo_start(spec: dict[str, Any], i: int, lead_in: float = 0.0) -> float:
    """When photo i starts its entrance: the sum of the delays of photos 0..i
    plus the lead-in handle — twin of photoStart() in collageCore.ts. With
    beat sync on, the nominal time rolls forward to the next stored beat."""
    hold = 0.0
    for k in range(i + 1):
        hold += photo_delay(spec, k)
    if spec.get("beatSync") is True:
        beats = spec.get("beats")
        beats = [b for b in beats if isinstance(b, (int, float)) and not isinstance(b, bool)] if isinstance(beats, list) else []
        snapped = next_beat(beats, hold)
        if snapped is not None:
            hold = snapped
    return lead_in + hold


# How long an entrance runs until the photo is fully at rest.
ENTRANCE_LENGTH = {
    "drop": 0.55, "pop": 0.5, "swing": 2.0, "flip": 0.45, "none": 0.0,
    "fade": 0.45, "slide": 0.5, "rise": 0.5, "tumble": 0.6, "zoom": 0.55,
    "fold": 0.55, "glitch": 0.5, "ink": 0.55, "brush": 0.5,
}
LAYOUTS = ("stack", "grid", "scatter", "filmstrip", "fan", "masonry", "free", "template",
           "honeycomb", "zigzag", "arc", "photowall", "booth", "silhouette", "cube")
ANIMS = ("drop", "pop", "swing", "flip", "none", "fade", "slide", "rise", "tumble",
         "zoom", "fold", "glitch", "ink", "brush")
FRAME_SHAPES = ("polaroid", "none", "rect", "rounded", "circle", "oval", "heart", "star",
                "diamond", "hexagon", "triangle", "octagon", "cloud", "arch", "ticket")

# Exits - how the photos leave after the hold. Twin of collageCore.ts.
EXIT_LENGTH = {"sweep": 0.45, "deal": 0.32, "shuffle": 0.4}
EXIT_STAGGER = {"sweep": 0.05, "deal": 0.22, "shuffle": 0.1}


def exit_mode(spec: dict[str, Any]) -> str:
    """The spec's exit mode, sanitised."""
    return spec.get("exit") if spec.get("exit") in ("sweep", "deal", "shuffle") else "none"


def exit_offset(spec: dict[str, Any], i: int) -> float:
    """When photo i starts leaving, relative to the end of the hold. 'deal'
    clears the top of the pile first (photo n-1 leaves first)."""
    mode = exit_mode(spec)
    if mode == "none":
        return 0.0
    n = len(spec.get("photos") or [])
    if mode == "deal":
        return EXIT_STAGGER["deal"] * max(0, n - 1 - i)
    return EXIT_STAGGER[mode] * i


def exit_total(spec: dict[str, Any]) -> float:
    """Total seconds the exit adds to the slide (longest offset + fly)."""
    mode = exit_mode(spec)
    if mode == "none":
        return 0.0
    n = len(spec.get("photos") or [])
    if not n:
        return 0.0
    return EXIT_LENGTH[mode] + max(exit_offset(spec, 0), exit_offset(spec, n - 1))


def _round2(v: float) -> float:
    # Same rounding as Math.round(v*100)/100 in the TS twin (floor(x+.5)),
    # NOT Python's round() which rounds half to even.
    return math.floor(v * 100 + 0.5) / 100


def base_duration(spec: dict[str, Any]) -> float:
    """The slide duration before any exit: last photo's start + its entrance
    + the hold after it ('none' photos are all on screen from the first
    frame, so just the hold). 0 for an empty collage."""
    photos = spec.get("photos") or []
    if not photos:
        return 0.0
    try:
        hold = float(spec.get("hold"))
    except (TypeError, ValueError):
        hold = 2.0
    if not math.isfinite(hold):
        hold = 2.0
    hold = max(0.0, min(120.0, hold))
    anim = str(spec.get("animation") or "drop")
    if anim == "none":
        return _round2(hold)
    last = photo_start(spec, len(photos) - 1, 0.0)
    return _round2(last + ENTRANCE_LENGTH.get(anim, 0.0) + hold)


def collage_duration(spec: dict[str, Any]) -> float:
    """The slide duration implied by the photo timings: entrances + hold +
    the exit fly at the end. This is what the editor writes into the slide."""
    if not (spec.get("photos") or []):
        return 0.0
    return _round2(base_duration(spec) + exit_total(spec))


def bg_fit_filter(fit: str | None, width: int, height: int) -> str:
    """How a background picture fills the frame — twin of bgFitStyle()."""
    f = fit if fit in ("fill", "fit", "stretch", "tile", "center", "span") else "fill"
    if f == "fit":
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    if f == "stretch":
        return f"scale={width}:{height},setsar=1"
    if f == "tile":
        return (f"loop=loop=63:size=1,tile=8x8,crop={width}:{height}:0:0,setsar=1")
    if f == "center":
        return (f"scale='min(iw,{width})':'min(ih,{height})':force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    # fill and span: cover the frame, cropping overflow
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"


def bg_blur_css_px(blur: float) -> float:
    """Background blur as a CSS blur() radius in px on the 1920-wide stage."""
    v = float(blur) if isinstance(blur, (int, float)) and math.isfinite(blur) else 0.0
    return 2.0 + 58.0 * _clamp01(v)


def bg_blur_radius(blur: float) -> int:
    """Background blur as an FFmpeg boxblur luma radius for the same strength."""
    return max(1, round(bg_blur_css_px(blur) / 2))


# Depth push-back tuning — mirrored as DEPTH in collageCore.ts.
DEPTH = {"scale": 0.06, "dim": 0.11, "max": 4.0, "delay": 0.4, "span": 0.35}


def push_depth(spec: dict[str, Any], i: int, t: float, lead_in: float = 0.0) -> float:
    """Depth push-back units on photo i at time t: every photo that lands
    after it pushes it back a little (eased, capped) — twin of pushDepth().
    0 unless the spec asks for depth and the animation can show it."""
    if spec.get("depth") is not True:
        return 0.0
    anim = str(spec.get("animation") or "drop")
    if anim not in ("drop", "pop", "flip"):
        return 0.0
    e = ENTRANCE_LENGTH[anim]
    n = len(spec.get("photos") or [])
    p = 0.0
    for j in range(i + 1, n):
        tj = photo_start(spec, j, lead_in)
        q = _clamp01((t - (tj + DEPTH["delay"] * e)) / DEPTH["span"])
        p += q * q * (3 - 2 * q)
    return min(DEPTH["max"], p)


def camera_state(spec: dict[str, Any], t: float, lead_in: float = 0.0, aspect: float = 16 / 9) -> dict[str, float]:
    """Virtual camera at segment time t — twin of cameraState() in collageCore.ts.
    Returns the window zoom and centre (% of frame); 'pan' drifts across,
    the zoom family centres on the last photo's anchor, clamped inside the
    frame. The FFmpeg zoompan chain (camera_filter) and the preview's CSS
    transform are two views of these numbers."""
    mode = spec.get("camera") if spec.get("camera") in ("pan", "zoom", "telescope", "droste") else "none"
    if mode == "none":
        return {"z": 1.0, "cx": 50.0, "cy": 50.0}
    d = max(0.2, collage_duration(spec))
    raw = _clamp01((t - lead_in) / d)
    p = raw * raw * (3 - 2 * raw)
    if mode == "pan":
        return {"z": 1.09, "cx": 54 - 8 * p, "cy": 50.0}
    pls = placements(spec, aspect)
    a = pls[-1] if pls else {"cx": 50.0, "cy": 50.0}
    if mode == "zoom":
        z = 1 + 0.35 * p
    elif mode == "telescope":
        z = 1 + 0.9 * p
    else:
        z = 1 + 1.1 * (p ** 1.4)
    half = 50.0 / z
    return {
        "z": z,
        "cx": min(100 - half, max(half, a["cx"])),
        "cy": min(100 - half, max(half, a["cy"])),
    }


# Virtual-camera supersampling: zoompan snaps its window to whole pixels of
# ITS INPUT, so the composed scene is scaled up S× first and zoompan samples
# that finer grid — the camera then moves in steps of 1/S output pixel
# instead of whole (or, on 4:2:0, even-numbered) pixels. S is the largest
# factor whose S×-scaled frame stays under the pixel budget: 720p → 4,
# 1080p → 3, 1440p → 2, 4K → 1 (a 4K pixel is already a quarter the size).
CAMERA_SUPERSAMPLE_MAX = 4
CAMERA_SUPERSAMPLE_BUDGET = 20_000_000


def camera_supersample(width: int, height: int) -> int:
    """Supersampling factor the virtual camera uses at this output size."""
    for s in range(CAMERA_SUPERSAMPLE_MAX, 1, -1):
        if width * s * height * s <= CAMERA_SUPERSAMPLE_BUDGET:
            return s
    return 1


def camera_filter(spec: dict[str, Any], width: int, height: int, fps: float, lead_in: float,
                  aspect: float | None = None) -> str | None:
    """The FFmpeg filter chain for the virtual camera over the composed scene,
    or None when the collage has no camera.

    zoompan is the one stock filter that re-crops AND rescales every frame.
    (crop cannot do it: its w/h expressions are evaluated once, at init, with
    t = NaN — a crop=w='…t…' camera silently renders a static full frame.)
    The zoom and window centre follow camera_state()'s curves, driven by the
    input timestamp ``it`` so the lead-in shift and smoothstep match the
    preview. Before zoompan the scene is supersampled (camera_supersample)
    and converted to 4:4:4 — zoompan aligns 4:2:0 windows to even pixels —
    so the camera moves on a sub-pixel grid instead of stuttering.
    """
    cam = spec.get("camera") if spec.get("camera") in ("pan", "zoom", "telescope", "droste") else "none"
    if cam == "none":
        return None
    pls = placements(spec, width / height if aspect is None else aspect)
    if not pls:
        return None
    d = max(0.2, collage_duration(spec))
    raw_p = f"min(max((it-{_n(lead_in)})/{_n(d)},0),1)"
    p = f"({raw_p}*{raw_p}*(3-2*{raw_p}))"
    if cam == "pan":
        zexpr = "1.09"
        cx, cy = f"(54-8*{p})", "50"
    else:
        if cam == "zoom":
            zexpr = f"1+0.35*{p}"
        elif cam == "telescope":
            zexpr = f"1+0.9*{p}"
        else:
            zexpr = f"1+1.1*pow({p},1.4)"
        anchor = pls[-1]
        cx, cy = _n(anchor["cx"]), _n(anchor["cy"])
    # zoompan clamps x/y into [0, iw-iw/zoom] itself, which is exactly the
    # twin's "window stays inside the frame" clamp on the centre.
    ss = camera_supersample(width, height)
    up = f"scale=iw*{ss}:ih*{ss}:flags=bicubic," if ss > 1 else ""
    return (
        f"{up}format=yuv444p,"
        f"zoompan=z='{zexpr}':x='{cx}*iw/100-iw/(2*zoom)':y='{cy}*ih/100-ih/(2*zoom)'"
        f":d=1:s={width}x{height}:fps={_n(fps)},setsar=1"
    )


def photo_state(spec: dict[str, Any], i: int, t: float, lead_in: float = 0.0, aspect: float = 16 / 9) -> dict[str, float]:
    """Animated offsets of photo i at segment time t — twin of photoState()."""
    t0 = photo_start(spec, i, lead_in)
    push = push_depth(spec, i, t, lead_in)
    anim = str(spec.get("animation") or "drop")
    if anim == "drop":
        q = _clamp01((t - t0) / 0.55)
        settle = (1 - q) ** 3
        st = {"dx": 0.0, "dy": -26 * settle, "rot": -7 * settle, "scale": 1 - DEPTH["scale"] * push, "scaleX": 1.0, "alpha": _clamp01((t - t0) / 0.22), "dim": DEPTH["dim"] * push}
    elif anim == "pop":
        q = _clamp01((t - t0) / 0.5)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": (0.55 + 0.45 * back) * (1 - DEPTH["scale"] * push), "scaleX": 1.0, "alpha": _clamp01((t - t0) / 0.18), "dim": DEPTH["dim"] * push}
    elif anim == "flip":
        # A card flipping open around its vertical axis: edge-on (4% width)
        # with an outBack overshoot, straightening as it settles.
        q = _clamp01((t - t0) / 0.45)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": -4 * (1 - q), "scale": 1 - DEPTH["scale"] * push, "scaleX": max(0.04, 0.04 + 0.96 * back), "alpha": _clamp01((t - t0) / 0.15), "dim": DEPTH["dim"] * push}
    elif anim == "swing":
        tau = max(0.0, t - t0)
        rot = 14 * math.exp(-1.3 * tau) * math.cos(2 * math.pi * tau / 1.6)
        st = {"dx": 0.0, "dy": 0.0, "rot": rot, "scale": 1.0, "scaleX": 1.0, "alpha": _clamp01(tau / 0.15), "dim": 0.0}
    elif anim == "fade":
        q = _clamp01((t - t0) / 0.45)
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": q * q * (3 - 2 * q), "dim": 0.0}
    elif anim == "slide":
        q = _clamp01((t - t0) / 0.5)
        settle = (1 - q) ** 3
        st = {"dx": -28 * settle, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": _clamp01((t - t0) / 0.18), "dim": 0.0}
    elif anim == "rise":
        q = _clamp01((t - t0) / 0.5)
        settle = (1 - q) ** 3
        st = {"dx": 0.0, "dy": 22 * settle, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": _clamp01((t - t0) / 0.18), "dim": 0.0}
    elif anim == "tumble":
        q = _clamp01((t - t0) / 0.6)
        settle = (1 - q) ** 3
        st = {"dx": 10 * settle, "dy": -18 * settle, "rot": 28 * settle, "scale": 1 - 0.15 * settle, "scaleX": 1.0, "alpha": _clamp01((t - t0) / 0.16), "dim": 0.0}
    elif anim == "zoom":
        q = _clamp01((t - t0) / 0.55)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 0.2 + 0.8 * back, "scaleX": 1.0, "alpha": _clamp01((t - t0) / 0.16), "dim": 0.0}
    elif anim == "fold":
        q = _clamp01((t - t0) / 0.55)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": max(0.04, 0.04 + 0.96 * back), "alpha": _clamp01((t - t0) / 0.14), "dim": 0.0}
    elif anim == "glitch":
        q = _clamp01((t - t0) / 0.5)
        j = (1 - q) * (1 - q)
        seed = int(spec.get("seed") or 1)
        st = {"dx": (hash01(seed, i, 61) - 0.5) * 10 * j, "dy": (hash01(seed, i, 63) - 0.5) * 6 * j,
              "rot": (hash01(seed, i, 65) - 0.5) * 8 * j, "scale": 1.0,
              "scaleX": 1 + (hash01(seed, i, 67) - 0.5) * 0.18 * j, "alpha": _clamp01((t - t0) / 0.12), "dim": 0.15 * j}
    elif anim == "ink":
        q = _clamp01((t - t0) / 0.55)
        blob = q * q * (3 - 2 * q)
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 0.35 + 0.65 * blob, "scaleX": 1.0, "alpha": blob, "dim": 0.0}
    elif anim == "brush":
        q = _clamp01((t - t0) / 0.5)
        settle = (1 - q) ** 3
        st = {"dx": -36 * settle, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": max(0.12, 1 - 0.55 * settle),
              "alpha": _clamp01((t - t0) / 0.16), "dim": 0.0}
    else:
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": 1.0, "dim": 0.0}
    if spec.get("kenBurns") is True:
        land = t0 + ENTRANCE_LENGTH.get(anim, 0.0)
        dur = collage_duration(spec)
        kb = _clamp01((t - land) / max(0.8, dur * 0.7))
        seed = int(spec.get("seed") or 1)
        st["scale"] *= 1 + 0.08 * kb
        st["dx"] += (hash01(seed, i, 51) - 0.5) * 5 * kb
        st["dy"] += (hash01(seed, i, 53) - 0.5) * 3.5 * kb
    if spec.get("sway") is True and (t - t0) > ENTRANCE_LENGTH.get(anim, 0.0) * 0.7:
        st["rot"] += 1.5 * math.sin(t * 2.15 + i * 0.9)
    # Exit: after the hold (and every entrance) the photos leave the frame.
    mode = exit_mode(spec)
    if mode != "none":
        seed = int(spec.get("seed") or 1)
        te0 = lead_in + base_duration(spec) + exit_offset(spec, i)
        qe = _clamp01((t - te0) / EXIT_LENGTH[mode])
        if qe > 0:
            ease = qe * qe
            if mode == "sweep":
                pl = placements(spec, aspect)[i]
                vx = pl["cx"] - 50
                vy = pl["cy"] - 50
                length = math.hypot(vx, vy)
                dirx = 0.0 if length < 1e-6 else vx / length
                diry = -1.0 if length < 1e-6 else vy / length
                st["dx"] += 90 * dirx * ease
                st["dy"] += 90 * diry * ease
                st["rot"] += (hash01(seed, i, 71) - 0.5) * 20 * qe
            elif mode == "deal":
                st["dx"] += 90 * ease
                st["dy"] += -6 * math.sin(math.pi * qe)
                st["rot"] += 25 * qe
            else:
                a = hash01(seed, i, 73) * 2 * math.pi
                st["dx"] += 75 * math.cos(a) * ease
                st["dy"] += 55 * math.sin(a) * ease
                st["rot"] += (hash01(seed, i, 75) - 0.5) * 40 * qe
            st["alpha"] *= 1 - _clamp01((qe - 0.75) / 0.25)
    return st


def pin_anchor(spec: dict[str, Any]) -> bool:
    return str(spec.get("animation") or "drop") == "swing"


def normalize_collage(item: dict[str, Any]) -> dict[str, Any] | None:
    """The item's valid collage spec, or None when it has no usable photos.

    Two shapes are accepted: a nested `collage` object (the editor's shape —
    a title frame carrying photos), and the documented top-level shape where
    a `type: 'collage'` item carries its fields directly. When both are
    present the nested spec wins.
    """
    raw = item.get("collage") if isinstance(item.get("collage"), dict) else None
    if raw is None and item.get("type") == "collage":
        raw = item
    if raw is None:
        return None
    photos, seen = [], set()
    for p in raw.get("photos") or []:
        if not isinstance(p, dict):
            continue
        path = str(p.get("path") or "")
        if not path or path in seen:
            continue
        seen.add(path)
        delay = p.get("delay")
        try:
            delay = float(delay) if delay is not None and not isinstance(delay, bool) else None
        except (TypeError, ValueError):
            delay = None
        if delay is None or not math.isfinite(delay):
            delay = None
        else:
            delay = max(0.0, min(30.0, delay))
        entry = {"path": path, "name": str(p.get("name") or "")}
        if delay is not None:
            entry["delay"] = delay
        size = p.get("size")
        try:
            size = float(size) if size is not None and not isinstance(size, bool) else None
        except (TypeError, ValueError):
            size = None
        if size is not None and math.isfinite(size):
            entry["size"] = max(0.5, min(1.5, size))
        for key, lo, hi, fallback in (("cx", 0.0, 100.0, None), ("cy", 0.0, 100.0, None), ("rot", -45.0, 45.0, None)):
            try:
                v = float(p.get(key)) if p.get(key) is not None and not isinstance(p.get(key), bool) else None
            except (TypeError, ValueError):
                v = None
            if v is not None and math.isfinite(v):
                entry[key] = max(lo, min(hi, v))
        if isinstance(p.get("filter"), str) and p.get("filter"):
            entry["filter"] = p["filter"]
        try:
            amt = float(p.get("filterAmount")) if p.get("filterAmount") is not None and not isinstance(p.get("filterAmount"), bool) else None
        except (TypeError, ValueError):
            amt = None
        if amt is not None and math.isfinite(amt):
            entry["filterAmount"] = _clamp01(amt)
        if isinstance(p.get("filterAdjust"), dict):
            entry["filterAdjust"] = p["filterAdjust"]
        if isinstance(p.get("crop"), dict):
            entry["crop"] = p["crop"]
        if isinstance(p.get("frame"), dict):
            entry["frame"] = photo_frame({"frame": p["frame"]}, None)
        photos.append(entry)
        if len(photos) >= MAX_COLLAGE_PHOTOS:
            break
    if not photos:
        return None
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else "stack"
    animation = raw.get("animation") if raw.get("animation") in ANIMS else "drop"
    exit_ = raw.get("exit") if raw.get("exit") in ("sweep", "deal", "shuffle") else None
    camera = raw.get("camera") if raw.get("camera") in ("pan", "zoom", "telescope", "droste") else None
    shape = raw.get("shape") if raw.get("shape") in ("4:3", "square", "3:4", "16:9", "9:16", "3:2", "2:3") else "4:3"
    try:
        seed = int(raw.get("seed") or 1)
    except (TypeError, ValueError):
        seed = 1
    # Beat list: finite numbers, clamped, rounded to ms, ascending, de-duped.
    beats_in = []
    if isinstance(raw.get("beats"), list):
        for b in raw["beats"]:
            try:
                v = float(b) if b is not None and not isinstance(b, bool) else None
            except (TypeError, ValueError):
                v = None
            if v is not None and math.isfinite(v):
                beats_in.append(math.floor(max(0.0, min(600.0, v)) * 1000 + 0.5) / 1000)
    beats_in.sort()
    beats = [b for k, b in enumerate(beats_in) if k == 0 or b - beats_in[k - 1] > 0.001][:1200]
    hold = raw.get("hold")
    try:
        hold = float(hold) if hold is not None and not isinstance(hold, bool) else None
    except (TypeError, ValueError):
        hold = None
    if hold is None or not math.isfinite(hold):
        hold = None
    else:
        hold = max(0.0, min(120.0, hold))
    blur = raw.get("backgroundBlur")
    try:
        blur = float(blur) if blur is not None and not isinstance(blur, bool) else None
    except (TypeError, ValueError):
        blur = None
    if blur is None or not math.isfinite(blur):
        blur = None
    else:
        blur = _clamp01(blur)
    bg = raw.get("backgroundImage")
    bg = bg if isinstance(bg, str) and bg else None
    spec = {"photos": photos, "layout": layout, "animation": animation, "shape": shape, "seed": seed}
    if hold is not None:
        spec["hold"] = hold
    if raw.get("beatSync") is True:
        spec["beatSync"] = True
    if beats:
        spec["beats"] = beats
    if raw.get("depth") is True:
        spec["depth"] = True
    if exit_ is not None:
        spec["exit"] = exit_
    if camera is not None:
        spec["camera"] = camera
    if bg is not None:
        spec["backgroundImage"] = bg
    if blur is not None:
        spec["backgroundBlur"] = blur
    fit = raw.get("backgroundFit")
    if fit in ("fill", "fit", "stretch", "tile", "center", "span"):
        spec["backgroundFit"] = fit
    look = raw.get("backgroundLook")
    if isinstance(look, dict):
        spec["backgroundLook"] = look
    if raw.get("randomSize") is True:
        spec["randomSize"] = True
        for key in ("randomSizeMin", "randomSizeMax"):
            try:
                v = float(raw.get(key)) if raw.get(key) is not None and not isinstance(raw.get(key), bool) else None
            except (TypeError, ValueError):
                v = None
            if v is not None and math.isfinite(v):
                spec[key] = max(0.5, min(1.5, v))
    tid = raw.get("template")
    if isinstance(tid, str) and any(t["id"] == tid for t in COLLAGE_TEMPLATES):
        spec["template"] = tid
    if isinstance(raw.get("frame"), dict):
        spec["frame"] = photo_frame({"frame": raw["frame"]}, None)
    try:
        gap = float(raw.get("gap")) if raw.get("gap") is not None and not isinstance(raw.get("gap"), bool) else None
    except (TypeError, ValueError):
        gap = None
    if gap is not None and math.isfinite(gap):
        spec["gap"] = max(0.0, min(12.0, gap))
    if raw.get("kenBurns") is True:
        spec["kenBurns"] = True
    if raw.get("sway") is True:
        spec["sway"] = True
    if raw.get("stickers") in ("tape", "pin", "mix"):
        spec["stickers"] = raw["stickers"]
    return spec


# ---------------------------------------------------------------------------
# FFmpeg composite graph
# ---------------------------------------------------------------------------

def _n(v: float) -> str:
    """Format a number for an FFmpeg expression (no exponent notation)."""
    s = f"{float(v):.4f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def collage_inputs(settings, item: dict[str, Any]) -> list[str]:
    """Absolute source paths of the collage's photos (validated against the
    mounted roots). Raises when a photo is missing."""
    spec = normalize_collage(item)
    if spec is None:
        return []
    out: list[str] = []
    for i, p in enumerate(spec["photos"]):
        src = source_path(settings, {"path": p["path"], "name": p.get("name", "")})
        if not src.is_file():
            raise FileNotFoundError(f"Collage photo {i + 1} is missing: {p['path']}")
        out.append(str(src))
    return out


def collage_background(settings, item: dict[str, Any]) -> Path | None:
    """Absolute path of the collage's background picture, or None when the
    collage uses the plain colour bed. Raises when the picture is set but
    missing — the render must fail with the reason, not produce a black bed."""
    spec = normalize_collage(item)
    if spec is None:
        return None
    bg = str(spec.get("backgroundImage") or "")
    if not bg:
        return None
    src = source_path(settings, {"path": bg, "name": ""})
    if not src.is_file():
        raise FileNotFoundError(f"Collage background picture is missing: {bg}")
    return src


def _shape_mask(fr: dict[str, Any], w: int, h: int) -> str:
    """Alpha geq that clips the mat to a decorative shape. Twin of frameClipPath()."""
    shape = fr.get("shape") or "polaroid"
    if shape in ("polaroid", "rect", "none"):
        return ""
    nx, ny = "(2*X/W-1)", "(1-2*Y/H)"
    if shape == "rounded":
        r = max(2, int(round((fr.get("radius") or 12) / 100 * min(w, h))))
        expr = (f"if(between(X,{r},W-{r})*between(Y,0,H)+between(Y,{r},H-{r})*between(X,0,W),"
                f"alpha(X,Y),if(lt(hypot(X-{r},Y-{r}),{r})+lt(hypot(X-(W-{r}),Y-{r}),{r})"
                f"+lt(hypot(X-{r},Y-(H-{r})),{r})+lt(hypot(X-(W-{r}),Y-(H-{r})),{r}),alpha(X,Y),0))")
    elif shape == "circle":
        expr = f"if(lt(hypot(X-W/2,Y-H/2),min(W,H)/2),alpha(X,Y),0)"
    elif shape == "oval":
        expr = f"if(lt(pow((X-W/2)/(W/2),2)+pow((Y-H/2)/(H*0.42),2),1),alpha(X,Y),0)"
    elif shape == "diamond":
        expr = f"if(lt(abs({nx})+abs({ny}),1),alpha(X,Y),0)"
    elif shape == "hexagon":
        expr = f"if(lt(max(abs({nx}),abs({nx})*0.5+abs({ny})*0.866),0.92),alpha(X,Y),0)"
    elif shape == "triangle":
        expr = f"if(gte(Y,H*0.08)*lte(Y,H*0.92)*gte(X,W/2-(Y-H*0.08)/(H*0.84)*W/2)*lte(X,W/2+(Y-H*0.08)/(H*0.84)*W/2),alpha(X,Y),0)"
    elif shape == "octagon":
        expr = f"if(lt(max(abs({nx}),abs({ny}),(abs({nx})+abs({ny}))*0.707),0.92),alpha(X,Y),0)"
    elif shape == "star":
        # 5-point star via polar radius; good enough at collage sizes.
        expr = (f"if(lt(hypot({nx},{ny}),0.38+0.22*abs(cos(5*atan2({ny},{nx})))),alpha(X,Y),0)")
    elif shape == "heart":
        expr = (f"if(lt(pow(pow({nx}*1.1,2)+pow({ny}*1.15+0.15,2)-1,3)"
                f"-pow({nx}*1.1,2)*pow({ny}*1.15+0.15,3),0),alpha(X,Y),0)")
    elif shape == "cloud":
        expr = (f"if(lt(hypot(X-W*0.32,Y-H*0.52),H*0.34)+lt(hypot(X-W*0.68,Y-H*0.48),H*0.32)"
                f"+lt(hypot(X-W*0.5,Y-H*0.62),H*0.36),alpha(X,Y),0)")
    elif shape == "arch":
        expr = f"if(gte(Y,H/2)*between(X,W*0.08,W*0.92)+lt(hypot(X-W/2,Y-H/2),W*0.42)*lte(Y,H/2),alpha(X,Y),0)"
    elif shape == "ticket":
        expr = (f"if(between(X,W*0.06,W*0.94)*between(Y,0,H)"
                f"+between(Y,H*0.12,H*0.88)*between(X,0,W)"
                f"-lt(hypot(X,Y-H/2),H*0.08)-lt(hypot(X-W,Y-H/2),H*0.08),alpha(X,Y),0)")
    else:
        return ""
    return f",geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{expr}'"


def collage_graph(item: dict[str, Any], width: int, height: int, fps: float,
                  duration: float, lead_in: float, first_input: int, base_label: str) -> tuple[list[str], str] | None:
    """Filter-graph lines that composite the collage photos onto ``base_label``.

    Returns (lines, last_label) — each photo's sprite (mat + soft shadow) is
    overlaid in z-order; `last_label` is the composed result — or None when
    the item has no collage. Photos are matted (white polaroid border +
    caption strip), given a soft shadow, and animated with the same curves
    the preview uses (drop / pop / swing / none).
    """
    spec = normalize_collage(item)
    if spec is None:
        return None
    aspect = width / height
    pls = placements(spec, aspect)
    pin = pin_anchor(spec)
    anim = spec["animation"]
    # Depth push-back applies to drop / pop / flip — the same set the twin's
    # pushDepth() covers (swing/none never push back).
    depth_on = spec.get("depth") is True and anim in ("drop", "pop", "flip")

    def dim_ladder(i: int) -> str | None:
        """Per-frame eq commands that dim photo i as later photos land.

        eq's options accept expressions but evaluate them once, so the dim
        rides sendcmd instead: one command per video frame inside the push
        windows. contrast = saturation = M with brightness = 128*(M-1)/255
        is exactly multiplicative luma AND chroma scaling — the same
        brightness(M) the DOM preview applies via CSS filter.
        """
        if not depth_on or i >= len(pls) - 1:
            return None
        e = ENTRANCE_LENGTH[anim]
        first = photo_start(spec, i + 1, lead_in) + DEPTH["delay"] * e
        last = photo_start(spec, len(pls) - 1, lead_in) + DEPTH["delay"] * e + DEPTH["span"]
        cmds: list[str] = []
        prev_m: float | None = None
        for k in range(max(0, math.floor(first * fps)), math.ceil(last * fps) + 1):
            t = k / fps
            m = 1 - DEPTH["dim"] * push_depth(spec, i, t, lead_in)
            if prev_m is not None and abs(m - prev_m) <= 1e-4:
                continue
            cmds.append(f"{_n(t)} eq@dim{i} contrast {_n(m)}")
            cmds.append(f"{_n(t)} eq@dim{i} saturation {_n(m)}")
            cmds.append(f"{_n(t)} eq@dim{i} brightness {_n(128 * (m - 1) / 255)}")
            prev_m = m
        return ";".join(cmds) if cmds else None

    lines: list[str] = []
    prev = base_label
    seed = int(spec.get("seed") or 1)
    ex_mode = exit_mode(spec)
    for i, pl in enumerate(pls):
        inp = first_input + i
        photo = spec["photos"][i] if i < len(spec["photos"]) else {}
        fr = photo_frame(spec, photo)
        mat_w = max(24, round(pl["w"] / 100 * width))
        border = max(0, round(frame_border_frac(fr) * mat_w))
        if fr["shape"] == "none":
            border = 0
        bottom = max(0, round(0.205 * mat_w)) if fr["shape"] == "polaroid" else border
        photo_w = max(2, mat_w - 2 * border)
        photo_h = max(2, round(photo_w / _photo_aspect(spec["shape"])))
        mat_h = photo_h + border + bottom
        hex_c = fr["color"][1:] if isinstance(fr.get("color"), str) and fr["color"].startswith("#") and len(fr["color"]) == 7 else "ffffff"
        pad_color = "white" if hex_c.lower() == "ffffff" else "0x" + hex_c

        # Rotation headroom: the sprite canvas must fit the mat (plus shadow
        # margin) at the largest angle the animation reaches. The canvas size
        # is constant, so the overlay positions stay simple expressions.
        rest = math.radians(pl["rot"])
        if anim == "swing":
            max_ang = abs(rest) + math.radians(16)
        else:
            max_ang = max(abs(rest) + math.radians(7), 0.18)
        sm = border + 4                       # shadow margin around the mat
        sin_a, cos_a = math.sin(max_ang), math.cos(max_ang)
        cw = math.ceil(mat_w * cos_a + mat_h * sin_a) + 2 * sm
        if pin:
            # pivot = pin at the mat's top edge; canvas centre sits on the pin
            above = math.ceil(mat_w / 2 * sin_a) + sm
            below = math.ceil(mat_h * cos_a + mat_w / 2 * sin_a) + sm
            ch = above + below
        else:
            ch = math.ceil(mat_w * sin_a + mat_h * cos_a) + 2 * sm
            above = (ch - mat_h) // 2
        mat_x = (cw - mat_w) // 2
        mat_y = above

        t0 = photo_start(spec, i, lead_in)
        # Exit: when this photo flies out (None when it stays until the
        # transition). The exit terms are appended to every entrance's
        # position/rotation expressions — the twin's photoState() defines
        # the exact curves (accelerating fly, alpha fading over the last
        # quarter, 'deal' off to the right, seeded 'shuffle' directions).
        ex_dx = ex_dy = ex_rot = None
        fade_out = ""
        t_end = None
        if ex_mode != "none":
            te0 = lead_in + base_duration(spec) + exit_offset(spec, i)
            t_end = te0 + EXIT_LENGTH[ex_mode]
            qe = f"min(max((t-{_n(te0)})/{_n(EXIT_LENGTH[ex_mode])},0),1)"
            fade_d = 0.25 * EXIT_LENGTH[ex_mode]
            fade_out = f",fade=t=out:st={_n(t_end - fade_d)}:d={_n(fade_d)}:alpha=1"
            if ex_mode == "sweep":
                vx, vy = pl["cx"] - 50, pl["cy"] - 50
                vlen = math.hypot(vx, vy)
                dirx = 0.0 if vlen < 1e-6 else vx / vlen
                diry = -1.0 if vlen < 1e-6 else vy / vlen
                ex_dx = 90 * dirx / 100 * width
                ex_dy = 90 * diry / 100 * height
                ex_rot = (hash01(seed, i, 71) - 0.5) * 20
            elif ex_mode == "deal":
                ex_dx = 0.9 * width
                ex_dy = -0.06 * height          # -6*sin(pi*qe) percent of height
                ex_rot = 25.0
            else:
                a = hash01(seed, i, 73) * 2 * math.pi
                ex_dx = 75 * math.cos(a) / 100 * width
                ex_dy = 55 * math.sin(a) / 100 * height
                ex_rot = (hash01(seed, i, 75) - 0.5) * 40

        def fly_term(value: float, expr: str) -> str:
            # Sign-aware term so generated expressions never contain '+-'.
            return (f"-{_n(abs(value))}" if value < 0 else f"+{_n(value)}") + f"*{expr}"
        ex_x = fly_term(ex_dx, f"pow({qe},2)") if ex_dx is not None else ""
        ex_y = (fly_term(ex_dy, f"pow({qe},2)") if ex_mode != "deal"
                else fly_term(ex_dy, f"sin(PI*{qe})")) if ex_dy is not None else ""
        ex_r = fly_term(ex_rot, qe) if ex_rot is not None else ""

        # Time-varying rotation: the entrance's settle plus the exit spin,
        # one expression per animation (empty when the photo never rotates).
        q_drop = f"min(max((t-{_n(t0)})/0.55,0),1)"
        q_flip = f"min(max((t-{_n(t0)})/0.45,0),1)"
        q_slide = f"min(max((t-{_n(t0)})/0.5,0),1)"
        q_tumble = f"min(max((t-{_n(t0)})/0.6,0),1)"
        q_brush = f"min(max((t-{_n(t0)})/0.5,0),1)"
        q_glitch = f"min(max((t-{_n(t0)})/0.5,0),1)"
        extra_x = extra_y = extra_r = ""
        if anim == "slide":
            extra_x = f"-{_n(0.28 * width)}*pow(1-{q_slide},3)"
        elif anim == "rise":
            extra_y = f"+{_n(0.22 * height)}*pow(1-{q_slide},3)"
        elif anim == "tumble":
            extra_x = f"+{_n(0.10 * width)}*pow(1-{q_tumble},3)"
            extra_y = f"-{_n(0.18 * height)}*pow(1-{q_tumble},3)"
            extra_r = f"+28*pow(1-{q_tumble},3)"
        elif anim == "brush":
            extra_x = f"-{_n(0.36 * width)}*pow(1-{q_brush},3)"
        elif anim == "glitch":
            jg = f"pow(1-{q_glitch},2)"
            extra_x = fly_term((hash01(seed, i, 61) - 0.5) * 0.10 * width, jg)
            extra_y = fly_term((hash01(seed, i, 63) - 0.5) * 0.06 * height, jg)
            extra_r = fly_term((hash01(seed, i, 65) - 0.5) * 8, jg)
        if spec.get("kenBurns") is True:
            land = t0 + ENTRANCE_LENGTH.get(anim, 0.0)
            span = max(0.8, collage_duration(spec) * 0.7)
            kb = f"min(max((t-{_n(land)})/{_n(span)},0),1)"
            extra_x = (extra_x or "") + fly_term((hash01(seed, i, 51) - 0.5) * 0.05 * width, kb)
            extra_y = (extra_y or "") + fly_term((hash01(seed, i, 53) - 0.5) * 0.035 * height, kb)
        sway_r = f"+1.5*sin(2.15*t+{_n(i * 0.9)})" if spec.get("sway") is True else ""
        if anim == "drop":
            ang = f"({_n(pl['rot'])}-7*pow(1-{q_drop},3){ex_r}{extra_r}{sway_r})*PI/180"
        elif anim == "flip":
            ang = f"(-4*(1-{q_flip}){ex_r}{extra_r}{sway_r})*PI/180"
        elif anim == "swing":
            tau = f"max(t-{_n(t0)},0)"
            ang = f"{_n(pl['rot'])}*PI/180+14*PI/180*exp(-1.3*{tau})*cos(2*PI*{tau}/1.6){ex_r}*PI/180{sway_r}*PI/180"
        elif extra_r or sway_r or ex_r or abs(pl["rot"]) > 0.01:
            ang = f"({_n(pl['rot']) if abs(pl['rot']) > 0.01 else '0'}{ex_r}{extra_r}{sway_r})*PI/180"
        else:
            ang = ""
        rot_seg = f",rotate='{ang}':c=black@0.0" if ang else ""

        # Alpha only rides the mid chain — the per-frame SIZE cannot: FFmpeg's
        # scale filter freezes its output dimensions at init even with
        # eval=frame (verified on the render binary), so pop's spring, flip's
        # edge-on width and the depth shrink are rendered as a LADDER of
        # static scales whose overlay enable windows partition time. The
        # sizes are sampled from the twin's photoState() at frame resolution,
        # so the MP4 shows what the preview shows, frame for frame.
        fade_d_in = {"drop": 0.22, "pop": 0.18, "flip": 0.15, "swing": 0.15,
                     "fade": 0.45, "slide": 0.18, "rise": 0.18, "tumble": 0.16, "zoom": 0.16,
                     "fold": 0.14, "glitch": 0.12, "ink": 0.4, "brush": 0.16}.get(anim)
        mid = (f"fade=t=in:st={_n(t0)}:d={_n(fade_d_in)}:alpha=1" if fade_d_in else "null") + fade_out

        # The anchor: the canvas centre lands on the mat centre (or the pin
        # for swinging photos). With the size ladder each level knows its own
        # literal W/H, so the anchor is exact per level.
        ax_px = pl["cx"] / 100 * width
        ay_px = pl["cy"] / 100 * height - (mat_h / 2 if pin else 0)
        fall = f"-{_n(0.26 * height)}*pow(1-{q_drop},3)" if anim == "drop" else ""

        # --- size ladder ---------------------------------------------------
        def size_levels() -> list[tuple[int, int, list[list[float]]]]:
            """Distinct (W, H) sprite sizes photo i shows, each with the
            [start, end) frame windows it is on screen. The last window is
            open-ended (end=None)."""
            varies = anim in ("pop", "flip", "zoom", "fold", "ink", "brush", "glitch", "tumble") or depth_on or spec.get("kenBurns") is True
            if not varies:
                return [(cw, ch, [[0.0, None]])]
            # the timeline only moves during the entrance and the depth push
            # windows — sample the twin at frame resolution up to there
            last_var = t0 + ENTRANCE_LENGTH[anim]
            if depth_on:
                e = ENTRANCE_LENGTH[anim]
                for j in range(i + 1, len(pls)):
                    tj = photo_start(spec, j, lead_in)
                    last_var = max(last_var, tj + DEPTH["delay"] * e + DEPTH["span"])
            n_frames = max(1, math.ceil(last_var * fps))
            runs: list[list] = []           # [start_frame, end_frame, W, H]
            for k in range(n_frames + 1):
                st = photo_state(spec, i, k / fps, lead_in, aspect)
                w_ = max(2, int(round(cw * st["scale"] * st["scaleX"])))
                h_ = max(2, int(round(ch * st["scale"])))
                if runs and runs[-1][2] == w_ and runs[-1][3] == h_:
                    runs[-1][1] = k
                else:
                    runs.append([k, k, w_, h_])
            levels: dict[tuple[int, int], list[list[float]]] = {}
            order: list[tuple[int, int]] = []
            for k0, k1, w_, h_ in runs:
                key = (w_, h_)
                if key not in levels:
                    levels[key] = []
                    order.append(key)
                # windows cut at frame midpoints, padded a few ms either way:
                # ffmpeg computes t as pts*(1/fps) which can land one ulp below
                # the exact k/fps an expression boundary would use, and a
                # boundary exactly on a frame time made that frame match no
                # window at all (the photo blinked out for a frame)
                a = max(0.0, k0 / fps - 0.003)
                b = None if k1 == n_frames else k1 / fps + 0.5 / fps + 0.003
                levels[key].append([a, b])
            return [(w_, h_, levels[(w_, h_)]) for w_, h_ in order]

        def enable_expr(windows: list[list[float]]) -> str:
            # Any level can carry the open-ended window: the entrance's
            # settle size often repeats a size it crossed earlier (outBack
            # passes through 1.0 on the way up and settles there), which
            # merges the final run into an early level's windows.
            parts = []
            for a, b in windows:
                if b is None:                  # runs to the end of the segment
                    parts.append(f"between(t,{_n(a)},{_n(t_end + 0.003)})" if t_end is not None
                                 else f"gte(t,{_n(a - 0.003)})")
                else:
                    if t_end is not None:
                        b = min(b, t_end + 0.003)
                    parts.append(f"between(t,{_n(a)},{_n(b)})")
            if parts == ["gte(t,0)"] or parts == ["gte(t,-0.003)"]:
                parts = []                     # always on: no window at all
            return ":" + "enable='" + "+".join(parts) + "'" if parts else ""

        levels = size_levels()

        m, t_, sh = f"m{i}", f"t{i}", f"s{i}"
        # Depth push-back dims the mat itself (the shadow silhouette is black
        # either way): the ladder drives a per-photo eq (multiplicative, the
        # CSS brightness() equivalent) before the rgba split, so alpha is
        # untouched.
        ladder = dim_ladder(i)
        dim_seg = (f"sendcmd=commands='{ladder}',eq@dim{i}=contrast=1:saturation=1:brightness=0,"
                   if ladder is not None else "")
        photo = spec["photos"][i] if i < len(spec["photos"]) else {}
        look = picture_look(photo, photo_w, photo_h)
        crop_chain = ",".join(crop_filters(normalize_crop({"crop": photo.get("crop")})))
        pre = ",".join(x for x in (crop_chain, look) if x)
        pre = (pre + ",") if pre else ""
        use_shadow = fr.get("shadow") is not False and fr["shape"] != "none"
        mask = _shape_mask(fr, mat_w, mat_h)
        if use_shadow:
            lines.append(
                f"[{inp}:v]{pre}scale={photo_w}:{photo_h}:force_original_aspect_ratio=increase,"
                f"crop={photo_w}:{photo_h},pad={mat_w}:{mat_h}:{border}:{border}:color={pad_color},"
                f"{dim_seg}format=rgba{mask},split[{m}a][{m}b];"
            )
            lines.append(f"[{m}b]pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0[{t_}];")
            lines.append(
                f"[{m}a]lutrgb=r=0:g=0:b=0,colorchannelmixer=aa=0.34,"
                f"pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0,boxblur={max(2, max(border, 4) // 2)}:2[{sh}];"
            )
            lines.append(f"[{sh}][{t_}]overlay=x=0:y=0[sp{i}];")
        else:
            lines.append(
                f"[{inp}:v]{pre}scale={photo_w}:{photo_h}:force_original_aspect_ratio=increase,"
                f"crop={photo_w}:{photo_h},pad={mat_w}:{mat_h}:{border}:{border}:color={pad_color},"
                f"{dim_seg}format=rgba{mask},pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0[sp{i}];"
            )
        lines.append(f"[sp{i}]{mid},format=rgba[sp{i}f];")
        if len(levels) > 1:
            lines.append(f"[sp{i}f]split={len(levels)}" +
                         "".join(f"[b{i}_{k}]" for k in range(len(levels))) + ";")

        # The size ladder: one static scale per distinct size, each enabled
        # only for its frame windows. Later levels overlay earlier ones, and
        # the windows partition time, so exactly one size per photo is ever
        # on screen.
        for k, (W, H, windows) in enumerate(levels):
            is_last = k == len(levels) - 1
            src = f"sp{i}f" if len(levels) == 1 else f"b{i}_{k}"
            # the final level closes photo i's chain, so its output keeps the
            # short label the camera/caption chains consume
            out = f"o{i}" if is_last else f"o{i}_{k}"
            lines.append(f"[{src}]scale={W}:{H}{rot_seg}[lv{i}_{k}];")
            x_expr = f"{_n(ax_px - W / 2)}{ex_x}{extra_x}"
            y_expr = f"{_n(ay_px - H / 2)}{fall}{ex_y}{extra_y}"
            enable = enable_expr(windows)
            lines.append(f"[{prev}][lv{i}_{k}]overlay=x='{x_expr}':y='{y_expr}'{enable}[{out}];")
            prev = out

    # Virtual camera over the composed scene — the last thing before the
    # caption: a supersampled zoompan following camera_state()'s curves
    # (see camera_filter for why not crop, and why supersampled).
    camera = camera_filter(spec, width, height, fps, lead_in, aspect)
    if camera is not None:
        lines.append(f"[{prev}]{camera}[cam];")
        prev = "cam"
    return lines, prev
