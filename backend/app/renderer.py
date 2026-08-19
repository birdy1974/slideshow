"""FFmpeg slideshow renderer.

The renderer deliberately normalizes every source and xfade result to an
identical frame rate, time base and timestamp origin before chaining the next
transition. This avoids xfade failures with mixed media and FFmpeg builds that
lose frame-rate metadata on intermediate filter outputs.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import Database, utcnow
from .media import UnsafePath, mounted_path

log = logging.getLogger(__name__)

RESOLUTIONS = {
    "4K UHD · 2160p": (3840, 2160), "Full HD · 1080p": (1920, 1080),
    "HD · 720p": (1280, 720), "SD · 480p": (854, 480),
}
XFADE = {
    "Fade":"fade", "Fade black":"fadeblack", "Fade white":"fadewhite", "Fade grays":"fadegrays", "Fade fast":"fadefast", "Fade slow":"fadeslow",
    "Dissolve":"dissolve", "Distance":"distance", "Pixelize":"pixelize", "H blur":"hblur",
    "Wipe left":"wipeleft", "Wipe right":"wiperight", "Wipe up":"wipeup", "Wipe down":"wipedown", "Wipe top-left":"wipetl", "Wipe top-right":"wipetr", "Wipe bottom-left":"wipebl", "Wipe bottom-right":"wipebr",
    "Slide left":"slideleft", "Slide right":"slideright", "Slide up":"slideup", "Slide down":"slidedown", "Smooth left":"smoothleft", "Smooth right":"smoothright", "Smooth up":"smoothup", "Smooth down":"smoothdown",
    "Circle crop":"circlecrop", "Rectangle crop":"rectcrop", "Circle open":"circleopen", "Circle close":"circleclose", "Vertical open":"vertopen", "Vertical close":"vertclose", "Horizontal open":"horzopen", "Horizontal close":"horzclose", "Radial":"radial",
    "Diagonal top-left":"diagtl", "Diagonal top-right":"diagtr", "Diagonal bottom-left":"diagbl", "Diagonal bottom-right":"diagbr", "Horizontal left slice":"hlslice", "Horizontal right slice":"hrslice", "Vertical up slice":"vuslice", "Vertical down slice":"vdslice",
    "Squeeze horizontal":"squeezeh", "Squeeze vertical":"squeezev", "Zoom in":"zoomin", "Horizontal left wind":"hlwind", "Horizontal right wind":"hrwind", "Vertical up wind":"vuwind", "Vertical down wind":"vdwind",
    "Cover left":"coverleft", "Cover right":"coverright", "Cover up":"coverup", "Cover down":"coverdown", "Reveal left":"revealleft", "Reveal right":"revealright", "Reveal up":"revealup", "Reveal down":"revealdown",
}


def parse_number(label: str, fallback: float) -> float:
    match = re.search(r"([\d.]+)", label or "")
    return float(match.group(1)) if match else fallback


def ff_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%").replace("[", r"\[").replace("]", r"\]")


def source_path(settings: Settings, item: dict[str, Any]) -> Path:
    path = str(item.get("path", ""))
    name = str(item.get("name", ""))
    if Path(path).suffix:
        return mounted_path(settings, path)
    return mounted_path(settings, path, name)


def xfade_name(label: str) -> str:
    # GLSL shaders are not portable on the DS918+ software path; use a safe
    # dissolve fallback while retaining the exact requested value in SQLite.
    return XFADE.get(label, "dissolve" if label.startswith("GLSL") else "fade")


def _parse_xfade_help(text: str) -> set[str]:
    """Transition constant names from `ffmpeg -h filter=xfade` output."""
    block = re.search(r"^\s*transition\s+<int>[^\n]*\n(.*?)^\s*duration\s+<duration>", text, re.S | re.M)
    if not block:
        return set()
    return {name for name in re.findall(r"^\s+(\w+)\s+-?\d+\s+\.\.", block.group(1), re.M) if name != "custom"}


def probe_xfade_transitions(ffmpeg_bin: str) -> set[str]:
    """Transitions the installed FFmpeg build actually supports.

    Many NAS packages ship FFmpeg 5.x, whose xfade lacks the wind/cover/reveal
    catalogue. Returns an empty set when detection is impossible.
    """
    try:
        result = subprocess.run([ffmpeg_bin, "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True, timeout=10)
    except Exception:
        return set()
    return _parse_xfade_help(f"{result.stdout or ''}\n{result.stderr or ''}")


class RenderError(RuntimeError):
    pass


class OutputExistsError(RuntimeError):
    """A render target already exists and the user has not acknowledged overwriting it."""


_FFMPEG_ERROR_RE = re.compile(
    r"(error|invalid|failed|no such file|permission denied|does not contain any|"
    r"unrecognized|not found|could not|unable to|no such filter|unknown encoder|conversion failed)",
    re.I,
)


def _short_label(name: str, limit: int = 42) -> str:
    name = name.strip() or "unnamed"
    return name if len(name) <= limit else name[: limit - 1] + "…"


def _ui_path(item: dict[str, Any]) -> str:
    """Project-facing path (`/photos/...`) rather than the host mount path."""
    path = str(item.get("path", "")).replace("\\", "/")
    name = str(item.get("name", ""))
    if Path(path).suffix:
        return path
    if name and (path.endswith("/" + name) or path == name):
        return path
    if path and name:
        return f"{path.rstrip('/')}/{name}"
    return path or name


def _probe_reason_from_ffprobe(output: str, path: Path) -> str:
    """Pick a short, human-readable reason out of ffprobe's stderr/stdout."""
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        for prefix in (f"{path}: ", f"{path.name}: "):
            if line.startswith(prefix):
                line = line[len(prefix):].strip()
        match = re.search(r"Error opening input:\s*(.+)$", line)
        if match:
            return match.group(1).strip()
        return line
    return "unreadable media"


def _probe_readable(path: Path, ffprobe_bin: str) -> str | None:
    """Return a short reason if `path` cannot be read as media, else None.

    Missing and empty (0-byte) files are rejected without spawning ffprobe.
    If ffprobe is not installed the content probe is skipped so a render can
    still start; FFmpeg remains the final judge.
    """
    if not path.exists():
        return "file is missing"
    if not path.is_file():
        return "not a file"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"cannot stat file ({exc})"
    if size == 0:
        return "file is empty (0 bytes)"
    probe = shutil.which(ffprobe_bin)
    if not probe:
        return None
    try:
        result = subprocess.run(
            [probe, "-hide_banner", "-v", "error",
             "-show_entries", "format=format_name",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return "ffprobe timed out"
    except OSError as exc:
        return f"ffprobe could not start ({exc})"
    if result.returncode == 0:
        return None
    return _probe_reason_from_ffprobe(f"{result.stderr or ''}\n{result.stdout or ''}", path)


def _summarize_ffmpeg_log(text: str, *, max_lines: int = 8) -> str:
    """Return the actionable FFmpeg error lines instead of a raw 5k-char tail."""
    tail = text[-12_000:] if len(text) > 12_000 else text
    picked: list[str] = []
    seen: set[str] = set()
    for raw in tail.splitlines():
        line = raw.strip()
        if not line or line.startswith("$ "):
            continue
        if not _FFMPEG_ERROR_RE.search(line):
            continue
        key = line[:200]
        if key in seen:
            continue
        seen.add(key)
        picked.append(line if len(line) <= 240 else line[:237] + "...")
        if len(picked) >= max_lines:
            break
    if picked:
        return "\n".join(picked)
    fallback = [ln.strip() for ln in tail.splitlines() if ln.strip() and not ln.startswith("$ ")]
    return "\n".join(fallback[-6:]) if fallback else "No FFmpeg error details were captured."


def format_ffmpeg_number(value: float) -> str:
    """Stable FFmpeg numeric literal — strips binary float noise (4.999999 → 5)."""
    return f"{round(float(value), 6):g}"


def build_filter_graph(durations: list[float], transitions: list[float], xfade_names: list[str], fps: int | None = None) -> str:
    """Compose transitions after each clip, rather than subtracting them.

    A clip's configured duration is its visible hold time.  Each transition is
    additional timeline time, so four 5-second clips with three 3-second
    transitions produces 29 seconds.  The caller supplies normalized segment
    files with lead-in/lead-out handles for xfade; offsets are calculated from
    the user-facing (hold) durations and the preceding transitions.

    ``fps`` is repeated before every xfade and after every xfade result as a
    CFR guard.  ``setpts=PTS-STARTPTS`` discards frame-rate metadata (FFmpeg
    6+/7+ then reports the link as 1/0), so ``fps`` must come *after* it —
    the last filter before each xfade — to reimpose a constant rate.  Placing
    it first lets ``setpts`` clobber it and every following xfade fails.
    """
    if not durations:
        raise ValueError("build_filter_graph requires at least one clip")
    normalize = f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps}" if fps else "settb=AVTB,setpts=PTS-STARTPTS"
    if len(durations) == 1:
        return f"[0:v]{normalize}[vout]"
    expected = len(durations) - 1
    if len(transitions) != expected or len(xfade_names) != expected:
        raise ValueError("transitions and xfade names must cover every clip pair")
    prepared = [f"[{index}:v]{normalize}[s{index}]" for index in range(len(durations))]
    chains: list[str] = []
    # xfade's offset is measured on its first input.  At each boundary the
    # prior clip has held for its configured duration and all earlier
    # transitions have completed.
    offset = durations[0]
    previous = "[s0]"
    for index in range(1, len(durations)):
        transition = transitions[index - 1]
        out = f"[x{index}]" if index < len(durations) - 1 else "[vout]"
        chains.append(
            f"{previous}[s{index}]xfade=transition={xfade_names[index - 1]}"
            f":duration={format_ffmpeg_number(transition)}"
            f":offset={format_ffmpeg_number(offset)},{normalize}{out}"
        )
        previous = out
        offset += durations[index] + transition
    return ";".join(prepared + chains)


class Renderer:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings
        self.pool = ThreadPoolExecutor(max_workers=settings.render_workers, thread_name_prefix="render")
        self.cancel_events: dict[str, threading.Event] = {}
        self._xfade_supported: set[str] | None = None
        self._xfade_lock = threading.Lock()
        self._qsv_encodable: bool | None = None
        self._qsv_lock = threading.Lock()

    def xfade_supported(self) -> set[str]:
        """Lazily probe (once) which xfade transitions this FFmpeg build has."""
        with self._xfade_lock:
            if self._xfade_supported is None:
                self._xfade_supported = probe_xfade_transitions(self.settings.ffmpeg_bin)
        return self._xfade_supported

    def resolve_xfade(self, label: str) -> str:
        """Map a UI transition to one this FFmpeg can run, degrading safely.

        Older FFmpeg builds (e.g. 5.x on the DS918+) lack wind/cover/reveal
        constants; rather than failing the whole render, fall back to dissolve.
        An empty probe result means detection failed: keep the mapped name.
        """
        name = xfade_name(label)
        supported = self.xfade_supported()
        if not supported or name in supported:
            return name
        fallback = "dissolve" if "dissolve" in supported else sorted(supported)[0]
        log.warning("Transition '%s' (%s) is not supported by this FFmpeg build; falling back to '%s'", label, name, fallback)
        return fallback

    def capabilities(self) -> dict[str, Any]:
        ffmpeg = shutil.which(self.settings.ffmpeg_bin)
        version = None
        if ffmpeg:
            try:
                version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=3).stdout.splitlines()[0]
            except Exception:
                pass
        return {"ffmpeg": bool(ffmpeg), "ffmpegVersion": version, "quickSync": self.qsv_encodable(), "cpuEncoding": bool(ffmpeg)}

    def qsv_encodable(self) -> bool:
        """Whether h264_qsv actually encodes on this host, verified by a probe.

        Merely having /dev/dri/renderD128 is not enough: runtimes differ in the
        rate control modes, pixel formats and resolutions they accept (the
        DS918+ in particular). A tiny test encode mirrors the renderer's
        bitrate-based settings, so "Auto" can pick the working encoder up front.
        """
        with self._qsv_lock:
            if self._qsv_encodable is None:
                self._qsv_encodable = self._probe_qsv()
        return self._qsv_encodable

    def _probe_qsv(self) -> bool:
        if not Path("/dev/dri/renderD128").exists():
            return False
        try:
            result = subprocess.run(
                [self.settings.ffmpeg_bin, "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "color=c=black:s=320x240:r=25",
                 "-frames:v", "12", "-c:v", "h264_qsv", "-b:v", "1M", "-maxrate", "1M", "-bufsize", "2M",
                 "-f", "null", "-"],
                capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def effective_transitions(media: list[dict[str, Any]]) -> list[float]:
        """Transition durations in timeline order.

        Transitions are additional time between clips, not an overlap deducted
        from either configured clip duration.  A small lower bound keeps xfade
        valid while allowing a long transition for a short still image.
        """
        return [max(.05, float(item.get("transitionTime", 1))) for item in media[:-1]]

    def _probe_readable(self, path: Path) -> str | None:
        """Check a single file with ffprobe; see module-level `_probe_readable`."""
        return _probe_readable(path, self.settings.ffprobe_bin)

    def _validate_media(self, project: dict[str, Any]) -> None:
        """Fail fast with one actionable message naming every unreadable input.

        Runs before any encoding so a 0-byte JPEG or a corrupt MP3 does not
        surface as a mid-render crash dumping thousands of FFmpeg log characters.
        Title frames have no file and are skipped.
        """
        problems: list[str] = []
        for item in project.get("media", []):
            if item.get("type") == "title":
                continue
            label = _short_label(str(item.get("name") or "media"))
            try:
                path = source_path(self.settings, item)
            except UnsafePath as exc:
                problems.append(f"{label}: invalid path ({exc}) ({_ui_path(item)})")
                continue
            reason = self._probe_readable(path)
            if reason:
                problems.append(f"{label}: {reason} ({_ui_path(item) or path})")
        for track in project.get("soundtrack", {}).get("tracks", []):
            label = _short_label(str(track.get("name") or "track"))
            try:
                path = source_path(self.settings, track)
            except UnsafePath as exc:
                problems.append(f"soundtrack '{label}': invalid path ({exc}) ({_ui_path(track)})")
                continue
            reason = self._probe_readable(path)
            if reason:
                problems.append(f"soundtrack '{label}': {reason} ({_ui_path(track) or path})")
        if not problems:
            return
        listed = "\n\n".join(f"  {line}" for line in problems)
        raise RenderError(
            "Cannot render — these media files could not be read:\n\n"
            f"{listed}\n\n"
            "Remove or replace them, then try again."
        )

    def render_output_path(self, project: dict[str, Any]) -> Path:
        """Destination MP4 for a final render, shared by submit and render."""
        output_settings = project.get("output", {})
        target_dir = mounted_path(self.settings, str(output_settings.get("path", "/output")))
        stem = Path(str(output_settings.get("filename", "slideshow"))).stem or "slideshow"
        return target_dir / f"{stem}.mp4"

    def render_output_ui_path(self, project: dict[str, Any]) -> str:
        """Project-facing destination (`/output/movie.mp4`) for user messages.

        The host mount path may be a NAS volume like `/volume1/output`; the UI
        always talks in terms of the `/output` mount, so the overwrite prompt
        should echo the path the user actually typed, not the host path.
        """
        output_settings = project.get("output", {})
        folder = str(output_settings.get("path", "/output")).replace("\\", "/").rstrip("/") or "/output"
        stem = Path(str(output_settings.get("filename", "slideshow"))).stem or "slideshow"
        return f"{folder}/{stem}.mp4"

    def submit(self, project_id: int, kind: str, overwrite: bool = False) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project:
            raise KeyError(project_id)
        if kind == "render":
            output = self.render_output_path(project)
            if output.exists() and not overwrite:
                raise OutputExistsError(self.render_output_ui_path(project))
        job_id = uuid.uuid4().hex
        job = {"id": job_id, "project_id": project_id, "kind": kind, "settings": project.get("output", {})}
        self.db.create_job(job)
        event = threading.Event(); self.cancel_events[job_id] = event
        self.pool.submit(self._run, job_id, project, kind, event)
        return self.db.get_job(job_id) or job

    def cancel(self, job_id: str) -> bool:
        event = self.cancel_events.get(job_id)
        if not event:
            return False
        event.set(); self.db.update_job(job_id, status="cancelling", stage="Stopping FFmpeg")
        return True

    def _run(self, job_id: str, project: dict[str, Any], kind: str, cancelled: threading.Event) -> None:
        work = self.settings.work_dir / job_id
        work.mkdir(parents=True, exist_ok=True)
        self.db.update_job(job_id, status="running", stage="Validating media", started_at=utcnow())
        try:
            if not shutil.which(self.settings.ffmpeg_bin):
                raise RenderError("FFmpeg is not installed or is not available on PATH")
            output = self.render(project, kind, work, cancelled, lambda p,s: self.db.update_job(job_id, progress=p, stage=s))
            self.db.update_job(job_id, status="complete", progress=100, stage="Complete", output_path=str(output), finished_at=utcnow())
        except Exception as exc:
            status = "cancelled" if cancelled.is_set() else "failed"
            self.db.update_job(job_id, status=status, stage="Cancelled" if cancelled.is_set() else "Failed", error_message=str(exc), finished_at=utcnow())
            log.exception("Render job %s failed", job_id)
        finally:
            self.cancel_events.pop(job_id, None)

    def _run_ffmpeg(self, command: list[str], cancelled: threading.Event, log_file: Path) -> None:
        with log_file.open("a", encoding="utf-8") as logs:
            logs.write("\n$ " + " ".join(command) + "\n")
            process = subprocess.Popen(command, stdout=logs, stderr=subprocess.STDOUT, text=True)
            while process.poll() is None:
                if cancelled.wait(.2):
                    process.terminate()
                    try: process.wait(5)
                    except subprocess.TimeoutExpired: process.kill()
                    raise RenderError("Render cancelled by user")
            if process.returncode:
                text = log_file.read_text(encoding="utf-8", errors="replace")
                summary = _summarize_ffmpeg_log(text)
                raise RenderError(f"FFmpeg exited with status {process.returncode}.\n{summary}")

    def _text_filter(self, item: dict[str, Any], defaults: dict[str, Any], width: int, height: int) -> str | None:
        text = str(item.get("text", "")).strip()
        if not text:
            return None
        start, end = float(item.get("textStart", 0)), float(item.get("textEnd", item.get("duration", 5)))
        fade_in = max(.01, float(item.get("textEnterDuration", .5))); fade_out = max(.01, float(item.get("textExitDuration", .5)))
        x, y = float(item.get("textX", 50)), float(item.get("textY", 72))
        size = max(8, int(float(defaults.get("fontSize", 48)) * width / 1920))
        colour = str(defaults.get("fontColor", "#ffffff")).replace("#", "0x")
        font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if defaults.get("bold") else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        alpha = f"if(lt(t,{start}),0,if(lt(t,{start+fade_in}),(t-{start})/{fade_in},if(lt(t,{end-fade_out}),1,if(lt(t,{end}),({end}-t)/{fade_out},0))))"
        return f"drawtext=fontfile='{font}':text='{ff_escape(text)}':fontsize={size}:fontcolor={colour}:alpha='{alpha}':x=(w-text_w)*{x/100}:y=(h-text_h)*{y/100}:shadowcolor=black@0.55:shadowx=2:shadowy=2:enable='between(t,{start},{end})'"

    def render(self, project: dict[str, Any], kind: str, work: Path, cancelled: threading.Event, progress: Callable[[float,str],None]) -> Path:
        media = list(project.get("media", []))
        if project.get("project", {}).get("randomOrder"):
            import random
            random.shuffle(media)
        if not media:
            raise RenderError("The project contains no media")
        self._validate_media({**project, "media": media})
        output_settings = project.get("output", {})
        if kind == "preview":
            width, height, fps, bitrate = 854, 480, 24, "2M"
        else:
            width, height = RESOLUTIONS.get(output_settings.get("resolution"), (1920, 1080))
            fps = int(parse_number(output_settings.get("frameRate", "30"), 30))
            bitrate = f"{parse_number(output_settings.get('bitrate', '8'), 8):g}M"
        defaults = project.get("textDefaults", {})
        log_file = work / "ffmpeg.log"
        progress(1, "Preparing soundtrack")
        soundtrack = self._make_soundtrack(project, work, cancelled, log_file)
        if soundtrack and project.get("soundtrack",{}).get("policy") == "Fit slideshow to audio":
            audio_duration = self._probe_duration(soundtrack)
            transition_total = sum(self.effective_transitions(media))
            duration_total = sum(max(.2, float(x.get("duration",5))) for x in media)
            if audio_duration > transition_total and duration_total > 0:
                # Holds scale to fill the audio; transitions retain their set duration.
                factor = (audio_duration - transition_total) / duration_total
                media = [{**item,"duration":max(.2,float(item.get("duration",5))*factor),"textEnd":min(float(item.get("textEnd",item.get("duration",5)))*factor,max(.2,float(item.get("duration",5))*factor))} for item in media]
        segments: list[Path] = []
        durations = [max(.2, float(item.get("duration", 5))) for item in media]
        transitions = self.effective_transitions(media)
        # Give xfade incoming/outgoing handles while preserving every configured
        # clip hold in full.  These handles make transition time additive.
        segment_durations = [
            duration + (transitions[index - 1] if index else 0) + (transitions[index] if index < len(transitions) else 0)
            for index, duration in enumerate(durations)
        ]
        progress(2, "Normalizing media")
        for index, item in enumerate(media):
            if cancelled.is_set(): raise RenderError("Render cancelled by user")
            duration = segment_durations[index]
            segment = work / f"segment-{index:04d}.mp4"
            kind_name = item.get("type", "image")
            base_filter = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}"
            command = [self.settings.ffmpeg_bin, "-hide_banner", "-y"]
            clip_t = format_ffmpeg_number(duration)
            if kind_name == "title":
                background = str(item.get("frameBackground", "#202020"))
                if not background.startswith("#"): background = "#30382a"
                command += ["-f", "lavfi", "-i", f"color=c={background}:s={width}x{height}:r={fps}:d={clip_t}"]
            else:
                source = source_path(self.settings, item)
                if not source.exists(): raise RenderError(f"Media file is missing: {source}")
                # Still images default to 25 fps. Without an explicit -framerate
                # matching the output, `-t 5` yields 125 frames which fps=30
                # then shortens to ~4.17s and every xfade offset is late.
                if kind_name == "image": command += ["-loop", "1", "-framerate", str(fps), "-t", clip_t, "-i", str(source)]
                else: command += ["-stream_loop", "-1", "-t", clip_t, "-i", str(source)]
            filters = [base_filter]
            effect = str(item.get("effect", ""))
            if kind_name == "image" and effect.startswith("Ken Burns"):
                delta = "0.0008" if "Zoom in" in effect else "-0.0008" if "Zoom out" in effect else "0.0003"
                start_zoom = "1" if delta.startswith("0") else "1.12"
                filters.append(f"zoompan=z='max(1,min(1.12,{start_zoom}+on*{delta}))':d=1:s={width}x{height}:fps={fps}")
            text_filter = self._text_filter(item, defaults, width, height)
            if text_filter: filters.append(text_filter)
            filters += ["format=yuv420p", "settb=AVTB", "setpts=PTS-STARTPTS"]
            command += ["-vf", ",".join(filters), "-an", "-t", clip_t, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(segment)]
            self._run_ffmpeg(command, cancelled, log_file)
            segments.append(segment)
            progress(5 + 45 * (index + 1) / len(media), f"Prepared item {index+1} of {len(media)}")

        inputs: list[str] = []
        for segment in segments: inputs += ["-i", str(segment)]
        xfade_names = [self.resolve_xfade(str(media[index].get("transition", "Fade"))) for index in range(len(media) - 1)]
        filter_graph = build_filter_graph(durations, transitions, xfade_names, fps)
        total_duration = sum(durations) + sum(transitions)

        audio_args: list[str] = []; audio_map: list[str] = []
        if soundtrack:
            audio_index=len(segments); policy=project.get("soundtrack",{}).get("policy","Loop & trim")
            if policy == "Loop & trim": audio_args += ["-stream_loop", "-1"]
            audio_args += ["-i", str(soundtrack)]
            volume=max(0,min(1,float(project.get("soundtrack",{}).get("volume",100))/100))
            fade = project.get("soundtrack",{}).get("fadeOut",True)
            af=f"volume={volume}"
            if fade: af += f",afade=t=out:st={format_ffmpeg_number(max(0,total_duration-2))}:d=2"
            filter_graph += f";[{audio_index}:a]{af}[aout]"
            audio_map=["-map","[aout]","-c:a","aac","-b:a","192k"]
        progress(55, "Composing transitions and soundtrack")
        if kind == "preview":
            target_dir = self.settings.preview_dir
            filename = f"project-{project.get('id','new')}-preview-{uuid.uuid4().hex[:8]}.mp4"
        else:
            output = self.render_output_path(project)
            target_dir = output.parent
            filename = output.name
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / filename
        encoder_label = str(output_settings.get("encoder", "Auto"))
        bitrate_value = parse_number(bitrate, 2)
        encoder = "h264_qsv" if "Quick Sync" in encoder_label and self.qsv_encodable() else "libx264"

        def compose_command(codec: str) -> list[str]:
            # QSV needs nv12 and its own rate control tolerance; libx264 uses
            # yuv420p plus a medium preset. Everything else is shared.
            if codec == "h264_qsv":
                encode_args = ["-c:v", codec, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_value * 2:g}M", "-pix_fmt", "nv12"]
            else:
                encode_args = ["-c:v", codec, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_value * 2:g}M", "-preset", "medium", "-pix_fmt", "yuv420p"]
            return [self.settings.ffmpeg_bin, "-hide_banner", "-y", *inputs, *audio_args, "-filter_complex", filter_graph, "-map", "[vout]", *audio_map, *encode_args, "-movflags", "+faststart", "-r", str(fps), "-t", str(total_duration), str(output)]

        command = compose_command(encoder)
        try:
            self._run_ffmpeg(command, cancelled, log_file)
        except RenderError:
            if encoder != "h264_qsv":
                raise
            # Quick Sync runtimes differ: rate control modes, pixel formats and
            # resolutions accepted by one device fail on another. Never let that
            # break a render — retry the identical composition on CPU.
            progress(70, "Quick Sync unavailable; retrying on CPU")
            log.warning("h264_qsv failed for %s; falling back to libx264", output)
            self._run_ffmpeg(compose_command("libx264"), cancelled, log_file)
        progress(98, "Finalizing MP4")
        return output

    def _probe_duration(self, path: Path) -> float:
        result=subprocess.run([self.settings.ffprobe_bin,"-v","error","-show_entries","format=duration","-of","json",str(path)],capture_output=True,text=True,timeout=30)
        if result.returncode: raise RenderError(f"Could not probe duration of {path.name}: {result.stderr}")
        return float(json.loads(result.stdout)["format"]["duration"])

    def _make_soundtrack(self, project: dict[str,Any], work: Path, cancelled: threading.Event, log_file: Path) -> Path | None:
        tracks=project.get("soundtrack",{}).get("tracks",[])
        if not tracks: return None
        sources=[]
        for track in tracks:
            path=str(track.get("path","")); name=str(track.get("name",""))
            source=mounted_path(self.settings,path) if Path(path).suffix else mounted_path(self.settings,path,name)
            if not source.exists(): raise RenderError(f"Soundtrack is missing: {source}")
            sources.append(source)
        output=work/"soundtrack.m4a"
        inputs=[]
        for source in sources: inputs += ["-i",str(source)]
        normalized=";".join(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]" for i in range(len(sources)))
        concat="".join(f"[a{i}]" for i in range(len(sources)))+f"concat=n={len(sources)}:v=0:a=1[aout]"
        command=[self.settings.ffmpeg_bin,"-hide_banner","-y",*inputs,"-filter_complex",normalized+";"+concat,"-map","[aout]","-vn","-c:a","aac","-b:a","192k",str(output)]
        self._run_ffmpeg(command,cancelled,log_file)
        return output
