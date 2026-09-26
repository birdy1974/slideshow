"""The collage engines must agree: src/collageCore.ts (editor preview) and
backend/app/collage.py (the FFmpeg render) turn the same collage spec into
the same placements and the same per-photo motion, value for value.

Python writes the cases (specs x aspects x times), Node evaluates the TS twin
on them (tests/collage_twin.mjs), and this test compares every number with a
tolerance far below anything visible on screen.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.collage import (
    MAX_COLLAGE_PHOTOS,
    bg_blur_radius,
    collage_duration,
    collage_graph,
    hash01,
    mat_height,
    normalize_collage,
    photo_delay,
    photo_size,
    photo_start,
    photo_state,
    pin_anchor,
    placements,
)

DRIVER = Path(__file__).resolve().parent / "collage_twin.mjs"
TOL = 1e-9


def _run_node(cases: dict) -> list:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(DRIVER)],
        input=json.dumps(cases), capture_output=True, text=True, timeout=120,
        cwd=Path(__file__).resolve().parent,
    )
    if proc.returncode != 0:
        pytest.fail(f"collage twin driver failed:\n{proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout)


def _spec(layout: str, animation: str, shape: str, count: int, seed: int,
          delays: list | None = None, hold: float | None = None, sizes: list | None = None) -> dict:
    photos = []
    for k in range(count):
        p = {"path": f"/photos/p{k}.jpg", "name": f"p{k}.jpg"}
        if delays is not None and k < len(delays) and delays[k] is not None:
            p["delay"] = delays[k]
        if sizes is not None and k < len(sizes) and sizes[k] is not None:
            p["size"] = sizes[k]
        photos.append(p)
    spec = {"photos": photos, "layout": layout, "animation": animation, "shape": shape, "seed": seed}
    if hold is not None:
        spec["hold"] = hold
    return spec


def _cases() -> list[dict]:
    cases = []
    times = [0.0, 0.15, 0.31, 0.5, 0.7, 1.0, 1.37, 2.0, 3.5]
    idx = 0
    for layout in ("stack", "grid", "scatter"):
        for animation in ("drop", "pop", "swing", "none"):
            for shape in ("4:3", "square", "3:4"):
                for count, seed in ((1, 1), (3, 7), (6, 42), (10, 2026)):
                    idx += 1
                    hold = [None, 2, 0.5, 4.25][idx % 4]
                    cases.append({
                        "id": f"{layout}-{animation}-{shape}-n{count}",
                        "spec": _spec(layout, animation, shape, count, seed, hold=hold),
                        "aspect": 16 / 9,
                        "leadIn": 0.5 if idx % 2 else 0.0,
                        "times": times,
                        "hashArgs": [seed, count, 29],
                        "matW": 34,
                    })
    # portrait and square frames too — the layout maths is aspect-aware
    for aspect in (4 / 3, 1.0, 9 / 16):
        cases.append({
            "id": f"stack-drop-4:3-n5-a{aspect:.3f}",
            "spec": _spec("stack", "drop", "4:3", 5, 3),
            "aspect": aspect, "leadIn": 0.0, "times": times, "hashArgs": [3, 5, 11], "matW": 30,
        })
    # explicit per-photo delays: the appearance rhythm the user sets, and the
    # slide duration that follows from it
    cases.append({
        "id": "timed-stack-drop",
        "spec": _spec("stack", "drop", "4:3", 5, 9, delays=[0.2, 1.0, 0.4, 2.5, 0.1], hold=3),
        "aspect": 16 / 9, "leadIn": 0.7, "times": [0.0, 0.3, 1.4, 4.2, 6.6, 8.8],
        "hashArgs": [9, 5, 11], "matW": 34,
    })
    cases.append({
        "id": "timed-mixed-defaults",
        "spec": _spec("grid", "pop", "square", 4, 5, delays=[None, 0.8, None, 0.05], hold=0),
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.15, 1.2, 1.8, 2.6],
        "hashArgs": [5, 4, 11], "matW": 30,
    })
    cases.append({
        "id": "timed-swing-hold",
        "spec": _spec("scatter", "swing", "3:4", 3, 2, delays=[0.5, 0.5, 0.5], hold=1.5),
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.6, 1.5, 3.0, 4.5],
        "hashArgs": [2, 3, 11], "matW": 30,
    })
    # per-photo sizes: bigger photos overlap their neighbours (junk values
    # must fall back to 1 identically in both engines)
    cases.append({
        "id": "sized-scatter-drop",
        "spec": _spec("scatter", "drop", "4:3", 6, 77, sizes=[1.2, 0.6, None, 1.5, 0.5, True]),
        "aspect": 16 / 9, "leadIn": 0.3, "times": [0.0, 0.5, 1.3, 2.8],
        "hashArgs": [77, 6, 29], "matW": 34,
    })
    cases.append({
        "id": "sized-stack-grid",
        "spec": _spec("grid", "pop", "square", 4, 8, sizes=[0.5, 1.5, 1.0, "1.25"], hold=1),
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.4, 1.6],
        "hashArgs": [8, 4, 29], "matW": 30,
    })
    return cases


def test_collage_twin_engines_agree():
    node = _run_node({"cases": _cases()})
    cases = _cases()
    assert len(node) == len(cases)
    for case, got in zip(cases, node):
        spec = case["spec"]
        aspect, lead_in = case["aspect"], case["leadIn"]
        where = case["id"]
        # hash + mat height + anchor + starts + derived duration
        assert abs(got["hash"] - hash01(*case["hashArgs"])) < TOL, where
        assert abs(got["matHeight"] - mat_height(case["matW"], spec["shape"], aspect)) < TOL, where
        assert got["pin"] == pin_anchor(spec), where
        py_starts = [photo_start(spec, i, lead_in) for i in range(len(spec["photos"]))]
        for a, b in zip(got["start"], py_starts):
            assert abs(a - b) < TOL, where
        assert abs(got["duration"] - collage_duration(spec)) < TOL, f"{where} duration: {got['duration']} != {collage_duration(spec)}"
        # placements
        py_pls = placements(spec, aspect)
        assert len(got["placements"]) == len(py_pls) == len(spec["photos"]), where
        for a, b in zip(got["placements"], py_pls):
            for key in ("cx", "cy", "w", "rot"):
                assert abs(a[key] - b[key]) < TOL, f"{where} {key}: {a[key]} != {b[key]}"
        # animation states at every sampled time
        for ti, t in enumerate(case["times"]):
            for i in range(len(spec["photos"])):
                py = photo_state(spec, i, t, lead_in)
                ts = got["states"][ti][i]
                for key in ("dx", "dy", "rot", "scale", "alpha"):
                    assert abs(ts[key] - py[key]) < TOL, f"{where} t={t} photo {i} {key}: {ts[key]} != {py[key]}"


def test_untouched_collage_keeps_the_legacy_stagger():
    """A spec saved before per-photo timing existed must animate exactly as it
    did: 0.15 s + stagger · i with stagger = min(0.32, 2.4/(n-1))."""
    for n in range(1, 13):
        spec = _spec("stack", "drop", "4:3", n, 1)
        stagger = min(0.32, 2.4 / (n - 1)) if n > 1 else 0.0
        for i in range(n):
            assert abs(photo_start(spec, i, 0.0) - (0.15 + stagger * i)) < TOL, (n, i)
            assert abs(photo_start(spec, i, 0.4) - (0.4 + 0.15 + stagger * i)) < TOL, (n, i)


def test_duration_follows_the_photo_timings():
    # Σ delays = 0.2+1.0+0.4+2.5+0.1 → last photo starts at 4.2 s
    spec = _spec("stack", "drop", "4:3", 5, 9, delays=[0.2, 1.0, 0.4, 2.5, 0.1], hold=3)
    assert abs(photo_start(spec, 4, 0.0) - 4.2) < TOL
    assert abs(collage_duration(spec) - 7.75) < TOL
    # pop entrance is 0.5 s, swing settles over 2 s
    assert abs(collage_duration(_spec("grid", "pop", "square", 1, 1, delays=[0.15], hold=1)) - 1.65) < TOL
    assert abs(collage_duration(_spec("grid", "swing", "square", 1, 1, delays=[0.15], hold=1)) - 3.15) < TOL
    # 'none': every photo is on screen from frame one, so just the hold
    assert abs(collage_duration(_spec("grid", "none", "square", 5, 1, delays=[3, 3, 3, 3, 3], hold=2.5)) - 2.5) < TOL
    # missing hold defaults to 2 s
    assert abs(collage_duration(_spec("stack", "drop", "4:3", 1, 1)) - 2.7) < TOL
    # an empty collage has no timing to derive
    assert collage_duration({"photos": [], "layout": "stack", "animation": "drop", "shape": "4:3", "seed": 1}) == 0.0


def test_delay_junk_falls_back_to_defaults():
    spec = {"photos": [{"path": "/photos/a.jpg"}, {"path": "/photos/b.jpg", "delay": True},
                       {"path": "/photos/c.jpg", "delay": ""}, {"path": "/photos/d.jpg", "delay": "0.7"},
                       {"path": "/photos/e.jpg", "delay": 99}],
            "layout": "stack", "animation": "drop", "shape": "4:3", "seed": 1}
    assert photo_delay(spec, 0) == 0.15                      # default
    assert photo_delay(spec, 1) == 0.32                      # True → default stagger (n=5 → 2.4/4 → 0.32)
    assert photo_delay(spec, 2) == 0.32                      # "" → default
    assert abs(photo_delay(spec, 3) - 0.7) < TOL             # numeric string accepted
    assert photo_delay(spec, 4) == 30.0                      # clamped


def test_photo_size_junk_falls_back_to_one():
    spec = {"photos": [{"path": "/photos/a.jpg"}, {"path": "/photos/b.jpg", "size": True},
                       {"path": "/photos/c.jpg", "size": ""}, {"path": "/photos/d.jpg", "size": "0.8"},
                       {"path": "/photos/e.jpg", "size": 9}, {"path": "/photos/f.jpg", "size": 0.1}],
            "layout": "stack", "animation": "drop", "shape": "4:3", "seed": 1}
    assert photo_size(spec, 0) == 1.0                       # default
    assert photo_size(spec, 1) == 1.0                       # True → default
    assert photo_size(spec, 2) == 1.0                       # "" → default
    assert abs(photo_size(spec, 3) - 0.8) < TOL             # numeric string accepted
    assert photo_size(spec, 4) == 1.5                       # clamped high
    assert photo_size(spec, 5) == 0.5                       # clamped low


def test_photo_size_scales_the_mat_width():
    base = placements(_spec("grid", "none", "4:3", 3, 5), 16 / 9)
    sized = placements(_spec("grid", "none", "4:3", 3, 5, sizes=[1.5, 0.5, None]), 16 / 9)
    assert abs(sized[0]["w"] - base[0]["w"] * 1.5) < TOL
    assert abs(sized[1]["w"] - base[1]["w"] * 0.5) < TOL
    assert abs(sized[2]["w"] - base[2]["w"]) < TOL
    # size never moves the centre — only the mat grows around it
    for a, b in zip(base, sized):
        assert abs(a["cx"] - b["cx"]) < TOL and abs(a["cy"] - b["cy"]) < TOL


def test_bg_blur_mapping():
    assert bg_blur_radius(0) == 1
    assert bg_blur_radius(0.5) == 16
    assert bg_blur_radius(1.0) == 30
    assert bg_blur_radius(None) == 1
    assert bg_blur_radius(7) == 30                           # out-of-range clamps


def test_placements_are_sane():
    for layout in ("stack", "grid", "scatter"):
        spec = _spec(layout, "drop", "4:3", 9, 5)
        for pl in placements(spec, 16 / 9):
            assert 4 <= pl["cx"] <= 96 and 8 <= pl["cy"] <= 92, (layout, pl)
            assert 15 <= pl["w"] <= 45, (layout, pl)
            assert abs(pl["rot"]) <= 15, (layout, pl)


def test_normalize_collage():
    assert normalize_collage({"collage": None}) is None
    assert normalize_collage({}) is None
    assert normalize_collage({"collage": {"photos": []}}) is None
    # nested shape (a title frame carrying photos)
    spec = normalize_collage({"collage": {
        "photos": [{"path": "/photos/a.jpg"}, {"path": "/photos/a.jpg"}, "junk",
                   {"path": ""}, {"path": "/photos/b.jpg", "name": "b.jpg"}],
        "layout": "nonsense", "animation": "nonsense", "shape": "nonsense", "seed": "x",
    }})
    assert spec is not None
    assert [p["path"] for p in spec["photos"]] == ["/photos/a.jpg", "/photos/b.jpg"]
    assert spec["layout"] == "stack" and spec["animation"] == "drop"
    assert spec["shape"] == "4:3" and spec["seed"] == 1
    many = normalize_collage({"collage": {"photos": [{"path": f"/photos/{k}.jpg"} for k in range(30)]}})
    assert many is not None and len(many["photos"]) == MAX_COLLAGE_PHOTOS
    # documented top-level shape: type 'collage' carries its fields directly
    top = normalize_collage({"type": "collage", "photos": [{"path": "/photos/c.jpg"}],
                             "layout": "grid", "animation": "swing", "shape": "square", "seed": 12})
    assert top is not None
    assert top["photos"] == [{"path": "/photos/c.jpg", "name": ""}]
    assert (top["layout"], top["animation"], top["shape"], top["seed"]) == ("grid", "swing", "square", 12)
    # a collage item with no photos is not a collage
    assert normalize_collage({"type": "collage", "photos": []}) is None
    # timing + background fields survive and are sanitised
    timed = normalize_collage({"collage": {
        "photos": [{"path": "/photos/a.jpg", "delay": 0.5}, {"path": "/photos/b.jpg", "delay": -3},
                   {"path": "/photos/c.jpg", "delay": "junk"}],
        "hold": 999, "backgroundImage": "", "backgroundBlur": 1.4}})
    assert timed is not None
    assert timed["photos"][0]["delay"] == 0.5
    assert timed["photos"][1]["delay"] == 0.0
    assert "delay" not in timed["photos"][2]
    assert timed["hold"] == 120
    assert "backgroundImage" not in timed
    assert timed["backgroundBlur"] == 1.0
    bg = normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg"}],
                                        "backgroundImage": "/photos/bg.jpg", "backgroundBlur": 0.3, "hold": 2.5}})
    assert bg is not None
    assert bg["backgroundImage"] == "/photos/bg.jpg"
    assert abs(bg["backgroundBlur"] - 0.3) < TOL
    assert abs(bg["hold"] - 2.5) < TOL
    # per-photo size: sanitised, clamped, junk dropped
    sized = normalize_collage({"collage": {"photos": [
        {"path": "/photos/a.jpg", "size": 1.25}, {"path": "/photos/b.jpg", "size": 4},
        {"path": "/photos/c.jpg", "size": True}, {"path": "/photos/d.jpg", "size": "0.6"}]}})
    assert sized is not None
    assert abs(sized["photos"][0]["size"] - 1.25) < TOL
    assert sized["photos"][1]["size"] == 1.5
    assert "size" not in sized["photos"][2]
    assert abs(sized["photos"][3]["size"] - 0.6) < TOL


def test_graph_builds_for_every_animation():
    for animation in ("drop", "pop", "swing", "none"):
        for layout in ("stack", "grid", "scatter"):
            item = {"collage": _spec(layout, animation, "square", 4, 9)}
            built = collage_graph(item, 1280, 720, 25.0, 3.0, 0.5, 1, "cb")
            assert built is not None, (animation, layout)
            lines, last = built
            assert len(lines) == 4 * 6, (animation, layout)   # 6 lines per photo
            assert last == "o3"
            graph = "".join(lines)
            assert "[cb][sp0r]overlay=" in graph
            # every photo input is referenced
            for k in (1, 2, 3, 4):
                assert f"[{k}:v]scale=" in graph


def test_graph_overlays_are_centre_anchored():
    """FFmpeg's overlay x/y is the overlaid sprite's TOP-LEFT corner, so the
    expressions must subtract half the canvas — the same translate(-50%, …)
    the DOM preview applies. Placing the anchor there instead (the Phase-1
    bug) shifts every photo down-right by half its sprite and pushes large
    mats off-screen."""
    import re
    # grid => rot 0, single photo => cx 50 %, cy 50 % (640/360 at 1280x720)
    for anim in ("none", "swing"):
        item = {"collage": _spec("grid", anim, "4:3", 1, 5)}
        lines, _ = collage_graph(item, 1280, 720, 25.0, 3.0, 0.0, 1, "cb")
        graph = "".join(lines)
        m = re.search(r"overlay=x='(-?[\d.]+)':y='(-?[\d.]+)'", graph)
        assert m, (anim, graph[-200:])
        x, y = float(m.group(1)), float(m.group(2))
        assert x < 640, f"{anim}: overlay left edge {x} must be left of the anchor (640)"
        assert y < 360, f"{anim}: overlay top edge {y} must be above the anchor (360)"
    # drop: y is '<rest-top>-<fall>*pow(…)' — the constant must sit above the anchor
    item = {"collage": _spec("grid", "drop", "4:3", 1, 5)}
    lines, _ = collage_graph(item, 1280, 720, 25.0, 3.0, 0.0, 1, "cb")
    graph = "".join(lines)
    m = re.search(r"overlay=x='(-?[\d.]+)':y='(-?[\d.]+)-", graph)
    assert m, graph[-200:]
    assert float(m.group(1)) < 640 and float(m.group(2)) < 360, (m.group(1), m.group(2))


def test_graph_places_the_mat_centre_on_the_anchor():
    """Parse the generated graph and prove the mat's centre lands on the
    layout anchor — the same translate(-50%, …) the DOM preview applies.
    Catches both Phase-1 positioning bugs: overlaying at the anchor instead
    of anchor-minus-half-canvas, and re-offsetting the mat inside its own
    sprite (shadow + mat are both pre-padded, so their overlay is at 0:0)."""
    import re
    for layout in ("grid", "stack", "scatter"):
        item = {"collage": _spec(layout, "none", "4:3", 3, 5)}
        W, H = 1280, 720
        spec = normalize_collage(item)
        pls = placements(spec, 16 / 9)
        lines, _ = collage_graph(item, W, H, 25.0, 3.0, 0.0, 1, "cb")
        graph = "".join(lines)
        mats = re.findall(r"pad=(\d+):(\d+):\d+:\d+:color=white", graph)
        canvases = re.findall(r"pad=(\d+):(\d+):(\d+):(\d+):color=black@0.0", graph)   # shadow + mat pad: 2 per photo
        overs = re.findall(r"overlay=x=(\d+):y=(\d+)\[sp\d+\]", graph)
        finals = re.findall(r"overlay=x='(-?[\d.]+)':y='(-?[\d.]+)'", graph)
        assert len(mats) == 3 and len(canvases) == 6 and len(overs) == 3 and len(finals) == 3, (layout, len(mats), len(canvases), len(overs), len(finals))
        for i in range(3):
            mat_w, mat_h = int(mats[i][0]), int(mats[i][1])
            cw, ch, mat_x, mat_y = (int(v) for v in canvases[2 * i])
            assert (int(overs[i][0]), int(overs[i][1])) == (0, 0), f"{layout}: sprite assembly must not re-offset the mat"
            fx, fy = float(finals[i][0]), float(finals[i][1])
            cx_screen = fx + mat_x + mat_w / 2
            cy_screen = fy + mat_y + mat_h / 2
            assert abs(cx_screen - pls[i]["cx"] / 100 * W) <= 1.5, (layout, i, cx_screen)
            assert abs(cy_screen - pls[i]["cy"] / 100 * H) <= 1.5, (layout, i, cy_screen)


def test_graph_none_without_collage():
    assert collage_graph({"collage": {"photos": []}}, 1280, 720, 25.0, 3.0, 0.0, 1, "cb") is None
    assert collage_graph({}, 1280, 720, 25.0, 3.0, 0.0, 1, "cb") is None
