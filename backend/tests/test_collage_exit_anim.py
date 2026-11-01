"""A photo's departure may be any arrival in the catalogue, read backwards.

The exit used to be six buttons — stay, sweep, deal, scatter, fade, reverse — while the
entrance had grown to almost a hundred animations. Rather than write a departure per
arrival (which is how a catalogue ends up with a *second*, smaller, staler list), an
exit is stored as the animation to mirror: `exit: 'iris'` means "un-iris", and
`'reverse'` is the special case where a photo mirrors its own arrival. One curve per
animation in each engine therefore serves both directions, so adding an entrance adds
an exit at the same moment and the two lists cannot drift apart.

What that claim costs is what this file checks:

* every id in the catalogue is legal as an exit *and* has a length — a name missing
  from `ENTRANCE_LENGTH` would give a zero-second fly and a photo that never leaves;
* the mirrored curve is the arrival's curve rather than an approximation of it: at the
  fly's first frame the photo is exactly at rest, and at its last it is exactly where
  that animation starts it;
* opacity only ever *falls* during a departure. A photo brightening on its way out was
  a real defect of the 'reverse' mirror, and the render's `fade=t=out` never had it;
* the generated FFmpeg expressions add the mirrored arrival's own terms when a
  departure names one, and do NOT for 'reverse' — which would be the same exit twice;
* a mask reveal used as an exit closes its mask and fades nothing.

`test_collage_animation_ffmpeg.py` renders the same ideas through real frames; this is
the part that has to hold even where ffmpeg is not installed.
"""
from __future__ import annotations

import math
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.collage import (  # noqa: E402
    ANIM_IDS,
    ENTRANCE_FADE,
    EXIT_STAGGER,
    ENTRANCE_LENGTH,
    EXITS,
    base_duration,
    collage_duration,
    collage_graph,
    exit_animation,
    exit_choice,
    exit_fly_length,
    exit_mode,
    exit_offset,
    is_exit_choice,
    is_reveal,
    photo_anim,
    photo_start,
    photo_state,
    resolve_anim,
)
from app.collage_reveal import REVEALS  # noqa: E402

FRAME_W, FRAME_H, FRAME_FPS = 640, 360, 25.0


def _spec(exit_name, animation="fade", **extra):
    spec = {"photos": [{"path": "/photos/p0.jpg"}, {"path": "/photos/p1.jpg"}],
            "layout": "grid", "animation": animation, "shape": "4:3", "seed": 5, "hold": 1.0}
    if exit_name is not None:
        spec["exit"] = exit_name
    spec.update(extra)
    return spec


def _leaving_ids():
    return [n for n in ANIM_IDS if n != "none"]


def _exit_times(spec, i=0, count=17):
    """Sample points across photo i's fly — from the moment *that* photo starts leaving
    to the moment it is gone, computed with the same helpers the engines use so the
    samples cannot drift out of the window when a length or the stagger changes."""
    start = base_duration(spec) + exit_offset(spec, i)
    fly = exit_fly_length(spec, i) or 0.001
    return [start + fly * k / (count - 1) for k in range(count)]


class ExitDomainTest(unittest.TestCase):
    def test_every_entrance_name_is_a_legal_exit(self):
        """One catalogue, two lanes: anything the entrance browser can write is accepted
        as a departure — the only reason an exit list cannot age badly."""
        bad = [name for name in ANIM_IDS if not is_exit_choice(name)]
        self.assertEqual(bad, [], "these entrance names are refused as exits")
        for name in EXITS:
            self.assertTrue(is_exit_choice(name), name)
        for junk in ("", None, 3, "notAnAnimation", "toBw", "drift", []):
            self.assertFalse(is_exit_choice(junk), repr(junk))

    def test_a_mirrored_exit_reports_itself_as_reverse(self):
        # every consumer that asks "does this photo fly somewhere of its own?" gets the
        # answer 'no' for a mirrored departure — the stagger, the size ladder, the pin
        # anchor for a swing and the graph's fade window all come from this one answer
        for name in _leaving_ids():
            spec = _spec(name)
            if name in EXITS[1:]:
                # 'fade' is the one name that means something else on the exit lane,
                # and it has meant "fade out" for longer than the catalogue has
                # existed: the mode wins, and both answers do nearly the same thing
                self.assertEqual(exit_mode(spec, 0), name, name)
                continue
            self.assertEqual(exit_mode(spec, 0), "reverse", name)
            self.assertEqual(exit_choice(spec, 0), name, name)
            self.assertEqual(exit_animation(spec, 0), resolve_anim(name), name)

    def test_the_travel_modes_keep_their_own_names(self):
        for mode in EXITS[1:]:
            spec = _spec(mode)
            self.assertEqual(exit_mode(spec, 0), mode)
            self.assertEqual(exit_choice(spec, 0), mode)
            if mode == "reverse":
                # the mirror needs something to mirror, and that is the photo's own
                # arrival — resolved, so a bridged name mirrors the animation behind it
                self.assertEqual(exit_animation(spec, 0), photo_anim(spec, 0))
            elif mode == "fade":
                # …and the collision above: naming the arrival 'fade' as an exit is the
                # mode, so no curve is mirrored at all
                self.assertEqual(exit_animation(spec, 0), "")
            else:
                self.assertEqual(exit_animation(spec, 0), "", mode)

    def test_no_exit_is_no_fly(self):
        for value in (None, "none", {"weird": 1}, [1], "notAnAnimation"):
            spec = _spec(value)
            self.assertEqual(exit_mode(spec, 0), "none", repr(value))
            self.assertEqual(exit_fly_length(spec, 0), 0.0, repr(value))


class MirroredFlyLengthTest(unittest.TestCase):
    def test_every_animation_supplies_a_real_length(self):
        """A name with no row in ENTRANCE_LENGTH would exit in zero seconds: the photo
        simply stops being drawn, in the preview and in the MP4 alike."""
        missing = [n for n in _leaving_ids() if not ENTRANCE_LENGTH.get(resolve_anim(n))]
        self.assertEqual(missing, [], "these ids have no arrival length to mirror")
        self.assertTrue(all(math.isfinite(exit_fly_length(_spec(n), 0)) for n in _leaving_ids()))

    def test_the_fly_is_the_arrivals_length_at_exit_speed(self):
        for name in ("iris", "drop", "swing", "slideleft", "glitch"):
            want = ENTRANCE_LENGTH[resolve_anim(name)]
            self.assertAlmostEqual(exit_fly_length(_spec(name), 0), want, places=6, msg=name)
            for speed in (0.25, 2.0):
                got = exit_fly_length(_spec(name, exitSpeed=speed), 0)
                self.assertAlmostEqual(got, want / speed, places=6, msg=(name, speed))
        # 'reverse' is the exception on purpose: it predates the shared catalogue and
        # keeps its own number, so no existing slide changes length when it reloads.
        self.assertAlmostEqual(exit_fly_length(_spec("reverse"), 0), 0.5, places=6)

    def test_the_seconds_reach_the_slide(self):
        # the slide grows by the last photo's stagger plus the fly it mirrors — the
        # exit's own schedule, not a guess at it
        spec = _spec("iris")
        n = len(spec["photos"])
        want = EXIT_STAGGER["reverse"] * (n - 1) + ENTRANCE_LENGTH["iris"]
        self.assertAlmostEqual(collage_duration(spec) - base_duration(spec), want, places=6)
        # and a photo that stays on adds nothing for itself
        mixed = _spec("iris")
        mixed["photos"][1]["exit"] = "none"
        self.assertAlmostEqual(exit_fly_length(mixed, 1), 0.0)


class MirroredCurveTest(unittest.TestCase):
    """The departure IS the arrival, backwards — the same sampled numbers."""

    def test_the_whole_curve_is_the_arrival_reversed(self):
        """For every id in the catalogue, sampling the departure at progress q has to
        equal sampling that same animation as an arrival at 1 - q — one table, read in
        both directions, with nothing between them but the window."""
        for name in _leaving_ids():
            if name == "fade":
                continue                      # the exit name means the mode, see above
            leaving = _spec(name, animation="none")
            arriving = _spec(None, animation=name)
            for i in range(2):
                t0e = base_duration(leaving) + exit_offset(leaving, i)
                t0a = photo_start(arriving, i)
                fly = exit_fly_length(leaving, i)
                self.assertGreater(fly, 0.0, name)
                for k in range(1, 13):
                    q = k / 12.0
                    out = photo_state(leaving, i, t0e + fly * q)
                    back = photo_state(arriving, i, t0a + fly * (1.0 - q))
                    self.assertEqual(out.get("revealKind"), back.get("revealKind"), (name, i, q))
                    for key in ("dx", "dy", "rot", "scale", "scaleX", "dim", "revealP"):
                        if key in ("revealP",) and key not in out and key not in back:
                            continue                      # neither side masks at all
                        a, b = out.get(key, 0.0), back.get(key, 0.0)
                        self.assertAlmostEqual(float(a), float(b), places=6, msg=(name, i, q, key))
                # the frame the fly starts on is the photo at rest, and its last is the
                # animation's own starting pose: the two ends of the sentence above
                rest = photo_state(leaving, i, t0e)
                landed = photo_state(arriving, i, t0a + fly * 4.0)
                for key in ("dx", "dy", "rot", "scale", "scaleX"):
                    # a damped pendulum never returns to *exactly* zero, so the frame a
                    # swing leaves on sits a hair off its landing angle — visible? no.
                    self.assertAlmostEqual(rest[key], landed[key], delta=0.01, msg=(name, i, "rest", key))
                gone = photo_state(leaving, i, t0e + fly)
                start_pose = photo_state(arriving, i, t0a)
                for key in ("dx", "dy", "rot", "scale", "scaleX"):
                    self.assertAlmostEqual(gone[key], start_pose[key], places=6, msg=(name, i, "gone", key))
                # an arrival with no alpha ramp of its own needs the exit's fade to
                # finish the job; a closing mask is already invisible at the end
                if not is_reveal(resolve_anim(name)):
                    self.assertLess(gone["alpha"], 0.05, name)

    def test_opacity_only_falls(self):
        """The regression this feature had to fix: the 'reverse' mirror used to overwrite
        the opacity with a linear fade whenever the sampled alpha happened to still be 1,
        so a drop-style photo went 1 → 0.5 → 1 → 0 on its way out. The render never did
        that (its `fade=t=out` is one window), so the preview was the odd one out."""
        for name in _leaving_ids():
            if name == "fade":
                continue
            spec = _spec(name)
            alphas = [photo_state(spec, 0, t)["alpha"] for t in _exit_times(spec)]
            for a, b in zip(alphas, alphas[1:]):
                self.assertLessEqual(b, a + 1e-9, (name, a, b))
            self.assertGreater(alphas[0], 0.98, name)
            if not is_reveal(resolve_anim(name)):
                # a closing mask is the exception on purpose: its own shape is the
                # opacity, and the render applies no `fade=t=out` to it either
                self.assertLess(alphas[-1], 0.02, name)

    def test_a_reveal_exit_closes_its_mask(self):
        for name in REVEALS:
            spec = _spec(name, animation="none")
            states = [photo_state(spec, 0, t) for t in _exit_times(spec)][1:]
            progress = [st["revealP"] for st in states]
            self.assertEqual({st["revealKind"] for st in states}, {name}, name)
            for a, b in zip(progress, progress[1:]):
                self.assertLessEqual(b, a + 1e-9, (name, a, b))
            self.assertGreater(progress[0], 0.9, name)
            self.assertLess(progress[-1], 0.1, name)
            # the mask IS the opacity here: nothing else may move …
            for st in states[:-1]:
                self.assertAlmostEqual(st["dx"], 0.0, places=9, msg=name)
                self.assertAlmostEqual(st["alpha"], 1.0, places=9, msg=name)
            # …and once the fly is over the photo is gone for good, which the model
            # now says out loud because the render enforces it by never compositing
            # the frame at all (see test_collage_visibility_envelope.py)
            self.assertEqual(states[-1]["alpha"], 0.0, name)
            self.assertLessEqual(states[-1]["revealP"], 1e-9, name)


class GraphTest(unittest.TestCase):
    """The expressions, not just the sampled model — the MP4 is built from these."""

    def _graph(self, spec):
        dur = collage_duration(spec)
        lines, _last = collage_graph({"collage": spec}, FRAME_W, FRAME_H, FRAME_FPS, dur, 0.0, 1, "cb")
        self.assertIsNotNone(lines)
        return "".join(lines or [])

    def test_a_named_exit_adds_that_arrivals_terms(self):
        graph = self._graph(_spec("rise", animation="drop"))
        # rise's own offset (0.22 of the frame height) carrying the mirrored progress:
        # `pow(1-<1 - the exit window>,3)` is a shape only an added *second* curve has
        self.assertIn(f"+{0.22 * FRAME_H:.4g}".rstrip("0").rstrip("."), graph)
        self.assertIn("pow(1-(1-min(max((t-", graph)
        # …and not the unparenthesised form, which ffmpeg evaluates as pow(-q,3): the
        # same curve with its direction flipped, so a photo that leaves the way it
        # arrived would leave upwards instead of back down
        self.assertNotIn("pow(1-1-min(max((t-", graph)
        # …and the photo's own arrival is untouched by it: a plain progress term, with
        # no mirror folded in the way 'reverse' folds one
        self.assertIn("pow(1-min(max((t-", graph)
        self.assertNotIn("pow(1-min(min(max((t-", graph)

    def test_a_reversing_photo_gets_its_terms_once(self):
        # same animation on both lanes, asked for as 'reverse': ONE progress, folded
        # with min(), and therefore no `1-min(max(` term of its own anywhere
        own = self._graph(_spec("reverse", animation="rise"))
        self.assertIn("pow(1-min(min(max((t-", own)
        self.assertNotIn("pow(1-(1-min(max((t-", own)
        # …asked for by name: the arrival keeps its plain progress and the departure
        # adds exactly one mirrored term — the same number of characters, one term
        # …and an exit that names the arrival it already performs IS 'reverse', down to
        # the last character: no second term is added for the same curve twice
        named = self._graph(_spec("rise", animation="rise"))
        self.assertEqual(named, own)

    def test_the_alpha_window_follows_the_mirrored_arrival(self):
        # rise fades in over part of its arrival, so it fades out over the same
        # fraction of its fly — not the whole fly, and not the movers' last quarter
        spec = _spec("rise")
        fly = exit_fly_length(spec, 0)
        ratio = ENTRANCE_FADE["rise"] / ENTRANCE_LENGTH["rise"]
        window = re.search(r"fade=t=out:st=([\d.]+):d=([\d.]+)", self._graph(spec))
        self.assertIsNotNone(window, "no fade window on the leaving photo")
        self.assertAlmostEqual(float(window.group(2)), fly * ratio, delta=0.01)
        self.assertLessEqual(float(window.group(1)) + float(window.group(2)) - (base_duration(spec) + fly),
                             0.011)

    def test_a_photo_that_never_faded_leaves_over_the_whole_fly(self):
        # 'none' as the arrival with a mirrored departure: there is no ramp to read
        # backwards, so the fade has to be the fly itself — in the model and in the
        # `fade=t=out` the render is handed, which is the pair this feature keeps in step
        spec = _spec("reverse", animation="none")
        fly = exit_fly_length(spec, 0)
        states = [photo_state(spec, 0, t)["alpha"] for t in _exit_times(spec)]
        self.assertAlmostEqual(states[0], 1.0, places=6)
        self.assertLess(states[-1], 1e-9)
        mid = states[len(states) // 2]
        self.assertAlmostEqual(mid, 1 - 0.5, places=1, msg="a straight line, like the filter")
        window = re.search(r"fade=t=out:st=([\d.]+):d=([\d.]+)", self._graph(spec))
        self.assertIsNotNone(window)
        self.assertAlmostEqual(float(window.group(2)), fly, delta=0.01)

    def test_a_reveal_exit_fades_nothing(self):
        # the mask closing IS the departure; a second fade would dim an already
        # transparent frame and would be visible as a grey haze over the exit
        for name in REVEALS[:4]:
            graph = self._graph(_spec(name, animation="none"))
            self.assertNotIn("fade=t=out", graph, name)
            self.assertIn("geq=", graph, name)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
