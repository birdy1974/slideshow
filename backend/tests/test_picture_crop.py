"""Cut & crop: the FFmpeg half must cut exactly what the browser shows.

A crop is a handful of numbers on the media item (`item["crop"]`) — a rectangle
in fractions, a straightening angle, a cut-out polygon and a feather amount.
`src/pictureCrop.ts` turns them into canvas/CSS for the editor, the thumbnails
and the lightbox; `app/picture_crop.py` turns the *same* numbers into FFmpeg
filters for the render. These tests pin that agreement:

* the shared constants (read straight out of the TypeScript source),
* the normalisation rules (clamping, garbage tolerance, "full frame = no crop"),
* the inscribed-rectangle maths, checked against a brute-force search and
  against the FFmpeg expressions the module actually emits,
* the crop filter chain and where the renderer puts it,
* the lasso mask bytes, the mask input and the filter_complex graph,
* the cropdetect parser that powers "remove black bars".

Nothing here ever writes to a media file: the mounts are read-only and a crop
lives only in the project.
"""
from __future__ import annotations

import math
import re
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app.config import Settings
from app.database import Database
from app.picture_crop import (
    DEFAULT_FEATHER,
    FULL_RECT,
    LASSO_BLUR_PX,
    LASSO_MASK_SIZE,
    MAX_LASSO_POINTS,
    MAX_STRAIGHTEN,
    MIN_CROP,
    MIN_LASSO_POINTS,
    crop_filters,
    cropdetect_command,
    has_crop,
    inscribed_size_expressions,
    inscribed_zoom,
    lasso_graph,
    lasso_inputs,
    lasso_mask_pgm,
    lasso_plan,
    normalize_crop,
    normalize_lasso,
    normalize_rect,
    parse_cropdetect,
    polygon_area,
)
from app.renderer import Renderer

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MODULE = REPO_ROOT / "src" / "pictureCrop.ts"

# cropdetect stderr as FFmpeg really prints it: the stream line carries the
# source size and every analysed frame proposes a crop.
CROPDETECT_STDERR = """
ffmpeg version 6.1.1 Copyright (c) 2000-2023 the FFmpeg developers
Input #0, mov,mp4,m4a,3gp,3g2,mj2, from '/videos/letterbox.mp4':
  Stream #0:0(und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709), 1920x1080, 8000 kb/s, 25 fps
[Parsed_cropdetect_0 @ 0x55f0] x1:0 x2:1919 y1:140 y2:939 w:1920 h:800 x:0 y:140 pts:0 t:0 crop=1920:800:0:140
[Parsed_cropdetect_0 @ 0x55f0] x1:0 x2:1919 y1:140 y2:939 w:1920 h:800 x:0 y:140 pts:25 t:1 crop=1920:800:0:140
frame=   50 fps= 25 q=-0.0 Lsize=N/A time=00:00:02.00 bitrate=N/A speed=4x
"""


def brute_force_zoom(aspect: float, degrees: float) -> float:
    """Independent check of the inscribed-rectangle formula.

    Search for the widest rectangle of the same aspect whose four corners still
    land inside the picture turned by `degrees`; the zoom is how much smaller it
    is than the picture. No algebra, just geometry and a binary search.
    """
    if aspect <= 0 or abs(degrees) < 1e-9:
        return 1.0
    width, height = aspect, 1.0
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)

    def inside(half_width: float) -> bool:
        half_height = half_width / aspect
        for sx in (1, -1):
            for sy in (1, -1):
                # Turn the corner back into the picture's own frame.
                x = sx * half_width * cos + sy * half_height * sin
                y = -sx * half_width * sin + sy * half_height * cos
                if abs(x) > width / 2 + 1e-12 or abs(y) > height / 2 + 1e-12:
                    return False
        return True

    low, high = 0.0, width / 2
    for _ in range(200):
        middle = (low + high) / 2
        if inside(middle):
            low = middle
        else:
            high = middle
    return (width / 2) / low


def evaluate(expression: str, iw: float, ih: float) -> float:
    """Evaluate an FFmpeg crop expression with iw/ih substituted."""
    python = expression.replace("iw", repr(float(iw))).replace("ih", repr(float(ih)))
    python = python.replace("min", "_min").replace("max", "_max").replace("trunc", "_trunc")
    return eval(python, {"_min": min, "_max": max, "_trunc": lambda value: math.trunc(value)})  # noqa: S307


def crop_arguments(filters: list[str]) -> dict[str, str]:
    """The w/h/x/y expressions of the last `crop=` in a filter list."""
    crop = next(entry for entry in filters if entry.startswith("crop="))
    parts = re.split(r":(?=[whxy]=)", crop[len("crop="):])
    return dict(part.split("=", 1) for part in parts)


class SharedConstantsTest(unittest.TestCase):
    """The browser and the renderer must agree on every magic number."""

    def test_typescript_exports_the_same_constants(self) -> None:
        source = FRONTEND_MODULE.read_text(encoding="utf-8")
        expected = {
            "MAX_STRAIGHTEN": MAX_STRAIGHTEN,
            "MIN_CROP": MIN_CROP,
            "MIN_LASSO_POINTS": MIN_LASSO_POINTS,
            "MAX_LASSO_POINTS": MAX_LASSO_POINTS,
            "DEFAULT_FEATHER": DEFAULT_FEATHER,
            "LASSO_MASK_SIZE": LASSO_MASK_SIZE,
        }
        for name, value in expected.items():
            match = re.search(rf"export const {name}\s*=\s*([-0-9.]+)", source)
            self.assertIsNotNone(match, f"{name} is not exported by src/pictureCrop.ts")
            self.assertAlmostEqual(float(match.group(1)), float(value), places=6,
                                   msg=f"{name} drifted between src/pictureCrop.ts and app/picture_crop.py")

    def test_inscribed_formula_is_the_same_on_both_sides(self) -> None:
        # The TypeScript keeps the same max(cos + sin/a, a·sin + cos) shape; a
        # rewritten formula on either side would silently disagree by degrees.
        source = FRONTEND_MODULE.read_text(encoding="utf-8")
        self.assertRegex(source, r"Math\.max\(\s*cos\s*\+\s*sin\s*/\s*aspect\s*,\s*aspect\s*\*\s*sin\s*\+\s*cos\s*\)")

    def test_full_rect_matches_the_typescript_default(self) -> None:
        self.assertEqual(FULL_RECT, {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
        self.assertIn("FULL_CROP", FRONTEND_MODULE.read_text(encoding="utf-8"))


class NormalizeRectTest(unittest.TestCase):
    def test_full_frame_counts_as_no_crop(self) -> None:
        for value in ({"x": 0, "y": 0, "w": 1, "h": 1}, {"x": 0.001, "y": 0, "w": 0.999, "h": 1}):
            self.assertIsNone(normalize_rect(value))

    def test_inside_rectangle_is_kept(self) -> None:
        self.assertEqual(normalize_rect({"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.7}),
                         {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.7})

    def test_rectangle_is_clamped_into_the_picture(self) -> None:
        rect = normalize_rect({"x": -0.4, "y": 0.9, "w": 1.6, "h": 0.4})
        self.assertIsNotNone(rect)
        assert rect is not None
        self.assertGreaterEqual(rect["x"], 0.0)
        self.assertLessEqual(rect["x"] + rect["w"], 1.0 + 1e-9)
        self.assertLessEqual(rect["y"] + rect["h"], 1.0 + 1e-9)
        self.assertLessEqual(rect["w"], 1.0)

    def test_tiny_crop_is_raised_to_the_minimum(self) -> None:
        rect = normalize_rect({"x": 0.5, "y": 0.5, "w": 0.001, "h": 0.02})
        assert rect is not None
        self.assertEqual(rect["w"], MIN_CROP)
        self.assertEqual(rect["h"], MIN_CROP)

    def test_garbage_falls_back_to_no_crop(self) -> None:
        for value in (None, "0.5", 42, [], {"x": 0.1}, {"x": None, "y": 0, "w": 1, "h": 1},
                      {"x": float("nan"), "y": 0, "w": 1, "h": 1}, {"x": "abc", "y": 0, "w": 1, "h": 1}):
            self.assertIsNone(normalize_rect(value), f"{value!r} should not produce a crop")

    def test_numeric_strings_are_accepted(self) -> None:
        self.assertEqual(normalize_rect({"x": "0.2", "y": "0", "w": "0.5", "h": "1"}),
                         {"x": 0.2, "y": 0.0, "w": 0.5, "h": 1.0})


class NormalizeLassoTest(unittest.TestCase):
    def test_triangle_is_kept(self) -> None:
        points = [(0.2, 0.3), (0.6, 0.25), (0.5, 0.7)]
        self.assertEqual(normalize_lasso(points), points)

    def test_fewer_than_three_points_is_not_a_polygon(self) -> None:
        self.assertIsNone(normalize_lasso([(0.1, 0.1), (0.9, 0.9)]))
        self.assertIsNone(normalize_lasso([]))
        self.assertIsNone(normalize_lasso(None))
        self.assertIsNone(normalize_lasso("0.2,0.3"))

    def test_degenerate_polygon_covers_nothing(self) -> None:
        self.assertIsNone(normalize_lasso([(0.5, 0.5), (0.5, 0.5), (0.5, 0.5)]))
        self.assertIsNone(normalize_lasso([(0.1, 0.1), (0.5, 0.5), (0.9, 0.9)]))

    def test_points_are_clamped_and_garbage_dropped(self) -> None:
        points = normalize_lasso([[-0.5, 2.0], {"x": 0.6, "y": 0.25}, "junk", [0.5, 0.7]])
        self.assertEqual(points, [(0.0, 1.0), (0.6, 0.25), (0.5, 0.7)])

    def test_long_polygons_are_cut_at_the_maximum(self) -> None:
        many = [(index / 40, (index % 7) / 8) for index in range(40)]
        points = normalize_lasso(many)
        assert points is not None
        self.assertEqual(len(points), MAX_LASSO_POINTS)

    def test_polygon_area_is_the_shoelace(self) -> None:
        self.assertAlmostEqual(polygon_area([(0, 0), (1, 0), (1, 1), (0, 1)]), 1.0)
        self.assertAlmostEqual(polygon_area([(0, 0), (0.5, 0), (0.5, 0.5), (0, 0.5)]), 0.25)


class NormalizeCropTest(unittest.TestCase):
    def test_no_crop_at_all(self) -> None:
        for item in (None, {}, {"crop": None}, {"crop": "0.5"}, {"crop": {}},
                     {"crop": {"rect": {"x": 0, "y": 0, "w": 1, "h": 1}}}):
            self.assertIsNone(normalize_crop(item))
            self.assertFalse(has_crop(item))

    def test_straightening_alone_is_a_crop(self) -> None:
        crop = normalize_crop({"crop": {"degrees": 3.4}})
        assert crop is not None
        self.assertEqual(crop["rect"], FULL_RECT)
        self.assertAlmostEqual(crop["degrees"], 3.4)
        self.assertIsNone(crop["lasso"])
        self.assertAlmostEqual(crop["feather"], DEFAULT_FEATHER)
        self.assertTrue(has_crop({"crop": {"degrees": 3.4}}))

    def test_angle_is_clamped_and_snapped(self) -> None:
        assert normalize_crop({"crop": {"degrees": 40}})["degrees"] == MAX_STRAIGHTEN
        assert normalize_crop({"crop": {"degrees": -40}})["degrees"] == -MAX_STRAIGHTEN
        # Below the snap threshold the picture is level again, so no zoom.
        self.assertIsNone(normalize_crop({"crop": {"degrees": 0.02}}))

    def test_feather_is_clamped(self) -> None:
        assert normalize_crop({"crop": {"degrees": 2, "feather": 3}})["feather"] == 1.0
        assert normalize_crop({"crop": {"degrees": 2, "feather": -1}})["feather"] == 0.0
        assert normalize_crop({"crop": {"degrees": 2, "feather": "junk"}})["feather"] == DEFAULT_FEATHER

    def test_rectangle_and_polygon_together(self) -> None:
        crop = normalize_crop({"crop": {"rect": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6},
                                        "lasso": [[0.2, 0.3], [0.6, 0.25], [0.5, 0.7]]}})
        assert crop is not None
        self.assertEqual(crop["rect"], {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6})
        self.assertEqual(crop["lasso"], [(0.2, 0.3), (0.6, 0.25), (0.5, 0.7)])
        self.assertEqual(crop["degrees"], 0.0)


class InscribedZoomTest(unittest.TestCase):
    def test_no_straightening_needs_no_zoom(self) -> None:
        for aspect in (16 / 9, 1.0, 0.6, 3.0):
            self.assertEqual(inscribed_zoom(aspect, 0), 1.0)

    def test_formula_matches_a_brute_force_search(self) -> None:
        for aspect in (16 / 9, 4 / 3, 1.0, 9 / 16, 2.35):
            for degrees in (0.5, 1, 2.5, 5, 7.5, 10, MAX_STRAIGHTEN):
                for sign in (1, -1):
                    self.assertAlmostEqual(inscribed_zoom(aspect, sign * degrees),
                                           brute_force_zoom(aspect, sign * degrees), places=5,
                                           msg=f"aspect {aspect} at {sign * degrees}°")

    def test_zoom_grows_with_the_angle(self) -> None:
        zooms = [inscribed_zoom(16 / 9, degrees) for degrees in (0, 3, 6, 9, 12, 15)]
        self.assertEqual(zooms, sorted(zooms))
        self.assertGreater(zooms[-1], 1.15)

    def test_wide_and_high_expressions_agree_with_the_formula(self) -> None:
        # The FFmpeg expressions must produce the same inscribed rectangle the
        # browser zooms to — that is the whole "honest parity" promise.
        for iw, ih in ((1920, 1080), (1080, 1920), (4000, 3000), (640, 480)):
            for degrees in (1, 3.5, 8, MAX_STRAIGHTEN):
                cos = math.cos(math.radians(degrees))
                sin = math.sin(math.radians(degrees))
                wide, high = inscribed_size_expressions()
                width = evaluate(wide.replace("C", repr(cos)).replace("S", repr(sin)), iw, ih)
                height = evaluate(high.replace("C", repr(cos)).replace("S", repr(sin)), iw, ih)
                zoom = inscribed_zoom(iw / ih, degrees)
                self.assertAlmostEqual(width, iw / zoom, delta=1.0)
                self.assertAlmostEqual(height, ih / zoom, delta=1.0)
                self.assertAlmostEqual(width / height, iw / ih, delta=1e-6)


class CropFiltersTest(unittest.TestCase):
    def test_no_crop_no_filters(self) -> None:
        self.assertEqual(crop_filters(None), [])
        self.assertEqual(crop_filters({}), [])
        # A full frame is the untouched picture — resampling it would only cost
        # quality, so nothing is emitted (a lasso-only cut lands here too).
        self.assertEqual(crop_filters({"rect": dict(FULL_RECT), "degrees": 0.0}), [])
        self.assertEqual(crop_filters({"rect": dict(FULL_RECT), "degrees": 0.01}), [])

    def test_rectangle_only_is_a_single_even_crop(self) -> None:
        filters = crop_filters({"rect": {"x": 0.25, "y": 0.1, "w": 0.5, "h": 0.8}, "degrees": 0.0})
        self.assertEqual(len(filters), 1)
        arguments = crop_arguments(filters)
        for iw, ih in ((1920, 1080), (4000, 3000), (639, 481)):
            width = evaluate(arguments["w"], iw, ih)
            height = evaluate(arguments["h"], iw, ih)
            x = evaluate(arguments["x"], iw, ih)
            y = evaluate(arguments["y"], iw, ih)
            self.assertEqual(width % 2, 0, "yuv420p needs even widths")
            self.assertEqual(height % 2, 0, "yuv420p needs even heights")
            self.assertAlmostEqual(width, iw * 0.5, delta=2)
            self.assertAlmostEqual(height, ih * 0.8, delta=2)
            self.assertAlmostEqual(x, iw * 0.25, delta=2)
            self.assertAlmostEqual(y, ih * 0.1, delta=2)
            self.assertLessEqual(x + width, iw)
            self.assertLessEqual(y + height, ih)

    def test_expressions_only_use_iw_and_ih(self) -> None:
        # Probing the source would mean an extra FFmpeg run per clip; the crop
        # has to work straight off the frame it is given, whatever its size.
        for filters in (crop_filters({"rect": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}}),
                        crop_filters({"rect": {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}, "degrees": 4})):
            for expression in crop_arguments(filters).values():
                self.assertNotRegex(expression, r"\b(ow|oh|main_w|main_h|out_w|out_h)\b")
                self.assertRegex(expression, r"iw|ih")

    def test_straightening_rotates_then_crops_the_inscribed_rectangle(self) -> None:
        filters = crop_filters({"rect": dict(FULL_RECT), "degrees": 5.0})
        self.assertEqual(len(filters), 2)
        rotate, crop = filters
        self.assertTrue(rotate.startswith("rotate="), rotate)
        self.assertIn(f"a={round(math.radians(5.0), 8):g}", rotate)
        # ow=iw/oh=ih keeps the canvas, so the inscribed crop below can work in
        # the same iw/ih space and the filled corners are what it throws away.
        self.assertIn("ow=iw", rotate)
        self.assertIn("oh=ih", rotate)
        self.assertIn("fillcolor=black", rotate)
        arguments = crop_arguments([crop])
        iw, ih = 1920, 1080
        width = evaluate(arguments["w"], iw, ih)
        height = evaluate(arguments["h"], iw, ih)
        zoom = inscribed_zoom(iw / ih, 5.0)
        self.assertAlmostEqual(width, iw / zoom, delta=2)
        self.assertAlmostEqual(height, ih / zoom, delta=2)
        self.assertAlmostEqual(evaluate(arguments["x"], iw, ih), (iw - width) / 2, delta=2)
        self.assertAlmostEqual(evaluate(arguments["y"], iw, ih), (ih - height) / 2, delta=2)

    def test_straightening_and_rectangle_together(self) -> None:
        filters = crop_filters({"rect": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.6}, "degrees": -7.5})
        arguments = crop_arguments(filters)
        iw, ih = 3000, 2000
        inscribed_w = iw / inscribed_zoom(iw / ih, 7.5)
        inscribed_h = ih / inscribed_zoom(iw / ih, 7.5)
        self.assertAlmostEqual(evaluate(arguments["w"], iw, ih), inscribed_w * 0.5, delta=2)
        self.assertAlmostEqual(evaluate(arguments["h"], iw, ih), inscribed_h * 0.6, delta=2)
        self.assertAlmostEqual(evaluate(arguments["x"], iw, ih), (iw - inscribed_w) / 2 + inscribed_w * 0.1, delta=2)
        self.assertAlmostEqual(evaluate(arguments["y"], iw, ih), (ih - inscribed_h) / 2 + inscribed_h * 0.2, delta=2)

    def test_negative_angle_rotates_the_other_way(self) -> None:
        filters = crop_filters({"rect": dict(FULL_RECT), "degrees": -3})
        self.assertIn(f"a={round(math.radians(-3), 8):g}", filters[0])
        self.assertLess(float(re.search(r"a=(-?[0-9.]+)", filters[0]).group(1)), 0)


class LassoMaskTest(unittest.TestCase):
    SQUARE = [(0.25, 0.25), (0.75, 0.25), (0.75, 0.75), (0.25, 0.75)]

    def test_pgm_header_and_length(self) -> None:
        for size in (8, 64, LASSO_MASK_SIZE):
            data = lasso_mask_pgm(self.SQUARE, size)
            header = f"P5\n{size} {size}\n255\n".encode("ascii")
            self.assertTrue(data.startswith(header), data[:24])
            self.assertEqual(len(data), len(header) + size * size)

    def test_inside_is_white_outside_is_black(self) -> None:
        size = 64
        data = lasso_mask_pgm(self.SQUARE, size)
        body = data[data.index(b"255\n") + 4:]
        self.assertGreater(body[int(size * 0.5) * size + int(size * 0.5)], 200)
        self.assertEqual(body[0], 0)
        self.assertEqual(body[size * size - 1], 0)
        self.assertEqual(body[int(size * 0.5) * size + 2], 0)

    def test_covered_area_matches_the_polygon_area(self) -> None:
        size = LASSO_MASK_SIZE
        body = lasso_mask_pgm(self.SQUARE, size)[len(f"P5\n{size} {size}\n255\n"):]
        # Anti-aliased edges, so compare the greyscale mass rather than counting.
        mass = sum(body) / 255
        self.assertAlmostEqual(mass / (size * size), abs(polygon_area(self.SQUARE)), delta=0.01)

    def test_concave_polygon_is_filled_correctly(self) -> None:
        arrow = [(0.1, 0.1), (0.9, 0.5), (0.1, 0.9), (0.35, 0.5)]
        size = 64
        body = lasso_mask_pgm(arrow, size)[len(f"P5\n{size} {size}\n255\n"):]
        mass = sum(body) / 255
        self.assertAlmostEqual(mass / (size * size), abs(polygon_area(arrow)), delta=0.02)

    def test_triangle_touching_the_edge(self) -> None:
        data = lasso_mask_pgm([(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], 32)
        body = data[data.index(b"255\n") + 4:]
        self.assertGreater(body[1], 200, "just inside the top-left corner")
        self.assertEqual(body[32 * 32 - 1], 0, "bottom-right corner stays outside")


class LassoGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = lasso_plan({"rect": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
                                "lasso": [(0.2, 0.3), (0.6, 0.25), (0.5, 0.7)], "feather": 0.5})
        assert self.plan is not None
        self.graph = lasso_graph(self.plan, 1, ["transpose=1", "crop=w=iw:h=ih:x=0:y=0"],
                                 ["scale=1920:1080", "format=yuv420p"])

    def test_no_plan_without_a_polygon(self) -> None:
        self.assertIsNone(lasso_plan(None))
        self.assertIsNone(lasso_plan({"rect": {"x": 0, "y": 0, "w": 0.5, "h": 1}}))
        self.assertIsNone(lasso_plan({"lasso": None}))

    def test_plan_carries_points_and_feather(self) -> None:
        assert self.plan is not None
        self.assertEqual(self.plan["feather"], 0.5)
        self.assertEqual(len(self.plan["points"]), 3)

    def test_pre_and_post_chains_are_in_order(self) -> None:
        self.assertTrue(self.graph.startswith("[0:v]transpose=1,crop=w=iw:h=ih:x=0:y=0[cutpre];"))
        self.assertTrue(self.graph.endswith("[cutfilled]scale=1920:1080,format=yuv420p[v]"))

    def test_empty_pre_chain_is_a_null(self) -> None:
        graph = lasso_graph(self.plan, 1, [], ["format=yuv420p"])
        self.assertTrue(graph.startswith("[0:v]null[cutpre];"))

    def test_mask_is_the_second_input_and_is_stretched_to_the_frame(self) -> None:
        self.assertIn("[1:v]format=gray", self.graph)
        self.assertIn("scale2ref=flags=bicubic", self.graph)

    def test_hole_is_filled_with_a_blurred_copy_via_alpha(self) -> None:
        self.assertIn("split=2[cutkeep][cutblur]", self.graph)
        self.assertIn(f"scale={LASSO_BLUR_PX}:-2", self.graph)
        self.assertIn("gblur=sigma=", self.graph)
        self.assertIn("alphamerge", self.graph)
        self.assertIn("overlay=0:0:format=auto[cutfilled]", self.graph)
        # maskedmerge round-trips the kept picture through RGB and corrupts
        # chroma on yuv420p — it must not come back.
        self.assertNotIn("maskedmerge", self.graph)

    def test_feather_scales_the_mask_blur(self) -> None:
        soft = lasso_graph({"points": self.plan["points"], "feather": 1.0}, 1, [], [])
        sharp = lasso_graph({"points": self.plan["points"], "feather": 0.0}, 1, [], [])
        self.assertIn(f"gblur=sigma={LASSO_MASK_SIZE / 42:g}", soft)
        self.assertIn("gblur=sigma=0", sharp)

    def test_mask_input_is_a_bounded_still_stream(self) -> None:
        arguments = lasso_inputs("/work/mask-0000.pgm", 30, 4.5)
        self.assertEqual(arguments, ["-loop", "1", "-framerate", "30", "-t", "4.5", "-i", "/work/mask-0000.pgm"])

    def test_mask_input_never_has_zero_length(self) -> None:
        self.assertIn("0.04", lasso_inputs(Path("m.pgm"), 25, 0))


class CropDetectTest(unittest.TestCase):
    def test_bars_are_turned_into_fractions(self) -> None:
        found = parse_cropdetect(CROPDETECT_STDERR)
        assert found is not None
        self.assertEqual(found["source"], {"width": 1920, "height": 1080})
        self.assertTrue(found["bars"])
        rect = found["rect"]
        self.assertAlmostEqual(rect["x"], 0.0)
        self.assertAlmostEqual(rect["y"], 140 / 1080)
        self.assertAlmostEqual(rect["w"], 1.0)
        self.assertAlmostEqual(rect["h"], 800 / 1080)

    def test_the_last_proposal_wins(self) -> None:
        stderr = CROPDETECT_STDERR.replace("crop=1920:800:0:140", "crop=1920:600:0:240", 1)
        found = parse_cropdetect(stderr)
        assert found is not None
        self.assertAlmostEqual(found["rect"]["h"], 800 / 1080, msg="the most stable (last) proposal is the one to use")

    def test_a_full_frame_is_not_reported_as_bars(self) -> None:
        stderr = CROPDETECT_STDERR.replace("crop=1920:800:0:140", "crop=1920:1080:0:0")
        found = parse_cropdetect(stderr)
        assert found is not None
        self.assertFalse(found["bars"])
        self.assertAlmostEqual(found["rect"]["w"], 1.0)
        self.assertAlmostEqual(found["rect"]["h"], 1.0)

    def test_rotation_swaps_the_source_dimensions(self) -> None:
        # The probe turns the picture first, so the reported frame is the turned
        # one: a portrait photo cropped in rotated space.
        stderr = ("Stream #0:0: Video: mjpeg, 3000x2000\n"
                  "[Parsed_cropdetect_0 @ 0x1] crop=2000:2600:0:200\n")
        found = parse_cropdetect(stderr, rotation=90)
        assert found is not None
        self.assertEqual(found["source"], {"width": 2000, "height": 3000})
        self.assertAlmostEqual(found["rect"]["w"], 1.0)
        self.assertAlmostEqual(found["rect"]["y"], 200 / 3000)
        self.assertAlmostEqual(found["rect"]["h"], 2600 / 3000)
        self.assertEqual(parse_cropdetect(stderr, rotation=0)["source"], {"width": 3000, "height": 2000})
        self.assertEqual(parse_cropdetect(stderr, rotation=270)["source"], {"width": 2000, "height": 3000})
        self.assertEqual(parse_cropdetect(stderr, rotation=180)["source"], {"width": 3000, "height": 2000})

    def test_unusable_output(self) -> None:
        self.assertIsNone(parse_cropdetect(""))
        self.assertIsNone(parse_cropdetect("no crop here"))
        self.assertIsNone(parse_cropdetect("crop=1920:800:0:140"))  # no stream line
        self.assertIsNone(parse_cropdetect("Stream #0:0: Video: h264, 1920x1080\ncrop=0:0:0:0"))

    def test_fractions_are_clamped(self) -> None:
        found = parse_cropdetect("Stream #0:0: Video: h264, 100x100\ncrop=400:400:10:10")
        assert found is not None
        for value in found["rect"].values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_probe_command_turns_the_picture_first(self) -> None:
        command = cropdetect_command("ffmpeg", "/photos/a.jpg", 90, 1)
        self.assertEqual(command[0], "ffmpeg")
        self.assertIn("-hide_banner", command)
        chain = command[command.index("-vf") + 1]
        self.assertTrue(chain.startswith("transpose=1,"), chain)
        self.assertIn("cropdetect=limit=24:round=2:reset=0", chain)
        self.assertEqual(command[-3:], ["-f", "null", "-"])
        self.assertIn("-an", command)
        self.assertEqual(command[command.index("-i") + 1], "/photos/a.jpg")

    def test_probe_command_without_rotation(self) -> None:
        command = cropdetect_command("ffmpeg", "/videos/a.mp4", 0, 4)
        chain = command[command.index("-vf") + 1]
        self.assertTrue(chain.startswith("cropdetect="), chain)
        self.assertNotIn("transpose", chain)
        self.assertEqual(command[command.index("-t") + 1], "4")

    def test_probe_never_runs_shorter_than_half_a_second(self) -> None:
        command = cropdetect_command("ffmpeg", "/videos/a.mp4", None, 0)
        self.assertEqual(command[command.index("-t") + 1], "0.5")


class SegmentCropTest(unittest.TestCase):
    """Where the crop sits in a real per-segment FFmpeg command."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.settings = Settings(config_dir=base / "config", photos_dir=base / "photos", videos_dir=base / "videos",
                                 output_dir=base / "out", music_dir=base / "music")
        for directory in (self.settings.photos_dir, self.settings.videos_dir, self.settings.work_dir, self.settings.preview_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (self.settings.photos_dir / "a.jpg").write_bytes(b"x" * 64)
        (self.settings.videos_dir / "a.mp4").write_bytes(b"x" * 64)
        self.renderer = Renderer(Database(base / "crop.db"), self.settings)
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _commands_for(self, media: list[dict]) -> list[list[str]]:
        project = {"id": 1, "media": media, "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps",
                                                       "bitrate": "8 Mbps", "encoder": "libx264", "path": "/output", "filename": "movie"}}
        self.commands = []

        def fake_run(command, cancelled, log_file):
            self.commands.append(list(command))
            Path(command[-1]).write_bytes(b"segment")

        with mock.patch.object(self.renderer, "_validate_media", return_value=None), \
             mock.patch.object(self.renderer, "_run_ffmpeg", side_effect=fake_run), \
             mock.patch.object(self.renderer, "_make_soundtrack", return_value=None), \
             mock.patch.object(self.renderer, "_probe_duration", return_value=2.0):
            work = self.settings.work_dir / "job"
            work.mkdir(parents=True, exist_ok=True)
            self.renderer.render(project, "render", work, threading.Event(), lambda p, s: None)
        return [command for command in self.commands if "-vf" in command or "-filter_complex" in command]

    def _chain(self, item: dict) -> str:
        command = self._commands_for([item])[0]
        if "-vf" in command:
            return command[command.index("-vf") + 1]
        return command[command.index("-filter_complex") + 1]

    @staticmethod
    def _item(**overrides) -> dict:
        item = {"id": 1, "type": "image", "path": "/photos/a.jpg", "duration": 3, "effect": "Ken Burns · Zoom in",
                "transition": "Fade", "transitionTime": 0.5, "text": "Holiday"}
        item.update(overrides)
        return item

    def test_crop_runs_before_the_frame_fit_and_the_zoom(self) -> None:
        chain = self._chain(self._item(crop={"rect": {"x": 0.2, "y": 0.1, "w": 0.6, "h": 0.8}}))
        self.assertIn("crop=w=trunc", chain)
        self.assertLess(chain.index("crop=w=trunc"), chain.index("zoompan="), "crop the source, then zoom the result")
        self.assertLess(chain.index("crop=w=trunc"), chain.index("format=yuv420p"))

    def test_rotation_then_crop_then_fit(self) -> None:
        chain = self._chain(self._item(rotation=90, crop={"rect": {"x": 0, "y": 0.1, "w": 1, "h": 0.8}}))
        self.assertLess(chain.index("transpose=1"), chain.index("crop=w=trunc"))

    def test_straightening_rotates_inside_the_segment(self) -> None:
        chain = self._chain(self._item(crop={"degrees": 4}))
        self.assertIn("rotate=a=", chain)
        self.assertLess(chain.index("rotate=a="), chain.index("crop=w=trunc"))

    def test_untouched_items_add_no_crop(self) -> None:
        baseline = self._chain(self._item())
        for item in (self._item(id=2, crop=None), self._item(id=3, crop={}),
                     self._item(id=4, crop={"rect": {"x": 0, "y": 0, "w": 1, "h": 1}}),
                     self._item(id=5, crop={"degrees": 0.01}), self._item(id=6, crop="junk")):
            self.assertEqual(baseline, self._chain(item))
        self.assertNotIn("crop=w=trunc", baseline)

    def test_title_frames_are_never_cropped(self) -> None:
        chain = self._chain({"id": 9, "type": "title", "path": "Generated frame", "duration": 4, "text": "Title",
                             "effect": "None", "transition": "Fade", "transitionTime": 0.5,
                             "frameBackground": "#112233", "crop": {"rect": {"x": 0.2, "y": 0.2, "w": 0.5, "h": 0.5}}})
        self.assertNotIn("crop=w=trunc", chain)

    def test_movies_take_the_same_crop(self) -> None:
        chain = self._chain(self._item(type="video", path="/videos/a.mp4", effect="Original motion",
                                       crop={"rect": {"x": 0.1, "y": 0, "w": 0.8, "h": 1}}))
        self.assertIn("crop=w=trunc", chain)

    def test_lasso_switches_to_filter_complex_with_a_mask_input(self) -> None:
        command = self._commands_for([self._item(crop={"rect": {"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8},
                                                       "lasso": [[0.2, 0.3], [0.6, 0.25], [0.5, 0.7]],
                                                       "feather": 0.4})])[0]
        self.assertNotIn("-vf", command)
        graph = command[command.index("-filter_complex") + 1]
        self.assertEqual(command[command.index("-map") + 1], "[v]")
        self.assertIn("alphamerge", graph)
        self.assertIn("overlay=0:0:format=auto", graph)
        # The crop runs before the cut, the fit and the caption after it.
        self.assertIn("crop=w=trunc", graph.split("[cutpre]")[0])
        self.assertIn("drawtext", graph.split("[cutfilled]")[-1])
        # A mask file is written next to the segments and fed in as input 1.
        mask_path = Path(command[command.index("-filter_complex") - 1])
        self.assertEqual(mask_path.name, "mask-0000.pgm")
        self.assertTrue(mask_path.exists())
        self.assertTrue(mask_path.read_bytes().startswith(b"P5\n512 512\n255\n"))
        # `-loop 1 -framerate fps -t duration -i mask.pgm`: a bounded still
        # stream, frame-aligned with the picture, so scale2ref never starves.
        before = command[command.index(str(mask_path)) - 7:command.index(str(mask_path))]
        self.assertEqual(before[0], "-loop")
        self.assertEqual(before[1], "1")
        self.assertEqual(before[2], "-framerate")
        self.assertEqual(before[4], "-t")
        self.assertEqual(before[6], "-i")
        self.assertEqual(command[command.index(str(mask_path)) + 1], "-filter_complex")

    def test_lasso_without_a_rectangle_still_cuts(self) -> None:
        graph = self._chain(self._item(crop={"lasso": [[0.1, 0.1], [0.9, 0.2], [0.5, 0.9]]}))
        self.assertIn("[0:v]null[cutpre]", graph)

    def test_crop_and_look_together(self) -> None:
        chain = self._chain(self._item(crop={"rect": {"x": 0.2, "y": 0.2, "w": 0.6, "h": 0.6}}, filter="mono"))
        self.assertLess(chain.index("crop=w=trunc"), chain.index("colorchannelmixer="),
                        "filter the cropped picture, not the whole file")


class CropDetectEndpointTest(unittest.TestCase):
    """GET /api/media/cropdetect — the "remove black bars" button."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")
        (self.settings.photos_dir / "letterbox.jpg").write_bytes(b"x" * 64)
        (self.settings.photos_dir / "empty.jpg").write_bytes(b"")
        (self.settings.photos_dir / "notes.txt").write_text("not a picture")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, **query) -> Any:
        from app.main import media_cropdetect
        arguments = {"root": "photos", "path": "letterbox.jpg", "rotation": 0, "seconds": 4.0}
        arguments.update(query)
        with mock.patch("app.main.settings", self.settings):
            return media_cropdetect(**arguments)

    def fake_ffmpeg(self, stderr: str):
        completed = mock.Mock(stderr=stderr, stdout="", returncode=0)
        return mock.patch("app.main.subprocess.run", return_value=completed)

    def test_bars_come_back_as_fractions(self) -> None:
        with self.fake_ffmpeg(CROPDETECT_STDERR):
            found = self.call()
        self.assertTrue(found["bars"])
        self.assertEqual(found["source"], {"width": 1920, "height": 1080})
        self.assertAlmostEqual(found["rect"]["h"], 800 / 1080)

    def test_the_file_is_scanned_through_ffmpeg(self) -> None:
        with mock.patch("app.main.subprocess.run", return_value=mock.Mock(stderr=CROPDETECT_STDERR)) as run:
            self.call(rotation=90, seconds=2)
        command = run.call_args[0][0]
        self.assertIn(str(self.settings.photos_dir / "letterbox.jpg"), command)
        self.assertIn("cropdetect=", command[command.index("-vf") + 1])
        self.assertTrue(command[command.index("-vf") + 1].startswith("transpose="), "turn it the way the user sees it")

    def test_a_full_frame_is_reported_without_bars(self) -> None:
        with self.fake_ffmpeg(CROPDETECT_STDERR.replace("crop=1920:800:0:140", "crop=1920:1080:0:0")):
            found = self.call()
        self.assertFalse(found["bars"])

    def test_rejections(self) -> None:
        from fastapi import HTTPException
        cases = [
            ({"path": "missing.jpg"}, 404),
            ({"path": "notes.txt"}, 415),
            ({"path": "empty.jpg"}, 422),
            ({"path": "../etc/passwd"}, 400),
            # A leading slash is joined onto the mount, so it is simply not there.
            ({"path": "/absolute/letterbox.jpg"}, 404),
        ]
        for query, status in cases:
            with self.subTest(query=query), self.assertRaises(HTTPException) as caught:
                self.call(**query)
            self.assertEqual(caught.exception.status_code, status)

    def test_music_mount_is_not_scannable(self) -> None:
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            self.call(root="music", path="song.mp3")

    def test_silent_ffmpeg_is_an_honest_error(self) -> None:
        from fastapi import HTTPException
        with self.fake_ffmpeg("frame= 0 fps= 0\n"):
            with self.assertRaises(HTTPException) as caught:
                self.call()
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("by hand", str(caught.exception.detail))

    def test_missing_ffmpeg_binary_is_a_service_error(self) -> None:
        from fastapi import HTTPException
        with mock.patch("app.main.subprocess.run", side_effect=OSError("ffmpeg not found")):
            with self.assertRaises(HTTPException) as caught:
                self.call()
        self.assertEqual(caught.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
