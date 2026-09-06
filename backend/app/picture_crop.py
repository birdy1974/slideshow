"""Cut & crop for pictures and movies — the render half of the crop editor.

The source files on /photos and /videos are read-only mounts, so nothing here
ever rewrites a picture. A crop is a handful of numbers on the media item
(`item["crop"]`) and this module turns them into FFmpeg filters, while
`src/pictureCrop.ts` turns the *same* numbers into CSS/canvas for the browser.

Coordinate space, identical on both sides:

1. the picture as stored, turned by the item's whole-quarter `rotation`
   (`transpose=1` etc.) — so a portrait photo is cropped the way it is shown;
2. straightened by `degrees` (−15..15) and zoomed to the largest inscribed
   rectangle, so no filled corners are ever visible;
3. `rect` — fractions of that straightened view, x/y the top-left corner,
   w/h the size, all in 0..1;
4. `lasso` — a polygon in fractions of the *cropped* view; its interior is cut
   away and filled with a blurred copy of the same picture (maskedmerge).

Everything missing, malformed or out of range falls back to "no crop", so a
project saved before this feature renders exactly as before.
"""
from __future__ import annotations

import math
import re
from typing import Any

# Straightening is a small levelling tool, not a rotation tool: the quarter
# turns already live in `rotation`. Beyond ~15° the inscribed zoom throws away
# too much of the picture to be worth it.
MAX_STRAIGHTEN = 15.0
# A crop smaller than this is a mistake, not a composition.
MIN_CROP = 0.05
MIN_LASSO_POINTS = 3
MAX_LASSO_POINTS = 24
# The polygon mask is written as a PGM next to the segment files and stretched
# to the frame with scale2ref. 512 is plenty: the hole is filled with a blurred
# copy, so its edge is soft by design.
LASSO_MASK_SIZE = 512
# The blurred fill is made on a small copy, like the letterbox backdrop in
# fit_frame_filter, then scaled back up — visually identical, far cheaper.
LASSO_BLUR_PX = 640
DEFAULT_FEATHER = 0.35

FULL_RECT = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def _number(value: Any) -> float | None:
    """float() that never raises and never returns NaN/inf (mirrors TS `finite`)."""
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def normalize_rect(value: Any) -> dict[str, float] | None:
    """A usable crop rectangle in fractions, or None for "the whole picture"."""
    if not isinstance(value, dict):
        return None
    x, y = _number(value.get("x")), _number(value.get("y"))
    w, h = _number(value.get("w")), _number(value.get("h"))
    if None in (x, y, w, h):
        return None
    w = max(MIN_CROP, min(1.0, w))
    h = max(MIN_CROP, min(1.0, h))
    x = min(max(0.0, x), 1.0 - w)
    y = min(max(0.0, y), 1.0 - h)
    rect = {"x": x, "y": y, "w": w, "h": h}
    if all(abs(rect[key] - FULL_RECT[key]) < 0.002 for key in FULL_RECT):
        return None
    return rect


def normalize_lasso(value: Any) -> list[tuple[float, float]] | None:
    """A usable cut-out polygon in fractions, or None."""
    if not isinstance(value, (list, tuple)):
        return None
    points: list[tuple[float, float]] = []
    for entry in value:
        if isinstance(entry, dict):  # tolerate {x, y} as well as [x, y]
            x, y = _number(entry.get("x")), _number(entry.get("y"))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            x, y = _number(entry[0]), _number(entry[1])
        else:
            x = y = None
        if x is None or y is None:
            continue
        points.append((clamp01(x), clamp01(y)))
        if len(points) >= MAX_LASSO_POINTS:
            break
    if len(points) < MIN_LASSO_POINTS:
        return None
    # A polygon that covers no area (all points on one spot) is not a cut.
    if abs(polygon_area(points)) < 1e-4:
        return None
    return points


def polygon_area(points: list[tuple[float, float]]) -> float:
    """Signed area of the polygon in fraction space (shoelace)."""
    total = 0.0
    for index, (x, y) in enumerate(points):
        x2, y2 = points[(index + 1) % len(points)]
        total += x * y2 - x2 * y
    return total / 2.0


def normalize_crop(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Flatten an item's stored crop into numbers both renderers agree on.

    Returns None when the item would render exactly as the untouched file.
    """
    if not isinstance(item, dict):
        return None
    stored = item.get("crop")
    if not isinstance(stored, dict):
        return None
    rect = normalize_rect(stored.get("rect"))
    degrees = _number(stored.get("degrees")) or 0.0
    degrees = max(-MAX_STRAIGHTEN, min(MAX_STRAIGHTEN, degrees))
    if abs(degrees) < 0.05:
        degrees = 0.0
    lasso = normalize_lasso(stored.get("lasso"))
    feather = _number(stored.get("feather"))
    feather = clamp01(DEFAULT_FEATHER if feather is None else feather)
    if rect is None and not degrees and lasso is None:
        return None
    return {"rect": rect or dict(FULL_RECT), "degrees": degrees, "lasso": lasso, "feather": feather}


def has_crop(item: dict[str, Any] | None) -> bool:
    """True when the item is cut or cropped in any way."""
    return normalize_crop(item) is not None


def inscribed_zoom(aspect: float, degrees: float) -> float:
    """How far a picture must be zoomed after `degrees` of straightening for the
    rotated corners to disappear.

    The largest rectangle of the same aspect inscribed in an W×H picture turned
    by θ has height ``H / k`` with ``k = max(cos θ + sin θ / a, a·sin θ + cos θ)``
    and a = W/H. `src/pictureCrop.ts` carries the identical formula; the test
    suite compares the two against the FFmpeg expression this module emits.
    """
    angle = math.radians(abs(degrees))
    cos, sin = math.cos(angle), math.sin(angle)
    if aspect <= 0 or sin == 0.0:
        return 1.0
    return max(cos + sin / aspect, aspect * sin + cos)


def _even_expression(expression: str) -> str:
    """Round an FFmpeg expression down to an even pixel count (yuv420p)."""
    return f"trunc(({expression})/2)*2"


def inscribed_size_expressions() -> tuple[str, str]:
    """FFmpeg expressions for the inscribed rectangle of the *input* frame.

    Written only with `iw`/`ih` so no probing is needed and the values stay
    correct for a 24 MP photo as well as a 480p movie:

        w = min(iw²/(iw·C+ih·S), iw·ih/(iw·S+ih·C))
        h = min(iw·ih/(iw·C+ih·S), ih²/(iw·S+ih·C))
    """
    return (
        "min(iw*iw/(iw*C+ih*S),iw*ih/(iw*S+ih*C))",
        "min(iw*ih/(iw*C+ih*S),ih*ih/(iw*S+ih*C))",
    )


def crop_filters(crop: dict[str, Any] | None) -> list[str]:
    """Straightening + rectangle crop as FFmpeg filters (lasso excluded).

    The rectangle is taken out of the inscribed view in a single `crop`, so a
    straightened picture is never resampled twice.
    """
    if not crop:
        return []
    rect = crop.get("rect") or FULL_RECT
    degrees = float(crop.get("degrees") or 0.0)
    # A full frame with nothing to straighten is the untouched picture: no
    # resample at all (a lasso cut-out on its own lands here too).
    if abs(degrees) < 0.05 and all(
        abs(rect.get(key, FULL_RECT[key]) - FULL_RECT[key]) < 0.002 for key in FULL_RECT
    ):
        return []
    filters: list[str] = []
    if abs(degrees) >= 0.05:
        # ow/oh keep the canvas size, so the inscribed crop below works in the
        # same iw/ih space; the corners it discards are the ones rotate fills.
        filters.append(f"rotate=a={_rad(degrees)}:ow=iw:oh=ih:fillcolor=black:bilinear=1")
        cos = math.cos(math.radians(abs(degrees)))
        sin = math.sin(math.radians(abs(degrees)))
        wide, high = inscribed_size_expressions()
        wide = wide.replace("C", _num(cos)).replace("S", _num(sin))
        high = high.replace("C", _num(cos)).replace("S", _num(sin))
        x = f"(iw-({wide}))/2+({wide})*{_num(rect['x'])}"
        y = f"(ih-({high}))/2+({high})*{_num(rect['y'])}"
        w = f"({wide})*{_num(rect['w'])}"
        h = f"({high})*{_num(rect['h'])}"
    else:
        # Without straightening the inscribed rectangle *is* the frame, so keep
        # the expression readable in the logs and in the tests.
        x, y = f"iw*{_num(rect['x'])}", f"ih*{_num(rect['y'])}"
        w, h = f"iw*{_num(rect['w'])}", f"ih*{_num(rect['h'])}"
    filters.append(
        f"crop=w={_even_expression(w)}:h={_even_expression(h)}"
        f":x={_even_expression(x)}:y={_even_expression(y)}"
    )
    return filters


def _rad(degrees: float) -> str:
    return _num(math.radians(degrees), 8)


def _num(value: float, digits: int = 6) -> str:
    """Stable FFmpeg numeric literal without binary float noise."""
    return f"{round(float(value), digits):g}"


def lasso_plan(crop: dict[str, Any] | None) -> dict[str, Any] | None:
    """What the renderer needs to cut a lasso hole: points + feather."""
    if not crop or not crop.get("lasso"):
        return None
    return {"points": crop["lasso"], "feather": float(crop.get("feather", DEFAULT_FEATHER))}


def lasso_mask_pgm(points: list[tuple[float, float]], size: int = LASSO_MASK_SIZE) -> bytes:
    """A greyscale PGM mask: white inside the polygon (cut), black outside.

    `maskedmerge` takes the second stream where the mask is white, so white is
    the hole that gets filled with the blurred copy. Edges are covered
    analytically in x (sub-pixel accurate) and sampled at the row centre in y,
    which keeps this O(rows × edges) instead of a per-pixel point-in-polygon
    test — a 512² mask costs a few tens of milliseconds.
    """
    size = max(8, int(size))
    rows: list[bytes] = []
    count = len(points)
    for row in range(size):
        yc = (row + 0.5) / size
        crossings: list[float] = []
        for index in range(count):
            x1, y1 = points[index]
            x2, y2 = points[(index + 1) % count]
            if y1 == y2:
                continue
            low, high = (y1, y2) if y1 < y2 else (y2, y1)
            if not (low <= yc < high):
                continue
            crossings.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
        line = bytearray(size)
        crossings.sort()
        for index in range(0, len(crossings) - 1, 2):
            start, end = crossings[index], crossings[index + 1]
            if end <= start:
                continue
            first = max(0, int(math.floor(start * size)))
            last = min(size, int(math.ceil(end * size)))
            for pixel in range(first, last):
                covered = min(end, (pixel + 1) / size) - max(start, pixel / size)
                if covered <= 0:
                    continue
                value = line[pixel] + int(round(covered * size * 255))
                line[pixel] = 255 if value > 255 else value
        rows.append(bytes(line))
    return f"P5\n{size} {size}\n255\n".encode("ascii") + b"".join(rows)


def lasso_graph(plan: dict[str, Any], mask_index: int, pre: list[str], post: list[str]) -> str:
    """The filter_complex that cuts the hole and fills it again.

    The mask is a still file, so it is stretched to whatever size the stream
    has at that point (scale2ref) — the renderer never needs to know the
    picture's pixel dimensions. Feathering happens on the small mask, which is
    orders of cheaper than blurring a full-frame mask per frame.

    ``pre`` runs before the cut (rotation, straightening, rectangle crop),
    ``post`` after it (fit/fill to the frame, Ken Burns, look filters, text).

    The fill is composited with alphamerge+overlay rather than maskedmerge: the
    mask's greyscale becomes the alpha of the blurred copy, so only that copy
    changes format and the kept picture is never round-tripped through RGB.
    """
    feather = max(0.0, min(1.0, float(plan.get("feather", DEFAULT_FEATHER))))
    # sigma is in mask pixels, so the softness scales with the frame: a 512 mask
    # stretched to 1920 turns ~12 px into a soft, natural edge.
    sigma = _num(feather * LASSO_MASK_SIZE / 42.0)
    blur_px = LASSO_BLUR_PX
    graph = (
        f"[0:v]{','.join(pre) if pre else 'null'}[cutpre];"
        f"[{mask_index}:v]format=gray,gblur=sigma={sigma}[cutmask0];"
        f"[cutmask0][cutpre]scale2ref=flags=bicubic[cutmask][cutpic];"
        f"[cutpic]split=2[cutkeep][cutblur];"
        f"[cutblur]scale={blur_px}:-2,gblur=sigma={_num(max(4.0, blur_px / 40))}[cutsoft0];"
        f"[cutsoft0][cutkeep]scale2ref=flags=bicubic[cutsoft][cutbase];"
        f"[cutsoft][cutmask]alphamerge[cutsoftalpha];"
        f"[cutbase][cutsoftalpha]overlay=0:0:format=auto[cutfilled];"
        f"[cutfilled]{','.join(post)}[v]"
    )
    return graph


def lasso_inputs(mask_path: Any, fps: int, seconds: float) -> list[str]:
    """The mask as a bounded still-image input, frame-aligned with the picture.

    Same pattern the renderer already uses for photos (`-loop 1 -framerate fps
    -t …`), so every stream in the graph is finite and starts at zero.
    """
    return [
        "-loop", "1", "-framerate", str(int(fps)),
        "-t", _num(max(0.04, seconds), 3), "-i", str(mask_path),
    ]


CROP_DETECT_RE = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
VIDEO_STREAM_RE = re.compile(r"Stream #\d+(?::\d+)?.*?Video:.*?(\d{2,6})x(\d{2,6})")


def parse_cropdetect(stderr: str, rotation: int = 0) -> dict[str, Any] | None:
    """Turn cropdetect's stderr into a crop rectangle in *rotated* fractions.

    cropdetect reports the last (most stable) proposal as `crop=w:h:x:y`, and
    the stream line carries the source dimensions. When the caller rotated the
    picture before detecting, the frame is turned too, so width and height swap
    for the quarter turns.
    """
    found = CROP_DETECT_RE.findall(stderr or "")
    if not found:
        return None
    width, height, x, y = (int(part) for part in found[-1])
    stream = VIDEO_STREAM_RE.search(stderr or "")
    if not stream:
        return None
    source_w, source_h = int(stream.group(1)), int(stream.group(2))
    if (rotation % 360) in (90, 270):
        source_w, source_h = source_h, source_w
    if width <= 0 or height <= 0 or source_w <= 0 or source_h <= 0:
        return None
    rect = {
        "x": clamp01(x / source_w),
        "y": clamp01(y / source_h),
        "w": clamp01(width / source_w),
        "h": clamp01(height / source_h),
    }
    # cropdetect rounds to the macroblock grid, so a full frame can come back a
    # pixel or two short; that is not a crop worth storing.
    covers = rect["w"] * rect["h"]
    return {"rect": rect, "source": {"width": source_w, "height": source_h}, "bars": covers < 0.985}


def cropdetect_command(ffmpeg_bin: str, source: str, rotation: Any, seconds: float) -> list[str]:
    """The probe command: turn the picture the way the user sees it, then look
    for bars. Two seconds is enough for a movie, and an image is one frame."""
    from .renderer import rotation_filter  # local import: renderer imports nothing here

    turn = rotation_filter(rotation)
    chain = f"{turn},cropdetect=limit=24:round=2:reset=0" if turn else "cropdetect=limit=24:round=2:reset=0"
    return [
        ffmpeg_bin, "-hide_banner", "-i", source,
        "-vf", chain, "-an", "-t", _num(max(0.5, seconds), 3), "-f", "null", "-",
    ]
