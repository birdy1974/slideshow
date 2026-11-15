"""The render half of a mask reveal: a masked sprite per step the photo shows.

The preview uncovers a photo with a CSS mask; the export has to do the same thing
with filters, and it does it by putting the sweep's step into the same ladder that
already carries the sprite's size (see `size_levels()` in app/collage.py): a
per-frame `geq` over a full clip is what a NAS cannot afford, one still per step
is what it can — and where one size serves several steps, `geq` is handed the
frame's own time and works the step out itself, which is a level per size rather
than a level per step (see `reveal_step_expr`).

What these tests guard is therefore not the count of sprites but the *steps*: for
every frame of the sweep, the mask that is on has to be the one the preview shows
at that frame. That is read back out of the emitted graph text — evaluated, not
re-derived from the Python it came from — so a graph that lies still fails here.

The tests read the graph text, so they run without FFmpeg.
"""
from __future__ import annotations

import math
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
    photo_start,
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


def _quoted(text: str, start: int) -> str:
    """A `'…'` value, honouring the backslash escapes FFmpeg's filter syntax needs."""
    end = text.find("'", start)
    while end != -1 and text[end - 1] == "\\":
        end = text.find("'", end + 1)
    return text[start:end]


def _masked_geqs(graph: str) -> list[str]:
    """Each alpha expression the ladder put into a sprite, in order."""
    return [_quoted(graph, m.end()) for m in re.finditer(re.escape(GEQ_HEAD), graph)]


def _mask_test(graph: str) -> str | None:
    """The operator of the first mask test in the graph, `lte` or `gte`."""
    for expr in _masked_geqs(graph):
        head, _, rest = expr.partition("*")
        if head == "alpha(X,Y)":
            return rest.split("(", 1)[0]
    return None


def _windows(cond: str) -> list[tuple[float, float | None]]:
    """`between(t,A,B)+between(t,C,D)` and friends, as (start, end) pairs."""
    out: list[tuple[float, float | None]] = []
    for a, b, c in re.findall(r"between\(t,([-0-9.]+),([-0-9.]+)\)|gte\(t,([-0-9.]+)\)", cond):
        out.append((float(a or c), float(b) if b else None))
    return out


def _masked_levels(graph: str) -> list[tuple[list[tuple[float, float | None]], str]]:
    """Every masked sprite the ladder built: the windows it is switched on for,
    and its alpha expression. The two come from the same list of windows, so a
    level's `select` is read here and checked against its `enable` elsewhere."""
    out = []
    for line in graph.split(";"):
        at = line.find(GEQ_HEAD)
        if at < 0:
            continue
        sel = re.search(r"select='([^']+)'", line)
        out.append((_windows(sel.group(1)) if sel else [],
                    _quoted(line, at + len(GEQ_HEAD))))
    return out


CALL_RE = re.compile(r"([A-Za-z_][A-Za-z_0-9]*)\(")


def _match(text: str, i: int) -> int:
    """The index just past the `(` at `i`'s matching `)`."""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise AssertionError(f"unbalanced parentheses in: {text[:80]}")


def _split_top(inner: str) -> list[str]:
    """A function's argument list, split on the commas that are not nested."""
    out, cur, depth = [], "", 0
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


def _unwrap(expr: str) -> str:
    """`((x))` to `x` — the generated expressions wrap themselves in spare pairs."""
    expr = expr.strip()
    while expr.startswith("(") and _match(expr, 0) == len(expr) - 1:
        expr = expr[1:-1].strip()
    return expr


#: FFmpeg's expression functions that have no Python spelling of their own
OPS = {"if": lambda a, f: f"({f(a[1])} if {f(a[0])} else {f(a[2])})",
       "gte": lambda a, f: f"({f(a[0])} >= {f(a[1])})",
       "lte": lambda a, f: f"({f(a[0])} <= {f(a[1])})",
       "gt": lambda a, f: f"({f(a[0])} > {f(a[1])})",
       "lt": lambda a, f: f"({f(a[0])} < {f(a[1])})"}
MATHS = ("floor", "min", "max", "abs", "hypot", "sqrt", "pow")


def _to_python(expr: str) -> str:
    """An FFmpeg expression as the same expression in Python, anywhere in it."""
    out, i, expr = [], 0, expr.strip()
    while i < len(expr):
        m = CALL_RE.match(expr, i)
        if not m:
            out.append(expr[i])
            i += 1
            continue
        end = _match(expr, m.end() - 1)
        args = _split_top(expr[m.end():end])
        name = m.group(1)
        if name in OPS:
            out.append(OPS[name](args, _to_python))
        elif name in MATHS:
            out.append(f"{name}({', '.join(_to_python(a) for a in args)})")
        else:
            raise AssertionError(f"the graph uses a function this test cannot read: {name}")
        i = end + 1
    return "".join(out)


def _mask_operand(alpha_expr: str) -> str:
    """What the mask test is compared against: a literal step, or `T`'s arithmetic.

    A kind that closes rather than opens masks the complement of the sweep's
    progress, so this is the number the step has to reach, not the progress itself.
    """
    head, _, rest = alpha_expr.partition("*")
    assert head == "alpha(X,Y)", f"unexpected mask: {alpha_expr[:40]}"
    rest = _unwrap(rest)
    return _split_top(rest[rest.index("(") + 1:_match(rest, rest.index("("))])[-1]


def _eval_operand(alpha_expr: str, t: float) -> float:
    """Evaluate an emitted mask test's operand at time `t`, reading the graph text.

    Both shapes are evaluated the same way — a baked level's operand is a literal
    and a merged one's is an expression in `T` — and neither is recomputed from the
    Python that generated it, which is the point: the graph is what the render runs.
    """
    return float(eval(_to_python(_mask_operand(alpha_expr)), {"__builtins__": {}},  # noqa: S307
                      {"T": t, "min": min, "max": max, "abs": abs, "floor": math.floor,
                       "hypot": math.hypot, "sqrt": math.sqrt, "pow": pow}))


def _twin_steps(kind: str, photos: int = 1, hold: float = 1.1) -> dict[int, float]:
    """The steps the preview goes through, frame by frame across the sweep.

    Sampled the way `size_levels()` samples it — the same quantising constant, the
    same frame grid — because that is what the two are required to agree on.
    """
    length = ENTRANCE_LENGTH[kind]
    spec = normalize_collage({"collage": {
        "layout": "grid", "animation": kind, "shape": "4:3", "seed": 1, "hold": hold,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": f"/photos/r{i}.jpg", "anim": kind} for i in range(photos)],
    }})
    assert spec is not None
    # the photo is not on screen before its own start, and a frame that shows
    # nothing has no mask to agree about
    first = max(0, math.ceil(photo_start(spec, 0, 0.0) * FPS - 0.001))
    want: dict[int, float] = {}
    for k in range(first, max(1, int(round((length + 0.2) * FPS))) + 1):
        # sampled through the engine's own state, not a re-implementation of it:
        # `size_levels()` builds the ladder from this same call
        p = photo_state(spec, 0, k / FPS, 0.0, W / H).get("revealP")
        if p is None or p >= 1.0:
            continue
        want[k] = reveal_quantise(REVEAL_STEPS, float(p))
    return want


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

    def test_every_frame_of_the_sweep_shows_the_step_the_preview_shows(self) -> None:
        # Fewer steps and the export stutters against the preview; more and it pays
        # for masks nobody sees. Whether a step is a held still of its own or a
        # value `geq` works out from `T` is an economy, not a contract, so this
        # asks the frames rather than counting the levels.
        for kind in REVEALS:
            with self.subTest(kind=kind):
                wanted = _twin_steps(kind)
                self.assertGreater(len(wanted), 3, f"{kind}: the control sweep has no steps")
                levels = _masked_levels(_graph_lines(kind, photos=1))
                self.assertTrue(levels, f"{kind}: nothing is masked in the graph at all")
                for k, want in sorted(wanted.items()):
                    t = k / FPS
                    on = [(win, expr) for win, expr in levels
                          if any(a - 1e-6 <= t <= (b + 1e-6 if b is not None else 1e9)
                                 for a, b in win)]
                    self.assertEqual(len(on), 1,
                                     f"{kind} frame {k}: {len(on)} masked levels are on, want one")
                    got = _eval_operand(on[0][1], t)
                    # …and the expected value goes through the same evaluation,
                    # because a kind that closes writes its operand as `1-step`
                    want_op = _eval_operand(geq_alpha(kind, want, 0), 0.0)
                    self.assertAlmostEqual(got, want_op, places=9,
                                           msg=f"{kind} frame {k}: mask operand {got} != "
                                               f"the preview's {want_op}")

    def test_one_sized_sprite_carries_the_whole_sweep(self) -> None:
        # A photo that changes size nowhere in its entrance used to hold one still
        # per step of the sweep at that same size. `geq` can do the arithmetic per
        # frame instead, so the level count follows the sizes, not the steps — this
        # is the byte saving the restructure exists for, so it is asserted here
        # rather than left to be silently given back.
        for kind in REVEALS:
            with self.subTest(kind=kind):
                levels = _masked_levels(_graph_lines(kind, photos=1))
                self.assertEqual(len(levels), 1,
                                 f"{kind}: {len(levels)} masked levels for one size")
                self.assertGreater(len(levels[0][0]), 1,
                                   "that level is not carrying a sweep, so the count proves nothing")

    def test_each_masked_sprite_is_held_for_its_own_window(self) -> None:
        # An `out` kind tests with gte and an in kind with lte, so the operator
        # is the one place the two families differ; and every masked level has
        # to be switched on by a window, or the final state would stick.
        graph = _graph_lines("wipeUp", photos=1)
        # read off the mask test itself: the step a merged level computes contains
        # its own `gte`, which is not the comparison that uncovers the photo
        self.assertEqual(_mask_test(_graph_lines("wipeUp", photos=1)), "lte")
        self.assertEqual(_mask_test(_graph_lines("mosaicOut", photos=1)), "gte")
        # Every masked step is bounded to its own frames, so the mask never runs
        # on a frame nobody sees, and its window is the same one the overlay
        # switches it on for.
        masked = [l for l in graph.split(";") if GEQ_HEAD in l]
        self.assertTrue(masked, "the sweep has no masked sprite at all")
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

    def test_the_expression_agrees_with_the_sampler_at_ffmpeg_clock_rates(self) -> None:
        # `geq` is handed the frame's timestamp, which libavfilter forms as
        # `round(pts * 1e6 / tb_den) / 1e6` — microseconds, rounded. At 24, 30 and 60 fps
        # that is not the `k / fps` the ladder samples at and the preview divides, and the
        # difference (3.3e-7 s) is big enough to move `floor` across a boundary: a frame
        # of the sweep then shows a whole step of mask the preview does not show. The
        # expression recovers the frame index and divides again, so this asks, for every
        # rate the app can render at, that the graph's step is the sampler's step.
        # The rates are the ones the render can be asked for: `renderer.py` reads
        # `frameRate` through `int()`, so a fractional rate never reaches a graph.
        from app.collage_reveal import geq_alpha_expr, reveal_progress, reveal_step_expr
        for kind in ("mosaic", "curtain", "clock"):
            for fps in (24, 25, 30, 50, 60):
                for start, frames in ((0.0, 15), (0.15, 12), (2.0, 19)):
                    span = frames / fps
                    expr = reveal_step_expr(kind, start, span, fps)
                    assert expr is not None
                    alpha = geq_alpha_expr(kind, expr, 0)
                    for k in range(int(round((start + span) * fps)) + 3):
                        # what the frame's clock says, and what the render will be handed
                        t_sampler = k / fps
                        t_ffmpeg = round(k * 1e6 / fps) / 1e6
                        # the eased progress, because that is what the ladder quantises:
                        # `photo_state` reports `revealP` after the family's curve, and the
                        # expression has to take that curve in the same order to agree
                        p = reveal_progress(kind, min(1.0, max(0.0, (t_sampler - start) / span)))
                        want = 1.0 if p >= 1.0 else reveal_quantise(REVEAL_STEPS, p)
                        got = _eval_operand(alpha, t_ffmpeg)
                        self.assertAlmostEqual(got, want, places=12,
                                               msg=f"{kind} at {fps} fps, frame {k}: the graph "
                                                   f"says {got}, the sampler is at {want}")

    def test_the_expression_easing_is_the_python_easing(self) -> None:
        # `EASE_EXPR` is FFmpeg's spelling of `EASE`, and the preview's spelling of it
        # too. The step is `floor` of the result, so a curve that drifts by a last ulp
        # moves a whole step of mask on the frames that straddle a boundary — which is
        # why the polynomials are written out as multiplications rather than `pow`.
        from app.collage_reveal import EASE, EASE_EXPR
        for fam, tmpl in EASE_EXPR.items():
            with self.subTest(family=fam):
                curve = EASE[fam]
                for i in range(201):
                    q = i / 200
                    got = float(eval(  # noqa: S307
                        _to_python(tmpl.replace("{q}", repr(q))), {"__builtins__": {}}, {}))
                    self.assertAlmostEqual(got, curve(q), places=12,
                                           msg=f"{fam} at q={q}: {got} != {curve(q)}")

    def test_every_reveal_can_write_its_sweep_as_time(self) -> None:
        # A kind with no `EASE_EXPR` entry still renders — one baked still per step, as
        # it always did — but it does not get the graph this file's sizes are measured
        # on, so a new reveal that forgets its expression twin is worth noticing here.
        from app.collage_reveal import reveal_step_expr
        for kind in REVEALS:
            with self.subTest(kind=kind):
                self.assertIsNotNone(reveal_step_expr(kind, 0.0, 0.6),
                                     f"{kind} has no expression twin: it keeps the full ladder")

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
        # …and the level before it was masked, or this proves nothing: and its
        # last window has to stop where the resting one begins, or the two would
        # both be on and the mask would outlive the sweep.
        swept = _masked_levels(graph)
        self.assertEqual(len(swept), 1, [e[:40] for _, e in swept])
        ends = [b for a, b in swept[0][0] if b is not None]
        self.assertTrue(ends, "the sweep's last window is open-ended")
        rest = re.search(r"enable='gte\(t,([-0-9.]+)\)'", final[0])
        assert rest is not None
        self.assertLess(max(ends), float(rest.group(1)),
                        "the sweep is still masked when the photo is at rest")


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
                    # (`sendcmd=fps=…` is not an option that filter has — the frame
                    # rate comes from the inputs — and this test only runs where an
                    # ffmpeg binary does, so it had never been executed)
                    graph = (f"[0:v]null[cb];{''.join(lines)}"
                             f"[{last}]format=gray,settb=AVTB,fps={FPS}[v]")
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


@unittest.skipUnless(_ffmpeg_bin(), "needs an ffmpeg binary (set FFMPEG_BIN)")
class MergedLadderFramesTest(unittest.TestCase):
    """The merged ladder, rendered: the same frames out of a much smaller graph.

    A photo's frames are the file, looped — so the only thing a level's shape can
    change is which pixels of it are uncovered on which frame, which is what this
    compares. The control is the same graph with the expression taken away from it
    (`reveal_step_expr` returning None makes every step a baked still again), which
    is the graph this branch starts from: byte-for-byte the same output is the whole
    claim of the restructure, and the size column is its only reward.
    """

    CONFIGS = (
        # anim, photos, hold, camera, motion, exit
        ("curtain", 3, 2.0, "zoom", "breathe", "zoom"),
        ("mosaic", 4, 5.0, "none", "none", "reverse"),
        ("wipeUp", 2, 1.5, "telescope", "sway", "none"),
        ("iris", 3, 3.0, "pan", "none", "swing"),
    )

    def _graph(self, anim, photos, hold, camera, motion, ex, merged: bool) -> tuple[str, float]:
        item = {"collage": {
            "layout": "grid", "animation": anim, "shape": "4:3", "seed": 3, "hold": hold,
            "camera": camera, "frame": {"shape": "rounded", "shadow": True},
            "photos": [{"path": f"/photos/r{i}.jpg", "anim": anim, "motion": motion, "exit": ex}
                       for i in range(photos)],
        }}
        spec = normalize_collage(item)
        assert spec is not None
        duration = collage_duration(spec)
        import app.collage as module
        real = module.reveal_step_expr
        if not merged:
            module.reveal_step_expr = lambda *a, **k: None
        try:
            lines, last = collage_graph(item, W, H, FPS, duration, 0.2, 1, "cb")
        finally:
            module.reveal_step_expr = real
        return f"[0:v]null[cb];{''.join(lines)}[{last}]format=yuv420p[v]", duration

    def _render(self, graph: str, frames: int, photos: int) -> bytes:
        ffmpeg = _ffmpeg_bin()
        assert ffmpeg is not None
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", f"color=c=0x3050a0:s={W}x{H}:r={FPS}:d={frames / FPS:.3f}"]
        # flat colours, because a real photo is one still: any difference left here is
        # a difference in the mask, not in which frame of a moving picture got painted
        for i in range(photos):
            cmd += ["-f", "lavfi",
                    "-i", f"color=c=0x{40 + i * 60:02x}{70 + i * 40:02x}b0:s=320x240:r={FPS}:d={frames / FPS:.3f}"]
        cmd += ["-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames),
                "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"]
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        self.assertEqual(proc.returncode, 0, proc.stderr[-400:].decode("utf-8", "replace"))
        return proc.stdout

    def test_the_same_frames_come_out_of_a_smaller_graph(self) -> None:
        for anim, photos, hold, camera, motion, ex in self.CONFIGS:
            with self.subTest(anim=anim, camera=camera, exit=ex):
                baked, dur = self._graph(anim, photos, hold, camera, motion, ex, False)
                merged, dur2 = self._graph(anim, photos, hold, camera, motion, ex, True)
                self.assertEqual(dur, dur2, "the slide got a different length")
                self.assertLess(len(merged), len(baked),
                                f"the ladder was not shortened: {len(baked)} -> {len(merged)}")
                frames = int(round((dur + 0.4) * FPS))
                a = self._render(baked, frames, photos)
                b = self._render(merged, frames, photos)
                size, step = W * H * 3 // 2, W * H * 3 // 2
                self.assertEqual(len(a), len(b), "a different number of frames came out")
                bad = [f for f in range(len(a) // step) if a[f * size:(f + 1) * size] != b[f * size:(f + 1) * size]]
                self.assertEqual(bad, [], f"{len(bad)} of {len(a) // step} frames differ, first {bad[:4]}"
                                          f" ({[f / FPS for f in bad[:4]]} s)")
                self.assertGreater(len(a) // step, frames - 2, "the render came up short")


if __name__ == "__main__":
    unittest.main()
