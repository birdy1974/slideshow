"""Picture looks: the FFmpeg half must match what the browser preview shows.

The catalogue is shared (registry/picture-filters.json is imported by both
src/pictureFilters.ts and app/picture_filters.py), so these tests pin the
parameter resolution, the FFmpeg chain each parameter produces, the order of
that chain inside a real segment command, and the few magic numbers the
TypeScript side has to agree on.
"""
from __future__ import annotations

import math
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.database import Database
from app.picture_filters import (
    BLUR_SIGMA_FACTOR,
    IDENTITY,
    LIMITS,
    PIXELATE_DIVISOR,
    PRESETS,
    WARMTH_GREEN,
    WARMTH_RED,
    colour_matrix,
    filter_chain,
    has_look,
    picture_look,
    resolve_look,
)
from app.renderer import Renderer

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_MODULE = REPO_ROOT / "src" / "pictureFilters.ts"


def params(**overrides: float) -> dict[str, float]:
    values = dict(IDENTITY)
    values.update(overrides)
    return values


class RegistryTest(unittest.TestCase):
    def test_presets_loaded_from_the_shared_registry(self) -> None:
        self.assertTrue(PRESETS, "registry/picture-filters.json did not load")
        self.assertIn("mono", PRESETS)
        self.assertIn("vintage", PRESETS)
        self.assertNotIn("none", PRESETS, "the Original chip carries no parameters")

    def test_presets_only_use_known_parameters(self) -> None:
        for preset_id, preset_params in PRESETS.items():
            for name, value in preset_params.items():
                self.assertIn(name, IDENTITY, f"{preset_id}.{name} is not a parameter")
                low, high = LIMITS[name]
                self.assertGreaterEqual(value, low, f"{preset_id}.{name} below its range")
                self.assertLessEqual(value, high, f"{preset_id}.{name} above its range")

    def test_every_parameter_has_a_range(self) -> None:
        for name in IDENTITY:
            self.assertIn(name, LIMITS, f"{name} has no slider bounds")

    def test_frontend_uses_the_same_magic_numbers(self) -> None:
        """The two implementations share constants; keep them from drifting."""
        if not FRONTEND_MODULE.exists():
            self.skipTest("frontend sources are not part of this image")
        source = FRONTEND_MODULE.read_text(encoding="utf-8")
        for name, value in (("PIXELATE_DIVISOR", PIXELATE_DIVISOR), ("WARMTH_RED", WARMTH_RED), ("WARMTH_GREEN", WARMTH_GREEN)):
            match = re.search(rf"export const {name} = (-?[\d.]+)", source)
            self.assertIsNotNone(match, f"{name} is not exported by src/pictureFilters.ts")
            self.assertEqual(float(match.group(1)), float(value), f"{name} drifted between the browser and the renderer")

    def test_frontend_reads_the_same_registry_file(self) -> None:
        if not FRONTEND_MODULE.exists():
            self.skipTest("frontend sources are not part of this image")
        self.assertIn("registry/picture-filters.json", FRONTEND_MODULE.read_text(encoding="utf-8"))


class ResolveLookTest(unittest.TestCase):
    def test_no_look_is_the_identity(self) -> None:
        self.assertEqual(IDENTITY, resolve_look({}))
        self.assertEqual(IDENTITY, resolve_look(None))
        self.assertEqual("", filter_chain(resolve_look({}), 1920, 1080))
        self.assertFalse(has_look({}))

    def test_unknown_or_malformed_values_are_ignored(self) -> None:
        for item in ({"filter": "does-not-exist"}, {"filter": "mono", "filterAmount": "wide"},
                     {"filter": "mono", "filterAdjust": "nope"}, {"filter": 12, "filterAdjust": [1]}):
            resolved = resolve_look(item)
            self.assertEqual(set(IDENTITY), set(resolved))
            for value in resolved.values():
                self.assertTrue(math.isfinite(value))

    def test_preset_parameters_land_in_the_resolution(self) -> None:
        resolved = resolve_look({"filter": "mono"})
        self.assertEqual(1.0, resolved["grayscale"])
        self.assertTrue(has_look({"filter": "mono"}))

    def test_amount_fades_a_preset_toward_the_original(self) -> None:
        full = resolve_look({"filter": "noir"})
        half = resolve_look({"filter": "noir", "filterAmount": 0.5})
        off = resolve_look({"filter": "noir", "filterAmount": 0})
        for name, neutral in IDENTITY.items():
            self.assertAlmostEqual(neutral + (full[name] - neutral) / 2, half[name], places=6, msg=name)
        self.assertEqual(IDENTITY, off)
        self.assertFalse(has_look({"filter": "noir", "filterAmount": 0}), "0 % is the original")

    def test_amount_is_clamped_to_zero_through_one(self) -> None:
        self.assertEqual(resolve_look({"filter": "mono", "filterAmount": 5}), resolve_look({"filter": "mono"}))
        self.assertEqual(resolve_look({"filter": "mono", "filterAmount": -2}), resolve_look({"filter": "mono", "filterAmount": 0}))

    def test_sliders_scale_the_multiplicative_parameters(self) -> None:
        resolved = resolve_look({"filter": "vivid", "filterAdjust": {"brightness": 1.1, "contrast": 0.9, "saturation": 0.5}})
        self.assertAlmostEqual(PRESETS["vivid"]["contrast"] * 0.9, resolved["contrast"], places=6)
        self.assertAlmostEqual(PRESETS["vivid"]["saturation"] * 0.5, resolved["saturation"], places=6)
        self.assertAlmostEqual(1.1, resolved["brightness"], places=6)

    def test_sliders_shift_the_additive_parameters(self) -> None:
        resolved = resolve_look({"filter": "vintage", "filterAdjust": {"warmth": -1.5, "vignette": 0.25, "softness": 1}})
        self.assertAlmostEqual(-1.5, resolved["warmth"], places=6)
        self.assertAlmostEqual(PRESETS["vintage"]["vignette"] + 0.25, resolved["vignette"], places=6)
        self.assertAlmostEqual(1.0, resolved["softness"], places=6)

    def test_sliders_stay_inside_their_ranges(self) -> None:
        resolved = resolve_look({"filter": "none", "filterAdjust": {"brightness": 9, "contrast": -3, "saturation": 40, "warmth": 99, "vignette": -4, "softness": 12}})
        for name in ("brightness", "contrast", "saturation", "warmth", "vignette", "softness"):
            low, high = LIMITS[name]
            self.assertGreaterEqual(resolved[name], low, name)
            self.assertLessEqual(resolved[name], high, name)


class ColourMatrixTest(unittest.TestCase):
    """The matrix must be the one the CSS filter functions define, or the
    browser preview and the MP4 drift apart."""

    def test_identity_produces_no_filter(self) -> None:
        self.assertIsNone(colour_matrix(params()))

    def test_grayscale_is_the_css_luma_matrix(self) -> None:
        matrix = colour_matrix(params(grayscale=1))
        self.assertIsNotNone(matrix)
        for row in matrix:
            self.assertAlmostEqual(0.2126, row[0], places=4)
            self.assertAlmostEqual(0.7152, row[1], places=4)
            self.assertAlmostEqual(0.0722, row[2], places=4)

    def test_sepia_is_the_css_sepia_matrix(self) -> None:
        matrix = colour_matrix(params(sepia=1))
        self.assertIsNotNone(matrix)
        self.assertAlmostEqual(0.393, matrix[0][0], places=4)
        self.assertAlmostEqual(0.769, matrix[0][1], places=4)
        self.assertAlmostEqual(0.189, matrix[0][2], places=4)
        self.assertAlmostEqual(0.131, matrix[2][2], places=4)

    def test_partial_amounts_interpolate_like_css(self) -> None:
        matrix = colour_matrix(params(grayscale=0.5))
        self.assertIsNotNone(matrix)
        self.assertAlmostEqual(0.2126 + 0.7874 * 0.5, matrix[0][0], places=6)
        self.assertAlmostEqual(0.7152 + 0.2848 * 0.5, matrix[1][1], places=6)

    def test_grayscale_then_sepia_is_one_folded_matrix(self) -> None:
        # Duotone-style looks stack grayscale and sepia; CSS applies them left
        # to right, so the folded matrix is M_sepia · M_gray.
        matrix = colour_matrix(params(grayscale=1, sepia=1))
        self.assertIsNotNone(matrix)
        luma = [0.2126, 0.7152, 0.0722]
        row_sums = [0.393 + 0.769 + 0.189, 0.349 + 0.686 + 0.168, 0.272 + 0.534 + 0.131]
        for index, row in enumerate(matrix):
            for column in range(3):
                self.assertAlmostEqual(row_sums[index] * luma[column], row[column], places=6)

    def test_warmth_is_a_plain_rgb_gain(self) -> None:
        matrix = colour_matrix(params(warmth=2))
        self.assertIsNotNone(matrix)
        self.assertAlmostEqual(1 + WARMTH_RED * 2, matrix[0][0], places=6)
        self.assertAlmostEqual(1 + WARMTH_GREEN * 2, matrix[1][1], places=6)
        self.assertAlmostEqual(1 - WARMTH_RED * 2, matrix[2][2], places=6)
        self.assertEqual(0.0, matrix[0][1])
        self.assertEqual(0.0, matrix[2][0])

    def test_hue_rotate_uses_the_css_spec_matrix(self) -> None:
        # Not a true HSV rotation: the Filter Effects spec defines hue-rotate()
        # as this exact matrix, which is why the browser and the render agree
        # when the renderer uses it too (its blue row does not sum to 1).
        for degrees in (12, 165, 180, -45):
            angle = math.radians(degrees)
            cos, sin = math.cos(angle), math.sin(angle)
            expected = [
                [0.213 + 0.787 * cos - 0.213 * sin, 0.715 - 0.715 * cos - 0.715 * sin, 0.072 - 0.072 * cos + 0.928 * sin],
                [0.213 - 0.213 * cos + 0.143 * sin, 0.715 + 0.285 * cos + 0.140 * sin, 0.072 - 0.072 * cos - 0.283 * sin],
                [0.213 - 0.213 * cos - 0.787 * sin, 0.715 - 0.715 * cos + 0.715 * sin, 0.072 + 0.928 * cos - 0.072 * sin],
            ]
            matrix = colour_matrix(params(hueRotate=degrees))
            self.assertIsNotNone(matrix, degrees)
            for row, want in zip(matrix, expected):
                for value, target in zip(row, want):
                    self.assertAlmostEqual(target, value, places=9, msg=f"hue-rotate({degrees})")

    def test_no_preset_produces_runaway_coefficients(self) -> None:
        # Hue rotation legitimately goes negative (infrared reaches -0.57); this
        # is a smoke test for a broken product, not for a particular look.
        for preset_id in PRESETS:
            matrix = colour_matrix(resolve_look({"filter": preset_id}))
            if matrix is None:
                continue
            for row in matrix:
                for value in row:
                    self.assertTrue(math.isfinite(value), preset_id)
                    self.assertGreater(value, -1.5, f"{preset_id} channel mix collapsed")
                    self.assertLess(value, 2.5, f"{preset_id} channel mix exploded")


class FilterChainTest(unittest.TestCase):
    def test_brightness_contrast_saturation_map_onto_eq(self) -> None:
        chain = filter_chain(params(brightness=1.1, contrast=1.25, saturation=0.8), 1920, 1080)
        self.assertIn("eq=brightness=0.05:contrast=1.25:saturation=0.8", chain)

    def test_eq_is_skipped_when_nothing_changes(self) -> None:
        self.assertNotIn("eq=", filter_chain(params(vignette=0.5), 1920, 1080))

    def test_softness_uses_the_calibrated_sigma(self) -> None:
        chain = filter_chain(params(softness=1.6), 1920, 1080)
        self.assertIn(f"gblur=sigma={1.6 * BLUR_SIGMA_FACTOR:g}", chain)

    def test_sharpen_is_a_real_unsharp_mask(self) -> None:
        self.assertIn("unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.86", filter_chain(params(sharpen=0.8), 1920, 1080))

    def test_vignette_angle_is_radians(self) -> None:
        chain = filter_chain(params(vignette=0.8), 1920, 1080)
        self.assertIn(f"vignette=angle={round(0.8 * math.pi / 4, 6):g}", chain)

    def test_negative_goes_through_rgb(self) -> None:
        # negate on YUV is not what CSS invert() does.
        self.assertEqual("format=rgb24,negate,format=yuv420p", filter_chain(params(invert=1), 1920, 1080))

    def test_pixelate_blocks_stay_relative_to_the_frame(self) -> None:
        for width, height in ((1920, 1080), (3840, 2160), (854, 480)):
            chain = filter_chain(params(pixelate=1), width, height)
            sizes = re.findall(r"scale=(\d+):(\d+)", chain)
            self.assertEqual(2, len(sizes), chain)
            block = max(2, round(width / PIXELATE_DIVISOR))
            self.assertEqual((str(width // block), str(height // block)), sizes[0], chain)
            self.assertEqual((str(width), str(height)), sizes[1], "the second scale must restore the frame size")
            self.assertIn("flags=neighbor", chain, "smooth scaling would not pixelate at all")
            self.assertIn("setsar=1", chain)

    def test_chain_order_matches_the_css_order(self) -> None:
        chain = filter_chain(params(pixelate=1, brightness=1.1, grayscale=1, invert=1, softness=1, sharpen=0.5, vignette=0.4), 1920, 1080)
        marks = ["flags=neighbor", "eq=", "colorchannelmixer=", "negate", "gblur=", "unsharp=", "vignette="]
        positions = [chain.index(mark) for mark in marks]
        self.assertEqual(sorted(positions), positions, chain)

    def test_look_survives_a_missing_frame_size(self) -> None:
        # Pixelate needs the frame size; everything else must still render.
        chain = filter_chain(params(pixelate=1, grayscale=1), 0, 0)
        self.assertNotIn("scale=", chain)
        self.assertIn("colorchannelmixer=", chain)


class SegmentLookTest(unittest.TestCase):
    """Where the look sits in a real per-segment FFmpeg command."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.settings = Settings(config_dir=base / "config", photos_dir=base / "photos", videos_dir=base / "videos",
                                 output_dir=base / "out", music_dir=base / "music")
        for directory in (self.settings.photos_dir, self.settings.videos_dir, self.settings.work_dir, self.settings.preview_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (self.settings.photos_dir / "a.jpg").write_bytes(b"x" * 64)
        (self.settings.videos_dir / "a.mp4").write_bytes(b"x" * 64)
        self.renderer = Renderer(Database(base / "look.db"), self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _segment_filters(self, media: list[dict]) -> list[str]:
        project = {"id": 1, "media": media, "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps",
                                                       "bitrate": "8 Mbps", "encoder": "libx264", "path": "/output", "filename": "movie"}}
        commands: list[list[str]] = []

        def fake_run(command, cancelled, log_file):
            commands.append(list(command))
            Path(command[-1]).write_bytes(b"segment")

        with mock.patch.object(self.renderer, "_validate_media", return_value=None), \
             mock.patch.object(self.renderer, "_run_ffmpeg", side_effect=fake_run), \
             mock.patch.object(self.renderer, "_make_soundtrack", return_value=None), \
             mock.patch.object(self.renderer, "_probe_duration", return_value=2.0):
            work = self.settings.work_dir / "job"
            work.mkdir(parents=True, exist_ok=True)
            self.renderer.render(project, "render", work, threading.Event(), lambda p, s: None)
        return [command[command.index("-vf") + 1] for command in commands if "-vf" in command]

    @staticmethod
    def _item(**overrides) -> dict:
        item = {"id": 1, "type": "image", "path": "/photos/a.jpg", "duration": 3, "effect": "Ken Burns · Zoom in",
                "transition": "Fade", "transitionTime": 0.5, "text": "Holiday"}
        item.update(overrides)
        return item

    def test_look_runs_after_zoompan_and_before_the_caption(self) -> None:
        filters = self._segment_filters([self._item(filter="mono")])
        chain = filters[0]
        self.assertIn("colorchannelmixer=", chain)
        self.assertLess(chain.index("zoompan="), chain.index("colorchannelmixer="), "filter the output-sized frame, not the 24 MP source")
        self.assertLess(chain.index("colorchannelmixer="), chain.index("drawtext"), "the caption keeps its own colour")
        self.assertLess(chain.index("colorchannelmixer="), chain.index("format=yuv420p"))

    def test_untouched_clips_add_nothing(self) -> None:
        # "Original", a preset faded to 0 % and an item that never heard of
        # filters must all produce the byte-identical command. (The blurred
        # letterbox backdrop carries its own eq=, so comparing against the
        # untouched baseline is the only honest assertion here.)
        baseline = self._segment_filters([self._item()])[0]
        for chain in self._segment_filters([self._item(id=2, filter="none"), self._item(id=3, filterAmount=0.4),
                                            self._item(id=4, filter="mono", filterAmount=0), self._item(id=5, filterAdjust={})]):
            self.assertEqual(baseline, chain)

    def test_text_frames_never_take_a_look(self) -> None:
        filters = self._segment_filters([
            {"id": 9, "type": "title", "path": "Generated frame", "duration": 4, "text": "Title", "effect": "None",
             "transition": "Fade", "transitionTime": 0.5, "frameBackground": "#112233", "filter": "noir"},
        ])
        self.assertNotIn("colorchannelmixer", filters[0])
        self.assertNotIn("vignette", filters[0])

    def test_movies_take_the_same_look(self) -> None:
        filters = self._segment_filters([
            {"id": 4, "type": "video", "path": "/videos/a.mp4", "duration": 2, "effect": "Original motion",
             "transition": "Fade", "transitionTime": 0.5, "filter": "vintage", "filterAmount": 0.5},
        ])
        self.assertIn("colorchannelmixer=", filters[0])
        self.assertIn("vignette=angle=", filters[0])

    def test_picture_look_helper_is_what_the_renderer_calls(self) -> None:
        self.assertEqual(filter_chain(resolve_look({"filter": "mono"}), 1920, 1080), picture_look({"filter": "mono"}, 1920, 1080))


if __name__ == "__main__":
    unittest.main()
