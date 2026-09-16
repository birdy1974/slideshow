"""Values that are pasted into an FFmpeg ``-vf`` / ``-filter_complex`` string.

FFmpeg tokenises a filter graph **twice**, and both passes use
``av_get_token()`` (``libavutil/avstring.c``)::

    1. libavfilter/graphparser.c — one filter at a time:
           name = av_get_token(buf, "=,;[")
           opts = av_get_token(buf, "[],;")     # after the '='
    2. ff_filter_opt_parse()   — the options string of that filter:
           key   = av_get_token(buf, "=")
           value = av_get_token(buf, ":")

``av_get_token`` stops at any character of its terminator set, treats a single
quote as "copy everything verbatim until the next quote" and collapses ``\\x``
to ``x`` — but only *outside* quotes; a quoted section cannot contain a quote
at all, escaped or not.

Two consequences that bite when expressions or user text are interpolated:

* An unquoted ``,`` in a value ends the filter, and the rest of the expression
  is parsed as filter names: ``fontsize=(31*(0.5+…clip((t-0)/5,0,1)))``
  becomes ``No such filter: '0)'``.
* A quote inside a quoted value closes it early, which shifts the quoting of
  everything after it — including the commas of the *next* options. A title
  like ``Oma's Verjaardag 2006`` escaped as ``'Oma\'s Verjaardag 2006'``
  therefore breaks the graph in exactly that way, and silently drops the
  apostrophe when it does not.

Because every pass eats one backslash, a value must be escaped for the pass
that *follows* it, not for the one it is handed to: escape for pass 2 first,
then embed that result in a quoted section for pass 1.
"""

from __future__ import annotations

#: Characters that end a filter (pass 1) or an option (pass 2), plus the two
#: that ``av_get_token`` itself interprets.
_SPECIALS = ",:'[];\\"


def quote_filter_value(value: str) -> str:
    """Return ``value`` ready to be pasted after ``option=`` in a filter graph.

    Plain values (numbers, identifiers, simple expressions) come back
    unchanged, so existing graphs stay byte-for-byte identical — only values
    that would otherwise be split get wrapped/escaped.
    """
    text = str(value)
    if not any(ch in text for ch in _SPECIALS):
        return text

    # Pass 2 — the option list is split on ':', so colons, backslashes and
    # quotes have to survive one more unescaping pass.
    escaped = text.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")

    # Pass 1 — wrap the whole value in single quotes so ',', ';', '[' and ']'
    # stay literal. A literal quote is written the shell way: close the quoted
    # section, emit an escaped quote, reopen it ("'\''").
    return "'" + escaped.replace("'", "'\\''") + "'"
