"""`GET /api/diagnose` — the facts a "renders fine, looks wrong" report needs.

The web layer is not testable here (FastAPI is not installed in every environment), so
these assert on `app.diagnostics`, which is where the work lives; the route in
`main.py` is a call. One pin checks that the route exists at all, because a diagnostic
nobody can reach answers nothing.
"""
from __future__ import annotations

import stat
import tempfile
from dataclasses import replace
import unittest
from pathlib import Path

from app.config import Settings
from app.diagnostics import cache_report, diagnose, file_report


def stub_binary(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


class DiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (self.base / name).mkdir()
        self.settings = Settings(self.base / "config", self.base / "photos", self.base / "videos",
                                 self.base / "music", self.base / "output",
                                 ffprobe_bin="/bin/false", ffmpeg_bin=str(self._ffmpeg()))

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _ffmpeg(self, *, decodes: bool = True) -> Path:
        """A fake FFmpeg: prints one stream line, and answers the tail probe.

        The stream line is written the way FFmpeg writes it, because `describe` parses
        real output — a nicer shape here would test the parser against nothing.
        """
        body = (
            "import pathlib, sys\n"
            "args = sys.argv[1:]\n"
            "if 'null' in args:\n"
            f"    sys.exit(0 if {decodes!r} else 1)\n"
            "sys.stderr.write('  Stream #0:0(und): Video: h264 (High) (avc1), "
            "yuv420p(tv, bt709/bt709/bt709, progressive), 640x360 [SAR 1:1 DAR 16:9], 2000 kb/s\\n')\n"
        )
        return stub_binary(self.base / "ffmpeg-stub", body)

    def test_file_report_reads_the_colour_description(self) -> None:
        movie = self.base / "videos" / "holiday.mp4"
        movie.write_bytes(b"ftypisom" + b"moov" + bytes(4096))
        report = file_report(self.settings, movie)
        self.assertTrue(report["readable"])
        self.assertEqual("yuv420p", report["pixFmt"])
        self.assertEqual("tv", report["range"], "the field that makes a photo part poison a movie part")
        self.assertEqual("bt709/bt709/bt709", report["colourTags"])
        self.assertEqual("640x360", report["size"])
        self.assertTrue(report["complete"])

    def test_file_report_calls_a_truncated_file_truncated(self) -> None:
        movie = self.base / "videos" / "broken.mp4"
        movie.write_bytes(b"ftypisom" + b"moov" + bytes(4096))
        broken = replace(self.settings, ffmpeg_bin=str(self._ffmpeg(decodes=False)))
        report = file_report(broken, movie)
        self.assertTrue(report["readable"], "FFmpeg can open it — which is why a player shows garbage instead of an error")
        self.assertFalse(report["complete"], "and the diagnosis is that it does not decode to the end")

    def test_cache_report_counts_a_killed_transcode(self) -> None:
        root = self.settings.config_dir / "video-previews"
        root.mkdir(parents=True)
        (root / "a.mp4").write_bytes(bytes(2048))
        (root / "b.mp4.part").write_bytes(b"half")
        entry = cache_report(self.settings)["video-previews"]
        self.assertEqual(1, entry["files"])
        self.assertEqual(2048, entry["bytes"])
        self.assertEqual(1, entry["partial"], "leftover .part files are the fingerprint of a render that was killed")
        self.assertEqual("movie proxies", entry["label"])

    def test_snapshot_answers_the_five_questions(self) -> None:
        class Renderer:
            def capabilities(self) -> dict[str, object]:
                return {"ffmpegVersion": "6.0", "quickSync": False, "vaapi": False}

        probed = self.base / "videos" / "holiday.mp4"
        probed.write_bytes(b"ftypisom" + b"moov" + bytes(4096))
        snapshot = diagnose(self.settings, Renderer(), {"dir": "/app/fonts", "files": 40}, root="videos", path="holiday.mp4")
        self.assertEqual("untracked", snapshot["revision"], "a build with no CI stamp must say so, not guess")
        self.assertEqual("6.0", snapshot["capabilities"]["ffmpegVersion"])
        self.assertEqual(40, snapshot["fonts"]["files"])
        self.assertIn("output", snapshot["disk"])
        self.assertIn("video-previews", snapshot["caches"])
        self.assertEqual("640x360", snapshot["file"]["size"])

    def test_an_outside_path_is_refused_rather_than_read(self) -> None:
        """The probe must not become a way to read the host."""
        class Renderer:
            def capabilities(self) -> dict[str, object]:
                return {}

        for root, path in (("videos", "../../../etc/passwd"), ("nope", "x.mp4")):
            snapshot = diagnose(self.settings, Renderer(), {}, root=root, path=path)
            self.assertIn("error", snapshot["file"], f"{root}:{path} must be answered with a refusal")

    def test_the_route_exists(self) -> None:
        main = Path(__file__).resolve().parents[1].joinpath("app/main.py").read_text(encoding="utf-8")
        self.assertIn('@app.get("/api/diagnose")', main)
        self.assertIn("diagnose(settings, renderer, fonts_status(settings.fonts_dir)", main)


if __name__ == "__main__":
    unittest.main()
