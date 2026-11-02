"""The karaoke strip's zoom, and the box the sung words are painted in.

The timing strip lays every lane out as a percent of the clip, so zooming is only
ever drawing that mapping wider inside a scroller — which is why almost nothing
about the strip needs a test. Three things *are* measured in screen pixels rather
than in time and so have to follow the zoom: how dense the ruler is, how wide the
floor under a one-word sentence has to be to stay grabbable, and the clamp that
keeps the slider honest. Those are pure functions in ``src/karaokeCore.ts``, run
through ``karaoke_strip.mjs`` the way ``test_karaoke_twin.py`` runs the plan maths.

The second half pins two things the popup cannot check by itself, both learned the
hard way from "the top and bottom of the letters are cut off".

*Sung words are revealed, never masked.* A gradient clipped to the glyphs
(``background-clip:text``) makes the word's box the mask, and the ``clip-path``
that replaced it clips to that same box: any face whose ink is taller than its own
line box — one whose metrics could not be read, a fallback substitution, a
condensed display cut — loses its cap and its descender to the clip either way.
``KaraokeStage`` measures the box from the font file (the win metrics
``app.text_motion`` hands libass, so the preview moves towards the render rather
than inventing a second guess) *and* grows the clip's rectangle vertically, so the
only thing the reveal can ever cut is on the x axis it is meant to cut.

*The lyric line must not wear the strip's class.* The stage used ``.kara-block``,
which the timing strip also uses, further down the same stylesheet, for a 34 px
tall ``display:flex`` row with ``overflow:hidden``, a background, a border and 9 px
of padding. Same specificity, later rule: the stage lost its line to a strip row's
chrome — a plate that does not grow with the font, letters outside it, words
ellipsised instead of wrapped. That, not a mask, is what "the background of the
karaoke text must fit the height of the text size" was. The stage now has a class
of its own, and ``StageBoxCollisionTest`` keeps the two apart.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "karaoke_strip.mjs"
CORE_TS = REPO / "src" / "karaokeCore.ts"
STAGE_TSX = REPO / "src" / "KaraokeStage.tsx"
STYLES = REPO / "src" / "styles.css"

import sys

sys.path.insert(0, str(REPO / "backend"))
from app import font_registry as FR  # noqa: E402

# What the 700 px box in karaokeCore.ts assumes, so the spacing checks below are
# about the ruler the strip actually draws rather than about a magic number.
BOX_PX = 700
LADDER = [0.01, 0.02, 0.04, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300]
SPANS = [1, 4, 10, 20, 45, 60, 100, 150, 240, 300, 900, 1800, 3600]
ZOOMS = [1, 1.5, 2, 3, 4, 6, 8, 10, 12]


def _node() -> str | None:
    return shutil.which("node")


def _run(cases: list[dict]) -> list[dict]:
    node = _node()
    if not node:
        raise AssertionError("node missing")
    proc = subprocess.run(
        [node, "--experimental-strip-types", str(DRIVER)],
        input=json.dumps({"cases": cases}),
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"karaoke_strip.mjs failed: {proc.stderr[-2000:]}")
    return json.loads(proc.stdout)


class KaraokeFollowScrollTest(unittest.TestCase):
    """The strip scrolls; the playhead stands under the centre of the box."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _node():
            raise unittest.SkipTest("node is not installed, so the strip's geometry helpers cannot be run")
        cases = []
        for span, zoom in [(20, 2), (20, 12), (1800, 12)]:
            width = BOX_PX * zoom
            for frac in (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0):
                cases.append({"span": span, "zoom": zoom, "x": width * frac, "box": BOX_PX})
        cases += [
            {"span": 20, "zoom": 2, "x": 400, "box": 0},        # a box that is not there yet
            {"span": 20, "zoom": 2, "x": -5, "box": 700},       # before the clip
            {"span": 20, "zoom": 2, "x": "nan", "box": 700},
        ]
        cls.rows = _run(cases)

    def test_the_playhead_is_centred_when_there_is_film_to_scroll(self) -> None:
        for row in self.rows:
            # `JSON.stringify` writes null for a NaN, which is exactly what the
            # hand-written-project-file case is meant to look like.
            x, box, follow = float(row["x"] or 0), float(row["box"] or 0), row["follow"]
            self.assertGreaterEqual(follow, 0, "a scroller never scrolls backwards off its own left edge")
            self.assertLessEqual(follow, max(x, 0), "the strip may not scroll past its own playhead")
            if box <= 0:
                self.assertEqual(follow, 0, "no box, no centre to aim at")
                continue
            if x - box / 2 > 0:
                self.assertAlmostEqual(follow + box / 2, x, places=6, msg=f"{row['span']}s x{row['zoom']}: the centre line must stand on the playhead, not beside it")
            elif x > 0:
                self.assertEqual(follow, 0, f"{row['span']}s x{row['zoom']}: near the clip's start the line walks from the left edge, it cannot be centred")

    def test_a_box_of_nothing_and_a_time_before_zero_both_scroll_nowhere(self) -> None:
        self.assertEqual(self.rows[-1]["follow"], 0, "a NaN playhead is not a reason to move")
        self.assertEqual(self.rows[-3]["follow"], 0, "no box, no centring")
        self.assertEqual(self.rows[-2]["follow"], 0, "the clip's IN point is at the left edge, not off it")


class KaraokeStripZoomTest(unittest.TestCase):
    """The clamp, the ruler's density and the block's floor, as the strip sees them."""

    @classmethod
    def setUpClass(cls) -> None:
        if not _node():
            raise unittest.SkipTest("node is not installed, so the strip's geometry helpers cannot be run")
        cases = [{"span": span, "zoom": zoom} for span in SPANS for zoom in ZOOMS]
        cases += [
            {"span": 30, "zoom": -3}, {"span": 30, "zoom": 0.2}, {"span": 30, "zoom": 99},
            {"span": 30, "zoom": 1.23456}, {"span": 0, "zoom": 1}, {"span": 30, "zoom": "0/0"},
        ]
        cls.rows = _run(cases)
        cls.by_key = {(row["span"], row["zoom"]): row for row in cls.rows}

    def test_zoom_clamps_to_the_screens_it_can_span(self) -> None:
        row = self.by_key[(20.0, 1.0)]
        self.assertEqual((row["min"], row["max"]), (1, 12), "1 = the clip already fits the box; 12 = twelve screens")
        clamped = {r["requested"]: r["zoom"] for r in self.rows if r["span"] == 30.0 and r["requested"] not in ZOOMS}
        self.assertEqual(clamped[-3], 1.0, "nothing is narrower than the whole clip")
        self.assertEqual(clamped[0.2], 1.0, "below fit the strip would be a shrunken lie")
        self.assertEqual(clamped[99], 12.0, "the ceiling is 1200 %, and past it the ruler has no rung")
        self.assertEqual(clamped[1.23456], 1.235, "three decimals, or the slider and the ruler disagree")
        self.assertEqual(clamped[None], 1.0, "a NaN from a hand-written project file is just 'fit'")

    def test_ruler_step_is_a_ladder_rung_and_moves_monotonically(self) -> None:
        for row in self.rows:
            if row["span"] == 0:
                continue
            self.assertIn(row["step"], LADDER, f"off-ladder step at {row['span']}s x{row['zoom']}")
        for span in SPANS:
            steps = [self.by_key[(span, z)]["step"] for z in ZOOMS if (span, z) in self.by_key]
            self.assertTrue(all(a >= b for a, b in zip(steps, steps[1:])), f"{span}s: zooming in must not coarsen the ruler ({steps})")
        for zoom in ZOOMS:
            steps = [self.by_key[(s, zoom)]["step"] for s in SPANS if (s, zoom) in self.by_key]
            self.assertTrue(all(a <= b for a, b in zip(steps, steps[1:])), f"x{zoom}: a longer clip must not get a finer ruler ({steps})")

    def test_ticks_stay_legible_and_cover_the_clip(self) -> None:
        for (span, zoom), row in self.by_key.items():
            if span == 0 or not row["ticks"]:
                continue
            count = len(row["ticks"])
            apart = BOX_PX * zoom / max(1, count - 1)
            self.assertGreaterEqual(apart, 40, f"{span}s x{zoom}: ticks {apart:.0f} px apart will not read")
            self.assertLessEqual(apart, 140, f"{span}s x{zoom}: {apart:.0f} px of empty ruler between ticks")
            self.assertEqual(row["ticks"][0], 0, "the strip starts at the clip's IN point")
            self.assertGreaterEqual(row["ticks"][-1], span - row["step"], "the ruler must reach the end of the clip")
            self.assertLessEqual(row["ticks"][-1], span + 0.001, "no tick past the clip")
            self.assertLessEqual(count, 140, f"{count} labels is a DOM bill for a ruler")
            self.assertEqual(row["ticks"], sorted(set(row["ticks"])), "ticks must be unique and in order")

    def test_labels_say_the_time_they_stand_on(self) -> None:
        for row in self.rows:
            for value, label in zip(row["ticks"], row["labels"]):
                parsed = float(label)
                if row["step"] >= 1:
                    self.assertLessEqual(abs(parsed - value), 0.5, f"{label!r} for {value}")
                else:
                    self.assertAlmostEqual(parsed, value, places=3, msg=f"{label!r} for {value} at {row['step']} s")
                self.assertFalse(label.endswith("."), f"{label!r} keeps a dangling point")
                self.assertFalse(label.endswith(".00"), f"{label!r} wastes room on zeros")

    def test_zooming_in_reaches_one_frame(self) -> None:
        # The point of the whole feature: at the top of the range a short clip's
        # ruler is finer than one frame of the render, so a syllable can be
        # landed on by eye instead of by guessing at a 0.3 px-per-ms strip.
        row = self.by_key[(4.0, 8.0)]
        self.assertLessEqual(row["step"], 1 / 25 + 0.001, "a 4 s clip at 8x should tick at frame width")
        # A strip is only so wide: at 8x a 30-minute clip gives ~5600 px, so the
        # ruler cannot promise frames any more than a map can promise a postcode.
        wide = self.by_key[(1800.0, 8.0)]
        self.assertLess(wide["step"], 60, "a 30-minute clip at 8x should still be finer than a minute")
        self.assertLess(wide["step"], self.by_key[(1800.0, 1.0)]["step"], "and finer than the same clip fitted to the box")

    def test_the_block_floor_is_a_screen_measurement(self) -> None:
        # A sentence block must stay ~6 px grabbable at every zoom, which means its
        # floor is 0.8 % of the *box*, divided by the zoom — not 0.8 % of the strip.
        self.assertAlmostEqual(self.by_key[(20.0, 1.0)]["floor"], 0.8, places=4)
        self.assertAlmostEqual(self.by_key[(20.0, 4.0)]["floor"], 0.2, places=4)
        self.assertAlmostEqual(self.by_key[(20.0, 8.0)]["floor"], 0.1, places=4)


class SungWordBoxTest(unittest.TestCase):
    """The preview must not clip what the render draws whole."""

    def test_the_stage_measures_the_box_from_the_font_file(self) -> None:
        stage = STAGE_TSX.read_text(encoding="utf-8")
        self.assertIn("fontMetrics(", stage, "the sung words must be measured from the face, not assumed")
        self.assertRegex(stage.replace("\n", " "), r"boxEm\s*=\s*Math\.max\([^)]*winAscent\s*\+\s*metrics\.winDescent",
                         "the painted box must be the face's own ascent plus descent")
        self.assertRegex(stage.replace("\n", " "), r"lineHeight:\s*boxEm", "the block must lay out on that box")
        self.assertNotIn("lineHeight: 1.28", stage, "a constant line box is what cut the letters off")

    def test_the_word_span_keeps_its_slack(self) -> None:
        css = STYLES.read_text(encoding="utf-8")
        rule = [part for part in css.split("}") if ".kara-word{" in part]
        self.assertEqual(len(rule), 1, "exactly one .kara-word rule, or the padding belongs somewhere unclear")
        body = rule[0][rule[0].index(".kara-word{"):]
        self.assertIn("position:relative", body, "the sung copy is placed against the word's own box")
        self.assertIn("overflow:visible", body, "nothing may clip the word's box, however it is sized")
        self.assertNotIn("padding:", body,
                         "padding would raise the absolutely placed overlay off the letters it colours")
        self.assertNotIn("margin:", body, "…and the compensating negative margin is that mistake twice")
        self.assertNotIn("background-clip", body, "a gradient clipped to the text is what cut the letters off")

    def test_the_sweep_is_a_clip_on_x_and_nothing_else(self) -> None:
        stage = STAGE_TSX.read_text(encoding="utf-8")
        self.assertRegex(stage, r"clipPath: `inset\(-100% ",
                         "the sung half is cut on one vertical line, and nothing else")
        self.assertIn("-100%", stage,
                      "a clip-path clips to the element's box: the vertical insets have to be grown "
                      "or a face taller than its own line box loses its cap and its descender anyway")
        self.assertNotIn("WebkitTextFillColor", stage, "transparent text needs a mask to be painted at all; a second copy does not")
        self.assertNotIn("backgroundClip", stage, "same: the mask was the bug")

    def test_no_bundled_face_is_given_a_box_shorter_than_its_own(self) -> None:
        # karaokeCore.ts floors the box at 1.02 em. That is only safe while no face
        # this app ships wants a shorter line than that — check the registry, since a
        # rebuilt fonts.json is exactly how one would turn up unnoticed.
        families = (FR.font_registry() or {}).get("families") or []
        self.assertGreater(len(families), 0, "no font registry to check")
        for entry in families:
            for style, metrics in (entry.get("metrics") or {}).items():
                if not isinstance(metrics, dict):
                    continue
                upm = metrics.get("upm") or 0
                if not upm:
                    continue
                ratio = (metrics.get("winAscent", 0) + metrics.get("winDescent", 0)) / upm
                self.assertGreater(ratio, 0.8, f"{entry.get('family')} {style}: a {ratio:.2f} em line box is a bad font build")
                self.assertLess(ratio, 2.4, f"{entry.get('family')} {style}: {ratio:.2f} em would blow the block out of the frame")

    def test_the_render_side_of_the_same_promise(self) -> None:
        # The MP4 has no background box for karaoke text, which is why the render
        # never clipped: if that ever changes, the box has to be sized from the
        # font as well or the two halves disagree in the opposite direction.
        kara = (REPO / "backend" / "app" / "karaoke.py").read_text(encoding="utf-8")
        self.assertNotIn("BorderStyle: 3", kara, "a karaoke box in the render needs the same measurement")
        self.assertNotIn("\\bord0\\shad0", kara)

    def test_the_zoom_is_a_view_not_a_slide_field(self) -> None:
        # The zoom must not leak into KaraokeData: the render reads that, and a view
        # preference saved into a slide is a change to the project the MP4 ignores.
        core = CORE_TS.read_text(encoding="utf-8")
        block = core[core.index("export interface KaraokeData"):]
        block = block[:block.index("\n}")]
        self.assertNotIn("zoom", block, "the strip's zoom belongs to the editor, not to the data")


class StageBoxCollisionTest(unittest.TestCase):
    """The letters were being cut by a *borrowed* box, not by a mask.

    `KaraokeStage` laid its lyric line out in an element whose class was also used,
    further down the same stylesheet, by the timing strip's rows — 34 px tall,
    `display:flex`, `overflow:hidden`, with a background, a border and 9 px of
    padding. Equal specificity, later rule, so the stage lost its text to a strip
    row's chrome: a plate behind the words that did not grow with the font, and
    letters whose tops and bottoms fell outside 34 px. The MP4 wraps a line in no
    box at all, so the stage may not either — hence a class of its own.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.stage = (REPO / "src" / "KaraokeStage.tsx").read_text(encoding="utf-8")
        cls.css = (REPO / "src" / "styles.css").read_text(encoding="utf-8")

    def test_the_stage_does_not_wear_the_strips_class(self) -> None:
        used = re.findall(r'className=\{?["`\x27]([^"`\x27]+)', self.stage)
        joined = " ".join(used)
        self.assertNotIn("kara-block", joined,
                         "the strip's row class carries a 34px clipped flex box; the lyric line must not")
        self.assertIn("kara-lyric", joined)

    def test_exactly_one_rule_owns_kara_block_and_it_is_the_strips(self) -> None:
        blocks = re.findall(r"\.kara-block\{[^}]*\}", self.css)
        self.assertEqual(len(blocks), 1, "a second rule with the same name silently edits the first")
        self.assertIn("height:34px", blocks[0])
        self.assertIn("overflow:hidden", blocks[0])

    def test_the_lyric_rule_clips_nothing_and_sizes_nothing(self) -> None:
        lyric = re.findall(r"\.kara-lyric\{[^}]*\}", self.css)
        self.assertEqual(len(lyric), 1, "the stage's line box is declared once")
        body = lyric[0]
        self.assertIn("overflow:visible", body)
        for forbidden in ("height:", "background:", "border:", "display:flex", "padding:0 9px"):
            self.assertNotIn(forbidden, body, f"a lyric plate has no {forbidden} in the render")

    def test_the_sung_copy_is_placed_on_the_same_line_box(self) -> None:
        """Both copies are laid out on one box, so the reveal can colour the letters
        it is drawn over rather than the empty air a few pixels above them."""
        sung = re.findall(r"\.kara-word-sung\{[^}]*\}", self.css)
        self.assertEqual(len(sung), 1, "the overlay copy is declared once")
        self.assertIn("top:0", sung[0])
        self.assertIn("left:0", sung[0])
        self.assertIn("position:absolute", sung[0])
        word = re.findall(r"\.kara-word\{[^}]*\}", self.css)[0]
        for shift in ("padding:", "margin:", "top:", "translate"):
            self.assertNotIn(shift, word, f"a {shift} on the word offsets the overlay from the letters")

    def test_the_words_are_separated_by_a_real_space(self) -> None:
        """The gap between words has to be text, not layout.

        `tokenize` matches `\\S+`, so a token never carries the space that followed it.
        While the lyric line was the strip's flex row, that row's `gap` was spacing the
        words; the row became a text line and the words ran together. A margin on
        `.kara-word` would look the same and be wrong twice — it does not scale with
        the font and it is not a break opportunity — while the render (`app/karaoke.py`)
        writes a literal `" "` between the `\\kf` runs. So: a text node between the
        spans, never inside one, because the box a `clip-path` reveals is the word's.
        """
        emit = re.search(r"\{shown\.map[\s\S]*?\n {12}\}\)", self.stage)
        assert emit, "the word loop is where the separation has to live"
        body = emit.group(0)
        self.assertIn("index > 0 ? ' ' : null", body,
                      "a space before every word but the first of the line")
        self.assertNotIn("gap:", body, "a flex gap is a substitute for the space, not the space")
        self.assertIn(r"token.nl ? '\n' : null", body,
                      "a break the render writes as \\N has to show up here as well")
        self.assertIn("<Fragment key={w.i}>", body, "the space is a sibling of the word, not its child")

    def test_a_word_never_ellipsises_itself(self) -> None:
        word = [b for b in re.findall(r"\.kara-word\{[^}]*\}", self.css)]
        self.assertEqual(len(word), 1)
        self.assertIn("overflow:visible", word[0])
        self.assertIn("text-overflow:clip", word[0])
        self.assertNotIn("background-clip", word[0])
        self.assertNotIn("white-space:nowrap", word[0], "a sung line wraps; it does not shorten")


if __name__ == "__main__":
    unittest.main()
