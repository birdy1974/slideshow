"""The karaoke strip's zoom, and the box the sung words are painted in.

The timing strip lays every lane out as a percent of the clip, so zooming is only
ever drawing that mapping wider inside a scroller — which is why almost nothing
about the strip needs a test. Three things *are* measured in screen pixels rather
than in time and so have to follow the zoom: how dense the ruler is, how wide the
floor under a one-word sentence has to be to stay grabbable, and the clamp that
keeps the slider honest. Those are pure functions in ``src/karaokeCore.ts``, run
through ``karaoke_strip.mjs`` the way ``test_karaoke_twin.py`` runs the plan maths.

The second half pins something the popup cannot check by itself. The sweep colours
a word by painting a gradient *clipped to the glyphs*, which makes the word's own
box the mask: a line box shorter than the face's ascent plus descent shaves the
tops and bottoms off the letters, a couple of pixels at 40 px and a visible bite
at 120. ``KaraokeStage`` therefore measures the box from the font file — the same
win metrics ``app.text_motion`` gives libass — and the assertions below are what
keeps a tidy-up from putting a constant back. They need no Node, so they run even
where the numbers above are skipped.
"""

from __future__ import annotations

import json
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
ZOOMS = [1, 1.5, 2, 3, 4, 6, 8]


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
        self.assertEqual((row["min"], row["max"]), (1, 8), "1 = the clip already fits the box; 8 = eight screens")
        clamped = {r["requested"]: r["zoom"] for r in self.rows if r["span"] == 30.0 and r["requested"] not in ZOOMS}
        self.assertEqual(clamped[-3], 1.0, "nothing is narrower than the whole clip")
        self.assertEqual(clamped[0.2], 1.0, "below fit the strip would be a shrunken lie")
        self.assertEqual(clamped[99], 8.0)
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
        self.assertIn("padding:.04em 0", body, "the box needs the slack that pays for overshoot")
        self.assertIn("margin:-.04em 0", body, "…paid for, or the block moves and the MP4 does not")
        self.assertNotIn("background-clip", body, "a gradient clipped to the text is what cut the letters off")

    def test_the_sweep_is_a_clip_on_x_and_nothing_else(self) -> None:
        stage = STAGE_TSX.read_text(encoding="utf-8")
        self.assertRegex(stage, r"clipPath: `inset\(0 ", "the sung half must be cut on one vertical line")
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


if __name__ == "__main__":
    unittest.main()
