from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.media import UnsafePath, browse, mounted_path, safe_path
from app.renderer import RenderError, Renderer, source_path


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

    def test_browse_marks_empty_files(self) -> None:
        (self.settings.photos_dir / "trip" / "empty.jpg").write_bytes(b"")
        result = browse(self.settings, "photos", "trip")
        by_name = {x["name"]: x for x in result["entries"]}
        self.assertFalse(by_name["image.jpg"]["empty"])
        self.assertTrue(by_name["empty.jpg"]["empty"])
        self.assertEqual(0, by_name["empty.jpg"]["size"])

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

    def test_browse_unreadable_folder_raises_permission_error(self) -> None:
        locked = self.settings.photos_dir / "Willem, 13-jul 2025"
        locked.mkdir()
        (locked / "secret.jpg").write_bytes(b"jpg")
        original = locked.stat().st_mode
        locked.chmod(0o000)
        try:
            if os.access(locked, os.R_OK):
                self.skipTest("process can still read a 000 directory (e.g. running as root)")
            with self.assertRaises(PermissionError) as ctx:
                browse(self.settings, "photos", "Willem, 13-jul 2025")
            self.assertIn("No permission to open", str(ctx.exception))
            self.assertIn("Willem, 13-jul 2025", str(ctx.exception))
        finally:
            locked.chmod(original)

    def test_browse_parent_survives_unreadable_child(self) -> None:
        locked = self.settings.photos_dir / "Willem, 13-jul 2025"
        locked.mkdir()
        original = locked.stat().st_mode
        locked.chmod(0o000)
        try:
            result = browse(self.settings, "photos", "")
            by_name = {x["name"]: x for x in result["entries"]}
            self.assertIn("trip", by_name)
            self.assertTrue(by_name["trip"]["accessible"])
            self.assertIn("Willem, 13-jul 2025", by_name)
            self.assertEqual("directory", by_name["Willem, 13-jul 2025"]["kind"])
            if not os.access(locked, os.R_OK):
                self.assertFalse(by_name["Willem, 13-jul 2025"]["accessible"])
        finally:
            locked.chmod(original)

    def test_browse_skips_children_that_cannot_be_statd(self) -> None:
        dangling = self.settings.photos_dir / "broken-link.jpg"
        dangling.symlink_to(self.settings.photos_dir / "does-not-exist.jpg")
        result = browse(self.settings, "photos", "")
        names = [x["name"] for x in result["entries"]]
        self.assertIn("trip", names)
        self.assertNotIn("broken-link.jpg", names)


class SourcePathTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.photos = base / "photos"
        self.settings = Settings(base / "config", self.photos, base / "videos", base / "music", base / "output")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_folder_plus_name(self) -> None:
        folder = self.photos / "_schilderij" / "city"
        folder.mkdir(parents=True)
        (folder / "photo1.jpg").write_bytes(b"jpg")
        resolved = source_path(self.settings, {"path": "/photos/_schilderij/city", "name": "photo1.jpg"})
        self.assertEqual(folder / "photo1.jpg", resolved)

    def test_full_file_path(self) -> None:
        folder = self.photos / "_schilderij" / "city"
        folder.mkdir(parents=True)
        target = folder / "photo1.jpg"
        target.write_bytes(b"jpg")
        resolved = source_path(self.settings, {"path": "/photos/_schilderij/city/photo1.jpg", "name": "photo1.jpg"})
        self.assertEqual(target, resolved)

    def test_dotted_folder_is_not_treated_as_a_file(self) -> None:
        folder = self.photos / "holiday.2024"
        folder.mkdir()
        (folder / "shot.jpg").write_bytes(b"jpg")
        resolved = source_path(self.settings, {"path": "/photos/holiday.2024", "name": "shot.jpg"})
        self.assertEqual(folder / "shot.jpg", resolved)

    def test_special_characters_in_photo_and_music_names(self) -> None:
        """Spaces, underscores, dashes, parentheses and unicode must round-trip."""
        folder = self.photos / "holiday 2024" / "_schilderij"
        folder.mkdir(parents=True)
        photo = "My Photo - 1_final (hdr).jpg"
        (folder / photo).write_bytes(b"jpg-bytes")
        music_dir = self.settings.music_dir / "4 Strings - Main Line (2006)"
        music_dir.mkdir()
        track = "01 - Take Me Away (Into The Night).mp3"
        (music_dir / track).write_bytes(b"mp3-bytes")

        listed = browse(self.settings, "photos", "holiday 2024/_schilderij")
        self.assertEqual([photo], [x["name"] for x in listed["entries"] if x["kind"] != "directory"])
        self.assertEqual(f"/photos/holiday 2024/_schilderij/{photo}", listed["entries"][0]["path"])

        resolved_photo = source_path(self.settings, {
            "name": photo, "path": f"/photos/holiday 2024/_schilderij/{photo}",
        })
        self.assertEqual(folder / photo, resolved_photo)
        self.assertTrue(resolved_photo.is_file())

        resolved_track = source_path(self.settings, {
            "name": track, "path": f"/music/4 Strings - Main Line (2006)/{track}",
        })
        self.assertEqual(music_dir / track, resolved_track)
        # Folder + name form (legacy snapshots) must still find the file.
        self.assertEqual(
            music_dir / track,
            source_path(self.settings, {"name": track, "path": "/music/4 Strings - Main Line (2006)"}),
        )

    def test_directory_item_is_reported_as_a_folder(self) -> None:
        from app.renderer import Renderer
        from app.database import Database
        folder = self.photos / "_schilderij" / "city"
        folder.mkdir(parents=True)
        renderer = Renderer(Database(self.settings.config_dir / "t.db"), self.settings)
        try:
            with self.assertRaises(RenderError) as ctx:
                renderer._validate_media({"media": [{"name": "city", "path": "/photos/_schilderij/city", "type": "image"}], "soundtrack": {"tracks": []}})
            self.assertIn("is a folder, not a media file", str(ctx.exception))
        finally:
            renderer.pool.shutdown(wait=False, cancel_futures=True)


class MainEndpointsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_validate_mount_references_uses_source_path(self) -> None:
        from app.main import validate_mount_references
        from unittest.mock import patch
        payload = {
            "output": {"path": "/output"},
            "media": [
                {"name": "photo.jpg", "path": "/photos/photo.jpg", "type": "image"},
                {"name": "Title", "type": "title"},
            ],
            "soundtrack": {
                "tracks": [{"name": "song.mp3", "path": "/music/song.mp3"}],
            },
        }
        with patch("app.main.settings", self.settings):
            # Should not raise any NameError or exception for valid mounts
            validate_mount_references(payload)

    def test_clear_output_directory(self) -> None:
        from app.main import clear_output_directory
        from unittest.mock import patch
        
        # Create some files and subdirectories in output
        out = self.settings.output_dir
        (out / "movie1.mp4").write_bytes(b"render1")
        (out / "movie2.mp4").write_bytes(b"render2")
        sub = out / "subfolder"
        sub.mkdir()
        (sub / "nested.mp4").write_bytes(b"nested")

        with patch("app.main.settings", self.settings):
            res = clear_output_directory("/output")
            self.assertEqual(2, res["deleted_files"])
            self.assertEqual(1, res["deleted_dirs"])
            self.assertEqual(0, len(list(out.iterdir())))


if __name__ == "__main__":
    unittest.main()
