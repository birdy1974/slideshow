"""Karaoke timing: the model the popup edits, and the ``.ass`` the render burns.

Two halves, deliberately tested apart:

* the *model* (``backend/app/karaoke.py``) turns ``karaoke.words`` — one optional
  time per word of the caption — into sentence windows and per-word windows. The
  browser runs a TypeScript twin; ``test_karaoke_twin.py`` pins the two engines
  together number for number, and this file pins the *rules a user can rely on*:
  lead-in, tail, no two lines on screen at once, the word cap, and times that
  survive a garbage project file.
* the *emission* (``build_karaoke_events``, ``text_effects.karaoke_document``)
  writes those windows as ``\\kf`` karaoke. The colour semantics and the tag
  spelling are the parts nobody can re-derive from here, so they are asserted by
  name, in both directions: what must be in the file and what must not.

The wiring assertions at the bottom are the joints the two engines cannot see.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app import karaoke as K  # noqa: E402

BS = chr(92)  # one backslash, so nothing in this file depends on escape rules
# A ``\kf`` / ``\k`` tag, and an override block to strip before comparing text.
KF = re.compile(BS + BS + "kf(\\d+)")
K_ONLY = re.compile(BS + BS + "k(\\d+)")
BLOCK = re.compile(BS + "{[^}]*" + BS + "}")


def visible(value: str) -> str:
    """The event text the way libass shows it, override blocks removed."""
    return BLOCK.sub("", value)


def data_for(times, **overrides):
    words = [{"t": t} if t is not None else {} for t in times]
    return {**K.KARAOKE_DEFAULTS, "words": words, **overrides}


def plan_for(text, times, window_to=10.0, **overrides):
    return K.plan_of(text, data_for(times, **overrides), 0.0, window_to, 0.0)


def body_of(event: str) -> str:
    """The ``Text`` field of a Dialogue line (the ten commas of the format)."""
    return ",".join(event[len("Dialogue: "):].split(",")[9:])


class TokenTest(unittest.TestCase):
    def test_words_keep_their_offsets(self) -> None:
        tokens = K.tokenize("Hey  there\nfriend")
        self.assertEqual([t["w"] for t in tokens], ["Hey", "there", "friend"])
        self.assertEqual([(t["i0"], t["i1"]) for t in tokens], [(0, 3), (5, 10), (11, 17)])
        self.assertTrue(tokens[1]["nl"])

    def test_weight_counts_utf16_units(self) -> None:
        # The preview is JavaScript, which indexes UTF-16 code units: an emoji is
        # two there and one here, so both sides must count the same way or the
        # distribute pass differs between preview and render by a few percent.
        self.assertEqual(K.text_length("🎉"), 2)
        self.assertEqual(K.word_weight("🎉"), 2.0)
        self.assertEqual(K.word_weight("a"), 2.0)  # never shorter than two units

    def test_the_tokeniser_is_the_shared_index_space(self) -> None:
        # Times are stored per word *number*, so a tokeniser difference would
        # shift every colour by a word without any code looking wrong.
        text = "a,  b.\nc\td"
        self.assertEqual([t["w"] for t in K.tokenize(text)], ["a,", "b.", "c", "d"])
        self.assertEqual([t["i0"] for t in K.tokenize(text)], [0, 4, 7, 9])


class SentenceSplitTest(unittest.TestCase):
    def test_abbreviations_do_not_cut(self) -> None:
        groups = K.sentence_groups(K.tokenize("Mr. Smith went home. Mrs. Jones left."), [], 12)
        self.assertEqual(groups, [(0, 3), (4, 6)])

    def test_a_lowercase_follower_means_a_decimal_not_a_stop(self) -> None:
        self.assertEqual(K.sentence_groups(K.tokenize("it is 3.5 km far."), [], 12), [(0, 4)])

    def test_the_cap_holds_and_prefers_a_comma(self) -> None:
        # Deferring a cap cut "to the next comma" used to run the line on to the
        # next full stop instead: a 4-word setting produced an eight-word
        # caption. The cap is the promise; the comma may only move the cut
        # *inside* it.
        tokens = K.tokenize("alpha beta, gamma delta, epsilon zeta eta theta.")
        self.assertEqual(K.sentence_groups(tokens, [], 4), [(0, 3), (4, 7)])
        for a, b in K.sentence_groups(tokens, [], 3):
            self.assertLessEqual(b - a + 1, 3)
        # No punctuation at all: cut at the cap anyway.
        flat = K.sentence_groups(K.tokenize(" ".join(f"w{i}" for i in range(13)) + "."), [], 12)
        self.assertLessEqual(flat[0][1] - flat[0][0] + 1, 12)
        self.assertTrue(all(b - a + 1 >= 2 for a, b in flat), flat)  # never a 1-word stub

    def test_forced_break_and_forced_glue(self) -> None:
        # ``br: 1`` ends the sentence after that word and ``br: -1`` forbids a
        # cut there — the two buttons the chips surface draws are exactly these.
        tokens = K.tokenize("a b. c d e f")
        self.assertEqual(K.sentence_groups(tokens, [{"br": 1}, {}, {"br": -1}, {}, {}, {}], 12), [(0, 0), (1, 5)])
        # the glue suppresses the only cut in the run, so the text stays one line
        self.assertEqual(K.sentence_groups(tokens, [{}, {"br": -1}, {}, {}, {}, {}], 12), [(0, 5)])

    def test_typed_line_breaks_are_cuts(self) -> None:
        self.assertEqual(K.sentence_groups(K.tokenize("one two\nthree four"), [], 80), [(0, 1), (2, 3)])

    def test_empty_text(self) -> None:
        self.assertEqual(K.sentence_groups([], [], 12), [])


class TimeModelTest(unittest.TestCase):
    def test_auto_time_spreads_by_weight_inside_the_window(self) -> None:
        times = [w["t"] for w in K.auto_time(K.tokenize("one two three four."), [], 12, 0.0, 8.0)]
        self.assertTrue(all(v is not None for v in times), times)
        self.assertTrue(all(b > a for a, b in zip(times, times[1:])), times)
        self.assertLess(times[-1], 8.0)

    def test_auto_time_keeps_every_time_the_user_tapped(self) -> None:
        tokens = K.tokenize("one two three four.")
        times = K.auto_time(tokens, [{"t": 1.0}, {}, {}, {"t": 5.0}], 12, 0.0, 8.0)
        self.assertEqual([times[0]["t"], times[3]["t"]], [1.0, 5.0])
        self.assertTrue(1.0 < times[1]["t"] < times[2]["t"] < 5.0)

    def test_auto_time_survives_a_shorter_word_array(self) -> None:
        # A caption that grew since the last save has more words than times.
        times = K.auto_time(K.tokenize("one two three four."), [{"t": 1.0}], 12, 0.0, 6.0)
        self.assertEqual(len(times), 4)
        self.assertEqual(times[0]["t"], 1.0)

    def test_snap_moves_to_the_nearest_onset_inside_the_radius(self) -> None:
        self.assertEqual(K.snap_times([1.0, 2.45, 2.7], [1.0, 2.5], 0.1), [1.0, 2.5, 2.7])
        self.assertEqual(K.snap_times([1.02], [1.0], 0.01), [1.02])

    def test_make_continuous_sorts_out_mistimed_taps(self) -> None:
        out = [w["t"] for w in K.make_continuous([{"t": 3.0}, {"t": 1.0}, {"t": 1.0}], 0.04)]
        self.assertEqual(out, [3.0, 3.04, 3.08])  # never earlier than its neighbour

    def test_plan_leads_in_and_holds_a_tail(self) -> None:
        plan = plan_for("Hello there friend.", [1.0, 2.0, 3.0])
        sentence = plan["sentences"][0]
        self.assertAlmostEqual(sentence["start"], 1.0 - K.KARAOKE_DEFAULTS["leadIn"], places=6)
        self.assertGreaterEqual(sentence["end"], 3.0)
        self.assertAlmostEqual(plan["end"], sentence["end"], places=6)

    def test_two_sentences_are_never_on_screen_together(self) -> None:
        # Times tapped on top of each other: the *words* are the truth, so the
        # display window of the first line shrinks and the second one never
        # appears before its own first word is sung.
        words = [{"t": 1.0}, {"t": 1.4, "br": 1}, {"t": 1.5}, {"t": 1.9}]
        plan = K.plan_of("one two three four", {**K.KARAOKE_DEFAULTS, "words": words}, 0.0, 10.0, 0.0)
        first, second = plan["sentences"]
        self.assertAlmostEqual(first["end"], second["start"], places=6)
        self.assertLessEqual(second["start"], second["words"][0]["t0"] + 1e-6)

    def test_words_without_times_interpolate(self) -> None:
        plan = plan_for("one two three four five.", [1.0, None, None, None, 5.0], 8.0)
        times = [w["t0"] for w in plan["sentences"][0]["words"]]
        self.assertEqual(len(times), 5)
        self.assertTrue(all(b > a for a, b in zip(times, times[1:])), times)

    def test_a_short_clip_still_gets_a_frame(self) -> None:
        plan = plan_for("word another.", [0.0, 0.05], 0.5)
        self.assertEqual(len(plan["sentences"]), 1)
        self.assertGreater(plan["sentences"][0]["end"], plan["sentences"][0]["start"])

    def test_nothing_is_on_screen_once_the_last_line_left(self) -> None:
        plan = plan_for("one two.", [1.0, 2.0])
        self.assertEqual(plan["end"], plan["sentences"][0]["end"])
        for t in (0.1, 9.5):
            self.assertEqual(K.karaoke_at(plan, t)["s"], -1, f"t={t}")
            self.assertEqual(K.word_states(plan, K.karaoke_at(plan, t)), [], f"t={t}")

    def test_the_lead_in_fades_the_line_in_before_the_first_word(self) -> None:
        plan = plan_for("one two.", [1.0, 2.0])
        mid = K.karaoke_at(plan, 0.8)          # inside the lead-in of a line starting at 0.65
        self.assertEqual((mid["s"], mid["k"]), (0, -1))
        self.assertGreater(mid["fade"], 0.0)
        self.assertLess(mid["fade"], 1.0)
        self.assertEqual(K.word_states(plan, mid), ["next", "next"])

    def test_sweep_progress_is_monotonic_within_a_word(self) -> None:
        plan = plan_for("hello there.", [1.0, 2.0])
        word = plan["sentences"][0]["words"][0]
        us = [K.karaoke_at(plan, t)["u"] for t in (word["t0"] + 0.001, (word["t0"] + word["t1"]) / 2, word["t1"] - 0.001)]
        self.assertEqual(us, sorted(us))
        self.assertLess(us[0], us[-1])

    def test_empty_plan_is_a_frame_too(self) -> None:
        empty = {"sentences": [], "end": 0.0}
        self.assertEqual(K.karaoke_at(empty, 1.0), {"s": -1, "k": -1, "u": 0.0, "done": -1, "fade": 1.0})
        self.assertEqual(K.word_states(empty, K.karaoke_at(empty, 1.0)), [])


class SanitizeTest(unittest.TestCase):
    def test_nothing_timed_means_no_karaoke(self) -> None:
        self.assertIsNone(K.sanitize_karaoke({"words": [{}, {}]}, 2))
        self.assertIsNone(K.sanitize_karaoke({"words": [{"t": None}]}, 1))
        self.assertIsNone(K.sanitize_karaoke(None, 3))
        self.assertIsNone(K.sanitize_karaoke({"words": "not a list"}, 3))

    def test_out_of_range_and_garbage_are_dropped(self) -> None:
        got = K.sanitize_karaoke({"words": [{"t": -5}, {"t": 1e9}, {"t": 2.5, "br": 9}, "junk", {"t": 4.0}]}, 5)
        self.assertEqual(got["words"], [{}, {}, {"t": 2.5}, {}, {"t": 4.0}])

    def test_style_editor_and_fill_are_validated(self) -> None:
        got = K.sanitize_karaoke({"style": "laser", "editor": "midi", "fill": "red", "leadIn": -3, "tail": 99,
                                  "words": [{"t": 1.0}]}, 1)
        self.assertEqual(got["style"], K.KARAOKE_DEFAULTS["style"])
        self.assertEqual(got["editor"], K.KARAOKE_DEFAULTS["editor"])
        self.assertEqual(got["fill"], K.KARAOKE_DEFAULTS["fill"])
        self.assertEqual(got["leadIn"], 0.0)
        self.assertLessEqual(got["tail"], 10.0)

    def test_a_good_hex_fill_survives(self) -> None:
        got = K.sanitize_karaoke({"fill": "#FFD166", "words": [{"t": 1.0}]}, 1)
        self.assertEqual(got["fill"].lower(), "#ffd166")

    def test_huge_caption_is_capped_not_crashed(self) -> None:
        got = K.sanitize_karaoke({"words": [{"t": float(i)} for i in range(9000)]}, 9000)
        self.assertLessEqual(len(got["words"]), 5000)

    def test_has_karaoke_agrees_with_the_sanitizer(self) -> None:
        self.assertTrue(K.has_karaoke({"karaoke": data_for([1.0])}))
        self.assertFalse(K.has_karaoke({"karaoke": {"words": [{}, {}]}}, ))
        self.assertFalse(K.has_karaoke({"karaoke": None}))
        self.assertFalse(K.has_karaoke(None))

    def test_the_style_block_is_kept_and_clamped(self) -> None:
        got = K.sanitize_karaoke({"words": [{"t": 1.0}], "text": {"family": "  Impact  ", "size": 9000,
                                                                  "x": -20, "colour": "#ff0000", "junk": 1}}, 1)
        self.assertEqual(got["text"], {"family": "Impact", "size": 400, "x": 0.0, "colour": "#ff0000"})

    def test_an_empty_or_absent_style_block_stays_absent(self) -> None:
        # "nothing overridden" must be one shape for the loader, the preview and
        # the render, or a saved project grows a phantom style that changes looks.
        for raw in ({}, {"junk": 1}, None, "nope", [], 4):
            got = K.sanitize_karaoke({"words": [{"t": 1.0}], "text": raw}, 1)
            self.assertNotIn("text", got, repr(raw))

    def test_a_boolean_is_not_a_size_and_a_string_is_not_an_outline(self) -> None:
        # The block is stricter than the timing fields on purpose: Python would
        # happily read True as 1 and "24" as a size, while the preview's
        # `typeof v === "number"` rejects both. Same garbage, same answer.
        got = K.sanitize_karaoke({"words": [{"t": 1.0}],
                                  "text": {"size": True, "bold": 1, "x": "24", "outline": "yes"}}, 1)
        self.assertNotIn("text", got)
        self.assertEqual(K.sanitize_karaoke({"words": [{"t": 1.0}],
                                             "text": {"size": 60, "bold": True, "outline": False}}, 1)["text"],
                         {"size": 60, "bold": True, "outline": False})

    def test_only_a_hex_colour_survives(self) -> None:
        for bad in ("red", "#abc", "#ff00", "", None, 3):
            self.assertNotIn("colour", K.sanitize_karaoke({"words": [{"t": 1.0}], "text": {"colour": bad}}, 1).get("text", {}), repr(bad))
        for good in ("#ff0000", "#AABBCC"):
            self.assertEqual(K.sanitize_karaoke({"words": [{"t": 1.0}], "text": {"colour": good}}, 1)["text"]["colour"], good)


class AssTest(unittest.TestCase):
    TEXT = "The quick brown fox."
    TIMES = [1.0, 2.0, 3.0, 4.0]

    def events(self, style="sweep", text=None, times=None):
        text = text or self.TEXT
        times = times or self.TIMES
        data = data_for(times)
        return K.build_karaoke_events(
            text, K.plan_of(text, data, 0.0, 10.0, 0.0),
            style=style, fill=data["fill"], base_colour="#ffffff", cx=960, cy=880,
            fade_in=0.2, fade_out=0.25,
        )

    def test_one_event_per_sentence_for_swept_styles(self) -> None:
        for style in ("sweep", "word", "reveal"):
            with self.subTest(style=style):
                self.assertEqual(len(self.events(style=style)), 1)

    def test_pop_writes_one_event_per_word(self) -> None:
        # Per-word scale cannot live inside one \k run, so "pop" needs an event
        # per word — and WrapStyle 2 keeps the line from re-wrapping around them.
        self.assertEqual(len(self.events(style="pop")), 4)

    def test_tags_reach_libass_with_a_single_backslash(self) -> None:
        # Inside an override block "\\pos" is not a tag, so a doubled backslash
        # silently drops the positioning, the fade and the sweep itself.
        for style in ("sweep", "word", "reveal", "pop"):
            for event in self.events(style=style):
                self.assertNotIn(BS * 2, event, style)
        self.assertIn("{" + BS + "an5", self.events()[0])
        self.assertIn(BS + "t(0,", self.events(style="pop")[0])
        self.assertIn(BS + "fscx118", self.events(style="pop")[0])

    def test_the_visible_text_is_the_caption(self) -> None:
        self.assertEqual(visible(body_of(self.events()[0])), self.TEXT)

    def test_each_words_sweep_last_exactly_as_long_as_that_word_was_timed(self) -> None:
        # The promise of the feature: a gap the user tapped stays a gap in the
        # MP4. Distributing over the whole line instead would land every word
        # late, and the twin test cannot see it because it compares plans.
        plan = plan_for(self.TEXT, self.TIMES)
        body = body_of(self.events()[0])
        words = plan["sentences"][0]["words"]
        for word, chunk in zip(words, body.split(" ")):
            want = (word["t1"] - word["t0"]) * 100
            got = sum(int(m) for m in KF.findall(chunk))
            self.assertLessEqual(abs(got - want), max(2, K.text_length(K.tokenize(self.TEXT)[word["i"]]["w"])))
        self.assertLessEqual(sum(int(m) for m in KF.findall(body)) / 100,
                             plan["sentences"][0]["end"] - plan["sentences"][0]["start"])

    def test_word_style_flips_once_per_word(self) -> None:
        event = self.events(style="word")[0]
        self.assertEqual(len(K_ONLY.findall(event)), 4)
        self.assertNotIn(BS + "kf", event)

    def test_sweep_colours_are_fill_then_base(self) -> None:
        # libass interpolates secondary → primary across a \k run, so the colour
        # a word *arrives at* is \1c: that is the sung colour. Inverting the two
        # sweeps the caption from highlight into plain text, backwards.
        body = self.events()[0]
        self.assertIn(BS + "1c&H0066D1FF&", body)   # #ffd166 in &HAABBGGRR&, alpha 00 = solid
        self.assertIn(BS + "2c&H00FFFFFF&", body)   # the caption's own colour, unsung

    def test_reveal_hides_unsung_words(self) -> None:
        body = self.events(style="reveal")[0]
        self.assertIn(BS + "1c&H00FFFFFF&", body)   # sung words are the caption colour …
        self.assertIn(BS + "2c&HFF", body)          # … and unsung words are transparent

    def test_a_hard_break_is_written_as_a_break_tag(self) -> None:
        events = self.events(text="one two\nthree four", times=[1.0, 2.0, 3.0, 4.0])
        self.assertEqual(len(events), 2)
        self.assertIn(BS + "N", body_of(events[0]))

    def test_alignment_tag_is_used_verbatim(self) -> None:
        event = K.build_karaoke_events(self.TEXT, plan_for(self.TEXT, self.TIMES), style="sweep", fill="#ffd166",
                                       base_colour="#ffffff", cx=100, cy=200, fade_in=0.0, fade_out=0.0,
                                       align_tag=BS + "an1")[0]
        self.assertIn(BS + "an1" + BS + "pos(100,200)", event)

    def test_the_fade_comes_from_the_data_window(self) -> None:
        self.assertIn(BS + "fad(200,250)", self.events()[0])

    def test_braces_cannot_inject_tags(self) -> None:
        text = "braces {yes} here"
        events = K.build_karaoke_events(
            text, K.plan_of(text, data_for([1.0, 2.0, 3.0, 4.0]), 0.0, 10.0, 0.0), style="sweep", fill="#ffd166",
            base_colour="#ffffff", cx=0, cy=0, fade_in=0.0, fade_out=0.0)
        # The text is written character by character, so compare what the viewer
        # sees once the override blocks are out of the way.
        seen = visible(body_of(events[0]))
        self.assertIn("(yes)", seen)
        self.assertNotIn("{yes}", seen)
        from app.text_effects import escape_ass
        self.assertEqual(K._escape_ass(text), escape_ass(text))  # no karaoke-only surprises


class DocumentTest(unittest.TestCase):
    """The renderer's entry point, without the renderer's dependencies."""

    def item(self, **overrides):
        return {
            "text": "The quick brown fox.",
            "karaoke": data_for([1.0, 2.0, 3.0, 4.0]),
            "textStart": 0.0, "textEnd": 10.0,
            "fontFamily": "DejaVu Sans", "fontSize": 48, "fontColor": "#ffffff",
            "textX": 50, "textY": 88, "textOutline": 2,
            **overrides,
        }

    def test_document_has_the_header_and_the_words(self) -> None:
        from app.text_effects import karaoke_document

        doc = karaoke_document(self.item(), {}, 1920, 1080)
        self.assertIsNotNone(doc)
        self.assertIn("WrapStyle: 2", doc)     # never re-wrap while words change colour
        self.assertIn("ScriptType: v4.00+", doc)
        self.assertIn("[V4+ Styles]", doc)
        self.assertIn("Style: FX,", doc)
        self.assertIn("PlayResX: 1920", doc)
        self.assertIn("quick", visible(doc))
        self.assertNotIn(BS * 2, doc)

    def test_no_karaoke_no_document(self) -> None:
        from app.text_effects import karaoke_document

        self.assertIsNone(karaoke_document(self.item(karaoke=None), {}, 1920, 1080))
        self.assertIsNone(karaoke_document(self.item(text="", karaoke=data_for([1.0])), {}, 1920, 1080))
        self.assertIsNone(karaoke_document(self.item(text="   "), {}, 1920, 1080))

    def test_overlay_filter_quotes_the_path(self) -> None:
        from app.text_effects import karaoke_overlay_filter

        got = karaoke_overlay_filter(Path("/tmp/it's here/k.ass"))
        self.assertTrue(got.startswith("ass=filename="), got)
        self.assertNotIn("/tmp/it's here", got)  # the apostrophe is escaped, not passed through


class TextStyleTest(unittest.TestCase):
    """``karaoke.text``: the sung words wearing something other than the caption.

    Three promises, in this order: an unset block changes nothing at all (so
    every project saved before this feature renders byte for byte as before); a
    set field lands in the one ``Style:`` line libass reads, plus the ``\\pos``
    and the ``\\2c`` base colour the sweep paints with; and a field the block
    cannot honour is dropped rather than guessed at, so the preview can never
    promise a look the MP4 will not deliver.
    """

    def item(self, **overrides):
        return {
            "text": "The quick brown fox.",
            "karaoke": data_for([1.0, 2.0, 3.0, 4.0]),
            "textStart": 0.0, "textEnd": 10.0,
            "fontFamily": "DejaVu Sans", "fontSize": 48, "fontColor": "#ffffff",
            "textX": 50, "textY": 88, "textOutline": True,
            **overrides,
        }

    def document(self, **item_overrides):
        from app.text_effects import karaoke_document

        doc = karaoke_document(self.item(**item_overrides), {}, 1920, 1080)
        self.assertIsNotNone(doc)
        return doc

    def style_parts(self, doc: str) -> list[str]:
        line = next(l for l in doc.splitlines() if l.startswith("Style: FX,"))
        return line.split(",")

    def test_nothing_set_is_the_caption_exactly(self) -> None:
        plain = self.document()
        for inert in ({}, {"shadow": 4}, {"size": "72"}, None):
            self.assertEqual(self.document(karaoke=data_for([1.0, 2.0, 3.0, 4.0], text=inert)), plain, repr(inert))

    def test_the_style_line_carries_the_override(self) -> None:
        from app.text_effects import ass_colour

        doc = self.document(karaoke=data_for([1.0, 2.0, 3.0, 4.0], text={
            "family": "Impact", "size": 96, "bold": False, "italic": True, "underline": True,
            "colour": "#ff0000", "outline": False, "x": 10, "y": 30}))
        # Name, Fontname, Fontsize, Primary, Secondary, OutlineColour, Back,
        # Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle,
        # BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
        parts = self.style_parts(doc)
        self.assertEqual(parts[1], "Impact")
        self.assertEqual(parts[2], "96")
        self.assertEqual(parts[3], ass_colour("#ff0000"))  # the *unsung* colour
        self.assertEqual((parts[7], parts[8], parts[9]), ("0", "-1", "-1"))
        self.assertEqual(parts[16], "0")                   # outline off = no border
        self.assertIn(BS + "pos(192", doc)                 # 10% of 1920 …
        self.assertIn("324", doc)                          # … and 30% of 1080

    def test_the_sweep_recolours_from_the_overridden_base(self) -> None:
        doc = self.document(karaoke=data_for([1.0, 2.0, 3.0, 4.0], style="sweep", fill="#00ff00",
                                                                      text={"colour": "#ff0000"}))
        # \1c is the colour a word arrives at, \2c the one it leaves — and the
        # latter must be the override, not the caption's own colour. The events go
        # through karaoke's own 8-digit writer (alpha first), not the style line's.
        self.assertIn(BS + "1c" + K._colour("#00ff00") + BS + "2c" + K._colour("#ff0000"), doc)
        self.assertNotIn(K._colour("#ffffff"), doc)

    def test_only_the_keys_that_are_set_move_the_caption(self) -> None:
        self.assertEqual(K.style_overrides({"text": {"size": 60}}), {"fontSize": 60})
        self.assertEqual(K.style_overrides({"text": {"family": "Zilla", "outline": False}}),
                         {"fontFamily": "Zilla", "textOutline": False})
        for inert in ({}, None, "nope", {"colour": "red"}):
            self.assertEqual(K.style_overrides({"text": inert}), {}, repr(inert))

    def test_the_document_is_otherwise_untouched(self) -> None:
        # A style override restyles the words; it must not re-time them.
        plain = self.document()
        styled = self.document(karaoke=data_for([1.0, 2.0, 3.0, 4.0], text={"size": 120, "family": "Impact"}))
        self.assertEqual([l for l in plain.splitlines() if l.startswith("Dialogue:") and "0:00:0" not in l], [])
        mine = [visible(body_of(l)) for l in plain.splitlines() if l.startswith("Dialogue:")]
        theirs = [visible(body_of(l)) for l in styled.splitlines() if l.startswith("Dialogue:")]
        self.assertEqual(mine, theirs)
        times = lambda doc: [l.split(",")[1:3] for l in doc.splitlines() if l.startswith("Dialogue:")]
        self.assertEqual(times(plain), times(styled))



    """Each engine is correct on its own; these are the joints, which no unit
    test of either engine can see."""

    def source(self, relative: str) -> str:
        return (REPO / relative).read_text(encoding="utf-8")

    def has(self, text: str, needle: str, what: str) -> None:
        self.assertTrue(needle in text, what)

    def test_the_renderer_uses_the_karaoke_document_and_degrades(self) -> None:
        renderer = self.source("backend/app/renderer.py")
        self.has(renderer, "karaoke_mod.has_karaoke(item)", "the branch is skipped for plain captions")
        self.has(renderer, "karaoke_document(", "the document is built for the item")
        self.has(renderer, "karaoke_overlay_filter(", "the ass= filter comes from text_effects")
        # Without libass the branch is not entered at all, so the caption falls
        # through to the ordinary path: words shown, no sweep, no failed job.
        guard = "has_ass and ass_path is not None and karaoke_mod.has_karaoke(item)"
        self.has(renderer, guard, "libass is required for the karaoke branch")
        self.has(renderer[renderer.index(guard):renderer.index(guard) + 700], "if doc is not None:",
                 "an unusable document degrades instead of failing")

    def test_both_engines_offer_the_same_style_fields(self) -> None:
        # The popup builds its controls from KARAOKE_TEXT_FIELDS and the render
        # splices what TEXT_STYLE maps. If one grows a field the other does not
        # have, a control appears that nothing honours — so the lists are pinned
        # here, in order, by name.
        core = self.source("src/karaokeCore.ts")
        table = core[core.index("KARAOKE_TEXT_FIELDS: readonly"):core.index("export const KARAOKE_DEFAULTS")]
        self.assertEqual(re.findall(r"\{ id: '(\w+)'", table), list(K.TEXT_STYLE))

    def test_the_render_splices_the_block_before_the_geometry(self) -> None:
        effects = self.source("backend/app/text_effects.py")
        self.has(effects, "overrides = karaoke_mod.style_overrides(data)", "the block is read from the sanitized data")
        self.has(effects, "_geometry({**item, **overrides} if overrides else item, defaults, width, height)",
                 "the caption is restyled through the ordinary geometry path, not ASS tags")

    def test_the_preview_merges_the_block_over_the_caption(self) -> None:
        # Neither engine's unit tests can see this: the popup's preview has to run
        # the same merge, including the font wait, or a bigger face in a different
        # family shows up in the MP4 and not where the user was looking at it.
        stage = self.source("src/KaraokeStage.tsx")
        self.has(stage, "karaokePaintStyle(props, karaoke)", "the stage merges the block over the caption props")
        self.has(stage, "ensureFont(look.family, look.bold, look.italic)", "the webfont wait follows the override")
        self.has(stage, "look.fontSize", "the size comes from the merge, not from the caption")
        self.has(stage, "left: `${look.x}%`", "x moves the block however it is aligned, like \\pos does")
        editor = self.source("src/KaraokeEditor.tsx")
        self.has(editor, "karaokePaintStyle(stageStyle, data)", "the popup's own preview paints the same look")
        self.has(editor, "KARAOKE_TEXT_FIELDS.filter(field => field.kind === 'flag')",
                 "the controls are built from the table the engines read")

    def test_the_field_is_persisted_and_loaded(self) -> None:
        # Media items are stored as free-form dicts, so nothing has to be taught
        # about `karaoke` — but the TypeScript model must declare it, or the
        # editor's patch never reaches the job.
        self.has(self.source("src/mediaItem.ts"), "karaoke?: KaraokeData", "the item declares the field")
        self.has(self.source("backend/app/main.py"), "media: list[dict[str, Any]]", "the API keeps unknown item keys")

    def test_the_popup_is_mounted_from_both_places(self) -> None:
        app = self.source("src/App.tsx")
        self.has(app, "<KaraokeEditor", "the caption popup opens the timing editor")
        self.has(app, "onOpenKaraoke", "and it is a button, not a hidden route")
        self.has(self.source("src/MovieEditor.tsx"), "<KaraokeEditor", "the movie editor opens the same editor")

    def test_the_preview_paints_the_same_plan(self) -> None:
        app = self.source("src/App.tsx")
        self.has(app, "<KaraokeStage", "the storyboard frame and the preview use the karaoke layer")
        self.has(app, "sanitizeKaraoke", "only real timing switches the frame over")

    def test_the_four_surfaces_exist_and_are_per_item(self) -> None:
        core = self.source("src/karaokeCore.ts")
        for surface in ("'strip'", "'tap'", "'chips'", "'wave'"):
            self.has(core, surface, f"the {surface} surface exists")
        editor = self.source("src/KaraokeEditor.tsx")
        self.has(editor, "KARAOKE_EDITORS.map", "the popup offers the choice")
        self.has(editor, "data.editor", "the choice is stored per movie")
        # …and never in general settings: no global control may mention it.
        for name in ("src/App.tsx", "src/MovieEditor.tsx", "src/SettingsDialog.tsx"):
            if (REPO / name).exists():
                self.assertTrue("karaokeEditor" not in self.source(name), name)

    def test_the_strip_is_styled(self) -> None:
        styles = self.source("src/styles.css")
        for selector in (".kara-strip", ".kara-block", ".kara-slot", ".kara-wave", ".karaoke-editor", ".kara-playhead"):
            self.has(styles, selector, selector)


if __name__ == "__main__":
    unittest.main()
