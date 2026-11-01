"""Photo collages — layout, choreography and the FFmpeg composite graph.

The layout maths (placements, mat sizes, animation curves) is the Python twin
of src/collageCore.ts: same inputs, same outputs, so the editor preview and
the rendered MP4 place every photo identically. The graph builder below turns
those numbers into FFmpeg filter chains (polaroid mat + soft shadow + animated
rotate/scale/overlay per photo), composited onto the slide's background colour
stream with the caption drawn on top.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .media import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, source_path
from .picture_crop import crop_filters, normalize_crop
from .picture_filters import picture_look
from .collage_reveal import (REVEALS, REVEAL_LENGTH, REVEAL_STEPS, geq_alpha, is_reveal, reveal_alpha,
                             reveal_length, reveal_progress, reveal_quantise)

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


def _smoothstep(v: float) -> float:
    """Ease in and out of a 0…1 progress: no jolt at either end, which is what a
    photo travelling to a picked position or angle needs."""
    return v * v * (3 - 2 * v)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _snap_native_aspect(ratio: float) -> float:
    """Snap a measured photo/video aspect (w/h) to the nearest canonical bucket.
    Landscape (>= 1/0.87 = 1.15) snaps to 16:9 or 4:3; portrait (<= 1/1.15) to
    9:16 or 3:4; anything in between stays square. Twin of snapNativeAspect()."""
    try:
        r = float(ratio)
    except (TypeError, ValueError):
        r = 0.0
    if not math.isfinite(r) or r <= 0:
        return 4.0 / 3.0
    if r >= 1.15:
        return 16.0 / 9.0 if abs(r - 16.0 / 9.0) < abs(r - 4.0 / 3.0) else 4.0 / 3.0
    if r <= 1.0 / 1.15:
        return 9.0 / 16.0 if abs(r - 9.0 / 16.0) < abs(r - 3.0 / 4.0) else 3.0 / 4.0
    return 1.0


# How many leading bytes of a still to scan for dimensions + EXIF. Phone JPEGs
# often carry a multi-megabyte ICC profile before the frame header; the
# orientation tag itself is almost always in the first APP1 segment.
_STILL_HEAD_LIMIT = 4_000_000
_EXIF_ORIENTATION = 0x0112
_EXIF_SUB_IFD = 0x8769


def _quarter_turn(rotation: float | None) -> bool:
    """True when a display-matrix rotation (degrees) swaps width and height.

    FFmpeg reports iPhone portrait clips as -90 or 270; either way the picture
    the viewer sees is the other way up from the stored pixels.
    """
    if rotation is None:
        return False
    try:
        turns = abs(float(rotation)) % 360.0
    except (TypeError, ValueError):
        return False
    return abs(turns - 90.0) < 1.5 or abs(turns - 270.0) < 1.5


def _display_size(width: int, height: int, swap: bool) -> tuple[int, int]:
    if swap and width > 0 and height > 0:
        return height, width
    return width, height


def _tiff_orientation(data: bytes, base: int = 0, depth: int = 0) -> int | None:
    """EXIF Orientation (1–8) from a TIFF header at ``data[base:]``, or None.

    Orientation lives in IFD0. A few writers park it only in the Exif sub-IFD,
    so that pointer (tag 0x8769) is followed once. Offsets are relative to the
    TIFF header, which for a JPEG APP1 segment starts after the ``Exif\\0\\0``
    prefix — the caller passes that base.
    """
    if depth > 3 or base < 0 or base + 8 > len(data):
        return None
    endian = data[base:base + 2]
    if endian == b"II":
        order = "little"
    elif endian == b"MM":
        order = "big"
    else:
        return None

    def u16(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 2], order)

    def u32(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 4], order)

    if u16(base + 2) != 42:
        return None
    return _tiff_ifd_orientation(data, base, base + u32(base + 4), u16, u32, depth)


def _tiff_ifd_orientation(data: bytes, base: int, ifd: int, u16, u32, depth: int) -> int | None:
    if depth > 3 or ifd < 0 or ifd + 2 > len(data):
        return None
    count = u16(ifd)
    if count > 256:
        return None
    found: int | None = None
    sub_ifd: int | None = None
    for i in range(count):
        entry = ifd + 2 + i * 12
        if entry + 12 > len(data):
            break
        tag = u16(entry)
        typ = u16(entry + 2)
        cnt = u32(entry + 4)
        if tag == _EXIF_ORIENTATION and cnt >= 1 and typ in (1, 3, 4):
            value = _tiff_inline(data, base, entry, typ, u16, u32)
            if value is not None and 1 <= value <= 8:
                found = value
        elif tag == _EXIF_SUB_IFD and typ == 4 and cnt >= 1:
            sub_ifd = base + u32(entry + 8)
    # A non-default orientation in IFD0 wins. Otherwise look in the Exif
    # sub-IFD — some cameras write the tag only there.
    if found not in (None, 1):
        return found
    if sub_ifd is not None and sub_ifd != ifd:
        nested = _tiff_ifd_orientation(data, base, sub_ifd, u16, u32, depth + 1)
        if nested not in (None, 1):
            return nested
    return found


def _tiff_inline(data: bytes, base: int, entry: int, typ: int, u16, u32) -> int | None:
    """The first value of a SHORT/LONG/BYTE IFD entry (inline, or via offset)."""
    size = {1: 1, 3: 2, 4: 4}.get(typ)
    if size is None:
        return None
    # Values of 4 bytes or less are stored in the entry itself.
    if size <= 4:
        raw = entry + 8
    else:
        raw = base + u32(entry + 8)
    if raw < 0 or raw + size > len(data):
        return None
    if typ == 1:
        return data[raw]
    if typ == 3:
        return u16(raw)
    return u32(raw)


def _orientation_in_exif_blob(blob: bytes) -> int | None:
    """Orientation from a raw TIFF EXIF blob, or one prefixed with ``Exif\\0\\0``."""
    if blob.startswith(b"Exif\x00\x00"):
        return _tiff_orientation(blob, 6)
    return _tiff_orientation(blob, 0)


def _jpeg_display_size(data: bytes) -> tuple[int, int] | None:
    """Displayed (width, height) of a JPEG, after the EXIF orientation tag.

    Stored pixels of a phone portrait are usually landscape; the browser (and
    FFmpeg's autorotate) turn them upright. The mat has to use that displayed
    size or the upright picture is letterboxed into a landscape frame.
    """
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    orientation = 1
    size: tuple[int, int] | None = None
    i = 2
    while i + 3 < len(data):
        if data[i] != 0xFF:
            break
        while i < len(data) and data[i] == 0xFF:
            i += 1
        if i >= len(data):
            break
        marker = data[i]
        i += 1
        # Standalone markers (no length): TEM and the RSTn restarts.
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD8, 0xD9):  # a nested SOI, or EOI
            if marker == 0xD9:
                break
            continue
        if i + 2 > len(data):
            break
        length = int.from_bytes(data[i:i + 2], "big")
        if length < 2 or i + length > len(data):
            break
        payload = data[i + 2:i + length]
        if marker == 0xE1 and payload.startswith(b"Exif\x00\x00"):
            found = _tiff_orientation(payload, 6)
            if found:
                orientation = found
        elif marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            # SOF: precision, height, width. This is the stored pixel size.
            if len(payload) >= 5:
                height = int.from_bytes(payload[1:3], "big")
                width = int.from_bytes(payload[3:5], "big")
                if width and height:
                    size = (width, height)
        if marker == 0xDA:  # SOS — entropy-coded data follows, headers are done
            break
        i += length
    if size is None:
        return None
    width, height = size
    return _display_size(width, height, orientation in (5, 6, 7, 8))


def _png_display_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = height = 0
    orientation = 1
    i = 8
    while i + 12 <= len(data):
        length = int.from_bytes(data[i:i + 4], "big")
        kind = data[i + 4:i + 8]
        if length < 0 or i + 12 + length > len(data):
            break
        chunk = data[i + 8:i + 8 + length]
        if kind == b"IHDR" and len(chunk) >= 8:
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
        elif kind == b"eXIf":
            found = _orientation_in_exif_blob(chunk)
            if found:
                orientation = found
        elif kind == b"IEND":
            break
        i += 12 + length
    if not width or not height:
        return None
    return _display_size(width, height, orientation in (5, 6, 7, 8))


def _webp_display_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    width = height = 0
    orientation = 1
    i = 12
    while i + 8 <= len(data):
        kind = data[i:i + 4]
        size = int.from_bytes(data[i + 4:i + 8], "little")
        chunk = data[i + 8:i + 8 + size]
        if len(chunk) < size:
            break
        if kind == b"VP8X" and size >= 10:
            width = 1 + int.from_bytes(chunk[4:7], "little")
            height = 1 + int.from_bytes(chunk[7:10], "little")
        elif kind == b"VP8 " and not width and size >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
            width = int.from_bytes(chunk[6:8], "little") & 0x3FFF
            height = int.from_bytes(chunk[8:10], "little") & 0x3FFF
        elif kind == b"VP8L" and not width and size >= 5 and chunk[0] == 0x2F:
            bits = int.from_bytes(chunk[1:5], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
        elif kind == b"EXIF":
            found = _orientation_in_exif_blob(chunk)
            if found:
                orientation = found
        i += 8 + size + (size & 1)
    if not width or not height:
        return None
    return _display_size(width, height, orientation in (5, 6, 7, 8))


def _tiff_display_size(data: bytes) -> tuple[int, int] | None:
    """Displayed size of a TIFF/DNG-style still. Width/length from IFD0."""
    if len(data) < 8 or data[:2] not in (b"II", b"MM"):
        return None
    order = "little" if data[:2] == b"II" else "big"

    def u16(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 2], order)

    def u32(offset: int) -> int:
        return int.from_bytes(data[offset:offset + 4], order)

    if u16(2) != 42:
        return None
    ifd = u32(4)
    if ifd + 2 > len(data):
        return None
    count = u16(ifd)
    if count > 512:
        return None
    width = height = 0
    for i in range(count):
        entry = ifd + 2 + i * 12
        if entry + 12 > len(data):
            break
        tag = u16(entry)
        typ = u16(entry + 2)
        if tag not in (0x0100, 0x0101) or typ not in (1, 3, 4):
            continue
        value = _tiff_inline(data, 0, entry, typ, u16, u32)
        if not value:
            continue
        if tag == 0x0100:
            width = value
        else:
            height = value
    if not width or not height:
        return None
    orientation = _tiff_orientation(data, 0) or 1
    return _display_size(width, height, orientation in (5, 6, 7, 8))


def _bmp_display_size(data: bytes) -> tuple[int, int] | None:
    # BMP has no orientation tag; a negative height is just top-down storage.
    if len(data) < 26 or data[:2] != b"BM":
        return None
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    if width > 0 and height != 0:
        return width, abs(height)
    return None


def _still_display_size(path: Path) -> tuple[int, int] | None:
    """Displayed pixel size of a still, or None if the header can't be read.

    This is the size the browser reports as naturalWidth/naturalHeight: EXIF
    orientations 5–8 swap the stored width and height. FFmpeg 8 autorotates
    from the same tag before any user filter, so the collage mat must be
    sized from this displayed size. Using the raw pixel size letterboxes an
    upright portrait into a landscape frame (white bars on both sides).
    """
    try:
        with path.open("rb") as handle:
            head = handle.read(_STILL_HEAD_LIMIT)
    except OSError:
        return None
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg"} or head[:2] == b"\xff\xd8":
        return _jpeg_display_size(head)
    if ext == ".png" or head[:8] == b"\x89PNG\r\n\x1a\n":
        return _png_display_size(head)
    if ext == ".webp" or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return _webp_display_size(head)
    if ext in {".tif", ".tiff"} or head[:2] in (b"II", b"MM"):
        found = _tiff_display_size(head)
        if found:
            return found
    if ext == ".bmp" or head[:2] == b"BM":
        return _bmp_display_size(head)
    return None


def _aspect_from_probe_json(data: dict[str, Any]) -> float | None:
    """Displayed w/h from an ffprobe JSON document.

    Stream width/height are the stored size. A display-matrix rotation (videos)
    or an Orientation side-data rotation (a decoded still) is applied the same
    way FFmpeg's autorotate filter turns the frames before ``scale``.
    """
    width = height = 0
    rotation: float | None = None

    def take_rotation(raw: Any) -> None:
        nonlocal rotation
        if rotation is not None or raw is None:
            return
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return
        if math.isfinite(value):
            rotation = value

    containers: list[dict[str, Any]] = []
    for key in ("streams", "frames"):
        for item in data.get(key) or []:
            if isinstance(item, dict):
                containers.append(item)
    for item in containers:
        if not width or not height:
            try:
                w = int(item.get("width") or 0)
                h = int(item.get("height") or 0)
            except (TypeError, ValueError):
                w = h = 0
            if w > 0 and h > 0:
                width, height = w, h
        tags = item.get("tags") if isinstance(item.get("tags"), dict) else {}
        take_rotation(tags.get("rotate"))
        for side in item.get("side_data_list") or []:
            if isinstance(side, dict):
                take_rotation(side.get("rotation"))
    if not width or not height:
        return None
    shown_w, shown_h = _display_size(width, height, _quarter_turn(rotation))
    return shown_w / shown_h


def _probe_aspect(path: Path, ffprobe_bin: str) -> float | None:
    """Displayed aspect (w/h) of a photo or video. None if unreadable.

    Returns the ratio the preview measures (browser naturalWidth/naturalHeight,
    which already applies EXIF orientation and video rotation) — not the raw
    stored pixels, and not snapped to a bucket — so a 'native' mat matches the
    picture FFmpeg actually draws. A portrait phone photo stored as landscape
    pixels with Orientation=6 must come back portrait; otherwise the upright
    picture is padded into a landscape mat with white bars on both sides.
    """
    try:
        ext = path.suffix.lower()
        if ext in IMAGE_EXTENSIONS or ext == "":
            shown = _still_display_size(path)
            if shown and shown[0] and shown[1]:
                return shown[0] / shown[1]
            # Pillow is optional. Image.size is the stored size and ignores
            # EXIF, which is exactly the bug — only trust it after transposing.
            try:
                from PIL import Image, ImageOps  # type: ignore
                with Image.open(path) as im:
                    turned = ImageOps.exif_transpose(im)
                    if turned is not None:
                        im = turned
                    w, h = im.size
                if w and h:
                    return w / h
            except Exception:  # noqa: BLE001 - fall through to ffprobe
                pass
        probe = shutil.which(ffprobe_bin) if ffprobe_bin else None
        if not probe:
            return None
        # Stream header only — no frame decode. A movie's rotation is display-
        # matrix side data (or the legacy rotate tag) on the stream; stills
        # are handled above, because a JPEG's EXIF orientation is not a stream
        # field. The plain width/height query is the fallback if a build
        # rejects the side-data selector.
        for entries in (
            "stream=width,height:stream_tags=rotate:stream_side_data=rotation",
            "stream=width,height",
        ):
            result = subprocess.run(
                [probe, "-hide_banner", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", entries, "-of", "json", str(path)],
                capture_output=True, text=True, timeout=10.0, check=False,
            )
            aspect = _aspect_from_probe_json(json.loads(result.stdout or "{}"))
            if aspect:
                return aspect
        return None
    except Exception:  # noqa: BLE001 - never fail a render over metadata
        return None


# Valid photo shapes (twin of COLLAGE_SHAPES_ALL in src/collageCore.ts).
SHAPES = ("native", "4:3", "square", "3:4", "16:9", "9:16", "3:2", "2:3")


def _photo_shape(spec: dict[str, Any], i: int) -> str:
    """Effective shape of photo i: its own override when valid, else the
    collage-wide default (twin of photoShape() in src/collageCore.ts)."""
    photos = spec.get("photos") or []
    p = photos[i] if 0 <= i < len(photos) else None
    s = p.get("shape") if isinstance(p, dict) else None
    if s in SHAPES:
        return str(s)
    shape = spec.get("shape")
    return str(shape) if shape in SHAPES else "4:3"


def _photo_aspect(shape: str, native_ratio: float | None = None) -> float:
    if shape == "native":
        # Measured photos use their real aspect so the mat fits exactly; when
        # probing failed (None) fall back to a safe 4:3 default.
        if native_ratio is not None and math.isfinite(native_ratio) and native_ratio > 0:
            return native_ratio
        return 4.0 / 3.0
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


def _mat_line_for(shape: str, fr: dict[str, Any], aspect: float, native_ratio: float | None = None) -> tuple[float, float]:
    """Mat height line for a given frame. `native_ratio` is the measured w/h of
    photo i; used when shape == 'native'. Twin of matLineFor()."""
    b = frame_border_frac(fr) * 100.0
    inv = 1.0 / _photo_aspect(shape, native_ratio)
    if fr["shape"] == "polaroid":
        return (inv + 0.205) * aspect, (b - 2 * b * inv) * aspect
    return inv * aspect, (2 * b - 2 * b * inv) * aspect


def mat_line(shape: str, fr: dict[str, Any], aspect: float) -> tuple[float, float]:
    """Mat height (% of frame HEIGHT) as a line of the mat width w (% of frame
    width): height = k * w + c. The border is a fixed share of the FRAME, so it
    adds the constant c; the polaroid caption strip stays proportional to the
    mat. Twin of matLine() in src/collageCore.ts."""
    return _mat_line_for(shape, fr, aspect)


def _template_slot_width(slot: dict[str, float], spec: dict[str, Any], i: int, aspect: float, ratios=None) -> float:
    """Widest mat that fits the slot's box for photo i's frame — twin of templateSlotWidth()."""
    h = slot.get("h")
    if h is None or not h > 0:
        return slot["w"]
    photos = spec.get("photos") or []
    mat_k, mat_c = _mat_line_for(_photo_shape(spec, i), photo_frame(spec, photos[i] if i < len(photos) else None), aspect, ratios[i] if ratios and i < len(ratios) else None)
    return min(slot["w"], (h - mat_c) / mat_k)


def photo_frame(spec: dict[str, Any] | None, photo: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolved frame for a photo — twin of photoFrame()."""
    a = (spec or {}).get("frame") if isinstance((spec or {}).get("frame"), dict) else {}
    b = (photo or {}).get("frame") if isinstance((photo or {}).get("frame"), dict) else {}
    shape = b.get("shape") if b.get("shape") in FRAME_SHAPES else (a.get("shape") if a.get("shape") in FRAME_SHAPES else "rect")

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
        width = 0.5
    else:
        width = max(0.0, min(12.0, raw_w))
    color = b.get("color") if isinstance(b.get("color"), str) and len(b.get("color")) == 7 and b.get("color").startswith("#") else (
        a.get("color") if isinstance(a.get("color"), str) and len(a.get("color")) == 7 and a.get("color").startswith("#") else "#ffffff")
    raw_r = _num(b, "radius")
    if raw_r is None:
        raw_r = _num(a, "radius")
    radius = 5.0 if raw_r is None else max(0.0, min(50.0, raw_r))
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
    mat_k, mat_c = mat_line(shape, photo_frame({"frame": frame} if frame else {}, None), aspect)
    return mat_k * w + mat_c


def arrangement_slots(spec: dict[str, Any]) -> list[int]:
    """Arrangement slot of every photo — twin of arrangementSlots() in collageCore.ts.

    Stored slots are used when they form a valid permutation; photos with a
    missing, duplicate or out-of-range slot take the lowest free slots in list order.
    """
    n = len(spec.get("photos") or [])
    out = [-1] * n
    taken: set[int] = set()
    for i, p in enumerate(spec.get("photos") or []):
        v = p.get("slot") if isinstance(p, dict) else None
        if isinstance(v, bool) or not isinstance(v, int):
            continue
        if 0 <= v < n and v not in taken:
            out[i] = v
            taken.add(v)
    free = 0
    for i in range(n):
        if out[i] >= 0:
            continue
        while free in taken:
            free += 1
        out[i] = free
        taken.add(free)
    return out


def placements(spec: dict[str, Any], aspect: float, ratios=None) -> list[dict[str, float]]:
    """Resting placement of every photo — twin of placements() in collageCore.ts:
    the layout's slots, with each photo moved onto its arrangement slot (its own
    size multiplier stays with the photo). Free positions are per photo."""
    raw = _base_placements(spec, aspect, ratios)
    if spec.get("layout") == "free" or len(raw) < 2:
        return raw
    slots = arrangement_slots(spec)
    if all(slot == i for i, slot in enumerate(slots)):
        return raw
    out = []
    for i, slot in enumerate(slots):
        frm = raw[slot]
        base = frm["w"] / photo_size(spec, slot)
        out.append({"cx": frm["cx"], "cy": frm["cy"], "rot": frm["rot"], "w": base * photo_size(spec, i)})
    return out


def _photo_mat_height(spec: dict[str, Any], i: int, w: float, aspect: float, ratios=None) -> float:
    photos = spec.get("photos") or []
    fr = photo_frame(spec, photos[i] if i < len(photos) else None)
    k, c = _mat_line_for(_photo_shape(spec, i), fr, aspect, ratios[i] if ratios and i < len(ratios) else None)
    return k * w + c


def _base_placements(spec: dict[str, Any], aspect: float, ratios=None) -> list[dict[str, float]]:
    """Layout slots of the photos, before the arrangement remap."""
    n = max(1, len(spec.get("photos") or []))
    seed = int(spec.get("seed") or 1)
    shape = str(spec.get("shape") or "4:3")
    out: list[dict[str, float]] = []

    def cells(count: int):
        cols = max(1, math.ceil(math.sqrt(count)))
        rows = math.ceil(count / cols)
        margin_x, margin_y = 7.0, 8.0
        return cols, rows, (100 - 2 * margin_x) / cols, (100 - 2 * margin_y) / rows

    # Mat height (% of frame HEIGHT) = line_k * w + line_c for the collage's
    # default frame — twin of matLine() in placements() (collageCore.ts): every
    # layout that fits mats into cells limits their height with it. For
    # 'native' shape this is the default (4:3) line used for grid cells;
    # masonry/photowall use per-photo heights via mat_h_of.
    line_k, line_c = mat_line(shape, photo_frame(spec, None), aspect)

    def fit_width(cell_w: float, cell_h: float) -> float:
        # 80 % of the cell width, and no taller than 90 % of the cell height
        return min(cell_w * 0.8, (cell_h * 0.9 - line_c) / line_k)

    layout = str(spec.get("layout") or "stack")
    # Per-photo size: each photo's mat is the layout width times its
    # multiplier (bigger photos overlap their neighbours — that is the point).
    def width_of(base: float, i: int) -> float:
        return base * photo_size(spec, i)

    def mat_h_of(w: float, i: int) -> float:
        return _photo_mat_height(spec, i, w, aspect, ratios)

    if layout == "grid":
        cols, _, cell_w, cell_h = cells(n)
        w = fit_width(cell_w, cell_h)
        for i in range(n):
            col, row = i % cols, i // cols
            out.append({"cx": 7 + cell_w * (col + 0.5), "cy": 8 + cell_h * (row + 0.5), "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 10})
    elif layout == "scatter":
        cols, _, cell_w, cell_h = cells(n)
        w = fit_width(cell_w, cell_h) * 0.94
        for i in range(n):
            col, row = i % cols, i // cols
            cx = 7 + cell_w * (col + 0.28 + 0.44 * hash01(seed, i, 29))
            cy = 8 + cell_h * (row + 0.28 + 0.44 * hash01(seed, i, 31))
            out.append({"cx": cx, "cy": cy, "w": width_of(w, i), "rot": (hash01(seed, i, 37) - 0.5) * 26})
    elif layout == "filmstrip":
        # A horizontal band of overlapping frames, like film frames edge to
        # edge — one row up to 5 photos, two rows beyond.
        rows = 1 if n <= 5 else 2
        per_row = math.ceil(n / rows)
        first_row = n - per_row * (rows - 1)
        for i in range(n):
            row = 0 if i < first_row else 1
            cols = first_row if row == 0 else per_row
            col = i if row == 0 else i - first_row
            cell_wr = (100 - 2 * 5) / cols
            cell_h = (100 - 2 * 12) / rows
            w = min(cell_wr * 1.1, (cell_h * 0.9 - line_c) / line_k)
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
            w_max = 48.0 if _photo_shape(spec, 0) == "native" else min(40.0, (76 - line_c) / line_k)
            out.append({"cx": 50.0, "cy": 50.0, "w": width_of(w_max, 0), "rot": 0.0})
        else:
            cols = 2 if n <= 2 else (3 if n <= 9 else 4)
            margin_x = 6.0
            cell_w = (100 - 2 * margin_x) / cols
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
                    mh = mat_h_of(ws[i] * scale, i)
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
        # Brick-tight: the gutter between rows equals the gutter between columns.
        w = min(cell_w - gap, (cell_h - gap - line_c) / line_k)
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
            ox = (0.14 if row % 2 else -0.14) * cell_w
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
        # A compact block of equal tiles with a small gutter — twin of the
        # photowall branch in collageCore.ts: the column count that gives the
        # largest tile (ties: fewest empty cells), block and short last row
        # centred. For 'native' shape we size by the TALLEST photo so every
        # portrait mat fits in its cell.
        try:
            gap = float(spec.get("gap")) if spec.get("gap") is not None and not isinstance(spec.get("gap"), bool) else 0.7
        except (TypeError, ValueError):
            gap = 0.7
        gap = max(0.0, min(12.0, gap if math.isfinite(gap) else 0.7))

        def tile_h(w: float) -> float:
            h = 0.0
            for i in range(n):
                h = max(h, mat_h_of(w, i))
            return h

        best_cols, best_rows, best_w, best_empty = 1, n, 0.0, 0
        for cols in range(1, n + 1):
            rows = math.ceil(n / cols)
            w_avail = (100 - gap * (cols + 1)) / cols
            lo, hi = 2.0, max(2.0, w_avail)
            for _ in range(12):
                mid = (lo + hi) / 2
                if tile_h(mid) * rows + gap * (rows + 1) <= 100:
                    lo = mid
                else:
                    hi = mid
            w = lo
            empty = cols * rows - n
            if w > best_w + 1e-9 or (abs(w - best_w) <= 1e-9 and empty < best_empty):
                best_cols, best_rows, best_w, best_empty = cols, rows, w, empty
        cols, rows = best_cols, best_rows
        w = max(2.0, best_w)
        mat_h = tile_h(w)
        y0 = (100 - (rows * mat_h + (rows - 1) * gap)) / 2
        for i in range(n):
            col, row = i % cols, i // cols
            in_row = n - cols * (rows - 1) if row == rows - 1 else cols
            x0 = (100 - (in_row * w + (in_row - 1) * gap)) / 2
            out.append({"cx": x0 + w / 2 + col * (w + gap), "cy": y0 + mat_h / 2 + row * (mat_h + gap),
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
            try:
                own = float(p.get("w")) if p.get("w") is not None and not isinstance(p.get("w"), bool) else float("nan")
            except (TypeError, ValueError):
                own = float("nan")
            base = max(FREE_MIN_W, min(FREE_MAX_W, own)) if math.isfinite(own) and own > 0 else w
            out.append({"cx": max(0.0, min(100.0, cx)), "cy": max(0.0, min(100.0, cy)), "w": width_of(base, i), "rot": rot})
    elif layout == "template":
        # Each mat is scaled down to fit its slot's box (width and height), so
        # the mosaic keeps its rows and columns whatever the photo shape or
        # frame style; the per-photo size multiplier still applies on top.
        slots = _template_slots(spec.get("template"), n)
        for i in range(n):
            out.append({"cx": slots[i]["cx"], "cy": slots[i]["cy"], "w": width_of(_template_slot_width(slots[i], spec, i, aspect, ratios), i), "rot": slots[i]["rot"]})
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


# Bounds for a photo's own Free-layout width (``photo["w"]``).
FREE_MIN_W = 4.0
FREE_MAX_W = 100.0


def free_from_displayed(spec: dict[str, Any], aspect: float, keep_stored: bool, ratios=None) -> list[dict[str, Any]]:
    """Photos of a Free arrangement seeded from what another arrangement shows
    (twin of ``freeFromDisplayed``): each photo keeps its displayed centre, tilt
    and mat size — the size stored as the photo's own width before its size
    multiplier. ``keep_stored`` prefers previously stored free values."""
    photos = list(spec.get("photos") or [])
    if spec.get("layout") == "free":
        return photos
    pls = placements(spec, aspect, ratios)
    out = []
    for i, p in enumerate(photos):
        if i < len(pls):
            pl = pls[i]
            shown = {"cx": pl["cx"], "cy": pl["cy"], "rot": pl["rot"], "w": pl["w"] / photo_size(spec, i)}
        else:
            shown = {"cx": 50.0, "cy": 50.0, "rot": 0.0, "w": None}
        entry = dict(p)
        for key in ("cx", "cy", "rot", "w"):
            if keep_stored and p.get(key) is not None:
                continue
            if shown[key] is None:
                entry.pop(key, None)
            else:
                entry[key] = shown[key]
        out.append(entry)
    return out


def photo_size(spec: dict[str, Any], i: int) -> float:
    """Photo i's mat size multiplier — stored value (clamped to 0.5..3) or 1.

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
    return max(0.5, min(3.0, v))


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


# ---------------------------------------------------------------------------
# The paper family — geometry entrances, one row each
# ---------------------------------------------------------------------------

# The same twelve rows as `COLLAGE_GEOM` in src/collageCore.ts, and the ONLY
# place the render gets its motion from as well: `photo_state` evaluates these
# curves as numbers and the filter graph builds the same curve as FFmpeg text,
# so an entrance cannot be a different move in the MP4 than in the editor. A
# curve is (kind, amplitude[, base]); the value is `base + amp × curve(q)`, with
# base 0 for the offsets and 1 for the scales. `hinge`/`recip` carry a floor of
# their own because the width a hinge leaves you is the sine of the angle it
# travelled, and it must not reach 0 in the first frames.
# The hand-written entrances whose sprite changes size while they arrive, and so
# need the render's size ladder rather than one static scale. The geometry rows
# report their own scales (`GEOM_SCALES`, derived from the table), but these are
# expressions inside photo_state(), so this is the list a new hand-written
# entrance has to be added to — tests/test_collage_speed.py checks that anyone who
# forgets is caught, because a size the ladder does not sample is a size the MP4
# never shows.
SCALE_ANIMS = ("pop", "flip", "zoom", "fold", "ink", "brush", "glitch", "tumble")

# The eight look-based entrances, the same rows as `COLLAGE_LOOK_ENTRANCE` in
# src/collageCore.ts. A channel is a curve on the look's own scale — 1 is
# untouched for `sat`/`bright`, degrees for `hue` — and `inv` has no amount,
# because FFmpeg's `negate` is on or off and so is the preview's CSS invert().
LOOK_ENTRANCE: dict[str, dict[str, Any]] = {
    "develop": {"length": 0.75, "fade": 0.3, "label": "Develop", "bright": ("one-smooth", -0.88, 1),
                "sat": ("one-smooth", -0.75, 1)},
    "flashIn": {"length": 0.4, "fade": 0.12, "label": "Flash", "bright": ("one-smooth", 1.2, 1)},
    "colourFlood": {"length": 0.6, "fade": 0.2, "label": "Colour flood", "sat": ("one-smooth", -0.95, 1)},
    "hueSwing": {"length": 0.65, "fade": 0.18, "label": "Hue swing", "hue": ("one-smooth", 120)},
    "negative": {"length": 0.45, "fade": 0.15, "label": "Negative", "inv": True},
    "shadowLift": {"length": 0.6, "fade": 0.25, "label": "Out of shadow",
                   "bright": ("one-smooth", -0.85, 1)},
    "glowIn": {"length": 0.5, "fade": 0.16, "label": "Glow", "bright": ("one-smooth", 0.45, 1),
               "sat": ("one-smooth", -0.3, 1)},
    "xerox": {"length": 0.6, "fade": 0.18, "label": "Photocopy", "bright": ("one-back", -0.35, 1.35),
              "sat": ("one-smooth", -0.95, 1)},
}
LOOK_ENTRANCE_ANIMS = tuple(LOOK_ENTRANCE)


def look_entrance(anim: str) -> dict[str, Any] | None:
    """The row for a look-based entrance id, or None for any other animation."""
    return LOOK_ENTRANCE.get(anim)


def is_look_entrance(anim: str) -> bool:
    return anim in LOOK_ENTRANCE


GEOM: dict[str, dict[str, Any]] = {
    "cube": {"length": 0.5, "dx": ("settle3", 18), "scaleX": ("hinge", 0.25, 0),
             "dim": ("settle3", 0.15), "fade": 0.14},
    "book": {"length": 0.55, "dx": ("one-smooth", -12), "rot": ("one-smooth", -5),
             "scaleX": ("hinge", 0.06, 1), "dim": ("one-smooth", 0.12), "fade": 0.12},
    "door": {"length": 0.55, "dx": ("one-smooth", -9), "scaleX": ("hinge", 0.1, 1),
             "dim": ("one-smooth", 0.25), "fade": 0.1},
    # Only the height changes, which this model can express exactly: width is
    # scale × scaleX and height is scale, so height k at width 1 is scale k and
    # scaleX 1/k.
    "unfold": {"length": 0.5, "scale": ("hinge", 0.08, 0), "scaleX": ("recip", 0.08, 0), "fade": 0.12},
    "rollover": {"length": 0.6, "dy": ("settle3", 28), "rot": ("settle3", -28),
                 "scale": ("settle3", -0.18), "fade": 0.16},
    "spiral": {"length": 0.65, "rot": ("settle3", 90), "scale": ("one-back", -0.9, 1), "fade": 0.18},
    "swish": {"length": 0.5, "dx": ("one-back", -30), "rot": ("settle2", -14), "fade": 0.1},
    "glide": {"length": 0.7, "dx": ("one-smooth", 16), "dy": ("one-smooth", 12),
              "rot": ("one-smooth", 3), "fade": 0.28},
    # four straight travels, no tilt and no scale — see the note in COLLAGE_GEOM
    "fromLeft": {"length": 0.5, "dx": ("one-smooth", -34), "fade": 0.16},
    "fromRight": {"length": 0.5, "dx": ("one-smooth", 34), "fade": 0.16},
    "fromTop": {"length": 0.5, "dy": ("one-smooth", -26), "fade": 0.16},
    "fromBottom": {"length": 0.5, "dy": ("one-smooth", 26), "fade": 0.16},
    "peek": {"length": 0.5, "dy": ("settle3", 16), "rot": ("settle3", 7),
             "scaleX": ("settle3", -0.1), "fade": 0.14},
    "boomerang": {"length": 0.7, "dx": ("one-back", -20), "dy": ("one-back", -9),
                  "rot": ("settle3", 24), "scale": ("one-back", -0.38, 1), "fade": 0.16},
    # The bow of the path is a half-sine of the part still to travel, so it starts
    # and ends on the straight line and is only ever in between.
    "sling": {"length": 0.55, "dx": ("one-smooth", 24), "dy": ("bow", -18),
              "rot": ("settle2", -16), "fade": 0.12},
    "thread": {"length": 0.7, "dx": ("settle3", -16), "dy": ("settle3", -13),
               "rot": ("settle3", -55), "scale": ("settle3", -0.3), "fade": 0.14},
}
GEOM_ANIMS = tuple(GEOM)
# Which of them change a size, and therefore have to ride the render's size
# ladder. Derived from the rows, so adding one with a scale cannot forget it.
GEOM_SCALES = frozenset(a for a, r in GEOM.items() if "scale" in r or "scaleX" in r)


def _geom_curve(kind: str, q: float, smooth: bool = False) -> float:
    """One curve of the shared vocabulary, at `q`. `_geom_expr` below is the same
    list as FFmpeg text; the two are checked against each other by
    tests/test_collage_twin.py."""
    if kind == "settle3":
        return (1 - q) ** 3
    if kind == "settle2":
        return (1 - q) ** 2
    if kind == "smooth":
        return q * q * (3 - 2 * q)
    if kind == "one-smooth":
        return 1 - q * q * (3 - 2 * q)
    if kind == "one-back":
        return 1 - (1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2)
    if kind == "bow":
        return math.sin(math.pi * (1 - q))
    if kind in ("hinge", "recip"):
        inner = q * q * (3 - 2 * q) if smooth else q
        fold = max(0.0, math.sin(math.pi / 2 * inner))
        return fold
    raise ValueError(f"unknown entrance curve: {kind}")


def _geom(spec_curve: Any, q: float, base: float) -> float:
    """`base + amp × curve(q)`, and a `hinge`/`recip` floor instead."""
    if not spec_curve:
        return base
    kind = spec_curve[0]
    if kind == "hinge":
        return max(spec_curve[1], _geom_curve("hinge", q, bool(spec_curve[2])))
    if kind == "recip":
        return 1 / max(spec_curve[1], _geom_curve("hinge", q, bool(spec_curve[2])))
    amp = spec_curve[1] if len(spec_curve) > 1 else 1.0
    # a row may name its own base (`xerox` starts at 1.35 brightness, not at the
    # 1 a scale channel rests at); when it does not, the caller's default stands
    b = spec_curve[2] if len(spec_curve) > 2 else base
    return b + amp * _geom_curve(kind, q)


def _geom_expr(spec_curve: Any, q: str, span: float, base: float = 0.0, signed: bool = True) -> str:
    """A curve as an FFmpeg expression, for the overlay's x/y. `span` converts
    the percent-of-frame unit the preview works in into pixels, exactly as the
    hand-written `drop`/`slide` terms above do; `rot` passes span=1 because
    degrees are already the unit."""
    if not spec_curve:
        return ""
    kind = spec_curve[0]
    if kind in ("hinge", "recip"):
        raise ValueError("hinge curves belong on the size ladder, not on an offset")
    amp = spec_curve[1] if len(spec_curve) > 1 else 1.0
    b = spec_curve[2] if len(spec_curve) > 2 else base
    mag = abs(amp) * span
    head = _n(b) if b else ""
    if not mag:
        return head
    sign = "+" if amp > 0 else "-"
    if kind == "settle3":
        c = f"pow(1-{q},3)"
    elif kind == "settle2":
        c = f"pow(1-{q},2)"
    elif kind == "smooth":
        c = f"pow({q},2)*(3-2*{q})"
    elif kind == "one-smooth":
        c = f"(1-pow({q},2)*(3-2*{q}))"
    elif kind == "one-back":
        c = f"(1-(1+2.70158*pow({q}-1,3)+1.70158*pow({q}-1,2)))"
    elif kind == "bow":
        c = f"sin(PI*(1-{q}))"
    else:
        raise ValueError(f"no FFmpeg form for entrance curve {kind!r}")
    if not head:
        # a term that starts an expression needs no leading `+`; one that is
        # appended to the overlay's x= or to another term does
        return f"{_n(mag)}*{c}" if not signed else f"+{_n(mag)}*{c}"
    return f"{head}{sign}{_n(mag)}*{c}"


# How long an entrance runs until the photo is fully at rest.
ENTRANCE_LENGTH = {
    "drop": 0.55, "pop": 0.5, "swing": 2.0, "flip": 0.45, "none": 0.0,
    "fade": 0.45, "slide": 0.5, "rise": 0.5, "tumble": 0.6, "zoom": 0.55,
    "fold": 0.55, "glitch": 0.5, "ink": 0.55, "brush": 0.5,
    # …and the reveals take theirs from the shared table, exactly like
    # ENTRANCE_LENGTH in src/collageCore.ts does: one source for how long a
    # sweep takes, so the slide's length and the ladder's sampling cannot use two
    # different numbers for the same reveal.
    **{a: r["length"] for a, r in GEOM.items()},
    **{a: r["length"] for a, r in LOOK_ENTRANCE.items()},
    **REVEAL_LENGTH,
}
# One entrance's alpha ramp in seconds, at speed 1 — `ENTRANCE_FADE` in
# src/collageCore.ts. `photo_state` reads it as the divisor of a ramp and
# `collage_graph` emits it as `fade=t=in:d=`, from this table, so the two cannot
# fade a photo in at different rates; `fade` and `ink` are the two whose preview
# alpha is an eased curve, and the export keeps the linear ramp on purpose.
ENTRANCE_FADE = {
    "drop": 0.22, "pop": 0.18, "swing": 0.15, "flip": 0.15, "none": 0.0, "fade": 0.45,
    "slide": 0.18, "rise": 0.18, "tumble": 0.16, "zoom": 0.16, "fold": 0.14, "glitch": 0.12,
    "ink": 0.4, "brush": 0.16,
    **{a: r["fade"] for a, r in GEOM.items()},
    **{a: r["fade"] for a, r in LOOK_ENTRANCE.items()},
    **{a: 0.0 for a in REVEALS},
}
# The same range the exits use: one slider grammar for arriving and for leaving.
ENTRANCE_SPEED_MIN, ENTRANCE_SPEED_MAX = 0.05, 2.0


def anim_speed(spec: dict[str, Any], i: int | None = None) -> float:
    """The speed multiplier on photo i's entrance (twin of `animSpeed`)."""
    raw = spec.get("animSpeed") if i is None else (
        (spec.get("photos") or [])[i].get("animSpeed")
        if 0 <= i < len(spec.get("photos") or []) else None)
    if raw is None:
        raw = spec.get("animSpeed")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(v) or v <= 0:
        return 1.0
    return max(ENTRANCE_SPEED_MIN, min(ENTRANCE_SPEED_MAX, v))


def entrance_length(spec: dict[str, Any], i: int) -> float:
    """Seconds photo i's entrance takes at its speed. Every length read in the
    engine goes through here, so a speed that scaled the sweep but not the alpha
    ramp, the pendulum or the size ladder cannot happen."""
    return ENTRANCE_LENGTH.get(photo_anim(spec, i), 0.0) / anim_speed(spec, i)


def entrance_fade(spec: dict[str, Any], i: int) -> float:
    """…and the same for the alpha ramp."""
    return ENTRANCE_FADE.get(photo_anim(spec, i), 0.0) / anim_speed(spec, i)


LAYOUTS = ("stack", "grid", "scatter", "filmstrip", "fan", "masonry", "free", "template",
           "honeycomb", "zigzag", "arc", "photowall", "booth", "silhouette", "cube")
# The mask reveals are animations too: one list, so a photo can be given a wipe
# wherever an entrance is chosen, and `photo_anim` cannot forget one.
ANIMS = ("drop", "pop", "swing", "flip", "none", "fade", "slide", "rise", "tumble",
         "zoom", "fold", "glitch", "ink", "brush", *GEOM_ANIMS, *LOOK_ENTRANCE_ANIMS, *REVEALS)
FRAME_SHAPES = ("polaroid", "none", "rect", "rounded", "circle", "oval", "heart", "star",
                "diamond", "hexagon", "triangle", "octagon", "cloud", "arch", "ticket")

# Exits - how the photos leave after the hold. Twin of collageCore.ts.
EXIT_LENGTH = {"sweep": 0.45, "deal": 0.32, "shuffle": 0.4, "fade": 0.5, "reverse": 0.5}
EXIT_STAGGER = {"sweep": 0.05, "deal": 0.22, "shuffle": 0.1, "fade": 0.08, "reverse": 0.08}
# The four modes that fly somewhere, plus "none" (stay until the transition) and
# "reverse", which is not a new motion at all but the photo's own entrance played
# backwards over the exit window.
EXITS = ("none", "sweep", "deal", "shuffle", "fade", "reverse")
# …and that last idea turned into the whole exit catalogue: *any* entrance id may be
# stored as an exit, and the departure is that arrival read backwards. So this is
# not a second list of names — it is ANIM_IDS, and one table serves both directions
# (see exit_choice()). An exit therefore never lags an entrance: adding an arrival is
# also adding a departure, in the editor and in the render at the same time.

# Motion one photo keeps doing while it is on screen — the "while" lane of the
# three-lane model the text layers use (enter / while / exit). Twin of MOTION
# in collageCore.ts: dx / dy in % of the frame, rot in degrees, speed in
# radians per second, decay in 1/s. These five only move and turn a photo,
# which FFmpeg can do per frame; a scaling motion cannot be (see size_levels).
MOTIONS = ("none", "drift", "float", "orbit", "shake", "sway", "breathe", "pulse", "beat",
           "move", "turn", "spin")
# Motions that change SIZE. FFmpeg's `scale` freezes its output dimensions at
# init, so these cannot ride an expression the way position and rotation do:
# they ride the size ladder, which samples the twin, quantises the tail and
# holds each size for its window.
SCALE_MOTIONS = ("breathe", "pulse", "beat")
# Motions that travel to an end state and hold it, rather than looping. With a
# motionDuration they finish at that moment; looping motions settle back to rest
# instead, because freezing mid-bob (or mid-orbit, off-centre) looks broken.
ONESHOT_MOTIONS = ("move", "turn", "spin", "drift", "breathe")
# Motions whose end state the user picks rather than the strength slider.
TARGET_MOTIONS = ("move", "turn")
MOTION = {
    "drift": {"dx": 9.0, "dy": 3.0, "rot": 0.0, "speed": 0.0, "decay": 0.0, "scale": 0.0},
    "float": {"dx": 0.7, "dy": 1.6, "rot": 1.2, "speed": 1.1, "decay": 0.0, "scale": 0.0},
    "orbit": {"dx": 1.8, "dy": 1.3, "rot": 0.0, "speed": 0.9, "decay": 0.0, "scale": 0.0},
    "shake": {"dx": 2.2, "dy": 1.4, "rot": 1.8, "speed": 34.0, "decay": 2.2, "scale": 0.0},
    "sway": {"dx": 0.0, "dy": 0.0, "rot": 1.5, "speed": 2.15, "decay": 0.0, "scale": 0.0},
    "breathe": {"dx": 0.0, "dy": 0.0, "rot": 0.0, "speed": 0.0, "decay": 0.0, "scale": 0.08},
    "pulse": {"dx": 0.0, "dy": 0.0, "rot": 0.0, "speed": 2.4, "decay": 0.0, "scale": 0.035},
    "beat": {"dx": 0.0, "dy": 0.0, "rot": 0.0, "speed": 0.0, "decay": 7.0, "scale": 0.09},
    # move and turn take their amplitude from the photo's own target (moveX /
    # moveY / turnTo), so their row is all zeros; spin's rot is degrees/second.
    "move": {"dx": 0.0, "dy": 0.0, "rot": 0.0, "speed": 0.0, "decay": 0.0, "scale": 0.0},
    "turn": {"dx": 0.0, "dy": 0.0, "rot": 0.0, "speed": 0.0, "decay": 0.0, "scale": 0.0},
    "spin": {"dx": 0.0, "dy": 0.0, "rot": 45.0, "speed": 0.0, "decay": 0.0, "scale": 0.0},
}
# Animated looks: how far the colour travels (saturation or brightness
# multiplier) and how fast it dies out. Each look moves only ONE of the two, so
# the preview's CSS filters and the render's filters cannot disagree about how
# they compose.
LOOK_ANIMS = ("none", "toBw", "toColor", "flicker", "invert", "hueFlip")
LOOK_ANIM = {
    "toBw": {"sat": 1.0, "bright": 0.0, "speed": 0.0, "decay": 0.0, "hue": 0.0},
    "toColor": {"sat": 1.0, "bright": 0.0, "speed": 0.0, "decay": 0.0, "hue": 0.0},
    "flicker": {"sat": 0.0, "bright": 0.16, "speed": 41.0, "decay": 1.8, "hue": 0.0},
    # invert is a hard flip — FFmpeg's `negate` is on or off, it has no amount —
    # and hueFlip turns the colour wheel half way round.
    "invert": {"sat": 0.0, "bright": 0.0, "speed": 0.0, "decay": 0.0, "hue": 0.0},
    "hueFlip": {"sat": 0.0, "bright": 0.0, "speed": 0.0, "decay": 0.0, "hue": 180.0},
}
# Most beat punches one photo carries: a long slide of fast music would
# otherwise put hundreds of size windows into one photo's ladder. Both engines
# thin the list the same way, so they still agree.
BEAT_PUNCH_MAX = 24
# The turn a motion reaches at full amplitude — sprite-canvas headroom, the
# same job ENTRANCE_SPIN does for an entrance.
MOTION_SPIN = {"drift": 0.0, "float": 1.2, "orbit": 0.0, "shake": 1.8, "sway": 1.5,
               "move": 0.0, "turn": 180.0, "spin": 180.0}
MOTION_AMOUNT_MIN, MOTION_AMOUNT_MAX = 0.25, 2.0
# Bounds of a motion's own duration, in seconds (0 = the rest of the slide).
MOTION_DURATION_MIN, MOTION_DURATION_MAX = 0.3, 12.0
# How far a `move` target may travel (% of the frame) and how far a `turn`
# target may rotate (degrees). Both clamp, neither folds.
MOVE_RANGE, TURN_RANGE = 60.0, 180.0
# Seconds of negative an `invert` look lasts on a photo that has no entrance to
# arrive in, so the flip is always visible.
INVERT_MIN = 0.45
# Seconds a motion takes to fade in once the photo has landed, so it starts
# from exactly the resting pose instead of jumping.
MOTION_RAMP = 0.35

# Widest angle (degrees) each entrance / exit / idle sway turns a photo beyond
# its resting tilt. The sprite canvas is sized for the widest moment, so a
# turned photo (a Free arrangement the user arranged by hand) never loses its
# corners to the canvas edge in the MP4 while the preview shows them whole.
ENTRANCE_SPIN = {"drop": 7.0, "flip": 4.0, "swing": 16.0, "tumble": 28.0, "glitch": 4.0}
EXIT_SPIN = {"sweep": 10.0, "deal": 25.0, "shuffle": 20.0, "fade": 0.0}
SWAY_SPIN = 1.5
# The historic baseline headroom (drop's settle): animations that never turn a
# photo keep exactly the canvas they have always had.
BASE_SPIN = 7.0
MIN_TILT_HEADROOM = 0.18      # radians


def ladder_buckets(cw: int, ch: int) -> int:
    """How many steps the size ladder may quantise a photo's scale into.

    A slow ramp (Ken Burns, a per-photo breathe) would otherwise emit one level
    per frame — hundreds of `scale`+`overlay` chains for one photo. The step is
    a fraction of the *resting* scale with 1.0 as a fixed point, so a photo that
    never scales after its entrance quantises to exactly the size it had, and
    the ladder stays byte-identical to the unquantised one. Coarse for a small
    sprite (a few pixels either way is invisible there), fine for a big one.
    """
    return max(8, min(64, int(round(min(cw, ch) / 6))))


def choreography_spin(anim: str, ex_mode: str, sway: bool, motion: str = "none",
                      amount: float = 1.0, spin: float | None = None) -> float:
    """Degrees the choreography can add to a photo's resting tilt: its
    entrance, its exit, the idle sway and its own motion, whichever turns it
    furthest. MOTION_SPIN is an upper bound for the motion's amplitude, so a
    moving photo keeps its corners inside the sprite canvas; pass `spin` when
    the motion's reach is the photo's own (a `turn` to a picked angle)."""
    reach = max(ENTRANCE_SPIN.get(anim, 0.0), EXIT_SPIN.get(ex_mode, 0.0), BASE_SPIN)
    reach += SWAY_SPIN if sway else 0.0
    return reach + (MOTION_SPIN.get(motion, 0.0) * amount if spin is None else spin)


EXIT_SPEED_MIN, EXIT_SPEED_MAX = 0.05, 2.0


def exit_speed(spec: dict[str, Any]) -> float:
    """The exit speed multiplier, clamped; 1 when missing or malformed (twin of exitSpeed())."""
    try:
        v = float(spec.get("exitSpeed"))
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(v) or v <= 0:
        return 1.0
    return max(EXIT_SPEED_MIN, min(EXIT_SPEED_MAX, v))


def is_exit_choice(name: Any) -> bool:
    """Can this name be stored as an exit? The five modes, "none", and any
    entrance id — including a bridged FFmpeg name, for the same reason it is a
    legal entrance (twin of isExitChoice())."""
    if not isinstance(name, str) or not name:
        return False
    if name == "none" or name in EXITS:
        return True
    return name in ANIM_IDS or resolve_anim(name) in ANIMS


def exit_choice(spec: dict[str, Any], i: int | None = None) -> str:
    """The exit as it is stored: "none" when the photo stays, otherwise one of the
    five modes or the id of the entrance to play backwards (twin of exitChoice()).
    This is the only reader that knows *which* animation a departure mirrors;
    exit_mode() below flattens the answer to how it behaves."""
    if i is not None:
        photos = spec.get("photos") or []
        if i < len(photos) and isinstance(photos[i], dict):
            # 'none' counts here and means the opposite of what it means on the
            # collage: an explicit 'none' on a photo is how you keep it on screen
            # while everything else leaves, so it is a chosen value, not an absence.
            own = photos[i].get("exit")
            if is_exit_choice(own):
                return str(own)
    shared = spec.get("exit")
    return str(shared) if is_exit_choice(shared) and shared != "none" else "none"


def exit_animation(spec: dict[str, Any], i: int) -> str:
    """The animation photo i's departure reads backwards, "" when the exit is "none"
    or one of the four travel modes (those add their own terms instead). "reverse"
    is the same mechanism pointed at the photo's own arrival (twin of
    exitAnimation())."""
    raw = exit_choice(spec, i)
    if raw == "none" or (raw in EXITS and raw != "reverse"):
        return ""
    if raw == "reverse":
        # …and "reverse" is the photo's own arrival, named: the mirror below is
        # guarded on "a *different* curve", so answering with the real id here is
        # what keeps every reader of this function honest about what runs backwards
        # (the TS twin has always answered this way; python used to say "").
        return photo_anim(spec, i)
    return resolve_anim(raw)


# The arrivals whose curves are hand-written rather than a GEOM row, as FFmpeg
# expression terms: (x as a fraction of width, y of height, rotation in degrees) and
# the ease they share. One table, two users — a photo's own arrival, and a departure
# that mirrors somebody else's arrival — because an exit is an entrance read
# backwards (see exit_animation()). The scale-only ids (pop, zoom, ink, fold, flip's
# width) are deliberately absent: the render draws a changing size as a sampled
# ladder of static scales from photo_state(), which already mirrors the exit, so
# there is nothing for the graph to add.
_ARRIVAL_EASE = {"settle3": "pow(1-{q},3)", "lin1": "(1-{q})", "settle2": "pow(1-{q},2)"}
_ARRIVAL = {
    "drop": (0.0, -0.26, -7.0, "settle3"),
    "flip": (0.0, 0.0, -4.0, "lin1"),
    "slide": (-0.28, 0.0, 0.0, "settle3"),
    "rise": (0.0, 0.22, 0.0, "settle3"),
    "tumble": (0.10, -0.18, 28.0, "settle3"),
    "brush": (-0.36, 0.0, 0.0, "settle3"),
}


def arrival_terms(anim: str, q: str, width: int, height: int, seed: int, i: int,
                  tau: str | None = None) -> tuple[str, str, str]:
    """This animation's (x, y, rotation) terms at progress `q` — the expressions the
    photo's position and angle are built from, for the *editor's* hand-written
    entrances, the `paper` family and the bridged names (whose rows carry the curve
    data), plus glitch's seeded jitter and a pendulum's `tau`. Twin-side of
    _entrance_state(): same numbers, one place each. Returns '' for a channel the
    animation does not touch, so callers splice empty strings freely.

    `tau` is only read by 'swing', whose clock is raw seconds scaled by speed rather
    than a window: the caller passes the mirrored one to make a swing leave the way
    it arrived."""
    row = _ARRIVAL.get(anim)
    if row is not None:
        ease = _ARRIVAL_EASE[row[3]].replace("{q}", q)
        x = fly_term(row[0] * width, ease) if row[0] else ""
        y = fly_term(row[1] * height, ease) if row[1] else ""
        r = fly_term(row[2], ease) if row[2] else ""
        return (x, y, r)
    if anim == "swing" and tau is not None:
        return ("", "", f"+14*exp(-1.3*{tau})*cos(2*PI*{tau}/1.6)")
    if anim == "glitch":
        jg = f"pow(1-{q},2)"
        return (fly_term((hash01(seed, i, 61) - 0.5) * 0.10 * width, jg),
                fly_term((hash01(seed, i, 63) - 0.5) * 0.06 * height, jg),
                fly_term((hash01(seed, i, 65) - 0.5) * 8, jg))
    if anim in GEOM:
        grow = GEOM[anim]
        return (_geom_expr(grow.get("dx"), q, width), _geom_expr(grow.get("dy"), q, height),
                _geom_expr(grow.get("rot"), q, 1.0))
    return ("", "", "")



def exit_fly_length(spec: dict[str, Any], i: int | None = None) -> float:
    """A photo's fly-out length in seconds at the spec's exit speed (twin of
    exitFlyLength()). Pass i for a photo that leaves on its own. A departure that
    names an entrance is as long as that entrance's own arrival, scaled by *exit*
    speed; "reverse" keeps its own number, so no existing slide changes."""
    raw = exit_choice(spec) if i is None else exit_choice(spec, i)
    if raw == "none":
        return 0.0
    base = EXIT_LENGTH.get(raw, ENTRANCE_LENGTH.get(resolve_anim(raw), 0.0))
    return base / exit_speed(spec)


def exit_mode(spec: dict[str, Any], i: int | None = None) -> str:
    """The exit mode: the collage's setting, or photo i's own when i is given.
    A missing or unknown override inherits; "none" has to be written explicitly
    to keep one photo on screen while the others leave. Any entrance id reports
    itself as "reverse", because that is how it behaves: no fly of its own, the
    arrival's curve running backwards (twin of exitMode())."""
    raw = exit_choice(spec) if i is None else exit_choice(spec, i)
    if raw == "none":
        return "none"
    return raw if raw in EXITS[1:] else "reverse"


def exit_schedule_mode(spec: dict[str, Any]) -> str:
    """The mode that sets the ORDER of the exit: the collage's own exit when it
    has one, otherwise the first photo that leaves on its own. A mixed exit
    still needs one shared schedule, or the photos would leave in an order
    nobody chose."""
    own = exit_mode(spec)
    if own != "none":
        return own
    for i in range(len(spec.get("photos") or [])):
        m = exit_mode(spec, i)
        if m != "none":
            return m
    return "none"


def exit_offset(spec: dict[str, Any], i: int) -> float:
    """When photo i starts leaving, relative to the end of the hold. 'deal'
    clears the top of the pile first (photo n-1 leaves first). exitReverse
    flips the order so the last/top photo leaves first in every mode. A photo
    that stays (its own exit "none") has no offset at all."""
    if exit_mode(spec, i) == "none":
        return 0.0
    mode = exit_schedule_mode(spec)
    if mode == "none":
        return 0.0
    n = len(spec.get("photos") or [])
    speed = exit_speed(spec)
    rev = spec.get("exitReverse") is True
    if mode == "deal":
        return EXIT_STAGGER["deal"] / speed * max(0, i if rev else n - 1 - i)
    return EXIT_STAGGER[mode] / speed * (max(0, n - 1 - i) if rev else i)


def exit_total(spec: dict[str, Any]) -> float:
    """Total seconds the exit adds to the slide: when the last photo to leave
    is gone — its own offset plus its own fly."""
    n = len(spec.get("photos") or [])
    if not n:
        return 0.0
    worst = 0.0
    for i in range(n):
        if exit_mode(spec, i) == "none":
            continue
        worst = max(worst, exit_offset(spec, i) + exit_fly_length(spec, i))
    return worst


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
    # With per-photo entrances the slide has to stay up until the LAST photo
    # has finished arriving — not necessarily the last one to start. A collage
    # whose photos all just appear is on screen from frame one, so it is only
    # the hold (the stagger delays are not waiting time then).
    last = 0.0
    animated = False
    for i in range(len(photos)):
        a = photo_anim(spec, i)
        if a != "none":
            animated = True
        last = max(last, photo_start(spec, i, 0.0) + entrance_length(spec, i))
    if not animated:
        return _round2(hold)
    return _round2(last + hold)


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
        return (f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1")
    if f == "stretch":
        return f"scale={width}:{height},setsar=1"
    if f == "tile":
        return (f"loop=loop=63:size=1,tile=8x8,crop={width}:{height}:0:0,setsar=1")
    if f == "center":
        return (f"scale='min(iw,{width})':'min(ih,{height})':force_original_aspect_ratio=decrease:force_divisible_by=2,"
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
    n = len(spec.get("photos") or [])
    p = 0.0
    for j in range(i + 1, n):
        tj = photo_start(spec, j, lead_in)
        # The push follows the ARRIVING photo's landing, so a photo with its
        # own entrance pushes back on its own clock.
        e = entrance_length(spec, j)
        q = _clamp01((t - (tj + DEPTH["delay"] * e)) / DEPTH["span"])
        p += q * q * (3 - 2 * q)
    return min(DEPTH["max"], p)


def camera_state(spec: dict[str, Any], t: float, lead_in: float = 0.0, aspect: float = 16 / 9, ratios=None) -> dict[str, float]:
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
    pls = placements(spec, aspect, ratios)
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
                  aspect: float | None = None, ratios=None) -> str | None:
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
    pls = placements(spec, width / height if aspect is None else aspect, ratios)
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


def _entrance_state(spec: dict[str, Any], i: int, anim: str, t0: float, ent: float,
                fade: float, spd: float, ti: float, push: float) -> dict[str, Any]:
    """The entrance curve for `anim`, sampled at `ti` — the whole arrival in one
    place so that a *departure* can be the same code with another id and another
    window: photo_state() calls it a second time for the animation the exit names.
    `t0` is when the curve starts, `ent` how long it runs, `fade` its alpha ramp,
    `spd` the clock a pendulum compresses and `push` how far back a landed photo
    has been shoved by the ones arriving after it. Twin of entranceState() in
    collageCore.ts — including reading the pendulum at `ti` rather than on the raw
    clock, which is what lets a mirrored exit un-swing a photo in the preview the
    same way the render already did.
    """
    if anim == "drop":
        q = _clamp01((ti - t0) / ent)
        settle = (1 - q) ** 3
        st = {"dx": 0.0, "dy": -26 * settle, "rot": -7 * settle, "scale": 1 - DEPTH["scale"] * push, "scaleX": 1.0, "alpha": _clamp01((ti - t0) / fade), "dim": DEPTH["dim"] * push}
    elif anim == "pop":
        q = _clamp01((ti - t0) / ent)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": (0.55 + 0.45 * back) * (1 - DEPTH["scale"] * push), "scaleX": 1.0, "alpha": _clamp01((ti - t0) / fade), "dim": DEPTH["dim"] * push}
    elif anim == "flip":
        # A card flipping open around its vertical axis: edge-on (4% width)
        # with an outBack overshoot, straightening as it settles.
        q = _clamp01((ti - t0) / ent)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": -4 * (1 - q), "scale": 1 - DEPTH["scale"] * push, "scaleX": max(0.04, 0.04 + 0.96 * back), "alpha": _clamp01((ti - t0) / fade), "dim": DEPTH["dim"] * push}
    elif anim == "swing":
        # The pendulum is the one entrance measured in raw seconds, so speed
        # compresses the clock it runs on rather than a divisor.
        tau = max(0.0, ti - t0) * spd
        rot = 14 * math.exp(-1.3 * tau) * math.cos(2 * math.pi * tau / 1.6)
        st = {"dx": 0.0, "dy": 0.0, "rot": rot, "scale": 1.0, "scaleX": 1.0, "alpha": _clamp01(tau / 0.15), "dim": 0.0}
    elif anim == "fade":
        q = _clamp01((ti - t0) / ent)
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": q * q * (3 - 2 * q), "dim": 0.0}
    elif anim == "slide":
        q = _clamp01((ti - t0) / ent)
        settle = (1 - q) ** 3
        st = {"dx": -28 * settle, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": _clamp01((ti - t0) / fade), "dim": 0.0}
    elif anim == "rise":
        q = _clamp01((ti - t0) / ent)
        settle = (1 - q) ** 3
        st = {"dx": 0.0, "dy": 22 * settle, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": _clamp01((ti - t0) / fade), "dim": 0.0}
    elif anim == "tumble":
        q = _clamp01((ti - t0) / ent)
        settle = (1 - q) ** 3
        st = {"dx": 10 * settle, "dy": -18 * settle, "rot": 28 * settle, "scale": 1 - 0.15 * settle, "scaleX": 1.0, "alpha": _clamp01((ti - t0) / fade), "dim": 0.0}
    elif anim == "zoom":
        q = _clamp01((ti - t0) / ent)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 0.2 + 0.8 * back, "scaleX": 1.0, "alpha": _clamp01((ti - t0) / fade), "dim": 0.0}
    elif anim == "fold":
        q = _clamp01((ti - t0) / ent)
        back = 1 + 2.70158 * (q - 1) ** 3 + 1.70158 * (q - 1) ** 2
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": max(0.04, 0.04 + 0.96 * back), "alpha": _clamp01((ti - t0) / fade), "dim": 0.0}
    elif anim == "glitch":
        q = _clamp01((ti - t0) / ent)
        j = (1 - q) * (1 - q)
        seed = int(spec.get("seed") or 1)
        st = {"dx": (hash01(seed, i, 61) - 0.5) * 10 * j, "dy": (hash01(seed, i, 63) - 0.5) * 6 * j,
              "rot": (hash01(seed, i, 65) - 0.5) * 8 * j, "scale": 1.0,
              "scaleX": 1 + (hash01(seed, i, 67) - 0.5) * 0.18 * j, "alpha": _clamp01((ti - t0) / fade), "dim": 0.15 * j}
    elif anim == "ink":
        q = _clamp01((ti - t0) / ent)
        blob = q * q * (3 - 2 * q)
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 0.35 + 0.65 * blob, "scaleX": 1.0, "alpha": blob, "dim": 0.0}
    elif anim == "brush":
        q = _clamp01((ti - t0) / ent)
        settle = (1 - q) ** 3
        st = {"dx": -36 * settle, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": max(0.12, 1 - 0.55 * settle),
              "alpha": _clamp01((ti - t0) / fade), "dim": 0.0}
    elif anim in LOOK_ENTRANCE:
        # The photo does not move: its colour arrives, so every transform field is
        # at rest and only the alpha ramp belongs here. The eight channels live in
        # `photo_look_state`, which is where the preview's CSS filter and the
        # render's `hue`/`eq`/`negate` chain both read them from.
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1 - DEPTH["scale"] * push, "scaleX": 1.0,
              "alpha": _clamp01((ti - t0) / (fade or 0.001)), "dim": 0.0}
    elif anim in GEOM:
        row = GEOM[anim]
        q = _clamp01((ti - t0) / (ent or 0.001))
        st = {"dx": _geom(row.get("dx"), q, 0.0), "dy": _geom(row.get("dy"), q, 0.0),
              "rot": _geom(row.get("rot"), q, 0.0),
              "scale": _geom(row.get("scale"), q, 1.0) * (1 - DEPTH["scale"] * push),
              "scaleX": _geom(row.get("scaleX"), q, 1.0),
              "alpha": _clamp01((ti - t0) / (fade or 0.001)), "dim": _geom(row.get("dim"), q, 0.0)}
    elif is_reveal(anim):
        # A mask reveal: the photo never moves, it is uncovered, so every
        # transform field rests and `alpha` stays 1 — the mask is the opacity.
        # `revealP` is the eased progress both engines turn into a shape.
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": 1.0, "dim": 0.0,
              "revealKind": anim, "revealP": reveal_progress(anim, _clamp01((ti - t0) / ent)),
              "revealDir": float(photo_reveal_dir(spec, i))}
    else:
        st = {"dx": 0.0, "dy": 0.0, "rot": 0.0, "scale": 1.0, "scaleX": 1.0, "alpha": 1.0, "dim": 0.0}
    return st


def photo_state(spec: dict[str, Any], i: int, t: float, lead_in: float = 0.0, aspect: float = 16 / 9, ratios=None) -> dict[str, Any]:
    """Animated offsets of photo i at segment time t — twin of photoState()."""
    t0 = photo_start(spec, i, lead_in)
    push = push_depth(spec, i, t, lead_in)
    anim = photo_anim(spec, i)
    spd = anim_speed(spec, i)
    ent = ENTRANCE_LENGTH.get(anim, 0.0) / spd
    fade = ENTRANCE_FADE.get(anim, 0.0) / spd
    # A 'reverse' exit re-runs the entrance backwards over the exit window, so the
    # time the curves below are sampled at is the arrival's, read in reverse.
    # Mirroring the sample rather than writing an exit for each animation is what
    # keeps 41 ids and 41 departures from being 82 things to keep in step.
    ex_mode = exit_mode(spec, i)
    ex_start = lead_in + base_duration(spec) + exit_offset(spec, i)
    ex_q = 0.0 if ex_mode == "none" else _clamp01((t - ex_start) / (exit_fly_length(spec, i) or 0.001))
    ti = t0 + ent * (1 - ex_q) if ex_mode == "reverse" and ex_q > 0 else t
    st = _entrance_state(spec, i, anim, t0, ent, fade, spd, ti, push)
    # …and an exit may name any arrival at all. Then the departure is *that* entrance
    # read backwards over the exit window instead of the photo's own, which is one
    # extra call to the same chain: no exit is authored per animation, here or in the
    # render, so the exit catalogue can never fall behind the entrance one. Replacing
    # the arrival's contribution costs nothing — by the moment a photo leaves it has
    # long since settled, and every curve in the chain is at rest at q = 1.
    ex_anim = exit_animation(spec, i)
    if ex_q > 0 and ex_anim and ex_anim != anim:
        ex_ent = exit_fly_length(spec, i)
        ex_spd = exit_speed(spec)
        st = _entrance_state(spec, i, ex_anim, ex_start, ex_ent,
                             ENTRANCE_FADE.get(ex_anim, 0.0) / ex_spd, ex_spd,
                             ex_start + ex_ent * (1 - ex_q), push)
    if spec.get("kenBurns") is True:
        land = t0 + ent
        dur = collage_duration(spec)
        kb = _clamp01((t - land) / max(0.8, dur * 0.7))
        seed = int(spec.get("seed") or 1)
        st["scale"] *= 1 + 0.08 * kb
        st["dx"] += (hash01(seed, i, 51) - 0.5) * 5 * kb
        st["dy"] += (hash01(seed, i, 53) - 0.5) * 3.5 * kb
    if spec.get("sway") is True and (t - t0) > ent * 0.7:
        st["rot"] += 1.5 * math.sin(t * 2.15 + i * 0.9)
    add_motion(spec, i, t, st, t0, ent, lead_in)
    # Exit: after the hold (and every entrance) the photos leave the frame —
    # each one its own way when it has an override.
    mode = ex_mode
    # Which animation the departure mirrors: the photo's own for 'reverse', the chosen
    # one otherwise, and '' for the four travel modes. Used below to tell a closing
    # mask (which must not also be dimmed) from a mover that has to fade out.
    ex_a = ex_anim or anim
    if mode != "none":
        seed = int(spec.get("seed") or 1)
        te0 = ex_start
        qe = ex_q
        if qe > 0:
            ease = qe * qe
            if mode == "reverse":
                # Nothing to add: the mirrored curve above IS the departure, and an
                # arrival with an alpha ramp of its own leaves on that ramp backwards.
                # Only an entrance that never touched its alpha needs help going —
                # those fade over the fly, linearly, because `fade=t=out` is. Testing
                # the animation rather than the sampled opacity is the fix for a photo
                # that used to *brighten* halfway out: a drop-style arrival sits at
                # full opacity for most of its fly and only dims at the end, so the
                # instant-by-instant check kept switching the linear fade on and off,
                # while the render (whose `fade=t=out` window is the same ramp) never
                # flickered. Preview and MP4 now agree on exactly the curve.
                if not is_reveal(ex_a) and ENTRANCE_FADE.get(ex_a, 0.0) <= 0.0:
                    st["alpha"] = 1 - qe
            elif mode == "fade":
                # It leaves by fading where it stands: no travel, no turn, and
                # the whole fly is the fade — the movers fade over their last
                # quarter. Linear, because that is what `fade=t=out` does.
                pass
            elif mode == "sweep":
                pl = placements(spec, aspect, ratios)[i]
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
            if mode == "fade":
                st["alpha"] *= 1 - qe
            elif mode == "reverse" and is_reveal(ex_a):
                # The mask IS the departure, and the render fades nothing for it
                # (`fade=t=out` is empty for a closing reveal) — dimming here too would
                # grey out the frame the preview shows and the MP4 does not.
                pass
            else:
                st["alpha"] *= 1 - _clamp01((qe - 0.75) / 0.25)
    # The visibility envelope, which is the one rule the render enforces by hand
    # (it never composites the frame at all: see the window clip in size_levels)
    # and that every curve below therefore quietly used to violate. Each entrance
    # is at rest at q = 0, so an animation with an alpha ramp of its own hides the
    # photo before it arrives — but 'none', a colour lane, or a 12-frame snap does
    # not, and a delayed photo sat on the canvas at full opacity until its turn,
    # and could sit back there after it had left. Twin of photoState() in
    # collageCore.ts, and the same two bounds the MP4 clips its overlay to.
    if t < t0:
        st["alpha"] = 0.0
    elif ex_mode != "none" and t >= ex_start + exit_fly_length(spec, i):
        st["alpha"] = 0.0
    return st


def pin_anchor(spec: dict[str, Any], i: int | None = None) -> bool:
    """Does this photo hang from a pin? Only a swing does — and with per-photo
    entrances one photo can swing while its neighbours do not, so the anchor
    (and the sprite's rotation origin) is per photo."""
    anim = str(spec.get("animation") or "drop") if i is None else photo_anim(spec, i)
    return anim == "swing"


def st_dir(spec: dict[str, Any], i: int) -> int:
    """`photo_reveal_dir` under the short name the graph builder uses."""
    return photo_reveal_dir(spec, i)


def photo_reveal_dir(spec: dict[str, Any], i: int | None = None) -> int:
    """Which way a mask reveal runs: 0 is the direction its kind is named for,
    1 mirrors it on the axis that kind declares (twin of `photoRevealDir`)."""
    if i is not None:
        # A bridged name carries its own direction: FFmpeg's `slideleft` means the
        # new frame comes in from the right, so the wipe has to run the way the
        # name says rather than the way the collage was set to.
        row = bridge_row(photo_anim_name(spec, i))
        if row and row.get("dir") is not None:
            return int(row["dir"])
        photos = spec.get("photos") or []
        if 0 <= i < len(photos) and photos[i].get("animDir") in (0, 1):
            return int(photos[i]["animDir"])
    return 1 if spec.get("animDir") == 1 else 0


# ---------------------------------------------------------------------------
# The transition bridge — FFmpeg's own names, as per-photo reveals
# ---------------------------------------------------------------------------

# The same 38 rows as `COLLAGE_BRIDGE` in src/collageCore.ts. Most of FFmpeg's
# `xfade` names are something the collage can build more cheaply than a mask, so
# the row says which animation the name *runs*: the name is what the spec stores
# and what the picker lists, and the animation is what both engines evaluate.
# `dir` is the direction the name carries with it — `slideleft` pushes the new
# frame in from the right, and a name that silently meant the other way round
# would be a lie about the MP4.
BRIDGE: dict[str, dict[str, Any]] = {
    "wiperight": {"label": "Wipe right", "target": "wipeRight"},
    "wipeleft": {"label": "Wipe left", "target": "wipeLeft"},
    "wipeup": {"label": "Wipe up", "target": "wipeUp"},
    "wipedown": {"label": "Wipe down", "target": "wipeDown"},
    "wipetl": {"label": "Wipe to top-left", "target": "diagDownLeft", "dir": 1},
    "wipetr": {"label": "Wipe to top-right", "target": "diagDownRight", "dir": 1},
    "wipebl": {"label": "Wipe to bottom-left", "target": "diagDownLeft"},
    "wipebr": {"label": "Wipe to bottom-right", "target": "diagDownRight"},
    "slideleft": {"label": "Slide left", "target": "fromRight"},
    "slideright": {"label": "Slide right", "target": "fromLeft"},
    "slideup": {"label": "Slide up", "target": "fromBottom"},
    "slidedown": {"label": "Slide down", "target": "fromTop"},
    "circleopen": {"label": "Circle open", "target": "iris"},
    "circleclose": {"label": "Circle close", "target": "irisOut"},
    "vertopen": {"label": "Vertical open", "target": "curtain"},
    "vertclose": {"label": "Vertical close", "target": "curtain", "dir": 1},
    "horzopen": {"label": "Horizontal open", "target": "curtain", "dir": 1},
    "horzclose": {"label": "Horizontal close", "target": "curtain"},
    "squeezeh": {"label": "Squeeze horizontal", "target": "fold"},
    "squeezev": {"label": "Squeeze vertical", "target": "unfold"},
    "zoomin": {"label": "Zoom in", "target": "zoom"},
    "fadegrays": {"label": "Fade from grey", "target": "colourFlood"},
    "fadewhite": {"label": "Fade from white", "target": "flashIn"},
    "fadeblack": {"label": "Fade from black", "target": "shadowLift"},
    "fadefast": {"label": "Fade (fast)", "target": "fade"},
    "fadeslow": {"label": "Fade (slow)", "target": "fade"},
    "radial": {"label": "Radial", "target": "clock"},
    "rectcrop": {"label": "Rect crop", "target": "diamond"},
    # …and the ten the collage cannot build: the render runs FFmpeg's own
    # transition between two solid layers and uses the result as the photo's
    # alpha mask, and the preview draws the nearest shape it has.
    "smoothleft": {"label": "Smooth left", "target": "wipeLeft", "bridge": True, "soft": 0.22},
    "smoothright": {"label": "Smooth right", "target": "wipeRight", "bridge": True, "soft": 0.22},
    "smoothup": {"label": "Smooth up", "target": "wipeUp", "bridge": True, "soft": 0.22},
    "smoothdown": {"label": "Smooth down", "target": "wipeDown", "bridge": True, "soft": 0.22},
    "hlslice": {"label": "Slices in from left", "target": "blindsRight", "bridge": True},
    "hrslice": {"label": "Slices in from right", "target": "blindsRight", "dir": 1, "bridge": True},
    "vuslice": {"label": "Slices from above", "target": "blindsDown", "bridge": True},
    "vdslice": {"label": "Slices from below", "target": "blindsDown", "dir": 1, "bridge": True},
    "dissolve": {"label": "Dissolve", "target": "mosaic", "bridge": True},
    "pixelize": {"label": "Pixelize", "target": "mosaic", "bridge": True},
}
BRIDGE_NAMES = tuple(BRIDGE)
# The families an entrance belongs to — the shuffle's pools and the picker's tabs
# in both languages, so a name cannot be in a group in one and not the other.
ANIM_FAMILIES = ("classic", "paper", "colour", "mask", "ffmpeg")
ANIM_FAMILY = {
    **{k: "mask" for k in REVEALS},
    **{k: "paper" for k in GEOM},
    **{k: "colour" for k in LOOK_ENTRANCE},
    **{name: ("ffmpeg" if row.get("bridge") else str(row.get("family", "mask")))
       for name, row in BRIDGE.items()},
}
# Every id a spec may carry: our own animations and the bridged names.
ANIM_IDS = ANIMS + BRIDGE_NAMES


def resolve_anim(name: Any) -> str:
    """The animation a stored name runs. One place, so the state, the lengths and
    the graph cannot each resolve a bridge a little differently."""
    row = BRIDGE.get(str(name))
    return str(row["target"]) if row else str(name)


def bridge_row(name: Any) -> dict[str, Any] | None:
    """The bridge row for a name, or None for an animation of our own."""
    return BRIDGE.get(str(name))


def photo_anim_name(spec: dict[str, Any], i: int) -> str:
    """The entrance of photo i as it is *stored* — a bridged name stays itself, so
    the picker and the direction rules can see what the user picked."""
    photos = spec.get("photos") or []
    if i < len(photos) and isinstance(photos[i], dict):
        own = photos[i].get("anim")
        if own in ANIM_IDS:
            return str(own)
    return str(spec.get("animation") or "drop")


def photo_anim(spec: dict[str, Any], i: int) -> str:
    """The entrance photo i performs: its own override when it has one, the
    collage's animation otherwise, and either way resolved through the bridge, so
    a stored FFmpeg name arrives as the animation standing behind it (twin of
    photoAnim() + resolveAnim())."""
    return resolve_anim(photo_anim_name(spec, i))


def photo_motion(spec: dict[str, Any], i: int) -> tuple[str, float]:
    """The motion photo i keeps doing while on screen, and how strongly
    (twin of photoMotion())."""
    photos = spec.get("photos") or []
    p = photos[i] if i < len(photos) and isinstance(photos[i], dict) else {}
    mid = p.get("motion") if p.get("motion") in MOTIONS else "none"
    try:
        v = float(p.get("motionAmount"))
    except (TypeError, ValueError):
        v = float("nan")
    amount = max(MOTION_AMOUNT_MIN, min(MOTION_AMOUNT_MAX, v)) if math.isfinite(v) and v > 0 else 1.0
    return mid, amount


def beat_punches(spec: dict[str, Any], start: float, end: float) -> list[float]:
    """The beats a 'beat' motion punches on: the stored beat times inside this
    photo's window, thinned to BEAT_PUNCH_MAX by taking every k-th one (twin of
    beatPunches())."""
    raw = spec.get("beats")
    beats = [b for b in (raw if isinstance(raw, list) else [])
             if isinstance(b, (int, float)) and not isinstance(b, bool)
             and math.isfinite(b) and start <= b <= end]
    if len(beats) <= BEAT_PUNCH_MAX:
        return beats
    step = math.ceil(len(beats) / BEAT_PUNCH_MAX)
    return [b for k, b in enumerate(beats) if k % step == 0]


def photo_motion_duration(spec: dict[str, Any], i: int) -> float:
    """How long photo i's motion plays, in seconds from its landing; 0.0 means
    "the rest of the slide", which is what every existing project does
    (twin of photoMotionDuration())."""
    photos = spec.get("photos") or []
    raw = photos[i].get("motionDuration") if i < len(photos) and isinstance(photos[i], dict) else None
    try:
        v = float(raw) if raw is not None and not isinstance(raw, bool) else None
    except (TypeError, ValueError):
        v = None
    if v is None or not math.isfinite(v) or v <= 0:
        return 0.0
    return max(MOTION_DURATION_MIN, min(MOTION_DURATION_MAX, v))


def photo_move(spec: dict[str, Any], i: int) -> tuple[float, float]:
    """Where photo i's `move` motion travels to, in % of the frame from its
    resting spot — the units photoState's dx / dy use (twin of photoMove())."""
    photos = spec.get("photos") or []
    photo = photos[i] if i < len(photos) and isinstance(photos[i], dict) else {}
    out = []
    for key in ("moveX", "moveY"):
        try:
            v = float(photo.get(key)) if photo.get(key) is not None and not isinstance(photo.get(key), bool) else 0.0
        except (TypeError, ValueError):
            v = 0.0
        out.append(max(-MOVE_RANGE, min(MOVE_RANGE, v)) if math.isfinite(v) else 0.0)
    return out[0], out[1]


def photo_turn_to(spec: dict[str, Any], i: int) -> float:
    """The angle photo i's `turn` motion rotates to, in degrees clockwise from
    its resting tilt (twin of photoTurnTo())."""
    photos = spec.get("photos") or []
    raw = photos[i].get("turnTo") if i < len(photos) and isinstance(photos[i], dict) else None
    try:
        v = float(raw) if raw is not None and not isinstance(raw, bool) else 0.0
    except (TypeError, ValueError):
        v = 0.0
    if not math.isfinite(v):
        return 0.0
    return max(-TURN_RANGE, min(TURN_RANGE, v))


def motion_spin(spec: dict[str, Any], i: int) -> float:
    """Degrees of sprite-canvas headroom photo i's motion needs — the turn a
    motion reaches at full amplitude, or for `turn` the photo's own target
    (twin of motionSpin())."""
    mid, amount = photo_motion(spec, i)
    if mid == "none":
        return 0.0
    if mid == "turn":
        return min(abs(photo_turn_to(spec, i)), MOTION_SPIN.get("turn", 0.0))
    return MOTION_SPIN.get(mid, 0.0) * amount


def photo_look_anim(spec: dict[str, Any], i: int) -> str:
    """The animated look of photo i, sanitised (twin of photoLookAnim())."""
    photos = spec.get("photos") or []
    if i < len(photos) and isinstance(photos[i], dict):
        own = photos[i].get("lookAnim")
        if own in LOOK_ANIMS:
            return own
    return "none"


def photo_look_state(spec: dict[str, Any], i: int, t: float, lead_in: float = 0.0) -> dict[str, float]:
    """How the photo is tinted at time t: the animated look *and* any look-based
    entrance, composed into one set of channels (twin of `photoLookState`).

    Multiplying saturation and brightness and adding hue is not a convenience:
    the preview applies one CSS filter string, so if the two lanes were applied as
    two filters in one engine and one string in the other, a photo with both a
    `flicker` look and a `develop` entrance would be tinted in a different order
    on each side. One composed answer, read by both, cannot disagree.

    The preview applies them as CSS saturate() / brightness() on the mat. The
    render drives saturation with a per-frame `hue=s='...'` expression — hue
    evaluates its expressions every frame while eq evaluates them once at init,
    measured on the render binary — and brightness with a sendcmd ladder into a
    second eq, the same machinery the depth dim uses.
    """
    out = _look_anim_state(spec, i, t, lead_in)
    row = LOOK_ENTRANCE.get(photo_anim(spec, i))
    if row is not None:
        ent = entrance_length(spec, i) or 0.001
        q = _clamp01((t - photo_start(spec, i, lead_in)) / ent)
        out = dict(out)
        out["sat"] *= _geom(row.get("sat"), q, 1.0)
        out["bright"] *= _geom(row.get("bright"), q, 1.0)
        out["hue"] += _geom(row.get("hue"), q, 0.0)
        if row.get("inv"):
            out["inv"] = max(out["inv"], 1.0 if q < 1 else 0.0)
    return out


def _look_anim_state(spec: dict[str, Any], i: int, t: float, lead_in: float) -> dict[str, float]:
    """The "while" lane on its own: what the photo's animated look does at t."""
    lid = photo_look_anim(spec, i)
    plain = {"sat": 1.0, "bright": 1.0, "inv": 0.0, "hue": 0.0}
    if lid == "none":
        return dict(plain)
    look = LOOK_ANIM[lid]
    t0 = photo_start(spec, i, lead_in)
    ent = entrance_length(spec, i)
    land = t0 + ent
    if lid == "toBw":
        # colour drains away across the hold, at Ken Burns' pace
        plain["sat"] = 1 - look["sat"] * _clamp01((t - land) / max(0.8, collage_duration(spec) * 0.7))
        return plain
    if lid == "toColor":
        # it arrives in black & white and blooms into colour during the entrance
        plain["sat"] = _clamp01((t - t0) / max(0.45, ent))
        return plain
    if lid == "invert":
        # It arrives as a negative and flips to its real colours once it has
        # landed. The flip is a hard cut because FFmpeg's `negate` is on or off
        # — there is no per-frame amount — and a photo with no entrance of its
        # own still gets INVERT_MIN seconds of negative to develop out of.
        plain["inv"] = 1.0 if t < t0 + max(ent, INVERT_MIN) else 0.0
        return plain
    if lid == "hueFlip":
        # The colour wheel turns half way round across the slide, so the photo
        # ends in its opposite colours. `hue=H='...'` re-evaluates per frame.
        plain["hue"] = look["hue"] * _clamp01((t - t0) / max(0.8, collage_duration(spec) * 0.7))
        return plain
    # flicker: an old-projector brightness jitter that dies out after landing
    u = max(0.0, t - land)
    seed = int(spec.get("seed") or 1)
    tau = math.pi * 2
    r1 = hash01(seed, i, 87)
    r2 = hash01(seed, i, 89)
    env = math.exp(-look["decay"] * u)
    jitter = 0.6 * math.sin(u * look["speed"] + r1 * tau) + 0.4 * math.sin(u * look["speed"] * 0.578 + r2 * tau)
    plain["bright"] = 1 + look["bright"] * env * jitter
    return plain


def add_motion(spec: dict[str, Any], i: int, t: float, st: dict[str, float],
               t0: float | None = None, ent: float | None = None,
               lead_in: float = 0.0) -> dict[str, float]:
    """The "while" lane: one photo's own motion, added on top of its entrance,
    Ken Burns and the collage-wide sway (twin of addMotion()).

    Every term is additive and eases in from the resting pose over MOTION_RAMP,
    so switching a motion on for one photo cannot disturb the others — and
    every term is a closed-form function of t, because the render has to build
    the same numbers as FFmpeg expressions (motion_terms() below): position and
    rotation can be evaluated per frame, scale cannot.
    """
    mid, a = photo_motion(spec, i)
    if mid == "none":
        return st
    m = MOTION[mid]
    # A motion whose end state the user picked (move, turn) takes its strength
    # from that target, not from the slider.
    if mid in TARGET_MOTIONS:
        a = 1.0
    start = photo_start(spec, i, lead_in) if t0 is None else t0
    e0 = entrance_length(spec, i) if ent is None else ent
    u = max(0.0, t - (start + e0))
    g = _clamp01(u / MOTION_RAMP)
    dur = photo_motion_duration(spec, i)
    span = dur if dur > 0 else max(0.8, collage_duration(spec) * 0.7)
    # A motion with a duration of its own stops being interesting when that time
    # is up: one-shot motions hold the end state they travelled to, looping ones
    # settle back to rest over the last part of their window.
    settle = min(0.4, dur * 0.3) if dur > 0 and mid not in ONESHOT_MOTIONS else 0.0
    w = 1 - _clamp01((u - (dur - settle)) / settle) if settle > 0 else 1.0
    seed = int(spec.get("seed") or 1)
    tau = math.pi * 2
    r1 = hash01(seed, i, 81)
    r2 = hash01(seed, i, 83)
    r3 = hash01(seed, i, 85)
    if mid == "drift":
        # One-way pan that ramps in across the hold; the seeded direction keeps
        # a grid of photos from sliding as one block.
        e = _clamp01(u / span)
        st["dx"] += (r1 - 0.5) * 2 * m["dx"] * a * e * w
        st["dy"] += (r2 - 0.5) * 2 * m["dy"] * a * e * w
    elif mid == "shake":
        # A jolt as it lands, dying away — attention without fidgeting.
        e = math.exp(-m["decay"] * u) * g
        st["dx"] += m["dx"] * a * math.sin(u * m["speed"] + r1 * tau) * e * w
        st["dy"] += m["dy"] * a * math.sin(u * m["speed"] * 0.91 + r2 * tau) * e * w
        st["rot"] += m["rot"] * a * math.sin(u * m["speed"] * 0.85 + r3 * tau) * e * w
    elif mid == "sway":
        # The collage-wide pendulum, but for one photo at its own strength.
        # Absolute clock, so photos that share it stay in step (i * 0.9 phase).
        st["rot"] += m["rot"] * a * math.sin(t * m["speed"] + i * 0.9) * g * w
    elif mid == "orbit":
        # A slow ellipse around the resting spot.
        wo = u * m["speed"] + r3 * tau
        st["dx"] += m["dx"] * a * math.cos(wo) * g * w
        st["dy"] += m["dy"] * a * math.sin(wo) * g * w
    elif mid == "breathe":
        # Ken Burns' zoom for one photo: a slow ramp across the hold. A render
        # can only hold discrete sizes, so this one rides the size ladder.
        st["scale"] *= 1 + m["scale"] * a * _clamp01(u / span)
    elif mid == "pulse":
        # A rhythmic zoom around the resting size, eased in like the rest.
        st["scale"] *= 1 + m["scale"] * a * math.sin(u * m["speed"] + r2 * tau) * g * w
    elif mid == "beat":
        # A punch on every stored beat: each one swells the photo and lets it
        # fall back. Beat times live in the hold clock (0 = the hold starts),
        # so the photo's own lead-in is taken back off t.
        # Only beats that have already sounded count — a beat still ahead would
        # otherwise swell the photo from the moment it lands — and the punch
        # eases in with the rest of the motion so landing mid-beat cannot pop.
        punch = 0.0
        for b in beat_punches(spec, start + e0 - lead_in, collage_duration(spec)):
            since = t - lead_in - b
            if since >= 0:
                punch += math.exp(-m["decay"] * since)
        st["scale"] *= 1 + m["scale"] * a * punch * g * w
    elif mid == "move":
        # Travels to the end position the user picked and holds it there. Eased,
        # because a linear slide across the frame looks mechanical.
        e = _smoothstep(_clamp01(u / span))
        mx, my = photo_move(spec, i)
        st["dx"] += mx * e
        st["dy"] += my * e
    elif mid == "turn":
        # Turns to the end angle the user picked, from the photo's resting tilt.
        st["rot"] += photo_turn_to(spec, i) * _smoothstep(_clamp01(u / span))
    elif mid == "spin":
        # Continuous rotation: it turns for `span` seconds and keeps the angle it
        # reached, which is what "spin for three seconds" means. m["rot"] is
        # degrees per second here, not an amplitude.
        st["rot"] += m["rot"] * a * min(u, span) * g
    else:
        # float: a bob with a little lean, three unrelated slow frequencies.
        st["dx"] += m["dx"] * a * math.sin(u * m["speed"] * 0.64 + r1 * tau) * g * w
        st["dy"] += m["dy"] * a * math.sin(u * m["speed"] + r3 * tau) * g * w
        st["rot"] += m["rot"] * a * math.sin(u * m["speed"] * 0.73 + r2 * tau) * g * w
    return st


def _speed_val(raw: Any) -> float | None:
    """A speed multiplier as saved: finite, positive and clamped to the slider's
    range, or None when it is missing or malformed — which means 1. Both engines
    clamp here rather than trusting the UI, so a hand-edited 9001 cannot make a
    photo arrive in a single frame."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v <= 0:
        return None
    return max(ENTRANCE_SPEED_MIN, min(ENTRANCE_SPEED_MAX, v))


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
            entry["size"] = max(0.5, min(3.0, size))
        for key, lo, hi, fallback in (("cx", 0.0, 100.0, None), ("cy", 0.0, 100.0, None), ("rot", -180.0, 180.0, None)):
            try:
                v = float(p.get(key)) if p.get(key) is not None and not isinstance(p.get(key), bool) else None
            except (TypeError, ValueError):
                v = None
            if v is not None and math.isfinite(v):
                entry[key] = max(lo, min(hi, v))
        try:
            own_w = float(p.get("w")) if p.get("w") is not None and not isinstance(p.get("w"), bool) else None
        except (TypeError, ValueError):
            own_w = None
        if own_w is not None and math.isfinite(own_w) and own_w > 0:
            entry["w"] = max(FREE_MIN_W, min(FREE_MAX_W, own_w))
        slot_in = p.get("slot")
        if isinstance(slot_in, int) and not isinstance(slot_in, bool) and 0 <= slot_in < MAX_COLLAGE_PHOTOS:
            entry["slot"] = slot_in
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
        if p.get("shape") in SHAPES:
            entry["shape"] = p["shape"]
        if p.get("anim") in ANIM_IDS:
            entry["anim"] = p["anim"]
        # …and the two things that say *how* that entrance plays. Both are read
        # with the same tolerance at both levels, because a photo that carries a
        # direction the save dropped is a photo that wipes the wrong way.
        if p.get("animDir") in (0, 1) or p.get("animDir") in (False, True):
            entry["animDir"] = 1 if p["animDir"] else 0
        spd = _speed_val(p.get("animSpeed"))
        if spd is not None:
            entry["animSpeed"] = spd
        if p.get("animLock") is True:
            # "this photo's entrance survives the shuffle" — editor state, kept by
            # both engines so it does not vanish on save
            entry["animLock"] = True
        # …and, as for the collage's own exit below, any entrance id: a departure is
        # an arrival read backwards, so the domain of a stored exit is the whole
        # catalogue and not just the five modes (a name dropped here silently loses
        # the seconds the exit adds to the slide — the trap "fade" once fell into).
        if is_exit_choice(p.get("exit")):
            entry["exit"] = p["exit"]
        if p.get("motion") in MOTIONS:
            entry["motion"] = p["motion"]
        if p.get("lookAnim") in LOOK_ANIMS:
            entry["lookAnim"] = p["lookAnim"]
        amount = p.get("motionAmount")
        try:
            amount = float(amount) if amount is not None and not isinstance(amount, bool) else None
        except (TypeError, ValueError):
            amount = None
        if amount is not None and math.isfinite(amount) and amount > 0:
            entry["motionAmount"] = max(MOTION_AMOUNT_MIN, min(MOTION_AMOUNT_MAX, amount))
        for key, lo, hi, keep_zero in (("motionDuration", MOTION_DURATION_MIN, MOTION_DURATION_MAX, False),
                                       ("moveX", -MOVE_RANGE, MOVE_RANGE, True),
                                       ("moveY", -MOVE_RANGE, MOVE_RANGE, True),
                                       ("turnTo", -TURN_RANGE, TURN_RANGE, True)):
            raw_v = p.get(key)
            try:
                v = float(raw_v) if raw_v is not None and not isinstance(raw_v, bool) else None
            except (TypeError, ValueError):
                v = None
            if v is None or not math.isfinite(v):
                continue
            if not keep_zero and v <= 0:
                continue
            if keep_zero and v == 0:
                continue
            entry[key] = max(lo, min(hi, v))
        photos.append(entry)
        if len(photos) >= MAX_COLLAGE_PHOTOS:
            break
    if not photos:
        return None
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else "stack"
    # a bridged name (FFmpeg's own transition name) is as valid a choice here as
    # one of ours — `photo_anim` resolves it for everything downstream
    animation = raw.get("animation") if raw.get("animation") in ANIM_IDS else "drop"
    anim_dir = 1 if raw.get("animDir") in (1, True) else 0
    anim_speed = _speed_val(raw.get("animSpeed"))
    exit_ = raw.get("exit") if is_exit_choice(raw.get("exit")) and raw.get("exit") != "none" else None
    try:
        speed_in = float(raw.get("exitSpeed")) if raw.get("exitSpeed") is not None and not isinstance(raw.get("exitSpeed"), bool) else None
    except (TypeError, ValueError):
        speed_in = None
    exit_speed_value = max(EXIT_SPEED_MIN, min(EXIT_SPEED_MAX, speed_in)) if speed_in is not None and math.isfinite(speed_in) and speed_in > 0 else None
    camera = raw.get("camera") if raw.get("camera") in ("pan", "zoom", "telescope", "droste") else None
    shape = raw.get("shape") if raw.get("shape") in SHAPES else "4:3"
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
    if anim_dir:
        spec["animDir"] = 1
    if anim_speed is not None:
        spec["animSpeed"] = anim_speed
    # The random shuffle's memory: which seed rolled this set, which families it
    # drew from, and whether it avoided repeats. The entrances themselves are
    # stored as concrete ids on the photos, so the render never needs any of this —
    # it is here so a project reopens with the same roll, and so a shuffle the
    # save dropped is a shuffle the *editor* would silently undo.
    seed_in = raw.get("animSeed")
    if seed_in is not None and not isinstance(seed_in, bool):
        # float() first, then truncate: `int("7.5")` would raise while TypeScript's
        # Number() truncates it to 7, and a field one engine keeps and the other
        # drops is the divergence class that costs the most to find
        try:
            as_num = float(seed_in)
        except (TypeError, ValueError):
            as_num = None
        if as_num is not None and math.isfinite(as_num):
            spec["animSeed"] = int(as_num) & 0x7FFFFFFF
    fams = raw.get("animFamilies")
    if isinstance(fams, list):
        keep = [f for f in fams if f in ANIM_FAMILIES]
        if keep:
            spec["animFamilies"] = keep
    if raw.get("animOneOfEach") is True:
        spec["animOneOfEach"] = True
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
    if exit_speed_value is not None and exit_speed_value != 1.0:
        spec["exitSpeed"] = exit_speed_value
    if raw.get("exitReverse") is True:
        spec["exitReverse"] = True
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

def fly_term(value: float, expr: str) -> str:
    """Sign-aware term so generated expressions never contain '+-'. Module level
    because arrival_terms() below needs it as much as the graph builder did."""
    return (f"-{_n(abs(value))}" if value < 0 else f"+{_n(value)}") + f"*{expr}"


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
                  duration: float, lead_in: float, first_input: int, base_label: str,
                  settings: Any = None) -> tuple[list[str], str] | None:
    """Filter-graph lines that composite the collage photos onto ``base_label``.

    Returns (lines, last_label) — each photo's sprite (mat + soft shadow) is
    overlaid in z-order; `last_label` is the composed result — or None when
    the item has no collage. Photos are matted (border in the frame style),
    given a soft shadow, and animated with the same curves the preview uses
    (drop / pop / swing / none). ``settings`` is only needed to probe the
    real aspect of 'native'-shape photos from disk; without it those fall
    back to 4:3.
    """
    spec = normalize_collage(item)
    if spec is None:
        return None
    aspect = width / height
    # For 'native' shape (collage-wide or per-photo), probe each photo's
    # *displayed* aspect once — after EXIF orientation / video rotation, the
    # same size the preview measures and the size FFmpeg autorotates to before
    # scale. Unknown files fall back to 4:3 so the render never fails over
    # metadata. A raw (unrotated) probe letterboxes an upright portrait into a
    # landscape mat: white bars on both sides, the picture small in the middle.
    ratios: list[float | None] | None = None
    if any(_photo_shape(spec, i) == "native" for i in range(len(spec.get("photos") or []))):
        ratios = []
        ffprobe_bin = getattr(settings, "ffprobe_bin", "ffprobe") if settings is not None else "ffprobe"
        for p in spec.get("photos") or []:
            try:
                src = source_path(settings, {"path": p["path"], "name": p.get("name", "")})
            except Exception:  # noqa: BLE001
                ratios.append(None)
                continue
            ratios.append(_probe_aspect(src, ffprobe_bin))
    pls = placements(spec, aspect, ratios)
    # The collage's own entrance family. Each photo may override its entrance,
    # its exit and its idle motion, so the values that drive one photo's sprite
    # (anim / ex_mode / pin, below) are read inside the loop. What stays
    # collage-wide is whether the depth push is on at all — the twin's
    # pushDepth() gates on the spec's animation, not on each photo's, and a
    # photo with a different entrance is still part of the same pile.
    collage_anim = spec["animation"]
    depth_on = spec.get("depth") is True and collage_anim in ("drop", "pop", "flip")

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
        # The push follows each ARRIVING photo's own landing, so a photo that
        # enters on its own clock pushes back on that clock.
        first = (photo_start(spec, i + 1, lead_in) + DEPTH["delay"] * entrance_length(spec, i + 1))
        last = (photo_start(spec, len(pls) - 1, lead_in)
                + DEPTH["delay"] * entrance_length(spec, len(pls) - 1)
                + DEPTH["span"])
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

    def look_segment(i: int) -> str:
        """Filters that carry photo i's animated look, or '' when it has none.

        Colour rides `hue`: it re-evaluates its expressions on every frame (eq
        evaluates them once at init — both measured on the render binary), so a
        slow drain to black & white or a turn of the colour wheel is one exact
        expression instead of a staircase. Brightness has no per-frame filter,
        so it rides sendcmd into its own eq instance, exactly like the depth dim
        does — a separate instance, because the dim's eq is already using
        contrast+saturation+brightness together to stay multiplicative. `negate`
        has no amount at all, so `invert` is a hard flip on a timeline
        expression, which is also what the preview's CSS invert() does.

        All of it sits where the dim sits: after the mat is padded, before the
        alpha and the shape mask, so the frame border and a 'native' letterbox
        react the same way the CSS filter on the preview's mat does, and in the
        order the preview's filter string applies them — hue, brightness, then
        the flip.
        """
        lid = photo_look_anim(spec, i)
        row = LOOK_ENTRANCE.get(photo_anim(spec, i))
        if lid == "none" and row is None:
            return ""
        t0 = photo_start(spec, i, lead_in)
        ent = entrance_length(spec, i)
        land = t0 + ent
        segs: list[str] = []
        span = max(0.8, collage_duration(spec) * 0.7)
        # Each channel is collected from whichever lanes ask for it, then merged
        # into the one `hue` instance — because `photo_look_state` multiplied and
        # added them for the preview, and a second filter would apply the two
        # lanes in the other order.
        s_terms: list[str] = []
        h_terms: list[str] = []
        inv_until: float | None = None
        if lid == "toBw":
            s_terms.append(f"1-min(max((t-{_n(land)})/{_n(span)},0),1)")
        elif lid == "toColor":
            s_terms.append(f"min(max((t-{_n(t0)})/{_n(max(0.45, ent))},0),1)")
        elif lid == "hueFlip":
            # `hue=H=` takes RADIANS — the filter multiplies the expression by
            # 180/pi itself — while the twin's hue is in degrees, which is what
            # the preview's CSS hue-rotate() wants. So the turn is converted
            # once, here, and the ramp stays a plain 0…1 progress.
            turn = LOOK_ANIM[lid]["hue"] * math.pi / 180
            h_terms.append(f"{_n(turn)}*min(max((t-{_n(t0)})/{_n(span)},0),1)")
        elif lid == "invert":
            # It arrives as a negative and flips to its real colours once it has
            # landed; a photo with no entrance still gets INVERT_MIN seconds of
            # negative, or the flip would happen before it is visible.
            inv_until = t0 + max(ent, INVERT_MIN)
        if row is not None:
            q_look = f"min(max((t-{_n(t0)})/{_n(ent or 0.001)},0),1)"
            sat = _geom_expr(row.get("sat"), q_look, 1.0, signed=False)
            if sat:
                s_terms.append(sat)
            hue = _geom_expr(row.get("hue"), q_look, math.pi / 180, signed=False)
            if hue:
                h_terms.append(hue)
            if row.get("inv"):
                # The flip is at the end of the entrance — `q < 1` is the whole
                # negative stretch in the twin, and the same instant here.
                inv_until = max(inv_until or 0.0, land)
        if s_terms:
            segs.append("hue=s='" + (s_terms[0] if len(s_terms) == 1
                                    else "*".join(f"({t})" for t in s_terms)) + "'")
        if h_terms:
            segs.append("hue=H='" + (h_terms[0] if len(h_terms) == 1
                                    else "+".join(f"({t})" for t in h_terms)) + "'")
        if inv_until is not None:
            segs.append(f"negate=enable='lt(t,{_n(inv_until)})'")
        if lid == "flicker" or (row is not None and row.get("bright")):
            r1 = hash01(seed, i, 87)
            r2 = hash01(seed, i, 89)
            tau = math.pi * 2
            # A look entrance brightens from its first frame; a flicker look only
            # jitters after the photo has landed.
            first = t0 if row is not None else land
            last = lead_in + duration
            cmds: list[str] = []
            prev_b: float | None = None
            for k in range(max(0, math.floor(first * fps)), math.ceil(last * fps) + 1):
                t = k / fps
                b = photo_look_state(spec, i, t, lead_in)["bright"]
                if prev_b is not None and abs(b - prev_b) <= 0.004:
                    continue
                cmds.append(f"{_n(t)} eq@fx{i} contrast {_n(b)}")
                cmds.append(f"{_n(t)} eq@fx{i} brightness {_n(128 * (b - 1) / 255)}")
                prev_b = b
            if cmds:
                flick = ";".join(cmds)
                segs.append(f"sendcmd=commands='{flick}',eq@fx{i}=contrast=1:brightness=0")
        return ",".join(segs) + ("," if segs else "")

    lines: list[str] = []
    prev = base_label
    seed = int(spec.get("seed") or 1)
    for i, pl in enumerate(pls):
        inp = first_input + i
        photo = spec["photos"][i] if i < len(spec["photos"]) else {}
        # This photo's three lanes: how it enters, how it leaves, and what it
        # keeps doing in between. A missing override inherits the collage's.
        anim = photo_anim(spec, i)
        # The entrance's own clock for this photo: every term the graph builds
        # from here (offsets, rotation, the size ladder's sampling, the fade)
        # divides by `ent` so that one number — the table's length over the
        # speed — is what the whole arrival is measured in.
        ent = entrance_length(spec, i)
        spd = anim_speed(spec, i)
        ex_mode = exit_mode(spec, i)
        pin = pin_anchor(spec, i)
        mo_id, mo_amt = photo_motion(spec, i)
        fr = photo_frame(spec, photo)
        mat_w = max(24, round(pl["w"] / 100 * width))
        border = max(0, round(frame_border_frac(fr) * width))   # share of the FRAME, not the mat
        if fr["shape"] == "none":
            border = 0
        bottom = max(0, round(0.205 * mat_w)) if fr["shape"] == "polaroid" else border
        photo_w = max(2, mat_w - 2 * border)
        native_ratio = ratios[i] if ratios is not None and i < len(ratios) else None
        this_aspect = _photo_aspect(_photo_shape(spec, i), native_ratio)
        photo_h = max(2, round(photo_w / this_aspect))
        mat_h = photo_h + border + bottom
        hex_c = fr["color"][1:] if isinstance(fr.get("color"), str) and fr["color"].startswith("#") and len(fr["color"]) == 7 else "ffffff"
        pad_color = "white" if hex_c.lower() == "ffffff" else "0x" + hex_c

        # Rotation headroom: the sprite canvas must fit the mat (plus shadow
        # margin) at the largest angle the animation reaches — the resting tilt
        # (a Free photo turned by hand, a seeded scatter tilt) plus everything
        # the entrance, the exit and the idle sway add. The canvas size is
        # constant, so the overlay positions stay simple expressions.
        # It must also fit the UNROTATED mat: ffmpeg pads the mat into the
        # canvas before it rotates, and a canvas narrower than the mat clips
        # it (a wide mat turned 90° is a tall box).
        rest = math.radians(pl["rot"])
        reach = max(abs(rest)
                    + math.radians(choreography_spin(anim, ex_mode, spec.get("sway") is True,
                                                     mo_id, mo_amt, motion_spin(spec, i))),
                    MIN_TILT_HEADROOM)
        sm = border + 4                       # shadow margin around the mat
        # The turned mat's bounding box, w*|cos a| + h*|sin a|, grows until
        # a = atan2(h, w) and falls back after it, so the widest box the range
        # reaches is the one at min(reach, atan2(h, w)) — the same value the
        # old single-angle formula gave while the tilt stayed small.
        wide_a = min(reach, math.atan2(mat_h, mat_w))
        tall_a = min(reach, math.atan2(mat_w, mat_h))
        cw = max(mat_w, math.ceil(mat_w * math.cos(wide_a) + mat_h * math.sin(wide_a))) + 2 * sm
        if pin:
            # Swing hangs the mat from a pin at its top-centre edge, and
            # ffmpeg's rotate turns the sprite about its CENTRE. So the
            # canvas is sized symmetrically about the pin (the mat's top
            # edge) — it must reach the mat's full height below the pin and
            # the swung corners above it — and the mat's top edge sits on
            # the canvas centre. The canvas centre lands on the pin
            # (ay_px below = cy - mat_h / 2), exactly where photoState()'s
            # pinned mat (transform-origin 50% 0) is drawn in the preview.
            hang_a = min(reach, math.atan2(mat_h, mat_w / 2))
            half = max(math.ceil(mat_w / 2 * math.sin(min(reach, math.pi / 2))) + sm,
                       math.ceil(mat_w / 2 * math.sin(hang_a) + mat_h * math.cos(hang_a)) + sm,
                       mat_h)
            ch = 2 * half
            above = half
        else:
            ch = max(mat_h, math.ceil(mat_w * math.sin(tall_a) + mat_h * math.cos(tall_a))) + 2 * sm
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
            fly = exit_fly_length(spec, i)
            t_end = te0 + fly
            qe = f"min(max((t-{_n(te0)})/{_n(fly)},0),1)"
            # A photo that fades out spends its whole fly fading — that IS the
            # exit — while the movers fade over their last quarter on the way out.
            fade_d = fly if ex_mode == "fade" else 0.25 * fly
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
            elif ex_mode == "fade":
                pass                            # it leaves where it stands
            elif ex_mode == "reverse":
                # The mirror in photo_state() is the whole departure: no fly, no
                # turn of its own, and the alpha below follows the entrance's ramp
                # backwards. Anything added here would be a second exit.
                pass
            else:
                a = hash01(seed, i, 73) * 2 * math.pi
                ex_dx = 75 * math.cos(a) / 100 * width
                ex_dy = 55 * math.sin(a) / 100 * height
                ex_rot = (hash01(seed, i, 75) - 0.5) * 40

        def fly_term(value: float, expr: str) -> str:
            # Sign-aware term so generated expressions never contain '+-'.
            return (f"-{_n(abs(value))}" if value < 0 else f"+{_n(value)}") + f"*{expr}"

        def motion_terms(idx: int) -> tuple[str, str, str]:
            """This photo's 'while' lane as FFmpeg expression terms: (x px,
            y px, rotation degrees), each already signed so it can be appended
            to the overlay position and to the rotate angle. Twin of
            addMotion() in collageCore.ts — same amplitudes, same seeded
            phases, same ease-in from the resting pose, and the factors are
            left in the same order so both engines round the same way.

            Only position and rotation live here: FFmpeg evaluates an overlay's
            x/y and a rotate angle per frame, while `scale` freezes its output
            size at init, so a motion that changes size has to ride the size
            ladder instead (see size_levels).
            """
            if mo_id == "none":
                return "", "", ""
            m = MOTION[mo_id]
            land = t0 + ent
            u = f"max(t-{_n(land)},0)"                  # seconds since it landed
            ramp = f"min({u}/{_n(MOTION_RAMP)},1)"      # eases in from rest
            r1 = hash01(seed, idx, 81)
            r2 = hash01(seed, idx, 83)
            r3 = hash01(seed, idx, 85)
            tau = math.pi * 2
            dur = photo_motion_duration(spec, idx)
            span = dur if dur > 0 else max(0.8, collage_duration(spec) * 0.7)
            # A looping motion with a duration of its own settles back to rest
            # over the last part of its window; a one-shot one holds its end
            # state, so it needs no envelope at all.
            settle = min(0.4, dur * 0.3) if dur > 0 and mo_id not in ONESHOT_MOTIONS else 0.0
            w = (f"*(1-min(max(({u}-{_n(dur - settle)})/{_n(settle)},0),1))" if settle > 0 else "")
            if mo_id == "move":
                # Travels to the end position the user picked and holds it.
                mx, my = photo_move(spec, idx)
                prog = f"min({u}/{_n(span)},1)"
                eased = f"pow({prog},2)*(3-2*{prog})"
                return (fly_term(mx / 100 * width, eased),
                        fly_term(my / 100 * height, eased), "")
            if mo_id == "turn":
                # Turns to the end angle the user picked, from the resting tilt.
                prog = f"min({u}/{_n(span)},1)"
                eased = f"pow({prog},2)*(3-2*{prog})"
                return ("", "", fly_term(photo_turn_to(spec, idx), eased))
            if mo_id == "spin":
                # Continuous rotation for `span` seconds, holding the angle it
                # reached: m["rot"] is degrees per second, not an amplitude.
                return ("", "", fly_term(m["rot"] * mo_amt, f"min({u},{_n(span)})*{ramp}"))
            if mo_id == "drift":
                e = f"min({u}/{_n(span)},1)" + w
                return (fly_term((r1 - 0.5) * 2 * m["dx"] * mo_amt / 100 * width, e),
                        fly_term((r2 - 0.5) * 2 * m["dy"] * mo_amt / 100 * height, e),
                        "")
            if mo_id == "shake":
                e = f"exp(-{_n(m['decay'])}*{u})*{ramp}" + w
                return (fly_term(m["dx"] * mo_amt / 100 * width,
                                 f"sin({u}*{_n(m['speed'])}+{_n(r1 * tau)})*{e}"),
                        fly_term(m["dy"] * mo_amt / 100 * height,
                                 f"sin({u}*{_n(m['speed'])}*0.91+{_n(r2 * tau)})*{e}"),
                        fly_term(m["rot"] * mo_amt,
                                 f"sin({u}*{_n(m['speed'])}*0.85+{_n(r3 * tau)})*{e}"))
            if mo_id == "sway":
                # Absolute clock, so photos that share the motion stay in step.
                return ("", "",
                        fly_term(m["rot"] * mo_amt,
                                 f"sin({_n(m['speed'])}*t+{_n(idx * 0.9)})*{ramp}" + w))
            if mo_id == "orbit":
                wo = f"{u}*{_n(m['speed'])}+{_n(r3 * tau)}"
                return (fly_term(m["dx"] * mo_amt / 100 * width, f"cos({wo})*{ramp}" + w),
                        fly_term(m["dy"] * mo_amt / 100 * height, f"sin({wo})*{ramp}" + w),
                        "")
            # float: a bob with a little lean, three unrelated slow frequencies
            return (fly_term(m["dx"] * mo_amt / 100 * width,
                             f"sin({u}*{_n(m['speed'])}*0.64+{_n(r1 * tau)})*{ramp}" + w),
                    fly_term(m["dy"] * mo_amt / 100 * height,
                             f"sin({u}*{_n(m['speed'])}+{_n(r3 * tau)})*{ramp}" + w),
                    fly_term(m["rot"] * mo_amt,
                             f"sin({u}*{_n(m['speed'])}*0.73+{_n(r2 * tau)})*{ramp}" + w))
        ex_x = fly_term(ex_dx, f"pow({qe},2)") if ex_dx is not None else ""
        ex_y = (fly_term(ex_dy, f"pow({qe},2)") if ex_mode != "deal"
                else fly_term(ex_dy, f"sin(PI*{qe})")) if ex_dy is not None else ""
        ex_r = fly_term(ex_rot, qe) if ex_rot is not None else ""
        mo_x, mo_y, mo_r = motion_terms(i)

        # Time-varying rotation: the entrance's settle plus the exit spin,
        # one expression per animation (empty when the photo never rotates).
        # One entrance progress, shared by every animation's terms — and read
        # from the scaled length, so a faster entrance is a shorter fade, a
        # tighter pendulum and a shorter size ladder in the MP4 as well as in the
        # editor. The per-animation literals this replaces (0.55, 0.45, …) were
        # each the table's own number, so nothing about an unchanged slide moves.
        q_ent = f"min(max((t-{_n(t0)})/{_n(ent or 0.001)},0),1)"
        # Which arrival the departure reads backwards ("" for the four travel modes).
        ex_anim = exit_animation(spec, i)
        q_x = ""
        if ex_mode == "reverse":
            # …and read backwards from the moment it leaves.
            q_rev = f"1-min(max((t-{_n(te0)})/{_n(fly or 0.001)},0),1)"
            if ex_anim and ex_anim != anim:
                # An exit that names a *different* animation needs that animation's
                # own terms, so they are added to the arrival's below rather than
                # folded into its progress. Summing is exact, not an approximation:
                # each curve is at rest outside its own window — the arrival has
                # settled at q=1 by the time the photo leaves, and the mirror is 1
                # (also at rest) until the fly starts.
                q_x = q_rev
            else:
                # 'reverse' mirrors the photo's own curve, so one expression can serve
                # both directions: before the exit the mirrored term is 1 and the
                # arrival's own value wins, after it the arrival has settled at 1 and
                # the mirror wins. `gt`, not `gte`, because that is the comparison
                # photo_state() uses.
                q_ent = f"min({q_ent},{q_rev})"
        q_drop = q_flip = q_slide = q_tumble = q_brush = q_glitch = q_ent
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
        elif anim in GEOM:
            # The paper family's offsets, straight out of the row the preview
            # evaluates — no second copy of the numbers here, which is how the
            # hand-written terms above drifted from the curves once already.
            row = GEOM[anim]
            qg = q_ent
            extra_x = _geom_expr(row.get("dx"), qg, width)
            extra_y = _geom_expr(row.get("dy"), qg, height)
            extra_r = _geom_expr(row.get("rot"), qg, 1.0)
        elif anim == "glitch":
            jg = f"pow(1-{q_glitch},2)"
            extra_x = fly_term((hash01(seed, i, 61) - 0.5) * 0.10 * width, jg)
            extra_y = fly_term((hash01(seed, i, 63) - 0.5) * 0.06 * height, jg)
            extra_r = fly_term((hash01(seed, i, 65) - 0.5) * 8, jg)
        if q_x:
            # the departure's curve, from the same table the arrival is drawn from
            tau_x = None
            if ex_anim == "swing":
                # a pendulum's clock is raw seconds, so the mirror is the settled
                # span counting down — the exit's own length, since that window is
                # what its fly is made of (and speed cancels, as it does on arrival)
                tau_x = (f"{_n(ENTRANCE_LENGTH.get(ex_anim, 0.0))}"
                         f"*(1-min(max((t-{_n(te0)})/{_n(fly or 0.001)},0),1))")
            ex2_x, ex2_y, ex2_r = arrival_terms(ex_anim, q_x, width, height, seed, i, tau=tau_x)
            extra_x = (extra_x or "") + ex2_x
            extra_y = (extra_y or "") + ex2_y
            extra_r = (extra_r or "") + ex2_r
        if spec.get("kenBurns") is True:
            land = t0 + ent
            span = max(0.8, collage_duration(spec) * 0.7)
            kb = f"min(max((t-{_n(land)})/{_n(span)},0),1)"
            extra_x = (extra_x or "") + fly_term((hash01(seed, i, 51) - 0.5) * 0.05 * width, kb)
            extra_y = (extra_y or "") + fly_term((hash01(seed, i, 53) - 0.5) * 0.035 * height, kb)
        sway_r = f"+1.5*sin(2.15*t+{_n(i * 0.9)})" if spec.get("sway") is True else ""
        if anim == "drop":
            ang = f"({_n(pl['rot'])}-7*pow(1-{q_drop},3){ex_r}{extra_r}{sway_r}{mo_r})*PI/180"
        elif anim == "flip":
            # The resting tilt rides along: the preview draws the photo turned
            # by its placement rot from the first frame (a Free photo turned by
            # hand, a seeded scatter tilt), so the flip has to open around that
            # same angle instead of straightening it.
            ang = f"({_n(pl['rot'])}-4*(1-{q_flip}){ex_r}{extra_r}{sway_r}{mo_r})*PI/180"
        elif anim == "swing":
            # the same compressed clock photo_state() uses
            # at speed 1 the clock is the clock: no factor to carry around
            tau = (f"max(t-{_n(t0)},0)*{_n(anim_speed(spec, i))}"
                   if anim_speed(spec, i) != 1.0 else f"max(t-{_n(t0)},0)")
            if ex_mode == "reverse":
                # the same clock, run back from where the swing settled
                tau = (f"if(gt(t,{_n(te0)}),{_n(ent)}*(1-min(max((t-{_n(te0)})/{_n(fly or 0.001)},0),1)),"
                       f"{tau})")
            # Keep every component in degrees until the final conversion.
            # The old expression converted the 14° swing amplitude twice,
            # leaving only ~0.24° of motion in the rendered MP4 even though
            # the editor preview uses the full pendulum curve.
            ang = f"({_n(pl['rot'])}+14*exp(-1.3*{tau})*cos(2*PI*{tau}/1.6){ex_r}{extra_r}{sway_r}{mo_r})*PI/180"
        elif extra_r or sway_r or ex_r or mo_r or abs(pl["rot"]) > 0.01:
            ang = f"({_n(pl['rot']) if abs(pl['rot']) > 0.01 else '0'}{ex_r}{extra_r}{sway_r}{mo_r})*PI/180"
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
        # …and the alpha ramp, which every geometry entrance keeps linear so the
        # filter's own `fade` is the same curve the preview computes.
        fade_d_in = entrance_fade(spec, i) or None
        if ex_mode == "reverse":
            # The exit's alpha is the entrance's, backwards: a photo that spent
            # the first 0.16 s of a 0.6 s arrival becoming visible spends the last
            # 0.16/0.6 of its fly disappearing, and sits invisible after that.
            # A mask reveal needs none of it — its mask is closing — and an
            # entrance that never faded ('none') leaves over the whole fly.
            # …and for an exit that names another animation, the ramp is *that*
            # one's: the same ratio, taken from the mirrored curve's own tables and
            # scaled by exit speed, so `fade=t=out` covers the same fraction of the
            # fly that `fade=t=in` covered of the arrival.
            ex_a = ex_anim or anim
            if ex_anim and ex_anim != anim:
                spd_x = exit_speed(spec)
                fd = ENTRANCE_FADE.get(ex_a, 0.0) / spd_x
                ent_r = ENTRANCE_LENGTH.get(ex_a, 0.0) / spd_x or fly
            else:
                fd = entrance_fade(spec, i)
                ent_r = ent
            if is_reveal(ex_a):
                fade_out = ""
            elif fd and fd < ent_r:
                d_out = fly * (fd / ent_r)
                fade_out = f",fade=t=out:st={_n(te0 + fly - d_out)}:d={_n(d_out)}:alpha=1"
            else:
                fade_out = f",fade=t=out:st={_n(te0)}:d={_n(fly)}:alpha=1"
        mid = (f"fade=t=in:st={_n(t0)}:d={_n(fade_d_in)}:alpha=1" if fade_d_in else "null") + fade_out

        # The anchor: the canvas centre lands on the mat centre (or the pin
        # for swinging photos). With the size ladder each level knows its own
        # literal W/H, so the anchor is exact per level.
        ax_px = pl["cx"] / 100 * width
        ay_px = pl["cy"] / 100 * height - (mat_h / 2 if pin else 0)
        fall = f"-{_n(0.26 * height)}*pow(1-{q_drop},3)" if anim == "drop" else ""

        # --- size ladder ---------------------------------------------------
        def size_levels() -> list[tuple[int, int, float | None, str | None, list[list[float]]]]:
            """Distinct (W, H, mask step) sprite variants photo i shows, each with
            the [start, end) frame windows it is on screen. The last window is
            open-ended (end=None).

            The mask step is part of the key because a reveal needs the same
            treatment as a changing size: FFmpeg's `geq` would otherwise evaluate a
            whole clip per pixel per frame, while here each step of the sweep is
            ONE baked still that is held for its window — which is why a NAS can
            render a mosaic in about what a wipe costs."""
            def clip(ws: list) -> list:
                """Bound photo i's composited windows to the time it is on screen.

                Before `t0` the entrance has not begun and after the exit's fly the
                photo is gone, and neither end can be left to the fades to hide:
                `fade=t=in:st=t0` passes the frames *before* st through untouched, at
                full opacity, so a staggered or delayed photo used to sit on the
                canvas until its turn came — visible from the first frame for every
                animation that does not ramp its own alpha (none, a colour lane, a
                snap). The pad is the same 3 ms the window boundaries already use, and
                it is free: the frames it lets in are inside the fade's first
                milliseconds, where the photo is transparent anyway.
                """
                out: list = []
                lo_pad, hi_pad = 0.003, 0.003
                for a, b in ws:
                    lo = max(float(a), t0 - lo_pad)
                    if b is None:
                        hi = None if t_end is None else float(t_end) + hi_pad
                    else:
                        hi = min(float(b), (float(t_end) if t_end is not None else float(b)) + hi_pad)
                    if hi is None:
                        out.append([lo, None])
                    elif hi > lo + 1e-4:
                        out.append([lo, hi])
                return out

            # A closing mask is as much a reveal as an opening one: when the exit
            # mirrors a reveal id, its steps have to be baked per level too.
            reveal = is_reveal(anim) or (ex_mode == "reverse" and is_reveal(ex_anim))
            varies = (anim in SCALE_ANIMS or anim in GEOM_SCALES
                      or depth_on or spec.get("kenBurns") is True or mo_id in SCALE_MOTIONS or reveal
                      # a mirrored exit re-runs the same size changes, so the
                      # levels have to be sampled across the fly as well
                      or (ex_mode == "reverse" and (reveal or anim in SCALE_ANIMS or anim in GEOM_SCALES
                                                    # …and a departure mirroring a *different* animation scales
                                                    # and unmasks on that curve, so its levels have to be
                                                    # sampled across the fly too (photo_state() has them).
                                                    or is_reveal(exit_animation(spec, i))
                                                    or exit_animation(spec, i) in SCALE_ANIMS
                                                    or exit_animation(spec, i) in GEOM_SCALES)))
            if not varies:
                return [(cw, ch, None, None, clip([[0.0, None]]))]
            # the timeline only moves during the entrance and the depth push
            # windows — sample the twin at frame resolution up to there
            last_var = t0 + ent
            if depth_on:
                e = ent
                for j in range(i + 1, len(pls)):
                    tj = photo_start(spec, j, lead_in)
                    last_var = max(last_var, tj + DEPTH["delay"] * e + DEPTH["span"])
            # Ken Burns — and a breathe / pulse / beat motion — keeps scaling
            # the sprite long after the entrance has settled, so the ladder has
            # to cover the tail as well: sampling
            # only to `last_var` left the last level open-ended, which held the
            # size the photo had when it landed for the rest of the slide — the
            # preview zoomed to 1.08 and the MP4 never moved. The tail is
            # quantised (ladder_buckets() steps of the resting scale, 1.0 being a
            # fixed point) so a slow ramp costs a handful of levels instead of
            # one per frame, and a photo that does not scale after its entrance
            # emits exactly the sizes it always did.
            tail = spec.get("kenBurns") is True or mo_id in SCALE_MOTIONS
            ex_win = (te0, te0 + fly) if ex_mode == "reverse" else None
            end_var = last_var
            if tail:
                end_var = max(last_var, t_end if t_end is not None else lead_in + duration)
            if ex_win:
                end_var = max(end_var, ex_win[1])
            buckets = ladder_buckets(cw, ch)
            n_frames = max(1, math.ceil(end_var * fps))
            runs: list[list] = []           # [start_frame, end_frame, W, H, mask step, kind]
            for k in range(n_frames + 1):
                t = k / fps
                st = photo_state(spec, i, t, lead_in, aspect, ratios)
                # the exit window is motion, sampled like the entrance is —
                # quantising it would step a photo that is leaving in one hop
                if tail and t > last_var and not (ex_win and ex_win[0] <= t <= ex_win[1]):
                    s_h = round(st["scale"] * buckets) / buckets
                    s_w = round(st["scale"] * st["scaleX"] * buckets) / buckets
                    w_ = max(2, int(round(cw * s_w)))
                    h_ = max(2, int(round(ch * s_h)))
                else:
                    # the entrance window, sampled exactly as before
                    w_ = max(2, int(round(cw * st["scale"] * st["scaleX"])))
                    h_ = max(2, int(round(ch * st["scale"])))
                # …and the sweep's step, quantised with the SAME constant the
                # preview's mask cache uses, so frame k shows the same shape here
                # as it does on screen. 1.0 means "no mask left to apply".
                mp: float | None = None
                kind: str | None = None
                if reveal:
                    rp = st.get("revealP")
                    # …and WHICH reveal: a photo can arrive as an iris and leave as a
                    # wipe, and the step alone does not say what shape to cut. The
                    # sampled state knows, because it is the state that chose to have
                    # a mask at all — so the kind joins the level key instead of being
                    # read back off the photo's arrival, which is where a shared
                    # `anim` used to paint the exit with the entrance's shape.
                    kind = st.get("revealKind")
                    mp = 1.0 if rp is None or rp >= 1.0 else reveal_quantise(REVEAL_STEPS, float(rp))
                if (runs and runs[-1][2] == w_ and runs[-1][3] == h_
                        and runs[-1][4] == mp and runs[-1][5] == kind):
                    runs[-1][1] = k
                else:
                    runs.append([k, k, w_, h_, mp, kind])
            levels: dict[tuple[int, int, float | None, str | None], list[list[float]]] = {}
            order: list[tuple[int, int, float | None, str | None]] = []
            for k0, k1, w_, h_, mp, kind in runs:
                key = (w_, h_, mp, kind)
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
            out = []
            for w_, h_, mp, kind in order:
                # a level whose every window fell outside the photo's own span is a
                # sprite nothing will ever show: it is dropped, so no `enable` is
                # emitted for a time the photo does not exist in
                ws = clip(levels[(w_, h_, mp, kind)])
                if ws:
                    out.append((w_, h_, mp, kind, ws))
            return out

        def select_expr(windows: list[list[float]]) -> str:
            """The same windows as `enable_expr`, as a bare `select` condition.

            A masked level is a still image with the sweep's step burned in, so
            the frames outside its window are worth nothing — but FFmpeg does not
            know that and would evaluate the mask for every frame of the slide,
            for every level. Dropping them first costs one geq per frame of the
            entrance instead of one per frame of the whole slide, which is the
            difference between a NAS exporting a mosaic in seconds and in
            minutes."""
            parts = []
            for a, b in windows:
                if b is None:
                    parts.append(f"gte(t,{_n(a - 0.003)})" if t_end is None else
                                 f"between(t,{_n(a)},{_n(t_end + 0.003)})")
                else:
                    if t_end is not None:
                        b = min(b, t_end + 0.003)
                    parts.append(f"between(t,{_n(a)},{_n(b)})")
            return "+".join(parts)

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
        if not levels:
            # Nothing of this photo is on screen inside its own span — an entrance
            # that starts and ends between two frames, with a departure already over.
            # The sprite chain still has to be *consumed* (FFmpeg refuses a filter
            # output nobody reads), and an enable nothing satisfies composites it
            # never, which is exactly the answer: the canvas, unchanged.
            lines.append(f"[{prev}][sp{i}f]overlay=x=0:y=0:enable='lt(t,0)'[o{i}];")
            prev = f"o{i}"

        m, t_, sh = f"m{i}", f"t{i}", f"s{i}"
        # Depth push-back dims the mat itself (the shadow silhouette is black
        # either way): the ladder drives a per-photo eq (multiplicative, the
        # CSS brightness() equivalent) before the rgba split, so alpha is
        # untouched.
        ladder = dim_ladder(i)
        dim_seg = (f"sendcmd=commands='{ladder}',eq@dim{i}=contrast=1:saturation=1:brightness=0,"
                   if ladder is not None else "")
        look_seg = look_segment(i)
        photo = spec["photos"][i] if i < len(spec["photos"]) else {}
        look = picture_look(photo, photo_w, photo_h)
        crop_chain = ",".join(crop_filters(normalize_crop({"crop": photo.get("crop")})))
        pre = ",".join(x for x in (crop_chain, look) if x)
        pre = (pre + ",") if pre else ""
        use_shadow = fr.get("shadow") is not False and fr["shape"] != "none"
        mask = _shape_mask(fr, mat_w, mat_h)
        # 'native' shape shows the WHOLE photo (contain), letterboxing into the
        # mat with the mat colour; fixed shapes crop to fill (cover) as before.
        # photo_w/photo_h are the *displayed* aspect (see _probe_aspect): FFmpeg
        # autorotates EXIF / display-matrix before this scale, so a portrait
        # phone photo must be given a portrait box or it sits small between
        # two white side bars. Do not add a second transpose here.
        native_mode = _photo_shape(spec, i) == "native"
        if native_mode:
            # force_divisible_by=2: with an odd target height the decrease-scale
            # of a 4:2:0 (yuvj420p) source can come out one row taller than
            # requested, and the exact pad that follows then fails with
            # "Padded dimensions cannot be smaller than input dimensions" (exit
            # 234). Even output + pad keeps the same visible mat, 1px short at most.
            scale_chain = (f"scale={photo_w}:{photo_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                           f"pad={photo_w}:{photo_h}:(ow-iw)/2:(oh-ih)/2:color={pad_color}")
        else:
            scale_chain = (f"scale={photo_w}:{photo_h}:force_original_aspect_ratio=increase,"
                           f"crop={photo_w}:{photo_h}")
        if use_shadow:
            lines.append(
                f"[{inp}:v]{pre}{scale_chain},"
                f"pad={mat_w}:{mat_h}:{border}:{border}:color={pad_color},"
                f"{dim_seg}{look_seg}format=rgba{mask},split[{m}a][{m}b];"
            )
            lines.append(f"[{m}b]pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0[{t_}];")
            lines.append(
                f"[{m}a]lutrgb=r=0:g=0:b=0,colorchannelmixer=aa=0.34,"
                f"pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0,boxblur={max(2, max(border, 4) // 2)}:2[{sh}];"
            )
            lines.append(f"[{sh}][{t_}]overlay=x=0:y=0[sp{i}];")
        else:
            lines.append(
                f"[{inp}:v]{pre}{scale_chain},"
                f"pad={mat_w}:{mat_h}:{border}:{border}:color={pad_color},"
                f"{dim_seg}{look_seg}format=rgba{mask},pad={cw}:{ch}:{mat_x}:{mat_y}:color=black@0.0[sp{i}];"
            )
        lines.append(f"[sp{i}]{mid},format=rgba[sp{i}f];")
        if len(levels) > 1:
            lines.append(f"[sp{i}f]split={len(levels)}" +
                         "".join(f"[b{i}_{k}]" for k in range(len(levels))) + ";")

        # The size ladder: one static scale per distinct size, each enabled
        # only for its frame windows. Later levels overlay earlier ones, and
        # the windows partition time, so exactly one size per photo is ever
        # on screen.
        for k, (W, H, mp, kind, windows) in enumerate(levels):
            is_last = k == len(levels) - 1
            src = f"sp{i}f" if len(levels) == 1 else f"b{i}_{k}"
            # the final level closes photo i's chain, so its output keeps the
            # short label the camera/caption chains consume
            out = f"o{i}" if is_last else f"o{i}_{k}"
            x_expr = f"{_n(ax_px - W / 2)}{ex_x}{extra_x}{mo_x}"
            y_expr = f"{_n(ay_px - H / 2)}{fall}{ex_y}{extra_y}{mo_y}"
            enable = enable_expr(windows)
            # The reveal mask, with this level's step of the sweep baked in. It
            # rides on the sprite's own box — the same box the preview masks —
            # which is why the two agree without any coordinate translation, and
            # before the rotate, because a CSS mask turns with the element.
            a_expr = geq_alpha(kind or anim, mp, int(st_dir(spec, i))) if (
                mp is not None and mp < 1.0) else ""
            if a_expr:
                # `select` first: a step is a still image, so the frames outside
                # its window are worth nothing, and dropping them before the mask
                # is the difference between one geq per frame of the entrance and
                # one per frame of the whole slide, per level.
                lines.append(f"[{src}]select='{select_expr(windows)}',setpts=N/FRAME_RATE/TB,"
                             f"scale={W}:{H},geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='{a_expr}'"
                             f"{rot_seg}[lv{i}_{k}];")
            else:
                lines.append(f"[{src}]scale={W}:{H}{rot_seg}[lv{i}_{k}];")
            lines.append(f"[{prev}][lv{i}_{k}]overlay=x='{x_expr}':y='{y_expr}'{enable}[{out}];")
            prev = out

    # Virtual camera over the composed scene — the last thing before the
    # caption: a supersampled zoompan following camera_state()'s curves
    # (see camera_filter for why not crop, and why supersampled).
    camera = camera_filter(spec, width, height, fps, lead_in, aspect, ratios)
    if camera is not None:
        lines.append(f"[{prev}]{camera}[cam];")
        prev = "cam"
    return lines, prev
