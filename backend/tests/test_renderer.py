from __future__ import annotations

import unittest

from app.renderer import parse_number, xfade_name


class RendererMappingTest(unittest.TestCase):
    def test_ui_transition_names_map_to_ffmpeg(self) -> None:
        self.assertEqual("circlecrop", xfade_name("Circle crop"))
        self.assertEqual("smoothleft", xfade_name("Smooth left"))
        self.assertEqual("dissolve", xfade_name("GLSL · Dreamy"))
        self.assertEqual("fade", xfade_name("Unknown future transition"))

    def test_preset_numbers_are_parsed(self) -> None:
        self.assertEqual(30, parse_number("30 fps", 25))
        self.assertEqual(8, parse_number("8 Mbps · High", 4))


if __name__ == "__main__":
    unittest.main()
