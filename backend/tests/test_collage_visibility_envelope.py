"""The visibility envelope: a photo starts invisible and ends invisible.

Nothing may show a photo before its arrival has begun or after its departure is
over — in the preview that is the sampled state's opacity, and in the MP4 it is the
overlay's own window, because FFmpeg's `fade=t=in` passes the frames *before* its
start through untouched at full opacity. Both halves are asserted here in both
engines: `photo_state` for the preview, the generated graph for the render. The
measured-pixel version of the same two claims is
`test_a_photo_is_absent_before_it_arrives_and_after_it_leaves` in
test_collage_animation_ffmpeg.py.

Runs without ffmpeg; nothing here needs it.
"""
from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.collage import (ANIM_IDS, EXITS, base_duration, collage_duration, collage_graph,  # noqa: E402
                         entrance_length, exit_animation, exit_fly_length, exit_mode,
                         exit_offset, is_reveal, photo_start, photo_state, resolve_anim)
from app.collage_reveal import REVEALS  # noqa: E402

W, H, FPS = 640, 360, 25.0
N = 3
# the tail of the envelope is only observable while the slide still runs, so the
# hold has to outlast a staggered exit
HOLD = 2.5


def _spec(animation=None, exit=None, layout='grid', photos=None, **extra):
    ph = [{'path': f'/p{i}.jpg'} for i in range(N)]
    for i, p in enumerate(photos or {}):
        ph[i].update(photos[i])
    spec = {'layout': layout, 'shape': '16:9', 'seed': 5, 'hold': HOLD, 'photos': ph}
    if animation:
        spec['animation'] = animation
    if exit:
        spec['exit'] = exit
    spec.update(extra)
    return spec


def _ex_end(spec, i) -> float:
    return base_duration(spec) + exit_offset(spec, i) + exit_fly_length(spec, i)


def _mask_closed(spec, i, t) -> bool:
    rp = photo_state(spec, i, t, 0.0, 16 / 9).get('revealP')
    return rp is not None and rp <= 1e-9


def _graph(spec):
    dur = collage_duration(spec)
    return ''.join(collage_graph({'collage': spec}, W, H, FPS, dur, 0.0, 1, 'cb')[0]), dur


def _windows(graph: str, i: int) -> list[tuple[float, float | None]]:
    """Every (start, end) photo i is composited on, across its size-ladder levels.
    `end is None` is the open-ended form, which runs to the end of the segment; a
    level with no `enable` at all is always on, which for a photo means: from its
    first frame, since that is what the builder emits when there is nothing to cut."""
    out: list[tuple[float, float | None]] = []
    # one filter segment at a time: the builder writes a photo's whole chain on one
    # line, so a line-level match handed photo 0's window to photo 1's assertions.
    # `[lv0]` (a photo that never changes size has a single level) is as much this
    # photo's overlay as `[lv0_2]` is.
    for seg in re.split(r"[;\n]", graph):
        if not re.match(rf"^\[[^\]]*\]\[lv{i}(?:_\d+)?\]overlay=", seg):
            continue
        en = re.search(r":enable='([^']*)'", seg)
        if not en:
            out.append((0.0, None))
            continue
        for part in en.group(1).split('+'):
            both = re.match(r"between\(t,([-0-9.eE+]+),([-0-9.eE+]+)\)", part)
            from_on = re.match(r"gte\(t,([-0-9.eE+]+)\)", part)
            if both:
                out.append((float(both.group(1)), float(both.group(2))))
            elif from_on:
                out.append((float(from_on.group(1)), None))
    return out


class PreviewTest(unittest.TestCase):
    """The state the stage paints from — and that the MP4 samples its levels at."""

    def test_nothing_is_on_screen_before_the_arrival(self):
        for name in ANIM_IDS:
            spec = _spec(animation=name)
            for i in range(N):
                t0 = photo_start(spec, i)
                self.assertGreater(t0, 0.0, f'{name}: the layout must stagger')
                for t in (0.0, t0 / 2, t0 - 0.02):
                    self.assertEqual(photo_state(spec, i, t, 0.0, 16 / 9)['alpha'], 0.0,
                                     (name, i, round(t, 3)))
                    if is_reveal(resolve_anim(name)):
                        # a mask reveal hides itself, and says so outside its own
                        # window as well: two reasons, one promise
                        self.assertTrue(_mask_closed(spec, i, t), (name, i, t))

    def test_nothing_is_on_screen_after_the_departure(self):
        for name in [e for e in EXITS if e != 'none'] + list(ANIM_IDS):
            spec = _spec(exit=name)
            for i in range(N):
                if exit_mode(spec, i) == 'none':
                    continue
                end, dur = _ex_end(spec, i), collage_duration(spec)
                if end >= dur - 0.05:
                    continue                      # the last photo's fly ends with the slide
                for t in (end, end + 0.05, dur - 0.02):
                    self.assertEqual(photo_state(spec, i, t, 0.0, 16 / 9)['alpha'], 0.0,
                                     (name, i, round(t, 3)))
                if is_reveal(resolve_anim(name)) and exit_animation(spec, i):
                    # a closing mask is the departure, so it has to be shut for good
                    self.assertTrue(_mask_closed(spec, i, end + 0.02), (name, i))

    def test_a_photo_that_stays_on_stays_to_the_last_frame(self):
        # …or the envelope would just be "photos vanish at the end of the slide",
        # the same bug pointed the other way
        spec = _spec(animation='drop', exit='sweep', photos=[{'exit': 'none'}])
        self.assertEqual(exit_mode(spec, 0), 'none')
        dur = collage_duration(spec)
        t0 = photo_start(spec, 0) + entrance_length(spec, 0) + 0.1
        for t in (t0, dur / 2, dur - 0.02):
            self.assertEqual(photo_state(spec, 0, t, 0.0, 16 / 9)['alpha'], 1.0, t)
        self.assertEqual(photo_state(spec, 1, dur - 0.02, 0.0, 16 / 9)['alpha'], 0.0)

    def test_the_interior_is_untouched(self):
        # an envelope that zeroed everything would pass both tests above, so: the
        # middle of an arrival is partly there, and after it the photo is whole
        for name in ('drop', 'fade', 'slide', 'pop', 'wipeUp', 'iris', 'glitch'):
            spec = _spec(animation=name)
            for i in range(N):
                t0, ent = photo_start(spec, i), entrance_length(spec, i)
                self.assertGreater(t0, 0.0, name)
                mid = photo_state(spec, i, t0 + 0.5 * ent, 0.0, 16 / 9)
                if is_reveal(resolve_anim(name)):
                    self.assertGreater(mid['revealP'], 0.05, (name, i))
                else:
                    self.assertGreater(mid['alpha'], 0.05, (name, i))
                landed = photo_state(spec, i, t0 + ent + 0.1, 0.0, 16 / 9)
                self.assertEqual(landed['alpha'], 1.0, (name, i))


class TileSampleTest(unittest.TestCase):
    """The animation browser's tiles are one-photo collages sampled through this same
    model, with `hold: 0` so the departure starts as early as the engine allows. That
    is only honest while the demo photo's own arrival is already over — `base_duration`
    counts a `none` arrival as instant, so a default per-photo delay would put the fly
    before the photo exists, and the envelope would (correctly) draw nothing: an empty
    tile instead of a preview. `src/AnimPicker.tsx` sets `delay: 0` for that reason, and
    this is the number that pins."""

    def _tile(self, name: str, out: bool) -> dict:
        photo = {'src': '', 'delay': 0, 'exit': name} if out else {'src': '', 'delay': 0, 'anim': name}
        spec = {'layout': 'grid', 'cols': 1, 'rows': 1, 'shape': '4:3', 'seed': 5, 'frame': 'none',
                'animDir': 0, 'photos': [photo], 'animation': name if not out else
                ('drop' if name == 'reverse' else 'none')}
        if out:
            spec['hold'] = 0
        return spec

    def test_every_sample_a_tile_draws_is_on_screen(self):
        for name in list(ANIM_IDS) + [e for e in EXITS if e != 'none']:
            for out in (False, True):
                spec = self._tile(name, out)
                if not out and name in EXITS and name != 'reverse':
                    continue                      # the arrival lane does not hold the modes
                fly = exit_fly_length(spec, 0) if out else entrance_length(spec, 0)
                pre = base_duration(spec) if out else 0.0
                length = fly or entrance_length(spec, 0) or 0.6
                idle = pre if out else pre + length + 0.2
                for label, t in (('idle', idle), ('start', pre), ('mid', pre + 0.5 * length),
                                 ('end', pre + length)):
                    # the two frames an envelope may legitimately empty: an arrival's
                    # first, where it is transparent by design, and a departure's last,
                    # where it has just finished leaving
                    if label == 'end' or (label == 'start' and not out):
                        continue
                    st = photo_state(spec, 0, max(0.0, t), 0.0, 16 / 9)
                    # at rest the tile owes a *whole* photo (that is what "here is the
                    # animation you are about to pick" means), and mid-fly it only owes
                    # one that is not blank — a fade-out at its halfway point is half
                    # gone, and that is the animation, not an empty square
                    floor = 0.5 if label in ('idle', 'start') else 0.05
                    visible = st['alpha'] > floor or (st.get('revealP') or 0.0) > floor
                    self.assertTrue(visible, (name, 'out' if out else 'in', label, round(t, 3),
                                              round(st['alpha'], 2), round(st.get('revealP', 1.0), 2)))


class GraphTest(unittest.TestCase):
    """The expressions the MP4 is built from, clipped at both ends."""

    def test_the_overlay_starts_when_the_photo_arrives(self):
        for name in ('drop', 'none', 'fade', 'pop', 'iris', 'wipeUp', 'glitch', 'swing',
                     'toColor', 'brush'):
            spec = _spec(animation=name)
            graph, _dur = _graph(spec)
            for i in range(N):
                win = _windows(graph, i)
                self.assertTrue(win, name)
                # one frame of tolerance, on purpose: a level boundary is padded by
                # 3 ms so that a frame whose time lands exactly on it matches *some*
                # window (without the pad the photo blinked out for a frame), and the
                # frames the pad lets in are inside the fade-in, where the photo is
                # transparent. What must not happen is the old behaviour — a window
                # opening at frame 0 for a photo whose arrival is a second later.
                self.assertGreaterEqual(min(a for a, _b in win), photo_start(spec, i) - 1.0 / FPS,
                                        f'{name}/photo {i}: composited before it arrives')

    def test_the_overlay_ends_when_the_photo_leaves(self):
        for name in ('sweep', 'deal', 'shuffle', 'fade', 'reverse', 'wipeUp', 'iris', 'rise'):
            spec = _spec(exit=name)
            graph, _dur = _graph(spec)
            for i in range(N):
                end = _ex_end(spec, i)
                for a, b in _windows(graph, i):
                    self.assertIsNotNone(b, (name, i, 'still open past the departure'))
                    self.assertLessEqual(b, end + 0.004, (name, i, a, b))

    def test_a_photo_with_no_exit_runs_to_the_end(self):
        spec = _spec(animation='drop')
        graph, dur = _graph(spec)
        for i in range(N):
            ends = [b for _a, b in _windows(graph, i)]
            self.assertTrue(any(b is None or b >= dur - 0.01 for b in ends), (i, ends))

    def test_the_photo_that_arrives_at_zero_is_untouched(self):
        # with no lead-in and no delay there is nothing to clip, and the envelope
        # must not have invented a window where none was needed
        spec = _spec(animation='none', layout='free', photos=[
            {'delay': 0, 'cx': 25, 'cy': 50, 'w': 30},
            {'delay': 0, 'cx': 75, 'cy': 50, 'w': 30}])
        graph, _dur = _graph(spec)
        self.assertEqual(photo_start(spec, 0), 0.0)
        self.assertTrue(any(a <= 1e-9 for a, _b in _windows(graph, 0)), _windows(graph, 0))

    def test_the_ladder_still_carries_every_step_of_a_closing_mask(self):
        # the clip drops the frames before the arrival; the steps of the sweep have
        # to survive it, or the exit silently becomes a hard cut again
        for name in REVEALS[:4]:
            spec = _spec(animation='none', exit=name)
            graph, _dur = _graph(spec)
            self.assertGreaterEqual(graph.count('geq='), 4, name)
            self.assertNotIn('fade=t=out', graph, name)


if __name__ == '__main__':
    unittest.main(verbosity=2)
