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
from app.collage import (EXIT_LENGTH, EXIT_STAGGER, EXIT_SPEED_MAX, EXIT_SPEED_MIN,  # noqa: E402
                         base_duration, exit_start, normalize_collage, photo_start)

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
        got = self._run([spec("none"), spec("notAnAnimation"), spec(1), spec(None)])
        for one in got:
            self.assertIsNone(one["exit"], "only a name from the catalogue may be kept")
            self.assertEqual(one["exitMode"], "none")
            self.assertEqual(one["schedule"], "none")
            self.assertEqual(one["exitTotal"], 0)
            self.assertEqual(one["offsets"], [0, 0, 0], "a photo that never leaves has no schedule")

    def test_an_arrival_is_a_legal_departure(self) -> None:
        """The exit catalogue IS the entrance catalogue. A stored `exit` may name any
        animation the picker can offer, and the render has to see the same seconds for
        it that the preview does — the fly is that arrival's own length, the schedule is
        'reverse'\'s, because a mirrored exit travels nowhere of its own.
        """
        from app.collage import ENTRANCE_LENGTH as PY_ENTRANCE_LENGTH
        from app.collage import resolve_anim
        # a mask reveal, a mover, a bridged alias and a real FFmpeg transition — the
        # four kinds of name the entrance browser can write, all of them exits now
        ids = ["iris", "rise", "slideleft", "fadeblack"]
        got = self._run([spec(i) for i in ids])
        self.assertEqual([one["exit"] for one in got], ids,
                         "a name the browser offers must survive the load — dropping it loses the exit *and* its seconds")
        for name, one in zip(ids, got):
            self.assertEqual(one["exitMode"], "reverse", name)
            self.assertEqual(one["schedule"], "reverse", name)
            self.assertAlmostEqual(one["flies"][0], PY_ENTRANCE_LENGTH[resolve_anim(name)], places=5,
                                   msg=f"{name}'s fly is that entrance's own length")
        # the slide really does grow by those seconds, on both sides of the bridge
        self.assertGreater(got[0]["exitTotal"], 0)
        self.assertAlmostEqual(got[0]["exitTotal"], EXIT_STAGGER["reverse"] * 2 + PY_ENTRANCE_LENGTH["iris"], places=5,
                               msg="two staggered gaps plus the iris's own length")

    def test_a_photos_own_none_stays_a_choice(self) -> None:
        """An explicit 'none' on a photo is not an absence: it is how you keep one photo
        on the board while the others leave. The collage-level 'none' means the same as
        nothing at all, so it is the *photo* row that has to preserve it.
        """
        photos = [{"path": "a.jpg", "exit": "none"}, {"path": "b.jpg", "exit": "iris"}]
        got = self._run([spec("sweep", photos=photos)])
        self.assertEqual(got[0]["photoExits"], ["none", "iris"])
        self.assertEqual(got[0]["exitModeFirst"], "none", "the photo that stays reports no mode at all")
        self.assertEqual(got[0]["exitModeLast"], "reverse", "the mirrored one behaves like 'reverse'")
        self.assertEqual(got[0]["flies"][0], 0, "and it has no fly to schedule")

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

    def test_the_timing_lanes_two_fields_survive(self) -> None:
        """The timing lane writes `at` and `until` — seconds the user typed, not a
        schedule to be nudged. Both gates have to keep them, round them once and the
        same way, and agree on the windows they add up to, because the popup draws
        the block from these helpers while the render clips its overlays to them."""
        photos = [
            {"path": "a.jpg", "at": 1.23456, "until": 2.99999},   # ms-rounded, both sides
            {"path": "b.jpg", "at": 0, "until": 0.0},             # `at: 0` is frame one
            {"path": "c.jpg", "at": 900},                          # clamped, not dropped
            {"path": "d.jpg", "until": "1s"},                      # junk is no time
            {"path": "e.jpg"},                                     # untouched: no fields at all
        ]
        case = spec("sweep", photos=photos)
        got = self._run([case])[0]
        self.assertEqual(got["windowAt"], [1.235, 0.0, 600.0, None, None])
        self.assertEqual(got["windowUntil"], [3.0, None, None, None, None],
                         "a stop that is not after its start is not a stop")
        want = normalize_collage({"collage": case})
        self.assertEqual([p.get("at") for p in want["photos"]], got["windowAt"],
                         "the render's gate has to keep exactly what the editor's kept")
        self.assertEqual([p.get("until") for p in want["photos"]], got["windowUntil"])
        n = len(photos)
        for i in range(n):
            self.assertAlmostEqual(got["starts"][i], photo_start(want, i, 0.5), places=6)
            self.assertAlmostEqual(got["exitStarts"][i], exit_start(want, i, 0.5), places=6)
        self.assertAlmostEqual(got["base"], base_duration(want), places=5)
        # the photo whose authored stop falls inside the pile must not shorten the
        # schedule of the one nobody touched
        self.assertGreater(got["exitStarts"][4], got["exitStarts"][1],
                           "an authored early departure must not pull the last photo out with it")

    def test_reversed_order_survives_too(self) -> None:
        got = self._run([spec("fade", exitReverse=True), spec("fade", exitReverse=False)])
        self.assertTrue(got[0]["exitReverse"])
        self.assertFalse(got[1]["exitReverse"])
        n = len(got[0]["offsets"])
        self.assertEqual(got[0]["offsets"], [round(EXIT_STAGGER["fade"] * (n - 1 - i), 6) for i in range(n)])


if __name__ == "__main__":
    unittest.main()
