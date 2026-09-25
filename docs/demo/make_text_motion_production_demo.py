#!/usr/bin/env python3
"""Render the stacked text effects through the app's real render pipeline.

Unlike make_text_motion_stack_demo.py (the prototype), this builds an ordinary
project — text frames using the curated presets from registry/text-motion.json,
the bundled handwriting / script / typewriter fonts and colour A -> B frame
backgrounds — and hands it to ``backend/app/renderer.Renderer``: exactly what
"Render MP4" does in the GUI (segments, xfade transitions, libass overlays,
concat). Then it samples one frame per scene into a poster.

Needs FFmpeg + ffprobe with libass and xfade (the Docker image has them)::

    python3 docs/demo/make_text_motion_production_demo.py
    FFMPEG=/path/to/ffmpeg FFPROBE=/path/to/ffprobe python3 docs/demo/make_text_motion_production_demo.py

Writes docs/demo/text-motion-production.mp4 and text-motion-production-poster.png.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))

from app.config import Settings  # noqa: E402
from app.database import Database  # noqa: E402
from app.renderer import Renderer  # noqa: E402

OUT_MP4 = REPO / "docs" / "demo" / "text-motion-production.mp4"
OUT_POSTER = REPO / "docs" / "demo" / "text-motion-production-poster.png"
PRESETS = {p["id"]: p for p in json.loads((REPO / "registry" / "text-motion.json").read_text(encoding="utf-8"))["presets"]}
HOLD, TRANSITION = 4.0, 0.6


def frame(idx: int, text: str, preset: str | None = None, *, layers: list[dict] | None = None, background: str = "#30382a",
          colour: str = "#ffffff", size: int = 96, font: str | None = None, bold: bool | None = None, frame_colours: dict | None = None,
          transition: str = "Fade") -> dict:
    """A text frame as the editor saves it, with a preset applied like the browser does
    (its layers, its font and — for colour-change presets — its frame colours)."""
    p = PRESETS[preset] if preset else {}
    item = {
        "id": idx, "type": "title", "path": "Generated frame", "name": f"Scene {idx}", "duration": HOLD,
        "text": text, "textCentered": True, "textX": 50, "textY": 50, "fontSize": size, "fontColor": colour,
        "fontFamily": font or (p.get("font") or {}).get("family") or "Montserrat",
        "textBold": bold if bold is not None else (p.get("font") or {}).get("bold", True),
        "textItalic": False, "frameBackground": background,
        "transition": transition, "transitionTime": TRANSITION,
        "textFx": [dict(layer, id=f"s{idx}l{k}") for k, layer in enumerate(layers or p.get("layers") or [])],
    }
    fc = frame_colours or p.get("frame")
    if fc:
        item.update({"frameBackground": fc["background"], "frameBackground2": fc["background2"], "frameTransition": fc["transition"],
                     "frameTransitionTime": fc["time"], "frameTransitionStart": fc["start"]})
    return item


SCENES = [
    # (item, poster time in seconds from the scene start — a multiple of 1/25 s, poster caption)
    (frame(1, "Dear Lisbon,\nsee you soon", "handwritten-note", background="#f5f1e6", colour="#23262b", size=104),
     2.2, "Handwritten note · Kalam · write on + pen boil"),
    (frame(2, "Summer in Portugal", "sunrise-card", size=100), 1.6,
     "Sunrise card · circle open A→B · text follows the colour"),
    (frame(3, "Ana & Tomás", "wedding-script", size=150, background="#5b285f",
           layers=PRESETS["wedding-script"]["layers"][:2] + [{"effect": "bg-follow"}, PRESETS["wedding-script"]["layers"][2]],
           frame_colours={"background": "#5b285f", "background2": "#f4e3d7", "transition": "Wipe right", "time": 1.4, "start": 1.2}),
     1.95, "Great Vibes · ink bleed + follow the wipe (script joins intact)"),
    (frame(4, "Chapter two", "ink-swap-card", size=130), 2.1, "Ink swap · Caveat · wipe up, text takes colour A"),
    (frame(5, "Day 3 — Porto", "memo-typewriter", background="#37474f", size=84), 1.5, "Memo typewriter · Special Elite · caret"),
    (frame(6, "The journey home", "reveal-card", size=100), 1.2, "Reveal with colour · text only where B arrives"),
    (frame(7, "Let's go!", "kinetic-pop", font="Permanent Marker", bold=False, background="#163c44", size=140), 1.2,
     "Kinetic pop · Permanent Marker · words pop + pulse"),
    (frame(8, "Maria", "signature", background="#f5f1e6", colour="#1d3557", size=170), 1.6, "Signature · Mr Dafoe · one stroke"),
]


def main() -> int:
    ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    ffprobe = os.environ.get("FFPROBE") or shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        print("needs ffmpeg and ffprobe (with libass) on PATH or in FFMPEG / FFPROBE", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        settings = Settings(config_dir=base / "config", photos_dir=base / "photos", videos_dir=base / "videos",
                            music_dir=base / "music", output_dir=base / "output", fonts_dir=REPO / "public" / "fonts",
                            ffmpeg_bin=ffmpeg, ffprobe_bin=ffprobe)
        for d in (settings.photos_dir, settings.videos_dir, settings.music_dir, settings.output_dir, settings.work_dir):
            d.mkdir(parents=True, exist_ok=True)
        renderer = Renderer(Database(base / "demo.db"), settings)
        if not renderer.ass_filter_supported():
            print("this FFmpeg has no libass 'ass' filter: stacked effects would degrade to fades", file=sys.stderr)
            return 1
        media = [item for item, _, _ in SCENES]
        project = {"id": 1, "project": {"name": "Text motion demo"}, "media": media,
                   "output": {"resolution": "HD · 720p", "frameRate": "25 fps", "bitrate": "3 Mbps", "encoder": "libx264",
                              "path": "/output", "filename": "text-motion-production"}}
        work = settings.work_dir / "demo"
        work.mkdir(parents=True, exist_ok=True)
        rendered = renderer.render(project, "render", work, threading.Event(), lambda p, s: print(f"{p:5.1f}%  {s}"))
        # smaller copy for the repository (flat colours compress well)
        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(rendered), "-an", "-c:v", "libx264", "-preset", "slow", "-crf", "24",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT_MP4)], check=True)
        # poster: one frame per scene, from the full-quality render. Transitions
        # are their own segments, so scene k's visible hold starts at k * (HOLD + TRANSITION).
        starts, t = [], 0.0
        for item, _, _ in SCENES:
            starts.append(t)
            t += HOLD + TRANSITION
        tiles = []
        for k, ((item, at, caption), start) in enumerate(zip(SCENES, starts)):
            png = base / f"tile-{k}.png"
            label = caption.replace(":", "\\:").replace("'", "\u2019")
            subprocess.run([ffmpeg, "-v", "error", "-y", "-ss", f"{start + at:.3f}", "-i", str(rendered), "-frames:v", "1",
                            "-vf", "scale=640:360", str(png)], check=True)
            tiles.append((png, label))
        inputs: list[str] = []
        for png, _ in tiles:
            inputs += ["-i", str(png)]
        n = len(tiles)
        layout = "|".join(f"{(k % 2) * 640}_{(k // 2) * 360}" for k in range(n))
        subprocess.run([ffmpeg, "-v", "error", "-y", *inputs, "-filter_complex", f"xstack=inputs={n}:layout={layout}",
                        "-frames:v", "1", str(OUT_POSTER)], check=True)
        for (_, label), k in zip(tiles, range(n)):
            print(f"poster tile {k + 1}: {label}")
    print(f"wrote {OUT_MP4.relative_to(REPO)} ({OUT_MP4.stat().st_size // 1024} KB) and {OUT_POSTER.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
