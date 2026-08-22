from __future__ import annotations

import logging
import re
import subprocess
import threading
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
    _probe_readable,
    _summarize_ffmpeg_log,
    KEN_BURNS_MAX_ZOOM,
    COMPOSE_BATCH_SIZE,
    build_filter_graph,
    chunk_indices,
    fill_frame_filter,
    fit_frame_filter,
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

# The complete set of built-in xfade transitions in FFmpeg (libavfilter/vf_xfade.c),
# excluding "custom" (which needs an expression, not a preset name). Every UI
# label in renderer.XFADE must map to exactly one of these.
FFMPEG_XFADE_TRANSITIONS = [
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite", "radial",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose", "horzopen", "horzclose",
    "dissolve", "pixelize", "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice", "hblur", "fadegrays",
    "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev", "zoomin",
    "fadefast", "fadeslow", "hlwind", "hrwind", "vuwind", "vdwind",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
]


class RendererMappingTest(unittest.TestCase):
    def test_ui_transition_names_map_to_ffmpeg(self) -> None:
        self.assertEqual("circlecrop", xfade_name("Circle crop"))
        self.assertEqual("smoothleft", xfade_name("Smooth left"))
        self.assertEqual("dissolve", xfade_name("GLSL · Dreamy"))
        self.assertEqual("fade", xfade_name("Unknown future transition"))

    def test_catalogue_is_complete_and_exact(self) -> None:
        # renderer.XFADE must cover the full FFmpeg xfade catalogue with no
        # typos, no omissions and no invented names (which would crash FFmpeg
        # at render time). See libavfilter/vf_xfade.c.
        from app.renderer import XFADE
        mapped = list(XFADE.values())
        self.assertEqual(sorted(FFMPEG_XFADE_TRANSITIONS), sorted(set(mapped)))
        self.assertEqual(len(mapped), len(set(mapped)), "duplicate FFmpeg transition names in XFADE")

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


class CapabilitiesHealthTest(unittest.TestCase):
    """Regression tests for container "unhealthy" false positives.

    /api/health (polled by the UI) must answer instantly even while the
    one-time Quick Sync probe is still running or a render saturates the
    CPUs — the old implementation spawned `ffmpeg -version` per call and
    blocked on the QSV lock, which made Docker/Portainer flag busy but
    healthy containers as unhealthy."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.renderer = Renderer(Database(base / "test.db"), Settings())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_ffmpeg_version_probed_at_most_once(self) -> None:
        with mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("app.renderer.subprocess.run", return_value=mock.Mock(stdout="ffmpeg version 7.1 test\n")) as run:
            self.assertEqual("ffmpeg version 7.1 test", self.renderer.ffmpeg_version())
            self.assertEqual("ffmpeg version 7.1 test", self.renderer.ffmpeg_version())
            self.assertEqual(1, run.call_count)

    def test_capabilities_spawns_no_subprocess_once_warm(self) -> None:
        self.renderer._ffmpeg_version = "ffmpeg version 7.1 test"
        self.renderer._version_probed = True
        self.renderer._qsv_encodable = True
        with mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffmpeg"), \
             mock.patch("app.renderer.subprocess.run", side_effect=AssertionError("capabilities must not spawn processes")):
            caps = self.renderer.capabilities()
        self.assertEqual("ffmpeg version 7.1 test", caps["ffmpegVersion"])
        self.assertTrue(caps["ffmpeg"])
        self.assertTrue(caps["cpuEncoding"])
        self.assertTrue(caps["quickSync"])

    def test_capabilities_does_not_wait_for_qsv_probe(self) -> None:
        self.renderer._ffmpeg_version = "ffmpeg version 7.1 test"
        self.renderer._version_probed = True
        # Simulate the (up to 30 s) Quick Sync test encode holding its lock;
        # capabilities() must answer anyway instead of blocking a health check.
        held = threading.Event()
        release = threading.Event()
        def hold_lock() -> None:
            with self.renderer._qsv_lock:
                held.set()
                release.wait(10)
        keeper = threading.Thread(target=hold_lock, daemon=True)
        keeper.start()
        self.assertTrue(held.wait(5), "test could not acquire the QSV lock scenario")
        try:
            caps = self.renderer.capabilities()
        finally:
            release.set()
            keeper.join(5)
        self.assertFalse(caps["quickSync"], "unprobed Quick Sync must report False, never block")

    def test_warm_capabilities_never_raises(self) -> None:
        with mock.patch("app.renderer.shutil.which", return_value=None), \
             mock.patch("app.renderer.subprocess.run", side_effect=OSError("probe exploded")):
            self.renderer.warm_capabilities()  # daemon thread swallows and logs
        # Blocking paths still degrade safely for render-time decisions.
        self.assertFalse(self.renderer.qsv_encodable_cached())


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

    def test_missing_transition_time_defaults_to_five_seconds(self) -> None:
        # The UI default transition is 5 s; legacy items without an explicit
        # transitionTime must render with the same default the UI shows.
        self.assertEqual([5.0], Renderer.effective_transitions([{"duration": 5}, {"duration": 5}]))
        self.assertEqual([5.0, 5.0], Renderer.effective_transitions([
            {"duration": 5}, {"duration": 5}, {"duration": 5},
        ]))


class ProbeReadableTest(unittest.TestCase):
    """Retry behaviour for 0-byte (cloud-hydrating) files and slow mounts."""

    class FakePath:
        def __init__(self, sizes, is_file=True):
            self._sizes = list(sizes)
            self.is_file_ = is_file
            self.stat_calls = 0

        def exists(self): return True

        def is_dir(self): return False

        def is_file(self): return self.is_file_

        def stat(self):
            size = self._sizes[min(self.stat_calls, len(self._sizes) - 1)]
            self.stat_calls += 1
            return type("Stat", (), {"st_size": size})()

    def _probe(self, path, run_side_effect=None, run_side_effect_once=False, retries=2, delay=0.005):
        with mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffprobe"), \
             mock.patch("app.renderer.subprocess.run", side_effect=run_side_effect) as run, \
             mock.patch("app.renderer.time.sleep") as sleep:
            result = _probe_readable(path, "ffprobe", timeout=5, retries=retries, retry_delay=delay)
        return result, run, sleep

    def test_empty_file_is_rechecked_before_failing(self) -> None:
        # First stat reports 0 bytes (e.g. still hydrating), second one finds data.
        path = self.FakePath([0, 1024])
        ok = type("Result", (), {"returncode": 0, "stderr": "", "stdout": "jpeg"})()
        result, run, sleep = self._probe(path, run_side_effect=[ok])
        self.assertIsNone(result)
        self.assertEqual(2, path.stat_calls)
        self.assertEqual(1, sleep.call_count, "empty file should be retried after a delay")

    def test_persistently_empty_file_is_rejected(self) -> None:
        path = self.FakePath([0, 0, 0])
        result, run, sleep = self._probe(path)
        self.assertEqual("file is empty (0 bytes)", result)
        self.assertEqual(3, path.stat_calls)
        self.assertEqual(2, sleep.call_count)
        run.assert_not_called()  # no point probing a file with no content

    def test_ffprobe_timeout_is_retried_once(self) -> None:
        ok = type("Result", (), {"returncode": 0, "stderr": "", "stdout": "mp3"})()
        path = self.FakePath([2048])
        with mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffprobe"), \
             mock.patch("app.renderer.time.sleep"), \
             mock.patch("app.renderer.subprocess.run", side_effect=[subprocess.TimeoutExpired("ffprobe", 5), ok]) as run:
            result = _probe_readable(path, "ffprobe", timeout=5, retries=0, retry_delay=0)
        self.assertIsNone(result)
        self.assertEqual(2, run.call_count)

    def test_ffprobe_timeout_after_retry_is_reported(self) -> None:
        path = self.FakePath([2048])
        with mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffprobe"), \
             mock.patch("app.renderer.time.sleep"), \
             mock.patch("app.renderer.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 5)) as run:
            result = _probe_readable(path, "ffprobe", timeout=5, retries=0, retry_delay=0)
        self.assertEqual("ffprobe timed out", result)
        self.assertEqual(2, run.call_count)

    def test_missing_and_folder_paths_fail_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch("app.renderer.time.sleep"):
            self.assertEqual("file is missing", _probe_readable(Path("/definitely/not/here.jpg"), "ffprobe", retries=2, retry_delay=0))
            self.assertEqual("is a folder, not a media file", _probe_readable(Path(tmp), "ffprobe", retries=2, retry_delay=0))


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

    def test_render_output_ui_path_is_project_facing(self) -> None:
        saved = self.db.save_project(self.project_payload())
        # The UI talks in /output terms even when OUTPUT_DIR is a NAS mount.
        self.assertEqual("/output/movie.mp4", self.renderer.render_output_ui_path(self.db.get_project(saved["id"])))

    def test_existing_output_requires_overwrite_acknowledgement(self) -> None:
        saved = self.db.save_project(self.project_payload())
        target = self.settings.output_dir / "movie.mp4"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"previous render")
        with self.assertRaises(OutputExistsError) as ctx:
            self.renderer.submit(saved["id"], "render")
        # The prompt must echo the user-facing path, not the host mount path.
        self.assertEqual("/output/movie.mp4", str(ctx.exception))
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
        self.assertIn("photo1.jpg — file is empty (0 bytes)", message)
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
        self.assertIn("gone.jpg — file is missing", message)
        self.assertIn("soundtrack 'We Are The Champions.mp3' — file is empty (0 bytes)", message)
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
        self.assertIn("broken.jpg — Invalid data found when processing input", str(ctx.exception))

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


class ComposeBatchingTest(unittest.TestCase):
    def test_small_stories_are_a_single_window(self) -> None:
        self.assertEqual([(0, 3)], chunk_indices(3, 8))
        self.assertEqual([(0, 8)], chunk_indices(8, 8))

    def test_avoids_a_leftover_singleton(self) -> None:
        self.assertEqual([(0, 7), (7, 9)], chunk_indices(9, 8))

    def test_89_clips_never_open_more_than_the_batch(self) -> None:
        windows = chunk_indices(89, COMPOSE_BATCH_SIZE)
        self.assertGreater(len(windows), 1)
        for start, end in windows:
            self.assertLessEqual(end - start, COMPOSE_BATCH_SIZE)
            self.assertGreaterEqual(end - start, 2)


class FrameFittingTest(unittest.TestCase):
    """Pictures must be shown whole: scaled down to fit, never cropped."""

    def test_images_are_fitted_not_cropped(self) -> None:
        graph = fit_frame_filter(1920, 1080, 30)
        self.assertIn("scale=1920:1080:force_original_aspect_ratio=decrease", graph)
        self.assertNotIn("force_original_aspect_ratio=increase,crop=1920:1080", graph)
        self.assertIn("overlay=(W-w)/2:(H-h)/2", graph)
        self.assertIn("gblur", graph, "letterbox bars are filled with a blurred copy")
        self.assertTrue(graph.rstrip().endswith("fps=30"))

    def test_ken_burns_headroom_keeps_the_zoom_inside_the_picture(self) -> None:
        graph = fit_frame_filter(1920, 1080, 30, KEN_BURNS_MAX_ZOOM)
        inner_w = int(1920 / KEN_BURNS_MAX_ZOOM) // 2 * 2
        inner_h = int(1080 / KEN_BURNS_MAX_ZOOM) // 2 * 2
        self.assertIn(f"scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease", graph)
        # Even fully zoomed the visible area stays within the fitted picture.
        self.assertLessEqual(inner_w * KEN_BURNS_MAX_ZOOM, 1920 + 2)
        self.assertLessEqual(inner_h * KEN_BURNS_MAX_ZOOM, 1080 + 2)

    def test_dimensions_stay_even_for_yuv420p(self) -> None:
        for width, height in ((1920, 1080), (3840, 2160), (1280, 720), (854, 480)):
            graph = fit_frame_filter(width, height, 25, KEN_BURNS_MAX_ZOOM)
            sizes = re.findall(r"scale=(\d+):(\d+)", graph)
            self.assertTrue(sizes)
            for w, h in sizes:
                self.assertEqual(0, int(w) % 2, graph)
                self.assertEqual(0, int(h) % 2, graph)

    def test_videos_and_title_frames_still_fill_the_frame(self) -> None:
        graph = fill_frame_filter(1920, 1080, 30)
        self.assertIn("force_original_aspect_ratio=increase", graph)
        self.assertIn("crop=1920:1080", graph)
        self.assertNotIn("overlay", graph)


class SegmentFilterSelectionTest(unittest.TestCase):
    """The render loop must pick fit-vs-fill per media type."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.settings = Settings(config_dir=base / "config", photos_dir=base / "photos", videos_dir=base / "videos", output_dir=base / "out", music_dir=base / "music")
        for directory in (self.settings.photos_dir, self.settings.videos_dir, self.settings.work_dir, self.settings.preview_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (self.settings.photos_dir / "a.jpg").write_bytes(b"x" * 64)
        (self.settings.videos_dir / "a.mp4").write_bytes(b"x" * 64)
        self.renderer = Renderer(Database(base / "fit.db"), self.settings)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _segment_filters(self, media: list[dict]) -> list[str]:
        project = {"id": 1, "media": media, "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps", "bitrate": "8 Mbps", "encoder": "libx264", "path": "/output", "filename": "movie"}}
        commands: list[list[str]] = []

        def fake_run(command, cancelled, log_file):
            commands.append(list(command))
            Path(command[-1]).write_bytes(b"segment")

        with mock.patch.object(self.renderer, "_validate_media", return_value=None), \
             mock.patch.object(self.renderer, "_run_ffmpeg", side_effect=fake_run), \
             mock.patch.object(self.renderer, "_make_soundtrack", return_value=None), \
             mock.patch.object(self.renderer, "_probe_duration", return_value=2.0):
            work = self.settings.work_dir / "job"
            work.mkdir(parents=True, exist_ok=True)
            self.renderer.render(project, "render", work, threading.Event(), lambda p, s: None)
        return [command[command.index("-vf") + 1] for command in commands if "-vf" in command]

    def test_image_is_fitted_video_is_filled(self) -> None:
        filters = self._segment_filters([
            {"id": 1, "type": "image", "path": "/photos/a.jpg", "duration": 2, "effect": "None", "transition": "Fade", "transitionTime": 0.5},
            {"id": 2, "type": "video", "path": "/videos/a.mp4", "duration": 2, "effect": "Original motion", "transition": "Fade", "transitionTime": 0.5},
        ])
        self.assertEqual(2, len(filters))
        self.assertIn("force_original_aspect_ratio=decrease", filters[0])
        self.assertNotIn("crop=1920:1080", filters[0])
        self.assertIn("crop=1920:1080", filters[1])
        self.assertNotIn("force_original_aspect_ratio=decrease", filters[1])

    def test_ken_burns_zoom_is_centred_and_bounded(self) -> None:
        filters = self._segment_filters([
            {"id": 1, "type": "image", "path": "/photos/a.jpg", "duration": 3, "effect": "Ken Burns · Zoom in", "transition": "Fade", "transitionTime": 0.5},
        ])
        self.assertIn("zoompan=", filters[0])
        self.assertIn("x='iw/2-(iw/zoom/2)'", filters[0], "zoompan defaults to the top-left corner")
        self.assertIn("y='ih/2-(ih/zoom/2)'", filters[0])
        self.assertIn(f"scale={int(1920 / KEN_BURNS_MAX_ZOOM) // 2 * 2}:", filters[0])


class VideoPlaysToEndTest(unittest.TestCase):
    """A video clip must finish its complete movie before the next transition.

    Previously every video was opened with `-stream_loop -1 -t <clip>`, which
    either cut a long movie short or restarted it mid-hold. The renderer now
    probes the native duration, expands the hold to cover it, plays the file
    once, and freezes first/last frames only for the xfade handles.
    """

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.settings = Settings(
            config_dir=base / "config",
            photos_dir=base / "photos",
            videos_dir=base / "videos",
            output_dir=base / "out",
            music_dir=base / "music",
        )
        for directory in (
            self.settings.photos_dir, self.settings.videos_dir,
            self.settings.work_dir, self.settings.preview_dir, self.settings.output_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        (self.settings.photos_dir / "still.jpg").write_bytes(b"x" * 64)
        (self.settings.videos_dir / "movie.mp4").write_bytes(b"x" * 64)
        self.renderer = Renderer(Database(base / "video.db"), self.settings)
        self.commands: list[list[str]] = []

    def tearDown(self) -> None:
        self.renderer.pool.shutdown(wait=False, cancel_futures=True)
        self.temp.cleanup()

    def _render(self, media: list[dict], native_video: float = 12.5) -> list[list[str]]:
        project = {
            "id": 1,
            "media": media,
            "output": {
                "resolution": "Full HD · 1080p", "frameRate": "30 fps",
                "bitrate": "8 Mbps", "encoder": "libx264",
                "path": "/output", "filename": "movie",
            },
        }

        def fake_run(command, cancelled, log_file):
            self.commands.append(list(command))
            Path(command[-1]).write_bytes(b"segment")

        def fake_probe(path):
            name = Path(path).name
            if name.endswith(".mp4") and "segment" not in name and "movie.mp4" in str(path):
                return native_video
            if name == "movie.mp4":
                return native_video
            return 1.0

        with mock.patch.object(self.renderer, "_validate_media", return_value=None), \
             mock.patch.object(self.renderer, "_run_ffmpeg", side_effect=fake_run), \
             mock.patch.object(self.renderer, "_make_soundtrack", return_value=None), \
             mock.patch.object(self.renderer, "_probe_duration", side_effect=fake_probe):
            work = self.settings.work_dir / "job"
            work.mkdir(parents=True, exist_ok=True)
            self.renderer.render(project, "render", work, threading.Event(), lambda p, s: None)
        return self.commands

    def test_video_hold_expands_to_native_duration(self) -> None:
        # UI default for a video used to be 10 s; a 42 s movie must still play fully.
        commands = self._render([
            {"id": 1, "type": "video", "path": "/videos/movie.mp4", "name": "movie.mp4",
             "duration": 10, "effect": "Original motion", "transition": "Fade", "transitionTime": 1},
            {"id": 2, "type": "image", "path": "/photos/still.jpg", "name": "still.jpg",
             "duration": 5, "effect": "None", "transition": "Fade", "transitionTime": 1},
        ], native_video=42.0)
        video_cmd = next(c for c in commands if any(str(a).endswith("movie.mp4") for a in c))
        # Must NOT loop the source — that would restart the movie mid-hold.
        self.assertNotIn("-stream_loop", video_cmd)
        # Segment length = native 42 + lead-in 0 + lead-out 1 (outgoing transition).
        t_flag = video_cmd[video_cmd.index("-t") + 1]
        self.assertEqual("43", t_flag)
        vf = video_cmd[video_cmd.index("-vf") + 1]
        self.assertIn("tpad=", vf)
        self.assertIn("stop_duration=1", vf)

    def test_video_between_photos_gets_both_transition_pads(self) -> None:
        commands = self._render([
            {"id": 1, "type": "image", "path": "/photos/still.jpg", "name": "still.jpg",
             "duration": 5, "effect": "None", "transition": "Fade", "transitionTime": 2},
            {"id": 2, "type": "video", "path": "/videos/movie.mp4", "name": "movie.mp4",
             "duration": 3, "effect": "Original motion", "transition": "Dissolve", "transitionTime": 1.5},
            {"id": 3, "type": "image", "path": "/photos/still.jpg", "name": "still.jpg",
             "duration": 5, "effect": "None", "transition": "Fade", "transitionTime": 1},
        ], native_video=8.0)
        video_cmd = next(c for c in commands if any(str(a).endswith("movie.mp4") for a in c))
        self.assertNotIn("-stream_loop", video_cmd)
        # hold = max(3, 8) = 8; segment = 2 (in) + 8 + 1.5 (out) = 11.5
        self.assertEqual("11.5", video_cmd[video_cmd.index("-t") + 1])
        vf = video_cmd[video_cmd.index("-vf") + 1]
        self.assertIn("start_duration=2", vf)
        self.assertIn("stop_duration=1.5", vf)

    def test_image_still_loops_as_before(self) -> None:
        commands = self._render([
            {"id": 1, "type": "image", "path": "/photos/still.jpg", "name": "still.jpg",
             "duration": 5, "effect": "None", "transition": "Fade", "transitionTime": 1},
        ])
        image_cmd = next(c for c in commands if any(str(a).endswith("still.jpg") for a in c))
        self.assertIn("-loop", image_cmd)

    def test_long_story_composes_in_batches(self) -> None:
        photos = []
        for i in range(20):
            name = f"p{i:02d}.jpg"
            (self.settings.photos_dir / name).write_bytes(b"x" * 64)
            photos.append({
                "id": i, "type": "image", "path": f"/photos/{name}", "name": name,
                "duration": 2, "effect": "None", "transition": "Fade", "transitionTime": 0.5,
            })
        self._render(photos)
        compose = [c for c in self.commands if "-filter_complex" in c]
        self.assertGreaterEqual(len(compose), 2, compose)
        for command in compose:
            inputs = sum(1 for a, b in zip(command, command[1:]) if a == "-i")
            self.assertLessEqual(inputs, COMPOSE_BATCH_SIZE + 1)


if __name__ == "__main__":
    unittest.main()
