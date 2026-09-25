#!/usr/bin/env python3
"""Golden test: the Python compositor and its JavaScript twin must agree.

The proposal keeps preview and render identical by interpreting ONE
declarative registry (text-motion-effects.json) with two engines: Python for
the FFmpeg/libass render, JavaScript for the live preview. This script is the
prototype of the test that would guard that promise in CI:

  1. Python lays out a set of captions (HarfBuzz) and evaluates every stack at
     every frame (text_motion_stack.py);
  2. node evaluates the same stacks on the same layout with
     docs/mockups/text-motion-core.js;
  3. every channel of every unit at every frame is compared.

    python3 docs/demo/check_twin_engines.py                 (needs node on PATH)
    python3 docs/demo/check_twin_engines.py --sync-mockup   (refresh the registry
        snapshot embedded in docs/mockups/text-effects-stack.html for file:// use)
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import text_motion_stack as tms  # noqa: E402

CORE = HERE.parent / "mockups" / "text-motion-core.js"
MOCKUP = HERE.parent / "mockups" / "text-effects-stack.html"
SNAPSHOT = re.compile(r'(<script type="application/json" id="effects-snapshot">)(.*?)(</script>)', re.S)

# Stacks chosen to cover every channel type: per-unit hierarchy (word + char),
# 3D origins, clips, colour folding, generators (sine/noise/flicker), random
# keyframes, content (scramble/flap/count/countdown), paths and intensity.
CASES = [
    ("Hello, summer!", [tms.Layer("typewriter", stagger=0.1), tms.Layer("wave"), tms.Layer("fade-out")]),
    ("Pop & pulse", [tms.Layer("pop-in", unit="word", stagger=0.2), tms.Layer("wave", intensity=0.6),
                     tms.Layer("pulse"), tms.Layer("pop-out", unit="char", order="reverse", stagger=0.03)]),
    ("Blurry spin away", [tms.Layer("blurry-spin"), tms.Layer("rainbow-wave"), tms.Layer("letter-burst")]),
    ("Sliding\nreveal", [tms.Layer("sliding-reveal"), tms.Layer("shimmer"), tms.Layer("mask-out")]),
    ("GLITCH 42", [tms.Layer("glitch-reveal"), tms.Layer("rgb-split"), tms.Layer("flicker-out")]),
    ("GATE 42", [tms.Layer("departures-flap"), tms.Layer("tilt"), tms.Layer("wipe-out")]),
    ("0", [tms.Layer("pop-in"), tms.Layer("count-up", params={"from": 0, "to": 2026, "separator": ","}),
           tms.Layer("split-out")]),                         # split-out is re-united to the whole text
    ("3", [tms.Layer("countdown")]),
    ("On the move", [tms.Layer("slide-in-left"), tms.Layer("motion-path", params={"points": [[30, 40], [50, 60], [70, 40]]}),
                     tms.Layer("tracking-in"), tms.Layer("zoom-out")]),
    ("Flip 3D words", [tms.Layer("flip-3d", unit="word"), tms.Layer("breathe"), tms.Layer("scramble-in"),
                       tms.Layer("cascade-drop", order="edges"), tms.Layer("delete")]),
]


def unit_dict(u: tms.Unit) -> dict:
    return {"index": u.index, "text": u.text, "cx": u.cx, "cy": u.cy, "w": u.w, "h": u.h,
            "anc": {lvl: a.index for lvl, a in u.anc.items()}, "col": u.col, "ncol": u.ncol}


def python_side() -> list[dict]:
    """Layout + per-frame composed states from the Python engine."""
    out = []
    for text, stack in CASES:
        cap = tms.Caption(text, 640, 360, 64, stack, start=0.0, end=4.0)
        units = tms.layout(text, cap.family, cap.bold, cap.size, cap.x, cap.y)
        out.append({
            "text": text,
            "stack": [{k: v for k, v in layer.__dict__.items() if v is not None and v != {} and not (k == "muted" and v is False)}
                      for layer in stack],
            "units": {lvl: [unit_dict(u) for u in units[lvl]] for lvl in tms.LEVELS},
            "caption": {"size": cap.size, "colour": cap.colour, "start": cap.start, "end": cap.end,
                        "frameW": cap.frame_w, "frameH": cap.frame_h},
            "states": record_states(cap),
        })
    return out


def record_states(cap: tms.Caption) -> dict:
    """Run the real compile_caption and capture the composed state of every
    unit at every frame (the main pass: copies and the caret are stubbed out,
    they are derived from these states anyway)."""
    captured: dict = {"gran": None, "frames": []}
    real = (tms._ass_state, tms._copy_states, tms._caret_events)

    def spy(s, em, gran):
        captured["gran"] = gran
        captured["frames"].append({"x": s["x"], "y": s["y"], "text": s["text"], "tot": s["tot"],
                                   "colour": list(s["colour"]), "clip": list(s["clip"]) if s["clip"] else None,
                                   "org": list(s["org"]) if s["org"] else None})
        return real[0](s, em, gran)

    tms._ass_state = spy
    tms._copy_states = lambda *args, **kwargs: iter(())
    tms._caret_events = lambda *args, **kwargs: []
    try:
        tms.compile_caption(cap, tms.Doc(1280, 720))
    finally:
        tms._ass_state, tms._copy_states, tms._caret_events = real
    return captured


NODE_SCRIPT = r"""
const fs = require('fs')
// the repo is "type": "module", so evaluate the browser script in a CommonJS-style wrapper
const mod = { exports: {} }
new Function('module', 'exports', fs.readFileSync(process.argv[2], 'utf8'))(mod, mod.exports)
const core = mod.exports
const effects = Object.fromEntries(JSON.parse(fs.readFileSync(process.argv[3], 'utf8')).effects.map(e => [e.id, e]))
const cases = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'))
const FPS = 25
const out = []
for (const c of cases) {
  const ctx = core.compile(c.stack, c.units, c.caption, effects)
  const frames = []
  const n = Math.round((c.caption.end - c.caption.start) * FPS)
  for (const e of c.units[ctx.gran]) {
    for (let k = 0; k <= n; k++) {
      const t = c.caption.start + k * (1 / FPS)
      const s = core.evaluate(ctx, e, t)
      frames.push({ x: s.x, y: s.y, text: s.text, tot: s.tot, colour: s.colour, clip: s.clip, org: s.org })
    }
  }
  out.push({ gran: ctx.gran, frames })
}
process.stdout.write(JSON.stringify(out))
"""


def compare(py: dict, js: dict, label: str) -> tuple[int, float]:
    if py["gran"] != js["gran"]:
        raise SystemExit(f"{label}: granularity differs ({py['gran']} vs {js['gran']})")
    if len(py["frames"]) != len(js["frames"]):
        raise SystemExit(f"{label}: {len(py['frames'])} vs {len(js['frames'])} samples")
    worst = 0.0
    for a, b in zip(py["frames"], js["frames"]):
        if a["text"] != b["text"]:
            raise SystemExit(f"{label}: text differs {a['text']!r} vs {b['text']!r}")
        pairs = [(a["x"], b["x"]), (a["y"], b["y"])]
        pairs += [(a["tot"][k], b["tot"][k]) for k in a["tot"]]
        pairs += list(zip(a["colour"], b["colour"]))
        for key in ("clip", "org"):
            if (a[key] is None) != (b[key] is None):
                raise SystemExit(f"{label}: {key} presence differs")
            if a[key] is not None:
                pairs += list(zip(a[key], b[key]))
        worst = max(worst, max(abs(p - q) for p, q in pairs))
    return len(py["frames"]), worst


def snapshot_in_sync(write: bool) -> bool:
    """The mockup embeds a copy of the registry for when it is opened from disk."""
    registry = json.dumps(json.loads(tms.EFFECTS_PATH.read_text(encoding="utf-8")), ensure_ascii=False, separators=(",", ":"))
    html = MOCKUP.read_text(encoding="utf-8")
    match = SNAPSHOT.search(html)
    if match is None:
        raise SystemExit("effects-snapshot block not found in the mockup")
    if match.group(2) == registry:
        return True
    if write:
        MOCKUP.write_text(html[:match.start(2)] + registry + html[match.end(2):], encoding="utf-8")
        print(f"synced the registry snapshot in {MOCKUP.name}")
        return True
    return False


def main() -> int:
    if "--sync-mockup" in sys.argv:
        snapshot_in_sync(write=True)
    elif not snapshot_in_sync(write=False):
        print(f"note: the registry snapshot in {MOCKUP.name} is out of date (run with --sync-mockup)")
    node = shutil.which("node")
    if not node:
        print("node is not installed; skipping the twin-engine check")
        return 0
    cases = python_side()
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "cases.json"
        data.write_text(json.dumps([{k: c[k] for k in ("stack", "units", "caption")} for c in cases]), encoding="utf-8")
        script = Path(tmp) / "run.js"
        script.write_text(NODE_SCRIPT, encoding="utf-8")
        result = subprocess.run([node, str(script), str(CORE), str(tms.EFFECTS_PATH), str(data)],
                                capture_output=True, text=True)
        if result.returncode != 0:
            print(result.stderr)
            return 1
    js_all = json.loads(result.stdout)
    total = 0
    worst_all = 0.0
    for case, js in zip(cases, js_all):
        n, worst = compare(case["states"], js, case["text"].replace("\n", " / "))
        total += n
        worst_all = max(worst_all, worst)
        layers = " + ".join(layer["effect"] for layer in case["stack"])
        print(f"  {case['text'].replace(chr(10), ' / '):18s} {case['states']['gran']:5s} {n:5d} samples  "
              f"max |diff| {worst:.2e}   {layers}")
    ok = worst_all < 1e-6
    print(f"{'OK' if ok else 'MISMATCH'}: {total} unit-frames, every channel, max |diff| = {worst_all:.2e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
