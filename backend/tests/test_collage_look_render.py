"""The render half of the look-based entrances: which filter carries which channel.

The colour channels are four numbers in a state struct, and four numbers can be
carried into FFmpeg in several ways — `hue` re-evaluates per frame, `eq` does not,
and `negate` has no amount at all. These tests read the graph text to pin the
choice, because a channel that silently rides no filter is a look the MP4 never
shows while the editor plays it. No FFmpeg needed.
"""
from __future__ import annotations

import math
import re
import unittest

from app.collage import (
    LOOK_ENTRANCE,
    collage_duration,
    collage_graph,
    entrance_length,
    normalize_collage,
    photo_start,
)

W, H, FPS = 640, 360, 25


def _graph(anim: str, look: str | None = None, hold: float = 1.5) -> tuple[str, dict]:
    photo: dict = {"path": "/a.jpg", "anim": anim}
    if look:
        photo["lookAnim"] = look
    item = {"collage": {
        "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1, "hold": hold,
        "frame": {"shape": "none", "shadow": False}, "photos": [photo],
    }}
    if look:
        item["collage"]["lookAnim"] = look
    spec = normalize_collage(item)
    assert spec is not None
    lines, _last = collage_graph(item, W, H, FPS, collage_duration(spec), 0, 1, "cb")
    return "".join(lines), spec


class LookChannelTest(unittest.TestCase):
    def test_saturation_rides_hue_and_brightness_rides_a_ladder(self) -> None:
        graph, spec = _graph("develop")
        # `hue` takes an expression it evaluates for every frame, so the colour
        # gain is exact; `eq` evaluates once at init, so the lightness has to be
        # stepped into it through sendcmd.
        self.assertIn("hue=s='", graph)
        self.assertIn("eq@fx0 contrast", graph)
        self.assertIn("eq@fx0 brightness", graph)
        s_expr = re.search(r"hue=s='([^']+)'", graph).group(1)  # type: ignore[union-attr]
        self.assertIn("0.75", s_expr, "the row's own saturation amplitude is missing")
        # the ladder's first command is the value the photo *starts* at, and
        # develop starts dark. The commands carry a time because sendcmd needs one
        # — it is the photo's own start, not frame zero, since nothing is on
        # screen before the entrance begins.
        steps = re.findall(r"([0-9.]+) eq@fx0 contrast ([0-9.]+)", graph)
        self.assertGreater(len(steps), 3, "the lightness did not step")
        self.assertLess(float(steps[0][1]), 0.3, "the ladder does not start at the dark end")
        self.assertAlmostEqual(float(steps[-1][1]), 1.0, places=2, msg="it never reaches the resting value")

    def test_the_ladder_stops_when_the_entrance_does(self) -> None:
        # A slow colour ramp is fine to bake per frame, but a command per frame of
        # a two-minute slide is not: after the photo has landed the values stop
        # changing, so the ladder must stop too.
        graph, spec = _graph("develop")
        land = photo_start(spec, 0, 0.0) + entrance_length(spec, 0)
        times = [float(m) for m in re.findall(r"([0-9.]+) eq@fx0 contrast", graph)]
        self.assertGreater(len(times), 3, "no ladder to measure")
        self.assertLessEqual(max(times), land + 0.05,
                             f"the colour ladder runs to {max(times)}s, past the entrance at {land}s")

    def test_hue_is_converted_to_radians_once(self) -> None:
        graph, _spec = _graph("hueSwing")
        want = LOOK_ENTRANCE["hueSwing"]["hue"][1] * math.pi / 180      # 120° in radians
        m = re.search(r"hue=H='([+-]?[0-9.]+)\*", graph)
        assert m is not None, f"no hue turn in the graph: {graph[:200]}"
        self.assertAlmostEqual(float(m.group(1)), want, places=3,
                               msg="hue's H= is radians; the state is degrees")
        self.assertNotIn("hue=H='+", graph, "a term that starts an expression should not lead with +")

    def test_an_invert_entrance_is_a_window_on_negate(self) -> None:
        graph, spec = _graph("negative")
        land = photo_start(spec, 0, 0.0) + entrance_length(spec, 0)
        m = re.search(r"negate=enable='lt\(t,([0-9.]+)\)'", graph)
        assert m is not None
        self.assertAlmostEqual(float(m.group(1)), round(land, 3), places=3,
                               msg="the flip has to land where the entrance does")

    def test_two_lanes_multiply_instead_of_fighting(self) -> None:
        # `toColor` in the "while" lane and `xerox` arriving: one hue filter whose
        # s= is the product, because the preview multiplies the same two numbers
        # into a single CSS saturate(). Two `hue` filters would apply in whichever
        # order the chain happened to be built.
        graph, _spec = _graph("xerox", look="toColor")
        self.assertEqual(graph.count("hue=s='"), 1, "two saturation filters in one chain")
        s_expr = re.search(r"hue=s='([^']+)'", graph).group(1)  # type: ignore[union-attr]
        self.assertIn(")*(", s_expr, f"the two lanes are not multiplied: {s_expr}")
        self.assertEqual(s_expr.count("("), s_expr.count(")"), f"unbalanced expression: {s_expr}")

    def test_a_photo_that_only_moves_pays_for_no_colour_chain(self) -> None:
        for anim in ("drop", "wipeUp", "cube", "none"):
            with self.subTest(anim=anim):
                graph, _spec = _graph(anim)
                self.assertNotIn("eq@fx", graph, "a look ladder on a photo with no look")
                self.assertNotIn("hue=s='", graph)
                self.assertNotIn("negate=", graph)


if __name__ == "__main__":
    unittest.main()
