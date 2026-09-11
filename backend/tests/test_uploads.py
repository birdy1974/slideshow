from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.uploads import UploadRejected, sanitized_name, store_upload, unique_name

# A stand-in for ffprobe: accepts the photo/movie extensions with a JSON video
# stream (like the real binary does for any decodable file) and fails for
# everything else, or whenever FAKE_PROBE_FAIL is set — mirroring "renamed
# junk" and real decode errors.
STUB_LINES = [
    '#!/usr/bin/env python3',
    '"""Test double for ffprobe: accepts known media extensions, else fails."""',
    'import json, os, pathlib, sys',
    'target = pathlib.Path(sys.argv[-1])',
    'if os.environ.get("FAKE_PROBE_FAIL"):',
    '    sys.stderr.write("Invalid data found when processing input" + chr(10))',
    '    sys.exit(1)',
    'if target.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"):',
    '    json.dump({"streams": [{"codec_type": "video"}]}, sys.stdout)',
    '    sys.exit(0)',
    'sys.stderr.write("Invalid data found when processing input" + chr(10))',
    'sys.exit(1)',
]
STUB = "\n".join(STUB_LINES) + "\n"


class UploadsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.ffprobe = base / "ffprobe-stub.py"
        self.ffprobe.write_text(STUB)
        self.ffprobe.chmod(self.ffprobe.stat().st_mode | stat.S_IEXEC)
        self.settings = Settings(
            base / "config", base / "photos", base / "videos", base / "music", base / "output",
            upload_max_mb=1,
            ffprobe_bin=str(self.ffprobe),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    # ------------------------------------------------------------- sanitising

    def test_names_are_sanitised_like_project_files(self) -> None:
        self.assertEqual("evil.jpg", sanitized_name("../../evil.jpg"))
        self.assertEqual("evil.jpg", sanitized_name("..\\..\\evil.jpg"))
        self.assertEqual("beach.jpg", sanitized_name("beach.jpg"))
        self.assertEqual("BEACH.jpg", sanitized_name("BEACH.JPG"))  # stem case kept, extension normalised
        self.assertEqual("holi-day.jpg", sanitized_name('holi:day?.jpg'))
        with self.assertRaises(UploadRejected):
            sanitized_name("notes.txt")
        with self.assertRaises(UploadRejected):
            sanitized_name("clip.mpg")
        with self.assertRaises(UploadRejected):
            sanitized_name("")

    def test_unique_names_never_clash_case_insensitively(self) -> None:
        root = self.settings.uploads_dir
        root.mkdir(parents=True)
        (root / "beach.jpg").write_bytes(b"x")
        self.assertEqual("beach-2.jpg", unique_name(root, "beach.jpg"))
        (root / "BEACH-2.jpg").write_bytes(b"x")
        self.assertEqual("beach-3.jpg", unique_name(root, "beach.jpg"))

    # ------------------------------------------------------------- store flow

    def test_upload_is_stored_probed_and_returned_as_browse_entry(self) -> None:
        entry = store_upload(self.settings, "beach.jpg", io.BytesIO(b"jpeg-bytes"))
        self.assertEqual("beach.jpg", entry["name"])
        self.assertEqual("/uploads/beach.jpg", entry["path"])
        self.assertEqual("image", entry["kind"])
        self.assertEqual(len(b"jpeg-bytes"), entry["size"])
        self.assertTrue((self.settings.uploads_dir / "beach.jpg").exists())

    def test_duplicate_uploads_are_de_duplicated(self) -> None:
        store_upload(self.settings, "beach.jpg", io.BytesIO(b"one"))
        entry = store_upload(self.settings, "beach.jpg", io.BytesIO(b"two"))
        self.assertEqual("beach-2.jpg", entry["name"])
        self.assertEqual("/uploads/beach-2.jpg", entry["path"])

    def test_movie_uploads_keep_their_kind(self) -> None:
        entry = store_upload(self.settings, "holiday.mov", io.BytesIO(b"moov"))
        self.assertEqual("video", entry["kind"])
        self.assertEqual("/uploads/holiday.mov", entry["path"])

    def test_oversized_uploads_are_rejected_without_a_trace(self) -> None:
        with self.assertRaises(UploadRejected) as caught:
            store_upload(self.settings, "big.jpg", io.BytesIO(b"x" * (1024 * 1024 + 1)))
        self.assertIn("upload limit", str(caught.exception))
        self.assertEqual([], list(self.settings.uploads_dir.glob("*")))

    def test_empty_uploads_are_rejected(self) -> None:
        with self.assertRaises(UploadRejected) as caught:
            store_upload(self.settings, "empty.jpg", io.BytesIO(b""))
        self.assertIn("empty", str(caught.exception))
        self.assertEqual([], list(self.settings.uploads_dir.glob("*")))

    def test_files_ffprobe_cannot_read_are_rejected_and_removed(self) -> None:
        # A text file renamed .mp4 passes sanitising and the size cap, then
        # must fail the probe and leave nothing behind.
        os.environ["FAKE_PROBE_FAIL"] = "1"
        try:
            with self.assertRaises(UploadRejected) as caught:
                store_upload(self.settings, "clip.mp4", io.BytesIO(b"not-a-movie"))
            self.assertIn("Not a valid movie", str(caught.exception))
        finally:
            os.environ.pop("FAKE_PROBE_FAIL", None)
        self.assertEqual([], list(self.settings.uploads_dir.glob("*")))

    def test_uploads_default_below_the_config_directory(self) -> None:
        # Without UPLOADS_DIR (bare checkout / dev), uploads persist under the
        # config directory instead of failing on a missing /uploads mount.
        settings = Settings(
            Path(self.temp.name) / "config", Path(self.temp.name) / "photos", Path(self.temp.name) / "videos",
            Path(self.temp.name) / "music", Path(self.temp.name) / "output", ffprobe_bin=str(self.ffprobe),
        )
        self.assertEqual(settings.config_dir / "uploads", settings.uploads_dir)
        self.assertIn("uploads", settings.media_roots)

    def test_uploads_root_is_browsable_like_a_mount(self) -> None:
        from app.media import browse
        store_upload(self.settings, "beach.jpg", io.BytesIO(b"jpeg-bytes"))
        result = browse(self.settings, "uploads", "")
        self.assertEqual(["beach.jpg"], [x["name"] for x in result["entries"]])


if __name__ == "__main__":
    unittest.main()
