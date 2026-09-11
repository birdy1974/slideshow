"""Tests for delivering finished renders to the local device.

Option 1 of docs/output-download-options.md: the jobs-file download route gets
real sizes on the job rows, friendly attachment names for proxy previews, and
an availability flag so the GUI can offer re-render instead of a dead link.
"""
from __future__ import annotations

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock  # noqa: F401 - used via mock.patch below

from app.database import Database
from app.config import Settings


def sample_payload(name: str = "Beach trip") -> dict:
    return {
        "schemaVersion": 1,
        "project": {"name": name, "randomOrder": False},
        "media": [],
        "textDefaults": {},
        "soundtrack": {"tracks": []},
        "output": {"resolution": "Full HD · 1080p", "frameRate": "30 fps", "bitrate": "8 Mbps · High", "encoder": "CPU · x264", "path": "/output", "filename": "beach-trip"},
    }


class SizeBytesMigrationTest(unittest.TestCase):
    def test_fresh_database_has_the_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "db.sqlite")
            db.initialize()
            with db.connect() as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(render_jobs)")}
            self.assertIn("size_bytes", columns)
            with db.connect() as conn:
                versions = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
            self.assertEqual(versions, {1, 2})

    def test_v1_database_is_upgraded_in_place(self) -> None:
        """A database created before the column existed gains it on initialize()."""
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "db.sqlite"
            conn = sqlite3.connect(path)
            # The exact v1 shape of render_jobs (no size_bytes), plus a v1 marker row.
            conn.executescript(
                """
                CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
                INSERT INTO schema_migrations VALUES (1, '2026-01-01T00:00:00');
                CREATE TABLE projects (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, schema_version INTEGER NOT NULL, name TEXT NOT NULL,
                  random_order INTEGER NOT NULL DEFAULT 0, timeline_rows TEXT NOT NULL DEFAULT 'auto',
                  timeline_zoom REAL NOT NULL DEFAULT 1.0, payload_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE render_jobs (
                  id TEXT PRIMARY KEY,
                  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                  kind TEXT NOT NULL CHECK(kind IN ('preview','render')),
                  status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, stage TEXT NOT NULL DEFAULT 'Queued',
                  output_path TEXT, error_message TEXT, log_text TEXT NOT NULL DEFAULT '',
                  settings_json TEXT NOT NULL, created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
                );
                INSERT INTO projects(schema_version,name,payload_json,created_at,updated_at) VALUES(1,'old','{}','2026-01-01','2026-01-01');
                INSERT INTO render_jobs(id,project_id,kind,status,progress,stage,settings_json,created_at)
                  VALUES('legacy',1,'render','complete',100,'Complete','{}','2026-01-01');
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            db.initialize()
            with db.connect() as fresh:
                columns = {row["name"] for row in fresh.execute("PRAGMA table_info(render_jobs)")}
                versions = {row["version"] for row in fresh.execute("SELECT version FROM schema_migrations")}
                row = fresh.execute("SELECT size_bytes FROM render_jobs WHERE id='legacy'").fetchone()
            self.assertIn("size_bytes", columns)
            self.assertEqual(versions, {1, 2})
            self.assertIsNone(row[0])
            # Re-initializing stays idempotent.
            db.initialize()
            with db.connect() as again:
                versions = {r["version"] for r in again.execute("SELECT version FROM schema_migrations")}
            self.assertEqual(versions, {1, 2})


class SizeRoundTripTest(unittest.TestCase):
    def test_update_job_persists_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "db.sqlite")
            db.initialize()
            saved = db.save_project(sample_payload())
            db.create_job({"id": "job-1", "project_id": saved["id"], "kind": "render", "settings": {}})
            db.update_job("job-1", status="complete", progress=100, output_path="/output/x.mp4", size_bytes=1234567)
            job = db.get_job("job-1")
            self.assertEqual(job["size_bytes"], 1234567)
            listed = db.list_jobs(saved["id"])
            self.assertEqual(listed[0]["size_bytes"], 1234567)
            # Absent stays NULL, and unknown keys never reach the table.
            db.create_job({"id": "job-2", "project_id": saved["id"], "kind": "preview", "settings": {}})
            self.assertIsNone(db.get_job("job-2")["size_bytes"])
            db.update_job("job-2", nonsense_key=1)  # type: ignore[arg-type]
            self.assertNotIn("nonsense_key", db.get_job("job-2"))


class RendererCompletionSizeTest(unittest.TestCase):
    def test_completed_job_records_output_size(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(config_dir=root / "config", photos_dir=root, videos_dir=root, music_dir=root, output_dir=root / "output")
            settings.work_dir.mkdir(parents=True, exist_ok=True)
            from app.renderer import Renderer

            db = Database(root / "db.sqlite")
            db.initialize()
            renderer = Renderer(db, settings)
            saved = db.save_project(sample_payload())
            db.create_job({"id": "job-size", "project_id": saved["id"], "kind": "render", "settings": {}})

            output = root / "output" / "done.mp4"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"\x00" * 2048)
            with mock.patch.object(Renderer, "render", return_value=output), \
                 mock.patch("app.renderer.shutil.which", return_value="/usr/bin/ffmpeg"):
                renderer._run("job-size", {"media": []}, "render", threading.Event())

            job = db.get_job("job-size")
            self.assertEqual(job["status"], "complete")
            self.assertEqual(job["size_bytes"], 2048)


class DeliveryEndpointTest(unittest.TestCase):
    """The route helpers: availability flags and friendly preview names."""

    def setUp(self) -> None:
        import app.main as main

        self.main = main
        self.temp = tempfile.TemporaryDirectory()
        # app.main reads its globals at import; every test swaps in an isolated
        # database and settings (restored implicitly by process teardown).
        root = Path(self.temp.name)
        self.settings = Settings(config_dir=root / "config", photos_dir=root, videos_dir=root, music_dir=root, output_dir=root / "output")
        self.preview_dir = self.settings.preview_dir
        self.output_dir = self.settings.output_dir
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(root / "db.sqlite")
        self.db.initialize()
        saved = self.db.save_project(sample_payload("Beach trip"))
        self.project_id = saved["id"]
        self.root = root
        self.main.db = self.db
        self.main.settings = self.settings

    def test_preview_download_name(self) -> None:
        name = self.main.preview_download_name("Beach trip", "abcdef1234567890")
        self.assertEqual(name, "Beach trip (preview abcdef).mp4")
        # Illegal characters become dashes; empty names fall back.
        self.assertEqual(self.main.preview_download_name('a<b>:"c', "xyz123"), "a-b---c (preview xyz123).mp4")
        self.assertTrue(self.main.preview_download_name("", "xyz123").startswith("slideshow (preview"))
        # Long names are capped.
        self.assertLessEqual(len(self.main.preview_download_name("x" * 300, "xyz123")), 110)

    def test_get_job_and_list_flag_availability(self) -> None:
        root, db, project_id = self.root, self.db, self.project_id
        preview = self.preview_dir / "p.mp4"
        preview.write_bytes(b"\x00" * 128)
        db.create_job({"id": "j-prev", "project_id": project_id, "kind": "preview", "settings": {}})
        db.update_job("j-prev", status="complete", output_path=str(preview), size_bytes=128)
        db.create_job({"id": "j-gone", "project_id": project_id, "kind": "preview", "settings": {}})
        db.update_job("j-gone", status="complete", output_path=str(self.preview_dir / "deleted.mp4"), size_bytes=128)

        job = self.main.get_job("j-prev")
        self.assertTrue(job["fileAvailable"])
        self.assertEqual(job["fileUrl"], "/api/jobs/j-prev/file")
        self.assertEqual(self.main.get_job("j-gone")["fileAvailable"], False)
        listed = {j["id"]: j for j in self.main.list_jobs(project_id)}
        self.assertTrue(listed["j-prev"]["fileAvailable"])
        self.assertFalse(listed["j-gone"]["fileAvailable"])

    def test_job_file_uses_friendly_name_for_previews(self) -> None:
        root, db, project_id = self.root, self.db, self.project_id
        preview = self.preview_dir / "project-7-preview-ab12cd34.mp4"
        preview.write_bytes(b"\x00" * 64)
        db.create_job({"id": "j-name", "project_id": project_id, "kind": "preview", "settings": {}})
        db.update_job("j-name", status="complete", output_path=str(preview), size_bytes=64)

        response = self.main.job_file("j-name")
        self.assertEqual(response.filename, "Beach trip (preview j-name).mp4")

        # Final renders keep the exact on-disk name the user chose.
        final = self.output_dir / "beach-trip.mp4"
        final.parent.mkdir(parents=True, exist_ok=True)
        final.write_bytes(b"\x00" * 64)
        db.create_job({"id": "j-final", "project_id": project_id, "kind": "render", "settings": {}})
        db.update_job("j-final", status="complete", output_path=str(final), size_bytes=64)
        self.assertEqual(self.main.job_file("j-final").filename, "beach-trip.mp4")

        # A missing file still 404s for the direct route.
        db.create_job({"id": "j-gone2", "project_id": project_id, "kind": "render", "settings": {}})
        db.update_job("j-gone2", status="complete", output_path=str(self.output_dir / "vanished.mp4"), size_bytes=0)
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as ctx:
            self.main.job_file("j-gone2")
        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
