"""Karaoke timing — the Python twin of ``src/karaokeCore.ts``.

The slide's caption text stays the single source of what is *said*; this module
owns *when*. One entry per word (``{"t": 3.4}``), sentences derived from the
text and the ``br`` flags, and per-character ``\\kf`` durations inside each
word — which is the one thing the ``while``-phase ``karaoke-sweep`` effect
cannot do on its own, because it divides the window evenly over every character
of the whole caption.

Both engines must agree word for word: the same file, ``plan_of()``, produces
the sentence and word windows the browser paints (through ``karaokeCore.ts``)
and the ones the render burns in (through :func:`build_karaoke_events`).
``backend/tests/test_karaoke_twin.py`` runs both over the same cases.

The rules mirrored from the tools people actually use for this (Aegisub,
Subtitle Edit, the karaoke guides): a word is sung until the next one starts, a
sentence shows a beat before its first word and holds a beat after its last,
and a word that was never timed splits the distance between its timed
neighbours by weight instead of being dropped.
"""

from __future__ import annotations

import math
import re
from typing import Any

__all__ = [
    "KARAOKE_DEFAULTS",
    "tokenize",
    "word_weight",
    "auto_break",
    "sentence_groups",
    "auto_time",
    "snap_times",
    "make_continuous",
    "sanitize_karaoke",
    "plan_of",
    "karaoke_at",
    "word_states",
    "build_karaoke_events",
    "has_karaoke",
    "data_for",
    "follow_sentence",
    "shift_times",
    "sanitize_text_style",
    "style_overrides",
]

KARAOKE_DEFAULTS: dict[str, Any] = {
    "editor": "strip",
    "style": "sweep",
    "fill": "#ffd166",
    "leadIn": 0.35,
    "tail": 0.3,
    "snap": 0.25,
    "maxWords": 12,
}

EDITORS = ("strip", "tap", "chips", "wave")

STYLES = ("sweep", "word", "reveal", "pop")

# What a karaoke block may restyle, mapped to the caption key the renderer reads
# (``_geometry`` in backend/app/text_effects.py takes font, size, the weight trio,
# colour, outline and position from those). ``kind`` picks the validation and
# ``bounds`` the clamp; the preview's controls come from ``KARAOKE_TEXT_FIELDS`` in
# src/karaokeCore.ts, which lists the same fields in the same order. Outline
# colour, shadow depth, letter spacing and alignment are *not* here because the
# shared ``Style: FX`` line fixes them for every text on this path — offering them
# for karaoke alone would mean a preview the MP4 contradicts.
TEXT_STYLE: dict[str, tuple[str, str, tuple[float, float] | None]] = {
    "family": ("fontFamily", "text", None),
    "size": ("fontSize", "size", (8.0, 400.0)),
    "bold": ("textBold", "bool", None),
    "italic": ("textItalic", "bool", None),
    "underline": ("textUnderline", "bool", None),
    "colour": ("fontColor", "colour", None),
    "outline": ("textOutline", "bool", None),
    "x": ("textX", "pos", (0.0, 100.0)),
    "y": ("textY", "pos", (0.0, 100.0)),
}

_STRONG = re.compile(r"[.!?…][\"'”’)\]]*$")
_SOFT = re.compile(r"[,;:…][\"'”’)\]]*$")
_LOWER = re.compile(r"^[a-z]")
_DIGIT = re.compile(r"^[0-9]")
_TRAILER = re.compile(r"[\"'”’)\]]+$")
# Upper-case words that end in a full stop without ending a sentence.
ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "no", "vs", "etc",
    "approx", "fig", "inc", "ltd", "govt", "dept", "ave", "blvd", "rd",
}
# `\\S+` in JavaScript and Python both follow Unicode whitespace, so the word
# lists cannot drift apart; the one difference is that JS counts UTF-16 code
# units while Python counts code points, hence `text_length`.
_TOKEN_RE = re.compile(r"\S+")
_NL_RE = re.compile(r"^[ \t]*\r?\n")


# ---------------------------------------------------------------------------
# Small helpers that must match the TypeScript exactly
# ---------------------------------------------------------------------------


def ms_round(value: float) -> float:
    """Milliseconds — the resolution both engines agree on, so a save → load →
    render cycle cannot drift by a frame.

    Deliberately ``floor(x + 0.5)`` and not ``round()``: Python rounds half to
    *even*, JavaScript rounds half *up*, and a word boundary landing exactly on
    half a millisecond would then differ between the preview and the MP4.
    """
    return math.floor(float(value) * 1000 + 0.5) / 1000


def text_length(value: str) -> int:
    """Length in UTF-16 code units, i.e. JavaScript's ``String.length``.

    ``len("😀")`` is 1 in Python and 2 in JS; the character ``\\kf`` step divides
    by that length, so an emoji would shift every later word by a centisecond
    without this.
    """
    return len(str(value).encode("utf-16-le")) // 2


def _num(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


# ---------------------------------------------------------------------------
# Tokenising and sentence grouping
# ---------------------------------------------------------------------------


def tokenize(text: str) -> list[dict[str, Any]]:
    """Words with their char spans; ``nl`` marks a word the source text ends a
    line after (an explicit break the user typed, and a ``\\N`` in the render)."""
    out: list[dict[str, Any]] = []
    for m in _TOKEN_RE.finditer(str(text or "")):
        i0, i1 = m.start(), m.end()
        out.append({"w": m.group(0), "i0": i0, "i1": i1, "nl": bool(_NL_RE.match(str(text or "")[i1:]))})
    return out


def word_weight(word: str) -> float:
    """Weight of a word = its length, so a long word takes longer to sing.
    Deliberately character-based: that is also what the ASS ``\\kf`` step
    divides, so the two cannot disagree about where a word ends."""
    return max(2.0, float(text_length(word)))


def _abbreviation(word: str, nxt: dict[str, Any] | None) -> bool:
    """True when a trailing ``.`` is not a sentence end ("Mr.", "J.", "vol. 3")."""
    bare = _TRAILER.sub("", word)
    if not bare.endswith("."):
        return False
    stem = bare[:-1]
    if not stem:
        return False
    if len(stem) == 1:
        return True
    if nxt and _DIGIT.match(nxt["w"]):
        return True
    return stem.lower() in ABBREVIATIONS


def auto_break(tokens: list[dict[str, Any]], i: int, run_start: int, max_words: float) -> bool:
    """The automatic break after word ``i``."""
    if i < 0 or i >= len(tokens):
        return False
    t = tokens[i]
    if t.get("nl"):
        return True
    if i >= len(tokens) - 1:
        return False
    cap = max(1, round(max_words))
    if i - run_start + 1 >= cap:
        # The cap is a legibility promise, so when it fires the line ends there.
        # *Where* it ends can still be nicer — `sentence_groups` steps back onto
        # a comma one word earlier — but deferring the cut used to run the line
        # on to the next full stop, which is how "4 words per line" produced an
        # eight-word sentence and a one-word orphan.
        return True
    if not _STRONG.search(t["w"]):
        return False
    if _abbreviation(t["w"], tokens[i + 1] if i + 1 < len(tokens) else None):
        return False
    nxt = tokens[i + 1] if i + 1 < len(tokens) else None
    if nxt and _LOWER.match(nxt["w"]) and re.search(r"\.$", _TRAILER.sub("", t["w"])):
        stem = _TRAILER.sub("", t["w"])[:-1]
        if not re.search(r"[!?…]", t["w"]) and len(stem) > 1 and stem.lower() not in ABBREVIATIONS:
            return False
    return True


def sentence_groups(tokens: list[dict[str, Any]], words: list[dict[str, Any]], max_words: float) -> list[tuple[int, int]]:
    """Word-index ranges of the sentences, honouring the forced ``br`` flags."""
    groups: list[tuple[int, int]] = []
    cap = max(1, round(max_words))
    start = 0
    for i in range(len(tokens)):
        forced = (words[i] or {}).get("br") if i < len(words) else None
        if forced == 1:
            cut = True
        elif forced == -1:
            cut = False
        else:
            cut = auto_break(tokens, i, start, max_words)
        if cut or i == len(tokens) - 1:
            end = i
            if forced not in (1, -1) and cut and i - start + 1 >= cap:
                # The cap, not a full stop, is what ended this line: if the word
                # before it closes a clause, cut there instead. One word back is
                # the limit, so the run never grows past the cap and a short
                # line never becomes a one-word orphan.
                prev = i - 1
                if prev > start and _SOFT.search(tokens[prev]["w"]) and not _STRONG.search(tokens[i]["w"]) and not _SOFT.search(tokens[i]["w"]):
                    end = prev
                # …and a handful of words left over is folded back in rather than
                # stranded as its own caption: two even lines beat a stub.
                short = max(2, math.ceil(cap / 4))
                remaining = len(tokens) - (end + 1)
                if 0 < remaining < short:
                    back = min(short - remaining, end + 1 - start - math.ceil(cap / 2))
                    if back > 0:
                        end -= back
            groups.append((start, end))
            start = end + 1
    return groups


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def fill_sentence(
    tokens: list[dict[str, Any]], words: list[dict[str, Any]], frm: int, to: int, t0: float, t1: float
) -> list[dict[str, Any]]:
    """Give the un-timed words of one sentence their share of ``[t0, t1]``.

    Words that already have a time keep it, so a half-timed line only has its
    gaps filled."""
    # `words` may be shorter than the token list (a fresh caption, a project
    # saved before the last sentence was typed): pad it instead of indexing
    # past the end, which is what the JavaScript twin does by construction.
    out = [dict(w or {}) for w in words] + [{} for _ in range(max(0, len(tokens) - len(words)))]
    if to < frm:
        return out
    weights = [word_weight(tokens[i]["w"]) for i in range(frm, to + 1)]
    total = sum(weights) or 1.0
    span = max(0.15, t1 - t0)
    acc = 0.0
    for k, i in enumerate(range(frm, to + 1)):
        if not math.isfinite(_num(out[i].get("t"), float("nan"))):
            out[i] = {**out[i], "t": ms_round(t0 + (acc / total) * span)}
        acc += weights[k]
    return out


def auto_time(
    tokens: list[dict[str, Any]], words: list[dict[str, Any]], max_words: float, t0: float, t1: float
) -> list[dict[str, Any]]:
    """The first pass: sentences weighted by their characters over the window,
    words shared inside each one. Not right — *correctable*, which is the
    point."""
    groups = sentence_groups(tokens, words or [], max_words)
    if not tokens or not groups:
        return [dict(w or {}) for w in words]
    weights = []
    for a, b in groups:
        weights.append(sum(word_weight(tokens[i]["w"]) for i in range(a, b + 1)))
    total = sum(weights) or 1.0
    span = max(0.5, t1 - t0)
    out = [dict(w or {}) for w in words]
    acc = 0.0
    for g, (a, b) in enumerate(groups):
        s0 = t0 + (acc / total) * span
        acc += weights[g]
        s1 = t0 + (acc / total) * span
        out = fill_sentence(tokens, out, a, b, s0, s1)
    return out


def snap_times(times: list[float], onsets: list[float], radius: float, min_gap: float = 0.04) -> list[float]:
    """Pull each time onto the nearest onset inside ``radius``, keeping the
    order and a minimum gap (two words at one instant make one invisible)."""
    if not (radius > 0) or not onsets:
        return list(times)
    ordered = sorted(onsets)
    out: list[float] = []
    for t in times:
        lo, hi = 0, len(ordered) - 1
        best: float | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            d = abs(ordered[mid] - t)
            if best is None or d < abs(best - t):
                best = ordered[mid]
            if ordered[mid] < t:
                lo = mid + 1
            else:
                hi = mid - 1
        v = best if best is not None and abs(best - t) <= radius else t
        if out:
            v = max(v, out[-1] + min_gap)
        out.append(ms_round(v))
    return out


def make_continuous(words: list[dict[str, Any]], min_gap: float = 0.04) -> list[dict[str, Any]]:
    """Keep the sequence increasing without moving anything earlier."""
    out = [dict(w or {}) for w in words]
    prev = -math.inf
    for w in out:
        t = _num(w.get("t"), float("nan"))
        if not math.isfinite(t):
            continue
        w["t"] = ms_round(max(t, prev + min_gap))
        prev = w["t"]
    return out


# ---------------------------------------------------------------------------
# The plan — the only thing either engine needs
# ---------------------------------------------------------------------------


def plan_of(
    text: str, data: dict[str, Any], frm: float = 0.0, to: float = 0.0, duration: float = 0.0
) -> dict[str, Any]:
    """Absolute sentence and word windows. Mirrors ``planOf`` in the TypeScript
    twin, statement for statement."""
    tokens = tokenize(text)
    words = data.get("words") or []
    groups = sentence_groups(tokens, words, _num(data.get("maxWords"), KARAOKE_DEFAULTS["maxWords"]))
    lead_in = _clamp(_num(data.get("leadIn"), KARAOKE_DEFAULTS["leadIn"]), 0.0, 5.0)
    tail = _clamp(_num(data.get("tail"), KARAOKE_DEFAULTS["tail"]), 0.0, 10.0)
    lo = max(0.0, _num(frm, 0.0))
    ceiling = _num(to, 0.0) if _num(to, 0.0) > 0 else _num(duration, 0.0)
    raw: list[float | None] = []
    for i in range(len(tokens)):
        t = _num((words[i] or {}).get("t") if i < len(words) else None, float("nan"))
        raw.append(ms_round(t) if math.isfinite(t) and t >= 0 else None)
    # Fill the gaps: an un-timed word splits the distance between the timed
    # neighbours around it by weight.
    for a, b in groups:
        for i in range(a, b + 1):
            if raw[i] is not None:
                continue
            prev = i - 1
            while prev >= a and raw[prev] is None:
                prev -= 1
            nxt = i + 1
            while nxt <= b and raw[nxt] is None:
                nxt += 1
            va = raw[prev] if prev >= a else None
            vb = raw[nxt] if nxt <= b else None
            own = word_weight(tokens[i]["w"])
            if va is not None and vb is not None and vb > va:
                acc = sum(word_weight(tokens[k]["w"]) for k in range(prev + 1, i))
                span = sum(word_weight(tokens[k]["w"]) for k in range(prev + 1, nxt))
                raw[i] = ms_round(va + ((vb - va) * (acc + own / 2)) / max(span, 1.0))
            elif va is not None:
                raw[i] = ms_round(va + own * 0.045)
            elif vb is not None:
                raw[i] = ms_round(max(lo, vb - own * 0.045))
    timed = [v for v in raw if v is not None]
    if not timed:
        return {"sentences": [], "end": 0.0}
    sentences: list[dict[str, Any]] = []
    end = 0.0
    for g, (a, b) in enumerate(groups):
        start = raw[a]
        if start is None:
            continue
        boundary: float | None = None
        for h in range(g + 1, len(groups)):
            if raw[groups[h][0]] is not None:
                boundary = raw[groups[h][0]]
                break
        if boundary is None:
            boundary = ceiling if ceiling > 0 else ms_round(start + 1.2)
        items: list[dict[str, Any]] = []
        last1 = start
        for i in range(a, b + 1):
            # Never earlier than the previous word ended: a mistimed tap cannot
            # reorder the line, it just stops the previous word there.
            t0 = last1 if raw[i] is None else max(raw[i], last1)
            items.append({"i": i, "t0": t0, "t1": 0.0})
            last1 = t0
        # Each word is sung until the next one starts; the last one owns the
        # rest of the sentence but keeps a breath before the next lands.
        for k, w in enumerate(items):
            nxt = items[k + 1] if k + 1 < len(items) else None
            if nxt is not None:
                # A word is sung *until the next one starts* — that is what
                # makes the sweep land on the next syllable. There is no
                # minimum-length floor here on purpose: pushing a word past its
                # successor would overlap two colours. A word that is shorter
                # than 50 ms is therefore a *timing* problem, and the strip
                # shows it as a too-thin block for the user to widen (the
                # editor's own drags keep MIN_WORD_GAP between starts).
                w["t1"] = ms_round(nxt["t0"])
            else:
                room = max(0.3, min(1.1, boundary - w["t0"]))
                w["t1"] = ms_round(w["t0"] + room)
            last1 = max(last1, w["t1"])
        if last1 > boundary:
            last1 = boundary
        hard = any(tokens[i].get("nl") for i in range(a, b))
        s_start = ms_round(max(lo, start - lead_in))
        s_end = ms_round(min(ceiling, last1 + tail) if ceiling > 0 else last1 + tail)
        if s_end <= s_start:
            s_end = ms_round(s_start + 0.2)
        sentences.append({"from": a, "to": b, "start": s_start, "end": s_end, "hard": hard, "words": items})
        end = max(end, s_end)
    # Two sentences must never be on screen at once: they share one \pos, so an
    # overlap is two lines printed on top of each other. The handoff point is
    # the next line's first word — the incoming line is what the eye is already
    # looking for, and a word's sweep finishing exactly as its line leaves is
    # what makes a *contiguous* caption look seamless. So the previous line is
    # allowed to hold until that word, but never past it: when a user taps two
    # sentences on top of each other, the earlier line is cut short instead of
    # overprinting, which is the visible, honest signal that they need a gap.
    # Word times are never moved here, only the display windows. Mirrored
    # statement for statement in src/karaokeCore.ts.
    for i in range(1, len(sentences)):
        prev, cur = sentences[i - 1], sentences[i]
        if cur["start"] >= prev["end"]:
            continue
        first_word = cur["words"][0]["t0"] if cur["words"] else cur["start"]
        target = min(max(cur["start"], prev["end"]), first_word)
        cur["start"] = ms_round(target)
        prev["end"] = ms_round(target)
        if prev["end"] <= prev["start"]:
            # Nothing left to truncate: the earlier line keeps a blink and the
            # later one starts after it, rather than both drawing at once.
            prev["end"] = ms_round(prev["start"] + 0.2)
            cur["start"] = ms_round(max(cur["start"], prev["end"]))
    return {"sentences": sentences, "end": end if not sentences else ms_round(max(s["end"] for s in sentences))}


def follow_sentence(plan: dict[str, Any], t: float) -> int:
    """Which sentence the panel and the strip should be on while the playhead
    moves: the one showing, the next one during a gap, the last one past the end.
    ``-1`` when there is nothing to follow.

    The twin of ``followSentence`` in ``src/karaokeCore.ts`` — the popup cannot
    select a line the plan does not consider current, so the rule is one line in
    each engine and pinned by the twin test.
    """
    lines = plan.get("sentences") or []
    if not lines:
        return -1
    for index, line in enumerate(lines):
        if t < float(line.get("end") or 0.0):
            return index
    return len(lines) - 1


def karaoke_at(plan: dict[str, Any], t: float, style: str = "sweep") -> dict[str, Any]:
    """The frame the browser paints, recomputed here for the twin test."""
    empty = {"s": -1, "k": -1, "u": 0.0, "done": -1, "fade": 1.0}
    for si, s in enumerate(plan.get("sentences") or []):
        if t < s["start"] or t > s["end"]:
            continue
        lead = (s["words"][0]["t0"] - s["start"]) if s["words"] else 0.2
        fade = _clamp01((t - s["start"]) / lead) if lead > 0.06 else 1.0
        k, u = -1, 0.0
        for wi, w in enumerate(s["words"]):
            if t < w["t0"]:
                break
            k = wi
            u = _clamp01((t - w["t0"]) / (w["t1"] - w["t0"])) if w["t1"] > w["t0"] else 1.0
            if t < w["t1"]:
                break
        done = k if u >= 1 else k - 1
        if style in ("word", "pop", "reveal"):
            u = 1.0 if k >= 0 else 0.0
        return {"s": si, "k": k, "u": u, "done": done, "fade": fade}
    return empty


def word_states(plan: dict[str, Any], frame: dict[str, Any], style: str = "sweep") -> list[str]:
    sentences = plan.get("sentences") or []
    s = frame["s"]
    if s < 0 or s >= len(sentences):
        return []
    out: list[str] = []
    for i in range(len(sentences[s]["words"])):
        if style in ("word", "pop"):
            out.append("now" if i == frame["k"] else "sung" if i < frame["k"] else "next")
        elif style == "reveal":
            out.append("sung" if i <= frame["done"] else "next")
        elif i < frame["k"]:
            out.append("sung")
        elif i > frame["k"]:
            out.append("next")
        else:
            out.append("sung" if frame["u"] >= 1 else "now")
    return out


# ---------------------------------------------------------------------------
# Model plumbing
# ---------------------------------------------------------------------------


def sanitize_karaoke(raw: Any, word_count: int) -> dict[str, Any] | None:
    """Validate a project's karaoke block: unknown keys dropped, numbers
    clamped, so a corrupt field can never reach FFmpeg."""
    if not isinstance(raw, dict):
        return None
    editor = raw.get("editor") if raw.get("editor") in EDITORS else KARAOKE_DEFAULTS["editor"]
    style = raw.get("style") if raw.get("style") in STYLES else KARAOKE_DEFAULTS["style"]
    fill = str(raw.get("fill") or "")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", fill):
        fill = KARAOKE_DEFAULTS["fill"]
    text = sanitize_text_style(raw.get("text"))
    source = raw.get("words") if isinstance(raw.get("words"), list) else []
    n = int(_clamp(round(_num(word_count, len(source))), 0, 5000))
    words: list[dict[str, Any]] = []
    timed = 0
    for i in range(n):
        entry: dict[str, Any] = {}
        src = source[i] if i < len(source) and isinstance(source[i], dict) else None
        if src:
            t = _num(src.get("t"), float("nan"))
            if math.isfinite(t) and 0 <= t < 86400:
                entry["t"] = ms_round(t)
                timed += 1
            if src.get("br") in (1, "1", True):
                entry["br"] = 1
            elif src.get("br") in (-1, "-1"):
                entry["br"] = -1
        words.append(entry)
    if not timed and not any(w.get("br") is not None for w in words):
        return None
    return {
        "editor": editor,
        "style": style,
        "fill": fill,
        "leadIn": _clamp(_num(raw.get("leadIn"), KARAOKE_DEFAULTS["leadIn"]), 0.0, 5.0),
        "tail": _clamp(_num(raw.get("tail"), KARAOKE_DEFAULTS["tail"]), 0.0, 10.0),
        "snap": _clamp(_num(raw.get("snap"), KARAOKE_DEFAULTS["snap"]), 0.0, 2.0),
        "maxWords": int(_clamp(round(_num(raw.get("maxWords"), KARAOKE_DEFAULTS["maxWords"])), 1, 80)),
        # Only present when something is overridden, so a project that never
        # touched the block loads and saves exactly as it always did.
        **({"text": text} if text else {}),
        "words": words,
    }


def sanitize_text_style(raw: Any) -> dict[str, Any] | None:
    """Validate ``karaoke.text``: keep the nine known fields, clamp each to its
    own range, and return ``None`` when nothing is left.

    The stricter-than-the-rest rule here is deliberate: a number must *be* a
    number (``True`` is an ``int`` in Python and ``float("24")`` works, but the
    preview's ``typeof v === "number"`` check rejects both), and a colour must be
    the same ``#rrggbb`` the ``fill`` field already demands. Two engines reading
    one file have to agree on what is garbage, or the preview and the MP4 diverge
    on a hand-edited project. Field for field the twin of
    ``sanitizeKaraokeTextStyle`` in src/karaokeCore.ts.
    """
    if not isinstance(raw, dict):
        return None
    out: dict[str, Any] = {}
    for key, (_caption_key, kind, bounds) in TEXT_STYLE.items():
        value = raw.get(key)
        if kind == "text":
            family = value.strip() if isinstance(value, str) else ""
            if family:
                out[key] = family[:64]
        elif kind == "colour":
            colour = value if isinstance(value, str) else ""
            if re.fullmatch(r"#[0-9a-fA-F]{6}", colour):
                out[key] = colour
        elif kind == "bool":
            if isinstance(value, bool):
                out[key] = value
        else:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if not math.isfinite(float(value)):
                continue
            lo, hi = bounds or (0.0, 100.0)
            clamped = _clamp(float(value), lo, hi)
            out[key] = int(math.floor(clamped + 0.5)) if kind == "size" else clamped
    return out or None


def style_overrides(data: Any) -> dict[str, Any]:
    """``karaoke.text`` in the shape the caption model uses, ready to splice over
    an item: ``{**item, **style_overrides(karaoke)}`` gives the caption's own
    look with the karaoke block's answers on top.

    Only the keys the block actually sets appear, so everything else keeps
    falling through to the caption and then to the project defaults — which is
    the whole point: karaoke inherits until you say otherwise. Sanitized again on
    the way out, so callers may hand it a raw dict from a project file.
    """
    text = sanitize_text_style(data.get("text") if isinstance(data, dict) else None)
    if not text:
        return {}
    out: dict[str, Any] = {}
    for key, value in text.items():
        out[TEXT_STYLE[key][0]] = value
    return out


def shift_times(data: dict[str, Any], delta: float) -> dict[str, Any]:
    """Move every word time by ``delta`` seconds.

    The renderer's ASS timeline is the *normalized segment*, which opens with
    the incoming transition handle (the ``lead_in`` cloned frames), so the
    caption window is shifted there too — see ``Renderer._text_filter``. Karaoke
    times are stored hold-relative like ``textStart``/``textEnd``, so they need
    the same shift to stay glued to the words they belong to.
    """
    if not delta:
        return data
    out = {**data, "words": [({**w, "t": ms_round(w["t"] + delta)} if "t" in w else dict(w)) for w in (data.get("words") or [])]}
    return out


def data_for(item: dict[str, Any] | None, lead_in: float = 0.0) -> dict[str, Any] | None:
    """The item's karaoke block, sanitized against the item's own text (so a
    stale tail of entries from a shortened caption cannot leak into the render)
    and shifted onto the segment's timeline."""
    if not item:
        return None
    text = str(item.get("text") or "")
    n = len(tokenize(text))
    if not n:
        return None
    data = sanitize_karaoke(item.get("karaoke"), n)
    if data is None:
        return None
    return shift_times(data, max(0.0, float(lead_in)))


def degrade_reason(item: dict[str, Any] | None) -> str:
    """Why this item's karaoke block will not reach the screen, "" when it will.

    A caption that loses its sung words loses them *silently*: the MP4 encodes,
    the slide looks finished, and no pixel explains the absence. Every reason
    below is a state a saved project can genuinely be in — text edited after the
    words were tapped, timings never tapped, the caption switched off — so the
    renderer can name the one it hit instead of leaving the user to guess. Kept
    free of FFmpeg so the tests can pin all of them.
    """
    if not isinstance(item, dict):
        return "the slide is missing"
    text = str(item.get("text") or "")
    if not text.strip():
        return "the caption text is empty, and karaoke times the words of that text"
    block = item.get("karaoke")
    if not isinstance(block, dict):
        return "the slide has no karaoke block"
    words = block.get("words")
    if not isinstance(words, list) or not words:
        return "the karaoke block has no word entries"
    if not has_karaoke(item):
        return f"none of its {len(words)} word entries carries a time yet — tap along with the movie or import lyrics"
    if item.get("textEnabled") is False:
        return "the caption is switched off (textEnabled is false), which hides the karaoke with it"
    if data_for(item) is None:
        return "every word time is out of range, so sanitize_karaoke() drops the block as corrupt (0-86399 s are accepted)"
    return ""


def has_karaoke(item: dict[str, Any] | None) -> bool:
    """True when an item carries word times worth rendering as karaoke."""
    if not item:
        return False
    data = item.get("karaoke")
    if not isinstance(data, dict):
        return False
    for w in data.get("words") or []:
        if not isinstance(w, dict):
            continue
        t = _num(w.get("t"), float("nan"))
        if math.isfinite(t) and t >= 0:
            return True
        if w.get("br") is not None:
            return True
    return False


# ---------------------------------------------------------------------------
# ASS output
# ---------------------------------------------------------------------------


def _escape_ass(value: str) -> str:
    return str(value).replace("{", "(").replace("}", ")")


def _ass_time(seconds: float) -> str:
    cs = max(0, int(round(float(seconds) * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _ass_rgb(hex_colour: str) -> str:
    """``&HAABBGGRR`` body with the alpha the caller wants."""
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hex_colour or "").strip())
    hex6 = match.group(1) if match else "ffffff"
    r, g, b = hex6[0:2], hex6[2:4], hex6[4:6]
    return f"{b}{g}{r}"


def _colour(hex_colour: str, alpha: int = 0) -> str:
    return f"&H{alpha:02X}{_ass_rgb(hex_colour)}&".upper()


def _sentence_body(text: str, sentence: dict[str, Any], tokens: list[dict[str, Any]], style: str) -> str:
    """The ``\\kf`` body of one sentence: a tag run per *character*, sized so the
    fill lands on the next word exactly when that word starts.

    Words are joined with a literal space and a line the user broke by hand
    keeps its ``\\N``, so libass cannot re-wrap one half of the caption while
    the other half is being sung (``WrapStyle: 2``, see ``_header`` in
    ``text_effects.py``).
    """
    out: list[str] = []
    for w in sentence["words"]:
        token = tokens[w["i"]]
        word = str(token["w"])
        chars = list(word)
        count = max(1, len(chars))
        dur = max(0.0, float(w["t1"]) - float(w["t0"]))
        if style == "word":
            # One instant flip for the whole word: `\k` instead of `\kf`.
            run = "{\\k" + str(max(1, int(round(dur * 100)))) + "}" + _escape_ass(word)
        else:
            step = max(1, int(round(dur * 100 / count)))
            run = "".join(("{" + "\\kf" + str(step) + "}" + _escape_ass(ch)) if ch.strip() else " " for ch in chars)
        if token.get("nl"):
            run += "\\N"  # the hard break the user typed, as an ASS tag
        out.append(run)
    return " ".join(out)


def _pop_body(word: str, dur: float) -> str:
    step = max(1, int(round(max(0.0, dur) * 100 / max(1, text_length(word)))))
    return "".join(f"{{\\kf{step}}}{_escape_ass(ch)}" if ch != " " else " " for ch in list(word))


def build_karaoke_events(
    text: str,
    plan: dict[str, Any],
    *,
    style: str,
    fill: str,
    base_colour: str,
    cx: float,
    cy: float,
    fade_in: float,
    fade_out: float,
    align_tag: str = "\\an5",
) -> list[str]:
    """One ``Dialogue`` line per sentence (per word for ``pop``).

    libass karaoke interpolates *secondary → primary* colour across a ``\\kf``
    run, so the caption's own colour is the not-yet-sung side and ``fill`` is
    what a word turns into as it is sung: the text never changes hue for words
    the user did not time. ``reveal`` puts the caption colour behind a
    transparent secondary, which is the same sweep seen as a build-up.
    """
    tokens = tokenize(text)
    events: list[str] = []
    fi = max(0, int(round(fade_in * 1000)))
    fo = max(0, int(round(fade_out * 1000)))
    if style == "pop":
        for sentence in plan.get("sentences") or []:
            for w in sentence["words"]:
                token = tokens[w["i"]]
                t0 = float(w["t0"])
                t1 = max(t0 + 0.18, float(w["t1"]))
                tags = (
                    f"{align_tag}\\pos({cx:.0f},{cy:.0f})\\fad({fi},{fo})"
                    f"\\1c{_colour(fill)}\\2c{_colour(base_colour)}"
                    f"\\t(0,{min(220, int((t1 - t0) * 1000))},\\fscx118\\fscy118)"
                    f"\\t({min(220, int((t1 - t0) * 1000))},{min(420, int((t1 - t0) * 1200))},\\fscx100\\fscy100)"
                )
                events.append(
                    f"Dialogue: 0,{_ass_time(t0)},{_ass_time(t1)},FX,,0,0,0,,{{{tags}}}{_pop_body(token['w'], t1 - t0)}"
                )
        return events
    for sentence in plan.get("sentences") or []:
        start, stop = float(sentence["start"]), float(sentence["end"])
        if style == "reveal":
            colours = f"\\1c{_colour(base_colour)}\\2c&HFF{_ass_rgb(base_colour)}&"
        else:
            colours = f"\\1c{_colour(fill)}\\2c{_colour(base_colour)}"
        tags = f"{align_tag}\\pos({cx:.0f},{cy:.0f})\\fad({fi},{fo}){colours}"
        body = _sentence_body(text, sentence, tokens, style)
        events.append(f"Dialogue: 0,{_ass_time(start)},{_ass_time(max(stop, start + 0.02))},FX,,0,0,0,,{{{tags}}}{body}")
    return events
