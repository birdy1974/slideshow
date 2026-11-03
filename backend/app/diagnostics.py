"""One snapshot that answers "the render finished, but the picture is wrong".

Every fact in here used to require a shell on the NAS, which is why reports arrive as
adjectives: *scrambled*, *missing*, *still*. What actually decides such a report is
five mechanical questions, and all five can be answered from inside the container:

* which build is running (`revision`) — a stale image and an old cache look identical
  from the browser, and `:latest` is retagged per merge, so the SHA is the only tell;
* which FFmpeg and which encoders it verified (`capabilities`) — a GPU part and a CPU
  part must not meet in one `-c:v copy` stitch, and a build without `ass` degrades
  karaoke to plain fades;
* whether a caption can find its font files (`fonts`);
* whether the volumes a render writes to have room (`disk`) — a full volume is exactly
  a truncated MP4 with a valid-looking header, and the caches used to believe it;
* and, for one named file, what FFmpeg says its streams are and whether it decodes to
  the end (`file`) — colour description included, because a photo tagged `yuvj420p`
  and a movie tagged `yuv420p` in one timeline is the "wrong colours everywhere"
  report, and `docs/timeline-colour-normalisation.md` says what the answer should be.

The caches are reported too: `caches` counts the generated files per directory, their
bytes, and any `.part` left behind by an interrupted transcode — the fingerprint of a
render that was killed, which is the story behind a torn file that kept being served.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from .config import Settings
from .media import safe_path
from .media_probe import describe, stream_lines, whole_video

# Directories holding files the browser plays, which no request re-examines. `label`
# is what a report should call them, `sweep` is what an interrupted write leaves.
CACHES = (
    ("video-previews", "movie proxies"),
    ("filmstrips", "filmstrip sprites"),
    ("transition-previews", "transition gallery"),
    ("text-effect-previews", "text-effect gallery"),
)


def _disk(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
        return {"bytes": usage.total, "free": usage.free}
    except OSError:
        return {"bytes": None, "free": None}


def cache_report(settings: Settings) -> dict[str, Any]:
    """What each generated-file cache holds right now, and whether it looks interrupted."""
    out: dict[str, Any] = {}
    for folder, label in CACHES:
        root = settings.config_dir / folder
        entry: dict[str, Any] = {"label": label, "exists": root.is_dir(), "files": 0, "bytes": 0, "partial": 0}
        if entry["exists"]:
            for item in root.rglob("*"):
                if not item.is_file():
                    continue
                if item.suffix == ".part":
                    entry["partial"] += 1
                    continue
                entry["files"] += 1
                try:
                    entry["bytes"] += item.stat().st_size
                except OSError:
                    pass
        out[folder] = entry
    return out


def file_report(settings: Settings, path: Path) -> dict[str, Any]:
    """FFmpeg's own answer about one file: streams, colour description, completeness."""
    lines = stream_lines(settings.ffmpeg_bin, path)
    report: dict[str, Any] = {"path": str(path), "readable": bool(lines)}
    if not lines:
        report["video"] = report["audio"] = None
        report["complete"] = False
        return report
    report.update(describe(lines))
    report["complete"] = whole_video(settings, path) if path.suffix.lower() in {".mp4", ".m4v", ".mov"} else None
    return report


def diagnose(settings: Settings, renderer: Any, fonts: dict[str, Any], root: str = "", path: str = "") -> dict[str, Any]:
    """The payload for `GET /api/diagnose`. `root`/`path` optionally probe one file.

    A requested file is resolved through the same `safe_path` the media endpoints use,
    so this can describe `/videos/holiday.mp4` or `/output/movie.mp4` and nothing
    outside the mounts — it is a diagnostic, not a file reader.
    """
    roots = dict(settings.media_roots)
    roots["config"] = settings.config_dir
    snapshot: dict[str, Any] = {
        "revision": settings.revision[:12] or "untracked",
        "version": app_version(settings),
        "capabilities": renderer.capabilities(),
        "fonts": fonts,
        "disk": {"output": _disk(settings.output_dir), "config": _disk(settings.config_dir)},
        "caches": cache_report(settings),
    }
    if root or path:
        if root not in roots:
            snapshot["file"] = {"error": f"unknown root {root!r}; expected one of {', '.join(sorted(roots))}"}
        else:
            try:
                target = safe_path(roots[root], path)
            except Exception as exc:  # noqa: BLE001 - a traversal attempt is an answer, not a crash
                snapshot["file"] = {"error": f"could not resolve {path!r}: {exc}"}
            else:
                snapshot["file"] = file_report(settings, target) if target.is_file() else {"error": "no such file", "path": str(target)}
    return snapshot


def app_version(settings: Settings) -> str:
    """The app's own version string, read lazily so this module imports no web layer."""
    try:
        from .main import app  # imported here: main owns the FastAPI instance

        return str(app.version)
    except Exception:  # noqa: BLE001 - diagnostics must never fail because of the web layer
        return "unknown"
