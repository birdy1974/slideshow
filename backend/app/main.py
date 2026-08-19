"""Slideshow API and production single-page application server."""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

import mimetypes

from .config import settings
from .database import Database
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, UnsafePath, browse, mounted_path, safe_path
from .renderer import OutputExistsError, Renderer

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

db = Database(settings.database_path)
renderer = Renderer(db, settings)


class ProjectPayload(BaseModel):
    """Flexible envelope; payload_json guarantees lossless future settings."""
    model_config = ConfigDict(extra="allow")
    schemaVersion: int = 1
    project: dict[str, Any]
    media: list[dict[str, Any]] = Field(default_factory=list)
    textDefaults: dict[str, Any] = Field(default_factory=dict)
    soundtrack: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    timeline: dict[str, Any] = Field(default_factory=dict)


class JobRequest(BaseModel):
    kind: Literal["preview", "render"] = "render"
    overwrite: bool = False


def validate_mount_references(payload: dict[str, Any]) -> None:
    try:
        mounted_path(settings, str(payload.get("output", {}).get("path", "/output")))
    except UnsafePath as exc:
        raise HTTPException(422, f"Invalid output path: {exc}") from exc
    for item in payload.get("media", []):
        if item.get("type") == "title":
            continue
        try:
            mounted_path(settings, str(item.get("path", "")), "" if Path(str(item.get("path", ""))).suffix else str(item.get("name", "")))
        except UnsafePath as exc:
            raise HTTPException(422, f"Invalid media path for {item.get('name','item')}: {exc}") from exc
    for track in payload.get("soundtrack", {}).get("tracks", []):
        try:
            mounted_path(settings, str(track.get("path", "")), "" if Path(str(track.get("path", ""))).suffix else str(track.get("name", "")))
        except UnsafePath as exc:
            raise HTTPException(422, f"Invalid soundtrack path for {track.get('name','track')}: {exc}") from exc


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.config_dir.mkdir(parents=True, exist_ok=True)
    settings.work_dir.mkdir(parents=True, exist_ok=True)
    settings.preview_dir.mkdir(parents=True, exist_ok=True)
    settings.output_dir.mkdir(parents=True, exist_ok=True)
    db.initialize()
    log.info("Database ready at %s", settings.database_path)
    yield
    renderer.pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Slideshow", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/health")
def health() -> dict[str, Any]:
    capabilities = renderer.capabilities()
    return {"status": "ok", "database": str(settings.database_path), "capabilities": capabilities, "version": app.version}


@app.get("/api/projects")
def list_projects() -> list[dict[str, Any]]:
    return db.list_projects()


@app.post("/api/projects", status_code=201)
def create_project(payload: ProjectPayload) -> dict[str, Any]:
    value = payload.model_dump(mode="json")
    validate_mount_references(value)
    return db.save_project(value)


@app.get("/api/projects/{project_id}")
def get_project(project_id: int) -> dict[str, Any]:
    project = db.get_project(project_id)
    if not project: raise HTTPException(404, "Project not found")
    return project


@app.put("/api/projects/{project_id}")
def update_project(project_id: int, payload: ProjectPayload) -> dict[str, Any]:
    value = payload.model_dump(mode="json")
    validate_mount_references(value)
    try: return db.save_project(value, project_id)
    except KeyError as exc: raise HTTPException(404, str(exc)) from exc


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int) -> dict[str, bool]:
    if not db.delete_project(project_id): raise HTTPException(404, "Project not found")
    return {"deleted": True}


@app.delete("/api/projects")
def delete_all_projects() -> dict[str, bool]:
    """Delete all projects from the database."""
    with db.connect() as conn:
        conn.execute("DELETE FROM projects")
    return {"deleted": True}


@app.post("/api/cleanup")
def cleanup_temporary_files() -> dict[str, Any]:
    """Clear all temporary files (work dir, preview dir) and jobs."""
    import shutil
    deleted_files = 0
    deleted_dirs = 0
    
    # Delete work directory contents
    if settings.work_dir.exists():
        for item in settings.work_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                    deleted_dirs += 1
                else:
                    item.unlink()
                    deleted_files += 1
            except Exception as e:
                log.warning(f"Could not delete {item}: {e}")
    
    # Delete preview directory contents
    if settings.preview_dir.exists():
        for item in settings.preview_dir.iterdir():
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                    deleted_dirs += 1
                else:
                    item.unlink()
                    deleted_files += 1
            except Exception as e:
                log.warning(f"Could not delete {item}: {e}")
    
    # Delete all jobs from database
    with db.connect() as conn:
        conn.execute("DELETE FROM render_jobs")
    
    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "work_dir": str(settings.work_dir),
        "preview_dir": str(settings.preview_dir),
    }


@app.get("/api/media/browse")
def browse_media(root: str = Query(pattern="^(photos|videos|music|output)$"), path: str = "", folders: bool = False) -> dict[str, Any]:
    try: return browse(settings, root, path, folders_only=folders)
    except UnsafePath as exc: raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc: raise HTTPException(404, f"Folder not found: {exc}") from exc


STREAMABLE_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


@app.get("/api/media/file")
def media_file(root: str = Query(pattern="^(photos|videos|music)$"), path: str = "") -> FileResponse:
    """Stream a media file from a read-only mount, e.g. to preview an MP3 in the browser."""
    try: target = safe_path(settings.media_roots[root], path)
    except (UnsafePath, KeyError) as exc: raise HTTPException(400, f"Invalid media path: {exc}") from exc
    if target.suffix.lower() not in STREAMABLE_EXTENSIONS: raise HTTPException(403, "File type is not streamable")
    if not target.is_file(): raise HTTPException(404, "Media file not found")
    return FileResponse(target, media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream", filename=target.name)


@app.post("/api/projects/{project_id}/jobs", status_code=202)
def create_job(project_id: int, request: JobRequest) -> dict[str, Any]:
    try: return renderer.submit(project_id, request.kind, overwrite=request.overwrite)
    except KeyError as exc: raise HTTPException(404, "Project not found") from exc
    except OutputExistsError as exc:
        raise HTTPException(409, detail={"code": "output_exists", "path": str(exc)}) from exc


@app.get("/api/jobs")
def list_jobs(project_id: int | None = None) -> list[dict[str, Any]]:
    return db.list_jobs(project_id)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = db.get_job(job_id)
    if not job: raise HTTPException(404, "Render job not found")
    if job.get("output_path"): job["fileUrl"] = f"/api/jobs/{job_id}/file"
    return job


@app.post("/api/jobs/{job_id}/cancel", status_code=202)
def cancel_job(job_id: str) -> dict[str, str]:
    if not renderer.cancel(job_id): raise HTTPException(409, "Job is not running")
    return {"status": "cancelling"}


@app.get("/api/jobs/{job_id}/file")
def job_file(job_id: str) -> FileResponse:
    job = db.get_job(job_id)
    if not job or not job.get("output_path"): raise HTTPException(404, "Output is not available")
    path = Path(job["output_path"]).resolve()
    allowed = [settings.output_dir.resolve(), settings.preview_dir.resolve()]
    if not any(path == root or root in path.parents for root in allowed): raise HTTPException(403, "Output path is outside an allowed root")
    if not path.is_file(): raise HTTPException(404, "Output file is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/jobs/{job_id}/log")
def job_log(job_id: str) -> dict[str, str]:
    job = db.get_job(job_id)
    if not job: raise HTTPException(404, "Render job not found")
    path = settings.work_dir / job_id / "ffmpeg.log"
    return {"log": path.read_text(encoding="utf-8", errors="replace")[-100_000:] if path.exists() else "No FFmpeg output yet."}


# The production image copies the Vite build here. API routes are registered
# first so the SPA fallback never shadows them.
static_dir = Path(os.getenv("STATIC_DIR", "/app/static"))
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        candidate = (static_dir / path).resolve()
        if candidate.is_file() and static_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")
