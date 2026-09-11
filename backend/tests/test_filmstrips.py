from __future__ import annotations

import io
import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from app.config import Settings
from app.filmstrips import FilmstripUnavailable, build_filmstrip

# Stubs for ffprobe (reports a fixed duration) and ffmpeg (fabricates the
# sprite JPEG and logs its arguments). Like the real binary, the ffmpeg stub
# refuses output names without a declared muxer — the .part guard from the
# transition-preview fix.
FFPROBE_LINES = [
    '#!/usr/bin/env python3',
    'import json, sys',
    'json.dump({"format": {"duration": "40.0"}}, sys.stdout)',
]
FFMPEG_LINES = [
    '#!/usr/bin/env python3',
    'import os, pathlib, sys',
    'args = sys.argv[1:]',
    'with open(os.environ["FFMPEG_STUB_LOG"], "a") as handle:',
    '    handle.write(" ".join(args) + chr(10))',
    'out = pathlib.Path(args[-1])',
    'tail = args[args.index("-i") + 1:] if "-i" in args else args',
    'declared = tail[tail.index("-f") + 1] if "-f" in tail else ""',
    'ext = out.suffix.lstrip(".").lower()',
    'if ext not in ("png", "jpg", "jpeg") and declared not in ("mjpeg", "image2"):',
    '    sys.stderr.write("Unable to choose an output format for " + str(out) + chr(10))',
    '    sys.exit(1)',
    'out.write_bytes(b"JPG-STUB")',
]


class FilmstripsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.log = base / "calls.log"
        os.environ["FFMPEG_STUB_LOG"] = str(self.log)
        self.ffprobe = base / "ffprobe-stub.py"
        self.ffprobe.write_text("\n".join(FFPROBE_LINES) + "\n")
        self.ffprobe.chmod(self.ffprobe.stat().st_mode | stat.S_IEXEC)
        self.ffmpeg = base / "ffmpeg-stub.py"
        self.ffmpeg.write_text("\n".join(FFMPEG_LINES) + "\n")
        self.ffmpeg.chmod(self.ffmpeg.stat().st_mode | stat.S_IEXEC)
        self.settings = Settings(
            base / "config", base / "photos", base / "videos", base / "music", base / "output",
            ffprobe_bin=str(self.ffprobe), ffmpeg_bin=str(self.ffmpeg),
        )
        movie = base / "videos" / "holiday.mp4"
        movie.write_bytes(b"fake-movie-bytes")

    def tearDown(self) -> None:
        self.temp.cleanup()
        os.environ.pop("FFMPEG_STUB_LOG", None)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    def test_renders_one_sprite_with_ten_seeked_inputs(self) -> None:
        sprite = build_filmstrip(self.settings, "videos", "holiday.mp4")
        self.assertTrue(sprite.exists())
        self.assertTrue(sprite.name.endswith(".jpg"))
        render = next(c for c in self.calls() if "-filter_complex" in c)
        self.assertEqual(10, render.count("-ss"), "one fast seek per cell")
        self.assertIn("hstack=inputs=10", render)
        self.assertIn("scale=160:-2", render)
        self.assertIn("-f mjpeg", render)
        self.assertIn(".jpg.part", render)
        self.assertEqual([], [p.name for p in sprite.parent.glob("*.part")])

    def test_seek_timestamps_span_the_whole_movie(self) -> None:
        build_filmstrip(self.settings, "videos", "holiday.mp4")
        render = next(c for c in self.calls() if "-filter_complex" in c)
        stamps = [float(render.split("-ss")[i].split()[0]) for i in range(1, 11)]
        self.assertAlmostEqual(2.0, stamps[0], places=2)   # (0+0.5)/10 * 40
        self.assertAlmostEqual(38.0, stamps[-1], places=2)  # (9+0.5)/10 * 40

    def test_second_call_is_served_from_the_cache(self) -> None:
        first = build_filmstrip(self.settings, "videos", "holiday.mp4")
        renders = [c for c in self.calls() if "-filter_complex" in c]
        again = build_filmstrip(self.settings, "videos", "holiday.mp4")
        self.assertEqual(first, again)
        self.assertEqual(len(renders), len([c for c in self.calls() if "-filter_complex" in c]))

    def test_changing_the_file_invalidates_the_cache(self) -> None:
        movie = self.settings.videos_dir / "holiday.mp4"
        build_filmstrip(self.settings, "videos", "holiday.mp4")
        renders = len([c for c in self.calls() if "-filter_complex" in c])
        time.sleep(0.01)
        movie.write_bytes(b"re-uploaded-movie")
        os.utime(movie, (time.time() + 5, time.time() + 5))
        build_filmstrip(self.settings, "videos", "holiday.mp4")
        self.assertGreater(len([c for c in self.calls() if "-filter_complex" in c]), renders)

    def test_count_and_width_change_the_sprite(self) -> None:
        a = build_filmstrip(self.settings, "videos", "holiday.mp4", count=5)
        b = build_filmstrip(self.settings, "videos", "holiday.mp4", count=10, width=240)
        self.assertNotEqual(a, b)
        self.assertEqual(2, len([c for c in self.calls() if "-filter_complex" in c]))

    def test_missing_and_unreadable_files_are_refused(self) -> None:
        with self.assertRaises(FilmstripUnavailable):
            build_filmstrip(self.settings, "videos", "nope.mp4")
        notes = self.settings.videos_dir / "notes.txt"
        notes.write_text("no")
        with self.assertRaises(FilmstripUnavailable):
            build_filmstrip(self.settings, "videos", "notes.txt")
        empty = self.settings.videos_dir / "empty.mp4"
        empty.write_bytes(b"")
        with self.assertRaises(FilmstripUnavailable):
            build_filmstrip(self.settings, "videos", "empty.mp4")
        with self.assertRaises(FilmstripUnavailable):
            build_filmstrip(self.settings, "videos", "../../etc/passwd")

    def test_probe_failure_is_reported_and_nothing_is_cached(self) -> None:
        # ffprobe stub without exec permission behaves like a missing binary.
        self.ffprobe.chmod(0o644)
        with self.assertRaises(FilmstripUnavailable):
            build_filmstrip(self.settings, "videos", "holiday.mp4")
        self.assertEqual([], list((self.settings.config_dir / "filmstrips").glob("*")))

    def test_uploads_root_works_too(self) -> None:
        movie = self.settings.uploads_dir / "phone.mov"
        movie.parent.mkdir(parents=True)
        movie.write_bytes(b"uploaded-movie")
        sprite = build_filmstrip(self.settings, "uploads", "phone.mov")
        self.assertTrue(sprite.exists())


if __name__ == "__main__":
    unittest.main()
