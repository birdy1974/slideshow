from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.database import Database
from app.config import Settings
from app.renderer import (
    OutputExistsError,
    RenderError,
    Renderer,
    _parse_xfade_help,
    _summarize_ffmpeg_log,
    build_filter_graph,
    format_ffmpeg_number,
    parse_number,
    xfade_name,
)

# Shape of `ffmpeg -h filter=xfade` on an FFmpeg 5.x NAS build: the catalogue
# stops at fadeslow; hlwind/hrwind/vuwind/vdwind and cover*/reveal* are absent.
FFMPEG5_XFADE_HELP = """
xfade AVOptions:
   transition        <int>        ..FV.....T. set cross fade transition (from -1 to 45) (default fade)
     custom          -1           ..FV.....T. custom transition
     fade            0            ..FV.....T. fade transition
     dissolve        25           ..FV.....T. dissolve transition
     wipeleft        1            ..FV.....T. wipe left transition
     fadegrays       36           ..FV.....T. fadegrays transition
     fadefast        44           ..FV.....T. fast fade transition
     fadeslow        45           ..FV.....T. slow fade transition
   duration          <duration>   ..FV.....T. set cross fade duration (default 1)
   offset            <duration>   ..FV.....T. set cross fade offset relative to first input stream (default 0)
"""


class RendererMappingTest(unittest.TestCase):
    def test_ui_transition_names_map_to_ffmpeg(self) -> None:
        self.assertEqual("circlecrop", xfade_name("Circle crop"))
        self.assertEqual("smoothleft", xfade_name("Smooth left"))
        self.assertEqual("dissolve", xfade_name("GLSL · Dreamy"))
        self.assertEqual("fade", xfade_name("Unknown future transition"))

    def test_preset_numbers_are_parsed(self) -> None:
        self.assertEqual(30, parse_number("30 fps", 25))
        self.assertEqual(8, parse_number("8 Mbps · High", 4))

    def test_xfade_help_parsing(self) -> None:
        names = _parse_xfade_help(FFMPEG5_XFADE_HELP)
        self.assertIn("fade", names)
        self.assertIn("fadeslow", names)
        self.assertNotIn("hrwind", names)  # FFmpeg 5.x lacks wind transitions
        self.assertNotIn("custom", names)  # custom needs an expression; never mapped
        self.assertEqual(set(), _parse_xfade_help("no options here"))


class RendererFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.renderer = Renderer(Database(base / "test.db"), Settings())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_unsupported_transitions_degrade_to_dissolve(self) -> None:
        self.renderer._xfade_supported = _parse_xfade_help(FFMPEG5_XFADE_HELP)
        self.assertEqual("dissolve", self.renderer.resolve_xfade("Horizontal right wind"))
        self.assertEqual("dissolve", self.renderer.resolve_xfade("Cover left"))
        self.assertEqual("dissolve", self.renderer.resolve_xfade("GLSL · Dreamy"))
        self.assertEqual("fade", self.renderer.resolve_xfade("Fade"))
        self.assertEqual("wipeleft", self.renderer.resolve_xfade("Wipe left"))

    def test_failed_probe_keeps_full_catalogue(self) -> None:
        self.renderer._xfade_supported = set()
        self.assertEqual("hrwind", self.renderer.resolve_xfade("Horizontal right wind"))


class TransitionTimingTest(unittest.TestCase):
    def test_transitions_are_additional_timeline_time(self) -> None:
        media = [
            {"duration": 5, "transitionTime": 3},
            {"duration": 5, "transitionTime": 3},
            {"duration": 5, "transitionTime": 3},
            {"duration": 5, "transitionTime": 3},
        ]
        transitions = Renderer.effective_transitions(media)
        self.assertEqual([3, 3, 3], transitions)
        self.assertEqual(29, sum(item["duration"] for item in media) + sum(transitions))

    def test_transition_has_xfade_minimum_but_is_not_limited_by_clip_length(self) -> None:
        self.assertEqual([3], Renderer.effective_transitions([
            {"duration": .2, "transitionTime": 3}, {"duration": .2, "transitionTime": 3},
        ]))
        self.assertEqual([.05], Renderer.effective_transitions([
            {"duration": 1, "transitionTime": 0}, {"duration": 1},
        ]))

    def test_empty_and_single_item_media(self) -> None:
        self.assertEqual([], Renderer.effective_transitions([]))
        self.assertEqual([], Renderer.effective_transitions([{"duration": 5, "transitionTime": 99}]))


class OutputProtectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.settings = Settings(base / "config", base / "photos", base / "videos", base / "music", base / "output")
        self.db = Database(base / "slideshow.db")
        self.db.initialize()
        self.renderer = Renderer(self.db, self.settings)
        # Queued background renders fail without FFmpeg; keep their tracebacks out of the test output.
        logging.disable(logging.CRITICAL)

    def tearDown(self) -> None:
        # Wait while logging is still disabled so background job tracebacks stay quiet.
        self.renderer.pool.shutdown(wait=True, cancel_futures=True)
        logging.disable(logging.NOTSET)
        self.temp.cleanup()

    def project_payload(self) -> dict:
        return {
            "schemaVersion": 1,
            "project": {"name": "Overwrite", "randomOrder": False},
            "media": [],
            "soundtrack": {"tracks": []},
            "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps", "bitrate": "8 Mbps · High",
                       "encoder": "Auto · Quick Sync", "path": "/output", "filename": "movie.mp4"},
        }

    def test_render_output_path_resolves_to_output_mount(self) -> None:
        saved = self.db.save_project(self.project_payload())
        self.assertEqual(self.settings.output_dir / "movie.mp4", self.renderer.render_output_path(self.db.get_project(saved["id"])))

    def test_existing_output_requires_overwrite_acknowledgement(self) -> None:
        saved = self.db.save_project(self.project_payload())
        target = self.settings.output_dir / "movie.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"previous render")
        with self.assertRaises(OutputExistsError):
            self.renderer.submit(saved["id"], "render")
        # Nothing was queued while the user has not acknowledged the overwrite.
        self.assertEqual([], self.db.list_jobs(saved["id"]))

    def test_overwrite_acknowledged_queues_the_render(self) -> None:
        saved = self.db.save_project(self.project_payload())
        target = self.settings.output_dir / "movie.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"previous render")
        job = self.renderer.submit(saved["id"], "render", overwrite=True)
        self.assertEqual("queued", job["status"])
        self.assertEqual(1, len(self.db.list_jobs(saved["id"])))

    def test_missing_output_file_does_not_block(self) -> None:
        saved = self.db.save_project(self.project_payload())
        job = self.renderer.submit(saved["id"], "render")
        self.assertEqual("queued", job["status"])


class MediaValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output"):
            (base / name).mkdir()
        self.photos = base / "photos"
        self.music = base / "music"
        self.settings = Settings(base / "config", self.photos, base / "videos", self.music, base / "output")
        self.renderer = Renderer(Database(base / "test.db"), self.settings)

    def tearDown(self) -> None:
        self.renderer.pool.shutdown(wait=False, cancel_futures=True)
        self.temp.cleanup()

    def test_empty_file_fails_fast_with_actionable_message(self) -> None:
        folder = self.photos / "_schilderij" / "universe"
        folder.mkdir(parents=True)
        (folder / "photo1.jpg").write_bytes(b"")
        project = {
            "media": [{"name": "photo1.jpg", "path": "/photos/_schilderij/universe", "type": "image"}],
            "soundtrack": {"tracks": []},
        }
        with self.assertRaises(RenderError) as ctx:
            self.renderer._validate_media(project)
        message = str(ctx.exception)
        self.assertIn("Cannot render — these media files could not be read", message)
        self.assertIn("photo1.jpg: file is empty (0 bytes)", message)
        self.assertIn("/photos/_schilderij/universe/photo1.jpg", message)
        self.assertIn("Remove or replace them, then try again.", message)

    def test_reports_every_unreadable_input_in_one_message(self) -> None:
        (self.music / "We Are The Champions.mp3").write_bytes(b"")
        project = {
            "media": [
                {"name": "gone.jpg", "path": "/photos/_schilderij/universe", "type": "image"},
                {"name": "Title card", "path": "Generated frame", "type": "title"},
            ],
            "soundtrack": {"tracks": [{"name": "We Are The Champions.mp3", "path": "/music"}]},
        }
        with self.assertRaises(RenderError) as ctx:
            self.renderer._validate_media(project)
        message = str(ctx.exception)
        self.assertIn("gone.jpg: file is missing", message)
        self.assertIn("soundtrack 'We Are The Champions.mp3': file is empty (0 bytes)", message)
        self.assertNotIn("Title card", message)

    def test_ffprobe_reason_included_and_absent_ffprobe_degrades(self) -> None:
        junk = self.photos / "broken.jpg"
        junk.write_bytes(b"not-a-real-image")
        project = {
            "media": [{"name": "broken.jpg", "path": "/photos", "type": "image"}],
            "soundtrack": {"tracks": []},
        }
        probe = mock.Mock(returncode=1, stderr=f"{junk}: Invalid data found when processing input\n", stdout="")
        with mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffprobe"), \
             mock.patch("app.renderer.subprocess.run", return_value=probe):
            with self.assertRaises(RenderError) as ctx:
                self.renderer._validate_media(project)
        self.assertIn("broken.jpg: Invalid data found when processing input", str(ctx.exception))

        # No ffprobe: a non-empty file is allowed through (FFmpeg will be the judge).
        blind = Settings(
            self.settings.config_dir, self.settings.photos_dir, self.settings.videos_dir,
            self.settings.music_dir, self.settings.output_dir, "ffmpeg", "ffprobe-not-installed",
        )
        renderer = Renderer(Database(self.settings.config_dir / "blind.db"), blind)
        try:
            renderer._validate_media(project)
        finally:
            renderer.pool.shutdown(wait=False, cancel_futures=True)

    def test_ffmpeg_failure_surfaces_key_error_lines(self) -> None:
        raw = (
            "$ ffmpeg -hide_banner -i broken.jpg\n"
            + ("configuration: --enable-gpl " * 80) + "\n"
            "frame=   12 fps=0.0 q=0.0 size=       0kB\n"
            "[in#0 @ 0x55aa] Error opening input: Invalid data found when processing input\n"
            "Error opening input file broken.jpg.\n"
            "Conversion failed!\n"
        )
        summary = _summarize_ffmpeg_log(raw)
        self.assertIn("Invalid data found when processing input", summary)
        self.assertIn("Conversion failed!", summary)
        self.assertNotIn("$ ffmpeg", summary)
        self.assertNotIn("configuration:", summary)
        self.assertLess(len(summary), 500)


class FilterGraphTest(unittest.TestCase):
    def test_three_equal_clips_use_cumulative_xfade_offsets(self) -> None:
        # Holds are preserved and transitions are added: boundaries are at 5s and 11s.
        graph = build_filter_graph([5, 5, 5], [1, 1], ["fade", "dissolve"])
        self.assertEqual(
            "[0:v]settb=AVTB,setpts=PTS-STARTPTS[s0];"
            "[1:v]settb=AVTB,setpts=PTS-STARTPTS[s1];"
            "[2:v]settb=AVTB,setpts=PTS-STARTPTS[s2];"
            "[s0][s1]xfade=transition=fade:duration=1:offset=5,"
            "settb=AVTB,setpts=PTS-STARTPTS[x1];"
            "[x1][s2]xfade=transition=dissolve:duration=1:offset=11,"
            "settb=AVTB,setpts=PTS-STARTPTS[vout]",
            graph,
        )

    def test_single_clip_resets_timestamps_to_vout(self) -> None:
        self.assertEqual("[0:v]settb=AVTB,setpts=PTS-STARTPTS[vout]", build_filter_graph([5], [], []))

    def test_fps_is_enforced_for_every_xfade_input_and_result(self) -> None:
        graph = build_filter_graph([5, 5, 5], [3, 3], ["fade", "dissolve"], fps=30)
        # fps must follow setpts so it is not clobbered by PTS-STARTPTS.
        normalization = "settb=AVTB,setpts=PTS-STARTPTS,fps=30"
        for index in range(3):
            self.assertIn(f"[{index}:v]{normalization}[s{index}]", graph)
        self.assertIn(
            "[s0][s1]xfade=transition=fade:duration=3:offset=5,"
            f"{normalization}[x1]",
            graph,
        )
        self.assertIn(
            "[x1][s2]xfade=transition=dissolve:duration=3:offset=13,"
            f"{normalization}[vout]",
            graph,
        )

    def test_two_clip_offset_starts_after_first_clip_hold(self) -> None:
        graph = build_filter_graph([5, 7], [1], ["wipeleft"])
        self.assertIn(
            "[s0][s1]xfade=transition=wipeleft:duration=1:offset=5,"
            "settb=AVTB,setpts=PTS-STARTPTS[vout]",
            graph,
        )

    def test_offset_formatting_avoids_float_noise(self) -> None:
        self.assertEqual("0.8", format_ffmpeg_number(0.8000000000000002))
        graph = build_filter_graph([1.1, 1.1], [0.3], ["fade"])
        self.assertIn("offset=1.1", graph)
        self.assertNotRegex(graph, r"1\.0999|1\.100000")


if __name__ == "__main__":
    unittest.main()
