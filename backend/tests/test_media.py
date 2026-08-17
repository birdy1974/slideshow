from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.media import UnsafePath, browse, mounted_path, safe_path


class MediaSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        (base / "photos" / "trip").mkdir()
        (base / "photos" / "trip" / "image.jpg").write_bytes(b"jpg")
        (base / "photos" / "trip" / "ignore.txt").write_text("no")
        self.settings = Settings(base/"config", base/"photos", base/"videos", base/"music", base/"output")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_browse_filters_and_resolves_mounts(self) -> None:
        result = browse(self.settings, "photos", "trip")
        self.assertEqual(["image.jpg"], [x["name"] for x in result["entries"]])
        self.assertEqual(self.settings.photos_dir / "trip" / "image.jpg", mounted_path(self.settings, "/photos/trip", "image.jpg"))

    def test_traversal_is_rejected(self) -> None:
        with self.assertRaises(UnsafePath): safe_path(self.settings.photos_dir, "../../etc/passwd")
        with self.assertRaises(UnsafePath): mounted_path(self.settings, "/etc/passwd")


if __name__ == "__main__":
    unittest.main()
