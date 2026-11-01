"""The karaoke popup and the MP4 must agree, word for word.

``src/karaokeCore.ts`` is what the editor draws and ``backend/app/karaoke.py``
is what writes the ``.ass`` the render burns in. Both derive their sentence and
word windows from the same input — the caption text plus one time per word — so
this test generates a pile of timing shapes (coarse passes, holes, mistimed
taps, emoji, forced breaks, a window that cuts the text off) and requires the
two implementations to produce the *same numbers*, at the same sampled instants,
for the same frames.

It is the karaoke-specific sibling of ``test_text_motion_twin.py``; where that
one compares the 154 text effects, this one compares the clock the words ride
on. Skipped when Node is unavailable, like its neighbour.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).resolve().parent / "karaoke_twin.mjs"

import sys

sys.path.insert(0, str(REPO / "backend"))
from app import karaoke as K  # noqa: E402


def _node() -> str | None:
    return shutil.which("node")


def _times_for(plan: dict) -> list[float]:
    """Sample every boundary: a sentence edge and a word edge is exactly where
    a disagreement between the engines would show up."""
    out = {0.0}
    for sentence in plan["sentences"]:
        out.add(round(sentence["start"], 3))
        out.add(round(max(0.0, sentence["start"] - 0.05), 3))
        out.add(round(sentence["end"], 3))
        out.add(round(sentence["end"] + 0.05, 3))
        for word in sentence["words"]:
            out.add(round(word["t0"], 3))
            out.add(round(max(0.0, word["t0"] - 0.03), 3))
            out.add(round((word["t0"] + word["t1"]) / 2, 3))
            out.add(round(word["t1"], 3))
    return sorted(t for t in out if t >= 0)[:120]


def _cases() -> list[dict]:
    timed = lambda *values: [{"t": v} for v in values]
    return [
        {
            "name": "two sentences, every word timed",
            "text": "The quick brown fox jumps. Then the dog runs home.",
            "data": {"words": timed(1.0, 1.4, 1.9, 2.4, 3.2, 3.8, 4.4, 5.0)},
            "from": 0.0, "to": 8.0,
        },
        {
            "name": "coarse pass: one tap per sentence",
            "text": "First line of the song. Second line follows it. Third one ends.",
            "data": {"words": [{"t": 1.0}, {}, {}, {}, {"t": 4.0}, {}, {}, {}, {"t": 8.0}, {}, {}]},
            "from": 0.0, "to": 12.0,
        },
        {
            "name": "holes in the middle",
            "text": "Alpha beta gamma delta epsilon zeta eta theta.",
            "data": {"words": [{"t": 0.5}, {}, {}, {"t": 2.0}, {}, {}, {"t": 4.5}, {}]},
            "from": 0.0, "to": 6.0,
        },
        {
            "name": "mistimed taps: equal and reversed",
            "text": "One two three four five six.",
            "data": {"words": [{"t": 3.0}, {"t": 1.0}, {"t": 1.0}, {"t": 5.0}, {"t": 2.2}, {"t": 2.2}]},
            "from": 0.0, "to": 9.0,
        },
        {"name": "single word", "text": "Hello.", "data": {"words": [{"t": 1.25}]}, "from": 0.0, "to": 4.0},
        {
            "name": "abbreviations must not cut a sentence",
            "text": "Mr. Smith went home. Mrs. Jones left later.",
            "data": {"words": [{"t": i * 0.6} for i in range(9)]},
            "from": 0.0, "to": 7.0,
        },
        {
            "name": "no punctuation: the cap splits the run",
            "text": " ".join(f"w{i}" for i in range(30)),
            "data": {"words": [{"t": i * 0.4} for i in range(30)], "maxWords": 7},
            "from": 0.0, "to": 13.0,
        },
        {
            "name": "emoji count in UTF-16 units, not code points",
            "text": "Party 🎉🎉 time now. Second line here.",
            "data": {"words": [{"t": i * 0.55} for i in range(8)]},
            "from": 0.0, "to": 6.0,
        },
        {
            "name": "forced break and forced glue",
            "text": "a b c d e f",
            "data": {"words": [{"t": 0.5}, {"t": 1.0, "br": 1}, {"t": 1.5, "br": -1}, {"t": 2.0}, {"t": 2.5, "br": -1}, {"t": 3.0}]},
            "from": 0.0, "to": 5.0,
        },
        {
            "name": "typed line breaks are sentences too",
            "text": "Line one stays.\nLine two comes\nLine three goes",
            "data": {"words": [{"t": i * 0.9} for i in range(6)]},
            "from": 0.0, "to": 8.0,
        },
        {
            "name": "no lead-in, no tail",
            "text": "Tight first line. Tight second line.",
            "data": {"words": [{"t": 1.0}, {"t": 1.5}, {"t": 2.0, "br": 1}, {"t": 2.5}, {"t": 3.0}], "leadIn": 0.0, "tail": 0.0},
            "from": 0.0, "to": 6.0,
        },
        {
            "name": "window cuts the tail of the text off",
            "text": "Early words are fine. These later ones are outside the window.",
            "data": {"words": [{"t": 1.0}, {"t": 1.4}, {"t": 1.9}, {"t": 2.3, "br": 1}, {"t": 6.5}, {"t": 7.0}, {"t": 7.6}, {"t": 8.2}]},
            "from": 0.0, "to": 5.0,
        },
        {
            "name": "long gap between two sentences",
            "text": "Short start. Much later ending here.",
            "data": {"words": [{"t": 0.4}, {"t": 1.0, "br": 1}, {"t": 9.0}, {"t": 9.6}, {"t": 10.2}]},
            "from": 0.0, "to": 12.0,
        },
        {
            "name": "reveal style, same clock",
            "text": "Reveal me word by word here.",
            "data": {"words": [{"t": i * 0.5} for i in range(6)], "style": "reveal"},
            "from": 0.0, "to": 5.0,
        },
        {
            "name": "word-highlight style",
            "text": "Highlight the sung word only.",
            "data": {"words": [{"t": i * 0.45} for i in range(6)], "style": "word"},
            "from": 0.0, "to": 4.0,
        },
        {
            "name": "pop style",
            "text": "One word at a time now.",
            "data": {"words": [{"t": i * 0.6} for i in range(6)], "style": "pop", "leadIn": 0.1, "tail": 0.1},
            "from": 0.0, "to": 5.0,
        },
        # The sung text wearing something other than the caption's clothes. These
        # only exercise the style path — the timing above stays what it is.
        {
            "name": "no style block: the caption's own look",
            "text": "Plain as the caption. Nothing overridden here.",
            "data": {"words": [{"t": i * 0.7} for i in range(7)]},
            "from": 0.0, "to": 6.0,
        },
        {
            "name": "bigger, in a display face",
            "text": "Make it large and serif. Like a title card.",
            "data": {"words": [{"t": i * 0.8} for i in range(8)],
                     "text": {"family": "Playfair Display", "size": 96}},
            "from": 0.0, "to": 7.0,
        },
        {
            "name": "no outline, different unsung colour",
            "text": "Flat lyric video look. No ring around the words.",
            "data": {"words": [{"t": i * 0.65} for i in range(9)],
                     "text": {"outline": False, "colour": "#e8e8e8", "bold": False, "italic": True}},
            "from": 0.0, "to": 7.0,
        },
        {
            "name": "moved down the frame",
            "text": "Lower third karaoke. Sits above the subtitles.",
            "data": {"words": [{"t": i * 0.55} for i in range(7)], "text": {"x": 24.5, "y": 91}},
            "from": 0.0, "to": 6.0,
        },
        {
            "name": "garbage is dropped, never guessed",
            "text": "Hand edited files happen. Numbers and colours get checked.",
            "data": {"words": [{"t": i * 0.5} for i in range(8)],
                     "text": {"size": "40", "colour": "#abc", "bold": 1, "x": 500, "family": "  Times  ",
                              "y": -20, "shadow": 4, "spacing": 3, "align": "left", "junk": True}},
            "from": 0.0, "to": 6.0,
        },
        {
            "name": "out of range clamps, an empty block is no block",
            "text": "Too small and far too big. Both get pulled back.",
            "data": {"words": [{"t": i * 0.9} for i in range(6)],
                     "text": {"size": 9000, "family": "   ", "colour": "", "outline": "yes"}},
            "from": 0.0, "to": 6.0,
        },
    ]


# What a caption looks like before karaoke touches it: `CAPTION_ITEM` in the field
# names the render reads, `CAPTION_PAINT` in the names the preview paints with. The
# two are linked by `TEXT_STYLE` (which override goes into which caption key) plus
# the single rename `karaokePaintStyle` does — `size` on the block, `fontSize` on
# the stage. Deriving the mapping here instead of repeating a literal is the point:
# if an engine renames or drops a field, the two views stop lining up and this file
# says so.
CAPTION_ITEM = {
    "fontFamily": "Montserrat",
    "fontSize": 48,
    "textBold": True,
    "textItalic": False,
    "textUnderline": False,
    "fontColor": "#ffffff",
    "textOutline": True,
    "textX": 50.0,
    "textY": 72.0,
}
STAGE_OF = {"size": "fontSize"}
PAINT_OF = {caption: STAGE_OF.get(key, key) for key, (caption, _kind, _bounds) in K.TEXT_STYLE.items()}
CAPTION_PAINT = {PAINT_OF.get(key, key): value for key, value in CAPTION_ITEM.items()}


def _effective_paint(data: dict) -> dict:
    """The caption's style with ``karaoke.text`` spliced over it, in the stage's
    names — the mirror of ``karaokePaintStyle`` in src/karaokeCore.ts."""
    overrides = {PAINT_OF[key]: value for key, value in K.style_overrides(data).items()}
    return {**CAPTION_PAINT, **overrides}


def _close(test: unittest.TestCase, a: float | None, b: float | None, what: str) -> None:
    if a is None or b is None:
        test.assertEqual(a, b, what)
        return
    test.assertTrue(math.isclose(a, b, abs_tol=1e-6), f"{what}: {a!r} != {b!r}")


class KaraokeTwinTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = _node()
        if not cls.node or not DRIVER.exists():
            raise unittest.SkipTest("needs node and the karaoke twin driver")

    def _js(self, cases: list[dict]) -> list[dict]:
        payload = json.dumps({"cases": cases})
        proc = subprocess.run(
            [self.node, "--experimental-strip-types", str(DRIVER)],
            input=payload, capture_output=True, text=True, timeout=180,
        )
        if proc.returncode != 0:
            self.fail(f"karaoke twin driver failed: {proc.stderr.strip()[:2000]}")
        return json.loads(proc.stdout)

    def test_plan_and_frames_match(self) -> None:
        cases = _cases()
        js_cases = []
        py_plans = []
        for case in cases:
            text = case["text"]
            data = {**K.KARAOKE_DEFAULTS, **(case.get("data") or {})}
            tokens = K.tokenize(text)
            # Both sides must be handed exactly the same word list.
            words = list(data.get("words") or [])
            while len(words) < len(tokens):
                words.append({})
            data = {**data, "words": words[: max(0, len(tokens))]}
            plan = K.plan_of(text, data, case.get("from", 0.0), case.get("to", 0.0), 0.0)
            py_plans.append((case, data, plan))
            js_cases.append({"text": text, "data": data, "from": case.get("from", 0.0), "to": case.get("to", 0.0),
                             "times": _times_for(plan), "caption": CAPTION_PAINT})
        results = self._js(js_cases)
        self.assertEqual(len(results), len(py_plans))
        for (case, data, plan), got in zip(py_plans, results):
            name = case["name"]
            self.assertEqual(len(plan["sentences"]), len(got["plan"]["sentences"]), f"{name}: sentence count")
            _close(self, plan["end"], got["plan"]["end"], f"{name}: plan end")
            for i, (mine, theirs) in enumerate(zip(plan["sentences"], got["plan"]["sentences"])):
                label = f"{name} / sentence {i}"
                self.assertEqual((mine["from"], mine["to"]), (theirs["from"], theirs["to"]), f"{label}: word range")
                self.assertEqual(bool(mine["hard"]), bool(theirs.get("hard")), f"{label}: hard break")
                _close(self, mine["start"], theirs["start"], f"{label}: start")
                _close(self, mine["end"], theirs["end"], f"{label}: end")
                self.assertEqual(len(mine["words"]), len(theirs["words"]), f"{label}: word count")
                for mine_word, their_word in zip(mine["words"], theirs["words"]):
                    self.assertEqual(mine_word["i"], their_word["i"], f"{label}: word index")
                    _close(self, mine_word["t0"], their_word["t0"], f"{label}: word start")
                    _close(self, mine_word["t1"], their_word["t1"], f"{label}: word end")
            # The frames the preview paints, at every boundary of the plan.
            for mine, theirs in zip(_py_frames(case, data, plan), got["frames"]):
                for key in ("s", "k", "done"):
                    self.assertEqual(mine["frame"][key], theirs["frame"][key], f"{name}: frame {key} at {theirs['t']}")
                for key in ("u", "fade"):
                    _close(self, mine["frame"][key], theirs["frame"][key], f"{name}: frame {key} at {theirs['t']}")
                self.assertEqual(mine["states"], theirs["states"], f"{name}: word colours at {theirs['t']}")
                # …and which line the panel and the strip should be on, because the
                # popup promises "this is the sentence you are hearing".
                self.assertEqual(K.follow_sentence(plan, theirs["t"]), theirs["follow"], f"{name}: followed sentence at {theirs['t']}")
            self.assertEqual([list(g) for g in K.sentence_groups(K.tokenize(case["text"]), data["words"], data["maxWords"])],
                             [list(g) for g in got["groups"]], f"{name}: sentence groups")
            # ---- the text style: same validation, same effective look ----
            raw_text = (case.get("data") or {}).get("text")
            self.assertEqual(K.sanitize_text_style(raw_text) or {}, got["style"], f"{name}: style block validation")
            mine_paint, their_paint = _effective_paint(data), got["look"]
            for key in sorted(set(mine_paint) | set(their_paint)):
                mine_value, their_value = mine_paint.get(key), their_paint.get(key)
                if isinstance(mine_value, (int, float)) and isinstance(their_value, (int, float)):
                    _close(self, float(mine_value), float(their_value), f"{name}: style {key}")
                else:
                    self.assertEqual(mine_value, their_value, f"{name}: style {key}")
            # …and the nine the render actually reaches for, spliced into the item:
            # the same values, read out of the caption keys `style_overrides` wrote.
            item = {**CAPTION_ITEM, **K.style_overrides(data)}
            for karaoke_key, (caption_key, _kind, _bounds) in K.TEXT_STYLE.items():
                mine_value, their_value = item[caption_key], their_paint[STAGE_OF.get(karaoke_key, karaoke_key)]
                if isinstance(mine_value, (int, float)) and isinstance(their_value, (int, float)):
                    _close(self, float(mine_value), float(their_value), f"{name}: render {caption_key}")
                else:
                    self.assertEqual(mine_value, their_value, f"{name}: render {caption_key}")
            for mine, theirs in zip(K.auto_time(K.tokenize(case["text"]), [], data["maxWords"], case.get("from", 0.0), case.get("to", 0.0)), got["auto"]):
                _close(self, mine.get("t"), theirs, f"{name}: first-pass time")
            self.assertTrue(got["signed"], f"{name}: signature")

    def test_windows_never_overlap_and_words_finish(self) -> None:
        """The rule the strip depends on: a sentence leaves when the next one
        arrives, but never before its own last word has finished filling."""
        for case in _cases():
            text = case["text"]
            tokens = K.tokenize(text)
            data = {**K.KARAOKE_DEFAULTS, **(case.get("data") or {})}
            words = list(data.get("words") or [])
            while len(words) < len(tokens):
                words.append({})
            data = {**data, "words": words[: len(tokens)]}
            plan = K.plan_of(text, data, case.get("from", 0.0), case.get("to", 0.0), 0.0)
            for prev, cur in zip(plan["sentences"], plan["sentences"][1:]):
                self.assertLessEqual(prev["end"], cur["start"] + 1e-6, case["name"])
            ceiling = case.get("to") or 0.0
            for i, sentence in enumerate(plan["sentences"]):
                # The sweep must be able to finish while the line is on screen —
                # unless the display window ends earlier, which is the user's
                # own cut of the clip and not a bug in the plan.
                nxt_start = plan["sentences"][i + 1]["words"][0]["t0"] if i + 1 < len(plan["sentences"]) else float("inf")
                caps = [sentence["words"][-1]["t1"], nxt_start]
                if ceiling > 0:
                    caps.append(ceiling)
                reachable = min(caps)
                self.assertGreaterEqual(sentence["end"], reachable - 1e-6, f"{case['name']}: sweep cut short")
                self.assertLessEqual(sentence["start"], sentence["end"], f"{case['name']}: window inverted")
                self.assertLessEqual(sentence["words"][0]["t0"] - sentence["start"], data["leadIn"] + 1e-6, f"{case['name']}: lead-in")
                self.assertGreaterEqual(sentence["words"][0]["t0"] - sentence["start"], 0.0, f"{case['name']}: word before its line")
                for mine_word, nxt in zip(sentence["words"], sentence["words"][1:]):
                    # Exactly contiguous: never overlapping, never a gap.
                    self.assertAlmostEqual(mine_word["t1"], nxt["t0"], places=6, msg=f"{case['name']}: word overlap")
                for mine_word in sentence["words"]:
                    self.assertGreaterEqual(mine_word["t1"], mine_word["t0"], f"{case['name']}: negative word")
            starts = [s["start"] for s in plan["sentences"]]
            self.assertEqual(starts, sorted(starts), f"{case['name']}: sentences out of order")

    def test_ass_times_are_the_plans_times(self) -> None:
        """Every Dialogue line's start/end must be the sentence window, rounded
        to centiseconds — or the caption the user timed drifts by a frame."""
        for case in _cases():
            text = case["text"]
            tokens = K.tokenize(text)
            data = {**K.KARAOKE_DEFAULTS, **(case.get("data") or {})}
            words = list(data.get("words") or [])
            while len(words) < len(tokens):
                words.append({})
            data = {**data, "words": words[: len(tokens)]}
            plan = K.plan_of(text, data, case.get("from", 0.0), case.get("to", 0.0), 0.0)
            events = K.build_karaoke_events(
                text, plan, style=data["style"], fill=data["fill"], base_colour="#ffffff",
                cx=960, cy=900, fade_in=0.2, fade_out=0.2,
            )
            if data["style"] == "pop":
                self.assertEqual(len(events), sum(len(s["words"]) for s in plan["sentences"]), case["name"])
                continue
            self.assertEqual(len(events), len(plan["sentences"]), case["name"])
            for event, sentence in zip(events, plan["sentences"]):
                # Dialogue: Layer,Start,End,Style,Name,...
                fields = event[len("Dialogue: "):].split(",")
                self.assertEqual(fields[1], K._ass_time(sentence["start"]), case["name"])
                self.assertEqual(fields[2], K._ass_time(max(sentence["end"], sentence["start"] + 0.02)), case["name"])


def _py_frames(case: dict, data: dict, plan: dict) -> list[dict]:
    out = []
    for t in _times_for(plan):
        frame = K.karaoke_at(plan, t, data.get("style", "sweep"))
        out.append({"t": t, "frame": frame, "states": K.word_states(plan, frame, data.get("style", "sweep"))})
    return out


if __name__ == "__main__":
    unittest.main()
