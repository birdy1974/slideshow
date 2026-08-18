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

    def test_output_root_is_folder_pick_only(self) -> None:
        (self.settings.output_dir / "renders").mkdir()
        (self.settings.output_dir / "renders" / "done.mp4").write_bytes(b"mp4")
        (self.settings.output_dir / "stray.mp4").write_bytes(b"mp4")
        with self.assertRaises(UnsafePath): browse(self.settings, "output")
        result = browse(self.settings, "output", "", folders_only=True)
        self.assertEqual(["renders"], [x["name"] for x in result["entries"]])
        self.assertTrue(all(x["kind"] == "directory" for x in result["entries"]))

    def test_folders_only_hides_media_files(self) -> None:
        result = browse(self.settings, "photos", "", folders_only=True)
        self.assertEqual(["trip"], [x["name"] for x in result["entries"]])
        with self.assertRaises(UnsafePath): browse(self.settings, "photos", "../output", folders_only=True)


if __name__ == "__main__":
    unittest.main()
