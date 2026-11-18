"""The argument vector has a size limit, and a many-photo slide can hit it.

`Failed — [Errno 7] Argument list too long: 'ffmpeg' · 23%` is not an FFmpeg error: it
is `E2BIG` from `execve()`, raised by Python before FFmpeg was even running. Linux caps
**one single argument** at `MAX_ARG_STRLEN` = 32 pages = 131 072 bytes, and a
`-filter_complex` graph is one argument — a collage page whose photos each carry a baked
per-frame size ladder easily writes 200 KB of `scale`/`rotate`/`overlay` lines. The
renderer's answer (app/ffmpeg_argv.py) is to hand the same graph over in a file when it
gets too long, and to leave every other command exactly as it was.

The pure tests below run anywhere; the two that need FFmpeg are skipped when there is no
FFmpeg to ask — including the reproduction of the failure itself, which is only claimed
where it can actually be measured.
"""
from __future__ import annotations

import errno
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from app.config import Settings
from app.database import Database
from app.ffmpeg_argv import (
    GRAPH_SCRIPT_FLAGS,
    MAX_ARG_STRLEN,
    MAX_FILTER_GRAPH_BYTES,
    byte_len,
    graph_script_command,
    oversized_graph_positions,
    probe_graph_script_flag,
    remove_quietly,
)
from app.renderer import Renderer, RenderError

# The kernel's real number: 32 pages of 4 KiB, and it is compared against the argument
# including its terminating NUL.


def ffmpeg_bin() -> str | None:
    """An FFmpeg to run for real, or None (CI has none, so those tests skip)."""
    return shutil.which("ffmpeg")


def long_graph(chars: int = 200_000) -> str:
    """A graph as long as a busy slide's: one filter carrying a 200 KB value.

    Length is what these tests are about, so the bytes go into a `drawtext` string rather
    than into 40 000 chained `null` filters — the same size, and 25 seconds of FFmpeg
    building filter instances is too much for a test to pay for a shape. A graph that does
    not parse would prove nothing either, hence one real filter.
    """
    return "[0:v]drawtext=text='" + "x" * chars + "':x=0:y=0[v]"


class ThresholdTest(unittest.TestCase):
    def test_the_threshold_sits_under_what_the_kernel_allows(self) -> None:
        self.assertLess(MAX_FILTER_GRAPH_BYTES, MAX_ARG_STRLEN,
                        "the point of the constant is to stop before the argument limit")
        self.assertGreater(MAX_FILTER_GRAPH_BYTES, 8192,
                           "a threshold that fires on ordinary slides would rewrite every render")

    def test_bytes_are_counted_not_characters(self) -> None:
        """The limit is in bytes, and a CJK caption is three bytes a character.

        Measuring this in `len()` would leave a graph that the kernel rejects looking
        acceptable to us, which is the one way this module could fail silently.
        """
        text = "[0:v]drawtext=text='" + "字" * 40000 + "':x=3[v]"
        self.assertLess(len(text), MAX_FILTER_GRAPH_BYTES, "short if counted in characters")
        self.assertEqual(3, byte_len("字"))
        self.assertGreater(byte_len(text), MAX_FILTER_GRAPH_BYTES, "over the limit in bytes")
        self.assertEqual([1], oversized_graph_positions(["-filter_complex", text]))
        self.assertEqual([], oversized_graph_positions(["-filter_complex", "[0:v]字[v]"]))


class GraphScriptCommandTest(unittest.TestCase):
    """What the rewrite does to a command, measured on the argv itself."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def command(self, graph: str) -> list[str]:
        return ["/usr/bin/ffmpeg", "-hide_banner", "-i", "in.mp4", "-filter_complex", graph,
                "-map", "[v]", "-c:v", "libx264", "-y", str(self.dir / "segment-0044.mp4")]

    def test_a_graph_that_fits_is_left_exactly_where_it_was(self) -> None:
        command = self.command("[0:v]scale=320:180[v]")
        moved, notes, written = graph_script_command(command, self.dir, "segment-0044", "-/filter_complex")
        self.assertEqual(command, moved)
        self.assertEqual([], notes)
        self.assertEqual([], written)
        self.assertEqual([], list(self.dir.iterdir()), "no file is written for a command that does not need one")

    def test_the_pair_is_replaced_and_nothing_after_it_moves(self) -> None:
        graph = long_graph()
        command = self.command(graph)
        moved, notes, written = graph_script_command(command, self.dir, "segment-0044",
                                                     "-/filter_complex", limit=1024)
        self.assertEqual(len(command), len(moved), "an option and its value are two entries either way")
        self.assertEqual(command[:4], moved[:4], "the inputs are untouched")
        self.assertEqual(command[6:], moved[6:], "-map [v] and the encoder flags must not shift by one")
        self.assertEqual("-/filter_complex", moved[4])
        self.assertEqual(str(written[0]), moved[5])
        self.assertEqual(1, len(notes))
        self.assertEqual(1, len(written))
        # The value handed to FFmpeg is the graph, byte for byte, with no trailing
        # newline: the file's content *is* the option's value.
        self.assertEqual(graph.encode("utf-8"), written[0].read_bytes())
        self.assertFalse(written[0].read_bytes().endswith(b"\n"))
        self.assertIn(str(len(graph.encode("utf-8"))), notes[0], "the log line names the size that tripped")
        self.assertIn("-filter_complex", notes[0])

    def test_the_caller_s_list_is_never_mutated(self) -> None:
        """The same list is patched later for the CPU fallback, so it must stay whole."""
        graph = long_graph()
        command = self.command(graph)
        snapshot = list(command)
        graph_script_command(command, self.dir, "segment-0044", "-/filter_complex", limit=1024)
        self.assertEqual(snapshot, command)
        self.assertIn(graph, command, "the graph stays in the command that was handed in")

    def test_no_script_option_means_no_change(self) -> None:
        """A build that cannot read a graph from a file must not be talked into one."""
        command = self.command(long_graph())
        moved, notes, written = graph_script_command(command, self.dir, "segment-0044", None, limit=1024)
        self.assertEqual(command, moved)
        self.assertEqual([], notes)
        self.assertEqual([], list(self.dir.iterdir()))

    def test_only_the_graph_option_is_moved(self) -> None:
        """A long `-vf` chain is left inline: it is short by construction, and the
        file-form option for it is a separate promise FFmpeg does not make."""
        chain = ",".join(f"drawtext=text='{i}'" for i in range(4000))
        command = ["/usr/bin/ffmpeg", "-vf", chain, "-y", str(self.dir / "out.mp4")]
        moved, notes, written = graph_script_command(command, self.dir, "out", "-/filter_complex", limit=1024)
        self.assertEqual(command, moved)
        self.assertEqual([], notes)

    def test_the_scratch_files_are_removed_by_the_caller(self) -> None:
        _moved, _notes, written = graph_script_command(self.command(long_graph()), self.dir,
                                                       "segment-0044", "-/filter_complex", limit=1024)
        self.assertTrue(all(path.exists() for path in written))
        remove_quietly(written)
        self.assertEqual([], list(self.dir.iterdir()))
        remove_quietly(written)   # a second sweep over an empty list is not an error


class RunFFmpegWiringTest(unittest.TestCase):
    """`_run_ffmpeg` is the one place every render command goes through."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        for name in ("config", "photos", "videos", "music", "output", "work"):
            (base / name).mkdir()
        self.base = base
        self.settings = Settings(base / "config", base / "photos", base / "videos",
                                 base / "music", base / "output")
        self.db = Database(base / "slideshow.db")
        self.db.initialize()
        self.renderer = Renderer(self.db, self.settings)
        self.log = base / "work" / "ffmpeg.log"
        self.log.parent.mkdir(parents=True, exist_ok=True)
        logging.disable(logging.CRITICAL)


    def tearDown(self) -> None:
        self.renderer.pool.shutdown(wait=True, cancel_futures=True)
        logging.disable(logging.NOTSET)
        self.temp.cleanup()

    def exec_command(self, command: list[str], flag: str | None = "-/filter_complex") -> dict[str, object]:
        """Run `_run_ffmpeg` with a fake process, and report what it exec'ed.

        `"bytes"` is read from inside the fake, because a scratch graph file exists only
        while the process runs — which is exactly when the test can still see it.
        """
        seen: dict[str, object] = {}

        class FakeProcess:
            returncode = 0

            def poll(self) -> int:
                return 0

        def fake_popen(argv, **_kwargs):
            seen["argv"] = list(argv)
            for position, arg in enumerate(argv):
                if arg in GRAPH_SCRIPT_FLAGS:
                    seen["bytes"] = Path(argv[position + 1]).read_bytes()
            return FakeProcess()

        with mock.patch.object(self.renderer, "graph_script_flag", return_value=flag), \
                mock.patch.object(subprocess, "Popen", side_effect=fake_popen):
            self.renderer._run_ffmpeg(command, threading.Event(), self.log)
        return seen

    def test_an_oversized_graph_is_executed_from_a_file(self) -> None:
        graph = long_graph()
        command = [self.settings.ffmpeg_bin, "-i", "in.mp4", "-filter_complex", graph,
                   "-map", "[v]", "-y", str(self.base / "work" / "segment-0001.mp4")]
        seen = self.exec_command(command)
        argv = seen["argv"]
        self.assertNotIn(graph, argv, "the graph must not be an argument any more")
        self.assertLessEqual(max(byte_len(arg) for arg in argv), MAX_ARG_STRLEN,
                             "every argument fits what execve() can carry")
        self.assertIn("-/filter_complex", argv)
        self.assertEqual(graph.encode("utf-8"), seen["bytes"])
        script = Path(argv[argv.index("-/filter_complex") + 1])
        self.assertEqual(["-map", "[v]"], argv[argv.index("-/filter_complex") + 2:][:2],
                         "the pad the graph declares is still mapped")
        # The log is the diagnostic record: it keeps the built command line, so the
        # `=== Slide 2/3 ===` triage reads the same as before, and adds what was exec'ed.
        text = self.log.read_text(encoding="utf-8")
        self.assertIn(graph[:200], text, "the logged command is the one that was built")
        self.assertIn("-/filter_complex", text)
        self.assertIn(str(MAX_ARG_STRLEN), text)
        self.assertFalse(script.exists(), "a render leaves no graph files behind")

    def test_cancel_stops_a_running_child(self) -> None:
        import sys
        cancelled = threading.Event()
        timer = threading.Timer(0.4, cancelled.set)
        timer.start()
        try:
            with mock.patch.object(self.renderer, "graph_script_flag", return_value=None):
                with self.assertRaisesRegex(RenderError, "cancelled"):
                    self.renderer._run_ffmpeg(
                        [sys.executable, "-c", "import time; time.sleep(30)"], cancelled, self.log)
        finally:
            timer.cancel()

    def test_a_small_graph_is_executed_unchanged(self) -> None:
        command = [self.settings.ffmpeg_bin, "-filter_complex", "[0:v]scale=2[v]", "-map", "[v]",
                   "-y", str(self.base / "work" / "p.mp4")]
        seen = self.exec_command(command)
        self.assertEqual(command, seen["argv"])
        self.assertEqual([], [p for p in (self.base / "work").iterdir() if p.name != "ffmpeg.log"])

    def test_a_build_without_the_option_is_left_alone(self) -> None:
        """An unprobed binary gets the command exactly as the builder wrote it."""
        command = [self.settings.ffmpeg_bin, "-filter_complex", long_graph(), "-y", "p.mp4"]
        seen = self.exec_command(command, flag=None)
        self.assertEqual(command, seen["argv"], "no rewrite, so no invented option name")
        self.assertEqual([], list((self.base / "work" / "job").glob("*.filter")))

    def test_a_vf_chain_is_left_inline(self) -> None:
        """`-vf` is the same kernel limit, but no renderer path builds a long one.

        Covering it would mean rewriting something that cannot overflow, so the choice
        is pinned instead: an enormous `-vf` is *not* moved into a file.
        """
        seen = self.exec_command([self.settings.ffmpeg_bin, "-vf", "zoompan=z=" + "9" * 200_000,
                                  "-y", "p.mp4"])
        self.assertIn("-vf", seen["argv"])
        self.assertIn("zoompan=z=" + "9" * 200_000, seen["argv"])

    def test_a_scratch_graph_is_deleted_when_ffmpeg_never_starts(self) -> None:
        """The file is ours to clean up, and the failed exec is our only chance."""
        seen: dict[str, object] = {}

        def boom(argv, **_kwargs):
            seen["argv"] = list(argv)
            raise OSError(errno.E2BIG, "Argument list too long", "ffmpeg")

        command = [self.settings.ffmpeg_bin, "-filter_complex", long_graph(), "-y",
                   str(self.base / "work" / "segment-0044.mp4")]
        with mock.patch.object(self.renderer, "graph_script_flag", return_value="-/filter_complex"), \
                mock.patch.object(subprocess, "Popen", side_effect=boom):
            with self.assertRaises(RenderError):
                self.renderer._run_ffmpeg(command, threading.Event(), self.log)
        # The move happened — the exec line is short — and the file it wrote is gone.
        argv = seen["argv"]
        self.assertIn("-/filter_complex", argv)
        self.assertLessEqual(max(byte_len(str(arg)) for arg in argv), MAX_ARG_STRLEN)
        self.assertEqual([], list((self.base / "work" / "job").glob("*.filter")),
                         "a failed exec must not litter the job directory")
        self.assertIn("Could not start FFmpeg", self.log.read_text(encoding="utf-8"))

    def test_an_exec_failure_says_what_could_not_start(self) -> None:
        """`[Errno 7] Argument list too long` on its own tells the reader nothing."""
        def boom(*_args, **_kwargs):
            raise OSError(errno.E2BIG, "Argument list too long", "ffmpeg")

        with mock.patch.object(self.renderer, "graph_script_flag", return_value=None), \
                mock.patch.object(subprocess, "Popen", side_effect=boom), \
                self.assertRaises(RenderError) as caught:
            self.renderer._run_ffmpeg([self.settings.ffmpeg_bin, "-y", "p.mp4"],
                                      threading.Event(), self.log)
        self.assertIn("Could not start FFmpeg", str(caught.exception))
        self.assertIn("Argument list too long", str(caught.exception))
        self.assertIn("Could not start FFmpeg", self.log.read_text(encoding="utf-8"))


@unittest.skipIf(ffmpeg_bin() is None, "needs a real FFmpeg to run a graph from a file")
class RealFFmpegTest(unittest.TestCase):
    """The failure, the fix and the picture — measured, not reasoned about."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = ffmpeg_bin()
        assert cls.ffmpeg is not None
        cls.temp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.temp.name)
        subprocess.run([cls.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "color=c=red:s=64x64:d=0.2:r=25", "-c:v", "libx264", "-y", str(cls.dir / "in.mp4")],
                       check=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_a_graph_the_size_of_a_slide_s_cannot_be_passed_as_an_argument(self) -> None:
        """The user's report, reproduced: the inline form cannot even start FFmpeg.

        Only assertable on Linux, where `MAX_ARG_STRLEN` exists; elsewhere the same
        command runs and this test says so rather than pretending.
        """
        graph = long_graph()
        self.assertGreater(byte_len(graph), MAX_ARG_STRLEN)
        command = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(self.dir / "in.mp4"),
                   "-filter_complex", graph, "-map", "[v]", "-frames:v", "1", "-y", str(self.dir / "inline.mp4")]
        if os.uname().sysname != "Linux":
            self.skipTest(f"no per-argument limit on {os.uname().sysname} to reproduce")
        with self.assertRaises(OSError) as caught:
            subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(errno.E2BIG, caught.exception.errno)
        self.assertIn("Argument list too long", str(caught.exception))
        # …and the same graph, same text, from a file: FFmpeg runs it.
        moved, notes, written = graph_script_command(command, self.dir, "repro",
                                                    probe_graph_script_flag(self.ffmpeg))
        self.assertEqual(1, len(notes))
        self.assertLessEqual(max(byte_len(arg) for arg in moved), MAX_ARG_STRLEN,
                             "the rewritten exec line has to fit the kernel's limit")
        result = subprocess.run(moved, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr[-300:])
        self.assertTrue((self.dir / "inline.mp4").exists(), "FFmpeg ran and wrote the file")
        remove_quietly(written)
        self.assertEqual([], [p.name for p in self.dir.iterdir() if p.suffix == ".filter"],
                         "the scratch graph is not left behind in the job directory")

    def test_the_same_graph_renders_the_same_frames_from_a_file(self) -> None:
        """Quoting survives the round trip through a file — the only real risk here.

        A graph is normally tokenised out of a shell-ish string, and the values in this
        one contain a comma, spaces inside single quotes, an expression in parentheses
        and an `enable` window. If reading the file changed any of that, the two encodes
        would differ, so the comparison is per-frame MD5s rather than file sizes.
        """
        graph = ("[0:v]drawtext=text='a, b':x='(w-tw)/2':y='3':enable='between(t,0.04,0.12)',"
                 "format=yuv420p[v]")
        inline = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(self.dir / "in.mp4"),
                  "-filter_complex", graph, "-map", "[v]", "-y", str(self.dir / "inline.mp4")]
        subprocess.run(inline, check=True, capture_output=True)
        # Two encodes, one graph inline and one read from a file: if the rewrite changed
        # what FFmpeg parses — a quote, an escape, a dropped character — the frames differ.
        scripted = list(inline)
        scripted[-1] = str(self.dir / "from-file.mp4")
        moved, _notes, written = graph_script_command(scripted, self.dir, "same",
                                                     probe_graph_script_flag(self.ffmpeg), limit=10)
        self.assertEqual(str(self.dir / "from-file.mp4"), moved[-1],
                         "the rewrite leaves every other argument where it was")
        subprocess.run(moved, check=True, capture_output=True)
        remove_quietly(written)

        def frame_hashes(path: Path) -> str:
            result = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
                                     "-f", "framemd5", "-"], capture_output=True, text=True, check=True)
            return result.stdout

        a, b = frame_hashes(self.dir / "inline.mp4"), frame_hashes(self.dir / "from-file.mp4")
        self.assertTrue(a.strip(), "the reference encode produced nothing to compare")
        self.assertEqual(a, b, "a graph read from a file must be the same graph")

    def test_the_probe_only_returns_a_name_this_build_accepts(self) -> None:
        flag = probe_graph_script_flag(self.ffmpeg)
        self.assertIn(flag, GRAPH_SCRIPT_FLAGS,
                      "either a spelling this FFmpeg takes, or None so the render stays inline")
        if flag is None:
            self.skipTest("this build has no way to read a graph from a file")
        graph = self.dir / "probe.filter"
        graph.write_text("[0:v]format=yuv420p[v]", encoding="utf-8", newline="")
        result = subprocess.run([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                                 "-i", "color=c=black:s=16x16:d=0.04", flag, str(graph),
                                 "-map", "[v]", "-frames:v", "1", "-y", str(self.dir / "probe.mp4")],
                                capture_output=True, text=True, check=False)
        self.assertEqual(0, result.returncode, result.stderr[-300:])


class ProbeGraceTest(unittest.TestCase):
    """A missing or broken FFmpeg must not become a render error."""

    def test_no_binary_means_no_flag(self) -> None:
        self.assertIsNone(probe_graph_script_flag(str(Path(tempfile.gettempdir()) / "definitely-not-ffmpeg")))

    def test_a_hanging_binary_is_given_up_on(self) -> None:
        with mock.patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("ffmpeg", 1)):
            self.assertIsNone(probe_graph_script_flag("ffmpeg"))

    def test_a_build_that_fails_for_another_reason_still_counts_as_support(self) -> None:
        """"Unrecognized option" is the only answer that means fall back.

        A build that accepted the option and then complained about lavfi, about the
        encoder or about its own day is a build that can read a graph from a file, and
        the render itself is the place for its problems to surface.
        """
        class Result:
            returncode = 1
            stdout = ""
            stderr = "[AVFilterGraph] No such filter: 'nonexistent'"

        with mock.patch.object(subprocess, "run", return_value=Result()):
            self.assertEqual(GRAPH_SCRIPT_FLAGS[0], probe_graph_script_flag("ffmpeg"))

        class Unknown:
            returncode = 1
            stdout = ""
            stderr = "Unrecognized option 'filter_complex_script'."

        with mock.patch.object(subprocess, "run", side_effect=[Unknown(), Unknown()]):
            self.assertIsNone(probe_graph_script_flag("ffmpeg"))


if __name__ == "__main__":
    unittest.main()
