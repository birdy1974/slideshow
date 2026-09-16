"""Quoting of values pasted into an FFmpeg filter graph (app/filter_values.py).

``_get_token``/``parse_filters`` below are a port of ``av_get_token()``
(``libavutil/avstring.c``) plus the two places that call it for a filter
string:

* ``filter_parse()`` in ``libavfilter/graphparser.c`` —
  ``name = av_get_token(buf, "=,;[")``, ``opts = av_get_token(buf, "[],;")``;
* ``ff_filter_opt_parse()`` — ``key = av_get_token(buf, "=")``,
  ``value = av_get_token(buf, ":")``.

It mirrors what a real FFmpeg build does with the same strings (verified
against FFmpeg 7.0.2), so the round-trip tests below fail the moment a value
would be split into extra filters or come back mangled — the
``No such filter: '0)'`` class of bug.
"""
from __future__ import annotations

import unittest

from app.filter_values import quote_filter_value

_WHITESPACE = " \n\t\r"


def _get_token(text: str, start: int, term: str) -> tuple[str, int]:
    """Port of ``av_get_token()``: the token plus the index right after it."""
    out: list[str] = []
    end = 0  # output index after the last quoted/escaped character
    i, n = start, len(text)
    while i < n and text[i] in _WHITESPACE:
        i += 1
    while i < n and text[i] not in term:
        char = text[i]
        i += 1
        if char == "\\" and i < n:
            out.append(text[i])
            i += 1
            end = len(out)
        elif char == "'":
            while i < n and text[i] != "'":
                out.append(text[i])
                i += 1
            if i < n:
                i += 1
                end = len(out)
        else:
            out.append(char)
    stop = len(out) - 1
    while stop >= 0 and stop >= end and out[stop] in _WHITESPACE:
        stop -= 1
    return "".join(out[: stop + 1]), i


def parse_filters(graph: str) -> list[tuple[str, dict[str, str | None]]]:
    """Split a ``-vf``/``-filter_complex`` string the way FFmpeg does."""
    filters: list[tuple[str, dict[str, str | None]]] = []
    i, n = 0, len(graph)
    while i < n:
        if graph[i] == "[":  # link label, not a filter
            close = graph.find("]", i)
            i = n if close < 0 else close + 1
            continue
        if graph[i] in ",;":
            i += 1
            continue
        name, i = _get_token(graph, i, "=,;[")
        options: dict[str, str | None] = {}
        if i < n and graph[i] == "=":
            opts, i = _get_token(graph, i + 1, "[],;")
            j, m = 0, len(opts)
            while j < m:
                key, j = _get_token(opts, j, "=")
                value = None
                if j < m and opts[j] == "=":
                    j += 1
                    value, j = _get_token(opts, j, ":")
                options[key] = value
                if j < m and opts[j] == ":":
                    j += 1
                else:
                    break
        filters.append((name, options))
    return filters


def option_value(graph: str, option: str) -> str | None:
    """The value FFmpeg hands to ``option`` when it parses ``graph``."""
    filters = parse_filters(graph)
    assert len(filters) == 1, f"graph was split into {[name for name, _ in filters]}"
    return filters[0][1].get(option)


class QuoteFilterValueTests(unittest.TestCase):
    def test_plain_values_stay_untouched(self) -> None:
        self.assertEqual("gl_cube", quote_filter_value("gl_cube"))
        self.assertEqual("steps(4)", quote_filter_value("steps(4)"))
        self.assertEqual("48", quote_filter_value("48"))
        self.assertEqual("black@0.55", quote_filter_value("black@0.55"))

    def test_values_that_would_end_the_filter_are_quoted(self) -> None:
        self.assertEqual("'min(iw,ih)'", quote_filter_value("min(iw,ih)"))
        self.assertEqual("'clip(t,0,1)'", quote_filter_value("clip(t,0,1)"))
        self.assertEqual("'gl_cube(persp=0.7,unzoom=0.3)'", quote_filter_value("gl_cube(persp=0.7,unzoom=0.3)"))
        self.assertEqual("'cubic-bezier(0.25,0.1,0.25,1)'", quote_filter_value("cubic-bezier(0.25,0.1,0.25,1)"))
        self.assertEqual("'a;b[c]'", quote_filter_value("a;b[c]"))

    def test_colon_is_escaped_for_the_option_splitter(self) -> None:
        # The option list is split on ':' in a second pass, which eats one
        # backslash — hence '\:' and not ':'.
        self.assertEqual("'Chapter 1\\: The beginning'", quote_filter_value("Chapter 1: The beginning"))

    def test_apostrophe_never_sits_inside_the_quoted_section(self) -> None:
        # Escaping it with a backslash (the old behaviour) closed the value
        # early: everything after it was re-parsed as separate filters.
        self.assertEqual("'Oma\\'\\''s Verjaardag 2006'", quote_filter_value("Oma's Verjaardag 2006"))
        self.assertEqual("'it\\'\\''s'", quote_filter_value("it's"))

    def test_backslash_survives_both_passes(self) -> None:
        self.assertEqual("'a\\\\b'", quote_filter_value("a\\b"))


class RoundTripTests(unittest.TestCase):
    CORPUS = (
        "Oma's Verjaardag 2006",
        "Summer, slowly.",
        "Chapter 1: The beginning",
        "semi;colon [brackets], all: of them",
        "a\\b",
        "it's a 50% thing",
        "min(iw,ih)",
        "clip((t-0)/5,0,1)",
        "gl_cube(persp=0.7,unzoom=0.3)",
        "plain",
        "48",
        "",
    )

    def test_the_option_receives_the_value_unchanged(self) -> None:
        for value in self.CORPUS:
            with self.subTest(value=value):
                graph = f"crop=w={quote_filter_value(value)}:h=32"
                self.assertEqual(value, option_value(graph, "w"))
                self.assertEqual("32", option_value(graph, "h"))

    def test_a_whole_drawtext_chain_stays_one_filter(self) -> None:
        text = quote_filter_value("Oma's Verjaardag 2006")
        fontsize = quote_filter_value("(31*(0.5+(0.6)*clip((t-0)/5,0,1)))")
        alpha = quote_filter_value("if(lt(t,0),0,if(lt(t,5),1,0))")
        enable = quote_filter_value("between(t,0,5)")
        graph = (
            "drawtext=fontfile=/fonts"
            f":text={text}"
            f":fontsize={fontsize}"
            ":fontcolor=0x050505"
            f":alpha={alpha}"
            f":enable={enable}"
        )
        self.assertEqual(1, len(parse_filters(graph)))
        self.assertEqual("Oma's Verjaardag 2006", option_value(graph, "text"))
        self.assertEqual("(31*(0.5+(0.6)*clip((t-0)/5,0,1)))", option_value(graph, "fontsize"))
        self.assertEqual("if(lt(t,0),0,if(lt(t,5),1,0))", option_value(graph, "alpha"))

    def test_unquoted_commas_split_the_graph(self) -> None:
        """What the quoting prevents: the rest of the expression becomes filters."""
        names = [name for name, _ in parse_filters("crop=w=min(iw,ih):h=32")]
        self.assertEqual(["crop", "ih):h"], names)


if __name__ == "__main__":
    unittest.main()
