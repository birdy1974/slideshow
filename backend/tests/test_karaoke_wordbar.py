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


class ThreeLineZoomTest(unittest.TestCase):
    """The zoom preset is arithmetic on the plan, so it is measured, not eyeballed."""

    def test_three_lines_is_the_span_of_the_neighbours_and_the_box_over_it(self) -> None:
        plan = {"sentences": [{"start": 1.0, "end": 4.0}, {"start": 4.0, "end": 7.0}, {"start": 7.0, "end": 9.5},
                              {"start": 9.5, "end": 12.0}]}
        cases = [
            {"span": 20.0, "lines": 3, "plan": plan, "at": 1, "back": 1, "forward": 1},
            {"span": 20.0, "lines": 3, "plan": plan, "at": 0, "back": 1, "forward": 1},
            {"span": 20.0, "lines": 3, "plan": plan, "at": 3, "back": 1, "forward": 1},
            {"span": 20.0, "lines": 3, "plan": {"sentences": []}, "at": 0, "back": 1, "forward": 1},
        ]
        got = _run(cases)
        self.assertEqual({"from": 1.0, "to": 9.5, "lines": 3}, got[0]["fit"]["span"])
        # `clampKaraokeZoom` snaps to the slider's own step, so the expected value
        # is what the control can actually hold, to three decimals.
        self.assertEqual(round(20 / 8.5, 3), got[0]["fit"]["zoom"], "the box shows 8.5 s of a 20 s clip")
        # At either end there is no neighbour: it fits what exists, and says so.
        self.assertEqual({"from": 1.0, "to": 7.0, "lines": 2}, got[1]["fit"]["span"])
        self.assertEqual(round(20 / 6.0, 3), got[1]["fit"]["zoom"])
        self.assertEqual({"from": 7.0, "to": 12.0, "lines": 2}, got[2]["fit"]["span"])
        self.assertEqual(round(20 / 5.0, 3), got[2]["fit"]["zoom"])
        self.assertIsNone(got[3]["fit"]["span"], "no sentences, nothing to centre on")

    def test_the_zoom_stays_inside_the_range_the_slider_offers(self) -> None:
        plan = {"sentences": [{"start": 10.0, "end": 10.05}, {"start": 10.05, "end": 10.1}]}
        got = _run([{"span": 3600.0, "lines": 3, "plan": plan, "at": 0, "back": 1, "forward": 1}])[0]
        self.assertEqual(got["fit"]["max"], got["fit"]["zoom"], "a window smaller than a frame still cannot ask for more zoom than the slider has")
        self.assertGreater(got["fit"]["zoom"], 1)


class SourceShapeTest(unittest.TestCase):
    """Tests that read the two files instead of running them — the popup's shape is
    CSS and JSX, and there is no browser here to ask."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.editor = (REPO / "src" / "KaraokeEditor.tsx").read_text(encoding="utf-8")
        cls.css = (REPO / "src" / "styles.css").read_text(encoding="utf-8")


class SentenceBreakIsAFlagTest(SourceShapeTest):
    """Why the "too many one-word sentences" report happened, in one place.

    `tokenize` treats every hard break in the caption as a sentence boundary, and
    `New sentence` used to write a `\\n` into `item.text` to split a line. One
    keystroke therefore re-wrapped the rendered movie *and* minted a sentence out of
    whatever single word happened to sit beside the caret. A boundary is now a `br`
    flag on a word — a display wrap the renderer still honours, but the pane, the
    rail and the strip all read the same flags, so they cannot disagree.
    """

    def test_the_break_is_a_flag_and_the_text_is_never_touched(self) -> None:
        body = self.editor[self.editor.index("const addSentenceAtCaret = () => {"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("setBreak(at, 1)", body)
        self.assertIn("plan.sentences.findIndex(l => l.to === at)", body, "and the rail follows the new row")
        self.assertNotIn("setDraftText", body, "a sentence break must not edit the caption text")
        self.assertNotIn("\\n", body, "no newline is written to the text from here")

    def test_merging_is_the_same_flag_the_other_way(self) -> None:
        body = self.editor[self.editor.index("const glueToPrevious = (index: number) => {"):]
        body = body[:body.index("\n  }\n")]
        self.assertIn("br: -1", body, "`br: -1` also overrides a hard break, so a merged row stays merged")
        self.assertNotIn("setDraftText", body)
        self.assertNotIn("replace(/\\\\n/g", body, "the fix is not to normalise the text afterwards")
        # And it does not claim a merge the cap will undo: `maxWords` splits a long
        # run by itself, so `br: -1` can be applied and the row can still come back.
        self.assertIn("Number(data.maxWords)", body)
        self.assertIn("kind: 'warn'", body, "the notice must name the setting that re-splits the line")

    def test_the_flags_survive_into_the_plan_both_ways(self) -> None:
        # The same four words, four ways, through the real `planOf`. This is the whole
        # claim of ask 8: the pane, the rail and the strip read one list of flags, so
        # the sentence you read is the sentence that is numbered and timed.
        text = "alpha beta gamma delta"
        # The flag lives on the word a line *ends* with: `br: 1` means "cut after me",
        # `br: -1` means "never cut after me", which is why the rail's × is just the
        # second value written over the first.
        rows = _run([{"breaks": {"text": text, "words": [{"t": 0.0}, {"t": 0.6}, {"t": 1.2}, {"t": 1.8}]}},
                     {"breaks": {"text": text, "words": [{"t": 0.0}, {"t": 0.6, "br": 1}, {"t": 1.2}, {"t": 1.8}]}},
                     {"breaks": {"text": text, "words": [{"t": 0.0}, {"t": 0.6, "br": -1}, {"t": 1.2}, {"t": 1.8}]}},
                     {"breaks": {"text": "alpha beta\ngamma delta", "words": [{"t": 0.0}, {"t": 0.6, "br": -1}, {"t": 1.2}, {"t": 1.8}]}}])
        self.assertEqual([[0, 3]], rows[0]["breaks"], "untimed text is one sentence")
        self.assertEqual([[0, 1], [2, 3]], rows[1]["breaks"], "`br: 1` splits it after word 1")
        self.assertEqual([[0, 3]], rows[2]["breaks"], "`br: -1` in the same place glues it back")
        self.assertEqual([[0, 3]], rows[3]["breaks"], "and that beats a hard break in the text")
        self.assertEqual([[0, 1], [2, 3]], _run([{"breaks": {"text": "alpha beta\ngamma delta", "words": [{"t": 0.0}, {"t": 0.6}, {"t": 1.2}, {"t": 1.8}]}}]
                                                )[0]["breaks"],
                         "while a project saved with real newlines still splits as it always did")


class PaneLayoutTest(SourceShapeTest):
    """Asks 2, 3, 6, 7 in the two files that decide them."""

    def test_the_split_and_merge_controls_are_back_on_the_main_popup(self) -> None:
        """Ask 2: they had been moved behind a Settings click.

        The sentence you are timing is the sentence whose controls you want, so the
        row lives directly under the timelines, in the same popup — not one click away.
        """
        row = self.editor[self.editor.index('<div className="kara-line-actions">'):]
        row = row[:row.index("{notice ?")]
        for control in ("splitAtPlayhead", "dropBreak", "startLineHere", "of ${plan.sentences.length}"):
            self.assertIn(control, row, f"{control} belongs under the strip, not behind a Settings click")
        # And it must be the main popup's own DOM, not a field of the settings layer.
        settings = self.editor[self.editor.index("const settingsFields = () => <>"):]
        settings = settings[:settings.index("\n</>")]
        for control in ("splitAtPlayhead", "dropBreak", "Selected sentence"):
            self.assertNotIn(control, settings, "the settings popup must not keep a copy of these")
        self.assertIn("evenWordsEverywhere", settings, "the bulk action stays in settings, where its effect is described")
        # Inside the popup body, before the aside: full width under the strip rather
        # than squeezed into the right-hand column beside the preview.
        self.assertLess(self.editor.index('<div className="kara-line-actions">'), self.editor.index('<aside className="kara-side">'))
        self.assertGreater(self.editor.index('<div className="kara-line-actions">'), self.editor.index('className="kara-wordbar"'))

    def test_the_lanes_span_the_popup_not_the_preview(self) -> None:
        body = self.css[self.css.index(".karaoke-body{"):]
        body = body[:body.index("@media")]
        self.assertIn(".kara-left{display:contents}", body, "the lanes must be grid children to span the popup")
        self.assertIn(".kara-strip-scroll{grid-column:1/-1", body)
        self.assertIn(".kara-wordbar{grid-column:1/-1", body)
        self.assertIn(".kara-line-actions{grid-column:1/-1;grid-row:6", self.css,
                      "the controls row that came back from Settings is full width too")
        self.assertEqual(0, body.count(".kara-left{display:flex"),
                         "a later `.kara-left{display:flex}` silently undid `display:contents` — the whole bug")

    def test_a_row_merges_into_the_row_before_it(self) -> None:
        """Ask 6: a × on the right of a numbered sentence glues it to the row before."""
        rail = self.editor[self.editor.index('<div className="kara-lines"'):]
        rail = rail[:rail.index("No sentences yet")]
        self.assertIn('className="kara-line-merge"', rail)
        self.assertIn('role="button" tabIndex={0}', rail,
                      "the row cannot be a <button>: the × in it would be a button inside a button")
        self.assertIn("onKeyDown={event => {", rail, "and a div has to be reachable from the keyboard itself")
        self.assertIn("i > 0", rail, "row 1 has no row before it to merge into")
        self.assertIn("event.stopPropagation()", rail, "the × is inside the row's own select button")
        self.assertIn("glueToPrevious(i)", rail)
        self.assertIn(".kara-line-merge{", self.css)

    def test_the_rail_scrolls_under_the_wheel(self) -> None:
        self.assertIn("addEventListener('wheel', onWheel, { passive: false })", self.editor)
        self.assertIn("box.scrollTop += by", self.editor)
        wheel = self.editor[self.editor.index("// The rail gets its own wheel."):][:1200]
        self.assertGreaterEqual(wheel.count("event.preventDefault()"), 1,
                                "without it the scroll bubbles and the popup jitters instead of the list moving")
        rows = _run([{"wheel": {"deltaY": 40, "deltaX": 0, "deltaMode": 0, "scrollHeight": 400, "clientHeight": 160}},
                     {"wheel": {"deltaY": 40, "deltaX": 200, "deltaMode": 0, "scrollHeight": 400, "clientHeight": 160}},
                     {"wheel": {"deltaY": 40, "deltaX": 0, "deltaMode": 1, "scrollHeight": 400, "clientHeight": 160}},
                     {"wheel": {"deltaY": 40, "deltaX": 0, "deltaMode": 0, "scrollHeight": 158, "clientHeight": 160}},
                     {"wheel": {"deltaY": 9000, "deltaX": 0, "deltaMode": 0, "scrollHeight": 4000, "clientHeight": 160}},
                     {"wheel": {"deltaY": -9000, "deltaX": 0, "deltaMode": 0, "scrollHeight": 4000, "clientHeight": 160}}])
        self.assertEqual(40, rows[0]["wheel"], "a pixel delta is pixels")
        self.assertEqual(0, rows[1]["wheel"], "a sideways swipe belongs to the strip beside it")
        self.assertEqual(96, rows[2]["wheel"], "Firefox reports lines, and 16 px a line is what makes it feel right")
        self.assertEqual(0, rows[3]["wheel"], "nothing to scroll means the page keeps the event")
        self.assertEqual(96, rows[4]["wheel"], "one notch cannot throw the list to the bottom")
        self.assertEqual(-96, rows[5]["wheel"], "nor to the top")

    def test_every_word_of_every_sentence_can_be_spaced_evenly(self) -> None:
        # Ask 5: the setting itself, and the contract that a line's window is not moved.
        settings = self.editor[self.editor.index("const settingsFields = () => <>"):]
        settings = settings[:settings.index("\n</>")]
        self.assertIn("Space all words evenly", settings, "ask 5: the setting, in the settings popup")
        self.assertIn("onClick={evenWordsEverywhere}", settings)
        self.assertIn("onClick={spaceEvenly}", settings, "the per-line action is still there next to it")
        rows = _run([{"even": {"from": 0.0, "to": 2.6, "count": 4}},
                     {"even": {"from": 10.0, "to": 10.4, "count": 1}},
                     {"even": {"from": 4.0, "to": 4.05, "count": 3}}])
        self.assertEqual([0.0, 0.65, 1.3, 1.95], rows[0]["even"]["starts"])
        self.assertEqual(0.65, rows[0]["even"]["slot"], "the last word's hold is the same slot, or the reveal outlives the line")
        self.assertEqual([10.0], rows[1]["even"]["starts"])
        self.assertEqual(0.4, rows[1]["even"]["slot"], "a single word takes the whole window")
        self.assertEqual([4.0, 4.02, 4.04], rows[2]["even"]["starts"], "a window narrower than a word still divides")
        self.assertGreaterEqual(rows[2]["even"]["slot"], 0.02)


class WaveformBucketsTest(unittest.TestCase):
    """The bucket maths, without FFmpeg.

    `test_the_probe_returns_both_levels_for_real_audio` below needs the binary, which
    this sandbox does not have — so the part of the lane that is *arithmetic* (one
    peak and one RMS per column, both divided by the file's loudest column) is tested
    here instead, against a sample array built by hand. Measured numbers, because the
    whole claim of ask 4 is that the shape now says something about the audio.
    """

    @classmethod
    def setUpClass(cls) -> None:
        import sys
        sys.path.insert(0, str(REPO / "backend"))
        from app import renderer as RD  # noqa: PLC0415
        cls.buckets = staticmethod(RD._waveform_buckets)

    @staticmethod
    def _square(amplitude: float, samples: int) -> list[int]:
        import array
        peak = int(amplitude * 32767)
        return array.array("h", [peak if i % 2 == 0 else -peak for i in range(samples)])

    def test_a_quiet_recording_is_read_as_loudly_as_a_loud_one(self) -> None:
        loud = self._square(0.5, 800)
        quiet = self._square(0.005, 800)
        zero = [0.0] * 4 + [1.0] * 4
        for samples in (loud, quiet):
            peaks, rms, top = self.buckets([0] * 800 + list(samples), 8)
            self.assertEqual(zero, peaks, "the silent half draws nothing, the loud half fills the lane")
            self.assertEqual(zero, rms, "a steady tone has no crest gap, and both bands agree")
            self.assertLess(top, 0.02 if samples is quiet else 0.9, "`top` is the raw level, kept out of the shape")

    def test_a_click_and_a_syllable_are_the_only_two_things_this_distinguishes(self) -> None:
        import array
        click = array.array("h", [0] * 799 + [30000])
        tone = self._square(0.5, 800)
        peaks, rms, _top = self.buckets(list(click) + list(tone), 8)
        self.assertEqual(1.0, peaks[3], "one sample at full scale is a peak, and the envelope shows it")
        self.assertAlmostEqual(0.0707, rms[3], places=4,
                               msg="but it is not a syllable: the RMS band stays flat, which is the whole read")
        # The tone is 16383 and the click is 30000, so normalised against the file's
        # own loudest bucket the *whole* rest of the movie reads 0.5461. That is the
        # cost of a single pop, and the reason the lane defaults to dB: see
        # `test_a_pop_flattens_a_linear_lane_and_not_a_log_one`.
        self.assertEqual([0.5461] * 4, peaks[4:])
        self.assertEqual([0.5461] * 4, rms[4:], "a steady tone has no crest gap at all")

    def test_silence_and_an_empty_file_answer_instead_of_raising(self) -> None:
        import array
        peaks, rms, top = self.buckets(array.array("h", [0] * 1000), 8)
        self.assertEqual([0.0] * 8, peaks, "all silence is a flat lane, not a divide-by-zero")
        self.assertEqual([0.0] * 8, rms)
        self.assertEqual(1.0, top, "with nothing to divide by, the scale stays at unity")
        self.assertEqual(([], [], 1.0), self.buckets(array.array("h", []), 8))

    def test_the_bucket_count_is_clamped_to_the_dom(self) -> None:
        import array
        samples = array.array("h", [100] * 400_000)
        for asked, expected in ((1, 8), (800, 800), (99_999, 4000)):
            peaks, rms, _top = self.buckets(samples, asked)
            self.assertEqual(expected, len(peaks), f"{asked} buckets")
            self.assertEqual(len(peaks), len(rms), "one RMS value per peak value, or the bands lie")


class AudioLaneScaleTest(unittest.TestCase):
    """The lane is measured, and the ffmpeg data behind it is checked end to end.

    `karaokeWaveLevel` is the whole difference between "a flat line" and "I can see
    the words": the peaks the backend returns are already divided by the file's own
    loudest bucket, so a linear view is that same ratio in pixels and quiet speech
    stays near the centre line. The dB view spends a fixed range (48 dB) below the
    file's own peak, which is where speech actually lives.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not _node():
            raise unittest.SkipTest("node is not installed, so the lane's scale cannot be run")

    def test_the_dB_scale_lifts_quiet_speech_without_inventing_it(self) -> None:
        values = [0.0, 0.02, 0.5, 1.0, 0.5, 0.02, 0.0]
        got = _run([{"wave": {"values": values, "reference": 1.0}}])[0]["wave"]
        self.assertEqual([0.0, 0.02, 0.5, 1.0, 0.5, 0.02, 0.0], got["linear"])
        # 0.02 of the peak is -34 dB, so 1 - 33.98/48 of the way up. Measured, not guessed.
        self.assertEqual([0.0, 0.2921, 0.8746, 1.0, 0.8746, 0.2921, 0.0], got["dB"])
        self.assertEqual(0.0, got["dB"][0], "silence is silence on either scale")
        self.assertEqual(1.0, got["dB"][3], "and so is the peak: dB expands, it does not lift")
        self.assertTrue(all(b > a for a, b in zip(got["linear"], got["dB"]) if 0 < a < 1),
                        "between silence and the peak, every dB level is taller than its linear twin")

    def test_a_quiet_file_is_normalised_once_and_the_gain_cap_holds_it_back(self) -> None:
        # peaks are already relative to the file's own max, so `reference` is 1.0; a
        # raw level of 0.02 with a 24 dB cap is lifted to ~0.32, not to 1.0 — a lone
        # pop must not turn the noise floor into a wall.
        # The backend divides both series by the file's own loudest bucket, so the
        # lane's 0 dB means "as loud as the loudest thing in this movie" and a quiet
        # recording still fills it. Room noise at a twentieth of the peak stays small.
        got = _run([{"wave": {"values": [0.02, 0.001], "reference": 0.02}}])[0]["wave"]
        self.assertEqual([1.0, 0.05], got["linear"], "the file's own peak fills the lane, noise does not")
        self.assertEqual([1.0, 0.4579], got["dB"], "26 dB down is well short of the top, on either scale")
        self.assertLess(got["linear"][1], 0.2, "the floor is not amplified into a wall")


    def test_a_pop_flattens_a_linear_lane_and_not_a_log_one(self) -> None:
        """Ask 4's payoff, in one pair of numbers.

        A full-scale click is the file's own peak, so every spoken bucket is
        normalised against *it*: measured on the click-plus-tone file from
        `WaveformBucketsTest`, the voice is 0.5461 of the lane's half-height in linear
        amplitude and 0.8905 of it in dB. dB is not a magic separator — it *compresses*
        the distance between two loud things (0.4754 of a lane apart in amplitude,
        0.3699 in dB, both asserted below so nobody sells the other story) — what it
        buys is that the quiet material a boundary is timed against stays in the
        picture instead of being squashed to a ripple near the centre line.
        """
        got = _run([{"wave": {"values": [0.5461, 0.0707, 1.0], "reference": 1.0}}])[0]["wave"]
        self.assertEqual([0.5461, 0.0707, 1.0], got["linear"])
        self.assertEqual([0.8905, 0.5206, 1.0], got["dB"])
        self.assertGreater(got["dB"][0], got["linear"][0], "the voice keeps 89 % of the lane, not half of it")
        self.assertGreater(got["dB"][1], got["linear"][1], "and the floor is lifted into view, not hidden")
        self.assertLess(got["dB"][0] - got["dB"][1], got["linear"][0] - got["linear"][1],
                        "log compresses the gap between two loud things; said out loud so it is not sold as a benefit")

    def test_the_lane_draws_an_envelope_an_inner_band_and_a_centre_line(self) -> None:
        peaks = [0.0, 0.4, 1.0, 0.4, 0.0]
        rms = [0.0, 0.1, 0.6, 0.1, 0.0]
        got = _run([{"wave": {"values": peaks, "rms": rms, "reference": 1.0, "log": True}},
                    {"wave": {"values": [0.2], "reference": 1.0}}])[0]
        paths = got["wave"]["paths"]
        self.assertTrue(paths["envelope"].startswith("M0,"))
        self.assertTrue(paths["inner"].startswith("M0,"))
        self.assertEqual("M0,50 L999,50", paths["centre"])
        self.assertIsNone(_run([{"wave": {"values": [0.2], "reference": 1.0}}])[0]["wave"]["paths"],
                          "one bucket is not a waveform, and the lane says so instead of drawing a spike")

    def test_the_probe_returns_both_levels_for_real_audio(self) -> None:
        import shutil as _shutil
        import subprocess
        import tempfile
        ffmpeg = _shutil.which("ffmpeg") or "/usr/bin/ffmpeg"
        if not _shutil.which("ffmpeg"):
            raise unittest.SkipTest("ffmpeg is not installed")
        import sys
        sys.path.insert(0, str(REPO / "backend"))
        from app import renderer as RD  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "half.wav"
            # A second of silence, then a second of tone: the lane must show the gap.
            subprocess.run([ffmpeg, "-y", "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=8000:duration=1",
                            "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono:duration=1",
                            "-filter_complex", "[1:a][0:a]concat=n=2:v=0:a=1[a]", "-map", "[a]", str(wav)],
                           capture_output=True, check=True, timeout=120)
            peaks, rms, duration = RD._waveform_peaks_pcm(ffmpeg, wav, buckets=20)
        self.assertGreater(len(peaks), 8)
        self.assertEqual(len(peaks), len(rms), "the RMS band needs one value per peak bucket")
        self.assertEqual(1.0, max(peaks), "normalised against the file's own loudest bucket")
        half = len(peaks) // 2
        self.assertEqual(0.0, sum(peaks[:half]), "the silent half of the file draws nothing")
        self.assertGreater(sum(rms[half:]), 0.0)
        self.assertTrue(all(r <= p for r, p in zip(rms, peaks)), "RMS is never above the peak it sits inside")
        self.assertAlmostEqual(2.0, duration, places=1)

    def test_the_endpoint_shape_the_lane_reads(self) -> None:
        """`Renderer.waveform` is what the fetch resolves to, so its keys are the API.

        The editor reads `peaks`, `rms` and `reference` and nothing else; `reference`
        is 1.0 precisely because both series are already divided by the file's peak,
        and a lane that divided by 0.9 as well would quietly over-draw everything.
        """
        import shutil as _shutil
        import subprocess
        import tempfile
        if not _shutil.which("ffmpeg"):
            self.skipTest("ffmpeg is not installed")
        import sys
        sys.path.insert(0, str(REPO / "backend"))
        from app.database import Database  # noqa: PLC0415
        from app.config import Settings  # noqa: PLC0415
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            wav = base / "tone.wav"
            subprocess.run([_shutil.which("ffmpeg"), "-y", "-f", "lavfi", "-i",
                            "sine=frequency=440:sample_rate=8000:duration=1.5", str(wav)],
                           capture_output=True, check=True, timeout=120)
            from app.renderer import Renderer
            data = Renderer(Database(base / "test.db"), Settings()).waveform(wav, buckets=24)
        self.assertEqual({"peaks", "rms", "reference", "duration", "start"}, set(data))
        self.assertEqual(1.0, data["reference"], "the lane's 0 dB is this file's own peak")
        self.assertEqual(len(data["peaks"]), len(data["rms"]))
        self.assertAlmostEqual(1.5, data["duration"], places=1)
        self.assertEqual(0.0, data["start"])
        self.assertEqual(1.0, max(data["peaks"]))


class RulerAtMaxZoomTest(unittest.TestCase):
    """A zoom ceiling is only real if the ruler stays readable at it."""

    def test_the_label_budget_scales_with_the_zoom_not_the_clip(self) -> None:
        got = _run([{"zoomAt": 1}, {"zoomAt": 12}, {"zoomAt": 60}, {"zoomAt": 999}])
        self.assertEqual(139, got[0]["budget"], "one screen of strip, one screen of labels")
        self.assertEqual(1668, got[1]["budget"], "139 per screen, so twelve screens get twelve screens of labels")
        self.assertEqual(6000, got[2]["budget"], "the cap: 139 x 60 would be 8340 <b> tags")
        self.assertEqual(6000, got[3]["budget"], "a cap, or an hour-long clip at full stretch asks for a million labels")


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
        # A `br` flag, never an edit to the caption: a newline in `item.text` is a
        # hard line break in the movie *and* a sentence to `tokenize`, which is how
        # one stray keystroke used to add a one-word sentence everywhere at once.
        self.assertIn("setBreak(at, 1)", body)
        for text_edit in ("setDraftText(next)", "apply({ ...data, words", "retarget("):
            self.assertNotIn(text_edit, body, f"a sentence break must not touch the caption ({text_edit})")
        self.assertIn("data.words[at]?.br === 1", body, "an already-broken word says so instead of stacking breaks")

    def test_the_words_pane_numbers_the_sentences_and_selects_one(self) -> None:
        rail = self.editor[self.editor.index('className="kara-lines"'):]
        rail = rail[:rail.index("</div>\n            {draftText !==")]
        self.assertIn("<b>{i + 1}</b>", rail)
        self.assertIn("selectLine(i)", rail)
        self.assertIn("kara-line${i === selected ? ' active'", rail)
        self.assertIn(".kara-line.active{", self.css)

    def test_the_preview_is_small_and_the_timelines_own_the_full_width(self) -> None:
        # `display:contents` is what lets the strip and the bar span the popup while
        # the video stays in the left column — and the right column is only the words
        # pane now, because everything else moved into the settings layer.
        self.assertIn(".kara-left{display:contents}", self.css)
        self.assertIn(".kara-strip-scroll{grid-column:1/-1;grid-row:3}", self.css)
        self.assertIn(".kara-wordbar{grid-column:1/-1;grid-row:4}", self.css)
        self.assertIn(".strip-legend{grid-column:1/-1;grid-row:5}", self.css)
        self.assertIn(".kara-side{grid-column:2;grid-row:1/span 2", self.css)
        self.assertIn("max-width:min(100%,520px)", self.css)

    def test_the_settings_are_a_layer_over_the_timing_not_a_column_beside_it(self) -> None:
        # One copy of the fields, rendered in a second popup: they are a function
        # returning JSX, so they still close over `apply`/`patchLook` and there is
        # no second editor state that could disagree with the timing.
        self.assertIn("const [showSettings, setShowSettings] = useState(false)", self.editor)
        self.assertIn("const settingsFields = () => <>", self.editor)
        self.assertIn("{settingsFields()}", self.editor)
        self.assertIn('onClick={() => setShowSettings(v => !v)}', self.editor)
        head = self.editor[:self.editor.index('className="karaoke-body"')]
        self.assertIn("Settings size={13}", head, "the button belongs by the movie's name, above the preview")
        # The right column of the main popup holds exactly one field: the words pane.
        aside = self.editor[self.editor.index('<aside className="kara-side">'):self.editor.index("</aside>")]
        self.assertEqual(1, aside.count('className="kara-field"'), aside[:200])

    def test_the_audio_profile_is_always_asked_for_and_names_why_when_absent(self) -> None:
        # The lane used to be gated on the `wave` surface, so in the other three
        # modes it sat empty and looked broken. Peaks are fetched once, and the
        # outcome is stated for each reason it can be missing.
        self.assertNotIn("data.editor !== 'wave' || peaks", self.editor)
        self.assertIn("if (!analyzable || peaks) return", self.editor)
        self.assertIn("/api/media/waveform", self.editor, "the levels come from ffmpeg, not the browser's decoder")
        self.assertIn(f"buckets=${{WAVE_BUCKETS}}", self.editor)
        for state in ("'reading'", "'ready'", "'empty'", "'failed'"):
            self.assertIn(state, self.editor)
        lane = self.editor[self.editor.index('className="kara-audio"'):][:4200]
        for note in ("no movie to read audio from", "the audio probe did not answer", "no audio track", "reading the movie"):
            self.assertIn(note, lane)
        self.assertIn(".kara-audio{position:relative;height:52px;", self.css)
        self.assertIn(".kara-audio path.rms{fill:#a3cd63", self.css, "the RMS band has to read as the lighter shape inside the darker one")
        # The dB / linear choice, remembered like the zoom.
        self.assertIn("slideshow.karaoke.waveScale", self.editor)
        self.assertIn("setWaveLog(true)", self.editor)
        self.assertIn(".kara-audio-scale", self.css, "levels need a scale: a peak envelope is max(|x|), not RMS")

    def test_the_audio_lane_shares_the_strips_axis_and_marks_both_timelines(self) -> None:
        lane = self.editor[self.editor.index('className="kara-audio"'):]
        lane = lane[:lane.index("kara-audio-note") + 900]
        self.assertIn("karaokeWavePaths(peaks.values, peaks.rms, peaks.level || 1", self.editor)
        self.assertIn('<path className="env" d={waveLane.envelope}/>', lane)
        self.assertIn('<path className="rms" d={waveLane.inner}/>', lane)
        self.assertIn('<path className="zero" d={waveLane.centre}/>', lane)
        self.assertIn("plan.sentences.map((line, ai) => <i key={`al-${ai}`}", lane)   # sentence starts
        self.assertIn("current ? current.words.map(w => <u", lane)                    # the selected line's words
        self.assertEqual(0, lane.count("stripWidth"), "it lives inside the strip, so the zoom is inherited, not repeated")

    def test_the_three_sentence_zoom_and_the_two_way_selection(self) -> None:
        self.assertIn("karaokeNeighbourSpan(plan, selected, 1, 1)", self.editor)
        self.assertIn("zoomTo(karaokeZoomToFit(span, fit.to - fit.from))", self.editor)
        self.assertIn('box.scrollLeft = karaokeFollowScroll(', self.editor)
        # the words pane scrolls to whatever the playhead or a block click picked
        self.assertIn("railRef.current?.querySelector('.kara-line.active')?.scrollIntoView", self.editor)
        self.assertIn('<div className="kara-lines" ref={railRef}>', self.editor)
        # and a sentence clicked (not dragged) in the strip goes to that moment
        self.assertIn("if (drag.kind === 'block' && !drag.moved) {", self.editor)

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
