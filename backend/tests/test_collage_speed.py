"""Entrance speed: one multiplier, every part of an arrival.

The editor's slider is a *speed*, not a duration, so 2 has to halve the sweep,
the alpha ramp, the pendulum's clock and the render's size-ladder sampling
together. These tests read the filter graph, so they run without FFmpeg; the
frame-by-frame parity with the preview lives in test_collage_twin.py.
"""
from __future__ import annotations

import re
import unittest

from app.collage import (
    ANIMS,
    ENTRANCE_FADE,
    ENTRANCE_LENGTH,
    ENTRANCE_SPEED_MAX,
    ENTRANCE_SPEED_MIN,
    base_duration,
    collage_duration,
    exit_fly_length,
    exit_offset,
    photo_state,
    anim_speed,
    collage_duration,
    collage_graph,
    entrance_fade,
    entrance_length,
    normalize_collage,
)


def _fmt(v: float) -> str:
    """`_n` as the graph writes it: trimmed, so the test compares the same text."""
    return ("%.3f" % v).rstrip("0").rstrip(".")


def _spec(**extra) -> dict:
    return {"collage": {
        "layout": "grid", "animation": "drop", "shape": "4:3", "seed": 1, "hold": 1.5,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": "/photos/a.jpg"}, {"path": "/photos/b.jpg"}],
        **extra,
    }}


def _graph(spec_extra: dict, size: tuple[int, int] = (320, 180)) -> str:
    item = _spec(**spec_extra)
    norm = normalize_collage(item)
    assert norm is not None
    lines, _last = collage_graph(item, size[0], size[1], 25, collage_duration(norm), 0, 1, "cb")
    return "".join(lines)


class SpeedAccessorsTest(unittest.TestCase):
    def test_the_multiplier_scales_length_and_fade_alike(self) -> None:
        for anim, fade in (("drop", 0.22), ("pop", 0.18), ("wipeUp", 0.5)):
            for mult, want_len in ((0.5, 2.0), (1.0, 1.0), (2.0, 0.5), (10.0, 1 / ENTRANCE_SPEED_MAX)):
                with self.subTest(anim=anim, mult=mult):
                    spec = normalize_collage({"collage": {
                        "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1, "hold": 1,
                        "animSpeed": mult, "photos": [{"path": "/a.jpg"}]}})
                    assert spec is not None
                    self.assertAlmostEqual(entrance_length(spec, 0), ENTRANCE_LENGTH[anim] * want_len, places=6)
                    self.assertAlmostEqual(anim_speed(spec, 0), 1 / want_len, places=6)
                    if fade:
                        self.assertAlmostEqual(entrance_fade(spec, 0),
                                             ENTRANCE_FADE[anim] * want_len, places=6)

    def test_malformed_and_absent_speeds_mean_one(self) -> None:
        for bad in (None, 0, -1, "fast", True, float("nan"), float("inf")):
            with self.subTest(bad=bad):
                spec = normalize_collage({"collage": {
                    "layout": "grid", "animation": "drop", "photos": [{"path": "/a.jpg"}],
                    "animSpeed": bad}})
                assert spec is not None
                self.assertEqual(anim_speed(spec), 1.0)
                self.assertAlmostEqual(entrance_length(spec, 0), ENTRANCE_LENGTH["drop"], places=9)

    def test_a_photo_can_be_faster_than_its_collage(self) -> None:
        spec = normalize_collage({"collage": {
            "layout": "grid", "animation": "drop", "photos": [
                {"path": "/a.jpg"}, {"path": "/b.jpg", "animSpeed": 2}],
            "animSpeed": 1}})
        assert spec is not None
        self.assertEqual(entrance_length(spec, 0), ENTRANCE_LENGTH["drop"])
        self.assertEqual(entrance_length(spec, 1), ENTRANCE_LENGTH["drop"] / 2)


class SpeedPersistenceTest(unittest.TestCase):
    def test_the_multipliers_survive_a_save(self) -> None:
        spec = normalize_collage(_spec(animSpeed=1.75, animDir=1, photos=[
            {"path": "/photos/a.jpg", "animSpeed": 0.4, "animDir": 1},
            {"path": "/photos/b.jpg", "animSpeed": 40},                       # clamped, not dropped
        ]))
        assert spec is not None
        self.assertAlmostEqual(spec["animSpeed"], 1.75, places=6)
        self.assertEqual(spec["animDir"], 1)
        self.assertAlmostEqual(spec["photos"][0]["animSpeed"], 0.4, places=6)
        self.assertEqual(spec["photos"][0]["animDir"], 1)
        self.assertAlmostEqual(spec["photos"][1]["animSpeed"], ENTRANCE_SPEED_MAX, places=6)
        # and a second pass changes nothing: what the engine keeps is stable
        again = normalize_collage({"collage": spec})
        assert again is not None
        self.assertAlmostEqual(again["animSpeed"], spec["animSpeed"], places=9)
        self.assertAlmostEqual(again["photos"][0]["animSpeed"], spec["photos"][0]["animSpeed"], places=9)

    def test_junk_is_dropped_rather_than_stored(self) -> None:
        # a numeric string is coerced, not rejected — `float()` in python and
        # `Number()` in the editor's validator, which is what `exitSpeed` already
        # does, and the two must agree because a spec can arrive from a JSON file.
        spec = normalize_collage(_spec(animSpeed="0.9"))
        assert spec is not None
        self.assertAlmostEqual(spec["animSpeed"], 0.9, places=6)
        for junk in ("", "fast", None, 0, -2):
            with self.subTest(junk=junk):
                dropped = normalize_collage(_spec(animSpeed=junk))
                assert dropped is not None
                self.assertNotIn("animSpeed", dropped)
        spec = normalize_collage(_spec(animDir="1"))
        assert spec is not None
        self.assertNotIn("animDir", spec)


class SpeedInGraphTest(unittest.TestCase):
    def test_a_faster_entrance_is_a_shorter_fade(self) -> None:
        slow = _graph({})
        fast = _graph({"animSpeed": 2})

        def fades(graph: str) -> list[float]:
            return [float(m) for m in re.findall(r"fade=t=in:st=[0-9.]+:d=([0-9.]+)", graph)]
        slow_d, fast_d = fades(slow), fades(fast)
        self.assertTrue(slow_d and fast_d, "no entrance fades in the graph at all")
        self.assertAlmostEqual(max(slow_d) / 2, max(fast_d), places=3)

    def test_the_swing_clock_is_compressed(self) -> None:
        # The pendulum is measured in raw seconds, so speed has to multiply the
        # clock inside its exp()/cos() — a graph that only divided by `ent` would
        # still swing at the same rate and stop mid-swing.
        slow = _graph({"animation": "swing"})
        fast = _graph({"animation": "swing", "animSpeed": 2})
        self.assertIn("exp(-1.3*max(t-0.15,0))", slow)
        # `_n` writes 2.0 as "2", so this is the whole multiplier, once
        self.assertIn("exp(-1.3*max(t-0.15,0)*2)", fast)
        self.assertIn("cos(2*PI*max(t-0.15,0)*2/1.6)", fast)

    def test_the_size_ladder_stops_when_the_entrance_does(self) -> None:
        def last_window_end(graph: str) -> float:
            ends = [float(m) for m in re.findall(r"enable='between\(t,[0-9.-]+,([0-9.]+)\)", graph)]
            return max(ends) if ends else 0.0
        # `pop` is the case that matters: its size ladder exists only because the
        # sprite changes size while it arrives, so a sampling window that ignored
        # the speed would keep stepping a photo that had already settled — or
        # stop before it had. At a small frame the coarse buckets collapse every
        # size into one sprite, so this asks at 720p.
        slow = last_window_end(_graph({"animation": "pop"}, (1280, 720)))
        fast = last_window_end(_graph({"animation": "pop", "animSpeed": 2}, (1280, 720)))
        self.assertGreater(slow, 0, "no size ladder to measure at all")
        self.assertGreater(slow, fast)
        self.assertLess(fast / slow, 0.85)

class ScaleLadderCoverageTest(unittest.TestCase):
    def test_every_anim_that_changes_size_is_listed_for_the_ladder(self) -> None:
        # The list a hand-written entrance has to join. Sampling each anim's own
        # state is the only way to notice one that was forgotten: a size the
        # ladder does not sample is a size the MP4 never shows, and no type or
        # table check would say so.
        from app.collage import GEOM_SCALES, SCALE_ANIMS
        for anim in ANIMS:
            if anim == "none":
                continue
            spec = normalize_collage({"collage": {
                "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1, "hold": 1,
                "photos": [{"path": "/a.jpg"}]}})
            assert spec is not None
            length = entrance_length(spec, 0) or 0.001
            varies = False
            for k in range(0, int(length * 25) + 1):
                st = photo_state(spec, 0, k / 25, 0.0, 320 / 180)
                if abs(st["scale"] - 1) > 1e-6 or abs(st["scaleX"] - 1) > 1e-6:
                    varies = True
                    break
            with self.subTest(anim=anim):
                self.assertEqual(
                    varies, anim in SCALE_ANIMS or anim in GEOM_SCALES,
                    f"{anim} changes size but is not listed for the size ladder" if varies
                    else f"{anim} is listed for the ladder but never changes size")


class ReverseExitGraphTest(unittest.TestCase):
    def _graph_for(self, anim: str, exit_mode: str) -> tuple[str, float, float]:
        item = {"collage": {
            "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1, "hold": 1.5,
            "exit": exit_mode, "frame": {"shape": "none", "shadow": False},
            "photos": [{"path": "/a.jpg"}]}}
        spec = normalize_collage(item)
        assert spec is not None
        te0 = base_duration(spec) + exit_offset(spec, 0)
        fly = exit_fly_length(spec, 0)
        lines, _last = collage_graph(item, 640, 360, 25, collage_duration(spec), 0, 1, "cb")
        return "".join(lines), te0, fly

    def _levels(self, graph: str) -> list[dict]:
        """One entry per sprite the ladder built for photo 0: the mask step it
        carries (None = unmasked) and the time windows it is switched on for.
        Read off the graph, because the graph is what the render runs."""
        out = []
        for m in re.finditer(r"\[(b0_(\d+)|sp0f)\].*?\[lv0_(\d+)\];", graph):
            idx = m.group(3)
            line = m.group(0)
            p = None
            # the threshold is the last number in the comparison, right before
            # the closing paren of the mask test
            am = re.search(r"a='alpha\(X,Y\).*?([0-9]+\.[0-9]+)\)'", line)
            if am:
                p = float(am.group(1))
            ov = re.search(r"\[lv0_" + idx + r"\]overlay=[^\[]*enable='([^']*)'", graph)
            windows = re.findall(r"(?:between\(t,([-0-9.]+),([-0-9.]+)\)|gte\(t,([-0-9.]+)\))",
                                 ov.group(1)) if ov else []
            out.append({"i": idx, "p": p,
                        "windows": [(float(a), float(b) if b else None) for a, b, c in windows
                                    for a, b in [(a or c, b)]]})
        return out

    def test_a_mirrored_reveal_closes_as_it_opened(self) -> None:
        graph, te0, fly = self._graph_for("wipeUp", "reverse")
        levels = self._levels(graph)
        self.assertGreater(len(levels), 4, "the sweep did not step at all")
        # The closing sweep does not bake new sprites: a step of the mask is a
        # still, and the same still serves the moment it opens and the moment it
        # closes, so those levels simply carry a second window.
        reused = [l for l in levels if l["p"] is not None and len(l["windows"]) > 1]
        self.assertGreaterEqual(len(reused), 3,
                                f"nothing was reused across the exit: {[l['p'] for l in levels]}")
        late = [w for l in reused for w in l["windows"] if w[0] >= te0 - 0.05]
        self.assertTrue(late, "no masked level has a window in the exit")

        def on_at(t: float) -> list[dict]:
            return [l for l in levels
                    if any(a - 0.01 <= t <= (b if b is not None else t + 1) + 0.01 for a, b in l["windows"])]
        # Before the fly it is fully uncovered — and stays so, no mask on the
        # resting level — and at the very end the photo has to be *covered*
        # again: an open-ended unmasked level would bring it back for the cut.
        self.assertTrue(all(l["p"] is None for l in on_at(te0 - 0.1)),
                        "a mask is still applied while the photo is at rest")
        at_end = on_at(te0 + fly - 0.02)
        self.assertTrue(at_end, "nothing is on screen at the end of the fly")
        self.assertTrue(all(l["p"] is not None and l["p"] < 0.1 for l in at_end),
                        f"the photo does not end covered: {[l['p'] for l in at_end]}")

    def test_the_alpha_out_is_the_alpha_in_reversed(self) -> None:
        graph, te0, fly = self._graph_for("pop", "reverse")
        spec = normalize_collage({"collage": {"layout": "grid", "animation": "pop", "hold": 1.5,
                                             "exit": "reverse", "photos": [{"path": "/a.jpg"}]}})
        assert spec is not None
        ratio = entrance_fade(spec, 0) / entrance_length(spec, 0)
        want_d = fly * ratio
        self.assertIn(f"fade=t=out:st={_fmt(te0 + fly - want_d)}:d={_fmt(want_d)}:alpha=1", graph,
                      "the fly's fade is not the entrance's ramp, read backwards")

    def test_a_mirrored_exit_does_not_fly_anywhere(self) -> None:
        # 'sweep' adds a travel term to the overlay's x; a mirrored exit must not,
        # because its motion comes from the entrance curves themselves.
        rev, _t, _f = self._graph_for("drop", "reverse")
        sweep, _t2, _f2 = self._graph_for("drop", "sweep")
        fly_term = re.compile(r"overlay=x='[^']*pow\(min\(max\(\(t-")
        self.assertTrue(fly_term.search(sweep), "the control case has no travel term to compare")
        self.assertIsNone(fly_term.search(rev), "a mirrored exit is adding its own travel")


if __name__ == "__main__":
    unittest.main()
