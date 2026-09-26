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
ENTRANCE_LENGTH = {"drop": 0.55, "pop": 0.5, "swing": 2.0, "none": 0.0}


def _round2(v: float) -> float:
    # Same rounding as Math.round(v*100)/100 in the TS twin (floor(x+.5)),
    # NOT Python's round() which rounds half to even.
    return math.floor(v * 100 + 0.5) / 100


def collage_duration(spec: dict[str, Any]) -> float:
    """The slide duration implied by the photo timings: last photo's start +
    its entrance + the hold after it ('none' photos are all on screen from
    the first frame, so just the hold). 0 for an empty collage."""
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
    if anim not in ("drop", "pop"):
        return 0.0
    e = ENTRANCE_LENGTH[anim]
    n = len(spec.get("photos") or [])
    p = 0.0
    for j in range(i + 1, n):
        tj = photo_start(spec, j, lead_in)
        q = _clamp01((t - (tj + DEPTH["delay"] * e)) / DEPTH["span"])
        p += q * q * (3 - 2 * q)
    return min(DEPTH["max"], p)


def photo_state(spec: dict[str, Any], i: int, t: float, lead_in: float = 0.0) -> dict[str, float]:
    """Animated offsets of photo i at segment time t — twin of photoState()."""
    t0 = photo_start(spec, i, lead_in)
    push = push_depth(spec, i, t, lead_in)
    anim = str(spec.get("animation") or "drop")
    if anim == "drop":
        q = _clamp01((t - t0) / 0.55)
        settle = (1 - q) ** 3
        return {"dx": 0.0, "dy": -26 * settle, "rot": -7 * settle, "scale": 1 - DEPTH["scale"] * push, "alpha": _clamp01((t - t0) / 0.22), "dim": DEPTH["dim"] * push}
    if anim == "pop":
        q = _clamp01((t - t0) / 0.5)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        return {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": (0.55 + 0.45 * back) * (1 - DEPTH["scale"] * push), "alpha": _clamp01((t - t0) / 0.18), "dim": DEPTH["dim"] * push}
    if anim == "swing":
        tau = max(0.0, t - t0)
        rot = 14 * math.exp(-1.3 * tau) * math.cos(2 * math.pi * tau / 1.6)
        return {"dx": 0.0, "dy": 0.0, "rot": rot, "scale": 1.0, "alpha": _clamp01(tau / 0.15), "dim": 0.0}
    return {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "alpha": 1.0, "dim": 0.0}


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
    animation = raw.get("animation") if raw.get("animation") in ("drop", "pop", "swing", "none") else "drop"
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
    # Depth push-back: a per-photo expression for how many "pushed back"
    # units have accumulated by time t (each later photo's landing adds an
    # eased term, capped). The sprite scale uses it directly and the overlay
    # anchors track the same scale so the mat stays centred.
    depth_on = spec.get("depth") is True and anim in ("drop", "pop")

    def push_expr(i: int) -> str | None:
        if not depth_on:
            return None
        e = ENTRANCE_LENGTH[anim]
        terms = []
        for j in range(i + 1, len(pls)):
            tj = photo_start(spec, j, lead_in)
            q = f"min(max((t-{_n(tj + DEPTH['delay'] * e)})/{_n(DEPTH['span'])},0),1)"
            terms.append(f"(pow({q},2)*(3-2*{q}))")
        if not terms:
            return None
        return f"(min({_n(DEPTH['max'])}," + "+".join(terms) + "))"

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
        push = push_expr(i)
        shrink = f"(1-{_n(DEPTH['scale'])}*{push})" if push is not None else None
        mid = ""                                # fade + rotate/scale chain
        if anim == "drop":
            q = f"min(max((t-{_n(t0)})/0.55,0),1)"
            ang = f"({_n(pl['rot'])}-7*pow(1-{q},3))*PI/180"
            mid = f"fade=t=in:st={_n(t0)}:d=0.22:alpha=1,rotate='{ang}':c=black@0.0"
            if shrink is not None:
                mid += f",scale=w='{_n(cw)}*{shrink}':h=-2:eval=frame"
        elif anim == "pop":
            q = f"min(max((t-{_n(t0)})/0.5,0),1)"
            scale = f"(0.55+0.45*(1+2.70158*pow({q}-1,3)+1.70158*pow({q}-1,2)))"
            if shrink is not None:
                scale = f"{scale}*{shrink}"
            mid = f"fade=t=in:st={_n(t0)}:d=0.18:alpha=1,scale=w='{_n(cw)}*{scale}':h=-2:eval=frame"
        elif anim == "swing":
            tau = f"max(t-{_n(t0)},0)"
            ang = f"{_n(pl['rot'])}*PI/180+14*PI/180*exp(-1.3*{tau})*cos(2*PI*{tau}/1.6)"
            mid = f"fade=t=in:st={_n(t0)}:d=0.15:alpha=1,rotate='{ang}':c=black@0.0"
        elif abs(pl["rot"]) > 0.01:
            mid = f"rotate='{_n(pl['rot'])}*PI/180':c=black@0.0"
        if not mid:
            mid = "null"

        # Anchor: the canvas centre lands on the mat centre, or on the pin
        # (top edge of the mat) for swinging photos. FFmpeg's overlay x/y is
        # the overlay's TOP-LEFT corner, so every expression subtracts half
        # the canvas — exactly the translate(-50%, …) the DOM preview uses.
        ax_px = pl["cx"] / 100 * width
        ay_px = pl["cy"] / 100 * height - (mat_h / 2 if pin else 0)
        if anim == "drop":
            q = f"min(max((t-{_n(t0)})/0.55,0),1)"
            if shrink is None:
                x_expr = _n(ax_px - cw / 2)
                y_expr = f"{_n(ay_px - ch / 2)}-{_n(0.26 * height)}*pow(1-{q},3)"
            else:
                # The sprite shrinks as later photos land on top; the anchor
                # tracks the same scale so the mat stays centred on it.
                x_expr = f"{_n(ax_px)}-{_n(cw / 2)}*{shrink}"
                y_expr = f"{_n(ay_px)}-{_n(ch / 2)}*{shrink}-{_n(0.26 * height)}*pow(1-{q},3)"
        elif anim == "pop":
            q = f"min(max((t-{_n(t0)})/0.5,0),1)"
            scale = f"(0.55+0.45*(1+2.70158*pow({q}-1,3)+1.70158*pow({q}-1,2)))"
            if shrink is not None:
                scale = f"{scale}*{shrink}"
            x_expr = f"{_n(ax_px)}-{_n(cw / 2)}*{scale}"
            y_expr = f"{_n(ay_px)}-{_n(ch / 2)}*{scale}"
        else:
            # swing / none: a static centre-anchored sprite (swing rotates
            # around the pin, which is the canvas centre — see above).
            x_expr, y_expr = _n(ax_px - cw / 2), _n(ay_px - ch / 2)
        enable = f":enable='gte(t,{_n(t0)})'" if anim != "none" else ""

        m, t_, s = f"m{i}", f"t{i}", f"s{i}"
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
            f"pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0,boxblur={max(2, border // 2)}:2[{s}];"
        )
        lines.append(f"[{m}b]pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0[{t_}];")
        # Both streams are already padded to the full canvas with the mat in
        # place, so the mat lands on its shadow at (0, 0) — any other offset
        # would shift the mat within its own sprite and off its anchor.
        lines.append(f"[{s}][{t_}]overlay=x=0:y=0[sp{i}];")
        lines.append(f"[sp{i}]{mid}[sp{i}r];")
        lines.append(f"[{prev}][sp{i}r]overlay=x='{x_expr}':y='{y_expr}'{enable}[o{i}];")
        prev = f"o{i}"
    return lines, prev
