"""Cleaning up after an app that died in the middle of a render.

A job lives in two places at once: a row in ``render_jobs``, and a
``threading.Event`` in the renderer's memory. The row survives anything; the event
does not survive the process. So whenever the app goes away during a render — a
container stop or restart, an out-of-memory killer that gets the app instead of the
encoder, a ``kill -9`` — the queue keeps a row that says ``running`` forever.
Nothing will ever write its ``finished_at``, ``GET /api/jobs`` keeps serving it as
work in progress, and ``POST /api/jobs/<id>/cancel`` answers 409 "Job is not
running" because that lookup reads the in-memory map. The only way out was to open
the database by hand and fix the row, which is what this module now does at
startup, once, before the first status poll arrives.

The FFmpeg child can outlive us the same way. An app killed by an unhandled signal
leaves its children reparented rather than dead, still encoding into the work
directory of a job nobody is watching any more — measured: the encoder kept
producing frames for its full slide, wrote an MP4 nobody would collect, and the
row stayed at the progress it had when the app died. Both halves are cleared here,
children first and rows second, so that no FFmpeg is still writing into a directory
the queue has already forgotten.

The judgement "a non-terminal row belongs to a dead instance" is sound because one
app instance owns one database — that is what ``compose.yaml`` sets up (one service,
one ``/config`` volume). Two containers sharing a ``/config`` would fight over the
same work directory regardless of this module.
"""
from __future__ import annotations

import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Callable

from .database import utcnow

#: Statuses that mean "somebody is working on this" and therefore cannot survive an
#: app restart: every one of them is written by a thread that no longer exists.
ABANDONED_STATUSES: tuple[str, ...] = ("queued", "running", "cancelling")

#: What the row says afterwards. The stage is what the queue's status column shows,
#: so it has to name the cause rather than blame the project.
ABANDONED_STAGE = "Abandoned — the app restarted during this render"
ABANDONED_ERROR = (
    "No render thread owns this row: the app exited while it was running (a container "
    "stop or restart, an out-of-memory kill, or a signal). Nothing is broken in the "
    "slideshow — render it again."
)

log = logging.getLogger("app.main")


def reap_abandoned_jobs(db: Any, *, now: str | None = None) -> list[str]:
    """Move every row left in a non-terminal status to ``failed``, and return their ids.

    ``failed`` rather than ``cancelled`` because the app did not cancel anything: the
    stage and the error text say what actually happened, and the queue's re-render
    action is what a ``failed`` row offers.
    """
    stamps = now or utcnow()
    marks = ",".join("?" * len(ABANDONED_STATUSES))

    def _write() -> list[str]:
        # One connection for the SELECT and the UPDATE, so a row cannot be inserted as
        # `queued` between them and then be missed; the private retry helper is the one
        # the database's own writers use for a `database is locked` answer.
        with db.connect(write=True) as conn:
            rows = conn.execute(f"SELECT id FROM render_jobs WHERE status IN ({marks})",
                                ABANDONED_STATUSES).fetchall()
            if not rows:
                return []
            conn.execute(
                f"UPDATE render_jobs SET status='failed', stage=?, error_message=?, finished_at=?"
                f" WHERE status IN ({marks})",
                (ABANDONED_STAGE, ABANDONED_ERROR, stamps, *ABANDONED_STATUSES),
            )
            return [str(row["id"]) for row in rows]

    return db._run_with_busy_retry(_write)


def process_cmdlines() -> dict[int, str]:
    """Every live pid and its command line. Empty where ``/proc`` is not mounted.

    Read from ``/proc`` rather than shelling out: the runtime image installs no
    ``procps``, so there is no ``ps``/``pgrep``/``pkill`` to ask.
    """
    out: dict[int, str] = {}
    for entry in os.listdir("/proc") if os.path.isdir("/proc") else []:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        if raw:
            out[int(entry)] = raw.replace(b"\0", b" ").decode("utf-8", "replace")
    return out


def orphaned_ffmpeg(work_dir: Path, *, cmdlines: dict[int, str] | None = None) -> list[int]:
    """Pids of FFmpeg processes working inside ``work_dir`` — i.e. ours.

    Deliberately narrow. A command line counts only when it names the work directory,
    so an FFmpeg somebody started in this container by hand, or another app sharing a
    PID namespace, is not ours to stop. The separator is included in the match so a
    sibling like ``/config/work-old`` does not read as ``/config/work``.

    ``argv[0]`` is what is tested, not the whole line: an ``ffmpeg.log`` path inside
    somebody else's ``python`` process is not an encoder. A wrapper such as
    ``sh -c 'ffmpeg …'`` is therefore not matched — and is not meant to be, because
    the encoder it execs appears with its own pid and its own ``argv[0]``.
    """
    needle = str(work_dir).rstrip(os.sep) + os.sep
    lines = process_cmdlines() if cmdlines is None else cmdlines
    found: list[int] = []
    for pid, cmdline in lines.items():
        words = cmdline.split()
        if not words or "ffmpeg" not in Path(words[0]).name.lower():
            continue
        if needle in cmdline:
            found.append(pid)
    return sorted(found)


def reap_orphaned_ffmpeg(
    work_dir: Path,
    *,
    cmdlines: dict[int, str] | None = None,
    kill: Callable[[int, int], None] = os.kill,
    alive: Callable[[int], bool] = lambda pid: os.path.exists(f"/proc/{pid}"),
    sleep: Callable[[float], None] = time.sleep,
    grace: float = 2.0,
    step: float = 0.1,
) -> list[int]:
    """Ask each orphan to stop, then insist. Returns the pids that were touched.

    SIGTERM first because FFmpeg answers it by closing the file it has open; the
    process is unwatched, so a polite death and a forced one produce the same
    leftover — but the grace period costs nothing when the child is well.
    """
    pids = orphaned_ffmpeg(work_dir, cmdlines=cmdlines)
    for pid in pids:
        try:
            kill(pid, signal.SIGTERM)
        except OSError:
            continue
    waited = 0.0
    while waited < grace:
        if not any(alive(pid) for pid in pids):
            break
        sleep(step)
        waited += step
    for pid in pids:
        if not alive(pid):
            continue
        try:
            kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return pids


def recover_jobs(db: Any, settings: Any, *, log: logging.Logger = log,
                 now: str | None = None) -> tuple[list[str], list[int]]:
    """Clear the rows and stop the children a dead instance left behind.

    Both halves log, because the container log is the only place a user looks to find
    out whether an update, a reboot or a restart is what lost their render — and
    without these lines the render simply vanishes from the app's side and reappears
    as a progress bar that never moves.
    """
    work_dir = Path(settings.work_dir)
    orphans = reap_orphaned_ffmpeg(work_dir) if work_dir.exists() else []
    if orphans:
        log.warning("Stopped %d FFmpeg process(es) left running by a previous instance: %s",
                    len(orphans), ", ".join(str(pid) for pid in orphans))
    abandoned = reap_abandoned_jobs(db, now=now)
    if abandoned:
        log.warning("Cleared %d render job(s) that the previous instance left unfinished: %s",
                    len(abandoned), ", ".join(job_id[:8] for job_id in abandoned))
    return abandoned, orphans
