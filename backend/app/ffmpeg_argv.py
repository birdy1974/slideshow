"""Keeping an FFmpeg command inside what `execve()` can carry.

The renderer builds one command per part and hands the whole filter graph to FFmpeg as
a single argument. That is the normal way, and for most of this project's life it was
the only way — until a collage page with several photos, each carrying its own baked
size ladder, produced a graph of a few hundred kilobytes and the render died at 23 %
with::

    [Errno 7] Argument list too long: 'ffmpeg'

…before FFmpeg had read a single byte. `Argument list too long` is `E2BIG` from
`execve()`, and on Linux the binding limit is not the total size of the argument block
(`ARG_MAX`, usually ~2 MB) but **one single argument**: `MAX_ARG_STRLEN`, 32 pages,
131 072 bytes. A graph is one argument, so a graph longer than that cannot be passed at
all — no amount of free memory or shorter project changes that.

The fix is not to shorten the picture. FFmpeg can read a graph from a file, and a file
is not an argument, so an oversized graph is written next to the job's other interim
files and passed by name. Small graphs stay inline, which keeps every other command in
the project byte-identical to what it always was.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

#: Linux's `MAX_ARG_STRLEN`: the ceiling on *one* argument, 32 pages. It is spelled out
#: here rather than computed from the page size because it is the number the diagnosis is
#: quoted with, and a render log has to say the same figure the kernel applied; with a
#: 64 KiB page (aarch64) the real ceiling is larger, which only makes this conservative.
MAX_ARG_STRLEN = 32 * 4096

#: Linux's `MAX_ARG_STRLEN` is `32 * PAGE_SIZE` = 131 072 bytes, and the byte counted is
#: the argument *plus* its NUL. Staying under 96 KiB leaves room for a UTF-8 multibyte
#: value (a caption's worth of CJK is 3 bytes a character) and for whatever platform
#: quirks a NAS build has. It is a threshold for *how to pass* a graph, not a limit on
#: how long a graph may be: a longer one simply goes into a file instead.
MAX_FILTER_GRAPH_BYTES = 96 * 1024

#: The inline option, and the names that can read the same value from a file. The
#: generic `-/option` form (FFmpeg 4.4+) comes first because it is the one that still
#: exists in current builds; `-filter_complex_script` is the older specific option,
#: which FFmpeg 9 has dropped. Which one works is a property of the binary, so it is
#: probed rather than assumed — see `probe_graph_script_flag`.
GRAPH_SCRIPT_FLAGS: tuple[str, ...] = ("-/filter_complex", "-filter_complex_script")

#: Only this option carries graphs that scale with the project. `-vf` chains in the
#: renderer are one line per filter (`ass='file.ass'`, `drawtext=…`): long values there
#: come from a caption's length, not from a slide's photo count, and they are nowhere
#: near 96 KiB. If that stops being true, the option can be added here and the probe
#: below extended to it.
GRAPH_OPTIONS: tuple[str, ...] = ("-filter_complex",)

#: What FFmpeg prints when it does not know an option — both the modern and the older
#: wording, because the probe has to tell "this build cannot do it" apart from "it did
#: it and the graph is bad", and only the first one means fall back.
UNKNOWN_OPTION_MARKS = ("unrecognized option", "unknown option", "option not found")


def byte_len(value: str) -> int:
    """Bytes this argument will occupy in the argument vector (what the kernel counts)."""
    return len(value.encode("utf-8"))


def oversized_graph_positions(command: Sequence[str], limit: int | None = None) -> list[int]:
    """Positions in `command` of a filter graph too long for one argument.

    The index returned is the index of the *value*, i.e. the graph itself, so a caller
    can substitute it in place. `limit` defaults to the module ceiling, read here rather
    than bound at definition time, so a test (or a future setting) can lower it.
    """
    if limit is None:
        limit = MAX_FILTER_GRAPH_BYTES
    found: list[int] = []
    for position, arg in enumerate(command):
        if arg not in GRAPH_OPTIONS:
            continue
        value = position + 1
        if value < len(command) and byte_len(command[value]) > limit:
            found.append(value)
    return found


def graph_script_command(
    command: Sequence[str],
    directory: Path,
    stem: str,
    script_flag: str | None,
    limit: int | None = None,
) -> tuple[list[str], list[str], list[Path]]:
    """Rewrite `command` so no filter graph has to fit in a single argument.

    Returns the command to exec, one note per graph that moved (for the job log), and
    the files written. `command` itself is never mutated: the same list is patched into
    the hardware→software fallback later, and it has to keep describing the render the
    way the builder wrote it.

    `script_flag is None` — this FFmpeg knows no way to read a graph from a file — leaves
    the command alone. Then a Linux render still hits `E2BIG`, which is the honest
    answer: an inline graph fails on that one platform, while a command rewritten into a
    form the binary does not understand would fail on every platform.
    """
    if limit is None:
        limit = MAX_FILTER_GRAPH_BYTES
    out = list(command)
    notes: list[str] = []
    written: list[Path] = []
    if not script_flag:
        return out, notes, written
    directory.mkdir(parents=True, exist_ok=True)
    for position in oversized_graph_positions(command, limit):
        graph = command[position]
        size = byte_len(graph)
        path = directory / f"{stem}-{position}.filter"
        # The file's content *is* the option's value, verbatim: FFmpeg does not strip a
        # trailing newline, and a newline after the last filter is part of the graph.
        # Same for a BOM, which is why this is written as plain UTF-8, not utf-8-sig.
        path.write_text(graph, encoding="utf-8", newline="")
        # The option and its value are two argv entries either way: only the pair's
        # *contents* change, so nothing after it shifts and `-map [v]` still refers to
        # the same output pad.
        out[position - 1] = script_flag
        out[position] = str(path)
        notes.append(
            f"{command[position - 1]} is {size} bytes, past the {limit}-byte ceiling on one "
            f"argument (Linux MAX_ARG_STRLEN is {MAX_ARG_STRLEN}): exec'ed as {script_flag} "
            f"{path}, same {len(graph)} characters"
        )
        written.append(path)
    return out, notes, written


def probe_graph_script_flag(ffmpeg_bin: str, timeout: float = 15.0) -> str | None:
    """Which option, if any, reads a filter graph from a file on this FFmpeg.

    `-h full` does not list either name, so the help text cannot be parsed for them — the
    only honest detection is to run a one-frame graph from a file and read what comes
    back. Anything that is not "I do not know that option" counts as support, including
    a failure from elsewhere in the command: this build understood the argument, which is
    the only thing being asked.
    """
    for flag in GRAPH_SCRIPT_FLAGS:
        try:
            with tempfile.TemporaryDirectory(prefix="ffmpeg-argv-probe-") as scratch:
                graph = Path(scratch) / "probe.filter"
                graph.write_text("[0:v]format=yuv420p,setsar=1[v]", encoding="utf-8", newline="")
                output = Path(scratch) / "probe.mp4"
                result = subprocess.run(
                    [ffmpeg_bin, "-hide_banner", "-loglevel", "error", "-y",
                     "-f", "lavfi", "-i", "color=c=black:s=16x16:d=0.04",
                     flag, str(graph), "-map", "[v]", "-frames:v", "1", str(output)],
                    capture_output=True, text=True, timeout=timeout, check=False,
                )
                if result.returncode == 0 and output.exists():
                    return flag
                text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
                if not any(mark in text for mark in UNKNOWN_OPTION_MARKS):
                    # Some other complaint: the option was accepted, so the graph-from-file
                    # path is real on this build. Let the render find its own error.
                    return flag
        except Exception:  # noqa: BLE001 - a missing/slow ffmpeg must not become a render error
            continue
    return None


def remove_quietly(paths: Sequence[Path]) -> None:
    """Delete the graph files a render wrote; a render's temp files are never an error."""
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass
