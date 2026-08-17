"""FFmpeg slideshow renderer.

The renderer deliberately normalizes every source to identical dimensions,
frame rate, time base and pixel format before chaining xfade. This avoids the
most common xfade failures with mixed phone photos and videos.
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
from .media import mounted_path

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


class RenderError(RuntimeError):
    pass


class Renderer:
    def __init__(self, db: Database, settings: Settings):
        self.db, self.settings = db, settings
        self.pool = ThreadPoolExecutor(max_workers=settings.render_workers, thread_name_prefix="render")
        self.cancel_events: dict[str, threading.Event] = {}

    def capabilities(self) -> dict[str, Any]:
        ffmpeg = shutil.which(self.settings.ffmpeg_bin)
        qsv = Path("/dev/dri/renderD128").exists()
        version = None
        if ffmpeg:
            try:
                version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True, timeout=3).stdout.splitlines()[0]
            except Exception:
                pass
        return {"ffmpeg": bool(ffmpeg), "ffmpegVersion": version, "quickSync": qsv, "cpuEncoding": bool(ffmpeg)}

    def submit(self, project_id: int, kind: str) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if not project:
            raise KeyError(project_id)
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
                tail = log_file.read_text(encoding="utf-8", errors="replace")[-5000:]
                raise RenderError(f"FFmpeg exited with status {process.returncode}.\n{tail}")

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
            transition_total = sum(float(x.get("transitionTime",1)) for x in media[:-1])
            duration_total = sum(float(x.get("duration",5)) for x in media)
            if audio_duration > transition_total and duration_total > 0:
                factor = (audio_duration + transition_total) / duration_total
                media = [{**item,"duration":max(.2,float(item.get("duration",5))*factor),"textEnd":min(float(item.get("textEnd",item.get("duration",5)))*factor,max(.2,float(item.get("duration",5))*factor))} for item in media]
        segments: list[Path] = []
        progress(2, "Normalizing media")
        for index, item in enumerate(media):
            if cancelled.is_set(): raise RenderError("Render cancelled by user")
            duration = max(.2, float(item.get("duration", 5)))
            segment = work / f"segment-{index:04d}.mp4"
            kind_name = item.get("type", "image")
            base_filter = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}"
            command = [self.settings.ffmpeg_bin, "-hide_banner", "-y"]
            if kind_name == "title":
                background = str(item.get("frameBackground", "#202020"))
                if not background.startswith("#"): background = "#30382a"
                command += ["-f", "lavfi", "-i", f"color=c={background}:s={width}x{height}:r={fps}:d={duration}"]
            else:
                source = source_path(self.settings, item)
                if not source.exists(): raise RenderError(f"Media file is missing: {source}")
                if kind_name == "image": command += ["-loop", "1", "-t", str(duration), "-i", str(source)]
                else: command += ["-stream_loop", "-1", "-t", str(duration), "-i", str(source)]
            filters = [base_filter]
            effect = str(item.get("effect", ""))
            if kind_name == "image" and effect.startswith("Ken Burns"):
                delta = "0.0008" if "Zoom in" in effect else "-0.0008" if "Zoom out" in effect else "0.0003"
                start_zoom = "1" if delta.startswith("0") else "1.12"
                filters.append(f"zoompan=z='max(1,min(1.12,{start_zoom}+on*{delta}))':d=1:s={width}x{height}:fps={fps}")
            text_filter = self._text_filter(item, defaults, width, height)
            if text_filter: filters.append(text_filter)
            filters += ["format=yuv420p", "settb=AVTB"]
            command += ["-vf", ",".join(filters), "-an", "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(segment)]
            self._run_ffmpeg(command, cancelled, log_file)
            segments.append(segment)
            progress(5 + 45 * (index + 1) / len(media), f"Prepared item {index+1} of {len(media)}")

        inputs: list[str] = []
        for segment in segments: inputs += ["-i", str(segment)]
        if len(segments) == 1:
            filter_graph = "[0:v]setpts=PTS-STARTPTS[vout]"
        else:
            chains=[]; cumulative=float(media[0].get("duration",5)); previous="[0:v]"
            for index in range(1,len(media)):
                transition=max(.05,min(float(media[index-1].get("transitionTime",1)), cumulative-.05, float(media[index].get("duration",5))-.05))
                offset=max(.01,cumulative-transition)
                out=f"[x{index}]" if index<len(media)-1 else "[vout]"
                chains.append(f"{previous}[{index}:v]xfade=transition={xfade_name(str(media[index-1].get('transition','Fade')))}:duration={transition}:offset={offset}{out}")
                previous=out; cumulative += float(media[index].get("duration",5))-transition
            filter_graph=";".join(chains)
        total_duration = sum(float(x.get("duration",5)) for x in media) - sum(float(x.get("transitionTime",1)) for x in media[:-1])

        audio_args: list[str] = []; audio_map: list[str] = []
        if soundtrack:
            audio_index=len(segments); policy=project.get("soundtrack",{}).get("policy","Loop & trim")
            if policy == "Loop & trim": audio_args += ["-stream_loop", "-1"]
            audio_args += ["-i", str(soundtrack)]
            volume=max(0,min(1,float(project.get("soundtrack",{}).get("volume",100))/100))
            fade = project.get("soundtrack",{}).get("fadeOut",True)
            af=f"volume={volume}"
            if fade: af += f",afade=t=out:st={max(0,total_duration-2)}:d=2"
            filter_graph += f";[{audio_index}:a]{af}[aout]"
            audio_map=["-map","[aout]","-c:a","aac","-b:a","192k"]
        progress(55, "Composing transitions and soundtrack")
        target_dir = self.settings.preview_dir if kind == "preview" else mounted_path(self.settings, str(output_settings.get("path", "/output")))
        target_dir.mkdir(parents=True, exist_ok=True)
        filename = f"project-{project.get('id','new')}-preview-{uuid.uuid4().hex[:8]}.mp4" if kind == "preview" else Path(str(output_settings.get("filename","slideshow"))).stem + ".mp4"
        output = target_dir / filename
        encoder_label=str(output_settings.get("encoder","Auto")); encoder="h264_qsv" if "Quick Sync" in encoder_label and Path("/dev/dri/renderD128").exists() else "libx264"
        encode_args=["-c:v",encoder,"-b:v",bitrate,"-maxrate",bitrate,"-bufsize",f"{parse_number(bitrate,2)*2:g}M"]
        if encoder=="libx264": encode_args += ["-preset","medium"]
        command=[self.settings.ffmpeg_bin,"-hide_banner","-y",*inputs,*audio_args,"-filter_complex",filter_graph,"-map","[vout]",*audio_map,*encode_args,"-pix_fmt","yuv420p","-movflags","+faststart","-t",str(total_duration),str(output)]
        try:
            self._run_ffmpeg(command,cancelled,log_file)
        except RenderError:
            if encoder=="h264_qsv" and "Auto" in encoder_label:
                progress(70,"Quick Sync unavailable; retrying on CPU")
                command[command.index("h264_qsv")]="libx264"
                self._run_ffmpeg(command,cancelled,log_file)
            else: raise
        progress(98,"Finalizing MP4")
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
