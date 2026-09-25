"""Tests for the dual-engine dynamic text effects (backend/app/text_effects.py).

Option 1 from docs/text-effects-options.md: animated drawtext expressions for
the simple effects, per-clip .ass overlays (libass) for everything else. The
legacy fade must stay byte-compatible for projects that never touched the new
slots, and every catalogue entry must produce a valid overlay.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.transition_previews import slugify
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
    normalize_text_window,
    overlay_plan,
    plan_engine,
    text_effect_catalog,
    _geometry,
    _merge_fades,
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

    def test_ids_and_preview_slugs_are_unique(self):
        catalog = text_effect_catalog()
        ids = [entry["id"] for entry in catalog]
        self.assertEqual(len(ids), len(set(ids)))
        # The GUI keys its symbol/seconds/param maps by bare label and names the
        # cached example clips after a slug of it, so two labels that slugify the
        # same would share one preview file (the exit slot's preview vanished).
        slugs = [slugify(entry["label"]) for entry in catalog]
        self.assertEqual(len(slugs), len(set(slugs)), [s for s in slugs if slugs.count(s) > 1])

    def test_known_and_unknown_labels(self):
        self.assertEqual(effect_for("Typewriter", "enter")["id"], "typewriter")
        # Unknown / wrong-slot labels degrade to the slot default.
        self.assertEqual(effect_for("Typewriter", "exit")["id"], "fade-out")
        self.assertIsNotNone(effect_for(None, "enter"))
        self.assertEqual(effect_for("Nope", "enter")["id"], "fade")
        self.assertEqual(effect_for("Nope", "while")["id"], "none")
        self.assertEqual(effect_for("Nope", "exit")["id"], "fade-out")


class StaticNoneTests(unittest.TestCase):
    """The three "no animation" entries — "None" (enter), "None (static)"
    (while), "None (hold)" (exit). Labels have to be unique across the whole
    catalogue, and projects saved while enter *and* exit were both called
    "None" must keep rendering exactly as they did."""

    def test_each_slot_resolves_its_own_none(self):
        self.assertEqual(effect_for("None", "enter")["id"], "none-enter")
        self.assertEqual(effect_for("None (static)", "while")["id"], "none")
        self.assertEqual(effect_for("None (hold)", "exit")["id"], "none-exit")

    def test_the_legacy_bare_none_still_resolves(self):
        self.assertEqual(effect_for("None", "exit")["id"], "none-exit")
        self.assertEqual(effect_for("None", "while")["id"], "none")
        self.assertEqual(effect_for(" none ", "exit")["id"], "none-exit")
        # "None" stays the enter entry, not an alias of something else.
        self.assertEqual(effect_for("None", "enter")["id"], "none-enter")

    def test_a_static_side_forces_its_duration_to_zero(self):
        for label in ("None (hold)", "None"):
            with self.subTest(label):
                geometry = _geometry(title_item(textFxExit=label, textExitDuration=0.6), {}, 1920, 1080)
                self.assertEqual(0.0, geometry.do)
        geometry = _geometry(title_item(textFxEnter="None", textEnterDuration=0.8), {}, 1920, 1080)
        self.assertEqual(0.0, geometry.di)
        # A real effect keeps the saved seconds.
        self.assertEqual(0.6, _geometry(title_item(textFxExit="Fade out", textExitDuration=0.6), {}, 1920, 1080).do)

    def test_both_spellings_render_the_same_ass(self):
        tmp = Path(tempfile.mkdtemp())
        docs = []
        for label in ("None (hold)", "None"):
            path = tmp / "clip.ass"
            overlay = build_text_overlay(
                title_item(textFxEnter="Pop in", textFxExit=label), {}, 1280, 720, Path("/fonts"), path,
            )
            self.assertIsNotNone(overlay)
            doc = path.read_text(encoding="utf-8")
            self.assertNotIn("\\fad(0,", doc)  # nothing fades out
            docs.append(doc)
        self.assertEqual(docs[0], docs[1])

    def test_both_spellings_render_the_same_drawtext(self):
        overlays = [
            build_text_overlay(title_item(textFxExit=label), {}, 1920, 1080, Path("/fonts"), None)
            for label in ("None (hold)", "None")
        ]
        for overlay in overlays:
            self.assertIsNotNone(overlay)
            # The outgoing ramp collapsed to a constant: no divide-by-zero.
            self.assertIn("if(lt(t,5),0,0)", overlay)
        self.assertEqual(overlays[0], overlays[1])


class HelperTests(unittest.TestCase):
    def test_ass_time(self):
        self.assertEqual(ass_time(0), "0:00:00.00")
        self.assertEqual(ass_time(3.5), "0:00:03.50")
        self.assertEqual(ass_time(62.123), "0:01:02.12")
        self.assertEqual(ass_time(-1), "0:00:00.00")

    def test_ass_colour_bgr_swap(self):
        self.assertEqual(ass_colour("#3C905F"), "&H5F903C&")
        self.assertEqual(ass_colour("nope"), "&HFFFFFF&")

    def test_text_window_never_reaches_the_outgoing_handle(self):
        start, end = normalize_text_window(title_item(duration=5, textStart=4.95, textEnd=5))
        self.assertEqual(4.9, start)
        self.assertEqual(5, end)
        start, end = normalize_text_window(title_item(duration=5, textStart=0, textEnd=99))
        self.assertEqual(0, start)
        self.assertEqual(5, end)

    def test_geometry_uses_the_normalized_window(self):
        geometry = _geometry(title_item(duration=5, textStart=4.95, textEnd=5), {}, 1920, 1080)
        self.assertIsNotNone(geometry)
        self.assertEqual(4.9, geometry.start)
        self.assertEqual(5, geometry.end)

    def test_escape_ass(self):
        self.assertEqual(escape_ass("{brace}"), "(brace)")

    def test_ff_escape_drawtext_quotes_for_both_parser_passes(self) -> None:
        """The value has to survive the graph parser *and* the option splitter.

        Escaping for the last layer only (the historic behaviour) left an
        apostrophe able to close the quoted section early, after which every
        following option was parsed as a filter name -- ``No such filter:
        '0)'`` for a title like "Oma's Verjaardag 2006".
        """
        # Nothing to escape: the value is emitted as-is.
        self.assertEqual("plain title", ff_escape_drawtext("plain title"))
        # A comma would end the filter, so the value is wrapped in quotes.
        self.assertEqual("'Summer, slowly.'", ff_escape_drawtext("Summer, slowly."))
        # A colon ends an option in the second pass: escape it.
        self.assertEqual("'Chapter 1\\: The beginning'", ff_escape_drawtext("Chapter 1: The beginning"))
        # An apostrophe is escaped for the second pass and then written the
        # shell way -- close the quoted section, escaped quote, reopen it.
        self.assertEqual("'Oma\\'\\''s Verjaardag 2006'", ff_escape_drawtext("Oma's Verjaardag 2006"))
        # drawtext's own scan eats one further backslash: '\%' prints '%'.
        self.assertEqual("'50\\\\% off'", ff_escape_drawtext("50% off"))


class DrawtextEngineTests(unittest.TestCase):
    def test_legacy_item_produces_the_historic_fade_filter(self) -> None:
        overlay = build_text_overlay(title_item(), {}, 1920, 1080, Path("/fonts"), None)
        self.assertIsNotNone(overlay)
        self.assertTrue(overlay.startswith("drawtext=fontfile="))
        # Values containing a comma (expressions, text) are quoted so the graph
        # parser keeps them in one filter; plain values are left alone.
        self.assertIn("alpha='if(lt(t,0),0,if(lt(t,0.8),(t-0)/0.8,", overlay)
        self.assertIn(":x=(w-text_w)*0.5:y=(h-text_h)*0.5:", overlay)
        self.assertIn("enable='between(t,0,5)'", overlay)

    def test_apostrophe_in_the_title_does_not_break_the_graph(self) -> None:
        """Regression: "Oma's Verjaardag 2006" produced ``No such filter: '0)'``.

        The apostrophe closed the ``text='...'`` value early, so the commas of
        the options after it (alpha, x, y, enable) were no longer inside a
        quoted section and each of them was parsed as a new filter.
        """
        item = title_item(text="Oma's Verjaardag 2006", textScaleEnabled=True,
                          textScaleFrom=0.5, textScaleTo=1.1,
                          textMoveEnabled=True, textMoveFromX=64.25,
                          textMoveFromY=20.59, textMoveToX=30.61, textMoveToY=75.60)
        overlay = build_text_overlay(item, {}, 1280, 720, Path("/fonts"), None)
        self.assertIsNotNone(overlay)
        # Escaped for the option splitter, with the quoted section closed and
        # reopened around the apostrophe.
        self.assertIn("text='Oma\\'\\''s Verjaardag 2006'", overlay)
        # A scaling fontsize is an expression full of commas -> quoted.
        self.assertIn("fontsize='(", overlay)
        # ...and the options after the text still belong to the same filter.
        for option in (":fontcolor=", ":alpha='", ":x='", ":y='", ":enable='between(t,0,5)'"):
            self.assertIn(option, overlay)

    def test_fontsize_expression_is_quoted(self) -> None:
        # Without the quotes the first comma of the expression ends the filter
        # and the remainder is read as filter names.
        item = title_item(text="No apostrophe here", textScaleEnabled=True,
                          textScaleFrom=0.5, textScaleTo=1.1)
        overlay = build_text_overlay(item, {}, 1280, 720, Path("/fonts"), None)
        self.assertIsNotNone(overlay)
        self.assertIn("fontsize='(", overlay)
        self.assertIn("clip((t-0)/5,0,1)", overlay)

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


class OutlineTests(unittest.TestCase):
    """'Outline & shadow' in Default text style: on unless switched off,
    picture captions only (text frames sit on their own colour bed)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_picture_caption_gets_outline_by_default(self):
        item = title_item(type="picture", text="Caption")
        overlay = build_text_overlay(item, {"fontSize": 48}, 1920, 1080, Path("/fonts"), None)
        self.assertIn(":borderw=3:bordercolor=black@0.85", overlay)

    def test_outline_can_be_switched_off(self):
        item = title_item(type="picture", text="Caption")
        overlay = build_text_overlay(item, {"fontSize": 48, "outline": False}, 1920, 1080, Path("/fonts"), None)
        self.assertNotIn("borderw", overlay)
        self.assertIn("shadowx=2", overlay, "the soft shadow stays, as it always did")

    def test_text_frames_never_get_an_outline(self):
        overlay = build_text_overlay(title_item(), {"outline": True}, 1920, 1080, Path("/fonts"), None)
        self.assertNotIn("borderw", overlay)

    def test_libass_style_carries_the_outline(self):
        path = self.tmp / "c.ass"
        item = title_item(type="picture", text="Caption", textFxEnter="Pop in")
        build_text_overlay(item, {"fontSize": 48}, 1920, 1080, Path("/fonts"), path)
        style = [l for l in path.read_text().splitlines() if l.startswith("Style: FX")][0]
        self.assertIn(",&H26000000&,&H73000000&,", style)
        self.assertTrue(style.endswith(",1,3,2,5,0,0,0,1"), style)
        build_text_overlay(item, {"fontSize": 48, "outline": False}, 1920, 1080, Path("/fonts"), path)
        style = [l for l in path.read_text().splitlines() if l.startswith("Style: FX")][0]
        self.assertTrue(style.endswith(",1,0,2,5,0,0,0,1"), style)


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
        # libass honours only the first \\fad of an event, so the enter and the
        # exit fade must arrive as ONE tag (two tags lost the fade-out).
        self.assertIn("\\fad(800,600)", doc)
        self.assertEqual(1, doc.count("\\fad("), doc)

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
        # The exit's fade is kept on purpose, merged with the slide's own fade-in.
        self.assertIn("\\fad(800,600)", doc)
        self.assertEqual(1, doc.count("\\fad("), doc)

    def test_while_effects_compose_with_forced_ass(self):
        _, doc = self.overlay(title_item(textFxWhile="Gentle float"))
        self.assertIn("\\fscy103", doc)


class FadeMergeTests(unittest.TestCase):
    """libass applies only the FIRST \\fad of an event and ignores the rest.

    The enter and exit builders each emit their own fade, so an event that got
    both (the default Fade / Fade out with any libass While effect, a slide
    enter with a degraded move exit, or every caption on an FFmpeg build
    without drawtext) silently lost its exit fade: the text stayed fully
    visible and popped off on the last frame. _ev() now merges them.
    """

    def doc(self, **overrides) -> str:
        item = title_item(**overrides)
        return build_ass_document(_geometry(item, {}, 1280, 720), overlay_plan(item))

    def test_merge_keeps_the_longest_in_and_out_at_the_first_position(self):
        self.assertEqual(_merge_fades("\\an5\\fad(800,0)\\blur2\\fad(0,600)"), "\\an5\\fad(800,600)\\blur2")
        self.assertEqual(_merge_fades("\\fad(400,0)\\fad(900,0)\\fad(0,300)"), "\\fad(900,300)")

    def test_blocks_without_a_second_fade_are_untouched(self):
        for tags in ("\\an5\\pos(640,360)", "\\an5\\fad(800,0)\\blur2",
                     # the 7-argument \\fade is a different tag and never merged
                     "\\fade(255,0,255,0,100,200,300)\\fad(0,600)"):
            with self.subTest(tags):
                self.assertEqual(_merge_fades(tags), tags)

    def test_exit_fade_survives_libass_while_effects(self):
        for while_fx in ("Neon glow", "Pulse", "Wave", "Slow zoom", "Colour cycle"):
            with self.subTest(while_fx):
                doc = self.doc(textFxWhile=while_fx)
                self.assertIn("\\fad(800,600)", doc)

    def test_no_event_carries_two_fades_for_any_enter_exit_pair(self):
        catalog = text_effect_catalog()
        enters = [e["label"] for e in catalog if e["slot"] == "enter"]
        exits = [e["label"] for e in catalog if e["slot"] == "exit"]
        # static (plain libass path), a tag-based loop and a frame-sliced one
        whiles = ("None (static)", "Neon glow", "Shake")
        offenders = []
        for enter in enters:
            for exit_ in exits:
                for while_fx in whiles:
                    doc = self.doc(textFxEnter=enter, textFxWhile=while_fx, textFxExit=exit_)
                    if any(line.count("\\fad(") > 1 for line in doc.splitlines() if line.startswith("Dialogue:")):
                        offenders.append(f"{enter} + {while_fx} + {exit_}")
        self.assertEqual([], offenders[:12], f"{len(offenders)} combinations still carry two \\fad tags")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_libass_really_fades_the_text_out(self):
        """Render with the real libass: near the end the caption must be dimmer."""
        ffmpeg = str(shutil.which("ffmpeg"))
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        if not re.search(r"^\s*\S+\s+ass\s", filters, re.M):
            self.skipTest("this FFmpeg build has no ass (libass) filter")
        fonts = Path(__file__).resolve().parents[2] / "public" / "fonts"
        work = Path(tempfile.mkdtemp())
        ass = work / "fade.ass"
        ass.write_text(self.doc(textFxWhile="Neon glow"), encoding="utf-8")

        def mean_luma(at: float) -> float:
            frame = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=640x360:r=25:d=5",
                 "-vf", f"ass=filename={ass}:fontsdir={fonts}", "-ss", str(at),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=120,
            ).stdout
            self.assertTrue(frame, "FFmpeg produced no frame")
            return sum(frame) / len(frame)

        held, fading = mean_luma(2.5), mean_luma(4.8)
        self.assertGreater(held, 1.0, "the caption did not render at all")
        # 0.2 s before the end of a 0.6 s fade-out the text is at ~1/3 opacity;
        # with the ignored second \\fad it was still at 100 % here.
        self.assertLess(fading, held * 0.6, (held, fading))


class SlicedTimingTests(unittest.TestCase):
    """Effects that cut the text window into consecutive events.

    Typewriter + caret / Decrypted scramble (one event per step), Typewriter
    delete / Scramble out (tail events) and motion paths (one event per path
    segment). libass times \t, \move and \fad from each event's own start or
    end, so tags written for the whole window must be re-timed per slice, and
    an exit may only sit on the event that really runs to the end.
    """

    def events(self, **overrides) -> list[str]:
        item = title_item(**overrides)
        doc = build_ass_document(_geometry(item, {}, 1280, 720), overlay_plan(item))
        return [line for line in doc.splitlines() if line.startswith("Dialogue:")]

    def test_typing_steps_do_not_fade_out_before_the_end(self):
        for enter in ("Typewriter + caret", "Decrypted scramble"):
            with self.subTest(enter):
                events = self.events(textFxEnter=enter, textFxExit="Fade out")
                self.assertGreater(len(events), 3)
                for line in events[:-1]:
                    self.assertNotIn("\\fad(", line, "an early step must not carry the exit fade")
                self.assertIn("\\fad(0,600)", events[-1])

    def test_a_sliced_exit_does_not_fade_the_text_before_it(self):
        for enter, fade in (("Fade", "\\fad(800,0)"), ("Pop in", None)):
            with self.subTest(enter):
                events = self.events(textFxEnter=enter, textFxExit="Typewriter delete")
                body = events[0]
                self.assertNotRegex(body, r"\\fad\(\d+,[1-9]\d*\)", "no fade-out before the delete")
                if fade:
                    self.assertIn(fade, body)
                self.assertGreater(len(events), 3, "the delete steps are still there")

    def test_loops_continue_across_typing_steps(self):
        events = self.events(textFxEnter="Typewriter + caret", textFxWhile="Pulse")
        first = re.search(r"\\t\((-?\d+),", events[0])
        self.assertEqual("0", first.group(1))
        for line in events[1:]:
            start = cs(line.split(",")[1]) * 10          # ms since the window start (0)
            match = re.search(r"\\t\((-?\d+),", line)
            self.assertIsNotNone(match, line)
            self.assertEqual(-start, int(match.group(1)), "the pulse continues, it does not restart")

    def test_motion_path_loops_continue_across_segments(self):
        events = self.events(textFxEnter="Fade", textFxWhile="Pulse", textFxExit="Fade out",
                             textMoveEnabled=True, textMovePathType="circle",
                             textMoveFromX=40, textMoveFromY=50, textMoveToX=60, textMoveToY=50)
        self.assertGreater(len(events), 10)
        restarting = [line for line in events if re.search(r"\\t\(0,\d+,\\fscx", line)]
        self.assertEqual(1, len(restarting), "only the first segment starts the pulse at 0")

    def test_the_exit_on_the_last_typing_step_is_retimed_to_that_step(self):
        events = self.events(textFxEnter="Typewriter + caret", textFxExit="Pop out")
        last = events[-1]
        parts = last.split(",")
        duration_ms = (cs(parts[2]) - cs(parts[1])) * 10
        match = re.search(r"\\t\((-?\d+),(-?\d+),\\fscx0\\fscy0", last)
        self.assertIsNotNone(match, last)
        self.assertEqual(duration_ms, int(match.group(2)), "the pop-out ends with the event")
        self.assertEqual(duration_ms - 600, int(match.group(1)))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_libass_renders_typing_at_full_strength_and_no_dip_before_a_delete(self):
        ffmpeg = str(shutil.which("ffmpeg"))
        filters = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
        if not re.search(r"^\s*\S+\s+ass\s", filters, re.M):
            self.skipTest("this FFmpeg build has no ass (libass) filter")
        fonts = Path(__file__).resolve().parents[2] / "public" / "fonts"
        work = Path(tempfile.mkdtemp())

        def luma(item: dict, at: float) -> float:
            ass = work / "t.ass"
            ass.write_text(build_ass_document(_geometry(item, {}, 640, 360), overlay_plan(item)), encoding="utf-8")
            frame = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "color=c=black:s=640x360:r=25:d=5",
                 "-vf", f"ass=filename={ass}:fontsdir={fonts}", "-ss", str(at),
                 "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=120,
            ).stdout
            return sum(frame) / max(1, len(frame))

        # Mid-typing (0.55 s, 7 of 10 letters) the typed text must be exactly as
        # bright as with no exit at all: every step used to carry the 600 ms
        # exit fade and sat between 13 % and 0 % opacity.
        typing = dict(text="Dear diary", textFxEnter="Typewriter + caret")
        self.assertGreater(luma(title_item(**typing, textFxExit="Fade out"), 0.55),
                           0.9 * luma(title_item(**typing, textFxExit="None (hold)"), 0.55))
        # Just before the delete starts (4.4 s) the text must still be at full
        # strength; it used to fade to ~17 % and pop back to be deleted.
        delete = title_item(text="Dear diary", textFxEnter="Wipe from left", textFxExit="Typewriter delete")
        self.assertGreater(luma(delete, 4.3), 0.9 * luma(delete, 2.5))


class HiddenLineTests(unittest.TestCase):
    """Per-letter effects draw the whole line in every event (so libass lays
    it out identically) with everything but one letter at alpha FF."""

    @staticmethod
    def visible_letters(text: str) -> str:
        visible, out, i = True, [], 0
        while i < len(text):
            if text[i] == "{":
                j = text.index("}", i)
                alphas = re.findall(r"\\alpha&H([0-9A-F]{2})&", text[i + 1:j])
                if alphas:                       # last one wins (a \t target included)
                    visible = alphas[-1] != "FF"
                i = j + 1
            elif text.startswith("\\N", i):
                i += 2
            else:
                if visible and text[i] != " ":
                    out.append(text[i])
                i += 1
        return "".join(out)

    def test_each_letter_event_shows_exactly_one_letter(self):
        text = "Summer, slowly.\nTwo"
        for enter in ("Split rise · chars", "Split from centre"):
            with self.subTest(enter):
                item = title_item(text=text, textFxEnter=enter)
                doc = build_ass_document(_geometry(item, {}, 1280, 720), overlay_plan(item))
                shown = [self.visible_letters(e["text"]) for e in parse_events(doc)]
                self.assertTrue(all(len(s) == 1 for s in shown), shown)
                self.assertEqual(sorted(text.replace(" ", "").replace("\n", "")), sorted("".join(shown)))


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


class FfmpegFilterGraphTests(unittest.TestCase):
    """Hand the generated filter to a real FFmpeg, when one is installed.

    ``No such filter: '0)'`` is a tokenising error, not a drawtext one, so the
    filter *name* is swapped for ``crop``: what is under test is whether the
    option values keep the graph in one piece. (``crop`` then rejects the
    unknown options, which is expected; a *split* graph reports "No such
    filter" / "Error parsing a filter description" instead.)
    """

    def _overlay(self) -> str:
        item = title_item(text="Oma's Verjaardag 2006", textScaleEnabled=True,
                          textScaleFrom=0.5, textScaleTo=1.1,
                          textMoveEnabled=True, textMoveFromX=64.25,
                          textMoveFromY=20.59, textMoveToX=30.61, textMoveToY=75.60)
        overlay = build_text_overlay(item, {}, 1280, 720, Path("/fonts"), None)
        self.assertIsNotNone(overlay)
        return str(overlay)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_the_option_values_do_not_split_the_graph(self) -> None:
        ffmpeg = str(shutil.which("ffmpeg"))
        overlay = self._overlay()
        probe = "crop=" + overlay.split("drawtext=", 1)[1]
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=320x180:r=25:d=1", "-frames:v", "1",
             "-vf", probe, "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        stderr = result.stderr or ""
        for marker in ("No such filter", "No option name", "Error parsing", "Trailing garbage"):
            self.assertNotIn(marker, stderr, f"FFmpeg split the graph: {stderr}\nfilter: {overlay}")

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is not installed")
    def test_drawtext_gets_the_title_back_unchanged(self) -> None:
        """The text option must still read "Oma's Verjaardag 2006"."""
        ffmpeg = str(shutil.which("ffmpeg"))
        # Same probe the renderer uses: `-h filter=drawtext` exits 0 even when
        # the filter is missing, so the help text decides.
        probe = subprocess.run(
            [ffmpeg, "-hide_banner", "-h", "filter=drawtext"],
            capture_output=True, text=True,
        )
        help_text = (probe.stdout or "") + (probe.stderr or "")
        if "Filter drawtext" not in help_text or "Unknown filter" in help_text:
            self.skipTest("this FFmpeg build has no drawtext filter")

        fonts = Path(__file__).resolve().parents[2] / "public" / "fonts"
        font = fonts / "Montserrat-Bold.ttf"
        if not font.exists():
            self.skipTest("bundled fonts are not available")
        item = title_item(text="Oma's Verjaardag 2006", duration=1, textEnd=1)
        overlay = build_text_overlay(
            item, {}, 320, 180, fonts, None,
            lambda family, bold, italic, fonts_dir: str(font),
        )
        self.assertIsNotNone(overlay)
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
             "-i", "color=c=black:s=320x180:r=25:d=1", "-frames:v", "1",
             "-vf", str(overlay), "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
