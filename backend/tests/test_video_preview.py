"""The browser-facing movie proxy must be whole, and must look like the render.

Two defects met in this one cache. It was trusted by `size > 1024`, which a
half-written file satisfies — and once trusted it is handed to every request for that
source forever, so the user watches the same broken clip through any number of
updates, with nothing in the app able to make it go away. And it was transcoded with
no colour rule, so a full-range source stayed full-range while the slideshow render
is limited/bt709: the clip being scrubbed and the file being downloaded did not
describe their pixels the same way.
"""
from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import video_preview
from app.config import Settings
from app.video_preview import CACHE_VERSION, _cache_key, build_video_preview


class VideoPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.ffmpeg = base / "ffmpeg-stub"
        # A stub that writes a plausible MP4 (a header and an index) wherever it is
        # told to put output, and records nothing: what this file asserts is the
        # command, which the fake below captures.
        self.ffmpeg.write_text("#!/usr/bin/env python3\nimport pathlib, sys\n"
                               "out = pathlib.Path(sys.argv[-1])\n"
                               "if out.suffix:\n"
                               "    out.parent.mkdir(parents=True, exist_ok=True)\n"
                               "    out.write_bytes(b'ftypisom' + b'moov' + bytes(4096))\n")
        self.ffmpeg.chmod(self.ffmpeg.stat().st_mode | stat.S_IEXEC)
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output",
                                 ffprobe_bin=str(self.ffmpeg), ffmpeg_bin=str(self.ffmpeg))
        self.movie = base / "videos" / "holiday.mts"
        self.movie.write_bytes(b"MPEG-TS" + bytes(4096))
        self.cache = base / "config" / "video-previews"

    def tearDown(self) -> None:
        self.temp.cleanup()

    @property
    def cached(self) -> Path:
        info = self.movie.stat()
        return self.cache / f"{_cache_key('videos', 'holiday.mts', info.st_size, info.st_mtime_ns, 640)}.mp4"

    def build(self, *, whole: bool) -> list[list[str]]:
        """One call to the builder, returning every FFmpeg command it issued."""
        commands: list[list[str]] = []

        def fake_run(command, *args, **kwargs):
            commands.append(list(command))
            target = Path(command[-1])
            if target.suffix:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"ftypisom" + b"moov" + bytes(4096))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(video_preview.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(video_preview, "verified_whole", return_value=whole):
            self.path = build_video_preview(self.settings, "videos", "holiday.mts")
        return commands

    def renders(self, commands: list[list[str]]) -> list[list[str]]:
        return [command for command in commands if str(self.movie) in command]

    def test_the_proxy_carries_the_render_s_colour_description(self) -> None:
        render = self.renders(self.build(whole=False))[0]
        graph = render[render.index("-vf") + 1]
        self.assertIn("out_range=limited", graph, "a full-range source must arrive limited, like the timeline")
        self.assertIn("out_color_matrix=bt709", graph)
        self.assertIn("-color_range", render)
        self.assertEqual("tv", render[render.index("-color_range") + 1])
        self.assertEqual("bt709", render[render.index("-colorspace") + 1])

    def test_a_cached_proxy_that_does_not_decode_is_rebuilt(self) -> None:
        """The old check was `st_size > 1024`, which a torn write passes.

        So this plants a file of exactly that kind — real bulk, no index — and insists
        the builder looks at the bytes rather than at the directory entry.
        """
        self.cache.mkdir(parents=True, exist_ok=True)
        torn = b"mdat" + bytes(4096)
        self.cached.write_bytes(torn)
        commands = self.build(whole=False)
        self.assertEqual(1, len(self.renders(commands)), "the cached clip failed the probe, so it must be redone")
        self.assertNotEqual(torn, self.cached.read_bytes())

    def test_a_whole_cached_proxy_is_served_without_running_ffmpeg(self) -> None:
        self.build(whole=False)
        self.assertTrue(self.cached.exists())
        self.assertEqual([], self.renders(self.build(whole=True)),
                         "a verified entry is a return, not a transcode")

    def test_an_interrupted_transcode_leaves_nothing_behind(self) -> None:
        self.cache.mkdir(parents=True, exist_ok=True)
        stale = self.cache / "deadbeef.mp4.part"
        stale.write_bytes(b"half a clip")
        self.build(whole=False)
        self.assertFalse(stale.exists(), "the .part of a killed FFmpeg must not pile up on the volume")

    def test_the_cache_key_announces_the_generation(self) -> None:
        self.assertEqual("v2", CACHE_VERSION,
                         "the colour rule arrived in v2; any further change to the command must bump it again")
        info = self.movie.stat()
        same = _cache_key("videos", "holiday.mts", info.st_size, info.st_mtime_ns, 640)
        self.assertEqual(same, _cache_key("videos", "holiday.mts", info.st_size, info.st_mtime_ns, 640))
        self.assertNotEqual(same, _cache_key("videos", "holiday.mts", info.st_size + 1, info.st_mtime_ns, 640),
                            "a re-uploaded file is a different clip")
        self.assertNotEqual(same, _cache_key("videos", "holiday.mts", info.st_size, info.st_mtime_ns, 960))


if __name__ == "__main__":
    unittest.main()
