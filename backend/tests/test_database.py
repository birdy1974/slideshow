from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.database import Database


class DatabaseRoundTripTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "slideshow.db")
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def payload(self) -> dict:
        return {
            "schemaVersion": 1,
            "project": {"name": "Complete project", "randomOrder": True, "futureProjectFlag": "retained"},
            "media": [{
                "id": 17, "name": "one.jpg", "path": "/photos/holiday", "src": "", "type": "image",
                "duration": 7.5, "effect": "Ken Burns · Pan left", "transition": "Circle crop", "transitionTime": 1.3,
                "text": "Caption", "textMode": "overlay", "textStart": 1.0, "textEnd": 6.0,
                "textEnter": "Slide up", "textExit": "Dissolve", "textEnterDuration": .4, "textExitDuration": .7,
                "textX": 37, "textY": 82, "frameBackground": "#123456", "futureItemSetting": {"x": 1},
            }],
            "textDefaults": {"fontFamily": "Roboto", "fontSize": 54, "fontColor": "#ffeeaa", "bold": True, "italic": True, "underline": False},
            "soundtrack": {"policy": "Loop & trim", "volume": 72, "fadeOut": True, "tracks": [{"id": 2, "name": "song.mp3", "path": "/music/set", "duration": "3:20", "gain": -2}]},
            "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps", "bitrate": "8 Mbps · High", "encoder": "Auto · Quick Sync", "path": "/output", "filename": "movie"},
            "timeline": {"rows": "3", "zoom": 1.8},
            "unknownFutureSection": {"must": "survive"},
        }

    def test_create_and_update_are_lossless(self) -> None:
        payload = self.payload()
        created = self.db.save_project(payload)
        project_id = created["id"]
        for key, value in payload.items():
            self.assertEqual(value, created[key])
        self.assertEqual(1, created["revision"])

        payload["media"][0]["text"] = "Updated"
        payload["output"]["filename"] = "new-name"
        updated = self.db.save_project(payload, project_id)
        self.assertEqual(2, updated["revision"])
        self.assertEqual("Updated", updated["media"][0]["text"])
        self.assertEqual("new-name", updated["output"]["filename"])
        self.assertEqual({"must": "survive"}, updated["unknownFutureSection"])

        with self.db.connect() as conn:
            media = conn.execute("SELECT * FROM media_items WHERE project_id=?", (project_id,)).fetchone()
            self.assertEqual("Ken Burns · Pan left", media["effect"])
            self.assertEqual("Slide up", media["text_enter"])
            audio = conn.execute("SELECT * FROM audio_tracks WHERE project_id=?", (project_id,)).fetchone()
            self.assertEqual("song.mp3", audio["name"])

    def test_transaction_rolls_back_on_missing_project(self) -> None:
        with self.assertRaises(KeyError):
            self.db.save_project(self.payload(), 999)
        self.assertEqual([], self.db.list_projects())

    def test_clear_all_then_save_creates_fresh_project(self) -> None:
        """Regression: "Clear all" wipes every row; the editor must then be able
        to save again (as a fresh POST, not an UPDATE of the deleted id)."""
        created = self.db.save_project(self.payload())
        stale_id = created["id"]
        with self.db.connect() as conn:
            conn.execute("DELETE FROM projects")

        with self.assertRaises(KeyError, msg="updating the deleted row must fail loudly"):
            self.db.save_project(self.payload(), stale_id)

        # Saving without an id (what the UI falls back to) creates a new row.
        recreated = self.db.save_project(self.payload())
        self.assertNotEqual(stale_id, recreated["id"])
        self.assertEqual(1, recreated["revision"])
        self.assertEqual(1, len(self.db.list_projects()))
        # Child tables belong to the new row only, and cascade deleted rows are gone.
        with self.db.connect() as conn:
            orphans = conn.execute(
                "SELECT COUNT(*) FROM media_items WHERE project_id=?", (stale_id,)
            ).fetchone()[0]
            fresh = conn.execute(
                "SELECT COUNT(*) FROM media_items WHERE project_id=?", (recreated["id"],)
            ).fetchone()[0]
        self.assertEqual(0, orphans)
        self.assertEqual(1, fresh)


class ConcurrentAccessTest(unittest.TestCase):
    """Regression: polling GET /api/jobs/{id} while a render job writes progress
    used to blow up with `sqlite3.OperationalError: database is locked`, because
    every connection re-ran `PRAGMA journal_mode=WAL` (which needs an exclusive
    lock) before the busy timeout had been configured."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp.name) / "slideshow.db")
        self.db.initialize()
        self.project_id = self.db.save_project({"project": {"name": "p"}})["id"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_reads_succeed_while_a_writer_holds_a_transaction(self) -> None:
        self.db.create_job({"id": "job-1", "project_id": self.project_id, "kind": "render"})
        with self.db.connect(write=True) as writer:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE render_jobs SET progress=42 WHERE id=?", ("job-1",))
            # A separate reader connection must not fail while the write is open.
            job = self.db.get_job("job-1")
            self.assertIsNotNone(job)
            self.assertEqual("queued", job["status"])
            self.assertEqual([], self.db.list_jobs(project_id=self.project_id + 999))
            writer.execute("COMMIT")
        self.assertEqual(42, self.db.get_job("job-1")["progress"])

    def test_parallel_readers_and_writers_do_not_deadlock(self) -> None:
        self.db.create_job({"id": "job-2", "project_id": self.project_id, "kind": "preview"})
        errors: list[BaseException] = []
        stop = threading.Event()

        def writer() -> None:
            try:
                for step in range(60):
                    self.db.update_job("job-2", progress=float(step), stage=f"step {step}")
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)
            finally:
                stop.set()

        def reader() -> None:
            try:
                while not stop.is_set():
                    self.assertIsNotNone(self.db.get_job("job-2"))
                    self.db.list_jobs()
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=writer)] + [threading.Thread(target=reader) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual([], errors)
        self.assertEqual(59, self.db.get_job("job-2")["progress"])

    def test_reads_work_when_the_file_is_not_in_wal_mode(self) -> None:
        """The exact production failure: a database still in rollback-journal
        mode plus an in-flight writer made the per-connection WAL switch abort."""
        with self.db.connect(write=True) as conn:
            conn.execute("PRAGMA journal_mode=DELETE")
        self.db._journal_mode_ready = False
        self.db.create_job({"id": "job-3", "project_id": self.project_id, "kind": "render"})

        writer = self.db._new_connection()
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE render_jobs SET stage='Encoding' WHERE id=?", ("job-3",))
            self.assertIsNotNone(self.db.get_job("job-3"))
            writer.execute("COMMIT")
        finally:
            writer.close()

    def test_journal_mode_is_wal_and_not_reapplied_per_connection(self) -> None:
        with self.db.connect() as conn:
            self.assertEqual("wal", conn.execute("PRAGMA journal_mode").fetchone()[0].lower())
            self.assertEqual(30000, conn.execute("PRAGMA busy_timeout").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
