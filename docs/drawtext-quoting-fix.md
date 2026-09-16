# `No such filter: '0)'` — the render dies on the first title slide

Date: 2026-09-16 · Status: **implemented** (shared `quote_filter_value()` + drawtext
text escaping + regression tests)

## Symptom

A 225-slide render aborts while encoding slide 1, before any video is produced:

```
=== Slide 1/225 — Title “Oma's Verjaardag 2006” — title 9s segment start ===
[AVFilterGraph @ 0x564ba1af5480] No such filter: '0)'
Error : Filter not found
```

The same message is in `improvements.md` (2026-09-15), where the only mitigation
was logging *which* slide was being encoded.

## Root cause

The filter string the backend builds for a title slide is

```
drawtext=fontfile='/app/fonts/Montserrat-Bold.ttf':text='Oma\'s Verjaardag 2006'
        :fontsize=(31*(0.5+(0.6)*clip((t-0)/5,0,1))):fontcolor=0x050505
        :alpha='if(lt(t,0),0,if(lt(t,0.5),min(1,(t-0)/0.5),…))':x='…':y='…'
        :shadowcolor=black@0.55:shadowx=2:shadowy=2:enable='between(t,0,5)'
```

and it contains **two independent quoting mistakes**:

1. **`fontsize=` is not quoted.** An unquoted `,` ends the filter, so
   `clip((t-0)/5,0,1)` is cut in two and the remainder (`0`, `1)))`, …) is
   parsed as filter names.
2. **The apostrophe in the title is escaped *inside* the quotes.**
   FFmpeg reads a filter string with `av_get_token()`, which copies a quoted
   section verbatim until the *next* quote — a `\` cannot escape a `'` from
   within one. So `text='Oma\'s Verjaardag 2006'` closes the value after
   `Oma\`, and the quote that was meant to open the text now *closes* it.
   Every later quote is shifted by one, which leaves the commas of
   `alpha=` outside any quoted section: the graph splits there, and the first
   stray token is `0)` — the filter FFmpeg complains about. (It also silently
   drops the apostrophe whenever it does not split.)

Reproduced against FFmpeg 7.0.2 by handing the generated option string to a real
binary: it reports `No such filter: '0)'`.

## Why escaping once is not enough

`-filter_complex` / `-vf` strings are tokenised **twice**, both times with
`av_get_token()`:

| pass | code | terminators |
| --- | --- | --- |
| 1 | `filter_parse()` in `libavfilter/graphparser.c` | `,` `;` `[` `]` (a `,` ends the filter) |
| 2 | `ff_filter_opt_parse()` | `:` (ends one option) |

`av_get_token()` collapses `\x` to `x` **outside** quotes and copies everything
verbatim **inside** them. Each pass therefore eats one backslash, so a value has
to be escaped for the pass that *follows* it — not for the one it is handed to.

## The fix

`backend/app/filter_values.py` (new) holds one helper used everywhere:

```python
quote_filter_value("Oma's Verjaardag 2006")  ->  "'Oma\\'\\''s Verjaardag 2006'"
```

* pass 2 first: `\` → `\\`, `'` → `\'`, `:` → `\:`;
* then pass 1: wrap the whole value in single quotes (this is what keeps `,`,
  `;`, `[`, `]` literal) and write a literal quote as `'\''` — close the quoted
  section, escaped quote, reopen.

Values without a special character are returned unchanged, so existing graphs
stay byte-for-byte identical.

`text_effects.py` now routes **every** drawtext option through it (`fontfile`,
`text`, `fontsize`, `alpha`, `x`, `y`, `enable`) and additionally escapes `\`
and `%` for drawtext's own per-frame expansion (`escape_drawtext_text()`), so
`%{…}` in a title can never turn into a metadata expansion. The `ass=` branch,
`renderer.quote_xfade_value()`, `picture_crop._quote_filter_value()` and the
transition-preview label now share the same helper instead of four copies of the
same (slightly wrong) escaping.

## Verification

* `backend/tests/test_filter_values.py` ports `av_get_token()` and both parser
  passes, then round-trips a corpus of nasty values (`Oma's …`, commas, colons,
  semicolons, brackets, backslashes, `%`) and asserts the option receives the
  value unchanged — plus one test that shows an *unquoted* comma does split the
  graph.
* `backend/tests/test_text_effects.py` asks a real FFmpeg to parse the generated
  filter (`skipUnless(shutil.which("ffmpeg"))`, filter name swapped for `crop`
  so it works on builds without drawtext) and fails on
  `No such filter` / `Error parsing` / `No option name near`.
* Checked against FFmpeg 7.0.2: before the fix the string splits into
  `No such filter: '0)'`, after the fix it stays a single filter and the `text`
  option reads back `Oma's Verjaardag 2006`.
