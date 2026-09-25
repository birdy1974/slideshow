#!/usr/bin/env python3
"""Render the stackable text motion demo for docs/text-motion-references.md.

Documentation artifact, not app code. Every frame is rendered by libass (the
``ass`` filter), the same engine the app's FFmpeg uses for text effects:

  1. Combinations, today vs proposed: left half = the app's CURRENT engine
     (backend/app/text_effects.py, imported as is), right half = the
     prototype compositor (text_motion_stack.py) with the same effects stacked.
  2. New effects from the references (Jitter, Prismic/CSS, GitHub libraries),
     all expressed as declarative channel tracks (text-motion-effects.json).
  3. Presets = saved stacks (Enter + While + Exit layers combined).

Requirements: ``pip install uharfbuzz fonttools`` and an FFmpeg with libass
(``$FFMPEG``, ``ffmpeg`` on PATH, or the imageio-ffmpeg binary). Run from any
directory:

    python3 docs/demo/make_text_motion_stack_demo.py

Writes text-motion-stack-demo.mp4 and text-motion-stack-poster.png (committed)
plus the three generated .ass files it renders from (git-ignored: large and
regenerated in seconds; open them to see exactly what libass receives).
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "backend"))

from text_motion_stack import FONT_DIR, FPS, Caption, Doc, Layer, ts  # noqa: E402

W, H = 1280, 720
HALF = W // 2
BG = "0x14161c"

# Timeline (seconds, multiples of 1/FPS so events sit on frame boundaries)
INTRO = (0.0, 3.2)
CMP_LEN = 4.4
CMP_START = 3.2
N_CMP = 5
FX_START = CMP_START + CMP_LEN * N_CMP          # 25.2
FX_LEN = 4.0
N_FX = 5
PRESET_START = FX_START + FX_LEN * N_FX         # 45.2
PRESET_LEN = 4.4
N_PRESET = 6
TOTAL = PRESET_START + PRESET_LEN * N_PRESET    # 71.6


def ffmpeg() -> str:
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        try:
            import imageio_ffmpeg
            exe = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            sys.exit("No FFmpeg found: set $FFMPEG or pip install imageio-ffmpeg")
    return exe


# ---------------------------------------------------------------------------
# UI text (labels, titles) - plain ASS, not part of the effect prototype
# ---------------------------------------------------------------------------
UI_STYLES = [
    "Style: Title,Montserrat,34,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1",
    "Style: Label,Montserrat,21,&H00D6CCC9,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1",
    "Style: Small,Montserrat,17,&H00A0908B,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1",
    "Style: Bad,Montserrat,21,&H007A7AFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1",
    "Style: Good,Montserrat,21,&H00A8E76E,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1",
    "Style: Chip,Montserrat,19,&H00F2E6DA,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,0,0,0,1",
]


def ui(doc: Doc, style: str, text: str, x: float, y: float, start: float, end: float, extra: str = "") -> None:
    doc.events.append(f"Dialogue: 9,{ts(start)},{ts(end)},{style},,0,0,0,,"
                      f"{{\\an8\\pos({x:.0f},{y:.0f})\\fad(160,160){extra}}}{text}")


def rect(doc: Doc, x: float, y: float, w: float, h: float, colour_bgr: str, start: float, end: float,
         alpha: str = "00", layer: int = 1) -> None:
    doc.events.append(f"Dialogue: {layer},{ts(start)},{ts(end)},Label,,0,0,0,,"
                      f"{{\\an7\\pos({x:.0f},{y:.0f})\\1c&H{colour_bgr}&\\alpha&H{alpha}&\\fad(160,160)\\p1}}"
                      f"m 0 0 l {w:.0f} 0 {w:.0f} {h:.0f} 0 {h:.0f}{{\\p0}}")


def chips(doc: Doc, stack_text: str, y: float, start: float, end: float) -> None:
    """Bottom line listing the stack, e.g. 'ENTER Pop in · letters  ▸  WHILE Wave'."""
    ui(doc, "Chip", stack_text, W / 2, y, start, end)


# ---------------------------------------------------------------------------
# Chapter 1: today (backend engine) vs proposed stack (prototype compositor)
# ---------------------------------------------------------------------------
def today_events(item: dict, offset: float, style_name: str) -> tuple[str, list[str]]:
    """ASS events from the app's current engine, shifted to `offset`.

    Returns (style line, events). The backend's 2 px drop shadow is set to 0
    so that both halves differ only in their effects.
    """
    from app.text_effects import _geometry, build_ass_document, overlay_plan
    g = _geometry(item, {}, HALF, H)
    doc = build_ass_document(g, overlay_plan(item))
    style_line = next(l for l in doc.splitlines() if l.startswith("Style: FX,"))
    fields = style_line[len("Style: "):].split(",")
    fields[0] = style_name
    fields[17] = "0"                                  # Shadow
    events = []
    for line in doc.splitlines():
        if not line.startswith("Dialogue:"):
            continue
        head, rest = line.split(": ", 1)
        parts = rest.split(",", 9)
        parts[1] = ts(_parse_ts(parts[1]) + offset)
        parts[2] = ts(_parse_ts(parts[2]) + offset)
        parts[3] = style_name
        events.append("Dialogue: " + ",".join(parts))
    return "Style: " + ",".join(fields), events


def _parse_ts(stamp: str) -> float:
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def base_item(**kw) -> dict:
    item = {
        "type": "title", "duration": 3.84, "textStart": 0, "textEnd": 3.84,
        "textX": 50, "textY": 50,
        "fontSize": 138,            # the backend scales by width/1920 -> 46 px on a 640 px half
        "fontFamily": "Montserrat", "textBold": True, "fontColor": "#ffffff",
        "textEnterDuration": 0.6, "textExitDuration": 0.5,
    }
    item.update(kw)
    return item


def comparisons():
    """(title, today label, stack label, backend item, stack) per comparison.

    Only combinations that the current engine cannot express are shown here.
    (The timing bugs fixed on this branch, e.g. a pulse restarting on every
    motion-path slice or the typewriter steps fading, are covered by tests in
    backend/tests/test_text_effects.py instead.)
    """
    return [
        ("Typewriter + Wave",
         "Wave replaces the letters: typewriter lost", "types in while waving",
         base_item(text="Hello, summer!", textFxEnter="Typewriter", textFxWhile="Wave", textFxExit="Fade out",
                   textEnterDuration=1.4),
         [Layer("typewriter", stagger=1.4 / 13), Layer("wave"), Layer("fade-out", duration=0.5)]),
        ("Slide from left + Motion path",
         "one position per event: the slide becomes a fade", "slides in while the path carries it",
         base_item(text="On the move", textFxEnter="Slide from left", textFxExit="Fade out",
                   textMoveEnabled=True, textMovePathType="straight",
                   textMoveFromX=35, textMoveFromY=50, textMoveToX=65, textMoveToY=50),
         [Layer("slide-in-left"), Layer("motion-path", params={"points": [[35, 50], [65, 50]]}),
          Layer("fade-out", duration=0.5)]),
        ("Rotate toggle + Typewriter + caret",
         "libass path: the rotation is dropped", "typing on a tilting line",
         base_item(text="Dear diary", textFxEnter="Typewriter + caret", textEnterDuration=1.2,
                   textRotateEnabled=True, textRotateFrom=-10, textRotateTo=10),
         [Layer("typewriter-caret", stagger=1.2 / 9), Layer("tilt"), Layer("fade-out", duration=0.5)]),
        ("Pop in + Count up",
         "Count up owns the timeline: pop lost", "pops in while it counts",
         base_item(text="0", textFxEnter="Pop in", textFxWhile="Count up", textFxExit="Fade out",
                   fontSize=180, textFxParams={"from": 0, "to": 2026}),
         [Layer("pop-in"), Layer("count-up", params={"from": 0, "to": 2026}), Layer("fade-out", duration=0.5)]),
        ("Split rise · letters + Shimmer",
         "per-letter events: Shimmer silently dropped", "letters rise, shimmer runs across",
         base_item(text="Golden hour", fontColor="#f2c14e", textFxEnter="Split rise · chars", textFxWhile="Shimmer",
                   textFxExit="Fade out", textEnterDuration=0.9),
         [Layer("split-rise"), Layer("shimmer"), Layer("fade-out", duration=0.5)]),
    ]


def chapter_combinations(main: Doc, left: list[str], left_styles: list[str], right: Doc) -> None:
    for i, (title, bad, good, item, stack) in enumerate(comparisons()):
        s = CMP_START + i * CMP_LEN
        t0, t1 = s + 0.28, s + 0.28 + 3.84
        style_line, events = today_events(item, t0, f"T{i}")
        left_styles.append(style_line)
        left.extend(events)
        colour = item.get("fontColor", "#ffffff")
        size = item["fontSize"] * HALF / 1920            # same px size as the backend computes
        compiled = right.add(Caption(item["text"], HALF / 2, H / 2, int(size), stack, start=t0, end=t1,
                                     colour=colour, frame_w=HALF, frame_h=H))
        # labels
        ui(main, "Small", "1 · COMBINATIONS: TODAY vs PROPOSED STACK", W / 2, 18, s, s + CMP_LEN)
        ui(main, "Title", title, W / 2, 46, s, s + CMP_LEN)
        ui(main, "Label", "TODAY (engine on main)", HALF / 2, 118, s, s + CMP_LEN)
        ui(main, "Bad", bad, HALF / 2, 148, s, s + CMP_LEN)
        ui(main, "Label", "PROPOSED STACK (prototype)", HALF + HALF / 2, 118, s, s + CMP_LEN)
        ui(main, "Good", good, HALF + HALF / 2, 148, s, s + CMP_LEN)
        rect(main, HALF - 1, 110, 2, 540, "4F4440", s, s + CMP_LEN)
        print(f"[1.{i + 1}] {title}: today {len(events)} events | stack {len(compiled.events)} events"
              + (f" | warnings: {compiled.warnings}" if compiled.warnings else ""))


# ---------------------------------------------------------------------------
# Chapter 2: new effects from the references (4 per screen)
# ---------------------------------------------------------------------------
QUADS = [(320, 250), (960, 250), (320, 555), (960, 555)]


def effect_screens():
    """[(screen title, [(name, source, caption kwargs)])]"""
    return [
        ("Jitter templates", [
            ("Blurry spin", "Jitter · Blurry Text Spin",
             dict(text="Blurry spin", stack=[Layer("blurry-spin"), Layer("fade-out")])),
            ("Sliding reveal", "Jitter · Sliding Text Reveal",
             dict(text="Sliding\nreveal", size=50, stack=[Layer("sliding-reveal"), Layer("mask-out")])),
            ("Snappy stretch", "Jitter · Snappy Text Stretch",
             dict(text="SNAPPY", family="Anton", bold=False, size=64, stack=[Layer("snappy-stretch"), Layer("fade-out")])),
            ("Departures board", "Jitter · Departures Board",
             dict(text="GATE 42", family="Bebas Neue", bold=False, size=70, colour="#ffd66b",
                  stack=[Layer("departures-flap"), Layer("fade-out")])),
        ]),
        ("Jitter templates", [
            ("Cascading text", "Jitter · Cascading Text (stagger from centre)",
             dict(text="Cascade", stack=[Layer("cascade-drop"), Layer("fade-out")])),
            ("Glitch reveal", "Jitter · Glitchy Text Reveal",
             dict(text="GLITCH", size=60, stack=[Layer("glitch-reveal"), Layer("fade-out")])),
            ("Type trail", "Jitter · Type Trail",
             dict(text="Trail", size=60, stack=[Layer("type-trail"), Layer("fade-out")])),
            ("Text extrusion", "Jitter · Text Extrusion",
             dict(text="DEPTH", family="Anton", bold=False, size=70, colour="#f4f0ff",
                  stack=[Layer("extrude-in"), Layer("float"), Layer("fade-out")])),
        ]),
        ("Prismic CSS examples", [
            ("Tracking in", "Animista via Prismic",
             dict(text="tracking in", stack=[Layer("tracking-in", duration=1.2), Layer("fade-out")])),
            ("3D flip · letters", "Prismic · 3D text spin",
             dict(text="Flip 3D", size=60, stack=[Layer("flip-3d"), Layer("fade-out")])),
            ("Outline to fill", "Prismic · draw-in (approximation)",
             dict(text="Outline", size=60, stack=[Layer("outline-draw"), Layer("fade-out")])),
            ("Letter burst (exit)", "Prismic · letter burst",
             dict(text="Burst!", size=64, stack=[Layer("fade-in", duration=0.4), Layer("letter-burst", duration=1.1)])),
        ]),
        ("Decorative loops = stackable While layers", [
            ("Mirror reflection", "Jitter · Text Mirror",
             dict(text="Mirror", size=58, y_off=-24, stack=[Layer("rise-in"), Layer("mirror"), Layer("fade-out")])),
            ("Rainbow wave (2 layers)", "Prismic · rainbow text + wavy text",
             dict(text="Rainbow", size=58, stack=[Layer("fade-in"), Layer("rainbow-wave"), Layer("wave", intensity=0.6),
                                                   Layer("fade-out")])),
            ("Breathe (weight)", "Prismic · variable-font breathe (approx.)",
             dict(text="Breathe", size=58, stack=[Layer("fade-in"), Layer("breathe"), Layer("fade-out")])),
            ("Dancing shadow", "Prismic · dancing shadow",
             dict(text="Shadow", size=58, stack=[Layer("fade-in"), Layer("dancing-shadow"), Layer("fade-out")])),
        ]),
        ("Numbers, words & retro", [
            ("Countdown", "Jitter · Countdown",
             dict(text="3", family="Anton", bold=False, size=90, stack=[Layer("countdown")])),
            ("Count up + Pop in", "Jitter · Counter",
             dict(text="0", family="Anton", bold=False, size=76,
                  stack=[Layer("pop-in"), Layer("count-up", params={"from": 0, "to": 2026}), Layer("fade-out")])),
            ("Rotating words", "GitHub · text-rotate / Animated Text Kit",
             dict(rotating=["sunsets", "the sea", "Sundays"])),
            ("CRT / RGB split", "Jitter · CRT Effect",
             dict(text="CRT 1986", size=58, stack=[Layer("flicker-in"), Layer("rgb-split"), Layer("flicker-out")])),
        ]),
    ]


def chapter_effects(main: Doc) -> None:
    for si, (title, cells) in enumerate(effect_screens()):
        s = FX_START + si * FX_LEN
        t0, t1 = s + 0.2, s + FX_LEN - 0.2
        ui(main, "Small", f"2 · NEW EFFECTS FROM THE REFERENCES  ({si + 1}/{N_FX})", W / 2, 18, s, s + FX_LEN)
        ui(main, "Title", title, W / 2, 46, s, s + FX_LEN)
        for (qx, qy), (name, source, kw) in zip(QUADS, cells):
            ui(main, "Label", name, qx, qy - 120, s, s + FX_LEN)
            ui(main, "Small", source, qx, qy - 92, s, s + FX_LEN)
            if "rotating" in kw:
                rotating_words(main, kw["rotating"], qx, qy, t0, t1)
                continue
            kw = dict(kw)
            y_off = kw.pop("y_off", 0)
            size = kw.pop("size", 54)
            compiled = main.add(Caption(x=qx, y=qy + y_off, size=size, start=t0, end=t1, **kw))
            if compiled.warnings:
                print("   warnings:", compiled.warnings)
        rect(main, HALF - 1, 110, 2, 580, "3A302D", s, s + FX_LEN)
        rect(main, 40, 400, W - 80, 2, "3A302D", s, s + FX_LEN)


def rotating_words(main: Doc, words: list[str], x: float, y: float, t0: float, t1: float) -> None:
    """'We love {a|b|c}': each word is its own caption with a mask-slide in/out stack."""
    main.add(Caption("We love", x, y - 34, 44, [Layer("fade-in"), Layer("fade-out")], start=t0, end=t1))
    span = (t1 - t0) / len(words)
    for i, word in enumerate(words):
        a = round((t0 + i * span) * FPS) / FPS
        b = round((t0 + (i + 1) * span + 0.2) * FPS) / FPS
        stack = [Layer("sliding-reveal", duration=0.45)]
        if i < len(words) - 1:
            stack.append(Layer("mask-out", duration=0.45))
        else:
            stack.append(Layer("fade-out", duration=0.3))
        main.add(Caption(word, x, y + 26, 50, stack, start=a, end=min(b, t1), colour="#7fd4ff"))


# ---------------------------------------------------------------------------
# Chapter 3: presets = saved stacks
# ---------------------------------------------------------------------------
def presets():
    return [
        ("Cinematic title", "ENTER Tracking in + Blur in · letters from centre  ▸  WHILE Slow zoom  ▸  EXIT Fade out",
         dict(text="THE LAST\nSUMMER", family="Playfair Display", bold=True, size=92,
              stack=[Layer("tracking-in", duration=1.8), Layer("blur-in", unit="char", stagger=0.04, order="center"),
                     Layer("slow-zoom"), Layer("fade-out", duration=0.8)])),
        ("Kinetic pop", "ENTER Pop in · letters  ▸  WHILE Wave  ▸  EXIT Pop out · letters (reverse)",
         dict(text="Let's go!", family="Anton", bold=False, size=120, colour="#ffe45e",
              stack=[Layer("pop-in", unit="char", stagger=0.05), Layer("wave"),
                     Layer("pop-out", unit="char", stagger=0.03, order="reverse")])),
        ("Neon sign", "ENTER Flicker in  ▸  WHILE Neon glow  ▸  EXIT Flicker out",
         dict(text="OPEN LATE", size=100, colour="#ffe6fa",
              stack=[Layer("flicker-in"), Layer("neon-glow"), Layer("flicker-out")])),
        ("Memo typewriter", "ENTER Typewriter + caret  ▸  EXIT Typewriter delete",
         dict(text="Dear diary,\nday one.", family="Caveat", bold=True, size=92, colour="#f5e9c9",
              stack=[Layer("typewriter-caret", stagger=0.08), Layer("delete", stagger=0.045)])),
        ("Headline slam", "ENTER Drop & bounce · letters from centre  ▸  WHILE Shake + CRT/RGB split  ▸  EXIT Wipe out",
         dict(text="BREAKING", family="Bebas Neue", bold=False, size=160, colour="#ffffff", y=392,
              stack=[Layer("drop-bounce", unit="char", stagger=0.04, order="center", intensity=0.7),
                     Layer("shake", intensity=0.5),
                     Layer("rgb-split"), Layer("wipe-out")])),
        ("Lower third", "ENTER Sliding reveal · lines  ▸  WHILE Shimmer  ▸  EXIT Slide out of mask · lines",
         dict(text="Anna & Tom\nAmsterdam · 2026", size=60, colour="#f2c14e", y=470,
              stack=[Layer("sliding-reveal"), Layer("shimmer"), Layer("mask-out")])),
    ]


def chapter_presets(main: Doc) -> None:
    for pi, (name, chain, kw) in enumerate(presets()):
        s = PRESET_START + pi * PRESET_LEN
        t0, t1 = s + 0.28, s + PRESET_LEN - 0.24
        ui(main, "Small", f"3 · PRESETS = SAVED STACKS  ({pi + 1}/{N_PRESET})", W / 2, 18, s, s + PRESET_LEN)
        ui(main, "Title", name, W / 2, 46, s, s + PRESET_LEN)
        rect(main, 90, 640, W - 180, 44, "2A2320", s, s + PRESET_LEN, alpha="10")
        chips(main, chain, 650, s, s + PRESET_LEN)
        kw = dict(kw)
        y = kw.pop("y", 360)
        compiled = main.add(Caption(x=W / 2, y=y, start=t0, end=t1, **kw))
        print(f"[3.{pi + 1}] {name}: {len(compiled.events)} events"
              + (f" | warnings: {compiled.warnings}" if compiled.warnings else ""))


def intro(main: Doc) -> None:
    a, b = INTRO
    main.add(Caption("Stackable text motion", W / 2, 300, 64,
                     [Layer("blur-in", unit="char", stagger=0.03, order="center"), Layer("fade-out", duration=0.4)],
                     start=a + 0.12, end=b - 0.08))
    ui(main, "Label", "prototype · every frame rendered by libass, the text engine in the app's FFmpeg", W / 2, 370, a, b)
    ui(main, "Small", "1 Combinations: today vs proposed    2 New effects from the references    3 Presets = saved stacks",
       W / 2, 420, a, b)


def main() -> None:
    left_styles: list[str] = []
    left_events: list[str] = []
    right = Doc(HALF, H)
    main_doc = Doc(W, H)
    for line in UI_STYLES:
        main_doc.raw_style(line)

    intro(main_doc)
    chapter_combinations(main_doc, left_events, left_styles, right)
    chapter_effects(main_doc)
    chapter_presets(main_doc)

    left = Doc(HALF, H)
    for line in left_styles:
        left.raw_style(line)
    left.events = left_events

    work = HERE
    (work / "text-motion-stack-left.ass").write_text(left.text(), encoding="utf-8")
    (work / "text-motion-stack-right.ass").write_text(right.text(), encoding="utf-8")
    (work / "text-motion-stack-demo.ass").write_text(main_doc.text(), encoding="utf-8")
    n_events = len(left.events) + len(right.events) + len(main_doc.events)
    print(f"ASS events: {n_events} (left {len(left.events)}, right {len(right.events)}, main {len(main_doc.events)})")

    mp4 = work / "text-motion-stack-demo.mp4"
    fonts = str(FONT_DIR)
    graph = (f"[0]ass=text-motion-stack-left.ass:fontsdir={fonts}[l];"
             f"[1]ass=text-motion-stack-right.ass:fontsdir={fonts}[r];"
             f"[l][r]hstack,ass=text-motion-stack-demo.ass:fontsdir={fonts},vignette=PI/5,format=yuv420p[v]")
    src = f"color=c={BG}:s={HALF}x{H}:r={FPS}:d={TOTAL}"
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", src, "-f", "lavfi", "-i", src,
                    "-filter_complex", graph, "-map", "[v]", "-t", str(TOTAL),
                    "-c:v", "libx264", "-preset", "slow", "-crf", "26", "-movflags", "+faststart",
                    str(mp4)], cwd=work, check=True)

    # Contact sheet: 12 representative frames (4 x 3)
    picks = [CMP_START + 1.0, CMP_START + CMP_LEN * 3 + 0.6, CMP_START + CMP_LEN * 4 + 0.8]
    picks += [FX_START + i * FX_LEN + 2.4 for i in range(N_FX)]
    picks += [PRESET_START + i * PRESET_LEN + t for i, t in ((0, 2.6), (1, 2.2), (2, 2.4), (4, 2.0))]
    frames = "+".join(f"eq(n\\,{int(round(t * FPS))})" for t in picks)
    poster = work / "text-motion-stack-poster.png"
    subprocess.run([ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp4),
                    "-vf", f"select='{frames}',scale=320:180,tile=4x3", "-frames:v", "1", str(poster)],
                   cwd=work, check=True)
    print(f"wrote {mp4.name} ({mp4.stat().st_size / 1e6:.2f} MB, {TOTAL:.1f} s) and {poster.name}")


if __name__ == "__main__":
    sys.exit(main())
