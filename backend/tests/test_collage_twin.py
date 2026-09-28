"""The collage engines must agree: src/collageCore.ts (editor preview) and
backend/app/collage.py (the FFmpeg render) turn the same collage spec into
the same placements and the same per-photo motion, value for value.

Python writes the cases (specs x aspects x times), Node evaluates the TS twin
on them (tests/collage_twin.mjs), and this test compares every number with a
tolerance far below anything visible on screen.
"""
from __future__ import annotations

import json
import re
import unittest
import subprocess
import sys
from pathlib import Path


from app.collage import (
    MAX_COLLAGE_PHOTOS,
    base_duration,
    camera_filter,
    camera_state,
    camera_supersample,
    exit_mode,
    exit_offset,
    exit_total,
    bg_blur_radius,
    collage_duration,
    collage_graph,
    hash01,
    mat_height,
    normalize_collage,
    photo_frame,
    photo_delay,
    photo_size,
    photo_start,
    push_depth,
    photo_state,
    pin_anchor,
    placements,
)



def _ae(got, want, tol: float = 1e-9) -> bool:
    """Approximate equality for scalars or flat sequences — a plain-stdlib
    stand-in for pytest's approx, so these tests run under unittest
    discovery (the CI harness) with no pytest dependency."""
    if isinstance(want, (list, tuple)):
        return len(got) == len(want) and all(
            abs(float(g) - float(w)) <= tol for g, w in zip(got, want))
    return abs(float(got) - float(want)) <= tol

DRIVER = Path(__file__).resolve().parent / "collage_twin.mjs"
TOL = 1e-9


def _run_node(cases: dict) -> list:
    proc = subprocess.run(
        ["node", "--experimental-strip-types", str(DRIVER)],
        input=json.dumps(cases), capture_output=True, text=True, timeout=120,
        cwd=Path(__file__).resolve().parent,
    )
    if proc.returncode != 0:
        raise AssertionError(f"collage twin driver failed:\n{proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout)


def _spec(layout: str, animation: str, shape: str, count: int, seed: int,
          delays: list | None = None, hold: float | None = None, sizes: list | None = None,
          depth: bool = False, beat_sync: bool = False, beats: list | None = None,
          exit: str | None = None, camera: str | None = None) -> dict:
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
    if depth:
        spec["depth"] = True
    if exit is not None:
        spec["exit"] = exit
    if camera is not None:
        spec["camera"] = camera
    if beat_sync:
        spec["beatSync"] = True
        if beats is not None:
            spec["beats"] = beats
    return spec


def _cases() -> list[dict]:
    cases = []
    times = [0.0, 0.15, 0.31, 0.5, 0.7, 1.0, 1.37, 2.0, 3.5]
    idx = 0
    for layout in ("stack", "grid", "scatter", "filmstrip", "fan", "masonry"):
        for animation in ("drop", "pop", "swing", "flip", "none"):
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
    # depth push-back: earlier photos shrink and dim as later ones land
    # (and swing/none ignore the flag — nothing to push)
    for anim in ("drop", "pop", "swing", "none"):
        cases.append({
            "id": f"depth-{anim}",
            "spec": _spec("stack", anim, "4:3", 5, 4, delays=[0.1, 0.6, 0.2, 0.9, 0.3], hold=2, depth=True),
            "aspect": 16 / 9, "leadIn": 0.5,
            "times": [0.0, 0.3, 0.75, 1.3, 1.9, 2.7, 4.4],
            "hashArgs": [4, 5, 11], "matW": 34,
        })
    cases.append({
        "id": "depth-pop-sized",
        "spec": _spec("fan", "pop", "square", 6, 11, delays=[0.4, 0.4, 0.4, 0.4, 0.4, 0.4], sizes=[1.5, 0.6, 1, 1.2, 0.8, 1], hold=1, depth=True),
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.6, 1.4, 2.2, 3.1],
        "hashArgs": [11, 6, 11], "matW": 30,
    })
    # beat sync: nominal starts roll forward to the next stored beat; several
    # photos may share a beat, and a beat list that runs out keeps nominal
    for layout in ("honeycomb", "zigzag", "arc", "photowall", "booth", "silhouette", "cube"):
        cases.append({
            "id": f"{layout}-none-4:3-n6",
            "spec": _spec(layout, "none", "4:3", 6, 11),
            "aspect": 16 / 9, "leadIn": 0.0, "times": times, "hashArgs": [11, 6, 29], "matW": 28,
        })
    for animation in ("fade", "slide", "rise", "tumble", "zoom", "fold", "glitch", "ink", "brush"):
        cases.append({
            "id": f"grid-{animation}-4:3-n4",
            "spec": _spec("grid", animation, "4:3", 4, 5, hold=2),
            "aspect": 16 / 9, "leadIn": 0.3, "times": times, "hashArgs": [5, 4, 11], "matW": 30,
        })
    kb = _spec("grid", "drop", "4:3", 4, 8, delays=[0.2, 0.4, 0.4, 0.4], hold=3)
    kb["kenBurns"] = True
    kb["sway"] = True
    cases.append({
        "id": "kenburns-sway",
        "spec": kb,
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.4, 1.2, 2.5, 4.0, 6.0],
        "hashArgs": [8, 4, 11], "matW": 30,
    })
    cases.append({
        "id": "beats-drop",
        "spec": _spec("masonry", "drop", "4:3", 4, 5, delays=[0.15, 0.2, 2.0, 0.1], hold=1.5, beat_sync=True, beats=[0.4, 0.8, 1.6, 2.4, 3.2]),
        "aspect": 16 / 9, "leadIn": 0.25,
        "times": [0.0, 0.4, 0.8, 1.65, 2.45, 3.3, 4.5],
        "hashArgs": [5, 4, 11], "matW": 30,
    })
    cases.append({
        "id": "beats-shared-and-exhausted",
        "spec": _spec("filmstrip", "pop", "square", 5, 3, delays=[0.1, 0.2, 0.15, 0.3, 0.2], hold=1, beat_sync=True, beats=[0.5, 1.0]),
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.55, 1.05, 1.8],
        "hashArgs": [3, 5, 11], "matW": 30,
    })
    cases.append({
        "id": "beats-ignored-without-flag",
        "spec": _spec("grid", "drop", "4:3", 3, 6, delays=[0.15, 0.2, 0.2], hold=1, beat_sync=False, beats=[1.0, 2.0, 3.0]),
        "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0, 0.5, 1.05],
        "hashArgs": [6, 3, 11], "matW": 30,
    })
    # exits: the photos leave after the hold — every entrance × every exit
    for exit_mode_name in ("sweep", "deal", "shuffle"):
        for animation in ("drop", "pop", "none", "flip"):
            cases.append({
                "id": f"exit-{exit_mode_name}-{animation}",
                "spec": _spec("stack", animation, "4:3", 5, 9, delays=[0.15, 0.5, 0.4, 0.7, 0.3], hold=2,
                              exit=exit_mode_name),
                "aspect": 16 / 9, "leadIn": 0.5,
                "times": [0.0, 0.8, 2.5, 4.2, 4.6, 5.0, 5.6, 6.2, 7.0],
                "hashArgs": [9, 5, 71], "matW": 34,
            })
    # sweep direction depends on the placement → exercise other aspects
    cases.append({
        "id": "exit-sweep-portrait",
        "spec": _spec("fan", "drop", "3:4", 4, 3, delays=[0.2, 0.3, 0.2, 0.3], hold=1, exit="sweep"),
        "aspect": 9 / 16, "leadIn": 0.0, "times": [0.0, 1.5, 3.0, 3.6, 4.2],
        "hashArgs": [3, 4, 71], "matW": 30,
    })
    # virtual cameras over the composed scene
    for camera in ("pan", "zoom", "telescope", "droste"):
        cases.append({
            "id": f"camera-{camera}",
            "spec": _spec("masonry", "pop", "4:3", 5, 5, delays=[0.15, 0.4, 0.3, 0.5, 0.2], hold=2, camera=camera),
            "aspect": 16 / 9, "leadIn": 0.5,
            "times": [0.0, 0.6, 1.7, 3.0, 4.4, 5.8, 7.0],
            "hashArgs": [5, 5, 11], "matW": 30, "camera": camera,
        })
    cases.append({
        "id": "camera-zoom-exit",
        "spec": _spec("grid", "drop", "square", 3, 2, delays=[0.15, 0.3, 0.3], hold=1.5, camera="zoom", exit="deal"),
        "aspect": 16 / 9, "leadIn": 0.25, "times": [0.0, 1.0, 2.5, 3.4, 4.0, 4.6],
        "hashArgs": [2, 3, 11], "matW": 30, "camera": "zoom",
    })
    # the editor's track→slide beat mapping, evaluated in the TS twin
    for i, mb in enumerate([
        {"beats": [0.0, 0.5, 1.0, 1.5, 2.0], "trackStart": 0.0, "trimStart": 0.0, "trimEnd": 0.0, "holdStart": 1.25},
        {"beats": [10.0, 10.5, 11.0, 11.5], "trackStart": 12.0, "trimStart": 10.0, "trimEnd": 11.4, "holdStart": 5.0},
        {"beats": [0.2, 3.0, "x", True, 7.5], "trackStart": 2.0, "trimStart": 0.5, "trimEnd": 0.0, "holdStart": 1.0},
    ]):
        cases.append({
            "id": f"map-beats-{i}",
            "spec": _spec("stack", "drop", "4:3", 1, 1),
            "aspect": 16 / 9, "leadIn": 0.0, "times": [0.0], "hashArgs": [1, 1, 11], "matW": 30,
            "mapBeats": mb,
        })
    return cases


class CollageTwinTest(unittest.TestCase):
    """Collage maths and generated filter graphs, mirrored in collageCore.ts.

    Plain unittest style so `python -m unittest discover` (the CI harness)
    collects them; pytest runs them just the same.
    """

    def test_collage_twin_engines_agree(self):
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
                    py = photo_state(spec, i, t, lead_in, aspect)
                    ts = got["states"][ti][i]
                    for key in ("dx", "dy", "rot", "scale", "scaleX", "alpha", "dim"):
                        assert abs(ts[key] - py[key]) < TOL, f"{where} t={t} photo {i} {key}: {ts[key]} != {py[key]}"
            # virtual camera: same window, same clamps
            if case.get("camera"):
                for ti, t in enumerate(case["times"]):
                    py = camera_state(spec, t, lead_in, aspect)
                    ts = got["camera"]["states"][ti]
                    for key in ("z", "cx", "cy"):
                        assert abs(ts[key] - py[key]) < TOL, f"{where} t={t} camera {key}: {ts[key]} != {py[key]}"
            # slideLocalBeats (editor-only helper): expected values computed here
            if case.get("mapBeats"):
                mb = case["mapBeats"]
                # hand-computed: local = trackStart + (b - trimStart) - holdStart,
                # beats outside the kept region or before the hold are dropped
                expect = {
                    0: [0.25, 0.75],                                     # beats at/before 1.25-1.0 fall before the hold
                    1: [7.0, 7.5, 8.0],                                  # 11.5 is past trimEnd 11.4
                    2: [3.5, 8.0],                                       # 0.2 < trimStart 0.5; junk skipped
                }[int(case["id"].rsplit("-", 1)[1])]
                assert _ae(got["mapBeats"], expect, TOL), f"{where} mapBeats: {got['mapBeats']} != {expect}"


    def test_untouched_collage_keeps_the_legacy_stagger(self):
        """A spec saved before per-photo timing existed must animate exactly as it
        did: 0.15 s + stagger · i with stagger = min(0.32, 2.4/(n-1))."""
        for n in range(1, 13):
            spec = _spec("stack", "drop", "4:3", n, 1)
            stagger = min(0.32, 2.4 / (n - 1)) if n > 1 else 0.0
            for i in range(n):
                assert abs(photo_start(spec, i, 0.0) - (0.15 + stagger * i)) < TOL, (n, i)
                assert abs(photo_start(spec, i, 0.4) - (0.4 + 0.15 + stagger * i)) < TOL, (n, i)


    def test_duration_follows_the_photo_timings(self):
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


    def test_delay_junk_falls_back_to_defaults(self):
        spec = {"photos": [{"path": "/photos/a.jpg"}, {"path": "/photos/b.jpg", "delay": True},
                           {"path": "/photos/c.jpg", "delay": ""}, {"path": "/photos/d.jpg", "delay": "0.7"},
                           {"path": "/photos/e.jpg", "delay": 99}],
                "layout": "stack", "animation": "drop", "shape": "4:3", "seed": 1}
        assert photo_delay(spec, 0) == 0.15                      # default
        assert photo_delay(spec, 1) == 0.32                      # True → default stagger (n=5 → 2.4/4 → 0.32)
        assert photo_delay(spec, 2) == 0.32                      # "" → default
        assert abs(photo_delay(spec, 3) - 0.7) < TOL             # numeric string accepted
        assert photo_delay(spec, 4) == 30.0                      # clamped


    def test_photo_size_junk_falls_back_to_one(self):
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


    def test_photo_size_scales_the_mat_width(self):
        base = placements(_spec("grid", "none", "4:3", 3, 5), 16 / 9)
        sized = placements(_spec("grid", "none", "4:3", 3, 5, sizes=[1.5, 0.5, None]), 16 / 9)
        assert abs(sized[0]["w"] - base[0]["w"] * 1.5) < TOL
        assert abs(sized[1]["w"] - base[1]["w"] * 0.5) < TOL
        assert abs(sized[2]["w"] - base[2]["w"]) < TOL
        # size never moves the centre — only the mat grows around it
        for a, b in zip(base, sized):
            assert abs(a["cx"] - b["cx"]) < TOL and abs(a["cy"] - b["cy"]) < TOL


    def test_bg_blur_mapping(self):
        assert bg_blur_radius(0) == 1
        assert bg_blur_radius(0.5) == 16
        assert bg_blur_radius(1.0) == 30
        assert bg_blur_radius(None) == 1
        assert bg_blur_radius(7) == 30                           # out-of-range clamps


    def test_placements_are_sane(self):
        for layout in ("stack", "grid", "scatter", "filmstrip", "fan", "masonry"):
            spec = _spec(layout, "drop", "4:3", 9, 5)
            for pl in placements(spec, 16 / 9):
                assert 4 <= pl["cx"] <= 96 and 8 <= pl["cy"] <= 92, (layout, pl)
                assert 15 <= pl["w"] <= 45, (layout, pl)
                assert abs(pl["rot"]) <= 32, (layout, pl)                            # fan spokes + seed offset


    def test_normalize_collage(self):
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
        # the three new layouts are accepted, junk still falls back to stack
        for layout in ("filmstrip", "fan", "masonry", "honeycomb", "zigzag", "arc", "photowall",
                       "booth", "silhouette", "cube"):
            assert normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg"}], "layout": layout}})["layout"] == layout
        assert normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg"}], "layout": "spiral"}})["layout"] == "stack"
        for anim in ("fade", "slide", "rise", "tumble", "zoom", "fold", "glitch", "ink", "brush"):
            assert normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg"}], "animation": anim}})["animation"] == anim
        framed = normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg", "frame": {"shape": "heart", "width": 5, "color": "#ff00aa"}}],
                                                "frame": {"shape": "circle", "width": 2, "color": "#112233"},
                                                "gap": 3, "kenBurns": True, "sway": True, "stickers": "tape"}})
        assert framed["frame"]["shape"] == "circle" and framed["frame"]["width"] == 2
        assert framed["photos"][0]["frame"]["shape"] == "heart"
        assert framed["gap"] == 3 and framed["kenBurns"] is True and framed["sway"] is True
        assert framed["stickers"] == "tape"
        # beat sync + beats: strict flags, sanitised list (clamped, sorted, deduped)
        b = normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg"}],
                                           "beatSync": True, "depth": True,
                                           "beats": [3, 1.0001, 1, "x", True, -5, 700, 2.0001]}})
        assert b["beatSync"] is True and b["depth"] is True
        assert b["beats"] == [0.0, 1.0, 2.0, 3.0, 600.0]            # 2.0001 rounds to 2.0 first
        loose = normalize_collage({"collage": {"photos": [{"path": "/photos/a.jpg"}],
                                               "beatSync": "yes", "depth": 1, "beats": [1]}})
        assert "beatSync" not in loose and "depth" not in loose
        assert loose["beats"] == [1.0]        # the list is data — kept even with the flag off


    def test_filmstrip_is_a_band_of_overlapping_frames(self):
        # up to 5 photos: one centred row, all mats equally wide and overlapping
        pls = placements(_spec("filmstrip", "none", "4:3", 5, 1), 16 / 9)
        assert all(abs(p["cy"] - 50) < TOL and abs(p["rot"]) <= 4 for p in pls)
        assert len({round(p["w"], 6) for p in pls}) == 1
        for a, b in zip(pls, pls[1:]):
            assert b["cx"] - a["cx"] < a["w"]                       # neighbours overlap
        assert pls[0]["cx"] > 4 and pls[-1]["cx"] < 96              # inside the frame
        # beyond 5: two rows
        assert len({round(p["cy"], 6) for p in placements(_spec("filmstrip", "none", "4:3", 7, 1), 16 / 9)}) == 2
        # portrait frames shrink the mats to keep the band inside
        for aspect in (4 / 3, 1.0, 9 / 16):
            for p in placements(_spec("filmstrip", "none", "3:4", 6, 1), aspect):
                assert 10 <= p["cx"] <= 90 and 12 <= p["cy"] <= 88, (aspect, p)


    def test_fan_radiates_from_below_the_frame(self):
        for n in (2, 3, 6, 12):
            pls = placements(_spec("fan", "none", "4:3", n, 1), 16 / 9)
            # tilts follow the spoke angles; a seeded offset can rotate the whole fan
            for p in pls:
                assert -32 <= p["rot"] <= 32
                assert 8 < p["cx"] < 92
                assert p["cy"] < 96                                  # above the pivot
            # the middle card is the most upright
            mid = pls[(n - 1) // 2]
            assert abs(mid["rot"]) <= max(abs(p["rot"]) for p in pls) + TOL
        # a single photo is simply centred and upright
        solo = placements(_spec("fan", "none", "4:3", 1, 1), 16 / 9)[0]
        assert abs(solo["cx"] - 50) < TOL and abs(solo["rot"]) < TOL


    def test_masonry_stacks_columns_with_varied_sizes(self):
        for n, shape in ((4, "4:3"), (9, "4:3"), (12, "3:4"), (12, "square")):
            spec = _spec("masonry", "none", shape, n, 5)
            pls = placements(spec, 16 / 9)
            for p in pls:
                assert 4 <= p["cx"] <= 96 and 10 <= p["cy"] <= 90, (n, shape, p)
            # seeded size variety (a plain grid this is not)
            assert len({round(p["w"], 4) for p in pls}) > 1
            # the stack must fit the frame: the deepest column stays inside
            k = 0.91 / (1.0 if shape == "square" else (0.75 if shape == "3:4" else 4 / 3)) + 0.25
            cols = {}
            for p in pls:
                cols.setdefault(round(p["cx"], 3), []).append(p)
            for cx, group in cols.items():
                deepest = max(p["cy"] + p["w"] * k / (16 / 9) / 2 for p in group)
                assert deepest <= 88.5, (n, shape, cx, deepest)
        # two photos: side-by-side columns
        two = placements(_spec("masonry", "none", "4:3", 2, 9), 16 / 9)
        assert abs(two[0]["cy"] - two[1]["cy"]) < 5 and abs(two[0]["cx"] - two[1]["cx"]) > 20


    def test_beat_sync_snaps_arrivals_to_the_next_beat(self):
        spec = _spec("grid", "drop", "4:3", 4, 1, delays=[0.15, 0.2, 0.3, 0.2], beat_sync=True,
                     beats=[0.4, 0.8, 1.6, 2.4])
        # nominal starts 0.15/0.35/0.65/0.85 → snapped 0.4/0.4/0.8/1.6
        assert [round(photo_start(spec, i), 6) for i in range(4)] == [0.4, 0.4, 0.8, 1.6]
        # lead-in shifts everything (the beats are in the hold clock)
        assert [round(photo_start(spec, i, 0.5), 6) for i in range(4)] == [0.9, 0.9, 1.3, 2.1]
        # a beat exactly at the nominal time is kept (no roll-forward)
        exact = _spec("grid", "drop", "4:3", 1, 1, delays=[0.4], beat_sync=True, beats=[0.4, 1.0])
        assert abs(photo_start(exact, 0) - 0.4) < TOL
        # beat list exhausted → later photos fall back to nominal timing
        tail = _spec("grid", "drop", "4:3", 2, 1, delays=[0.1, 0.1], beat_sync=True, beats=[0.15])
        assert abs(photo_start(tail, 0) - 0.15) < TOL and abs(photo_start(tail, 1) - 0.2) < TOL
        # junk in the list is ignored, not fatal
        junk = _spec("grid", "drop", "4:3", 2, 1, delays=[0.1, 0.1], beat_sync=True,
                     beats=["x", True, 0.3, None])
        assert abs(photo_start(junk, 0) - 0.3) < TOL
        # without the flag the beats sit unused
        off = _spec("grid", "drop", "4:3", 2, 1, delays=[0.1, 0.1], beat_sync=False, beats=[5.0])
        assert abs(photo_start(off, 0) - 0.1) < TOL and abs(photo_start(off, 1) - 0.2) < TOL
        # the derived duration follows the snapped start
        assert abs(collage_duration(spec) - (1.6 + 0.55 + 2)) < TOL     # hold defaults to 2


    def test_depth_push_back_shrinks_and_dims_the_pile(self):
        spec = _spec("stack", "drop", "4:3", 4, 1, delays=[0.1, 1.0, 1.0, 1.0], depth=True)
        # photo 0 lands at 0.1 and is alone until photo 1 starts at 1.1: no push yet
        assert push_depth(spec, 0, 1.0) == 0.0
        assert photo_state(spec, 0, 1.0)["scale"] == 1.0
        assert photo_state(spec, 0, 1.0)["dim"] == 0.0
        # photo 1's entrance is 40% through at 1.1+0.22 — the push starts there
        # and eases over 0.35 s (smoothstep: 0.5 at the window's midpoint)
        assert push_depth(spec, 0, 1.32) == 0.0                          # push begins
        assert abs(push_depth(spec, 0, 1.495) - 0.5) < 0.02              # mid-push
        done = photo_state(spec, 0, 1.7)                                 # push 1 complete (window ends 1.67)
        assert abs(done["scale"] - (1 - 0.06)) < TOL and abs(done["dim"] - 0.11) < TOL
        # all three later photos landed → push capped at 4 (only 3 here)
        late = photo_state(spec, 0, 9.9)
        assert abs(late["scale"] - (1 - 0.06 * 3)) < TOL and abs(late["dim"] - 0.11 * 3) < TOL
        # the last photo is never pushed
        assert push_depth(spec, 3, 9.9) == 0.0
        # a big pile caps at 4 pushes
        pile = _spec("stack", "drop", "4:3", 12, 1, depth=True)
        assert abs(push_depth(pile, 0, 999.0) - 4.0) < TOL
        # swing and none have no arrivals to react to — the flag is inert
        for anim in ("swing", "none"):
            inert = _spec("stack", anim, "4:3", 5, 1, depth=True)
            assert push_depth(inert, 0, 99.0) == 0.0
            assert photo_state(inert, 0, 99.0)["scale"] == 1.0
        # pop multiplies its spring by the push-back
        pop = _spec("stack", "pop", "4:3", 3, 1, delays=[0.05, 1.0, 1.0], depth=True)
        settled = photo_state(pop, 0, 2.0)     # between photo 1's push and photo 2's
        assert abs(settled["scale"] - (1 - 0.06)) < TOL and abs(settled["dim"] - 0.11) < TOL


    def test_exit_math_and_derived_duration(self):
        spec = _spec("stack", "drop", "4:3", 4, 1, delays=[0.15, 0.3, 0.3, 0.3], hold=2)
        assert exit_mode(spec) == "none" and exit_total(spec) == 0.0
        assert abs(collage_duration(spec) - base_duration(spec)) < TOL
        for mode, total in (("sweep", 0.45 + 0.05 * 3), ("shuffle", 0.4 + 0.1 * 3),
                            ("deal", 0.32 + 0.22 * 3)):
            exited = _spec("stack", "drop", "4:3", 4, 1, delays=[0.15, 0.3, 0.3, 0.3], hold=2, exit=mode)
            assert exit_mode(exited) == mode
            assert abs(exit_total(exited) - total) < TOL, mode
            assert abs(collage_duration(exited) - (base_duration(exited) + total)) < TOL, mode
        # deal clears the pile top-first: photo n-1 leaves immediately, photo 0 last
        deal = _spec("stack", "drop", "4:3", 4, 1, exit="deal")
        assert [exit_offset(deal, i) for i in range(4)] == [0.66, 0.44, 0.22, 0.0]
        sweep = _spec("stack", "drop", "4:3", 4, 1, exit="sweep")
        assert _ae([exit_offset(sweep, i) for i in range(4)], [0.0, 0.05, 0.1, 0.15])
        # 'none' entrance: the exit still applies after the plain hold
        plain = _spec("grid", "none", "4:3", 3, 1, hold=2, exit="sweep")
        assert abs(collage_duration(plain) - (2 + 0.45 + 0.05 * 2)) < TOL


    def test_exit_state_curves(self):
        # sweep: a photo right of centre flies right, a centred one flies up
        spec = _spec("grid", "none", "4:3", 2, 1, hold=1, exit="sweep")
        base = base_duration(spec)                      # 'none' → 1 s hold
        mid = photo_state(spec, 0, base + 0.3)          # grid photo 0 sits left of centre
        assert mid["dx"] < 0 and mid["dy"] == 0.0, mid  # flies left
        late = photo_state(spec, 0, base + 0.4)         # inside the final fade quarter
        assert 0 < late["alpha"] < 1
        gone = photo_state(spec, 0, base + 0.5)
        assert gone["alpha"] == 0.0 and abs(gone["dx"]) > 60
        before = photo_state(spec, 0, base - 0.01)
        assert before["dx"] == 0.0 and before["alpha"] == 1.0
        # deal: the arc dy is -6*sin(pi*qe), spin +25 deg, and photo n-1 leaves first
        deal = _spec("stack", "none", "4:3", 3, 1, hold=1, exit="deal")
        b = base_duration(deal)
        last_mid = photo_state(deal, 2, b + 0.16)       # half through its 0.32 s fly
        assert abs(last_mid["dy"] - (-6.0)) < TOL       # sin(pi/2) = 1
        assert abs(last_mid["rot"] - 12.5) < TOL
        first_not_started = photo_state(deal, 0, b + 0.16)
        assert first_not_started["dx"] == 0.0 and first_not_started["alpha"] == 1.0
        # shuffle: seeded directions are deterministic
        sh = _spec("scatter", "none", "4:3", 4, 7, hold=1, exit="shuffle")
        b = base_duration(sh)
        a = photo_state(sh, 1, b + 0.2)["dx"]
        again = photo_state(sh, 1, b + 0.2)["dx"]
        assert a == again


    def test_flip_entrance_opens_edge_on(self):
        spec = _spec("grid", "flip", "4:3", 2, 1, delays=[0.1, 0.3])
        start = photo_start(spec, 0, 0.0)
        edge = photo_state(spec, 0, start)
        assert abs(edge["scaleX"] - 0.04) < TOL and edge["alpha"] == 0.0
        mid = photo_state(spec, 0, start + 0.225)       # halfway through the 0.45 s flip
        assert 0.04 < mid["scaleX"] < 1.2 and abs(mid["rot"]) > 0   # outBack may overshoot
        settled = photo_state(spec, 0, start + 0.45)
        assert abs(settled["scaleX"] - 1.0) < 1e-6 and abs(settled["rot"]) < TOL
        # depth push-back applies to flip too (uniform shrink + dim)
        deep = _spec("grid", "flip", "4:3", 3, 1, delays=[0.05, 1.0, 1.0], depth=True)
        pushed = photo_state(deep, 0, 2.6)
        assert abs(pushed["scale"] - (1 - 0.06 * 2)) < TOL and abs(pushed["dim"] - 0.11 * 2) < TOL  # two later photos


    def test_camera_state_windows_stay_inside_the_frame(self):
        ident = camera_state(_spec("stack", "drop", "4:3", 3, 1), 1.0)
        assert (ident["z"], ident["cx"], ident["cy"]) == (1.0, 50.0, 50.0)
        # pan: a slight zoom drifting across, y fixed
        pan = _spec("grid", "pop", "4:3", 4, 1, hold=2, camera="pan")
        d = collage_duration(pan)
        for t, cx in ((0.0, 54.0), (d / 2, 50.0), (d, 46.0)):
            st = camera_state(pan, t)
            assert abs(st["cx"] - cx) < TOL and abs(st["cy"] - 50) < TOL and abs(st["z"] - 1.09) < TOL
        # zoom family: centres on the LAST photo's anchor, clamped inside the frame
        for mode in ("zoom", "telescope", "droste"):
            spec = _spec("masonry", "drop", "4:3", 5, 5, hold=2, camera=mode)
            d = collage_duration(spec)
            zs = []
            for k in range(21):
                st = camera_state(spec, d * k / 20)
                half = 50 / st["z"]
                assert half - 1e-9 <= st["cx"] <= 100 - half + 1e-9, (mode, st)
                assert half - 1e-9 <= st["cy"] <= 100 - half + 1e-9, (mode, st)
                zs.append(st["z"])
            assert all(b >= a for a, b in zip(zs, zs[1:]))     # monotonic zoom in
            assert zs[0] == 1.0 and zs[-1] > 1.3, (mode, zs[-1])
        # lead-in shifts the clock, not the curve
        spec = _spec("grid", "pop", "4:3", 4, 1, hold=2, camera="zoom")
        d = collage_duration(spec)
        assert abs(camera_state(spec, 0.5 + d / 2, 0.5)["z"] - camera_state(spec, d / 2)["z"]) < TOL


    def test_graph_builds_for_every_animation(self):
        for animation in ("drop", "pop", "swing", "none", "fade", "slide", "zoom", "fold", "glitch", "ink"):
            for layout in ("stack", "grid", "scatter"):
                item = {"collage": _spec(layout, animation, "square", 4, 9)}
                built = collage_graph(item, 1280, 720, 25.0, 3.0, 0.5, 1, "cb")
                assert built is not None, (animation, layout)
                lines, last = built
                assert last == "o3"
                graph = "".join(lines)
                assert "[cb][lv0_0]overlay=" in graph
                # every photo input is referenced, and every photo's ladder
                # chains into the single composition chain that ends at o3
                for k in (1, 2, 3, 4):
                    assert f"[{k}:v]scale=" in graph
                assert graph.count("]overlay=x='") >= 4   # one per photo, more when laddered
                assert "[o3]" in lines[-1]


    def test_graph_overlays_are_centre_anchored(self):
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


    def test_graph_places_the_mat_centre_on_the_anchor(self):
        """Parse the generated graph and prove the mat's centre lands on the
        layout anchor — the same translate(-50%, …) the DOM preview applies.
        Catches both Phase-1 positioning bugs: overlaying at the anchor instead
        of anchor-minus-half-canvas, and re-offsetting the mat inside its own
        sprite (shadow + mat are both pre-padded, so their overlay is at 0:0)."""
        import re
        for layout in ("grid", "stack", "scatter", "filmstrip", "fan", "masonry"):
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


    def test_graph_depth_push_back(self):
        """Depth adds a per-frame shrink the overlay anchors track, and dims the
        mat through a per-photo sendcmd ladder (eq expressions evaluate only
        once, so the dim cannot be a static expression). Only photos that
        actually get landed on carry the ladder — and never swing/none."""
        item = {"collage": _spec("stack", "drop", "4:3", 4, 7, delays=[0.1, 0.5, 0.5, 0.5], depth=True)}
        lines, _ = collage_graph(item, 1280, 720, 25.0, 6.0, 0.5, 1, "cb")
        graph = "".join(lines)
        assert graph.count("eq@dim") >= 3 and graph.count("sendcmd=commands=") == 3   # last photo has no pusher
        # the shrink itself is a ladder of static sizes (scale freezes its
        # output size at init, so the shrink cannot be an expression): every
        # pushed photo carries one, ending below its full canvas size
        assert graph.count("split=") == 3                            # last photo has no pusher
        for i in range(3):
            cw = int(re.search(rf"\[m{i}b\]pad=(\d+):", graph).group(1))
            sizes = [int(m) for m in re.findall(rf"\[b{i}_\d+\]scale=(\d+):", graph)]
            assert 1 < len(sizes) < 80, (i, len(sizes))
            # photo i is pushed by the 3-i photos after it: it settles at
            # (1 - 0.06*(3-i)) of its canvas, give or take a pixel of rounding
            settled = (1 - 0.06 * (3 - i)) * cw
            assert abs(min(sizes) - settled) <= 2 and max(sizes) <= cw, (i, cw, sizes)
        # the ladder drives eq per frame: multiplicative M = 1 - 0.11*pushes,
        # brightness 128*(M-1)/255 — the CSS brightness() equivalent
        assert " eq@dim0 contrast 0.67" in graph                      # 3 pushes: 1 - 0.33
        assert " eq@dim0 brightness -0.165" in graph                  # 128*(0.67-1)/255
        assert " eq@dim0 saturation 0.67" in graph
        assert " eq@dim1 contrast 0.78" in graph and " eq@dim2 contrast 0.89" in graph
        pop = {"collage": _spec("stack", "pop", "4:3", 3, 7, depth=True)}
        plines, _ = collage_graph(pop, 1280, 720, 25.0, 6.0, 0.0, 1, "cb")
        pgraph = "".join(plines)
        # pop always scales: every photo carries a ladder even without a pusher
        assert pgraph.count("sendcmd=commands=") == 2 and pgraph.count("split=") == 3
        for anim in ("swing", "none"):
            inert = {"collage": _spec("stack", anim, "4:3", 4, 7, depth=True)}
            igraph = "".join(collage_graph(inert, 1280, 720, 25.0, 6.0, 0.5, 1, "cb")[0])
            assert "eq@dim" not in igraph and "split=" not in igraph   # size never varies
        # depth off: the plain Phase-1 graph, untouched
        plain = {"collage": _spec("stack", "drop", "4:3", 4, 7, depth=False)}
        ggraph = "".join(collage_graph(plain, 1280, 720, 25.0, 6.0, 0.5, 1, "cb")[0])
        assert "eq@dim" not in ggraph and "split=" not in ggraph


    def test_graph_exits_and_flip_and_camera(self):
        # exits: windowed enable, a fade-out at the end of every fly, and the
        # fly terms appended to the position expressions
        item = {"collage": _spec("stack", "drop", "4:3", 4, 7, delays=[0.15, 0.3, 0.3, 0.3], hold=2, exit="deal")}
        lines, _ = collage_graph(item, 1280, 720, 25.0, 8.0, 0.5, 1, "cb")
        graph = "".join(lines)
        assert graph.count("between(t,") == 4                     # every photo leaves
        assert graph.count("fade=t=out") == 4
        assert graph.count("*sin(PI*") == 4                       # deal's dy arc, one per photo
        # 'none' entrance + exit: photos are on screen from frame 0
        none_item = {"collage": _spec("grid", "none", "4:3", 3, 7, hold=1, exit="sweep")}
        nlines, _ = collage_graph(none_item, 1280, 720, 25.0, 5.0, 0.5, 1, "cb")
        ngraph = "".join(nlines)
        assert ngraph.count("between(t,0,") == 3
        # flip: width follows the outBack curve, height only the depth shrink
        flip = {"collage": _spec("grid", "flip", "4:3", 2, 3, depth=True)}
        flines, _ = collage_graph(flip, 1280, 720, 25.0, 4.0, 0.5, 1, "cb")
        fgraph = "".join(flines)
        assert "rotate='(-4*(1-" in fgraph
        # flip + depth: the twin's pushDepth covers flip too, so the render
        # must dim it like drop/pop do (the sizes already come from photoState)
        assert "sendcmd=commands=" in fgraph and "eq@dim0" in fgraph
        # flip's width run is a ladder of static scales with windowed enables;
        # the width climbs from edge-on to the full canvas width
        flevels = re.findall(r"\[b0_\d+\]scale=(\d+):(\d+)", fgraph)
        assert 5 < len(flevels) < 80
        full_w = int(re.search(r"\[m0b\]pad=(\d+):", fgraph).group(1))
        assert min(int(w) for w, _ in flevels) < 0.35 * full_w    # edge-on start
        assert abs(int(flevels[-1][0]) - 0.94 * full_w) <= 2    # depth: 1 pusher shrinks it
        assert max(int(w) for w, _ in flevels) > full_w           # outBack overshoot
        assert fgraph.count("enable='between(t,") >= len(flevels)  # every window
        # camera: a supersampled zoompan over the composed scene, driven by
        # the input time so it follows camera_state()'s lead-in-shifted
        # smoothstep. NOT a crop with a time-dependent size: crop evaluates
        # w/h once, at init, with t = NaN — that camera rendered a static
        # frame (test_collage_camera_ffmpeg renders the real thing).
        pan = {"collage": _spec("grid", "pop", "4:3", 4, 5, hold=2, camera="pan")}
        plines, plast = collage_graph(pan, 1280, 720, 25.0, 6.0, 0.5, 1, "cb")
        pgraph = "".join(plines)
        assert plast == "cam"
        assert plines[-1].startswith("[o3]scale=iw*4:ih*4:flags=bicubic,format=yuv444p,zoompan=z='1.09':x='(54-8*(")
        assert "(it-0.5)/" in plines[-1] and ":d=1:s=1280x720:fps=25,setsar=1[cam];" in plines[-1]
        zoom = {"collage": _spec("masonry", "drop", "4:3", 5, 5, hold=2, camera="zoom")}
        zlines, zlast = collage_graph(zoom, 1280, 720, 25.0, 6.0, 0.5, 1, "cb")
        zgraph = "".join(zlines)
        assert zlast == "cam" and "zoompan=z='1+0.35*(min(max((it-0.5)/" in zlines[-1]
        assert "*iw/100-iw/(2*zoom)':y='" in zlines[-1] and "*ih/100-ih/(2*zoom)':d=1" in zlines[-1]
        for graph in (pgraph, zgraph):
            assert re.search(r"crop=(w=)?'", graph) is None, "crop sizes must be literal: crop evaluates w/h once"
            assert re.search(r"crop=[^,;\[]*\bt\b", graph) is None
        # 1080p supersamples 3×, 4K not at all (pixel budget)
        assert camera_supersample(1920, 1080) == 3 and camera_supersample(3840, 2160) == 1
        assert camera_supersample(1280, 720) == 4 and camera_supersample(2560, 1440) == 2
        big = collage_graph(zoom, 3840, 2160, 30, 6.0, 0.5, 1, "cb")[0][-1]
        assert big.startswith("[o4]format=yuv444p,zoompan=") and ":s=3840x2160:fps=30," in big
        assert camera_filter(normalize_collage(plain := {"collage": _spec("grid", "pop", "4:3", 4, 5, hold=2)}), 1280, 720, 25, 0.5) is None
        # no camera → the composed photos label is the result, no zoompan
        gglines, gglast = collage_graph(plain, 1280, 720, 25.0, 6.0, 0.5, 1, "cb")
        assert gglast == "o3" and "zoompan" not in "".join(gglines)


    def test_graph_ladder_handles_repeated_settle_size(self):
        """The entrance's settle size often repeats a size it crossed earlier —
        outBack passes through scale 1.0 on the way up and settles there — which
        merges the final (open-ended) run into an early level's windows. That
        level is not last in first-appearance order, so the open window must be
        handled wherever it lands (regression: min(None, t_end) raised TypeError
        and pop+deal previews died at 2%)."""
        def merged_count(spec, fps):
            lines, last = collage_graph({"collage": spec}, 1280, 720, fps, 4.0, 0.5, 1, "cb")
            return sum(1 for l in lines if "enable='" in l
                       and "+" in l.split("enable='")[1].split("'")[0]), last
        base = {"photos": [{"path": "/photos/p0.jpg"}, {"path": "/photos/p1.jpg"},
                           {"path": "/photos/p2.jpg"}],
                "layout": "grid", "animation": "pop", "shape": "4:3", "seed": 1, "hold": 2}
        n, last = merged_count(base, 24.0)                     # no exit: open window
        assert n >= 1 and last == "o2"
        n, last = merged_count({**base, "exit": "deal"}, 24.0)  # exit: windowed to t_end
        assert n >= 1 and last == "o2"
        # the open window resolves to gte/between, never to a bare None
        g = "".join(collage_graph({"collage": {**base, "exit": "deal"}}, 1280, 720, 24.0, 4.0, 0.5, 1, "cb")[0])
        assert "None" not in g


    def test_graph_none_without_collage(self):
        assert collage_graph({"collage": {"photos": []}}, 1280, 720, 25.0, 3.0, 0.0, 1, "cb") is None
        assert collage_graph({}, 1280, 720, 25.0, 3.0, 0.0, 1, "cb") is None

    def test_photo_frame_defaults_and_override(self):
        d = photo_frame({}, None)
        assert d["shape"] == "polaroid" and abs(d["width"] - 4.5) < TOL and d["color"] == "#ffffff" and d["shadow"] is True
        spec = {"frame": {"shape": "circle", "width": 6, "color": "#00ff00", "shadow": False}}
        assert photo_frame(spec, None)["shape"] == "circle"
        assert photo_frame(spec, {"frame": {"shape": "heart"}})["shape"] == "heart"
        assert abs(mat_height(34, "4:3", 16 / 9) - mat_height(34, "4:3", 16 / 9, {"shape": "polaroid"})) < TOL

    def test_new_layouts_place_photos(self):
        for layout in ("honeycomb", "zigzag", "arc", "photowall", "booth", "silhouette", "cube"):
            spec = _spec(layout, "none", "4:3", 6, 5)
            pls = placements(spec, 16 / 9)
            assert len(pls) == 6, layout
            for pl in pls:
                assert 0 <= pl["cx"] <= 100 and 0 <= pl["cy"] <= 100, (layout, pl)
                assert 6 <= pl["w"] <= 80, (layout, pl)

    def test_graph_shape_mask_and_no_shadow(self):
        item = {"collage": {**_spec("grid", "none", "4:3", 2, 1),
                            "frame": {"shape": "heart", "color": "#ff88aa", "width": 3, "shadow": False}}}
        built = collage_graph(item, 1280, 720, 25.0, 3.0, 0.0, 1, "cb")
        assert built is not None
        graph = "".join(built[0])
        assert "geq=" in graph and "0xff88aa" in graph
        assert "boxblur" not in graph
