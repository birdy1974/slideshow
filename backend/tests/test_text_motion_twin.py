"""The live preview and the render must agree: src/textMotionCore.ts (browser)
and backend/app/text_motion.py (FFmpeg/libass) evaluate the same stacks on the
same layout and every composed channel is compared.

Runs the TypeScript file directly with ``node --experimental-strip-types``;
skipped when Node (>= 22.6) is not available.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

from app.text_motion import (
    BgChange, Caption, Compiler, Layer, MotionPath, compile_stack, effects, evaluate, parse_stack, _ambient_opacity_ctx,
)

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "text_motion_twin.mjs"
FONTS = REPO / "public" / "fonts"


def _node() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
        major, minor = (int(p) for p in out.lstrip("v").split(".")[:2])
    except Exception:  # noqa: BLE001
        return None
    return node if (major, minor) >= (22, 6) else None


NODE = _node()


def _caption_json(cap: Caption) -> dict:
    return {
        "stack": [{k: v for k, v in {
            "effect": layer.effect, "unit": layer.unit, "duration": layer.duration, "delay": layer.delay,
            "stagger": layer.stagger, "order": layer.order, "loop": layer.loop, "intensity": layer.intensity,
            "params": layer.params or None, "muted": layer.muted or None, "sync": layer.sync,
            "range": list(layer.range) if layer.range else None,
        }.items() if v is not None} for layer in cap.stack],
        "em": cap.em, "colour": cap.colour, "start": cap.start, "end": cap.end,
        "frameW": cap.frame_w, "frameH": cap.frame_h, "steady": cap.steady,
        "bg": None if cap.bg is None else {"colourA": cap.bg.colour_a, "colourB": cap.bg.colour_b,
                                           "transition": cap.bg.transition, "start": cap.bg.start, "time": cap.bg.time},
        "motion": None if cap.motion is None else {"points": [list(p) for p in cap.motion.points], "easing": cap.motion.easing,
                                                   "rotateAlong": bool(cap.motion.rotate_along)},
    }


def _layout_json(layout) -> dict:
    return {
        "units": {lvl: [{"level": u.level, "index": u.index, "text": u.text, "cx": u.cx, "cy": u.cy, "w": u.w, "h": u.h,
                         "anc": u.anc, "line": u.line, "c0": u.c0, "c1": u.c1, "col": u.col, "ncol": u.ncol, "word": u.word}
                        for u in units] for lvl, units in layout.units.items()},
        "lines": layout.lines, "lineH": layout.line_h, "em": layout.em, "align": layout.align,
    }


def _close(a, b, path: str, errors: list[str], tol: float = 1e-6) -> None:
    if isinstance(a, dict) and isinstance(b, dict):
        for key in set(a) | set(b):
            _close(a.get(key), b.get(key), f"{path}.{key}", errors, tol)
        return
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            errors.append(f"{path}: length {len(a)} != {len(b)}")
            return
        for i, (x, y) in enumerate(zip(a, b)):
            _close(x, y, f"{path}[{i}]", errors, tol)
        return
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        if not (math.isfinite(a) and math.isfinite(b)) or abs(a - b) > tol * max(1.0, abs(a)):
            errors.append(f"{path}: {a!r} != {b!r}")
        return
    if a != b:
        errors.append(f"{path}: {a!r} != {b!r}")


def _cases() -> list[tuple[str, Caption]]:
    W, H = 1280, 720
    text = "Summer, slowly.\nEvery word matters"

    def cap(stack, **kw) -> Caption:
        base = dict(text=text, x=W / 2, y=H / 2, em=64, stack=stack, start=0.0, end=4.0, frame_w=W, frame_h=H, fps=25,
                    family="Montserrat", bold=True, colour="#f4f6f0")
        base.update(kw)
        return Caption(**base)

    out: list[tuple[str, Caption]] = []
    # every effect on its own (with the neighbouring lanes filled)
    for eid, fx in effects().items():
        stack = [Layer(eid, params={"from": 0, "to": 2026} if eid == "count-up" else {})]
        if fx["phase"] != "in":
            stack.insert(0, Layer("fade"))
        if fx["phase"] != "out":
            stack.append(Layer("fade-out"))
        bg = BgChange("#14213d", "#f4a261", "wiperight", 1.0, 1.2) if fx.get("needsBg") or fx.get("sync") else None
        motion = MotionPath([(30.0, 60.0), (50.0, 40.0), (70.0, 55.0)], "ease-in-out") if eid == "motion-path" else None
        out.append((eid, cap(stack, bg=bg, motion=motion)))
    # combinations that used to break, and the stack features
    L = Layer
    out += [
        ("typewriter+wave", cap([L("typewriter"), L("wave"), L("fade-out")])),
        ("split-rise+shimmer", cap([L("split-rise-chars"), L("shimmer"), L("pop-out")])),
        ("pop-words+wave-letters", cap([L("word-pop"), L("wave"), L("letter-burst")])),
        ("rotate+typewriter-caret", cap([L("typewriter-caret"), L("rotate", params={"from": -10, "to": 10}), L("fade-out")])),
        ("range-group-pulse", cap([L("fade"), L("pulse", range=(1, 3)), L("highlight", range=(2, 2), delay=0.4), L("fade-out")])),
        ("range-letters", cap([L("split-rise-chars", range=(3, 4)), L("fade-out")])),
        ("order-random-stagger", cap([L("split-fade-chars", order="random", stagger=0.8, duration=1.4), L("fade-out", order="center")])),
        ("intensity-loops", cap([L("fade"), L("shake", intensity=0.3), L("float-letters", loop="pingpong"), L("fade-out")])),
        ("steady-tail", cap([L("resize", params={"from": 0.8, "to": 1.3}), L("squash"), L("bouncy")], steady=1.5)),
        ("count+letters-warning", cap([L("split-rise-chars"), L("count-up"), L("fade-out")], text="0")),
        ("scramble-vs-flap", cap([L("scramble-reveal"), L("departures-flap"), L("scramble-out")])),
        ("left-aligned", cap([L("fade-up-lines"), L("swing"), L("drop-out")], align="left")),
        ("karaoke+colour", cap([L("fade"), L("karaoke-sweep"), L("colour-sweep"), L("fade-out")])),
        ("bg-follow-circle", cap([L("fade-up-words"), L("bg-follow"), L("bg-pulse"), L("fade-out")],
                                 bg=BgChange("#14213d", "#f4a261", "circleopen", 1.0, 1.4))),
        ("bg-arrive+follow-radial", cap([L("bg-arrive"), L("bg-follow"), L("gentle-float")],
                                        bg=BgChange("#1d3557", "#e63946", "radial", 0.6, 1.5))),
        ("bg-leave-diag", cap([L("fade"), L("bg-leave")], bg=BgChange("#000000", "#ffffff", "diagtl", 1.0, 1.0))),
        ("bg-invert-fade", cap([L("write-on"), L("bg-invert"), L("fade-out")], bg=BgChange("#1d1f24", "#f5f1e6", "fadeblack", 1.6, 1.0))),
        ("sync-bg-layers", cap([L("pop-in", sync="bg"), L("pulse", sync="bg"), L("sink-fade", sync="bg")],
                               bg=BgChange("#30382a", "#e9c46a", "slideup", 1.0, 1.5))),
        ("3d-org", cap([L("flip-words"), L("blurry-spin", unit="line"), L("fold-out")])),
        ("motion+wipe", cap([L("wipe-from-left"), L("motion-path"), L("wipe-out-right")],
                            motion=MotionPath([(20.0, 30.0), (80.0, 70.0)], "smooth"))),
        ("motion-rotate-along", cap([L("fade"), L("motion-path"), L("fade-out")],
                                    motion=MotionPath([(15.0, 70.0), (40.0, 25.0), (60.0, 60.0), (85.0, 30.0)], "linear", rotate_along=True))),
        ("motion-rotate-along-loop", cap([L("motion-path", loop="loop")],
                                         motion=MotionPath([(50.0, 50.0), (80.0, 30.0), (50.0, 70.0), (20.0, 30.0), (50.0, 50.0)], "linear", rotate_along=True))),
        ("motion-path-letters-stagger", cap([L("fade"), L("motion-path", unit="char", stagger=0.35), L("fade-out")],
                                            motion=MotionPath([(25.0, 65.0), (50.0, 25.0), (75.0, 60.0)], "ease-in-out", rotate_along=True))),
    ]
    return out


@unittest.skipUnless(NODE, "needs Node >= 22.6 for --experimental-strip-types")
class TwinEnginesAgree(unittest.TestCase):
    def test_every_effect_and_combination(self) -> None:
        cases = _cases()
        payload = {"cases": []}
        prepared = []
        for name, cap in cases:
            comp = Compiler(cap, FONTS)
            ctx = comp.ctx
            times = [round(0.0 + k * 0.08, 4) for k in range(int(cap.end / 0.08) + 1)] + [0.013, 1.234, 3.999]
            levels = sorted({ctx.gran, "line", "text", *(L.unit for L in ctx.stack)}, key=lambda s: s)
            payload["cases"].append({"caption": _caption_json(cap), "layout": _layout_json(comp.layout),
                                     "times": times, "levels": levels})
            prepared.append((name, ctx, times, levels))
        result = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(DRIVER)],
                                input=json.dumps(payload), capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        js = json.loads(result.stdout)
        failures: list[str] = []
        n_states = 0
        for (name, ctx, times, levels), got in zip(prepared, js):
            errors: list[str] = []
            if got["gran"] != ctx.gran:
                errors.append(f"gran {ctx.gran} != {got['gran']}")
            _close(ctx.levels, got["levels"], "levels", errors)
            _close(ctx.warnings, got["warnings"], "warnings", errors)
            _close({str(k): v for k, v in ctx.conflicts.items()}, got["conflicts"], "conflicts", errors)
            k = 0
            for level in levels:
                for unit in ctx.units.get(level, []):
                    for t in times:
                        py = evaluate(ctx, unit, t)
                        js_state = got["states"][k]["s"]
                        k += 1
                        py_json = json.loads(json.dumps(py))
                        _close(py_json, js_state, f"{level}#{unit.index}@{t}", errors)
                        n_states += 1
                        if len(errors) > 6:
                            break
            _close([_ambient_opacity_ctx(ctx, t) for t in times], got["ambient"], "ambient", errors)
            if errors:
                failures.append(f"{name}: " + "; ".join(errors[:6]))
        self.assertFalse(failures, "\n".join(failures[:12]))
        self.assertGreater(n_states, 20000)


if __name__ == "__main__":
    unittest.main()
