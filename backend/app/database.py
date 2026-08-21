"""SQLite persistence with transactional, lossless project round-tripping.

The normalized child tables make projects inspectable and migration-friendly.
`payload_json` is also retained as the canonical lossless snapshot: adding a UI
setting can never silently discard it before a matching migration is shipped.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schema_version INTEGER NOT NULL,
  name TEXT NOT NULL,
  random_order INTEGER NOT NULL DEFAULT 0,
  timeline_rows TEXT NOT NULL DEFAULT 'auto',
  timeline_zoom REAL NOT NULL DEFAULT 1.0,
  payload_json TEXT NOT NULL,
  revision INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_items (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  media_key TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  name TEXT NOT NULL,
  mounted_path TEXT NOT NULL,
  media_type TEXT NOT NULL,
  duration REAL NOT NULL,
  effect TEXT NOT NULL,
  transition_name TEXT NOT NULL,
  transition_duration REAL NOT NULL,
  text_content TEXT NOT NULL,
  text_mode TEXT NOT NULL,
  text_start REAL NOT NULL,
  text_end REAL NOT NULL,
  text_enter TEXT NOT NULL,
  text_exit TEXT NOT NULL,
  text_enter_duration REAL NOT NULL,
  text_exit_duration REAL NOT NULL,
  text_x REAL NOT NULL,
  text_y REAL NOT NULL,
  frame_background TEXT NOT NULL,
  item_json TEXT NOT NULL,
  PRIMARY KEY(project_id, media_key)
);
CREATE INDEX IF NOT EXISTS idx_media_project_order ON media_items(project_id, sort_order);
CREATE TABLE IF NOT EXISTS text_defaults (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  font_family TEXT NOT NULL,
  font_size REAL NOT NULL,
  font_color TEXT NOT NULL,
  is_bold INTEGER NOT NULL,
  is_italic INTEGER NOT NULL,
  is_underlined INTEGER NOT NULL,
  settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_settings (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  policy TEXT NOT NULL,
  volume REAL NOT NULL,
  fade_out INTEGER NOT NULL,
  settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audio_tracks (
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  track_key TEXT NOT NULL,
  sort_order INTEGER NOT NULL,
  name TEXT NOT NULL,
  mounted_path TEXT NOT NULL,
  duration_text TEXT NOT NULL,
  track_json TEXT NOT NULL,
  PRIMARY KEY(project_id, track_key)
);
CREATE INDEX IF NOT EXISTS idx_audio_project_order ON audio_tracks(project_id, sort_order);
CREATE TABLE IF NOT EXISTS output_settings (
  project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
  resolution TEXT NOT NULL,
  frame_rate TEXT NOT NULL,
  bitrate TEXT NOT NULL,
  encoder TEXT NOT NULL,
  output_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS render_jobs (
  id TEXT PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('preview','render')),
  status TEXT NOT NULL,
  progress REAL NOT NULL DEFAULT 0,
  stage TEXT NOT NULL DEFAULT 'Queued',
  output_path TEXT,
  error_message TEXT,
  log_text TEXT NOT NULL DEFAULT '',
  settings_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_project_created ON render_jobs(project_id, created_at DESC);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


BUSY_TIMEOUT_MS = 30_000
_JOURNAL_MODE_ATTEMPTS = 10


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._write_lock = threading.RLock()
        self._journal_mode_ready = False

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_journal_mode()
        with self.connect(write=True) as conn:
            conn.executescript(MIGRATION_1)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utcnow()),
            )

    def _ensure_journal_mode(self) -> None:
        """Switch the database file to WAL exactly once, never per connection.

        `PRAGMA journal_mode=WAL` needs a brief exclusive lock on the file, so
        running it on every connection turns any concurrent writer (a render job
        updating progress, for instance) into a spurious "database is locked"
        error on plain readers. The journal mode is a property of the file and
        survives across connections, so setting it once at startup is enough.
        """
        if self._journal_mode_ready:
            return
        with self._write_lock:
            if self._journal_mode_ready:
                return
            last_error: sqlite3.OperationalError | None = None
            for attempt in range(_JOURNAL_MODE_ATTEMPTS):
                conn = self._new_connection()
                try:
                    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                    if str(mode).lower() != "wal":
                        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                    if str(mode).lower() == "wal":
                        self._journal_mode_ready = True
                        return
                    last_error = sqlite3.OperationalError(f"journal_mode stayed {mode!r}")
                except sqlite3.OperationalError as exc:  # pragma: no cover - timing dependent
                    last_error = exc
                finally:
                    conn.close()
                time.sleep(min(0.05 * (attempt + 1), 0.5))
            # WAL is an optimisation, not a correctness requirement: a database on
            # a filesystem that cannot support it (some network mounts) still works
            # in the default rollback journal mode with a busy timeout.
            self._journal_mode_ready = True
            if last_error is not None:
                log.warning("Could not enable WAL journal mode for %s: %s", self.path, last_error)

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        # busy_timeout must come first so every later statement waits for a
        # competing writer instead of failing instantly with SQLITE_BUSY.
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    @contextmanager
    def connect(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Open a connection; `write=True` serialises writers in this process."""
        self._ensure_journal_mode()
        if not write:
            conn = self._new_connection()
            try:
                yield conn
            finally:
                conn.close()
            return
        with self._write_lock:
            conn = self._new_connection()
            try:
                yield conn
            finally:
                conn.close()

    def list_projects(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT id,name,revision,created_at,updated_at FROM projects ORDER BY updated_at DESC").fetchall()
        return [dict(row) for row in rows]

    def get_project(self, project_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT payload_json,revision,created_at,updated_at FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"])
        payload["id"] = project_id
        payload["revision"] = row["revision"]
        payload["createdAt"] = row["created_at"]
        payload["updatedAt"] = row["updated_at"]
        return payload

    def save_project(self, payload: dict[str, Any], project_id: int | None = None) -> dict[str, Any]:
        """Save every setting atomically and return the exact persisted snapshot."""
        now = utcnow()
        project = payload.get("project", {})
        timeline = payload.get("timeline", {})
        media = payload.get("media", [])
        text = payload.get("textDefaults", {})
        soundtrack = payload.get("soundtrack", {})
        output = payload.get("output", {})
        canonical = json.loads(json.dumps(payload, ensure_ascii=False))
        canonical.pop("id", None); canonical.pop("revision", None); canonical.pop("createdAt", None); canonical.pop("updatedAt", None)

        with self.connect(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if project_id is None:
                    cur = conn.execute(
                        "INSERT INTO projects(schema_version,name,random_order,timeline_rows,timeline_zoom,payload_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                        (SCHEMA_VERSION, project.get("name", "Untitled"), bool(project.get("randomOrder")), str(timeline.get("rows", "auto")), float(timeline.get("zoom", 1)), json.dumps(canonical, ensure_ascii=False), now, now),
                    )
                    project_id = int(cur.lastrowid)
                else:
                    result = conn.execute(
                        "UPDATE projects SET schema_version=?,name=?,random_order=?,timeline_rows=?,timeline_zoom=?,payload_json=?,revision=revision+1,updated_at=? WHERE id=?",
                        (SCHEMA_VERSION, project.get("name", "Untitled"), bool(project.get("randomOrder")), str(timeline.get("rows", "auto")), float(timeline.get("zoom", 1)), json.dumps(canonical, ensure_ascii=False), now, project_id),
                    )
                    if result.rowcount == 0:
                        raise KeyError(f"Project {project_id} does not exist")

                for table in ("media_items", "text_defaults", "audio_settings", "audio_tracks", "output_settings"):
                    conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))

                for position, item in enumerate(media):
                    conn.execute(
                        """INSERT INTO media_items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (project_id, str(item.get("id", position)), position, item.get("name", ""), item.get("path", ""), item.get("type", "image"), float(item.get("duration", 0)), item.get("effect", "None"), item.get("transition", "Fade"), float(item.get("transitionTime", 0)), item.get("text", ""), item.get("textMode", "overlay"), float(item.get("textStart", 0)), float(item.get("textEnd", item.get("duration", 0))), item.get("textEnter", "Fade"), item.get("textExit", "Fade"), float(item.get("textEnterDuration", .5)), float(item.get("textExitDuration", .5)), float(item.get("textX", 50)), float(item.get("textY", 50)), item.get("frameBackground", "#000000"), json.dumps(item, ensure_ascii=False)),
                    )
                conn.execute(
                    "INSERT INTO text_defaults VALUES(?,?,?,?,?,?,?,?)",
                    (project_id, text.get("fontFamily", "Montserrat"), float(text.get("fontSize", 48)), text.get("fontColor", "#ffffff"), bool(text.get("bold")), bool(text.get("italic")), bool(text.get("underline")), json.dumps(text, ensure_ascii=False)),
                )
                conn.execute(
                    "INSERT INTO audio_settings VALUES(?,?,?,?,?)",
                    (project_id, soundtrack.get("policy", "Loop & trim"), float(soundtrack.get("volume", 100)), bool(soundtrack.get("fadeOut")), json.dumps(soundtrack, ensure_ascii=False)),
                )
                for position, track in enumerate(soundtrack.get("tracks", [])):
                    conn.execute(
                        "INSERT INTO audio_tracks VALUES(?,?,?,?,?,?,?)",
                        (project_id, str(track.get("id", position)), position, track.get("name", ""), track.get("path", ""), str(track.get("duration", "")), json.dumps(track, ensure_ascii=False)),
                    )
                conn.execute(
                    "INSERT INTO output_settings VALUES(?,?,?,?,?,?,?,?)",
                    (project_id, output.get("resolution", "Full HD · 1080p"), output.get("frameRate", "30 fps"), output.get("bitrate", "8 Mbps · High"), output.get("encoder", "Auto · Quick Sync"), output.get("path", "/output"), output.get("filename", "slideshow"), json.dumps(output, ensure_ascii=False)),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        saved = self.get_project(project_id)
        assert saved is not None
        return saved

    def delete_project(self, project_id: int) -> bool:
        with self.connect(write=True) as conn:
            result = conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        return bool(result.rowcount)

    def create_job(self, job: dict[str, Any]) -> None:
        with self.connect(write=True) as conn:
            conn.execute(
                "INSERT INTO render_jobs(id,project_id,kind,status,progress,stage,settings_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                (job["id"], job["project_id"], job["kind"], "queued", 0, "Queued", json.dumps(job.get("settings", {})), utcnow()),
            )

    def update_job(self, job_id: str, **changes: Any) -> None:
        allowed = {"status", "progress", "stage", "output_path", "error_message", "log_text", "started_at", "finished_at"}
        values = {k: v for k, v in changes.items() if k in allowed}
        if not values:
            return
        sql = "UPDATE render_jobs SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?"
        with self.connect(write=True) as conn:
            conn.execute(sql, (*values.values(), job_id))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM render_jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_jobs(self, project_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM render_jobs"; params: tuple[Any, ...] = ()
        if project_id is not None:
            query += " WHERE project_id=?"; params = (project_id,)
        query += " ORDER BY created_at DESC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
