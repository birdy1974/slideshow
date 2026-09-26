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
    return 4.0 / 3.0


def mat_height(w: float, shape: str, aspect: float) -> float:
    """Mat height in % of frame height for a mat width of w % of frame width."""
    border = 0.045 * w
    bottom = 0.205 * w
    photo_w = w - 2 * border
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
            out.append({"cx": 7 + cell_w * (col + 0.5), "cy": 12 + cell_h * (row + 0.5), "w": width_of(w, i), "rot": 0.0})
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
            out.append({"cx": 5 + cell_wr * (col + 0.5), "cy": 12 + cell_h * (row + 0.5), "w": width_of(w, i), "rot": 0.0})
    elif layout == "fan":
        # Cards fanned out from a point below the frame — each card tilts
        # along its spoke, like a hand of cards offered to the viewer.
        spoke = 46.0
        spread = min(48.0, 10 + 7 * n)
        w = 36.0 if n <= 3 else (30.0 if n <= 6 else 26.0)
        for i in range(n):
            a = 0.0 if n == 1 else math.radians(spread * (i / (n - 1) - 0.5))
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
                out.append({"cx": sim[i]["cx"], "cy": sim[i]["cy"], "w": ws[i] * fit, "rot": 0.0})
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
    """Photo i's mat size multiplier — stored value (clamped to 0.5..1.5) or 1."""
    photos = spec.get("photos") or []
    raw = photos[i].get("size") if i < len(photos) else None
    v = float("nan")
    if raw is not None and not isinstance(raw, bool) and not (isinstance(raw, str) and raw.strip() == ""):
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = float("nan")
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
ENTRANCE_LENGTH = {"drop": 0.55, "pop": 0.5, "swing": 2.0, "flip": 0.45, "none": 0.0}

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
    frame. The FFmpeg crop/zoompan chain and the preview's CSS transform are
    two views of these numbers."""
    mode = spec.get("camera") if spec.get("camera") in ("pan", "zoom", "telescope", "droste") else "none"
    if mode == "none":
        return {"z": 1.0, "cx": 50.0, "cy": 50.0}
    d = max(0.2, collage_duration(spec))
    p = _clamp01((t - lead_in) / d)
    if mode == "pan":
        return {"z": 1.09, "cx": 54 - 8 * p, "cy": 50.0}
    pls = placements(spec, aspect)
    a = pls[-1] if pls else {"cx": 50.0, "cy": 50.0}
    if mode == "zoom":
        z = 1 + 0.35 * p
    elif mode == "telescope":
        z = 1 + 0.9 * (p * p * (3 - 2 * p))
    else:
        z = 1 + 1.1 * (p ** 2.2)
    half = 50.0 / z
    return {
        "z": z,
        "cx": min(100 - half, max(half, a["cx"])),
        "cy": min(100 - half, max(half, a["cy"])),
    }


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
    else:
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": 1.0, "dim": 0.0}
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
        photos.append(entry)
        if len(photos) >= MAX_COLLAGE_PHOTOS:
            break
    if not photos:
        return None
    layout = raw.get("layout") if raw.get("layout") in ("stack", "grid", "scatter", "filmstrip", "fan", "masonry") else "stack"
    animation = raw.get("animation") if raw.get("animation") in ("drop", "pop", "swing", "flip", "none") else "drop"
    exit_ = raw.get("exit") if raw.get("exit") in ("sweep", "deal", "shuffle") else None
    camera = raw.get("camera") if raw.get("camera") in ("pan", "zoom", "telescope", "droste") else None
    shape = raw.get("shape") if raw.get("shape") in ("4:3", "square", "3:4") else "4:3"
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
        mat_w = max(24, round(pl["w"] / 100 * width))
        border = max(2, round(0.045 * mat_w))
        bottom = max(3, round(0.205 * mat_w))
        photo_w = mat_w - 2 * border
        photo_h = max(2, round(photo_w / _photo_aspect(spec["shape"])))
        mat_h = photo_h + border + bottom

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
        if anim == "drop":
            ang = f"({_n(pl['rot'])}-7*pow(1-{q_drop},3){ex_r})*PI/180"
        elif anim == "flip":
            ang = f"(-4*(1-{q_flip}){ex_r})*PI/180"
        elif anim == "swing":
            tau = f"max(t-{_n(t0)},0)"
            ang = f"{_n(pl['rot'])}*PI/180+14*PI/180*exp(-1.3*{tau})*cos(2*PI*{tau}/1.6){ex_r}*PI/180"
        elif ex_r or abs(pl["rot"]) > 0.01:
            ang = f"({_n(pl['rot']) if abs(pl['rot']) > 0.01 else '0'}{ex_r})*PI/180"
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
        fade_d_in = {"drop": 0.22, "pop": 0.18, "flip": 0.15, "swing": 0.15}.get(anim)
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
            varies = anim in ("pop", "flip") or depth_on
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
        lines.append(
            f"[{inp}:v]scale={photo_w}:{photo_h}:force_original_aspect_ratio=increase,"
            f"crop={photo_w}:{photo_h},pad={mat_w}:{mat_h}:{border}:{border}:color=white,"
            f"{dim_seg}format=rgba,split[{m}a][{m}b];"
        )
        # Soft shadow: the mat silhouette, black and translucent, blurred and
        # padded onto the same canvas position the mat itself occupies.
        lines.append(
            f"[{m}a]lutrgb=r=0:g=0:b=0,colorchannelmixer=aa=0.34,"
            f"pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0,boxblur={max(2, border // 2)}:2[{sh}];"
        )
        lines.append(f"[{m}b]pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0[{t_}];")
        # Both streams are already padded to the full canvas with the mat in
        # place, so the mat lands on its shadow at (0, 0) — any other offset
        # would shift the mat within its own sprite and off its anchor.
        lines.append(f"[{sh}][{t_}]overlay=x=0:y=0[sp{i}];")
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
            x_expr = f"{_n(ax_px - W / 2)}{ex_x}"
            y_expr = f"{_n(ay_px - H / 2)}{fall}{ex_y}"
            enable = enable_expr(windows)
            lines.append(f"[{prev}][lv{i}_{k}]overlay=x='{x_expr}':y='{y_expr}'{enable}[{out}];")
            prev = out

    # Virtual camera over the composed scene — the last thing before the
    # caption: 'pan' drifts a constant window across the frame (crop), the
    # zoom family zooms into the last photo's anchor (zoompan). The window
    # maths is the twin's cameraState(); the clamps keep the window inside
    # the frame so both engines see identical pixels.
    cam = spec.get("camera") if spec.get("camera") in ("pan", "zoom", "telescope", "droste") else "none"
    if cam != "none" and pls:
        d = max(0.2, collage_duration(spec))
        if cam == "pan":
            ow, oh = max(2, int(width / 1.09)), max(2, int(height / 1.09))
            p = f"min(max((t-{_n(lead_in)})/{_n(d)},0),1)"
            lines.append(
                f"[{prev}]crop={ow}:{oh}:x='(54-8*{p})*iw/100-ow/2':y='(ih-oh)/2',"
                f"scale={width}:{height},setsar=1[cam];"
            )
        else:
            # zoompan has no t variable — 'on' is the output frame count and
            # the output fps equals the input fps (d=1), so on/fps is time.
            p = f"min(max((on/{_n(fps)}-{_n(lead_in)})/{_n(d)},0),1)"
            if cam == "zoom":
                zexpr = f"(1+0.35*{p})"
            elif cam == "telescope":
                zexpr = f"(1+0.9*({p}*{p}*(3-2*{p})))"
            else:
                zexpr = f"(1+1.1*pow({p},2.2))"
            anchor = pls[-1]
            cx = f"min(100-50/{zexpr},max(50/{zexpr},{_n(anchor['cx'])}))"
            cy = f"min(100-50/{zexpr},max(50/{zexpr},{_n(anchor['cy'])}))"
            lines.append(
                f"[{prev}]zoompan=z='{zexpr}':x='({cx}/100)*iw-iw/(2*{zexpr})'"
                f":y='({cy}/100)*ih-ih/(2*{zexpr})':d=1:s={width}x{height}:fps={_n(fps)},setsar=1[cam];"
            )
        prev = "cam"
    return lines, prev
