"""Slideshow API and production single-page application server."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
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
from .media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, UnsafePath, browse, mounted_path, safe_path, source_path
from .renderer import OutputExistsError, Renderer
from .transition_previews import PreviewUnavailable, TransitionPreviewCache, slugify

logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

db = Database(settings.database_path)
renderer = Renderer(db, settings)
transition_previews = TransitionPreviewCache(settings, renderer)


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
    # Project-wide editor defaults: ``slideSeconds`` (hold of every new photo /
    # text frame) and ``transitionSeconds`` (length of every new transition).
    defaults: dict[str, Any] = Field(default_factory=dict)


class JobRequest(BaseModel):
    kind: Literal["preview", "render"] = "render"
    overwrite: bool = False


class TransitionPreviewRequest(BaseModel):
    outgoing: dict[str, Any]
    incoming: dict[str, Any]
    transition: str = "Fade"
    duration: float = Field(default=1.0, ge=0.1, le=10)
    textDefaults: dict[str, Any] = Field(default_factory=dict)
    transitionParams: dict[str, Any] | None = None
    transitionEasing: str | None = None
    transitionReverse: int | None = None
    easing: str | None = None
    reverse: int | None = None
    params: dict[str, Any] | None = None


def validate_mount_references(payload: dict[str, Any]) -> None:
    try:
        mounted_path(settings, str(payload.get("output", {}).get("path", "/output")))
    except UnsafePath as exc:
        raise HTTPException(422, f"Invalid output path: {exc}") from exc
    for item in payload.get("media", []):
        if item.get("type") == "title":
            continue
        try:
            source_path(settings, item)
        except UnsafePath as exc:
            raise HTTPException(422, f"Invalid media path for {item.get('name','item')}: {exc}") from exc
    for track in payload.get("soundtrack", {}).get("tracks", []):
        try:
            source_path(settings, track)
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
    # Fill the ffmpeg/Quick Sync capability caches in the background so
    # /api/health answers instantly and never spawns a probe itself.
    renderer.warm_capabilities()
    yield
    renderer.pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Slideshow", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"], allow_methods=["*"], allow_headers=["*"])


@app.get("/api/ping")
async def ping() -> dict[str, str]:
    """Liveness probe for Docker/Portainer health checks.

    Deliberately dependency-free — no subprocess, no database, no locks — and
    async so it runs directly on the event loop: even while a render
    saturates the CPUs this answers in milliseconds. The richer /api/health
    report is meant for the UI, not for probes.
    """
    return {"status": "ok", "version": app.version}


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
    def _wipe() -> None:
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM projects")
    db._run_with_busy_retry(_wipe)
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
    def _wipe_jobs() -> None:
        with db.connect(write=True) as conn:
            conn.execute("DELETE FROM render_jobs")
    db._run_with_busy_retry(_wipe_jobs)

    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "work_dir": str(settings.work_dir),
        "preview_dir": str(settings.preview_dir),
    }


@app.post("/api/output/clear")
@app.delete("/api/output")
def clear_output_directory(path: str = Query(default="/output")) -> dict[str, Any]:
    """Delete all contents inside the output directory (or a subfolder inside /output)."""
    import shutil
    try:
        target = mounted_path(settings, path)
    except UnsafePath as exc:
        raise HTTPException(400, f"Invalid output path: {exc}") from exc

    try:
        resolved_target = target.resolve()
        resolved_output = settings.output_dir.resolve()
        if resolved_target != resolved_output and resolved_output not in resolved_target.parents:
            raise HTTPException(403, "Target path is outside the output directory")
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc

    if not target.exists():
        return {"deleted_files": 0, "deleted_dirs": 0, "path": str(target)}

    deleted_files = 0
    deleted_dirs = 0

    if target.is_dir():
        for item in list(target.iterdir()):
            try:
                if item.is_dir():
                    shutil.rmtree(item)
                    deleted_dirs += 1
                else:
                    item.unlink()
                    deleted_files += 1
            except Exception as e:
                log.warning(f"Could not delete {item}: {e}")
    elif target.is_file():
        try:
            target.unlink()
            deleted_files += 1
        except Exception as e:
            log.warning(f"Could not delete {target}: {e}")

    return {
        "deleted_files": deleted_files,
        "deleted_dirs": deleted_dirs,
        "path": str(target),
    }


@app.get("/api/media/browse")
def browse_media(root: str = Query(pattern="^(photos|videos|music|output)$"), path: str = "", folders: bool = False) -> dict[str, Any]:
    try: return browse(settings, root, path, folders_only=folders)
    except UnsafePath as exc: raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc: raise HTTPException(404, f"Folder not found: {exc}") from exc
    except PermissionError as exc: raise HTTPException(403, str(exc)) from exc


STREAMABLE_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


@app.get("/api/media/probe")
def probe_media(root: str = Query(pattern="^(photos|videos)$"), path: str = "") -> dict[str, Any]:
    """Read video metadata server-side, including formats browsers cannot decode.

    Casio EX-Z11 movies are Motion JPEG/PCM in an AVI container. FFmpeg can
    render those files, but most current browsers cannot even read their
    duration, so the media picker uses this endpoint as its metadata fallback.
    """
    try: target = safe_path(settings.media_roots[root], path)
    except (UnsafePath, KeyError) as exc: raise HTTPException(400, f"Invalid media path: {exc}") from exc
    if not target.is_file(): raise HTTPException(404, "Media file not found")
    if target.suffix.lower() not in VIDEO_EXTENSIONS: raise HTTPException(415, "File is not a supported video")
    if target.stat().st_size == 0: raise HTTPException(422, "File is empty (0 bytes)")
    try:
        result = subprocess.run(
            [settings.ffprobe_bin, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(target)],
            capture_output=True, text=True, timeout=settings.ffprobe_timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, f"Could not inspect video metadata: {exc}") from exc
    if result.returncode:
        raise HTTPException(422, "FFmpeg could not read this video")
    try: duration = float(json.loads(result.stdout).get("format", {}).get("duration", 0))
    except (TypeError, ValueError, json.JSONDecodeError): duration = 0
    if duration <= 0: raise HTTPException(422, "Video duration is unavailable")
    return {"duration": duration}


@app.get("/api/media/loudness")
def media_loudness(root: str = Query(pattern="^(music|videos|photos)$"), path: str = "", start: float = Query(default=0, ge=0), end: float = Query(default=0, ge=0)) -> dict[str, Any]:
    """Measure integrated loudness (EBU R128) of an audio file's kept region.

    Used by the Soundtracks pane to show which songs are louder or quieter
    before rendering. `start`/`end` mirror the track editor's cut points.
    """
    try: target = safe_path(settings.media_roots[root], path)
    except (UnsafePath, KeyError) as exc: raise HTTPException(400, f"Invalid media path: {exc}") from exc
    if not target.is_file(): raise HTTPException(404, "Media file not found")
    if target.suffix.lower() not in AUDIO_EXTENSIONS | VIDEO_EXTENSIONS: raise HTTPException(415, "File has no supported audio")
    if target.stat().st_size == 0: raise HTTPException(422, "File is empty (0 bytes)")
    from .renderer import track_edit_filter
    edit = track_edit_filter({"trimStart": start, "trimEnd": end})
    stats = renderer.measure_loudness(target, -14.0, edit_filter=edit)
    if not stats or abs(stats["input_i"]) > 1e6:
        raise HTTPException(422, "Could not measure loudness (silent or unreadable audio)")
    return {"integrated": round(stats["input_i"], 1), "truePeak": round(stats["input_tp"], 1), "range": round(stats["input_lra"], 1)}


@app.get("/api/media/cropdetect")
def media_cropdetect(
    root: str = Query(pattern="^(photos|videos)$"),
    path: str = "",
    rotation: int = Query(default=0),
    seconds: float = Query(default=4.0, ge=0.5, le=30.0),
) -> dict[str, Any]:
    """One-click "remove the black bars": let FFmpeg propose a crop rectangle.

    `cropdetect` scans the first frames for near-black borders — letterboxed
    movies, scanned photos with a dark edge, a slide filmed off a projector.
    The rectangle comes back as fractions of the picture **after** the item's
    quarter turn (`rotation` is applied before detecting, so width and height
    swap for 90/270), which is the same space the crop editor works in: the
    proposal can be dropped straight into `item.crop.rect`.

    Nothing is written: this only measures. The editor still decides.
    """
    try: target = safe_path(settings.media_roots[root], path)
    except (UnsafePath, KeyError) as exc: raise HTTPException(400, f"Invalid media path: {exc}") from exc
    if not target.is_file(): raise HTTPException(404, "Media file not found")
    if target.suffix.lower() not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
        raise HTTPException(415, "File is not a supported picture or movie")
    if target.stat().st_size == 0: raise HTTPException(422, "File is empty (0 bytes)")
    from .picture_crop import cropdetect_command, parse_cropdetect
    command = cropdetect_command(settings.ffmpeg_bin, str(target), rotation, seconds)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=settings.ffprobe_timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(503, f"Could not scan this file for bars: {exc}") from exc
    detected = parse_cropdetect(result.stderr or "", rotation)
    if detected is None:
        # cropdetect stays silent when every frame is dark or nothing decodes.
        raise HTTPException(422, "FFmpeg could not measure this file — set the crop by hand")
    return detected


@app.get("/api/media/file")
def media_file(root: str = Query(pattern="^(photos|videos|music)$"), path: str = "") -> FileResponse:
    """Stream a media file from a read-only mount for thumbnails, lightbox, and MP3 preview.

    Filenames may contain spaces, underscores, dashes, parentheses and other
    ordinary characters — they are not sanitized. The response is inline so
    ``<img>``/``<video>``/``<audio>`` tags can display it (attachment would
    hide thumbnails in several browsers).
    """
    try: target = safe_path(settings.media_roots[root], path)
    except (UnsafePath, KeyError) as exc: raise HTTPException(400, f"Invalid media path: {exc}") from exc
    try:
        is_file = target.is_file()
        size = target.stat().st_size if is_file else 0
    except PermissionError as exc:
        raise HTTPException(403, f"No permission to read this file: {target.name}") from exc
    if not is_file: raise HTTPException(404, "Media file not found")
    if target.suffix.lower() not in STREAMABLE_EXTENSIONS: raise HTTPException(403, "File type is not streamable")
    if size == 0:
        # A 0-byte file streams an empty body, which shows up in the UI as a
        # silently broken thumbnail/lightbox. Fail loudly so the frontend can
        # render a clear "unavailable" placeholder instead.
        raise HTTPException(422, "File is empty (0 bytes) — remove or replace it")
    return FileResponse(
        target,
        media_type=mimetypes.guess_type(target.name)[0] or "application/octet-stream",
        filename=target.name,
        content_disposition_type="inline",
    )


@app.get("/api/transition-previews/status")
def transition_preview_status() -> dict[str, Any]:
    """Which catalogue entries already have a cached example clip.

    The picker calls this once when it opens so it can show placeholders for
    clips that are still missing instead of firing 191 requests at once.
    """
    return transition_previews.status()


@app.post("/api/transition-previews/build")
def transition_preview_build() -> dict[str, Any]:
    """Start (or report on) a background pass that renders every missing clip."""
    return transition_previews.build_all()


@app.delete("/api/transition-previews")
def transition_preview_clear() -> dict[str, Any]:
    """Drop every cached clip so the catalogue can be re-rendered."""
    transition_previews.clear()
    return transition_previews.status()


@app.get("/api/transition-previews/{slug}")
def transition_preview_file(slug: str) -> FileResponse:
    """Cached example clip for a transition label, rendered on first request.

    ``slug`` is the slugs of the friendly UI label (``GL · Angular`` ->
    ``gl-angular``); a trailing ``.mp4`` is accepted and ignored.
    """
    name = slug[:-4] if slug.lower().endswith(".mp4") else slug
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", name or ""):
        raise HTTPException(404, "Unknown transition preview")
    entry = next((x for x in transition_previews.catalogue() if x["slug"] == name), None)
    if not entry:
        raise HTTPException(404, "Unknown transition preview")
    try:
        path = transition_previews.ensure(entry["label"], entry["params"])
    except PreviewUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - a failed probe must not 500 the picker
        log.exception("Could not build a preview for %s", entry["label"])
        raise HTTPException(500, f"Could not render preview: {exc}") from exc
    return FileResponse(path, media_type="video/mp4", filename=f"{name}.mp4", content_disposition_type="inline")


@app.post("/api/transitions/preview")
def transition_preview(request: TransitionPreviewRequest) -> FileResponse:
    """Render an authoritative two-clip transition sample at 360p.

    The sample is the transition alone: the renderer drops the hold of every
    `previewTrim` item, so the clip is exactly as long as the requested
    duration and starts and ends on the crossfade — no static picture on
    either side.  The sources are still cut to short handles, which keeps
    this interactive even when either one is a long movie or camera AVI.
    """
    import threading
    import uuid

    handle = max(1.0, request.duration)
    media = [dict(request.outgoing), dict(request.incoming)]
    # Collect easing/reverse/params from several accepted field names (frontend may send either)
    preview_params = request.transitionParams or request.params or {}
    preview_easing = request.transitionEasing or request.easing
    preview_reverse = request.transitionReverse if request.transitionReverse is not None else request.reverse
    media[0].update(duration=handle, transition=request.transition, transitionTime=request.duration, previewTrim=True, transitionParams=preview_params, transitionEasing=preview_easing, transitionReverse=preview_reverse)
    media[1].update(duration=handle, previewTrim=True)
    payload = {
        "id": "transition",
        "project": {"name": "Transition preview", "randomOrder": False},
        "media": media,
        "textDefaults": request.textDefaults,
        "soundtrack": {"tracks": [], "policy": "Play once, then silence"},
        "output": {"encoder": "CPU · x264"},
    }
    validate_mount_references({**payload, "output": {"path": "/output"}})
    token = uuid.uuid4().hex
    work = settings.work_dir / f"transition-{token}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        output = renderer.render(payload, "preview", work, threading.Event(), lambda _p, _s: None)
    except Exception as exc:
        # Interim segment files are no longer needed once the render stops.
        shutil.rmtree(work, ignore_errors=True)
        log.exception("Transition preview failed")
        raise HTTPException(422, f"Could not render transition preview: {exc}") from exc
    # The proxy MP4 is served straight from preview_dir; its interim segments
    # in the work dir are temporary and can be removed right away.
    shutil.rmtree(work, ignore_errors=True)
    return FileResponse(output, media_type="video/mp4", filename="transition-preview.mp4", content_disposition_type="inline")


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
    try:
        job = db.get_job(job_id)
    except Exception as exc:
        # Never surface a raw sqlite lock as a 500 to the progress poller —
        # the client retries every second and a transient lock should just
        # wait, not abort the whole render UX.
        log.warning("get_job(%s) failed: %s", job_id, exc)
        raise HTTPException(503, "Database temporarily unavailable; retry shortly") from exc
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
