"""``/api/health``'s fonts report — the one number that explains a missing caption.

The renderer draws every caption from ``settings.fonts_dir`` (drawtext by explicit
file, libass by ``fontsdir=``). A deployment that mounts an empty directory there
shows the browser's fonts and the render's fallbacks side by side, and says
nothing; this report is the "nothing" made visible. Names only — no fontconfig,
no FFmpeg — so it answers while a render saturates the machine.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.font_registry import font_files, fonts_status


class FontsStatusTest(unittest.TestCase):
    def test_counts_only_the_families_that_have_a_real_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            families = font_files()
            self.assertTrue(families, "the bundled registry must be readable in a checkout")
            first = next(iter(families))
            name = families[first].get("Regular") or next(iter(families[first].values()))
            (root / name).write_bytes(b"\x00\x01")

            status = fonts_status(root)

            self.assertTrue(status["exists"])
            self.assertEqual(str(root), status["dir"])
            self.assertEqual(len(families), status["families"])
            self.assertEqual(1, status["familiesWithFile"], "only the family whose TTF exists")
            self.assertEqual(1, status["files"])

    def test_an_absent_directory_is_reported_as_absent_not_as_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            settings = Settings(config_dir=Path(temp), photos_dir=Path(temp), videos_dir=Path(temp),
                               output_dir=Path(temp), music_dir=Path(temp))
            status = fonts_status(settings.config_dir / "fonts-does-not-exist")
            self.assertFalse(status["exists"])
            self.assertEqual(0, status["files"])
            self.assertEqual(0, status["familiesWithFile"])
            self.assertGreater(status["families"], 0, "the catalogue is still what the picker offers")


if __name__ == "__main__":
    unittest.main()
