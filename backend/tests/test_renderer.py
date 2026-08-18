from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.config import Settings
from app.renderer import Renderer, _parse_xfade_help, parse_number, xfade_name

# Shape of `ffmpeg -h filter=xfade` on an FFmpeg 5.x NAS build: the catalogue
# stops at fadeslow; hlwind/hrwind/vuwind/vdwind and cover*/reveal* are absent.
FFMPEG5_XFADE_HELP = """
xfade AVOptions:
   transition        <int>        ..FV.....T. set cross fade transition (from -1 to 45) (default fade)
     custom          -1           ..FV.....T. custom transition
     fade            0            ..FV.....T. fade transition
     dissolve        25           ..FV.....T. dissolve transition
     wipeleft        1            ..FV.....T. wipe left transition
     fadegrays       36           ..FV.....T. fadegrays transition
     fadefast        44           ..FV.....T. fast fade transition
     fadeslow        45           ..FV.....T. slow fade transition
   duration          <duration>   ..FV.....T. set cross fade duration (default 1)
   offset            <duration>   ..FV.....T. set cross fade offset relative to first input stream (default 0)
"""


class RendererMappingTest(unittest.TestCase):
    def test_ui_transition_names_map_to_ffmpeg(self) -> None:
        self.assertEqual("circlecrop", xfade_name("Circle crop"))
        self.assertEqual("smoothleft", xfade_name("Smooth left"))
        self.assertEqual("dissolve", xfade_name("GLSL · Dreamy"))
        self.assertEqual("fade", xfade_name("Unknown future transition"))

    def test_preset_numbers_are_parsed(self) -> None:
        self.assertEqual(30, parse_number("30 fps", 25))
        self.assertEqual(8, parse_number("8 Mbps · High", 4))

    def test_xfade_help_parsing(self) -> None:
        names = _parse_xfade_help(FFMPEG5_XFADE_HELP)
        self.assertIn("fade", names)
        self.assertIn("fadeslow", names)
        self.assertNotIn("hrwind", names)  # FFmpeg 5.x lacks wind transitions
        self.assertNotIn("custom", names)  # custom needs an expression; never mapped
        self.assertEqual(set(), _parse_xfade_help("no options here"))


class RendererFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.renderer = Renderer(Database(base / "test.db"), Settings())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unsupported_transitions_degrade_to_dissolve(self) -> None:
        self.renderer._xfade_supported = _parse_xfade_help(FFMPEG5_XFADE_HELP)
        self.assertEqual("dissolve", self.renderer.resolve_xfade("Horizontal right wind"))
        self.assertEqual("dissolve", self.renderer.resolve_xfade("Cover left"))
        self.assertEqual("dissolve", self.renderer.resolve_xfade("GLSL · Dreamy"))
        self.assertEqual("fade", self.renderer.resolve_xfade("Fade"))
        self.assertEqual("wipeleft", self.renderer.resolve_xfade("Wipe left"))

    def test_failed_probe_keeps_full_catalogue(self) -> None:
        self.renderer._xfade_supported = set()
        self.assertEqual("hrwind", self.renderer.resolve_xfade("Horizontal right wind"))


if __name__ == "__main__":
    unittest.main()
