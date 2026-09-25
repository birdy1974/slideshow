"""Stacked text effects (app/text_motion.py) beyond the twin-engine check:
registry v2, the v1 -> textFx migration (and its browser twin), how layers
compose, the text frame's colour A -> B background, the fonts, the renderer's
textFx path and — when an FFmpeg with libass is on PATH — the burnt-in pixels.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.database import Database
from app.font_registry import available_style, font_metrics, font_path
from app.renderer import Renderer
from app.text_effects import text_effect_catalog
from app.text_motion import (
    BgChange, Caption, Compiler, Layer, Shaper, bg_region, compile_caption, contrast_colour, effects, evaluate,
    layer_window, legacy_to_stack, motion_registry, parse_stack, region_contains, resolve_font, rgb, stack_fallback_fields,
)

REPO = Path(__file__).resolve().parents[2]
FONTS = REPO / "public" / "fonts"
FONT_REGISTRY = json.loads((REPO / "registry" / "fonts.json").read_text(encoding="utf-8"))
MIGRATION_DRIVER = Path(__file__).resolve().parent / "text_fx_migration.mjs"


def _ids(stack: list[dict]) -> list[str]:
    return [layer["effect"] for layer in stack]


def _caption(stack: list[Layer], text: str = "LEFT RIGHT", **kw) -> Caption:
    base = dict(text=text, x=640.0, y=360.0, em=64.0, stack=stack, start=0.0, end=4.0, frame_w=1280, frame_h=720,
                fps=25.0, family="Montserrat", bold=True, colour="#ffffff")
    base.update(kw)
    return Caption(**base)


def _ctx(cap: Caption):
    return Compiler(cap, FONTS).ctx


# ---------------------------------------------------------------------------
# Registry v2
# ---------------------------------------------------------------------------
class MotionRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = motion_registry()
        self.fx = effects()

    def test_ids_are_unique_and_every_reference_resolves(self) -> None:
        ids = [e["id"] for e in self.reg["effects"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 120)
        categories = {c["id"] for c in self.reg["categories"]}
        for e in self.reg["effects"]:
            self.assertIn(e["phase"], ("in", "hold", "out"), e["id"])
            self.assertIn(e["category"], categories, e["id"])
            self.assertIn(e.get("unit", "text"), ("char", "word", "line", "text"), e["id"])
        families = {f["family"] for f in FONT_REGISTRY["families"]}
        for preset in self.reg["presets"]:
            self.assertTrue(preset["layers"], preset["id"])
            for layer in preset["layers"]:
                self.assertIn(layer["effect"], self.fx, preset["id"])
            if "font" in preset:
                self.assertIn(preset["font"]["family"], families, preset["id"])
            if "frame" in preset:
                frame = preset["frame"]
                for key in ("background", "background2"):
                    self.assertRegex(frame[key], r"^#[0-9a-fA-F]{6}$", preset["id"])
                self.assertGreater(frame["time"], 0)

    def test_every_v1_effect_has_exactly_one_v2_effect(self) -> None:
        legacy: dict[tuple[str, str], str] = {}
        for e in self.reg["effects"]:
            if e.get("legacy"):
                key = (e["legacy"]["slot"], e["legacy"]["label"])
                self.assertNotIn(key, legacy, f"{key} mapped twice")
                legacy[key] = e["id"]
        for entry in text_effect_catalog():
            if entry.get("engine") == "none":
                continue   # "None" is an empty lane now
            self.assertIn((entry["slot"], entry["label"]), legacy, entry["label"])

    def test_the_former_toggles_and_the_new_batches_are_effects(self) -> None:
        for eid in ("resize", "rotate", "squash", "colour-morph", "bouncy", "motion-path",      # the old toggles
                    "tracking-in", "letter-burst", "dancing-shadow", "rainbow-wave",           # Jitter / Prismic
                    "write-on", "pen-boil", "ink-bleed", "write-off",                          # handwriting
                    "bg-follow", "bg-invert", "bg-arrive", "bg-leave", "bg-pulse"):             # colour A -> B
            self.assertIn(eid, self.fx)
        self.assertEqual("hold", self.fx["bouncy"]["phase"])
        self.assertEqual("hold", self.fx["motion-path"]["phase"])


# ---------------------------------------------------------------------------
# v1 fields -> textFx
# ---------------------------------------------------------------------------
class LegacyMigrationTest(unittest.TestCase):
    def test_historic_defaults_become_a_fade_in_and_out(self) -> None:
        self.assertEqual([{"effect": "fade", "duration": 0.5}, {"effect": "fade-out", "duration": 0.5}],
                         legacy_to_stack({"type": "image"}))

    def test_none_everywhere_is_an_empty_stack(self) -> None:
        self.assertEqual([], legacy_to_stack({"type": "title", "textFxEnter": "None", "textFxWhile": "None (static)",
                                              "textFxExit": "None (hold)"}))
        self.assertEqual([], legacy_to_stack({"type": "title", "textEnter": "none", "textExit": "none"}))

    def test_the_toggles_become_while_layers_between_enter_and_exit(self) -> None:
        stack = legacy_to_stack({
            "type": "title", "textFxEnter": "Pop in", "textEnterDuration": 0.8, "textFxExit": "Pop out",
            "textScaleEnabled": True, "textScaleFrom": 0.8, "textScaleTo": 1.3,
            "textRotateEnabled": True, "textRotateFrom": -20, "textRotateTo": 5, "textRotateSpeed": 3,
            "textSquishEnabled": True, "textColorAnimEnabled": True, "textColorFrom": "#ff0000", "textColorTo": "#00ff00",
            "textBouncyEnabled": True, "textBouncyHeight": 20, "textMoveEnabled": True,
        })
        self.assertEqual(["pop-in", "resize", "rotate", "squash", "colour-morph", "bouncy", "motion-path", "pop-out"], _ids(stack))
        self.assertEqual(0.8, stack[0]["duration"])
        self.assertEqual({"from": 0.8, "to": 1.3}, stack[1]["params"])
        self.assertEqual(3.0, stack[2]["duration"])
        self.assertEqual({"from": "#ff0000", "to": "#00ff00"}, stack[4]["params"])
        self.assertEqual(20.0, stack[5]["params"]["height"])

    def test_bouncy_is_one_layer_even_when_both_the_effect_and_the_toggle_were_on(self) -> None:
        bouncy = next(e for e in text_effect_catalog() if e["slot"] == "while" and "ounc" in e["label"])
        stack = legacy_to_stack({"type": "title", "textFxWhile": bouncy["label"], "textBouncyEnabled": True})
        self.assertEqual(1, _ids(stack).count("bouncy"))

    def test_the_untouched_colour_toggle_keeps_the_text_colour(self) -> None:
        # #ffcc33 was the toggle's default "to": an untouched toggle did not animate.
        stack = legacy_to_stack({"type": "title", "fontColor": "#123456", "textColorAnimEnabled": True, "textColorTo": "#ffcc33"})
        self.assertEqual({"from": "#123456", "to": "#123456"}, stack[1]["params"])

    def test_old_xfade_names_map_like_the_v1_editor_showed_them(self) -> None:
        self.assertEqual("wipe-from-left", legacy_to_stack({"type": "title", "textEnter": "Wipe left"})[0]["effect"])
        self.assertEqual("slide-from-top", legacy_to_stack({"type": "title", "textEnter": "Slide down"})[0]["effect"])
        self.assertEqual("fade", legacy_to_stack({"type": "title", "textEnter": "Fade in"})[0]["effect"])
        self.assertEqual("fade-out", legacy_to_stack({"type": "title", "textExit": "Something retired"})[-1]["effect"])

    def test_pictures_use_the_saved_default_style_titles_do_not(self) -> None:
        defaults = {"fxEnter": "Pop in", "fxWhile": "Pulse", "fxExit": "Pop out", "fxWhileSpeed": 3}
        self.assertEqual(["pop-in", "pulse", "pop-out"], _ids(legacy_to_stack({"type": "image"}, defaults)))
        self.assertEqual(3.0, legacy_to_stack({"type": "image"}, defaults)[1]["duration"])
        self.assertEqual(["fade", "fade-out"], _ids(legacy_to_stack({"type": "title"}, defaults)))


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


@unittest.skipUnless(NODE, "needs Node >= 22.6 for --experimental-strip-types")
class LegacyMigrationTwinTest(unittest.TestCase):
    """migrateLegacyTextFx() in the browser and legacy_to_stack() here agree."""

    def _cases(self) -> list[dict]:
        cases: list[dict] = []
        for entry in text_effect_catalog():
            key = {"enter": "textFxEnter", "while": "textFxWhile", "exit": "textFxExit"}[entry["slot"]]
            params = {p["name"]: p.get("default") for p in entry.get("params") or []}
            for kind in ("title", "image"):
                cases.append({"item": {"type": kind, key: entry["label"], "textFxParams": params, "textFxWhileSpeed": 3.25,
                                       "textEnterDuration": 0.7, "textExitDuration": "1.1"}})
        for label in ("Wipe left", "wipe → right", "Slide up", "Slide down", "Fade in", "Fade out", "none", "None", "NONE",
                      "Gone effect", "", "None (static)", "None (hold)"):
            for key in ("textEnter", "textExit", "textFxEnter", "textFxExit", "textFxWhile"):
                cases.append({"item": {"type": "title", key: label}})
        toggles = {
            "textScaleEnabled": True, "textScaleFrom": "0.7", "textScaleTo": 1.6, "textRotateEnabled": True, "textRotateFrom": -30,
            "textRotateTo": "12", "textRotateSpeed": 2.5, "textSquishEnabled": True, "textSquishFrom": 0.3, "textSquishTo": 1.2,
            "textColorAnimEnabled": True, "textColorFrom": "#ff8800", "textColorTo": "#ffcc33", "textBouncyEnabled": True,
            "textBouncyHeight": "18", "textBouncyBounces": 4, "textBouncyDamping": 0.5, "textMoveEnabled": True,
        }
        cases.append({"item": {"type": "title", **toggles}})
        cases.append({"item": {"type": "image", **toggles, "textRotateSpeed": 0, "textColorTo": None}})
        cases.append({"item": {"type": "image", "textEnterDuration": "abc", "textExitDuration": -4, "textFxWhile": "Pulse",
                               "textFxWhileSpeed": 99}})
        cases.append({"item": {"type": "image", "textEnterDuration": 100, "textExitDuration": 0}})
        defaults = {"fxEnter": "Typewriter", "fxWhile": "Wave", "fxExit": "Pop out", "fxWhileSpeed": 1.5}
        cases.append({"item": {"type": "image"}, "defaults": defaults})
        cases.append({"item": {"type": "title"}, "defaults": defaults})
        cases.append({"item": {"type": "image", "textFxWhile": "Count up", "textFxParams": {"from": "5", "to": "95"}}})
        return cases

    def test_every_v1_label_alias_and_toggle(self) -> None:
        cases = self._cases()
        result = subprocess.run([NODE, "--experimental-strip-types", "--no-warnings", str(MIGRATION_DRIVER)], input=json.dumps({"cases": cases}),
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stderr[-2000:])
        browser = json.loads(result.stdout)
        self.assertEqual(len(cases), len(browser))
        mismatches = []
        for case, js in zip(cases, browser):
            py = json.loads(json.dumps(legacy_to_stack(case["item"], case.get("defaults"))))
            if py != js:
                mismatches.append(f"{case}: python {py} != browser {js}")
        self.assertFalse(mismatches, "\n".join(mismatches[:8]))


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
class StackCompositionTest(unittest.TestCase):
    def test_parse_stack_validates_what_the_project_stored(self) -> None:
        stack = parse_stack([
            {"effect": "no-such-effect"}, "junk", {"effect": "fade", "duration": 999, "delay": -3, "intensity": 7},
            {"effect": "pulse", "range": [4, 1], "sync": "bg", "muted": True, "unit": "banana", "loop": "pingpong"},
            {"effect": "wave", "duration": 0},
        ])
        self.assertEqual(["fade", "pulse", "wave"], [layer.effect for layer in stack])
        self.assertEqual((120.0, 0.0, 3.0), (stack[0].duration, stack[0].delay, stack[0].intensity))
        self.assertEqual(((1, 4), "bg", True, None, "pingpong"),
                         (stack[1].range, stack[1].sync, stack[1].muted, stack[1].unit, stack[1].loop))
        self.assertIsNone(stack[2].duration, "no duration = the effect's own default")

    def test_channels_compose_instead_of_overwriting(self) -> None:
        # a text-wide fade and a letter-by-letter fade: opacities multiply
        t = 0.5
        both = _ctx(_caption([Layer("fade", duration=1.0), Layer("split-fade-chars", duration=1.0)], text="AB"))
        only_fade = _ctx(_caption([Layer("fade", duration=1.0)], text="AB"))
        only_split = _ctx(_caption([Layer("split-fade-chars", duration=1.0)], text="AB"))
        self.assertEqual("char", both.gran)
        letter = both.units["char"][1]
        op_both = evaluate(both, letter, t)["tot"]["opacity"]
        op_fade = evaluate(only_fade, only_fade.units[only_fade.gran][0], t)["tot"]["opacity"]
        op_split = evaluate(only_split, only_split.units["char"][1], t)["tot"]["opacity"]
        self.assertAlmostEqual(op_fade * op_split, op_both, places=9)
        self.assertLess(op_both, min(op_fade, op_split))
        # an entrance offset and a loop add up on the same letter
        moving = _ctx(_caption([Layer("slide-from-bottom", duration=1.0), Layer("wave")], text="AB"))
        s = evaluate(moving, moving.units[moving.gran][0], 0.5)
        rise = _ctx(_caption([Layer("slide-from-bottom", duration=1.0)], text="AB"))
        wave = _ctx(_caption([Layer("wave")], text="AB"))
        y_rise = evaluate(rise, rise.units[rise.gran][0], 0.5)["tot"]["y"]
        y_wave = evaluate(wave, wave.units[wave.gran][0], 0.5)["tot"]["y"]
        self.assertAlmostEqual(y_rise + y_wave, s["tot"]["y"], places=9)

    def test_word_range_targets_only_those_words(self) -> None:
        ctx = _ctx(_caption([Layer("fade"), Layer("pulse", range=(1, 1)), Layer("fade-out")], text="one two three"))
        words = ctx.units["word"]
        t = 1.5   # mid-cycle of the 2 s pulse
        scales = [evaluate(ctx, w, t)["tot"]["scale"] for w in words] if ctx.gran == "word" else None
        self.assertIsNotNone(scales, ctx.gran)
        self.assertEqual(1.0, scales[0])
        self.assertNotEqual(1.0, scales[1])
        self.assertEqual(1.0, scales[2])

    def test_two_text_rewriting_effects_in_one_lane_keep_the_top_one(self) -> None:
        ctx = _ctx(_caption([Layer("decrypted-scramble"), Layer("scramble-reveal"), Layer("fade-out")]))
        self.assertIn(0, ctx.conflicts)
        self.assertTrue(any("replaces" in w for w in ctx.warnings), ctx.warnings)
        self.assertEqual("scramble-reveal", ctx.content["in"].layer.effect)

    def test_background_effects_need_a_colour_change(self) -> None:
        ctx = _ctx(_caption([Layer("fade"), Layer("bg-follow"), Layer("pulse", sync="bg")]))
        self.assertIn(1, ctx.conflicts)
        self.assertEqual("no colour change to sync to", ctx.conflicts[2])
        self.assertNotIn("bg-follow", [L.layer.effect for L in ctx.stack], "skipped, not half-applied")

    def test_synced_layers_run_during_the_colour_change(self) -> None:
        bg = BgChange("#14213d", "#f4a261", "circleopen", 1.2, 1.5)
        ctx = _ctx(_caption([Layer("pop-in", sync="bg"), Layer("pulse", sync="bg"), Layer("fade-out")], bg=bg))
        windows = {L.layer.effect: layer_window(ctx, L) for L in ctx.stack}
        self.assertEqual((1.2, 2.7), windows["pop-in"])
        self.assertEqual((1.2, 2.7), windows["pulse"])
        self.assertEqual((0.0, 4.0), windows["fade-out"])


# ---------------------------------------------------------------------------
# Colour A -> B background
# ---------------------------------------------------------------------------
class BackgroundSyncTest(unittest.TestCase):
    def test_regions_follow_ffmpeg_xfade_geometry(self) -> None:
        self.assertEqual({"kind": "rect", "rect": [0.0, 0.0, 480.0, 1080.0], "inside": True}, bg_region("wiperight", 0.25, 1920, 1080))
        self.assertEqual([1440.0, 0.0, 1920.0, 1080.0], bg_region("Wipe left", 0.25, 1920, 1080)["rect"])
        self.assertTrue(region_contains(bg_region("circleopen", 0.5, 1920, 1080), 960, 540))
        self.assertFalse(region_contains(bg_region("circleopen", 0.5, 1920, 1080), 5, 5))
        self.assertEqual("none", bg_region("wiperight", 0.0, 100, 100)["kind"])
        self.assertEqual("all", bg_region("wiperight", 1.0, 100, 100)["kind"])
        self.assertEqual("mix", bg_region("dissolve", 0.5, 100, 100)["kind"])

    def test_contrast_colour_keeps_text_readable_on_colour_b(self) -> None:
        self.assertEqual((17.0, 17.0, 17.0), contrast_colour(rgb("#f4a261"), rgb("#ffffff")))
        self.assertEqual(rgb("#ffffff"), contrast_colour(rgb("#14213d"), rgb("#ffffff")))
        self.assertEqual(rgb("#101010"), contrast_colour(rgb("#f5f1e6"), rgb("#101010")), "readable preferred colour is kept")

    def test_follow_splits_the_text_exactly_at_the_transition_edge(self) -> None:
        bg = BgChange("#14213d", "#f4a261", "wiperight", 1.0, 1.0)
        ctx = _ctx(_caption([Layer("bg-follow")], bg=bg))
        whole = ctx.units[ctx.gran][0] if ctx.gran == "text" else ctx.units["text"][0]
        state = evaluate(ctx, whole, 1.5)
        split = state["split"]
        self.assertIsNotNone(split)
        self.assertEqual([0.0, 0.0, 640.0, 720.0], split["region"]["rect"])
        self.assertEqual(rgb("#ffffff"), tuple(split["a"]))
        self.assertEqual((17.0, 17.0, 17.0), tuple(split["b"]))
        self.assertEqual((17.0, 17.0, 17.0), tuple(evaluate(ctx, whole, 2.5)["colour"]))
        self.assertEqual(rgb("#ffffff"), tuple(evaluate(ctx, whole, 0.5)["colour"]))

    def test_swap_takes_colour_a_and_the_ass_clips_both_sides(self) -> None:
        bg = BgChange("#1d1f24", "#f5f1e6", "wipeup", 1.0, 1.0)
        cap = _caption([Layer("bg-invert")], bg=bg)
        ctx = _ctx(cap)
        after = evaluate(ctx, ctx.units["text"][0], 2.5)["colour"]
        self.assertEqual(rgb("#1d1f24"), tuple(after))
        doc, warnings = compile_caption(cap, FONTS)
        self.assertEqual([], warnings)
        self.assertIn("\\clip(", doc)
        self.assertIn("\\iclip(", doc)
        self.assertIn("Kerning: yes", doc)


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
class FontCatalogueTest(unittest.TestCase):
    def test_handwriting_script_and_typewriter_groups_are_bundled(self) -> None:
        groups: dict[str, list[str]] = {}
        for entry in FONT_REGISTRY["families"]:
            groups.setdefault(entry["group"], []).append(entry["family"])
        self.assertGreaterEqual(len(groups["Handwriting"]), 12)
        self.assertGreaterEqual(len(groups["Script"]), 12)
        self.assertGreaterEqual(len(groups["Typewriter"]), 2)
        self.assertGreaterEqual(sum(len(v) for v in groups.values()), 45)

    def test_every_file_and_licence_is_there(self) -> None:
        licences = {p.name.split("-")[0] for p in (FONTS / "licenses").glob("*.txt")}
        for entry in FONT_REGISTRY["families"]:
            if entry.get("system"):
                continue
            self.assertTrue(entry["files"], entry["family"])
            for style, name in entry["files"].items():
                self.assertTrue((FONTS / name).is_file(), f"{entry['family']} {style}: {name}")
                self.assertIn(style, entry.get("metrics", {}), entry["family"])
            self.assertIn(entry["family"].replace(" ", ""), licences, f"licence of {entry['family']}")
            self.assertIn(entry["licence"], ("OFL-1.1", "Apache-2.0"), entry["family"])

    def test_single_weight_fonts_are_never_faux_bolded(self) -> None:
        self.assertEqual((False, False), available_style("Caveat Brush", True, True))
        self.assertEqual((True, False), available_style("Kalam", True, True))
        self.assertEqual((True, True), available_style("Courier Prime", True, True))
        self.assertEqual(FONTS / "Kalam-Bold.ttf", font_path("Kalam", True, False, FONTS))
        self.assertGreater(font_metrics("Mr Dafoe", False, False)["upm"], 0)

    @unittest.skipUnless(importlib.util.find_spec("uharfbuzz"), "needs uharfbuzz (backend/requirements.txt)")
    def test_harfbuzz_shapes_with_kerning(self) -> None:
        shaper = Shaper(resolve_font("Montserrat", True, False, FONTS))
        self.assertIsNotNone(shaper.hb)
        pair = sum(shaper.advances("AV", 100.0))
        apart = shaper.advances("A", 100.0)[0] + shaper.advances("V", 100.0)[0]
        self.assertLess(pair, apart, "the AV pair is kerned")


# ---------------------------------------------------------------------------
# Renderer: items with textFx
# ---------------------------------------------------------------------------
class RendererTextFxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.settings = Settings(config_dir=base / "config", photos_dir=base / "photos", videos_dir=base / "videos",
                                 output_dir=base / "out", music_dir=base / "music", fonts_dir=FONTS)
        for directory in (self.settings.photos_dir, self.settings.videos_dir, self.settings.work_dir, self.settings.preview_dir):
            directory.mkdir(parents=True, exist_ok=True)
        (self.settings.photos_dir / "a.jpg").write_bytes(b"x" * 64)
        self.renderer = Renderer(Database(base / "fx.db"), self.settings)
        self.renderer._drawtext_supported = True
        self.renderer._ass_supported = True
        self.work = self.settings.work_dir / "job"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _commands(self, media: list[dict]) -> list[list[str]]:
        project = {"id": 1, "media": media, "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps", "bitrate": "8 Mbps",
                                                        "encoder": "libx264", "path": "/output", "filename": "movie"}}
        commands: list[list[str]] = []

        def fake_run(command, cancelled, log_file):
            commands.append(list(command))
            Path(command[-1]).write_bytes(b"segment")

        with mock.patch.object(self.renderer, "_validate_media", return_value=None), \
             mock.patch.object(self.renderer, "_run_ffmpeg", side_effect=fake_run), \
             mock.patch.object(self.renderer, "_make_soundtrack", return_value=None), \
             mock.patch.object(self.renderer, "_probe_duration", return_value=2.0):
            self.work.mkdir(parents=True, exist_ok=True)
            self.renderer.render(project, "render", self.work, threading.Event(), lambda p, s: None)
        return [c for c in commands if "segment-" in c[-1]]

    @staticmethod
    def _graph(command: list[str]) -> str:
        return command[command.index("-vf") + 1] if "-vf" in command else command[command.index("-filter_complex") + 1]

    def test_a_picture_caption_with_a_stack_is_drawn_by_the_compositor(self) -> None:
        commands = self._commands([
            {"id": 1, "type": "image", "path": "/photos/a.jpg", "duration": 4, "effect": "None", "transition": "Fade", "transitionTime": 1,
             "text": "Summer in Portugal", "textEnabled": True, "fontFamily": "Kalam",
             "textFx": [{"effect": "write-on", "duration": 1.2}, {"effect": "pen-boil"}, {"effect": "fade-out", "duration": 0.6}]},
        ])
        graph = self._graph(commands[0])
        self.assertIn("ass=filename=", graph)
        self.assertNotIn("drawtext", graph)
        ass = (self.work / "text-0000.ass").read_text(encoding="utf-8")
        self.assertIn("Kerning: yes", ass)
        self.assertIn(",Kalam,", ass)
        self.assertIn("Dialogue:", ass)

    def test_a_colour_change_title_draws_the_text_after_the_xfade(self) -> None:
        commands = self._commands([
            {"id": 1, "type": "image", "path": "/photos/a.jpg", "duration": 2, "effect": "None", "transition": "Fade", "transitionTime": 1},
            {"id": 2, "type": "title", "path": "Generated frame", "duration": 5, "text": "Sunrise", "transition": "Fade", "transitionTime": 0.5,
             "frameBackground": "#14213d", "frameBackground2": "#f4a261", "frameTransition": "Wipe right", "frameTransitionTime": 1.4,
             "frameTransitionStart": 1.0,
             "textFx": [{"effect": "fade-up-words"}, {"effect": "bg-follow"}, {"effect": "bg-pulse"}, {"effect": "fade-out"}]},
        ])
        graph = self._graph(commands[1])
        self.assertIn("[0:v][1:v]xfade=transition=wiperight:duration=1.4:offset=2", graph)
        self.assertLess(graph.index("xfade="), graph.index("ass=filename="))
        ass = (self.work / "text-0001.ass").read_text(encoding="utf-8")
        # the follow layer splits the words at the wipe edge while it passes
        self.assertIn("\\clip(", ass)
        self.assertIn("\\iclip(", ass)
        self.assertIn("\\1c&H111111&", ass, "readable dark text on the coral colour B")

    def test_an_empty_stack_still_shows_the_caption(self) -> None:
        self._commands([
            {"id": 1, "type": "title", "path": "Generated frame", "duration": 3, "text": "Static", "frameBackground": "#112233", "textFx": []},
        ])
        ass = (self.work / "text-0000.ass").read_text(encoding="utf-8")
        self.assertIn("Static", ass)
        self.assertNotIn("\\fad(", ass)

    def test_without_libass_a_stack_degrades_to_its_fades(self) -> None:
        self.renderer._ass_supported = False
        commands = self._commands([
            {"id": 1, "type": "title", "path": "Generated frame", "duration": 4, "text": "Plain", "frameBackground": "#112233",
             "textFx": [{"effect": "pop-in", "duration": 0.8}, {"effect": "wave"}, {"effect": "letter-burst", "duration": 0.6}]},
        ])
        graph = self._graph(commands[0])
        self.assertIn("drawtext", graph)
        self.assertNotIn("ass=", graph)
        self.assertEqual({"textFxEnter": "Fade", "textEnterDuration": 0.8, "textFxExit": "Fade out", "textExitDuration": 0.6},
                         {k: v for k, v in stack_fallback_fields({"textFx": [{"effect": "pop-in", "duration": 0.8}, {"effect": "wave"},
                                                                             {"effect": "letter-burst", "duration": 0.6}]}).items()
                          if k in ("textFxEnter", "textEnterDuration", "textFxExit", "textExitDuration")})


# ---------------------------------------------------------------------------
# End to end: FFmpeg + libass burn the colour-following caption in
# ---------------------------------------------------------------------------
def _ffmpeg_with_ass() -> str | None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
    except Exception:  # noqa: BLE001
        return None
    have = {line.split()[1] for line in filters.splitlines() if len(line.split()) > 2}
    return ffmpeg if {"ass", "xfade", "color"} <= have else None


FFMPEG = _ffmpeg_with_ass()


@unittest.skipUnless(FFMPEG, "needs an FFmpeg with the ass and xfade filters on PATH")
class BurntInColourFollowTest(unittest.TestCase):
    W, H, FPS = 640, 360, 25

    def test_letters_change_colour_where_colour_b_has_arrived(self) -> None:
        a, b = "#14213d", "#f4a261"
        bg = BgChange(a, b, "wiperight", 0.5, 1.0)
        cap = Caption(text="LEFT RIGHT", x=self.W / 2, y=self.H / 2, em=72.0, stack=[Layer("bg-follow")], start=0.0, end=3.0,
                      family="Montserrat", bold=True, colour="#ffffff", frame_w=self.W, frame_h=self.H, fps=self.FPS, bg=bg)
        doc, _ = compile_caption(cap, FONTS)
        with tempfile.TemporaryDirectory() as tmp:
            ass = Path(tmp) / "follow.ass"
            ass.write_text(doc, encoding="utf-8")
            graph = (f"[0:v][1:v]xfade=transition=wiperight:duration=1:offset=0.5,"
                     f"ass=filename={ass.as_posix()}:fontsdir={FONTS.as_posix()},"
                     f"select='eq(n\\,5)+eq(n\\,25)+eq(n\\,50)',format=rgb24")
            src = f"s={self.W}x{self.H}:r={self.FPS}:d=3"
            result = subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"color=c=0x{a[1:]}:{src}", "-f", "lavfi",
                                     "-i", f"color=c=0x{b[1:]}:{src}", "-filter_complex", graph, "-fps_mode", "passthrough",
                                     "-f", "rawvideo", "-"], capture_output=True, timeout=120)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace")[-1500:])
        size = self.W * self.H * 3
        frames = [result.stdout[i * size:(i + 1) * size] for i in range(len(result.stdout) // size)]
        self.assertEqual(3, len(frames))

        def ink(frame: bytes) -> tuple[list[int], list[int]]:
            """x of near-white and of near-black (#111, the readable colour on B) text pixels."""
            white, dark = [], []
            for y in range(self.H // 2 - 40, self.H // 2 + 40, 2):
                row = y * self.W * 3
                for x in range(0, self.W, 2):
                    r, g, bl = frame[row + 3 * x: row + 3 * x + 3]
                    if r > 225 and g > 225 and bl > 225:
                        white.append(x)
                    elif max(r, g, bl) < 40:
                        dark.append(x)
            return white, dark

        before_w, before_d = ink(frames[0])     # t = 0.2 s: colour A everywhere, white text
        mid_w, mid_d = ink(frames[1])           # t = 1.0 s: B covers x <= 320
        after_w, after_d = ink(frames[2])       # t = 2.0 s: colour B everywhere, dark text
        self.assertGreater(len(before_w), 200)
        self.assertLess(len(before_d), 5)
        self.assertGreater(len(after_d), 200)
        self.assertLess(len(after_w), 5)
        self.assertGreater(len(mid_w), 50)
        self.assertGreater(len(mid_d), 50)
        edge = self.W / 2
        self.assertLessEqual(max(mid_d), edge + 4, "dark letters only where B has arrived")
        self.assertGreaterEqual(min(mid_w), edge - 4, "white letters only on colour A")


if __name__ == "__main__":
    unittest.main()
