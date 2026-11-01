"""The render half of a mask reveal: one baked, masked sprite per step of the sweep.

The preview uncovers a photo with a CSS mask; the export has to do the same thing
with filters, and it does it by putting the sweep's step into the same ladder that
already carries the sprite's size (see `size_levels()` in app/collage.py): a
per-frame `geq` over a full clip is what a NAS cannot afford, one still per step
is what it can. These tests read the graph text, so they run without FFmpeg.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from app.collage import (
    ENTRANCE_LENGTH,
    collage_duration,
    collage_graph,
    normalize_collage,
    photo_state,
)
from app.collage_reveal import REVEALS, REVEAL_STEPS, geq_alpha, reveal_quantise

W, H, FPS = 320, 180, 25
GEQ_HEAD = "geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':a='"


def _graph_lines(anim: str, photos: int = 2, hold: float = 1.1, **extra) -> str:
    spec_photos = [{"path": f"/photos/r{i}.jpg"} for i in range(photos)]
    for i, ph in enumerate(spec_photos):
        ph["anim"] = anim
    item = {"collage": {
        "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1,
        "hold": hold, "frame": {"shape": "none", "shadow": False},
        "photos": spec_photos, **extra,
    }}
    spec = normalize_collage(item)
    assert spec is not None, "the engine must accept a reveal id as an animation"
    duration = collage_duration(spec)
    lines, _last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
    return "".join(lines)


def _masked_geqs(graph: str) -> list[str]:
    """Each alpha expression the ladder baked into a sprite, in order."""
    out = []
    for m in re.finditer(re.escape(GEQ_HEAD), graph):
        end = graph.find("'", m.end())
        while end != -1 and graph[end - 1] == "\\":
            end = graph.find("'", end + 1)
        out.append(graph[m.end():end])
    return out


class RevealMaskLadderTest(unittest.TestCase):
    def test_a_reveal_masks_the_sprite_and_other_anims_do_not(self) -> None:
        for kind in REVEALS:
            with self.subTest(kind=kind):
                graph = _graph_lines(kind)
                self.assertIn(GEQ_HEAD, graph, f"{kind}: no mask in the graph at all")
                for expr in _masked_geqs(graph):
                    # Multiplying the mat's own alpha is what keeps a shaped
                    # frame's corners transparent through the sweep.
                    self.assertTrue(expr.startswith("alpha(X,Y)*"), expr[:40])
                    self.assertEqual(expr.count("("), expr.count(")"), f"unbalanced: {expr}")
        for anim in ("drop", "pop", "fade", "brush"):
            with self.subTest(anim=anim):
                self.assertNotIn(GEQ_HEAD, _graph_lines(anim))

    def test_one_level_per_step_of_the_sweep_and_no_more(self) -> None:
        # The ladder's steps must be exactly the quantised states photo_state
        # goes through — fewer and the export stutters against the preview,
        # more and it pays for sprites nobody sees.
        for kind in REVEALS:
            with self.subTest(kind=kind):
                length = ENTRANCE_LENGTH[kind]
                spec = normalize_collage({"collage": {
                    "layout": "grid", "animation": kind, "shape": "4:3", "seed": 1, "hold": 1.1,
                    "frame": {"shape": "none", "shadow": False},
                    "photos": [{"path": "/photos/r0.jpg", "anim": kind}],
                }})
                assert spec is not None
                steps: list[float] = []
                n_frames = max(1, int(round((length + 0.2) * FPS)))
                for k in range(n_frames + 1):
                    st = photo_state(spec, 0, k / FPS, 0.0, W / H)
                    p = st.get("revealP")
                    if p is None:
                        continue
                    mp = 1.0 if p >= 1.0 else reveal_quantise(REVEAL_STEPS, float(p))
                    if not steps or steps[-1] != mp:
                        steps.append(mp)
                wanted = [s for s in steps if s < 1.0]
                got = _masked_geqs(_graph_lines(kind, photos=1))
                self.assertEqual(len(got), len(wanted), f"{kind}: {len(got)} masked levels, {wanted}")
                # ascending, because the sweep only ever uncovers more
                nums = [float(re.findall(r"([0-9]*\.?[0-9]+)", e)[-1]) for e in got]
                self.assertEqual(nums, sorted(nums), f"{kind}: mask steps went backwards: {nums}")
                self.assertTrue(all(0 < v < 1 for v in nums), f"{kind}: a step escaped the sweep: {nums}")

    def test_each_masked_sprite_is_held_for_its_own_window(self) -> None:
        # An `out` kind tests with gte and an in kind with lte, so the operator
        # is the one place the two families differ; and every masked level has
        # to be switched on by a window, or the final state would stick.
        graph = _graph_lines("wipeUp", photos=1)
        self.assertIn("lte(", graph)
        self.assertNotIn("gte(", _masked_geqs(graph)[0])
        self.assertIn("gte(", _graph_lines("mosaicOut", photos=1))
        # Every masked step is bounded to its own frames, so the mask never runs
        # on a frame nobody sees, and its window is the same one the overlay
        # switches it on for.
        masked = [l for l in graph.split(";") if GEQ_HEAD in l]
        self.assertGreater(len(masked), 1)
        for line in masked:
            self.assertIn("select='", line, "a baked step is not bounded to its window")
            win = re.search(r"select='([^']+)'", line)
            assert win is not None
            name = re.search(r"\[(lv0_\d+)\]", line)
            assert name is not None
            overlay = [l for l in graph.split(";") if l.count(name.group(1)) == 1 and "overlay=" in l]
            self.assertTrue(overlay, name.group(1))
            self.assertIn(f"enable='{win.group(1)}'", overlay[0],
                          f"{name.group(1)}: mask window and overlay window differ")

    def test_the_direction_reaches_the_expression(self) -> None:
        a = geq_alpha("wipeLeft", 0.5, 0)
        b = geq_alpha("wipeLeft", 0.5, 1)
        self.assertNotEqual(a, b)
        # A mirrored wipe of a symmetric kind is that kind's own mirror, so the
        # graph has to carry a different test, not just a different label.
        self.assertIn("-", b.replace("--", "-") if "-" in b else b)

    def test_a_finished_sweep_carries_no_mask(self) -> None:
        # Once the whole photo is uncovered the ladder must drop the geq for the
        # level that holds the rest of the slide — otherwise every remaining
        # frame pays for a mask that does nothing.
        kind = "wipeUp"
        graph = _graph_lines(kind, photos=1, hold=3.0)
        lines = [l for l in graph.split(";") if l.strip()]
        final = [l for l in lines if l.endswith("[o0]")]
        self.assertEqual(len(final), 1, "photo 0's chain should close once")
        src = re.search(r"\[(lv0_(\d+))\]", final[0])
        assert src is not None
        level = [l for l in lines if l.endswith(f"[{src.group(1)}]")]
        self.assertEqual(len(level), 1, src.group(1))
        self.assertNotIn(GEQ_HEAD, level[0], "the resting level is still masked")
        self.assertIn("enable='gte(t", final[0], "the resting level should run to the end of the slide")
        # …and the levels before it were masked, or this proves nothing.
        self.assertGreater(len(_masked_geqs(graph)), 1)


def _ffmpeg_bin() -> str | None:
    env = os.environ.get("FFMPEG_BIN")
    if env and ((os.path.isabs(env) and os.path.exists(env)) or shutil.which(env)):
        return env
    return shutil.which("ffmpeg")


@unittest.skipUnless(_ffmpeg_bin(), "needs an ffmpeg binary (set FFMPEG_BIN)")
class RevealMaskFramesTest(unittest.TestCase):
    """The pixels, for whoever has FFmpeg: the composite must get brighter as the
    sweep runs and match an unmasked photo when it is over."""

    def test_the_mask_actually_uncovers_the_photo(self) -> None:
        ffmpeg = _ffmpeg_bin()
        assert ffmpeg is not None
        for kind in ("wipeUp", "irisOut", "mosaic"):
            with self.subTest(kind=kind):
                means = []
                for anim in (kind, "none"):
                    item = {"collage": {
                        "layout": "grid", "animation": anim, "shape": "4:3", "seed": 1, "hold": 1.1,
                        "frame": {"shape": "none", "shadow": False},
                        "photos": [{"path": "/photos/r0.jpg", "anim": anim}],
                    }}
                    spec = normalize_collage(item)
                    assert spec is not None
                    duration = collage_duration(spec)
                    lines, last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
                    graph = (f"[0:v]null[cb];{''.join(lines)}"
                             f"[{last}]format=gray,settb=AVTB,sendcmd=fps={FPS}[v]")
                    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                           "-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}:d={duration}",
                           "-f", "lavfi", "-i", f"color=c=white:s=180x120:r={FPS}:d={duration}",
                           "-filter_complex", graph, "-map", "[v]", "-f", "rawvideo",
                           "-pix_fmt", "gray", "-"]
                    proc = subprocess.run(cmd, capture_output=True, timeout=240)
                    self.assertEqual(proc.returncode, 0, proc.stderr[-400:].decode("utf-8", "replace"))
                    frames = [proc.stdout[i:i + W * H] for i in range(0, len(proc.stdout) - W * H + 1, W * H)]
                    n = len(frames)
                    means.append([sum(f) / (W * H) for f in frames])
                sweep, plain = means
                at = lambda series, frac: series[min(len(series) - 1, int(len(series) * frac))]  # noqa: E731
                self.assertLess(at(sweep, 0.15), at(sweep, 0.85) * 0.85, f"{kind}: barely changes while sweeping")
                self.assertGreater(at(sweep, 0.99), at(plain, 0.99) * 0.85, f"{kind}: never finishes uncovered")
                self.assertGreater(at(plain, 0.2), 0, "the unmasked control is blank — the graph is wrong")


if __name__ == "__main__":
    unittest.main()
