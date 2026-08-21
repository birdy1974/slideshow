from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
