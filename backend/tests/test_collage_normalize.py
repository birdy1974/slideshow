"""Every collage exit the editor offers has to survive a save and a load.

``normalizeCollage`` in ``src/collageCore.ts`` is the one gate a project passes
through on its way into and out of storage, and it is *strict*: an unknown value
becomes ``undefined``, which for an exit means "no exit at all" — the setting is
gone and the seconds it added to the slide are gone with it. That is only safe
if the gate knows every value the UI can write, which is why the lists it
validates against are the same ones the chips and ``exitMode`` read.

It did not used to be: 'fade' was added to ``COLLAGE_EXITS``, to ``exitMode`` and
to the chips, but not to the hard-coded triple inside the normalizer, so a
whole-collage "Fade out" disappeared on reload while a *per-photo* fade (a
different line, validating against the full list) stayed. The lengths and the
stagger are compared against ``backend/app/collage.py`` too, because a slide the
preview shortens and the render does not is the same bug wearing a hat.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "collage_normalize.mjs"

import sys

sys.path.insert(0, str(REPO / "backend"))
from app.collage import EXIT_LENGTH, EXIT_STAGGER, EXIT_SPEED_MAX, EXIT_SPEED_MIN  # noqa: E402

MODES = ("sweep", "deal", "shuffle", "fade")


def spec(exit_mode=None, photos=None, **extra):
    out: dict = {"layout": "stack", "animation": "drop", "photos": photos if photos is not None else [{"path": f"p{i}.jpg"} for i in range(3)]}
    if exit_mode is not None:
        out["exit"] = exit_mode
    out.update(extra)
    return out


class CollageNormalizeExitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the collage normalize driver")

    def _run(self, cases: list[dict]) -> list[dict]:
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=json.dumps({"cases": cases}), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"collage normalize driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_every_offered_exit_survives(self) -> None:
        got = self._run([spec(m) for m in MODES])
        self.assertEqual([g["exit"] for g in got], list(MODES), "an exit the chips offer must not be dropped on load")
        for mode, one in zip(MODES, got):
            self.assertEqual(one["exitMode"], mode)
            self.assertEqual(one["schedule"], mode)
            self.assertEqual(one["exitModeLast"], mode)
            self.assertEqual(one["exitModeFirst"], mode)

    def test_the_exit_seconds_match_the_render(self) -> None:
        """The slide grows by the last photo's offset plus its own fly-out, with
        the tables both engines share — otherwise the preview runs short and the
        MP4 keeps a beat of nothing at the end."""
        got = self._run([spec(m) for m in MODES])
        for mode, one in zip(MODES, got):
            n = len(one["offsets"])
            want_total = EXIT_STAGGER[mode] * (n - 1) + EXIT_LENGTH[mode]
            self.assertAlmostEqual(one["exitTotal"], round(want_total, 6), places=5, msg=mode)
            self.assertEqual(one["flies"], [EXIT_LENGTH[mode]] * n, mode)
            # 'deal' clears the top of the pile first, so it counts down instead
            want_offsets = [round(EXIT_STAGGER[mode] * (n - 1 - i if mode == "deal" else i), 6) for i in range(n)]
            self.assertEqual(one["offsets"], want_offsets, mode)

    def test_a_speed_multiplier_divides_both_and_is_clamped(self) -> None:
        got = self._run([spec("fade", exitSpeed=2), spec("fade", exitSpeed=0.5), spec("fade", exitSpeed=99), spec("fade", exitSpeed=-3)])
        self.assertEqual(got[0]["exitSpeed"], 2)
        self.assertEqual(got[1]["exitSpeed"], 0.5)
        self.assertEqual(got[2]["exitSpeed"], EXIT_SPEED_MAX)
        self.assertIsNone(got[3]["exitSpeed"], "a nonsense speed is no speed, not a negative one")
        self.assertAlmostEqual(got[0]["exitTotal"], EXIT_STAGGER["fade"] * 2 / 2 + EXIT_LENGTH["fade"] / 2, places=5)
        self.assertAlmostEqual(got[1]["exitTotal"], EXIT_STAGGER["fade"] * 2 * 2 + EXIT_LENGTH["fade"] * 2, places=5)
        self.assertAlmostEqual(got[2]["exitTotal"], EXIT_STAGGER["fade"] * 2 / EXIT_SPEED_MAX + EXIT_LENGTH["fade"] / EXIT_SPEED_MAX, places=5)

    def test_none_and_junk_mean_no_exit(self) -> None:
        got = self._run([spec("none"), spec("iris"), spec(1), spec(None)])
        for one in got:
            self.assertIsNone(one["exit"], "only a real mode may be kept")
            self.assertEqual(one["exitMode"], "none")
            self.assertEqual(one["schedule"], "none")
            self.assertEqual(one["exitTotal"], 0)
            self.assertEqual(one["offsets"], [0, 0, 0], "a photo that never leaves has no schedule")

    def test_per_photo_exits_are_validated_against_the_same_list(self) -> None:
        photos = [{"path": "a.jpg", "exit": "fade"}, {"path": "b.jpg", "exit": "none"},
                  {"path": "c.jpg", "exit": "blur"}, {"path": "d.jpg"}]
        got = self._run([spec(None, photos=photos), spec("sweep", photos=photos)])
        for one in got:
            self.assertEqual(one["photoExits"], ["fade", "none", None, None])
        # The collage has no exit of its own: the first photo that leaves one
        # supplies the shared schedule, and the photo that says 'none' stays.
        first = got[0]
        self.assertEqual(first["schedule"], "fade")
        self.assertEqual(first["exitModeFirst"], "fade")
        self.assertEqual(first["exitModeLast"], "none")
        self.assertEqual(first["flies"][0], EXIT_LENGTH["fade"])
        self.assertEqual(first["flies"][3], 0)
        # a photo's own exit wins over the collage's; the others inherit it
        self.assertEqual(got[1]["exitModeFirst"], "fade")
        self.assertEqual(got[1]["exitModeLast"], "sweep")
        self.assertEqual(got[1]["exitMode"], "sweep")

    def test_reversed_order_survives_too(self) -> None:
        got = self._run([spec("fade", exitReverse=True), spec("fade", exitReverse=False)])
        self.assertTrue(got[0]["exitReverse"])
        self.assertFalse(got[1]["exitReverse"])
        n = len(got[0]["offsets"])
        self.assertEqual(got[0]["offsets"], [round(EXIT_STAGGER["fade"] * (n - 1 - i), 6) for i in range(n)])


if __name__ == "__main__":
    unittest.main()
