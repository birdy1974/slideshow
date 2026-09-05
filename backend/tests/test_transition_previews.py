from __future__ import annotations

import json
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path

from app.config import Settings
from app.renderer import Renderer
from app.transition_previews import PreviewUnavailable, TransitionPreviewCache, slugify

# A stand-in for FFmpeg: answers the capability probes the renderer makes and
# fabricates the output file for every render, so the cache logic can be tested
# on a machine without the (huge) real binary. Built from plain lines so no
# backslash escaping can corrupt the generated script.
STUB_LINES = [
    '#!/usr/bin/env python3',
    '"""Test double for FFmpeg: answers capability probes, fabricates outputs."""',
    'import os, pathlib, sys',
    'args = sys.argv[1:]',
    'with open(os.environ["FFMPEG_STUB_LOG"], "a") as handle:',
    '    handle.write(" ".join(args) + chr(10))',
    'if "-version" in args:',
    '    print("ffmpeg version 6.0-stub-slideshow")',
    '    sys.exit(0)',
    'if "-h" in args:',
    '    sys.stdout.write(chr(10).join([',
    '        "Filter xfade",',
    '        "  transition <string>  ..FV..... set cross fade transition",',
    '        "     fade   ..FV....... fade",',
    '        "     dissolve ..FV....... dissolve",',
    '        "  duration <duration>  ..FV..... cross fade duration",',
    '        "  easing <string>   ..FV..... easing function",',
    '        "  reverse <int>     ..FV..... reverse transition",',
    '        "",',
    '    ]))',
    '    sys.exit(0)',
    'out = pathlib.Path(args[-1])',
    'out.parent.mkdir(parents=True, exist_ok=True)',
    'out.write_bytes(b"PNG-STUB" if out.suffix == ".png" else b"STUBMP4")',
]
STUB = chr(10).join(STUB_LINES) + chr(10)


class TransitionPreviewCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output", "fonts"):
            (base / name).mkdir()
        self.log = base / "calls.log"
        self.ffmpeg = base / "ffmpeg-stub.py"
        self.ffmpeg.write_text(STUB)
        self.ffmpeg.chmod(self.ffmpeg.stat().st_mode | stat.S_IEXEC)
        os.environ["FFMPEG_STUB_LOG"] = str(self.log)
        self.settings = Settings(
            config_dir=base / "config", photos_dir=base / "photos", videos_dir=base / "videos",
            music_dir=base / "music", output_dir=base / "output", fonts_dir=base / "fonts",
            ffmpeg_bin=str(self.ffmpeg),
        )
        self.renderer = Renderer(None, self.settings)
        self.cache = TransitionPreviewCache(self.settings, self.renderer)

    def tearDown(self) -> None:
        self.renderer.pool.shutdown(wait=False)
        self.temp.cleanup()
        os.environ.pop("FFMPEG_STUB_LOG", None)

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines() if self.log.exists() else []

    # ------------------------------------------------------------------ catalogue

    def test_catalogue_covers_every_transition_exactly_once(self) -> None:
        items = self.cache.catalogue()
        slugs = [x["slug"] for x in items]
        self.assertEqual(len(slugs), len(set(slugs)), "slugs must be unique")
        self.assertEqual(58, sum(1 for x in items if x["kind"] == "xfade"))
        gl = [x for x in items if x["kind"] == "gl"]
        self.assertTrue(gl)
        self.assertTrue(all(x["label"].startswith("GL ·") for x in gl))
        # The registry is the single source of truth: one entry per GL transition.
        self.assertEqual(len(gl), len({x["label"] for x in gl}))

    def test_slugs_are_stable_and_filesystem_safe(self) -> None:
        self.assertEqual("fade", slugify("Fade"))
        self.assertEqual("wipe-top-left", slugify("Wipe top-left"))
        self.assertEqual("gl-angular", slugify("GL · Angular"))
        for item in self.cache.catalogue():
            self.assertRegex(item["slug"], r"^[a-z0-9][a-z0-9-]*$")

    def test_gl_entries_carry_registry_defaults(self) -> None:
        angular = next(x for x in self.cache.catalogue() if x["label"] == "GL · Angular")
        self.assertEqual({"startingAngle": "90", "clockwise": "0"}, angular["params"])

    # --------------------------------------------------------------------- cache

    def test_first_request_renders_and_later_ones_are_served_from_disk(self) -> None:
        path = self.cache.ensure("GL · Angular")
        self.assertEqual(self.cache.root / "gl-angular.mp4", path)
        self.assertTrue(path.exists())
        renders = [c for c in self.calls() if "filter_complex" in c]
        self.assertEqual(1, len(renders))

        again = self.cache.ensure("GL · Angular")
        self.assertEqual(path, again)
        self.assertEqual(1, len([c for c in self.calls() if "filter_complex" in c]))

    def test_render_uses_the_real_xfade_filter_with_registry_defaults(self) -> None:
        self.cache.ensure("GL · Angular")
        render = next(c for c in self.calls() if "filter_complex" in c)
        self.assertIn("xfade=transition='gl_angular(startingAngle=90,clockwise=0)'", render)
        self.assertIn("duration=0.8", render)
        self.assertIn("offset=0.4", render)
        self.assertIn("-crf", render)

    def test_example_frames_are_created_once(self) -> None:
        self.cache.ensure("GL · Angular")
        sources = sorted(p.name for p in self.cache.src_dir.glob("*.png"))
        self.assertEqual(["a.png", "b.png"], sources)
        self.cache.ensure("Fade")
        self.assertEqual(2, len([c for c in self.calls() if "-frames:v" in c]))

    def test_unsupported_transitions_are_recorded_not_rendered(self) -> None:
        # The stub only advertises fade/dissolve, so everything else is a
        # documented fallback instead of 190 identical dissolve clips.
        with self.assertRaises(PreviewUnavailable):
            self.cache.ensure("Wipe up")
        status = self.cache.status()
        self.assertEqual("unsupported", status["items"]["wipe-up"]["status"])
        self.assertEqual(0, len([c for c in self.calls() if "filter_complex" in c]))

    def test_status_counts_and_build_all(self) -> None:
        self.assertEqual(191, self.cache.status()["total"])
        self.cache.build_all()
        deadline = time.monotonic() + 60
        while self.cache.status()["building"] and time.monotonic() < deadline:
            time.sleep(0.05)
        status = self.cache.status()
        self.assertEqual(0, status["pending"])
        self.assertEqual(191, status["ready"] + status["unsupported"])
        self.assertTrue(status["ready"] >= 2)  # fade + dissolve
        self.assertEqual([], [p.name for p in self.cache.root.glob("*.part")], "no partial files left behind")

    def test_manifest_survives_a_new_cache_instance(self) -> None:
        self.cache.ensure("Fade")
        fresh = TransitionPreviewCache(self.settings, self.renderer)
        self.assertEqual("ready", fresh.status()["items"]["fade"]["status"])
        self.assertTrue(fresh.path_for("fade").exists())
        self.assertIn("fade", json.loads(fresh.manifest_path.read_text())["items"])

    def test_clear_empties_the_cache(self) -> None:
        self.cache.ensure("Fade")
        self.cache.clear()
        self.assertEqual(0, self.cache.status()["ready"])
        self.assertFalse(self.cache.path_for("fade").exists())


if __name__ == "__main__":
    unittest.main()
