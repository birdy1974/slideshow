from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.config import Settings
from app.renderer import OutputExistsError, Renderer, _parse_xfade_help, parse_number, xfade_name

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
    def test_effective_transitions_clamp_to_remaining_clip_time(self) -> None:
        media = [
            {"duration": 2, "transitionTime": 3},   # oversized: limited to min(1.95, next 0.95)
            {"duration": 1, "transitionTime": .5},  # fits both sides
            {"duration": 5, "transitionTime": 1},   # last item's transition is never used
        ]
        transitions = Renderer.effective_transitions(media)
        self.assertEqual([0.95, 0.5], transitions)
        durations = [2, 1, 5]
        self.assertEqual(2 + 1 - 0.95 + 5 - 0.5, sum(durations) - sum(transitions))

    def test_effective_transitions_cannot_exceed_cumulative_time(self) -> None:
        # A chain of minimum-length clips with maximum transitions must still
        # yield a positive total and never a negative remaining time.
        media = [
            {"duration": .2, "transitionTime": 5},
            {"duration": .2, "transitionTime": 5},
            {"duration": .2, "transitionTime": 5},
        ]
        transitions = Renderer.effective_transitions(media)
        cumulative = .2
        for transition, item in zip(transitions, media[:-1]):
            self.assertGreaterEqual(transition, .05)
            self.assertLessEqual(transition, cumulative - .05)
            self.assertAlmostEqual(transition, .15)
            cumulative += .2 - transition
        self.assertGreater(cumulative, 0)

    def test_empty_and_single_item_media(self) -> None:
        self.assertEqual([], Renderer.effective_transitions([]))
        self.assertEqual([], Renderer.effective_transitions([{"duration": 5, "transitionTime": 99}]))

    def test_last_clip_is_fully_included_in_total(self) -> None:
        # The composed length must keep the final clip's full duration (minus
        # only its incoming transition) — the last frame is never dropped.
        media = [
            {"duration": 5, "transitionTime": 1},
            {"duration": 5, "transitionTime": 1},
            {"duration": 7, "transitionTime": 1},
        ]
        transitions = Renderer.effective_transitions(media)
        self.assertEqual([1, 1], transitions)
        durations = [5, 5, 7]
        self.assertEqual(sum(durations) - sum(transitions), 15)  # 5+5+7-1-1
        # The final clip contributes its whole 7s minus the 1s overlap of the
        # transition that leads into it.
        self.assertEqual(7 - 1, durations[-1] - transitions[-1])


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


if __name__ == "__main__":
    unittest.main()
