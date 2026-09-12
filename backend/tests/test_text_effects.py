"""Tests for the dual-engine dynamic text effects (backend/app/text_effects.py).

Option 1 from docs/text-effects-options.md: animated drawtext expressions for
the simple effects, per-clip .ass overlays (libass) for everything else. The
legacy fade must stay byte-compatible for projects that never touched the new
slots, and every catalogue entry must produce a valid overlay.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from app.text_effects import (
    FxPlan,
    TextGeometry,
    ass_colour,
    ass_time,
    build_ass_document,
    build_text_overlay,
    effect_for,
    escape_ass,
    ff_escape_drawtext,
    overlay_plan,
    plan_engine,
    text_effect_catalog,
    _geometry,
)


def title_item(**overrides) -> dict:
    item = {
        "type": "title",
        "text": "Summer, slowly.",
        "duration": 5,
        "textStart": 0,
        "textEnd": 5,
        "textX": 50,
        "textY": 50,
        "fontSize": 72,
        "fontColor": "#ffffff",
        "fontFamily": "Montserrat",
        "textBold": True,
        "textItalic": False,
        "textEnterDuration": 0.8,
        "textExitDuration": 0.6,
    }
    item.update(overrides)
    return item


def parse_events(document: str) -> list[dict[str, str]]:
    events = []
    for line in document.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        parts = line.split(",", 9)
        events.append({"start": parts[1], "end": parts[2], "text": parts[9]})
    return events


def cs(stamp: str) -> int:
    h, m, rest = stamp.split(":")
    return int(h) * 360000 + int(m) * 6000 + round(float(rest) * 100)


class CatalogTests(unittest.TestCase):
    def test_catalog_loads_with_three_slots(self):
        catalog = text_effect_catalog()
        self.assertGreaterEqual(len(catalog), 60)
        slots = {entry["slot"] for entry in catalog}
        self.assertEqual(slots, {"enter", "while", "exit"})

    def test_labels_are_unique(self):
        labels = [entry["label"] for entry in text_effect_catalog()]
        self.assertEqual(len(labels), len(set(labels)))

    def test_known_and_unknown_labels(self):
        self.assertEqual(effect_for("Typewriter", "enter")["id"], "typewriter")
        # Unknown / wrong-slot labels degrade to the slot default.
        self.assertEqual(effect_for("Typewriter", "exit")["id"], "fade-out")
        self.assertIsNotNone(effect_for(None, "enter"))
        self.assertEqual(effect_for("Nope", "enter")["id"], "fade")
        self.assertEqual(effect_for("Nope", "while")["id"], "none")
        self.assertEqual(effect_for("Nope", "exit")["id"], "fade-out")


class HelperTests(unittest.TestCase):
    def test_ass_time(self):
        self.assertEqual(ass_time(0), "0:00:00.00")
        self.assertEqual(ass_time(3.5), "0:00:03.50")
        self.assertEqual(ass_time(62.123), "0:01:02.12")
        self.assertEqual(ass_time(-1), "0:00:00.00")

    def test_ass_colour_bgr_swap(self):
        self.assertEqual(ass_colour("#3C905F"), "&H5F903C&")
        self.assertEqual(ass_colour("nope"), "&HFFFFFF&")

    def test_escape_ass(self):
        self.assertEqual(escape_ass("{brace}"), "(brace)")

    def test_ff_escape_drawtext_legacy_parity(self):
        self.assertEqual(ff_escape_drawtext("a:b'c%d[e]f\\g"), r"a\:b\'c\%d\[e\]f\\g")


class DrawtextEngineTests(unittest.TestCase):
    def test_legacy_item_produces_the_historic_fade_filter(self):
        overlay = build_text_overlay(title_item(), {}, 1920, 1080, Path("/fonts"), None)
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.startswith("drawtext=fontfile="))
        self.assertIn("alpha='if(lt(t,0),0,if(lt(t,0.8),(t-0)/0.8,", overlay)
        self.assertIn("x='(w-text_w)*0.5'", overlay)
        self.assertIn("enable='between(t,0,5)'", overlay)

    def test_caption_text_enabled_false_is_none(self):
        item = title_item(type="picture", textEnabled=False)
        self.assertIsNone(build_text_overlay(item, {}, 1920, 1080, Path("/fonts"), None))

    def test_title_frames_ignore_text_enabled(self):
        item = title_item(textEnabled=False)
        self.assertIsNotNone(build_text_overlay(item, {}, 1920, 1080, Path("/fonts"), None))

    def test_slide_uses_x_expression(self):
        overlay = build_text_overlay(title_item(textFxEnter="Slide from left"), {}, 1920, 1080, Path("/fonts"), None)
        self.assertIn("drawtext=", overlay)
        self.assertIn("clip((t-0)/0.8,0,1)", overlay)

    def test_dt_plan_for_defaults_and_slides(self):
        self.assertEqual(plan_engine(overlay_plan(title_item())), "dt")
        self.assertEqual(plan_engine(overlay_plan(title_item(textFxExit="Slide out right"))), "dt")
        # Any libass effect flips the whole overlay to the ass engine.
        self.assertEqual(plan_engine(overlay_plan(title_item(textFxEnter="Pop in"))), "ass")
        self.assertEqual(plan_engine(overlay_plan(title_item(textFxWhile="Shake"))), "ass")

    def test_fallback_font_resolver_is_not_required_for_ass(self):
        # The ass path never touches drawtext, so a missing resolver is fine.
        overlay = build_text_overlay(title_item(textFxEnter="Typewriter"), {}, 640, 360, Path("/fonts"), None)
        self.assertIsNone(overlay)  # no ass_path -> refused, never a broken filter


class AssEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def document(self, item: dict, width=1280, height=720) -> tuple[str, str]:
        path = self.tmp / "clip.ass"
        overlay = build_text_overlay(item, {}, width, height, Path("/fonts"), path)
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.startswith("ass=filename="))
        self.assertIn("fontsdir=", overlay)
        return overlay, path.read_text(encoding="utf-8")

    def test_document_header_and_style(self):
        _, doc = self.document(title_item(textFxEnter="Pop in"))
        self.assertIn("[Script Info]", doc)
        self.assertIn("PlayResX: 1280", doc)
        self.assertIn("PlayResY: 720", doc)
        self.assertIn("Style: FX,Montserrat,", doc)
        # 72pt at 1280 wide == 72 * 1280/1920 = 48px
        self.assertIn("Montserrat,48,", doc)

    def test_karaoke_typewriter_is_a_single_event(self):
        # The plain Typewriter is karaoke-based: one event, per-char \k tags.
        _, doc = self.document(title_item(textFxEnter="Typewriter", text="Type me"))
        events = parse_events(doc)
        self.assertEqual(len(events), 1)
        # secondary colour transparent, per-char \k fills
        self.assertIn("\\2a&HFF&", doc)
        self.assertIn("\\k", doc)

    def test_caret_slices_do_not_overlap(self):
        _, doc = self.document(title_item(textFxEnter="Typewriter + caret", text="Type me"))
        events = parse_events(doc)
        self.assertGreater(len(events), 5)
        for current, following in zip(events, events[1:]):
            self.assertEqual(cs(current["end"]), cs(following["start"]))

    def test_typewriter_caret_reveals_progressively(self):
        _, doc = self.document(title_item(textFxEnter="Typewriter + caret", text="ABC"))
        events = parse_events(doc)
        self.assertEqual(len(events), 3)
        self.assertIn("A", events[0]["text"])
        self.assertIn("AB", events[1]["text"])
        self.assertIn("ABC", events[2]["text"])
        self.assertIn("|", doc)

    def test_typewriter_delete_trims_hold_and_deletes(self):
        _, doc = self.document(title_item(textFxExit="Typewriter delete", text="ABC", textExitDuration=0.6))
        events = parse_events(doc)
        # base event ends before the exit window starts (4.4 - epsilon)
        self.assertLessEqual(cs(events[0]["end"]), cs("0:00:04.40"))
        self.assertIn("A", doc)
        # shorter and shorter lines follow
        bodies = [event["text"] for event in events]
        self.assertTrue(any("AB" in body for body in bodies))
        self.assertTrue(any(body.rstrip().endswith("A") and "B" not in body for body in bodies))

    def test_wipe_uses_animated_clip(self):
        _, doc = self.document(title_item(textFxEnter="Wipe from left"))
        # a closed box, then an animated \t opening it (order: closed \clip first)
        self.assertRegex(doc, r"\\clip\(\d+,\d+,\d+,\d+\)\\t\(0,\d+,\\clip\(")

    def test_karaoke_sweep_uses_kf(self):
        _, doc = self.document(title_item(textFxWhile="Karaoke sweep"))
        self.assertIn("\\kf", doc)
        self.assertIn("\\2c", doc)

    def test_count_up_params(self):
        item = title_item(textFxWhile="Count up", textFxParams={"from": "5", "to": "95"}, duration=2, textStart=0, textEnd=2)
        _, doc = self.document(item, width=1920, height=1080)
        bodies = [event["text"] for event in parse_events(doc)]
        self.assertIn("5", bodies[0])
        self.assertIn("95", bodies[-1])

    def test_shake_produces_many_events(self):
        _, doc = self.document(title_item(textFxWhile="Shake"))
        self.assertGreater(doc.count("Dialogue:"), 10)

    def test_split_rise_one_event_per_char(self):
        _, doc = self.document(title_item(textFxEnter="Split rise · chars", text="AB"))
        events = parse_events(doc)
        self.assertEqual(len(events), 2)
        self.assertIn("\\move(", events[0]["text"])

    def test_lower_third_draws_a_bar(self):
        _, doc = self.document(title_item(textFxEnter="Lower-third bar"))
        self.assertIn("\\p1", doc)
        self.assertIn("m 0 0 l", doc)
        self.assertIn("\\1c&H000000&", doc)

    def test_multiline_text_preserved(self):
        _, doc = self.document(title_item(textFxEnter="Pop in", text="One line\nTwo lines"))
        self.assertIn(r"One line\NTwo lines", doc)

    def test_user_braces_escaped(self):
        _, doc = self.document(title_item(textFxEnter="Pop in", text="Hi {you}"))
        self.assertIn("Hi (you)", doc)

    def test_colour_reaches_the_style(self):
        _, doc = self.document(title_item(fontColor="#3C905F", textFxEnter="Pop in"))
        self.assertIn("&H5F903C&", doc)


class NoDrawtextBuildTests(unittest.TestCase):
    """Stock FFmpeg binaries (distro packages, many NAS builds) ship libass but
    not drawtext. Until now the legacy caption and every drawtext-expression
    effect (Fade, the Slides, Gentle float ...) failed with 'Filter not found'
    — the clip rendered with no text, which read as "text effects do not
    work". With force_ass everything goes through libass."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def overlay(self, item: dict, force: bool = True) -> tuple[str, str]:
        path = self.tmp / "clip.ass"
        result = build_text_overlay(item, {}, 1280, 720, Path("/fonts"), path, force_ass=force)
        self.assertIsNotNone(result)
        return result, path.read_text(encoding="utf-8") if path.exists() else ""

    def test_legacy_fades_route_through_libass_when_forced(self):
        overlay, doc = self.overlay(title_item())
        self.assertTrue(overlay.startswith("ass=filename="), overlay)
        self.assertIn("\\fad(800,0)", doc)
        self.assertIn("\\fad(0,600)", doc)

    def test_without_force_the_drawtext_path_is_unchanged(self):
        overlay, _ = self.overlay(title_item(), force=False)
        self.assertTrue(overlay.startswith("drawtext="), overlay)

    def test_slides_get_a_move_and_no_duplicate_pos(self):
        for label, expect in (("Slide from left", "\\move(256,360,640,360,0,800)"),
                              ("Slide from bottom", "\\move(640,490,640,360,0,800)"),
                              ("Rise & settle", "\\move(640,418,640,360,0,800)")):
            with self.subTest(label):
                _, doc = self.overlay(title_item(textFxEnter=label))
                self.assertIn(expect, doc)
                self.assertNotIn("\\pos(", doc, "one anchor per event: \\move replaces \\pos")
                self.assertIn("\\an5", doc)

    def test_move_enter_keeps_only_the_fade_of_a_move_exit(self):
        _, doc = self.overlay(title_item(textFxEnter="Slide from left", textFxExit="Slide out right"))
        self.assertEqual(1, doc.count("\\move("), doc)
        self.assertIn("\\fad(0,600)", doc)

    def test_while_effects_compose_with_forced_ass(self):
        _, doc = self.overlay(title_item(textFxWhile="Gentle float"))
        self.assertIn("\\fscy103", doc)


class RendererIntegrationTests(unittest.TestCase):
    def _renderer(self, fonts_dir: Path):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from app.database import Database
        from app.config import Settings
        from app.renderer import Renderer
        tmp = Path(tempfile.mkdtemp())
        settings = Settings(config_dir=tmp / "config", photos_dir=tmp, videos_dir=tmp, music_dir=tmp, output_dir=tmp, fonts_dir=fonts_dir)
        return Renderer(Database(tmp / "db.sqlite"), settings)

    def test_dispatch_drawtext_and_ass(self):
        renderer = self._renderer(Path(__file__).resolve().parents[2] / "public" / "fonts")
        legacy = renderer._text_filter(title_item(), {}, 1920, 1080)
        self.assertIn("drawtext=", legacy)
        work = Path(tempfile.mkdtemp())
        ass = renderer._text_filter(title_item(textFxEnter="Typewriter"), {}, 1920, 1080, work / "t.ass")
        if renderer.ass_filter_supported():
            self.assertIn("ass=filename=", ass)
            self.assertTrue((work / "t.ass").exists())
        else:
            # Degradation: falls back to the fade drawtext instead of failing.
            self.assertIn("drawtext=", ass)

    def test_caption_uses_project_defaults(self):
        renderer = self._renderer(Path(__file__).resolve().parents[2] / "public" / "fonts")
        item = title_item(type="picture", text="Caption", fontSize=999)
        overlay = renderer._text_filter(item, {"fontSize": 48, "fontColor": "#ff0000", "bold": False, "italic": False, "fontFamily": "Open Sans"}, 1920, 1080)
        self.assertIn("fontsize=48", overlay)
        self.assertIn("fontcolor=0xff0000", overlay)


if __name__ == "__main__":
    unittest.main()
