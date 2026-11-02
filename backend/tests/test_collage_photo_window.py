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

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LANE_TSX = REPO / "src" / "CollageTimingLanes.tsx"
LANE_MATH_TS = REPO / "src" / "CollageLaneMath.ts"
DRIVER = Path(__file__).resolve().parent / "collage_lane_math.mjs"
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
        cls.lane = LANE_TSX.read_text(encoding="utf-8")
        cls.math = LANE_MATH_TS.read_text(encoding="utf-8")
        cls.app = (REPO / "src" / "App.tsx").read_text()
        cls.core = (REPO / "src" / "collageCore.ts").read_text()

    def test_the_lane_asks_the_engine_instead_of_remembering(self) -> None:
        for helper in ("photoStart", "photoAt", "photoUntil", "exitStart", "exitFlyLength", "entranceLength"):
            self.assertIn(f"{helper}(", self.lane, f"{helper}() must be the lane's source of truth")
        self.assertIn("from './collageCore'", self.lane,
                      "the lane reads the shared engine, so it cannot drift from the render")
        self.assertNotIn("baseDuration", self.lane, "the lane draws windows, it does not schedule them")

    def test_the_lane_moves_on_a_drag_and_measures_its_own_track(self) -> None:
        """The two ways this lane was dead: nothing was listening for the move,
        and the numbers it read came from the lane's outer box (name column, seconds
        column and all) instead of the track under the cursor."""
        self.assertIn("window.addEventListener('pointermove'", self.lane,
                      "a 22 px lane loses the pointer in one frame, so the move lives on the window")
        self.assertIn("window.removeEventListener('pointermove'", self.lane,
                      "…and is taken off again, or every lane in the popup moves with one drag")
        self.assertIn(".closest<HTMLElement>('.collage-window-track')", self.lane,
                      "the ruler the drag is measured across is the row's track")
        self.assertNotIn("lanes.current?.getBoundingClientRect()", self.lane,
                         "the outer box is not a ruler: it also holds the name and value columns")
        self.assertNotIn("setPointerCapture", self.lane,
                         "capture retargets the events the window listener is already handling")

    def test_the_exit_end_of_a_lane_says_which_exit_it_ends(self) -> None:
        self.assertIn("exitChoice(spec, i)", self.lane, "the right edge ends an exit, so it names it")
        self.assertIn("exitMode(spec, i)", self.lane, "…by the name they gave it, flattened where that is all there is")
        self.assertIn("exitFlyLength(spec, i)", self.lane)

    def test_the_lane_snaps_to_the_frames_the_render_renders(self) -> None:
        self.assertIn("LANE_FPS = 25", self.math,
                      "25 frames a second is what the MP4 gets, so the lane must not offer a third of one")
        self.assertRegex(self.math.replace(" ", ""), r"Math\.round\(t\*fps\)/fps")
        self.assertIn("MIN_WINDOW = 0.12", self.math)

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



class LaneSelectionSurfaceTest(unittest.TestCase):
    """The group is chosen in the lane, edits everything, and is visible on the preview.

    These are the three ways the feature could be half-built, so each is pinned against
    the source of the component that has to do it: a checkbox that only highlights a
    row, an entrance picker that writes to the lead photo alone, or a selection nobody
    can see while they are changing it.
    """

    def setUp(self) -> None:
        self.lanes = (REPO / "src" / "CollageTimingLanes.tsx").read_text(encoding="utf-8")
        self.app = (REPO / "src" / "App.tsx").read_text(encoding="utf-8")

    def test_the_lane_offers_a_checkbox_a_number_and_a_select_all(self) -> None:
        self.assertIn('aria-label="Select every photo"', self.lanes, "the header's box is the only Select all")
        self.assertIn('type="checkbox" checked={on}', self.lanes)
        self.assertIn("<i onClick={click(i)}", self.lanes, "the number itself selects that photo alone")
        self.assertIn("el.indeterminate", self.lanes, "a partly checked column must not read as 'all'")
        self.assertIn("selected: number[]", self.lanes)
        self.assertNotIn("onSelect:", self.lanes, "the lane no longer owns a single index")

    def test_a_group_drag_and_a_group_reset_go_to_every_member(self) -> None:
        self.assertIn("if (!selected.includes(index)) onSelected([index])", self.lanes,
                      "dragging a row that is in the group must not dissolve the group")
        self.assertIn("const moved = groupShift(", self.lanes)
        self.assertIn("held.group.forEach", self.lanes)
        self.assertIn("const clearWindows = (index: number) => {", self.lanes)
        self.assertIn("const list = selected.includes(index) && selected.length > 1 ? selected : [index]", self.lanes,
                      "auto and double-click undo the group you are in, not just the row you touched")

    def test_every_panel_writer_expands_through_the_selection(self) -> None:
        for name in ("setPhotoFrame", "setPhotoShape", "setPhotoMotionField", "clearPhotoLanes",
                     "rotateFreePhoto", "setSize", "setPhotoLane"):
            self.assertIn(f"const {name} = (", self.app, name)
        wrapped = [line for line in self.app.splitlines() if "=> forSelected(" in line]
        self.assertGreaterEqual(len(wrapped), 7,
                               "each writer takes the same signature it always had and fans out inside — so no call site can forget the group")
        self.assertIn("const turnSelectedPhoto = (index: number, delta: number)", self.app,
                      "the turn buttons turn each photo from where it stands, not to one angle")
        self.assertIn("photoSelection.length > 1 ? `${photoSelection.length} photos`", self.app,
                      "the panel says how many it is editing")

    def test_the_selected_photos_are_dotted_on_the_preview(self) -> None:
        """A ring for every photo in the group — not only for the lead, and not on the mat.

        A mat rides its own entrance: it is transparent before it arrives, gone after
        its exit, and a mask reveal clips away anything drawn on it. A ring that lives
        on the mat is therefore invisible exactly when a person is deciding which
        photos to group, so the marks are siblings of the mats and loop over the whole
        selection.
        """
        self.assertIn("for (const mark of marks)", self.app, "one ring per selected photo, not one per selection")
        self.assertIn("const marks = useMemo(", self.app, "a stable list, so the clock subscription does not rebuild every frame")
        self.assertIn("ringRefs.current[mark]", self.app, "the ring is a sibling of the mats, never a border on them")
        self.assertIn("ringRefs.current[i] = el", self.app)
        self.assertIn("{marks.map(i => <div key={`ring-${i}`}", self.app)
        self.assertIn("i === selectIndex ? ' lead'", self.app,
                      "the photo the panel reads its numbers from keeps the heavier ring")
        self.assertIn("collage-selected-which", self.app, "and which ones, in words, beside the panel's title")
        self.assertNotIn("' marked' : ''", self.app, "the mat's own outline was dropped rather than doubling up with the ring")
        css = (REPO / "src" / "styles.css").read_text(encoding="utf-8")
        self.assertIn(".collage-select-ring:not(.lead){border-width:3px;border-style:dashed;border-color:#d9b35d}", css,
                      "the rest of the group in the lanes' gold, so a row and its picture match")
        self.assertIn(".collage-window-row.lead{outline:", css)

    def test_the_mixed_state_is_told_rather_than_guessed(self) -> None:
        self.assertIn("mixed={mixedOn(i => photos[i].anim)}", self.app)
        self.assertIn("mixed={mixedOn(i => photos[i].exit)}", self.app)
        picker = (REPO / "src" / "AnimPicker.tsx").read_text(encoding="utf-8")
        self.assertIn("mixed ? 'Mixed' : value === undefined ? PHASE.choose : label", picker,
                      "the chip says the photos disagree instead of naming one of them")


class GroupWriteBurstTest(unittest.TestCase):
    """Several writes in one interaction must add up instead of replacing each other.

    A group edit walks the selection photo by photo, a lane drag writes the entrance and
    then the exit, and React does not re-render in between — so every write in the burst
    starts from the same snapshot unless the popup keeps what it last wrote. The driver
    replays a burst against `beginWrite`/`recordWrite`, which is the code the popup's
    `setSpec` runs, so this measures the composition rather than the intention.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the lane math driver")
        cls.app = (REPO / "src" / "App.tsx").read_text(encoding="utf-8")

    def _run(self, cases: list[dict]) -> list[dict]:
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=json.dumps({"cases": cases}), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"lane math driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_a_group_edit_lands_on_every_photo_it_visited(self) -> None:
        got = self._run([{
            "script": [
                {"render": {"token": "A", "spec": {"photos": [{}, {}, {}]}}},
                {"lane": {"index": 0, "key": "anim", "value": "fade"}},
                {"lane": {"index": 1, "key": "anim", "value": "fade"}},
                {"lane": {"index": 2, "key": "anim", "value": "fade"}},
            ]
        }])[0]["burst"]
        self.assertEqual(got[0]["seen"], {"photos": [{}, {}, {}]}, "the burst starts where the item stands")
        sent = got[-1]["sent"]
        self.assertEqual([photo.get("anim") for photo in sent["photos"]], ["fade", "fade", "fade"],
                         "one write per photo, and the last one must not undo the first two")

    def test_both_edges_of_a_window_survive_one_another(self) -> None:
        got = self._run([{
            "script": [
                {"render": {"token": "A", "spec": {"photos": [{"anim": "fade"}]}}},
                {"write": {"at": 2}},
                {"write": {"until": 4}},
            ]
        }])[0]["burst"]
        sent = got[-1]["sent"]
        self.assertEqual(sent, {"photos": [{"anim": "fade"}], "at": 2, "until": 4},
                         "the field written first is still there, and so is the photo list")

    def test_the_buffer_is_dropped_once_the_item_catches_up(self) -> None:
        got = self._run([{
            "script": [
                {"render": {"token": "A", "spec": {"photos": [{"anim": "fade"}]}}},
                {"write": {"at": 2}},
                {"render": {"token": "B", "spec": {"photos": [{"anim": "fade"}], "at": 2, "duration": 9}}},
                {"write": {"until": 4}},
            ]
        }])[0]["burst"]
        self.assertEqual(got[2]["seen"]["duration"], 9, "a new item's value is the truth, buffer or not")
        self.assertEqual(got[-1]["sent"], {"photos": [{"anim": "fade"}], "at": 2, "until": 4, "duration": 9},
                         "and the next write builds on it instead of resurrecting what was dropped")

    def test_a_render_that_arrives_first_keeps_the_write_in_flight(self) -> None:
        # A re-render from something else (another field, a clock tick) must not roll a
        # drag back to where it started while the pointer is still down.
        got = self._run([{
            "script": [
                {"render": {"token": "A", "spec": {"photos": [{"n": 1}]}}},
                {"write": {"at": 2}},
                {"render": {"token": "A", "spec": {"photos": [{"n": 1}]}}},
            ]
        }])[0]["burst"]
        self.assertEqual(got[2]["seen"], {"photos": [{"n": 1}], "at": 2})

    def test_a_popup_that_writes_nothing_reads_the_item(self) -> None:
        got = self._run([{
            "script": [{"render": {"token": "A", "spec": {"photos": [{"n": 1}], "layout": "grid"}}}]
        }])[0]["burst"]
        self.assertEqual(got[0]["seen"], {"photos": [{"n": 1}], "layout": "grid"})
        self.assertNotIn("livePatch({ collage: { ...spec", self.app,
                        "no write may compose on the render's snapshot again")

    def test_every_group_writer_builds_on_the_live_list(self) -> None:
        for name in ("setPhotoFrame", "setPhotoShape", "setPhotoLane", "setPhotoMotionField",
                     "setPhotoMotionAmount", "clearPhotoLanes", "rotateFreePhoto", "turnSelectedPhoto",
                     "setSize", "movePhotoTarget", "turnPhotoTarget"):
            start = self.app.index(f"const {name} = (")
            body = self.app[start:start + 1400]
            self.assertIn("photosNow()", body, f"{name} reads the list the last write left")
        self.assertIn("const hasExit = exitChosen !== 'none' || photos.some(", self.app,
                      "a read for the panel keeps reading what this render was drawn with")
        self.assertIn("const photos = spec.photos ?? []", self.app)
        self.assertIn("const photosNow = (): CollagePhoto[] => specNow().photos ?? []", self.app)


class LaneMathTest(unittest.TestCase):
    """The lane's cursor↔second mapping, executed from the module the component
    uses — a pure function is only a promise until something outside it calls it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the lane math driver")

    def _run(self, cases: list[dict]) -> list[dict]:
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=json.dumps({"cases": cases}), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"lane math driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_the_selection_rules_are_one_thing_for_every_control(self) -> None:
        """Checkbox, number and Shift-range: one implementation, four call sites.

        The lane's checkbox, its number cell, the chip strip and the preview all let
        a person pick which photos the panel edits. If any one of them decided its own
        modifier rules, the popup would mean different things in different halves — so
        they share `nextSelection`, and this measures the sharing.
        """
        got = self._run([
            {"sel": [], "index": 2, "mode": "single", "count": 5},
            {"sel": [2], "index": 2, "mode": "single", "count": 5},
            {"sel": [2], "index": 4, "mode": "single", "count": 5},
            {"sel": [1, 3], "index": 3, "mode": "toggle", "count": 5},
            {"sel": [1, 3], "index": 2, "mode": "toggle", "count": 5},
            {"sel": [1], "index": 4, "mode": "range", "count": 5},
            {"sel": [], "index": 3, "mode": "range", "count": 5},
            {"sel": [1], "index": 9, "mode": "toggle", "count": 5},
            {"sel": [1, 7, -2, 1.5], "index": 2, "mode": "toggle", "count": 5},
            {"sel": [0], "index": 1, "mode": "toggle", "count": 0},
        ])
        self.assertEqual(got[0]["selection"], [2], "a click on nothing selected picks that one")
        self.assertEqual(got[1]["selection"], [], "clicking the only selected photo again lets it go")
        self.assertEqual(got[2]["selection"], [4], "a plain click means this one only")
        self.assertEqual(got[3]["selection"], [1], "the checkbox unchecks: it removes, it does not replace")
        self.assertEqual(got[4]["selection"], [1, 2, 3], "and adds in index order, so the lead photo stays the lead")
        self.assertEqual(got[5]["selection"], [1, 2, 3, 4], "Shift extends from the last thing clicked")
        self.assertEqual(got[6]["selection"], [3], "with nothing to extend from, Shift is just a click")
        self.assertEqual(got[7]["selection"], [1], "an index outside the pile is not a photo")
        self.assertEqual(got[8]["selection"], [1, 2], "stored junk drops out rather than reaching the spec")
        self.assertEqual(got[9]["selection"], [], "a collage with no photos has no selection")

    def test_a_group_drag_shifts_every_member_by_what_the_anchor_moved(self) -> None:
        """A body drag moves the selection together: same shift, own width, own clamp."""
        track = {"left": 0.0, "width": 600.0}
        span = 60.0
        got = self._run([
            {"rect": track, "span": span, "from": 100.0, "to": 130.0,
             "anchor": {"at": 10.0, "until": 20.0}, "others": [{"at": 0.0, "until": 5.0}, {"at": 55.0, "until": 58.0}]},
            {"rect": track, "span": span, "from": 100.0, "to": 20.0,
             "anchor": {"at": 10.0, "until": 20.0}, "others": [{"at": 2.0, "until": 6.0}]},
            {"rect": track, "span": span, "from": 100.0, "to": 700.0,
             "anchor": {"at": 40.0, "until": 50.0}, "others": [{"at": 12.0, "until": 14.0}]},
            {"rect": track, "span": span, "from": 100.0, "to": 130.0,
             "anchor": {"at": 10.0, "until": 20.0}, "others": []},
        ])
        self.assertEqual(got[0]["group"], [{"at": 3.0, "until": 8.0}, {"at": 57.0, "until": 60.0}],
                         "3 s right: the short window keeps its 5 s, the far one stops at the end of the lane")
        self.assertEqual(got[1]["group"], [{"at": 0.0, "until": 4.0}],
                         "the group stops with the anchor instead of sliding into negative time")
        self.assertEqual(got[2]["group"], [{"at": 22.0, "until": 24.0}],
                         "and at the far end the anchor was clamped to 50, so the group shifts by 10 and no further")
        self.assertEqual(got[3]["group"], [], "one photo selected is exactly the old single drag")


        span = 60.0
        track = {"left": 140.0, "width": 660.0}
        got = self._run([
            {"rect": track, "span": span, "x": 140.0},
            {"rect": track, "span": span, "x": 470.0},
            {"rect": track, "span": span, "x": 800.0},
            {"rect": {"left": 0.0, "width": 660.0}, "span": span, "x": 330.0},
            {"rect": {"left": 140.0, "width": 898.0}, "span": span, "x": 470.0},
        ])
        self.assertAlmostEqual(got[0]["secondsAt"], 0.0, places=6)
        self.assertAlmostEqual(got[1]["secondsAt"], 30.0, places=6)
        self.assertAlmostEqual(got[2]["secondsAt"], 60.0, places=6)
        # the same offset in seconds for the same place on the track, whichever
        # column layout the popup happens to have
        self.assertAlmostEqual(got[3]["secondsAt"], got[1]["secondsAt"], places=6,
                               msg="a drag must not depend on where the lane starts on the page")
        self.assertAlmostEqual(got[4]["secondsAt"], 22.05, delta=0.02,
                               msg="across the outer box the same click is 22 s, not 30 s — the whole bug")
        self.assertLess(got[4]["secondsAt"], 30.0,
                           "measuring the track is the whole point: the outer box said 22s here")

    def test_a_drag_measures_travel_not_absolute_position(self) -> None:
        track = {"left": 140.0, "width": 660.0}
        got = self._run([
            {"rect": track, "span": 60, "from": 300, "to": 366, "at": 1.0, "until": 4.0},
            {"rect": track, "span": 60, "from": 300, "to": 366, "at": 57, "until": 59},
            {"rect": track, "span": 60, "from": 300, "to": 240, "at": 1.0, "until": 4.0},
            {"rect": track, "span": 60, "from": 300, "to": 366, "at": 1.0, "until": 1.02},
        ])
        # 66 px of travel on a 660 px track across a 60 s lane is 6 seconds
        self.assertAlmostEqual(got[0]["move"]["at"], 7.0, places=6)
        self.assertAlmostEqual(got[0]["move"]["until"], 10.0, places=6,
                               msg="the window keeps the width the person set")
        self.assertAlmostEqual(got[1]["move"]["until"], 60.0, places=6, msg="…and stops at the lane")
        self.assertAlmostEqual(got[1]["move"]["at"], 58.0, places=6)
        self.assertAlmostEqual(got[2]["move"]["at"], 0.0, places=6, msg="and never goes negative")
        self.assertAlmostEqual(got[3]["move"]["until"] - got[3]["move"]["at"], 0.12, places=6,
                               msg="two frames is the shortest window worth writing")

    def test_the_edges_pin_and_clamp_the_other_end(self) -> None:
        track = {"left": 140.0, "width": 660.0}
        got = self._run([
            # Shift-click at 15 s on a 60 s lane with room, then at 59 s on a photo
            # whose stop is already at 4 s
            {"rect": track, "span": 60, "x": 305.0, "at": 1.0, "until": 20.0},
            {"rect": track, "span": 60, "x": 790.0, "at": 1.0, "until": 4.0},
            # Ctrl-click before the arrival, and one edge dragged past the other
            {"rect": track, "span": 60, "from": 0, "to": 0, "at": 10.0, "until": 4.0},
            {"rect": track, "span": 60, "from": 300, "to": 700, "at": 1.0, "until": 4.0},
        ])
        self.assertAlmostEqual(got[0]["pinStart"], 15.0, places=6,
                               msg="Shift-click pins the arrival where the pointer is")
        self.assertAlmostEqual(got[0]["pinStop"], 15.0, places=6,
                               msg="Ctrl-click pins the departure where the pointer is — the end of the fly")
        self.assertAlmostEqual(got[1]["pinStart"], 3.88, places=6,
                               msg="a pinned start stops short of its stop, never past it")
        self.assertLessEqual(got[1]["pinStart"], 4.0 - 0.12 + 1e-9,
                             "a start cannot pass the stop it is paired with")
        self.assertGreaterEqual(got[2]["pinStop"], 10.0 + 0.12 - 1e-9)
        self.assertLessEqual(got[3]["edgeStart"], 4.0 - 0.12 + 1e-9)
        self.assertAlmostEqual(got[3]["edgeEnd"], 40.36, places=6,
                               msg="the other edge does not move when one is dragged, and the one that "
                                   "does lands on a whole frame (40.3636 s snapped to 40.36 s)")

    def test_an_unusable_track_maps_to_zero_instead_of_a_division(self) -> None:
        got = self._run([
            {"rect": None, "span": 60, "x": 400},
            {"rect": {"left": 0, "width": 0}, "span": 60, "x": 400},
            {"rect": {"left": 0, "width": 660}, "span": 0, "x": 400},
            {"rect": {"left": 0, "width": 660}, "span": 60, "snap": 1.237},
        ])
        self.assertEqual([g["secondsAt"] for g in got[:3]], [0.0, 0.0, 0.0])
        self.assertAlmostEqual(got[3]["snap"], 1.24, places=9, msg="a fortieth of a second is the grid")


if __name__ == "__main__":
    unittest.main()
