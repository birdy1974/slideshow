"""The word bar under the karaoke strip: one sentence across the preview's width.

The strip times *sentences* over the whole clip, so the words inside a line sit a
few pixels apart and their dividers are millimetres wide — "it is easier to fix the
timing of the words" is the whole reason for the bar below it. The bar is a view of
the same plan, never a second source of truth: it reads `planOf`'s sentence and
writes the start of the word after the dragged boundary, so the strip, the preview
stage and the render's ``\\k`` tags all follow it for free.

That makes the bar three pure functions in ``src/karaokeCore.ts``, run through
``karaoke_wordbar.mjs`` the way ``test_karaoke_strip.py`` runs the strip's geometry:
``karaokeWordBar`` (the layout), ``karaokeWordBarTime`` (a pointer into seconds) and
``karaokeWordBarCut`` (a drag into a word start). The numbers below are what the
popup draws, so a regression here is a regression on screen.

The last class pins the two things the functions cannot see: the bar is *not*
inside the strip's zoom scroller (its axis is one sentence, so zoom would only push
part of it out of view), and it has its own classes — the sung line once wore
``.kara-block`` and inherited a timing row's 34 px clipped chrome.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "karaoke_wordbar.mjs"
CORE_TS = REPO / "src" / "karaokeCore.ts"
EDITOR_TSX = REPO / "src" / "KaraokeEditor.tsx"


def _node() -> str | None:
    return shutil.which("node")


def _run(cases: list[dict]) -> list[dict]:
    proc = subprocess.run(
        [_node(), "--experimental-strip-types", str(DRIVER)],
        input=json.dumps({"cases": cases}),
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"karaoke_wordbar.mjs failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


LINE = {
    "from": 4, "to": 8, "start": 10.0, "end": 13.4,
    "words": [
        {"i": 4, "t0": 10.2, "t1": 10.9},
        {"i": 5, "t0": 11.0, "t1": 11.5},
        {"i": 6, "t0": 11.9, "t1": 12.6},
        {"i": 7, "t0": 12.6, "t1": 13.0},
        {"i": 8, "t0": 13.1, "t1": 13.3},
    ],
}


class SentenceFlagsTest(unittest.TestCase):
    """`hold` and `plain` are word fields the render reads, not popup decoration.

    These run the real ``backend/app/karaoke.py`` — the twin of the bar — because
    the whole promise of the karaoke surfaces is that what the popup shows is what
    FFmpeg is handed. A flag that only changed the preview would pass every test
    above and still do nothing in the MP4.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import sys
        sys.path.insert(0, str(REPO / "backend"))
        from app import karaoke  # noqa: PLC0415
        cls.k = karaoke

    TEXT = "Never gonna give you up"
    DATA = {"style": "sweep", "fill": "#9BE564", "leadIn": 0.0, "tail": 0.5, "snap": 0.0,
            "maxWords": 12, "editor": "strip",
            "words": [{"t": 1.0}, {"t": 2.0}, {"t": 3.0}, {"t": 4.0}, {"t": 5.0, "hold": 2.0}]}

    def plan(self, data: dict) -> dict:
        return self.k.plan_of(self.TEXT, data, 0.0, 20.0, 20.0)

    def test_a_hold_moves_the_last_words_end_and_the_sentence_with_it(self) -> None:
        untimed_tail = {**self.DATA, "words": [dict(w) for w in self.DATA["words"][:4]] + [{"t": 5.0}]}
        with_hold, without = self.plan(self.DATA), self.plan(untimed_tail)
        held = with_hold["sentences"][0]["words"][-1]
        free = next(w for w in without["sentences"][0]["words"] if w["i"] == 4)
        self.assertEqual(7.0, held["t1"], "5.0 + a 2.0 s hold")  # noqa: E501
        self.assertLess(free["t1"], held["t1"], "without a hold the line ends on the automatic breath")
        self.assertEqual(held["t1"] + 0.5, with_hold["sentences"][0]["end"], "the sentence is its last word plus the tail")

    def test_a_hold_cannot_push_the_line_past_the_caption_window(self) -> None:
        wide = self.plan({**self.DATA, "words": [dict(w) for w in self.DATA["words"][:4]] + [{"t": 5.0, "hold": 40.0}]})
        s = wide["sentences"][0]
        self.assertLessEqual(s["words"][-1]["t1"], 20.0)
        self.assertLessEqual(s["end"], 20.0)

    def test_a_plain_line_has_no_word_colouring_in_either_surface(self) -> None:
        data = {**self.DATA, "words": [dict(w) for w in self.DATA["words"][:4]] + [{"t": 5.0, "plain": 1}]}
        plan = self.plan(data)
        s = plan["sentences"][0]
        self.assertTrue(s.get("plain"))
        states = self.k.word_states(plan, self.k.karaoke_at(plan, 3.0, "sweep"), "sweep")
        self.assertEqual(["next"] * 5, states, "the stage paints nothing while the line is up")
        events = self.k.build_karaoke_events(self.TEXT, plan, style="sweep", fill="#9BE564",
                                             base_colour="#FFFFFF", cx=960, cy=900, fade_in=0.0, fade_out=0.0)
        self.assertNotIn("\\k", events[0])
        self.assertIn("\\1c&H00FFFFFF&", events[0], "the line wears the caption colour, not the fill")
        self.assertIn("Never gonna give you up", events[0].replace("\\N", " "))

    def test_pop_still_shows_the_words_but_never_colours_them(self) -> None:
        data = {**self.DATA, "words": [dict(w) for w in self.DATA["words"][:4]] + [{"t": 5.0, "plain": 1}]}
        plan = self.plan(data)
        events = self.k.build_karaoke_events(self.TEXT, plan, style="pop", fill="#9BE564",
                                             base_colour="#FFFFFF", cx=960, cy=900, fade_in=0.0, fade_out=0.0)
        self.assertEqual(5, len(events), "pop is one event per word, plain or not — that is the *appearance*, not the colouring")
        self.assertNotIn("\\kf", "".join(events))
        self.assertIn("\\1c&H00FFFFFF&", events[0])

    def test_the_sanitizer_keeps_both_and_a_flagged_only_block_survives(self) -> None:
        kept = self.k.sanitize_karaoke({"style": "sweep", "words": [{"t": 2.0, "hold": 3.5, "plain": "1"}]}, 2)
        self.assertEqual(3.5, kept["words"][0]["hold"])
        self.assertEqual(1, kept["words"][0]["plain"])
        junk = self.k.sanitize_karaoke({"style": "sweep", "words": [{"t": 2.0, "hold": -4, "plain": "yes"}]}, 2)
        self.assertNotIn("hold", junk["words"][0], "a negative hold is not a duration")
        self.assertNotIn("plain", junk["words"][0], "anything but 1 is not the flag")
        only = self.k.sanitize_karaoke({"style": "sweep", "words": [{"plain": 1}]}, 1)
        self.assertIsNotNone(only, "a line that is flagged but not timed is still a decision")
        self.assertTrue(self.k.has_karaoke({"text": self.TEXT, "karaoke": only}))


class WordBarLayoutTest(unittest.TestCase):
    def test_the_bar_spans_the_sentence_and_tiles_the_words_by_time(self) -> None:
        got = _run([{"line": LINE, "ceiling": 14.0}])[0]
        bar = got["bar"]
        self.assertEqual(10.0, bar["from"])
        self.assertEqual(13.4, bar["to"])
        self.assertEqual(3.4, bar["dur"])
        # Percent of the *sentence*: (t0 - 10.0) / 3.4 and (t1 - t0) / 3.4. The
        # lead-in silence before the first word is visible as a gap, which is the
        # honest reading of a line that starts late.
        self.assertEqual(
            [{"i": 4, "left": 5.8824, "width": 20.5882},
             {"i": 5, "left": 29.4118, "width": 14.7059},
             {"i": 6, "left": 55.8824, "width": 20.5882},
             {"i": 7, "left": 76.4706, "width": 11.7647},
             {"i": 8, "left": 91.1765, "width": 5.8824}],
            [{"i": w["i"], "left": w["left"], "width": w["width"]} for w in bar["words"]],
        )
        # Times stay the clip's own: the bar changes the ruler, not the clock.
        self.assertEqual([w["i"] for w in bar["words"]], [4, 5, 6, 7, 8])
        # 0 % is the sentence start (the lead-in, not the first word),
        # 100 % is the end of the window: the bar is the line, whole.
        self.assertEqual([10.0, 11.7, 13.4], got["edges"])
        # A word's `left` and the pointer mapping are the same function twice.
        self.assertEqual(bar["words"][-1]["left"], got["frac"])

    def test_the_window_ends_the_caption_even_though_the_plan_added_a_tail(self) -> None:
        # `line.end` already carries the tail; the bar stops where the caption
        # stops, so you cannot drag a word into a frame that will not show it.
        got = _run([{"line": LINE, "ceiling": 12.0}])[0]
        self.assertEqual(12.0, got["bar"]["to"])
        self.assertTrue(all(w["t0"] <= 12.0 for w in got["bar"]["words"]))
        # Nothing is drawn outside the window it belongs to: a clipped tail word
        # loses its width instead of stealing frames the caption never shows.
        self.assertTrue(all(w["t1"] <= 12.0 for w in got["bar"]["words"]))
        self.assertTrue(all(0 <= w["width"] <= 100 for w in got["bar"]["words"]))

    def test_a_line_without_a_window_ceiling_still_gets_a_bar(self) -> None:
        got = _run([{"line": LINE}])[0]
        self.assertEqual(13.4, got["bar"]["to"])
        self.assertEqual(10.0, got["bar"]["from"])
        self.assertEqual(3.4, got["bar"]["dur"])


class WordBarDragTest(unittest.TestCase):
    def test_a_divider_moves_one_word_between_its_neighbours(self) -> None:
        # Drag the boundary before word 5 (i=5) onto 11.8 s: word 5 starts there
        # and word 4 keeps its own start — the delta is what a ripple-shift takes.
        one = _run([{"line": LINE, "ceiling": 14.0, "drag": [{"cut": 1, "seconds": 11.8}]}])[0]
        self.assertEqual({"i": 5, "t": 11.8, "delta": 0.8}, one["cuts"][0])
        # A drag onto where it already is changes nothing at all: no 1 ms edit for
        # the project to save and no flicker in the preview.
        none = _run([{"line": LINE, "ceiling": 14.0, "drag": [{"cut": 1, "seconds": 11.0}]}])[0]
        self.assertIsNone(none["cuts"][0])

    def test_a_divider_cannot_jump_its_neighbour_or_leave_the_line(self) -> None:
        cases = [{
            "line": LINE, "ceiling": 14.0,
            "drag": [
                {"cut": 1, "seconds": 9.0},    # before word 4: stops one gap after it
                {"cut": 1, "seconds": 11.95},  # onto word 6: stops one gap before it
                {"cut": 4, "seconds": 99.0},   # the last word: clamped to the window
                {"cut": 0, "seconds": 12.0},   # the line's own start: cannot pass word 5
                {"cut": 9, "seconds": 12.0},    # no such divider
            ],
        }]
        cuts = _run(cases)[0]["cuts"]
        gap = 0.06
        self.assertEqual(round(LINE["words"][0]["t0"] + gap, 4), cuts[0]["t"])
        self.assertEqual(round(LINE["words"][2]["t0"] - gap, 4), cuts[1]["t"])
        self.assertEqual(13.4, cuts[2]["t"])
        self.assertEqual(round(LINE["words"][1]["t0"] - gap, 4), cuts[3]["t"])
        self.assertIsNone(cuts[4])
        # Every word still starts after the one before it, so the render's \k
        # durations stay positive no matter how the boundaries are shoved.
        times = [w["t0"] for w in _run(cases)[0]["bar"]["words"]]
        self.assertEqual(sorted(times), times)

    def test_an_empty_bar_has_nothing_to_drag(self) -> None:
        got = _run([{"line": {"from": 0, "to": 0, "start": 1.0, "end": 2.0, "words": []},
                     "drag": [{"cut": 0, "seconds": 1.5}]}])[0]
        self.assertIsNone(got["bar"])
        self.assertIsNone(got["cuts"][0])


class TypedEndTest(unittest.TestCase):
    """The bar's number fields and the bar's handles write one number."""

    def test_a_typed_sentence_end_becomes_the_last_words_hold(self) -> None:
        cases = [
            {"line": LINE, "ceiling": 14.0, "end": 13.4, "tail": 0.2},   # exact: 0.1 s of hold
            {"line": LINE, "ceiling": 14.0, "end": 20.0, "tail": 0.2},   # past the window: capped
            {"line": LINE, "ceiling": 14.0, "end": 12.9, "tail": 0.2},   # before the word: floor
            {"line": {"from": 0, "to": 0, "start": 1.0, "end": 2.0, "words": []}, "end": 2.0, "tail": 0.2},
        ]
        got = _run(cases)
        self.assertEqual(0.1, got[0]["hold"])
        self.assertEqual(0.3, got[1]["hold"], "the caption window has the final say")
        self.assertEqual(0.05, got[2]["hold"], "a word is never held for less than a readable sliver")
        self.assertIsNone(got[3]["hold"], "no sentence, no number")

    def test_the_field_and_the_drag_write_the_same_field(self) -> None:
        src = (REPO / "src" / "KaraokeEditor.tsx").read_text(encoding="utf-8")
        self.assertIn("karaokeHoldForEnd(last.t0, end, data.tail, wordBar.to)", src)
        self.assertIn("words[last.i] = { ...words[last.i], hold: held.hold }", src)


class PopupShapeTest(unittest.TestCase):
    """The four things this popup's *shape* was asked for."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.editor = (REPO / "src" / "KaraokeEditor.tsx").read_text(encoding="utf-8")
        cls.layers = (REPO / "src" / "layers.tsx").read_text(encoding="utf-8")
        cls.css = (REPO / "src" / "styles.css").read_text(encoding="utf-8")

    def test_clicking_outside_does_not_close_the_karaoke_popup(self) -> None:
        # The backdrop is the one gesture that ends a session by accident, so the
        # karaoke popup opts out of it while keeping Escape and its own buttons —
        # `onDismiss` stays, which is what `useEscapeToClose` reads.
        self.assertIn('onDismiss={cancel} backdropDismiss={false}', self.editor)
        self.assertIn("backdropDismiss = true", self.layers)
        self.assertIn("onDismiss && backdropDismiss", self.layers)
        self.assertIn("useEscapeToClose(layer, onDismiss)", self.layers)

    def test_a_sentence_can_be_added_from_the_words_pane(self) -> None:
        self.assertIn("onClick={addSentenceAtCaret}", self.editor)
        # A hard break at the caret, and nothing if the caret is nowhere to cut.
        body = self.editor[self.editor.index("const addSentenceAtCaret"):self.editor.index("const [wordFocus")]
        self.assertIn("` ${before}\\n${after}`".replace(" ", ""), body)
        self.assertIn("retarget(data, tokens, tokenize(next))", body)

    def test_the_words_pane_numbers_the_sentences_and_selects_one(self) -> None:
        rail = self.editor[self.editor.index('className="kara-lines"'):]
        rail = rail[:rail.index("</div>\n            {draftText !==")]
        self.assertIn("<b>{i + 1}</b>", rail)
        self.assertIn("selectLine(i)", rail)
        self.assertIn("kara-line${i === selected ? ' active'", rail)
        self.assertIn(".kara-line.active{", self.css)

    def test_the_preview_is_small_and_the_timelines_own_the_full_width(self) -> None:
        # `display:contents` is what lets the strip and the bar span the popup while
        # the video stays in the left column, and the settings scroll beside it.
        self.assertIn(".kara-left{display:contents}", self.css)
        for selector, want in ((".kara-strip-scroll{", "grid-column:1/-1"),
                               (".kara-wordbar{grid-column", "1/-1"),
                               (".kara-stage-wrap{grid-column:1;grid-row:1;max-width:min(100%,460px)", "justify-self:start"),
                               (".kara-side{grid-column:2", "grid-row:1/span 5")):
            i = self.css.index(selector)
            self.assertIn(want, self.css[i:i + 400], selector)

    def test_the_bar_shows_the_audio_and_the_typed_times(self) -> None:
        bar = self.editor[self.editor.index('className="kara-wordbar"'):][:6200]
        self.assertIn('className="kara-wordbar-wave"', bar)
        self.assertIn("marginLeft: `-${(wordBar.from", bar)   # same peaks, cut to the sentence
        self.assertIn('className="kara-wordbar-times"', bar)
        self.assertIn('label="Sentence to"', bar)
        self.assertIn('<span>duration</span>', bar)
        # A duration is typed as a number; TimeField speaks clocks, where 0.4 would
        # read as 4 seconds.
        duration = self.editor[self.editor.index('className="kara-wordbar-duration"'):]
        self.assertIn('type="number"', duration[:400], "a duration is a number, not a clock: TimeField reads 0.4 as four seconds")
        for selector in (".kara-wordbar-wave{", ".kara-wordbar-times{", ".kara-newline{"):
            self.assertIn(selector, self.css)


class WordBarMarkupTest(unittest.TestCase):
    """The two things the geometry cannot prove on its own."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.core = CORE_TS.read_text(encoding="utf-8")
        cls.editor = EDITOR_TSX.read_text(encoding="utf-8")

    def test_the_bar_lives_below_the_strip_and_outside_the_zoom_scroller(self) -> None:
        scroll = self.editor.index('className="kara-strip-scroll"')
        close = self.editor.index("</div>\n          </div>", scroll)
        bar = self.editor.index('className="kara-wordbar"')
        self.assertGreater(bar, close, "the word bar must be under the whole strip")
        self.assertLess(bar, self.editor.index('className="strip-legend"'))
        self.assertIn("karaokeWordBar(current, windowEnd)", self.editor)

    def test_the_bar_does_not_wear_the_strips_classes(self) -> None:
        # `.kara-block` is a timing row's chrome (34 px, clipped, flex). The stage
        # inherited it once and the lyric line got cut off; the bar must not.
        block = self.editor[self.editor.index('className="kara-wordbar"'):]
        head = block[:block.index(" : null}")]
        self.assertLess(len(head), 6200, "the bar's own markup, not the whole file")
        self.assertNotIn("kara-block", head)
        self.assertNotIn("kara-slot", head)
        self.assertNotIn("kara-lane", head)

    def test_both_surfaces_edit_the_same_words(self) -> None:
        # No second copy of the timings, so nothing can drift between the bar and
        # the strip: the drag writes `words[i].t` through the same helper the
        # chips and the block drag use.
        self.assertIn("withWords(makeContinuous(", self.editor[self.editor.index("const moveBar"):
                                                        self.editor.index("const endBar")])
        for name in ("karaokeWordBar", "karaokeWordBarTime", "karaokeWordBarCut", "karaokeWordBarFraction"):
            self.assertIn(f"export function {name}", self.core)
        head = self.editor[:self.editor.index("} from './karaokeCore'")]
        for name in ("karaokeWordBar", "karaokeWordBarCut", "karaokeWordBarCut", "karaokeWordBarFraction",
                     "karaokeWordBarHold", "karaokeWordBarTime", "karaokeEvenWordTimes"):
            self.assertIn(name, head, f"the popup must import {name}, not reimplement it")

    def test_the_sentence_options_are_on_the_bar_not_reimplemented_elsewhere(self) -> None:
        # Even words and "no colouring" are per-sentence decisions, so they live in
        # the bar's head (which *is* the selected sentence) and write the two word
        # fields the render reads — nothing else stores a copy of them.
        bar = self.editor[self.editor.index('className="kara-wordbar"'):][:3600]
        self.assertIn("evenWordsInLine", bar)
        self.assertIn("togglePlainLine", bar)
        self.assertIn("beginBarEnd", bar)
        self.assertIn("words[w.i] = k === n - 1 ? { ...words[w.i], t: starts[k], hold: slot }", self.editor)
        self.assertIn("plain: 1", self.editor)

    def test_the_bar_has_its_own_style(self) -> None:
        css = (REPO / "src" / "styles.css").read_text(encoding="utf-8")
        for selector in (".kara-wordbar{", ".kara-wordbar-track{", ".kara-wordbar-word{", ".kara-wordbar-cut{"):
            self.assertIn(selector, css)
        self.assertFalse(re.search(r"\.kara-block[^{]*\{[^}]*kara-wordbar", css))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
