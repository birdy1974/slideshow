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
        self.assertLess(len(head), 4000, "the bar's own markup, not the whole file")
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
        self.assertIn("karaokeWordBar, karaokeWordBarCut, karaokeWordBarFraction, karaokeWordBarTime", self.editor)

    def test_the_bar_has_its_own_style(self) -> None:
        css = (REPO / "src" / "styles.css").read_text(encoding="utf-8")
        for selector in (".kara-wordbar{", ".kara-wordbar-track{", ".kara-wordbar-word{", ".kara-wordbar-cut{"):
            self.assertIn(selector, css)
        self.assertFalse(re.search(r"\.kara-block[^{]*\{[^}]*kara-wordbar", css))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
