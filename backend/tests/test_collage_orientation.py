"""Native collage mats must use the picture the viewer sees, not the stored pixels.

Phone portraits are usually saved as landscape pixels plus an EXIF Orientation
tag (or a video display-matrix rotation). The editor preview measures
naturalWidth/naturalHeight, which already include that turn, so the mat is
portrait. FFmpeg autorotates before ``scale``. Probing the raw pixel size
therefore builds a landscape mat and letterboxes the upright picture between
two white side bars. These tests pin the displayed aspect.
"""
from __future__ import annotations

import re
import struct
import tempfile
import unittest
from pathlib import Path

from app.collage import _aspect_from_probe_json, _probe_aspect, collage_graph
from app.config import Settings


def _u16(value: int, order: str) -> bytes:
    return value.to_bytes(2, order)


def _u32(value: int, order: str) -> bytes:
    return value.to_bytes(4, order)


def _exif_tiff(orientation: int, order: str = "little", nested: bool = False) -> bytes:
    """A TIFF header whose Orientation tag is ``orientation``.

    ``nested`` puts the tag only in the Exif sub-IFD, the way a few cameras do.
    """
    endian = "little" if order == "little" else "big"
    bom = b"II" if endian == "little" else b"MM"

    def entry(tag: int, typ: int, count: int, value: int) -> bytes:
        # SHORT values occupy the first two bytes of the 4-byte slot.
        payload = _u16(value, endian) + b"\x00\x00" if typ == 3 else _u32(value, endian)
        return _u16(tag, endian) + _u16(typ, endian) + _u32(count, endian) + payload

    if not nested:
        ifd = _u16(1, endian) + entry(0x0112, 3, 1, orientation) + _u32(0, endian)
        return bom + _u16(42, endian) + _u32(8, endian) + ifd
    # IFD0 holds only the Exif-IFD pointer; Orientation is in that sub-IFD.
    # TIFF header is 8 bytes, IFD0 is 2 + 12 + 4 = 18 bytes, sub-IFD follows.
    sub_offset = 8 + 2 + 12 + 4
    ifd0 = _u16(1, endian) + entry(0x8769, 4, 1, sub_offset) + _u32(0, endian)
    sub = _u16(1, endian) + entry(0x0112, 3, 1, orientation) + _u32(0, endian)
    return bom + _u16(42, endian) + _u32(8, endian) + ifd0 + sub


def jpeg_with_orientation(width: int, height: int, orientation: int, order: str = "little", nested: bool = False) -> bytes:
    """A header-only JPEG: EXIF orientation + SOF pixel size. Not decodable."""
    tiff = _exif_tiff(orientation, order, nested)
    exif = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    sof_body = bytes([8]) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([1, 0x11, 0x00])
    sof = b"\xff\xc0" + (len(sof_body) + 2).to_bytes(2, "big") + sof_body
    return b"\xff\xd8" + app1 + sof + b"\xff\xd9"


def png_with_orientation(width: int, height: int, orientation: int) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + _crc(kind + payload)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    exif = _exif_tiff(orientation, "big")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"eXIf", exif) + chunk(b"IEND", b"")


def _crc(data: bytes) -> bytes:
    return struct.pack(">I", _png_crc(data))


def _png_crc(data: bytes) -> int:
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def webp_with_orientation(width: int, height: int, orientation: int) -> bytes:
    exif = _exif_tiff(orientation, "little")
    # VP8X canvas is stored as width-1 / height-1, 24-bit little-endian.
    vp8x = bytes([0x08, 0, 0, 0]) + (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    def riff_chunk(kind: bytes, payload: bytes) -> bytes:
        pad = b"\x00" if len(payload) & 1 else b""
        return kind + len(payload).to_bytes(4, "little") + payload + pad
    body = riff_chunk(b"VP8X", vp8x) + riff_chunk(b"EXIF", exif)
    return b"RIFF" + (len(body) + 4).to_bytes(4, "little") + b"WEBP" + body


class DisplayedAspectTest(unittest.TestCase):
    def test_phone_portrait_jpeg_is_measured_upright(self) -> None:
        # 4032x3024 stored landscape, Orientation 6 = rotate 90° CW → 3024x4032.
        raw = jpeg_with_orientation(4032, 3024, 6)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phone.jpg"
            path.write_bytes(raw)
            aspect = _probe_aspect(path, "ffprobe")
        self.assertIsNotNone(aspect)
        assert aspect is not None
        self.assertAlmostEqual(3024 / 4032, aspect, places=6)
        self.assertLess(aspect, 1.0, "a portrait must not be probed as landscape")

    def test_orientation_8_and_big_endian_also_swap(self) -> None:
        raw = jpeg_with_orientation(1600, 1200, 8, order="big")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "phone.jpg"
            path.write_bytes(raw)
            aspect = _probe_aspect(path, "ffprobe")
        self.assertAlmostEqual(1200 / 1600, aspect or 0, places=6)

    def test_orientation_in_the_exif_sub_ifd_is_honoured(self) -> None:
        raw = jpeg_with_orientation(4000, 3000, 6, nested=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested.jpg"
            path.write_bytes(raw)
            aspect = _probe_aspect(path, "ffprobe")
        self.assertAlmostEqual(3000 / 4000, aspect or 0, places=6)

    def test_half_turn_does_not_swap_and_plain_landscape_stays_landscape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "turned.jpg").write_bytes(jpeg_with_orientation(1920, 1080, 3))
            (folder / "wide.jpg").write_bytes(jpeg_with_orientation(1920, 1080, 1))
            (folder / "tall.jpg").write_bytes(jpeg_with_orientation(1080, 1920, 1))
            self.assertAlmostEqual(1920 / 1080, _probe_aspect(folder / "turned.jpg", "ffprobe") or 0, places=5)
            self.assertAlmostEqual(1920 / 1080, _probe_aspect(folder / "wide.jpg", "ffprobe") or 0, places=5)
            self.assertAlmostEqual(1080 / 1920, _probe_aspect(folder / "tall.jpg", "ffprobe") or 0, places=5)

    def test_png_and_webp_orientation_swap_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "shot.png").write_bytes(png_with_orientation(4032, 3024, 6))
            (folder / "shot.webp").write_bytes(webp_with_orientation(4032, 3024, 5))
            self.assertAlmostEqual(3024 / 4032, _probe_aspect(folder / "shot.png", "ffprobe") or 0, places=5)
            self.assertAlmostEqual(3024 / 4032, _probe_aspect(folder / "shot.webp", "ffprobe") or 0, places=5)

    def test_video_display_matrix_swaps_the_probed_frame(self) -> None:
        landscape = {"streams": [{"width": 1920, "height": 1080, "side_data_list": [
            {"side_data_type": "Display Matrix", "rotation": -90},
        ]}]}
        upright = {"streams": [{"width": 1080, "height": 1920, "tags": {"rotate": "0"}}]}
        half = {"streams": [{"width": 1920, "height": 1080, "tags": {"rotate": "180"}}]}
        self.assertAlmostEqual(1080 / 1920, _aspect_from_probe_json(landscape) or 0, places=5)
        self.assertAlmostEqual(1080 / 1920, _aspect_from_probe_json(upright) or 0, places=5)
        self.assertAlmostEqual(1920 / 1080, _aspect_from_probe_json(half) or 0, places=5)

    def test_unreadable_file_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "notes.jpg"
            path.write_bytes(b"this is not a picture")
            self.assertIsNone(_probe_aspect(path, "ffprobe-not-installed"))


class NativeMatFollowsDisplayedAspectTest(unittest.TestCase):
    def test_portrait_and_landscape_photos_get_matching_mats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            photos = Path(tmp) / "photos"
            photos.mkdir()
            # Same stored pixels; only the orientation tag differs. The preview
            # shows the first upright and the second wide — the render must too.
            (photos / "portrait.jpg").write_bytes(jpeg_with_orientation(4032, 3024, 6))
            (photos / "landscape.jpg").write_bytes(jpeg_with_orientation(4032, 3024, 1))
            settings = Settings(
                config_dir=Path(tmp) / "config",
                photos_dir=photos,
                videos_dir=Path(tmp) / "videos",
                music_dir=Path(tmp) / "music",
                output_dir=Path(tmp) / "output",
                uploads_dir=Path(tmp) / "uploads",
            )
            item = {"collage": {
                "layout": "split-v",
                "animation": "none",
                "shape": "native",
                "seed": 1,
                "hold": 2,
                "frame": {"shape": "rect", "width": 0, "color": "#ffffff", "shadow": False},
                "photos": [
                    {"path": "/photos/portrait.jpg", "name": "portrait.jpg"},
                    {"path": "/photos/landscape.jpg", "name": "landscape.jpg"},
                ],
            }}
            built = collage_graph(item, 1920, 1080, 30, 4.0, 0.0, 1, "cb", settings)
        self.assertIsNotNone(built)
        assert built is not None
        graph = "".join(built[0])
        scales = [tuple(int(n) for n in pair) for pair in re.findall(
            r"scale=(\d+):(\d+):force_original_aspect_ratio=decrease", graph)]
        self.assertEqual(2, len(scales), graph[:400])
        portrait_w, portrait_h = scales[0]
        landscape_w, landscape_h = scales[1]
        self.assertGreater(portrait_h, portrait_w, "portrait photo was given a landscape box")
        self.assertGreater(landscape_w, landscape_h, "landscape photo was given a portrait box")
        self.assertAlmostEqual(portrait_w / portrait_h, 3024 / 4032, delta=0.02)
        self.assertAlmostEqual(landscape_w / landscape_h, 4032 / 3024, delta=0.02)
