"""Cleaning up after an app that died in the middle of a render.

Two things a restart leaves behind, both measured during the report this fixes:
a row that says ``running`` while nothing owns it (so the queue spins and
``POST /api/jobs/<id>/cancel`` answers 409), and an FFmpeg child that outlived the
app and kept encoding into a work directory nobody would ever collect.
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from app.database import Database
from app.job_recovery import (ABANDONED_ERROR, ABANDONED_STAGE, orphaned_ffmpeg, process_cmdlines, recover_jobs,
                              reap_abandoned_jobs, reap_orphaned_ffmpeg)

CREATED = "2026-01-01T00:00:00+00:00"


def add_job(db: Database, job_id: str, status: str, *, finished_at: str | None = None) -> None:
    # `render_jobs.project_id` is a foreign key with `PRAGMA foreign_keys=ON`, so the
    # row needs the project it belongs to — exactly as a real job does.
    project = db.save_project({"project": {"name": job_id}})
    with db.connect(write=True) as conn:
        conn.execute(
            "INSERT INTO render_jobs(id,project_id,kind,status,progress,stage,settings_json,created_at,finished_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (job_id, project["id"], "render", status, 22.7, "Encoding slide 45/112", "{}", CREATED, finished_at),
        )


class DatabaseFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.db = Database(self.base / "slideshow.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def rows(self) -> dict[str, Any]:
        return {row["id"]: row for row in self.db.list_jobs()}


class ReapAbandonedJobsTest(DatabaseFixture):
    def test_every_row_the_dead_instance_left_behind_is_cleared(self) -> None:
        for index, status in enumerate(("queued", "running", "cancelling")):
            add_job(self.db, f"dead{index}", status)
        cleared = reap_abandoned_jobs(self.db, now="2026-01-02T00:00:00+00:00")
        self.assertEqual(3, len(cleared))
        for job_id in ("dead0", "dead1", "dead2"):
            row = self.rows()[job_id]
            self.assertEqual("failed", row["status"], row)
            self.assertEqual(ABANDONED_STAGE, row["stage"])
            self.assertIn("restart", row["error_message"])
            self.assertEqual("2026-01-02T00:00:00+00:00", row["finished_at"],
                             "without a finish time the queue still treats the row as live")

    def test_rows_that_ended_are_left_alone(self) -> None:
        for status in ("complete", "cancelled", "failed"):
            add_job(self.db, f"done-{status}", status, finished_at=CREATED)
        self.assertEqual([], reap_abandoned_jobs(self.db))
        for status in ("complete", "cancelled", "failed"):
            row = self.rows()[f"done-{status}"]
            self.assertEqual(status, row["status"], "a finished job is history, not debris")
            self.assertEqual(CREATED, row["finished_at"])

    def test_the_second_pass_finds_nothing(self) -> None:
        add_job(self.db, "once", "running")
        self.assertEqual(["once"], reap_abandoned_jobs(self.db))
        self.assertEqual([], reap_abandoned_jobs(self.db), "startup must not rewrite history twice")

    def test_the_row_blames_the_restart_and_not_the_project(self) -> None:
        add_job(self.db, "blame", "running")
        reap_abandoned_jobs(self.db)
        row = self.rows()["blame"]
        self.assertNotIn("FFmpeg", row["stage"], "the stage column is what the queue shows")
        self.assertIn("Nothing is broken in the slideshow", row["error_message"])
        self.assertIn("render it again", ABANDONED_ERROR)

    def test_progress_survives_so_the_row_reads_where_it_stopped(self) -> None:
        add_job(self.db, "halfway", "running")
        reap_abandoned_jobs(self.db)
        self.assertEqual(22.7, self.rows()["halfway"]["progress"])


class ProcessSnapshotTest(unittest.TestCase):
    """``/proc`` is a race, and a raise here means the app never boots.

    A pid can disappear between the directory listing and the read of its command
    line. A startup that throws over that is worse than the frozen row it was sent to
    clear, so the snapshot has to swallow it and keep going.
    """

    def test_unreadable_entries_are_skipped_not_fatal(self) -> None:
        pids = ["999998", "not-a-pid", "999999"]
        with mock.patch("app.job_recovery.os.path.isdir", return_value=True), \
                mock.patch("app.job_recovery.os.listdir", return_value=pids):
            self.assertEqual({}, process_cmdlines())

    def test_no_proc_mount_is_an_empty_snapshot(self) -> None:
        with mock.patch("app.job_recovery.os.path.isdir", return_value=False):
            self.assertEqual({}, process_cmdlines())


class OrphanedFFmpegTest(unittest.TestCase):
    work = Path("/config/work")
    cmdlines = {
        10: "/usr/local/bin/ffmpeg -hide_banner -y -i /photos/a.jpg -filter_complex [0:v]null[v] "
            "-map [v] /config/work/68612b3f/segment-0000.mp4",
        11: "/usr/local/bin/ffmpeg -hide_banner -y -i /videos/a.mp4 /elsewhere/hand-run.mp4",
        12: "/usr/local/bin/ffmpeg -i /photos/b.jpg /config/work-old/keep-me/segment-0000.mp4",
        13: "/usr/local/bin/python -m uvicorn app.main:app",
        14: "ffmpeg -loop 1 -i /photos/c.jpg /config/work/68612b3f/transition-0001.mp4",
        15: "",
    }

    def test_only_the_processes_working_in_our_work_directory_are_ours(self) -> None:
        self.assertEqual([10, 14], orphaned_ffmpeg(self.work, cmdlines=self.cmdlines))

    def test_a_sibling_directory_that_shares_the_prefix_is_not_matched(self) -> None:
        self.assertNotIn(12, orphaned_ffmpeg(self.work, cmdlines=self.cmdlines),
                         "`/config/work` must not swallow `/config/work-old`")

    def test_an_empty_command_line_is_not_a_process_we_can_name(self) -> None:
        for pid in self.cmdlines:
            self.assertNotIn(15, orphaned_ffmpeg(self.work, cmdlines={15: self.cmdlines[15]}))


class ReapOrphanedFFmpegTest(unittest.TestCase):
    cmdlines = {
        7: "/usr/local/bin/ffmpeg -i /photos/a.jpg /config/work/j1/segment-0000.mp4",
        8: "/usr/local/bin/ffmpeg -i /photos/b.jpg /config/work/j2/segment-0000.mp4",
    }

    def test_a_polite_child_is_only_asked(self) -> None:
        import signal as signal_module
        signals: list[tuple[int, int]] = []
        gone: set[int] = set()

        def kill(pid, sig):
            signals.append((pid, sig))
            gone.add(pid)          # it answered the first request

        slept: list[float] = []
        reap = reap_orphaned_ffmpeg(Path("/config/work"), cmdlines=self.cmdlines, kill=kill,
                                    alive=lambda pid: pid not in gone, sleep=slept.append)
        self.assertEqual([7, 8], reap)
        self.assertEqual([(7, signal_module.SIGTERM), (8, signal_module.SIGTERM)], sorted(signals),
                         "a child that stops when asked must not also be killed")
        self.assertEqual([], slept, "there is nothing to wait for once both are gone")

    def test_a_stubborn_child_is_killed_once_the_grace_runs_out(self) -> None:
        import signal as signal_module
        signals: list[tuple[int, int]] = []

        def kill(pid, sig):
            signals.append((pid, sig))

        def alive(_pid):
            return not any(sig == signal_module.SIGKILL for _p, sig in signals)

        slept: list[float] = []
        reap_orphaned_ffmpeg(Path("/config/work"), cmdlines={7: self.cmdlines[7]}, kill=kill, alive=alive,
                             sleep=slept.append, grace=0.3, step=0.1)
        self.assertEqual(1, sum(1 for _p, sig in signals if sig == signal_module.SIGTERM))
        self.assertEqual(1, sum(1 for _p, sig in signals if sig == signal_module.SIGKILL))
        self.assertEqual([0.1, 0.1, 0.1], slept, "the grace has to be spent in steps, not busy-waiting")

    def test_a_process_that_vanishes_between_the_look_and_the_kill_is_not_an_error(self) -> None:
        import signal as signal_module

        def kill(_pid, _sig):
            raise ProcessLookupError("it left on its own")

        self.assertEqual([7], reap_orphaned_ffmpeg(Path("/config/work"), cmdlines={7: self.cmdlines[7]},
                                                   kill=kill, alive=lambda _pid: False))


class RecoverJobsTest(DatabaseFixture):
    def test_startup_clears_the_rows_and_reports_both_halves(self) -> None:
        add_job(self.db, "frozen", "running")
        settings = mock.Mock(work_dir=self.base / "work")
        (self.base / "work").mkdir()
        logger = logging.getLogger("test.recover")
        with mock.patch("app.job_recovery.reap_orphaned_ffmpeg", return_value=[4242]) as stopped, \
                self.assertLogs(logger, level="WARNING") as records:
            abandoned, orphans = recover_jobs(self.db, settings, log=logger)
        self.assertEqual(1, len(abandoned))
        self.assertEqual([4242], orphans)
        stopped.assert_called_once()
        self.assertEqual("failed", self.rows()["frozen"]["status"])
        joined = "\n".join(records.output)
        self.assertIn("Cleared 1 render job(s)", joined)
        self.assertIn("Stopped 1 FFmpeg process(es)", joined)

    def test_a_healthy_start_is_quiet(self) -> None:
        settings = mock.Mock(work_dir=self.base / "work")
        logger = logging.getLogger("test.recover")
        with mock.patch("app.job_recovery.reap_orphaned_ffmpeg", return_value=[]), self.assertNoLogs(logger, level="WARNING"):
            abandoned, orphans = recover_jobs(self.db, settings, log=logger)
        self.assertEqual(([], []), (abandoned, orphans))

    def test_the_app_calls_it_while_starting_up(self) -> None:
        """Run the startup hook and watch the call happen, rather than read its source.

        Worth the fiddling: the hook is one line in a decorator body, and a line can be
        moved after the `yield` (too late, the app is shutting down), or passed the wrong
        database, without any other test noticing. `settings` is a module-level singleton
        built from CONFIG_DIR, so the objects it touches are stubbed, not real.
        """
        try:
            from app import main
        except Exception as exc:  # noqa: BLE001 - fastapi is an optional extra in some dev venvs
            self.skipTest(f"app.main is not importable here: {exc}")
        stub = mock.Mock(work_dir=self.base / "config" / "work", database_path=self.base / "config" / "slideshow.db")
        started: list[tuple[object, ...]] = []

        def recover(db, settings, *, log):            # noqa: ANN001 - mirrors the real signature
            started.append((db, settings, log))
            return [], []

        async def drive() -> None:
            with mock.patch.object(main, "settings", stub), mock.patch.object(main.db, "initialize"), \
                    mock.patch.object(main.renderer, "warm_capabilities"), \
                    mock.patch.object(main, "recover_jobs", side_effect=recover):
                async with main.lifespan(mock.Mock()):
                    self.assertEqual(1, len(started), "the hook must clear the rows before it serves a request")
                    db, settings, log = started[0]
                    self.assertIs(main.db, db, "the recovery has to use the app's own database")
                    self.assertIs(stub, settings, "…and the app's own work directory")
                    self.assertIs(main.log, log, "…and it has to log where the container log is read from")

        asyncio.run(drive())
        self.assertEqual(1, len(started))


if __name__ == "__main__":
    unittest.main()
