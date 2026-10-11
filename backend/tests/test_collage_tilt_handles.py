"""The preview's turn-a-photo controls (Free arrangement) are pointer maths
with no render counterpart, so the value-by-value twin in test_collage_twin.py
cannot cover them: how an angle folds into ±180°, what a pointer position means
in degrees, what a drag adds to the stored tilt, and how the tilt is written
onto one photo without touching the others (or the spec at all when it comes
back to 0°).

Python writes the cases and an independent computation of what they must give;
Node evaluates src/collageCore.ts on the same input (tests/tilt_handles.mjs).
The last case is a real twin: the rot a stored tilt ends up as in the Free
placements — the number the FFmpeg render turns the photo by — must match
backend/app/collage.py exactly.
"""
from __future__ import annotations

import json
import math
import subprocess
import unittest
from pathlib import Path

from app.collage import normalize_collage, placements

DRIVER = Path(__file__).resolve().parent / "tilt_handles.mjs"
TOL = 1e-9


def _run_node(cases: list[dict]) -> dict:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(DRIVER)],
        input=json.dumps({"cases": cases}), capture_output=True, text=True,
        timeout=120, cwd=Path(__file__).resolve().parent,
    )
    if proc.returncode != 0:
        raise AssertionError(f"tilt handle driver failed:\n{proc.stderr}\n{proc.stdout}")
    return {r["id"]: r for r in json.loads(proc.stdout)}


def _js_round(x: float) -> int:
    """JS Math.round: halves go up (towards +infinity), not to even."""
    return math.floor(x + 0.5)


def _fold(degrees, decimals: int = 1) -> float:
    """normalizeTilt: fold into (-180, 180], then round to `decimals`."""
    raw = float(degrees) if not isinstance(degrees, bool) else float(degrees)
    if not math.isfinite(raw):
        return 0.0
    d = ((raw + 180.0) % 360.0) - 180.0
    if d <= -180.0:
        d = 180.0
    f = 10 ** max(0, min(6, int(decimals)))
    out = _js_round(d * f) / f
    return 180.0 if out == -180.0 else out


def _read(photo: dict) -> float:
    """photoTilt: 0 for a missing, null, boolean or non-numeric stored tilt."""
    raw = photo.get("rot")
    if raw is None or isinstance(raw, bool):
        return 0.0
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == "":
            return 0.0
        try:
            raw = float(raw)
        except ValueError:
            return 0.0
    return _fold(raw)


def _knob_lift(gap, pivot_y, up_y, band, half) -> float:
    """rotateKnobLift: above the mat, else below it, else onto the pivot."""
    reach = max(0.0, float(gap) if gap == gap else 0.0)

    def fits(lift: float) -> bool:
        y = pivot_y + up_y * lift
        return y - half >= band[0] and y + half <= band[1]

    if fits(reach):
        return reach
    if fits(-reach):
        return -reach
    return 0.0


class TiltHandleTest(unittest.TestCase):
    def test_tilt_folds_rounds_and_wraps(self):
        values = [0, 12.3456, -12.3456, 180, -180, 190, -190, 360, -360,
                  720.4, 45, -45, 179.94, -179.96, 0.04, 0.06, 89.99, -90.01]
        cases = [{"id": f"fold-{i}", "normalize": {"value": v}}
                 for i, v in enumerate(values)]
        # decimals: 0 keeps whole degrees, 3 keeps a dragged fine adjustment
        cases += [{"id": "fold-d0", "normalize": {"value": 45.6, "decimals": 0}},
                  {"id": "fold-d3", "normalize": {"value": 12.3456, "decimals": 3}},
                  {"id": "fold-d9", "normalize": {"value": 12.3456789, "decimals": 9}}]
        got = _run_node(cases)
        for i, v in enumerate(values):
            self.assertAlmostEqual(got[f"fold-{i}"]["normalize"], _fold(v), delta=TOL, msg=v)
        self.assertAlmostEqual(got["fold-d0"]["normalize"], 46.0, delta=TOL)
        self.assertAlmostEqual(got["fold-d3"]["normalize"], 12.346, delta=TOL)
        self.assertAlmostEqual(got["fold-d9"]["normalize"], 12.345679, delta=1e-6)  # decimals clamp to 6

    def test_a_stored_tilt_is_read_tolerantly(self):
        photos = [{}, {"path": "a.jpg"}, {"path": "a.jpg", "rot": None},
                  {"path": "a.jpg", "rot": True}, {"path": "a.jpg", "rot": ""},
                  {"path": "a.jpg", "rot": "x"}, {"path": "a.jpg", "rot": "37"},
                  {"path": "a.jpg", "rot": -90.44}, {"path": "a.jpg", "rot": 400},
                  {"path": "a.jpg", "rot": 1e9}]
        cases = [{"id": f"read-{i}", "tilt": {"photo": p}} for i, p in enumerate(photos)]
        got = _run_node(cases)
        for i, p in enumerate(photos):
            self.assertAlmostEqual(got[f"read-{i}"]["tilt"], _read(p), delta=TOL, msg=p)
        self.assertEqual(got["read-0"]["tilt"], 0.0)          # no rot at all
        self.assertAlmostEqual(got["read-8"]["tilt"], 40.0, delta=TOL)   # 400 wraps
        self.assertAlmostEqual(got["read-7"]["tilt"], -90.4, delta=TOL)

    def test_turning_one_photo_leaves_the_others_alone(self):
        photos = [{"path": "a.jpg"}, {"path": "b.jpg", "rot": 10, "size": 1.4},
                  {"path": "c.jpg", "cx": 20, "cy": 30}]
        cases = [{"id": "set-fold", "set": {"photos": photos, "index": 1, "degrees": 375}},
                 {"id": "set-zero", "set": {"photos": photos, "index": 1, "degrees": 0}},
                 {"id": "set-store", "set": {"photos": photos, "index": 0, "degrees": -45}},
                 {"id": "set-keep", "set": {"photos": photos, "index": 2, "degrees": 90.06}}]
        got = _run_node(cases)
        folded = got["set-fold"]["set"]
        self.assertAlmostEqual(folded[1]["rot"], 15.0, delta=TOL)
        self.assertAlmostEqual(folded[1]["size"], 1.4, delta=TOL)   # the rest of the photo survives
        self.assertIsNone(folded[0]["rot"])                          # untouched neighbours
        self.assertIsNone(folded[2]["rot"])
        self.assertIsNone(got["set-zero"]["set"][1]["rot"])          # 0° drops the stored value
        self.assertAlmostEqual(got["set-store"]["set"][0]["rot"], -45.0, delta=TOL)
        self.assertAlmostEqual(got["set-keep"]["set"][2]["rot"], 90.1, delta=TOL)
        self.assertAlmostEqual(got["set-keep"]["set"][2]["cx"], 20.0, delta=TOL)
        self.assertAlmostEqual(got["set-keep"]["set"][2]["cy"], 30.0, delta=TOL)

    def test_a_pointer_position_is_a_clockwise_angle(self):
        # Screen y grows downwards, so right = 0°, below = +90° — the same
        # direction CSS rotate() turns, which is what the mat is drawn with.
        points = {"right": [100, 100, 200, 100], "down": [100, 100, 100, 200],
                  "left": [100, 100, 0, 100], "up": [100, 100, 100, 0],
                  "diag": [100, 100, 137, 7], "pivot": [100, 100, 100, 100]}
        cases = [{"id": k, "pointer": v} for k, v in points.items()]
        got = _run_node(cases)
        for k, (ox, oy, px, py) in points.items():
            self.assertAlmostEqual(got[k]["pointer"], math.degrees(math.atan2(py - oy, px - ox)),
                                   delta=TOL, msg=k)
        self.assertAlmostEqual(got["right"]["pointer"], 0.0, delta=TOL)
        self.assertAlmostEqual(got["down"]["pointer"], 90.0, delta=TOL)
        self.assertAlmostEqual(got["left"]["pointer"], 180.0, delta=TOL)
        self.assertAlmostEqual(got["up"]["pointer"], -90.0, delta=TOL)
        self.assertAlmostEqual(got["pivot"]["pointer"], 0.0, delta=TOL)  # atan2(0, 0)

    def test_a_drag_adds_the_pointer_travel_to_the_stored_tilt(self):
        grab = math.degrees(math.atan2(0 - 100, 100 - 100))          # knob straight above: -90°
        cases, want = [], {}
        for name, start, (px, py), snap in [
            ("right", 0.0, (200, 100), 0),        # grab above, pull right → +90
            ("keeps", 30.0, (200, 100), 0),       # the stored tilt rides along
            ("snap-clean", 0.0, (200, 100), 15),  # Shift: a clean 90 stays clean
            ("snap-round", 0.0, (137, 7), 15),    # 21.7° → the 15° step
            ("exact", 0.0, (137, 7), 0),          # no Shift keeps the exact angle
            ("wrap", 170.0, (60, 200), 0),        # past a full turn → 11.8°
            ("wrap-snap", 170.0, (60, 200), 15),
            ("counter", 0.0, (0, 100), 0),        # pulling left turns counter-clockwise
        ]:
            angle = math.degrees(math.atan2(py - 100, px - 100))
            cases.append({"id": name, "drag": [start, grab, angle, snap]})
            nxt = start + (angle - grab)
            want[name] = _fold(_js_round(nxt / snap) * snap if snap else nxt)
        got = _run_node(cases)
        for name, expected in want.items():
            self.assertAlmostEqual(got[name]["drag"], expected, delta=TOL, msg=name)
        self.assertAlmostEqual(got["right"]["drag"], 90.0, delta=TOL)
        self.assertAlmostEqual(got["keeps"]["drag"], 120.0, delta=TOL)
        self.assertAlmostEqual(got["snap-round"]["drag"], 15.0, delta=TOL)
        self.assertAlmostEqual(got["wrap"]["drag"], 11.8, delta=0.05)
        self.assertAlmostEqual(got["counter"]["drag"], -90.0, delta=TOL)

    def test_the_knob_stays_inside_the_frame(self):
        """The host clips, so a knob above a photo that hangs over the top edge
        would be cut off and unreachable: it has to go below the mat instead,
        and onto the pivot when the photo spans the whole frame."""
        frame = [0.0, 1080.0]                     # camera-local y the frame shows
        zoomed = [270.0, 810.0]                   # the same window at zoom 2
        cases = [
            # (id, gap, pivotY, upY, band, half)
            ("middle", 200.0, 540.0, -1.0, frame, 15.0),          # room above: lift up
            ("over-top", 200.0, 60.0, -1.0, frame, 15.0),         # above is clipped -> below
            ("turned", 200.0, 1020.0, 1.0, frame, 15.0),          # 180° turn: "above" is down
            ("spanning", 700.0, 540.0, -1.0, frame, 15.0),        # neither side fits -> pivot
            ("zoomed", 100.0, 300.0, -1.0, zoomed, 15.0),         # the camera window is the band
            ("snug", 100.0, 130.0, -1.0, frame, 15.0),            # just fits above
            ("zero-gap", 0.0, 540.0, -1.0, frame, 15.0),
            ("junk-gap", float("nan"), 540.0, -1.0, frame, 15.0),
            ("off-frame", 100.0, -400.0, -1.0, frame, 15.0),      # pivot itself is outside
        ]
        got = _run_node([{"id": n, "knob": {"gap": None if g != g else g, "pivotY": py,
                                            "upY": uy, "band": b, "half": h}}
                         for n, g, py, uy, b, h in cases])
        for name, gap, pivot_y, up_y, band, half in cases:
            want = _knob_lift(0.0 if gap != gap else gap, pivot_y, up_y, band, half)
            self.assertAlmostEqual(got[name]["knob"], want, delta=TOL, msg=name)
        self.assertAlmostEqual(got["middle"]["knob"], 200.0, delta=TOL)
        self.assertAlmostEqual(got["over-top"]["knob"], -200.0, delta=TOL)
        self.assertAlmostEqual(got["turned"]["knob"], -200.0, delta=TOL)   # down on screen
        self.assertAlmostEqual(got["spanning"]["knob"], 0.0, delta=TOL)
        self.assertAlmostEqual(got["zoomed"]["knob"], -100.0, delta=TOL)
        self.assertAlmostEqual(got["snug"]["knob"], 100.0, delta=TOL)
        self.assertAlmostEqual(got["zero-gap"]["knob"], 0.0, delta=TOL)
        self.assertAlmostEqual(got["junk-gap"]["knob"], 0.0, delta=TOL)
        self.assertAlmostEqual(got["off-frame"]["knob"], 0.0, delta=TOL)

    def test_a_stored_tilt_is_the_rot_both_engines_place(self):
        spec = {"photos": [{"path": "/photos/a.jpg", "cx": 20, "cy": 30, "rot": -90},
                           {"path": "/photos/b.jpg", "rot": 400},
                           {"path": "/photos/c.jpg"},
                           {"path": "/photos/d.jpg", "rot": 12.34},
                           {"path": "/photos/e.jpg", "rot": -180}],
                "layout": "free", "animation": "none", "shape": "4:3", "seed": 1}
        got = _run_node([{"id": "free", "freeRot": spec}])["free"]["freeRot"]
        mine = [p["rot"] for p in placements(normalize_collage({"collage": spec}), 16 / 9)]
        self.assertEqual(len(got), len(mine))
        for ts, py in zip(got, mine):
            self.assertAlmostEqual(ts, py, delta=TOL, msg=(ts, py))
        self.assertAlmostEqual(mine[0], -90.0, delta=TOL)
        self.assertAlmostEqual(mine[1], 180.0, delta=TOL)    # 400 wraps, then clamps
        self.assertAlmostEqual(mine[2], 0.0, delta=TOL)      # never turned
        self.assertAlmostEqual(mine[3], 12.34, delta=TOL)    # normalization clamps, it does not
        # round: the 0.1° rounding happens when the knob writes the tilt
        # -180 stays -180: normalization clamps into the closed range, only the
        # write path (normalizeTilt / setPhotoRot) folds it onto +180, and the
        # two are the same turn on screen and in `rotate=` alike.
        self.assertAlmostEqual(mine[4], -180.0, delta=TOL)


if __name__ == "__main__":
    unittest.main()
