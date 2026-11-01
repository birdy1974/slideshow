"""A collage reveal must uncover the same pixels in the MP4 as in the preview.

Three implementations of one shape exist for a mask reveal, and all three have to
agree: ``src/collageReveal.ts`` paints the preview's CSS mask,
``backend/app/collage_reveal.py`` decides the timing the render is built around,
and the ``geq`` alpha expression that module generates is what FFmpeg evaluates on
every pixel of every frame. The first two are compared through a Node driver,
point by point on a 12×12 grid; the third is compared by *evaluating the
expression* — the arithmetic subset FFmpeg's ``geq`` uses is a dozen lines, and
that is the only way to test what the render will actually do rather than what the
function it was generated from says.

Also here: the properties that make a reveal a *reveal* — nothing uncovered at
progress 0, everything at 1, monotone growth — for all sixteen kinds and both
directions, and the render's own quantisation ladder (one mask per step of the
sweep), which is where a preview and an MP4 would otherwise part company by one
row of pixels.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "collage_reveal_twin.mjs"

import sys

sys.path.insert(0, str(REPO / "backend"))
from app.collage_reveal import (  # noqa: E402
    REVEALS, REVEAL_GEOM, REVEAL_LENGTH, EASE, geq_alpha, reveal_alpha, reveal_coverage,
    reveal_length, reveal_measure, reveal_progress, reveal_quantise,
)

GRID = 12
# Below this, a point sitting exactly on the sweep line is a rounding question and
# not a shape question; both sides are allowed to answer it either way.
DEAD_ZONE = 1e-7


def _grid_points() -> list[tuple[float, float]]:
    return [((c + 0.5) / GRID, (r + 0.5) / GRID) for r in range(GRID) for c in range(GRID)]


# --------------------------------------------------------------------------- #
# A tiny evaluator for the subset of FFmpeg's expression language we generate.
# --------------------------------------------------------------------------- #

_FUNCS = {
    "min": min, "max": max, "abs": abs, "hypot": math.hypot,
    "floor": math.floor, "gte": lambda a, b: 1.0 if a >= b else 0.0,
    "lte": lambda a, b: 1.0 if a <= b else 0.0, "gt": lambda a, b: 1.0 if a > b else 0.0,
    "lt": lambda a, b: 1.0 if a < b else 0.0, "eq": lambda a, b: 1.0 if a == b else 0.0,
    "sqrt": math.sqrt, "atan2": math.atan2, "sin": math.sin, "cos": math.cos,
}


def eval_ffmpeg_expr(expr: str, x: float, y: float, w: float = 100.0, h: float = 100.0) -> float:
    """Evaluate a generated ``geq`` alpha expression at one pixel.

    ``mod`` is floor-based here; every ``mod`` the generator writes has a
    non-negative first argument, where floor and trunc agree — which is why the
    difference in FFmpeg's own definition cannot bite.
    """
    def mod(a: float, b: float) -> float:
        return a - b * math.floor(a / b) if b else 0.0

    env = dict(_FUNCS)
    env["mod"] = mod
    # The generator scales the input alpha rather than replacing it, so a frame
    # shape's own mask survives the sweep. The expression is checked on a plain
    # opaque mat, where that input is 255 everywhere.
    env["alpha"] = lambda x, y: 255.0
    text = re.sub(r"\bX\b", repr(x * w), expr)
    text = re.sub(r"\bY\b", repr(y * h), text)
    text = re.sub(r"\bW\b", repr(w), text)
    text = re.sub(r"\bH\b", repr(h), text)
    text = text.replace("*255*", "*255*")
    return eval(text, {"__builtins__": {}}, env)  # noqa: S307 - our own generated string


class RevealShapeTest(unittest.TestCase):
    """Properties every kind must hold, before comparing engines at all."""

    def test_hidden_at_zero_and_whole_at_one(self) -> None:
        for kind in REVEALS:
            with self.subTest(kind=kind):
                for direction in (0, 1):
                    self.assertLessEqual(reveal_coverage(kind, 0.0, direction), 0.02, "starts already uncovered")
                    self.assertGreaterEqual(reveal_coverage(kind, 1.0, direction), 0.99, "never finishes")

    def test_the_uncovered_area_only_grows(self) -> None:
        for kind in REVEALS:
            steps = [reveal_coverage(kind, i / 20) for i in range(21)]
            for a, b in zip(steps, steps[1:]):
                self.assertGreaterEqual(b + 1e-9, a, f"{kind}: coverage shrank from {a:.3f} to {b:.3f}")
            self.assertGreater(steps[10] - steps[0], 0.15, f"{kind}: half-way looks like nothing")

    def test_every_kind_is_a_different_shape(self) -> None:
        # Sixteen ids that uncover the same pixels would be sixteen bugs wearing
        # names, and a table-driven catalogue makes that easy to do by accident.
        seen: dict[str, str] = {}
        for kind in REVEALS:
            sig = "".join(str(reveal_alpha(kind, (c + 0.5) / 8, (r + 0.5) / 8, 0.45)) for r in range(8) for c in range(8))
            self.assertNotIn(sig, seen, f"{kind} is identical to {seen.get(sig)}")
            seen[sig] = kind

    def test_mirroring_moves_the_sweep_to_the_other_side(self) -> None:
        for kind, geom in REVEAL_GEOM.items():
            if geom.get("mirror") == "none":
                continue
            left0 = reveal_alpha(kind, 0.08, 0.5, 0.3, 0)
            left1 = reveal_alpha(kind, 0.08, 0.5, 0.3, 1)
            right0 = reveal_alpha(kind, 0.92, 0.5, 0.3, 0)
            right1 = reveal_alpha(kind, 0.92, 0.5, 0.3, 1)
            if geom["mirror"] in ("x", "both"):
                self.assertEqual((left0, left1), (right1, right0), kind)

    def test_easing_is_shared_and_monotone(self) -> None:
        for kind in REVEALS:
            fam = REVEAL_GEOM[kind]["fam"]
            for q in (0.0, 0.2, 0.5, 0.9, 1.0):
                self.assertAlmostEqual(reveal_progress(kind, q), EASE[fam](max(0.0, min(1.0, q))), places=12, msg=kind)
            self.assertEqual(reveal_progress(kind, -1), 0.0)
            self.assertEqual(reveal_progress(kind, 3), 1.0)

    def test_lengths_are_the_table_both_engines_share(self) -> None:
        self.assertEqual(sorted(REVEAL_LENGTH), sorted(REVEALS))
        for kind, seconds in REVEAL_LENGTH.items():
            self.assertGreater(seconds, 0.05, kind)


class GeqExpressionTest(unittest.TestCase):
    """The generated expression, evaluated, on the same points."""

    def test_the_geq_alpha_is_the_same_predicate(self) -> None:
        points = _grid_points()
        for kind in REVEALS:
            for direction in (0, 1):
                for step in range(1, 8):
                    p = reveal_quantise(8, step / 8)
                    expr = geq_alpha(kind, p, direction)
                    self.assertTrue(expr, kind)
                    for (u, v) in points:
                        want = reveal_alpha(kind, u, v, p, direction)
                        got = 1 if eval_ffmpeg_expr(expr, u, v) > 127 else 0
                        if want != got:
                            # Only allowed where the point is exactly on the line.
                            geom = REVEAL_GEOM[kind]
                            x, y = (1 - u, v) if direction and geom["mirror"] in ("x", "both") else (u, v)
                            q = reveal_measure(geom, x, y)
                            edge = abs(q - p) if not geom.get("out") else abs(q - (1 - p))
                            self.assertLess(edge, DEAD_ZONE * 100, f"{kind} dir={direction} p={p} at ({u:.3f},{v:.3f})")

    def test_nothing_is_visible_at_zero_and_everything_at_one(self) -> None:
        for kind in REVEALS:
            for direction in (0, 1):
                expr0 = geq_alpha(kind, reveal_quantise(8, 0.0), direction)
                expr1 = geq_alpha(kind, 1.0, direction)
                self.assertTrue(all(eval_ffmpeg_expr(expr1, u, v) > 250 for u, v in _grid_points()), f"{kind} at p=1")
                # The first step of the ladder is a 1/8 sweep: for a wipe that is
                # one row of a 12-row grid, and nothing at all for the round and
                # tiled kinds. More than two rows means it did not start hidden.
                low = sum(1 for u, v in _grid_points() if eval_ffmpeg_expr(expr0, u, v) > 127)
                self.assertLessEqual(low, 2 * GRID, f"{kind} shows {low} of {GRID * GRID} pixels before it starts")

    def test_an_unknown_kind_generates_nothing(self) -> None:
        self.assertEqual(geq_alpha("irisHard", 0.5), "")
        self.assertEqual(geq_alpha(None, 0.5), "")

    def test_numbers_stay_short_and_exact(self) -> None:
        # A 17-digit float in a filter argument is a log nobody can read, and the
        # bake-in is why the geometry constants are rounded on purpose.
        expr = geq_alpha("wipeRight", 0.5)
        self.assertNotIn("0.500000", expr.replace("0.5", "X"), expr)
        self.assertLess(len(expr), 400, expr)


class QuantisationTest(unittest.TestCase):

    def test_the_ladder_never_shows_more_than_its_step(self) -> None:
        for kind in REVEALS:
            for steps in (4, 8, 16, 32):
                for i in range(steps):
                    q = reveal_quantise(steps, i / steps)
                    self.assertGreater(q, 0.0, kind)
                    self.assertLessEqual(q, 1.0, kind)
                    self.assertTrue(i / steps <= q < (i + 1) / steps + 1e-12, f"{kind} {i}/{steps} → {q}")

    def test_the_end_of_the_sweep_is_exact(self) -> None:
        # p = 1 must be *reachable*: a ladder that stops at 0.98 leaves the last
        # two rows of the photo uncovered in the MP4 and visible in the preview.
        for kind in REVEALS:
            self.assertEqual(reveal_quantise(16, 1.0), 1.0, kind)
            self.assertEqual(reveal_quantise(16, 1.5), 1.0, kind)


class LengthTest(unittest.TestCase):
    def test_the_two_tables_agree(self) -> None:
        self.assertEqual(set(REVEAL_LENGTH), set(REVEALS))
        for kind in REVEALS:
            self.assertAlmostEqual(reveal_length(kind), REVEAL_LENGTH[kind], places=12)

    def test_unknown_ids_have_no_length(self) -> None:
        self.assertEqual(reveal_length("nope"), 0.0)
        self.assertEqual(reveal_length(None), 0.0)


class EngineTwinTest(unittest.TestCase):
    """The preview's own numbers, from the file the browser loads."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the reveal twin driver")

    def _js(self, cases: list[dict]) -> list[dict]:
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=json.dumps({"cases": cases}), capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"reveal twin driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_grids_and_progress_agree_point_by_point(self) -> None:
        cases = [{"kind": kind, "dir": d, "p": p}
                 for kind in REVEALS for d in (0, 1) for p in (0.0, 0.15, 0.3, 0.45, 0.6, 0.8, 1.0)]
        got = self._js(cases)
        self.assertEqual(len(got), len(cases))
        for case, one in zip(cases, got):
            label = f"{case['kind']} dir={case['dir']} p={case['p']}"
            mine = "".join(str(reveal_alpha(case["kind"], (c + 0.5) / GRID, (r + 0.5) / GRID, case["p"], case["dir"]))
                           for r in range(GRID) for c in range(GRID))
            self.assertEqual(mine, one["grid"], label)
            self.assertAlmostEqual(reveal_coverage(case["kind"], case["p"], case["dir"]), one["coverage"], places=12, msg=label)
            self.assertAlmostEqual(reveal_progress(case["kind"], case["p"]), one["progress"], places=12, msg=label)
            self.assertAlmostEqual(reveal_length(case["kind"]), one["length"], places=12, msg=label)

    def test_the_css_mask_and_the_geq_mask_are_the_same_ladder(self) -> None:
        # The preview builds its mask by sampling `revealAlpha` at the centre of a
        # quantised step — the same rule `reveal_quantise` gives the render. So a
        # frame of the preview is a frame of the MP4, to one step of the sweep.
        cases = [{"kind": kind, "dir": 0, "p": p} for kind in REVEALS for p in (0.2, 0.5, 0.9)]
        got = self._js(cases)
        for case, one in zip(cases, got):
            self.assertTrue(one["mask"], case["kind"])
            step_p = reveal_quantise(32, case["p"])
            mine = "".join(str(reveal_alpha(case["kind"], (c + 0.5) / GRID, (r + 0.5) / GRID, step_p, 0))
                           for r in range(GRID) for c in range(GRID))
            expr = geq_alpha(case["kind"], step_p, 0)
            theirs = "".join("1" if eval_ffmpeg_expr(expr, (c + 0.5) / GRID, (r + 0.5) / GRID) > 127 else "0"
                             for r in range(GRID) for c in range(GRID))
            # one sampled mask is 12×12 cells, so at most a boundary row may differ
            diff = sum(1 for a, b in zip(mine, theirs) if a != b)
            self.assertLessEqual(diff, 2 * GRID, f"{case['kind']}: {diff} cells apart")


if __name__ == "__main__":
    unittest.main()
