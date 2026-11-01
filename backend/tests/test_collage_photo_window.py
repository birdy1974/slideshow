"""The timing lane's two numbers: `at` and `until`, per photo.

A collage is built as a *sequence*: photo i starts after the delays of photos
0..i, everybody lingers until the end of the hold, and each photo leaves on the
exit schedule's offset. That is a good default and a bad way to say "this photo
only during the second line", so the lane writes two optional seconds fields per
photo, measured from the start of the collage's own content (after the lead-in):

    at     — when this photo's entrance begins
    until  — the instant it is *fully gone*, i.e. the end of its exit fly,
             because that is what the right edge of a lane means

The rules below are the ones the GUI depends on: an authored arrival is exact
(beat snapping must not move a number someone typed), a departure is read back
over the fly so the number in the UI and the number in the graph are the same
event seen from the same end, a window too short to hold its own fly is refused
rather than run backwards, an authored stop can push the slide longer but never
shorter than the neighbours, a photo with no fields takes today's numbers to the
millisecond, and a stop authored on a photo that has no exit still closes its
overlay window — otherwise the last baked level would sit there for the rest of
the slide.

`photo_state` (the preview) and `collage_graph` (the MP4) are both checked,
because a lane the render ignores is a lie in a text box.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO))

from app.collage import (base_duration, collage_duration, collage_graph,  # noqa: E402
                         entrance_length, exit_fly_length, exit_offset, exit_start,
                         normalize_collage, photo_at, photo_start, photo_state,
                         photo_until)

LEAD_IN = 0.5


def _spec(n: int = 3, hold: float = 2.0, exit: str | None = "fade", **extra) -> dict:
    spec: dict = {
        "photos": [{"path": f"/p/{i}.jpg", "name": f"p{i}.jpg", "delay": 0.4} for i in range(n)],
        "layout": "grid",
        "animation": "drop",
        "shape": "4:3",
        "seed": 7,
        "hold": hold,
    }
    if exit is not None:
        spec["exit"] = exit
    spec.update(extra)
    return spec


def _windows(spec: dict, duration: float | None = None) -> list[tuple[float, float]]:
    """Each photo's composited window (the union of its overlay `enable` spans),
    exactly as the render will see it."""
    item = {"collage": spec}
    dur = collage_duration(spec) if duration is None else duration
    lines, _ = collage_graph(item, 1280, 720, 25.0, dur, LEAD_IN, len(spec["photos"]), "cb")
    graph = "\n".join(lines)
    out: list[tuple[float, float]] = []
    for i in range(len(spec["photos"])):
        spans: list[tuple[float, float]] = []
        # One overlay per ladder level, each with its own `enable`; the union of
        # those windows is the time the photo is composited at all.
        for enable in re.findall(rf"\[lv{i}(?:_\d+)?\]overlay=[^\[\n]*?:enable='([^']+)'", graph):
            for part in enable.split("+"):
                m = re.match(r"between\(t,([-0-9.]+),([-0-9.]+)\)", part)
                if m:
                    spans.append((float(m.group(1)), float(m.group(2))))
                    continue
                m = re.match(r"gte\(t,([-0-9.]+)\)", part)
                if m:
                    spans.append((float(m.group(1)), float("inf")))
        if not spans:
            out.append((float("nan"), float("nan")))
        else:
            out.append((min(a for a, _ in spans), max(b for _, b in spans)))
    return out


def _alpha(spec: dict, i: int, t: float) -> float:
    return float(photo_state(spec, i, t, LEAD_IN, 16 / 9)["alpha"])


class AuthoredWindowTest(unittest.TestCase):
    def test_only_real_numbers_are_authored(self) -> None:
        spec = _spec()
        self.assertIsNone(photo_at(spec, 0), "no field is no time, and 0 is a real time")
        spec["photos"][0]["at"] = 0
        self.assertEqual(photo_at(spec, 0), 0.0, "`at: 0` means frame one, not 'unset'")
        for junk in (None, "", " ", "2s", True, False, float("nan"), float("inf"), -1):
            spec["photos"][0]["at"] = junk
            self.assertIsNone(photo_at(spec, 0), f"{junk!r} is not an authored time")
        spec["photos"][0]["at"] = 2.5
        for junk in (None, "", "1s", True, 0, -2.0, float("nan"), float("inf")):
            spec["photos"][0]["until"] = junk
            self.assertIsNone(photo_until(spec, 0), f"{junk!r} is not an authored stop")
        spec["photos"][1]["at"] = 1.25
        spec["photos"][1]["until"] = 3.75
        self.assertAlmostEqual(photo_until(spec, 1), 3.75, places=6)
        self.assertIsNone(photo_at(spec, 99), "a photo that is not there has no schedule")

    def test_an_authored_arrival_is_not_moved_by_beat_sync(self) -> None:
        beats = [0.9, 1.1, 1.9, 2.3]
        spec = _spec(beatSync=True, beats=beats)
        spec["photos"][1]["at"] = 0.8      # the schedule would roll this onto 0.9
        self.assertAlmostEqual(photo_start(spec, 1, LEAD_IN), LEAD_IN + 0.8, places=9,
                               msg="a number someone typed is not a suggestion")
        del spec["photos"][1]["at"]
        self.assertAlmostEqual(photo_start(spec, 1, LEAD_IN), LEAD_IN + 0.9, places=9,
                               msg="and the same photo is still snapped when nobody typed it")

    def test_no_authored_field_changes_nothing(self) -> None:
        spec = _spec()
        legacy = base_duration(spec)
        graph_before = "".join(collage_graph({"collage": spec}, 1280, 720, 25.0,
                                            collage_duration(spec), LEAD_IN, 3, "cb")[0])
        for key in ("at", "until"):
            for junk in (None, "", True, "nope"):
                spec["photos"][1][key] = junk
        self.assertEqual(base_duration(spec), legacy, "a null field must not lengthen the slide")
        for i in range(3):
            self.assertAlmostEqual(
                exit_start(spec, i, LEAD_IN),
                LEAD_IN + base_duration(spec) + exit_offset(spec, i),
                places=9,
                msg="without `until` the departure is the legacy formula, in one place",
            )
        graph_after = "".join(collage_graph({"collage": spec}, 1280, 720, 25.0,
                                           collage_duration(spec), LEAD_IN, 3, "cb")[0])
        self.assertEqual(graph_after, graph_before, "a photo carrying only empty timing fields paints the same frames")

    def test_until_is_the_instant_the_photo_is_fully_gone(self) -> None:
        spec = _spec()
        spec["photos"][1]["until"] = 4.0
        fly = exit_fly_length(spec, 1)
        self.assertGreater(fly, 0)
        self.assertAlmostEqual(exit_start(spec, 1, LEAD_IN) + fly, LEAD_IN + 4.0, places=9)
        # fully gone at the authored end, and visible just before it
        self.assertLess(_alpha(spec, 1, LEAD_IN + 4.0 - 0.04), 0.5)
        self.assertEqual(_alpha(spec, 1, LEAD_IN + 4.0), 0.0)
        # the neighbours keep the schedule they always had
        for i in (0, 2):
            self.assertAlmostEqual(exit_start(spec, i, LEAD_IN),
                                   LEAD_IN + base_duration(spec) + exit_offset(spec, i), places=9)

    def test_a_window_too_short_never_runs_the_curves_backwards(self) -> None:
        spec = _spec()
        spec["photos"][1]["at"] = 1.0
        spec["photos"][1]["until"] = 1.0
        floor = photo_start(spec, 1, LEAD_IN) + entrance_length(spec, 1)
        self.assertAlmostEqual(exit_start(spec, 1, LEAD_IN), floor, places=9)
        self.assertGreater(_alpha(spec, 1, floor - 0.02), 0.0, "the photo still gets its entrance")
        spec["photos"][1]["until"] = 1.02
        self.assertGreaterEqual(exit_start(spec, 1, LEAD_IN), floor - 1e-9)

    def test_an_authored_stop_can_outrun_the_hold(self) -> None:
        spec = _spec(hold=1.0)
        legacy = base_duration(spec)
        spec["photos"][0]["until"] = legacy + 2.0
        self.assertGreaterEqual(base_duration(spec), legacy + 2.0 - 1e-6,
                                "the slide has to stay up while a photo is on it")
        self.assertGreater(collage_duration(spec), legacy, "and the frame follows, not just the base")
        self.assertAlmostEqual(_windows(spec)[0][1], LEAD_IN + legacy + 2.0, delta=0.05,
                               msg="its composited window ends where the lane says")
        # a stop *inside* the hold must not shorten anybody else's time on screen
        tight = _spec(hold=6.0)
        tight["photos"][0]["until"] = 1.5
        self.assertEqual(base_duration(tight), base_duration(_spec(hold=6.0)))
        self.assertGreater(_alpha(tight, 1, LEAD_IN + 3.0), 0.0)

    def test_a_stopping_photo_with_no_exit_is_cut_not_frozen(self) -> None:
        spec = _spec(exit="none")
        spec["photos"][1]["until"] = 1.8
        self.assertEqual(exit_fly_length(spec, 1), 0.0, "'none' has no fly")
        self.assertEqual(_alpha(spec, 1, LEAD_IN + 1.8), 0.0, "the preview cuts it")
        self.assertGreater(_alpha(spec, 1, LEAD_IN + 1.8 - 0.04), 0.0)
        self.assertEqual(_alpha(spec, 0, LEAD_IN + 1.8 + 0.5), 1.0, "the others keep staying to the end")
        win = _windows(spec)[1]
        self.assertAlmostEqual(win[1], LEAD_IN + 1.8, delta=0.01,
                               msg="the render closes the overlay window too, or the last level holds forever")
        self.assertEqual(_windows(spec)[0][1], float("inf"), "a photo with no exit stays to the end of the slide")

    def test_a_stopping_photo_with_an_exit_is_cut_when_its_fly_ends(self) -> None:
        spec = _spec(exit="fade")
        spec["photos"][1]["until"] = 3.2
        self.assertAlmostEqual(exit_start(spec, 1, LEAD_IN), LEAD_IN + 3.2 - exit_fly_length(spec, 1), places=9)
        win = _windows(spec)[1]
        self.assertAlmostEqual(win[1], LEAD_IN + 3.2, delta=0.01,
                               msg="one frame of grace at most, not a whole fly")
        self.assertEqual(_alpha(spec, 1, LEAD_IN + 3.2), 0.0)
        graph = "".join(collage_graph({"collage": spec}, 1280, 720, 25.0,
                                     collage_duration(spec), LEAD_IN, 3, "cb")[0])
        self.assertIn("fade=t=out", graph, "a photo with a fade exit still fades on its way out")


class LanePersistenceTest(unittest.TestCase):
    """`normalize_collage` is the gate a project passes through on its way into and
    out of storage: whatever the lane writes has to come back, rounded once."""

    def test_the_two_fields_are_kept_rounded_and_clamped(self) -> None:
        case = _spec(n=2)
        case["photos"][0]["at"] = 1.23456
        case["photos"][0]["until"] = 2.99999
        case["photos"][1]["at"] = 900
        case["photos"][1]["until"] = -5
        out = normalize_collage({"collage": case})
        self.assertEqual(out["photos"][0]["at"], 1.235)
        self.assertEqual(out["photos"][0]["until"], 3.0)
        self.assertEqual(out["photos"][1]["at"], 600.0, "clamped, not dropped — a lane can be long")
        self.assertNotIn("until", out["photos"][1], "and a nonsense stop is no stop")

    def test_a_stop_that_is_not_after_its_start_is_dropped(self) -> None:
        for until in (0.5, 1.0):
            case = _spec(n=1)
            case["photos"][0]["at"] = 1.0
            case["photos"][0]["until"] = until
            out = normalize_collage({"collage": case})
            self.assertNotIn("until", out["photos"][0], "a window with no width is not a window")
            self.assertEqual(out["photos"][0]["at"], 1.0, "the arrival the user typed stays")

    def test_nothing_is_written_for_an_untouched_photo(self) -> None:
        out = normalize_collage({"collage": _spec(n=2)})
        for photo in out["photos"]:
            self.assertNotIn("at", photo)
            self.assertNotIn("until", photo)


class PopupWiringTest(unittest.TestCase):
    """The lane is a feature only once it is in the popup the user is editing, and
    it has to read the model through the same accessors the render does."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.lane = (REPO / "src" / "CollageTimingLanes.tsx").read_text()
        cls.app = (REPO / "src" / "App.tsx").read_text()
        cls.core = (REPO / "src" / "collageCore.ts").read_text()

    def test_the_lane_asks_the_engine_instead_of_remembering(self) -> None:
        for helper in ("photoStart", "photoAt", "photoUntil", "exitStart", "exitFlyLength", "entranceLength"):
            self.assertIn(f"{helper}(", self.lane, f"{helper}() must be the lane's source of truth")
        self.assertIn("from './collageCore'", self.lane,
                      "the lane reads the shared engine, so it cannot drift from the render")
        self.assertNotIn("baseDuration", self.lane, "the lane draws windows, it does not schedule them")

    def test_the_lane_snaps_to_the_frames_the_render_renders(self) -> None:
        self.assertIn("Math.round(v * 25) / 25", self.lane,
                      "25 frames a second is what the MP4 gets, so the lane must not offer a third of one")

    def test_the_popup_mounts_the_lane_in_the_timing_group(self) -> None:
        self.assertIn("import { CollageTimingLanes } from './CollageTimingLanes'", self.app)
        self.assertIn("<CollageTimingLanes", self.app)
        head = self.app.index("When each photo is on screen")
        mount = self.app.index("<CollageTimingLanes")
        self.assertLess(head, mount, "the lane sits under the head that explains it")
        self.assertLess(mount, self.app.index("collage-timing-total", mount),
                        "and inside Photo size & timing, where the per-photo waits already live")

    def test_the_popup_writes_both_fields_as_seconds(self) -> None:
        self.assertIn("'at' | 'until'", self.app, "setPhotoLane has to accept the lane's two fields")
        self.assertIn("lane === 'at' || lane === 'until'", self.app)
        self.assertRegex(self.app, r"Math\.round\(Math\.min\(600[^\n]*\) \* 1000\) / 1000")
        self.assertRegex(self.app, r"<CollageTimingLanes[\s\S]{0,400}onChange=\{\(i, lane, value\) => setPhotoLane")

    def test_both_normalisers_round_and_validate_the_same_way(self) -> None:
        self.assertIn("Math.round(Math.max(0, Math.min(600, at)) * 1000) / 1000", self.core)
        self.assertIn("rawUntil <= at", self.core, "a stop that is not after its start is dropped in the editor too")
        backend = (REPO / "backend" / "app" / "collage.py").read_text()
        self.assertIn('entry["at"] = at', backend)
        self.assertIn('entry["until"] = until', backend)


if __name__ == "__main__":
    unittest.main()
