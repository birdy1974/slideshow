"""FFmpeg slideshow renderer.

Sources are normalized to a common frame rate, time base and timestamp origin.
Each visual transition is then rendered as an isolated two-input xfade unit;
the compatible units are joined by FFmpeg's concat demuxer. This avoids the
performance and reliability problems of a long, serial xfade filter chain.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .database import Database, utcnow
from .media import UnsafePath, mounted_path, source_path
from .picture_crop import crop_filters, lasso_graph, lasso_inputs, lasso_mask_pgm, lasso_plan, normalize_crop
from .picture_filters import picture_look

log = logging.getLogger(__name__)

KEN_BURNS_MAX_ZOOM = 1.12
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

# GL transitions (gl-transitions.com catalogue) are ported into the custom ffmpeg
# via ffmpeg-patch/xfade-easing.h. The authoritative catalogue (id, friendly label,
# group, params, defaults) lives in registry/transitions.json, shared with the
# frontend so both sides never drift. Friendly labels are what saved projects
# store; ids are what ffmpeg receives (xfade-easing dispatch is case-insensitive).
def _registry_candidates() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SLIDESHOW_REGISTRY")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "registry" / "transitions.json")  # repo checkout
    cands.append(Path("/app/registry/transitions.json"))             # Docker image
    cands.append(Path.cwd() / "registry" / "transitions.json")
    return cands


def _load_gl_registry() -> tuple[frozenset[str], dict[str, str]]:
    for path in _registry_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.get("gl") if isinstance(data, dict) else None
        if not entries:
            continue
        ids: set[str] = set()
        labels: dict[str, str] = {}
        for e in entries:
            tid, label = str(e.get("id", "")).strip(), str(e.get("label", "")).strip()
            if not tid.startswith("gl_") or not label or tid in ids or label in labels:
                continue
            ids.add(tid)
            labels[label] = tid
        if ids:
            log.info("Loaded %d GL transitions from %s", len(ids), path)
            return frozenset(ids), labels
    log.warning("registry/transitions.json not found; GL transition catalogue unavailable")
    return frozenset(), {}


GL_TRANSITIONS, GL_FRIENDLY_TO_ID = _load_gl_registry()
GL_ID_TO_FRIENDLY = {v: k for k, v in GL_FRIENDLY_TO_ID.items()}

# Easing catalogue supported by the patched xfade (custom ffmpeg). Empty/linear = no easing.
EASING_PRESETS = [
    "linear",
    "quadratic","quadratic-in","quadratic-out","quadratic-in-out",
    "cubic","cubic-in","cubic-out","cubic-in-out",
    "quartic","quartic-in","quartic-out","quartic-in-out",
    "quintic","quintic-in","quintic-out","quintic-in-out",
    "sinusoidal","sinusoidal-in","sinusoidal-out","sinusoidal-in-out",
    "exponential","exponential-in","exponential-out","exponential-in-out",
    "circular","circular-in","circular-out","circular-in-out",
    "elastic","elastic-in","elastic-out","elastic-in-out",
    "back","back-in","back-out","back-in-out",
    "bounce","bounce-in","bounce-out","bounce-in-out",
    "squareroot","squareroot-in","squareroot-out","squareroot-in-out",
    "cuberoot","cuberoot-in","cuberoot-out","cuberoot-in-out",
    "flipelastic","flipelastic-in","flipelastic-out","flipelastic-in-out",
    "flipback","flipback-in","flipback-out","flipback-in-out",
    "ease","ease-in","ease-out","ease-in-out","cubic-bezier(0.42,0,0.58,1)","cubic-bezier(0.25,0.1,0.25,1)","step-start","step-end",
]



def parse_number(label: str, fallback: float) -> float:
    match = re.search(r"([\d.]+)", label or "")
    return float(match.group(1)) if match else fallback


def ff_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%").replace("[", r"\[").replace("]", r"\]")


def source_path(settings: Settings, item: dict[str, Any]) -> Path:
    """Resolve a media/soundtrack item to a file on a mounted root.

    The UI used to store `path` as the parent folder and `name` as the
    filename. Newer snapshots store the full file path in `path`. Both work.
    Folder names that contain a dot (e.g. ``holiday.2024``) must not be treated
    as files just because ``Path.suffix`` is non-empty — if `path` is a
    directory, `name` is always joined.
    """
    path = str(item.get("path", "") or "").replace("\\", "/")
    name = str(item.get("name", "") or "")
    filename = Path(name).name
    if filename and Path(path.rstrip("/")).name == filename:
        return mounted_path(settings, path)
    base = mounted_path(settings, path)
    if not filename or base.is_file():
        return base
    return mounted_path(settings, path, filename)


def xfade_name(label: str) -> str:
    if not label:
        return "fade"
    # Direct gl_* identifiers (custom ffmpeg) are passed through unchanged
    if label in GL_TRANSITIONS:
        return label
    if label.startswith("gl_"):
        return label
    # Friendly GL names from the UI (GL · Cube) -> ffmpeg id
    if label in GL_FRIENDLY_TO_ID:
        return GL_FRIENDLY_TO_ID[label]
    if label.startswith("GL ") or label.startswith("GL ·"):
        # try normalised
        norm = label.strip()
        if norm in GL_FRIENDLY_TO_ID:
            return GL_FRIENDLY_TO_ID[norm]
        # fallback: strip prefix and lower
        return "dissolve" if label.startswith("GL") else "fade"
    # Native XFADE catalogue
    if label in XFADE:
        return XFADE[label]
    # Legacy GLSL prefix from old stock path
    if label.startswith("GLSL"):
        return "dissolve"
    return "fade"


def _parse_xfade_help(text: str) -> set[str]:
    """Transition constant names from `ffmpeg -h filter=xfade` output.

    Stock FFmpeg declares `transition <int>` and lists each constant with its
    numeric value; the xfade-easing build declares `transition <string>` and
    lists the same constants without a number. Both layouts are handled.
    """
    block = re.search(r"^\s*transition\s+<(?:int|string)>[^\n]*\n(.*?)^\s*duration\s+<duration>", text, re.S | re.M)
    if not block:
        return set()
    names = {name for name in re.findall(r"^\s+(\w+)\s+(?:-?\d+\s+)?\.\.", block.group(1), re.M) if name != "custom"}
    if names and "easing" in text.lower():
        # The custom build accepts every ported GL transition by name but does
        # not enumerate them in the help text; advertise them so resolve_xfade
        # does not downgrade gl_* picks to dissolve.
        names |= GL_TRANSITIONS
    return names


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


def probe_xfade_has_easing(ffmpeg_bin: str) -> bool:
    """Whether the installed xfade supports the custom `easing`/`reverse` options."""
    try:
        result = subprocess.run([ffmpeg_bin, "-hide_banner", "-h", "filter=xfade"], capture_output=True, text=True, timeout=10)
    except Exception:
        return False
    text = f"{result.stdout or ''}\n{result.stderr or ''}".lower()
    return "easing" in text and "reverse" in text


def parse_transition_label(label: str) -> tuple[str, dict[str, str]]:
    """Split a label like 'gl_cube' or 'gl_cube(persp=0.7,unzoom=0.3)' into id + params."""
    if not label:
        return "fade", {}
    label = label.strip()
    if "(" in label and label.endswith(")"):
        try:
            name, rest = label.split("(", 1)
            rest = rest[:-1]
            params: dict[str,str] = {}
            # split by comma or colon or semicolon
            for part in re.split(r"[,;]", rest):
                part=part.strip()
                if not part:
                    continue
                if "=" in part:
                    k,v = part.split("=",1)
                    params[k.strip()] = v.strip()
                elif ":" in part:
                    # allow colon as separator key:value? fallback
                    k,v = part.split(":",1)
                    params[k.strip()] = v.strip()
            return name.strip(), params
        except Exception:
            return label, {}
    return label, {}


def format_transition_params(ffmpeg_id: str, params: dict[str, Any] | None) -> str:
    """Build 'gl_cube(persp=0.7,unzoom=0.3)' preserving raw values (colours, numbers)."""
    if not params:
        return ffmpeg_id
    cleaned = {}
    for k, v in params.items():
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        # keep as string; strip whitespace
        cleaned[k.strip()] = str(v).strip()
    if not cleaned:
        return ffmpeg_id
    inner = ",".join(f"{k}={v}" for k, v in cleaned.items())
    return f"{ffmpeg_id}({inner})"


def quote_xfade_value(value: str) -> str:
    """Quote an xfade option value so FFmpeg's filtergraph parser keeps it whole.

    Inside a -filter_complex string an unquoted ',' ends the current filter
    and ':' ends the current option, so 'gl_cube(persp=0.7,unzoom=0.3)' or
    'cubic-bezier(0.25,0.1,0.25,1)' would be split into garbage ("No such
    filter: '0.1'"). Wrapping the value in single quotes is the documented
    xfade-easing form (transition='gl_cube(...)':easing='cubic-bezier(...)').
    Plain identifiers are left unquoted so existing graphs/tests are unchanged.
    """
    if not any(ch in value for ch in ",:'\\[];"):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", r"\'") + "'"


def build_xfade_filter(transition_label: str, duration: float, offset: float, easing: str | None = None, reverse: int | bool | None = None, params: dict[str, Any] | None = None, ffmpeg_bin: str | None = None) -> str:
    """Build xfade filter fragment, handling gl params, easing and reverse.

    If the local ffmpeg lacks easing support (stock build), easing/reverse are
    silently stripped so the filter remains valid. GL transitions on stock
    builds will have been filtered via resolve_xfade -> fallback.
    """
    ffmpeg_id = xfade_name(transition_label)
    # If caller passed params separately, merge with inline params
    base_id, inline_params = parse_transition_label(ffmpeg_id)
    merged: dict[str, str] = {}
    merged.update(inline_params)
    if params:
        for k,v in params.items():
            if v is None or (isinstance(v,str) and not v.strip()):
                continue
            merged[k] = str(v)
    transition_str = format_transition_params(base_id, merged if merged else None)

    # Validate easing – keep linear as default (no extra option needed)
    easing_str = None
    if easing and isinstance(easing, str):
        e = easing.strip()
        if e and e.lower() not in ("", "linear"):
            # allow css forms like cubic-bezier(...) steps(...) etc.
            easing_str = e

    reverse_int = 0
    if reverse is not None:
        try:
            reverse_int = int(bool(reverse)) if isinstance(reverse, bool) else int(reverse)
        except Exception:
            reverse_int = 1 if reverse else 0
        if reverse_int not in (0,1,2,3):
            reverse_int = 1 if reverse_int else 0

    # Probe easing support if ffmpeg_bin provided
    has_easing = True
    if ffmpeg_bin and easing_str:
        has_easing = probe_xfade_has_easing(ffmpeg_bin)
        if not has_easing:
            easing_str = None
            reverse_int = 0

    parts = [f"transition={quote_xfade_value(transition_str)}", f"duration={format_ffmpeg_number(duration)}", f"offset={format_ffmpeg_number(offset)}"]
    if easing_str and has_easing:
        # CSS easings carry commas (cubic-bezier(a,b,c,d), steps(n,pos)):
        # quote so the filtergraph parser does not split the chain there.
        parts.append(f"easing={quote_xfade_value(easing_str)}")
    if reverse_int and has_easing:
        parts.append(f"reverse={reverse_int}")
    return "xfade=" + ":".join(parts)



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
    if name and Path(path.rstrip("/")).name == name:
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


def _probe_readable(path: Path, ffprobe_bin: str, timeout: float = 30.0, retries: int = 2, retry_delay: float = 0.75) -> str | None:
    """Return a short reason if `path` cannot be read as media, else None.

    Missing and empty (0-byte) files are rejected without spawning ffprobe.
    Cloud-synced mounts (Synology on-demand sync, etc.) can briefly report a
    0-byte placeholder while the real content hydrates, so an empty file is
    re-stat'ed a few times before being declared unreadable.  If ffprobe is
    not installed the content probe is skipped so a render can still start;
    FFmpeg remains the final judge.
    """
    if not path.exists():
        return "file is missing"
    if path.is_dir():
        return "is a folder, not a media file"
    if not path.is_file():
        return "not a file"
    for attempt in range(retries + 1):
        try:
            size = path.stat().st_size
        except OSError as exc:
            return f"cannot stat file ({exc})"
        if size != 0:
            break
        if attempt < retries:
            time.sleep(retry_delay)
    else:
        return "file is empty (0 bytes)"
    probe = shutil.which(ffprobe_bin)
    if not probe:
        return None
    try:
        result = subprocess.run(
            [probe, "-hide_banner", "-v", "error",
             "-show_entries", "format=format_name",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # A slow/networked volume can exceed even a generous timeout while
        # the disk spins up. Give the probe one more chance before failing.
        try:
            result = subprocess.run(
                [probe, "-hide_banner", "-v", "error",
                 "-show_entries", "format=format_name",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return "ffprobe timed out"
        except OSError as exc:
            return f"ffprobe could not start ({exc})"
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


def _even(value: float) -> int:
    """Nearest even pixel count — libx264/yuv420p rejects odd dimensions."""
    return max(2, int(value) // 2 * 2)


def fill_frame_filter(width: int, height: int, fps: int) -> str:
    """Cover the frame, cropping the overflowing edges (videos, title cards)."""
    return f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps={fps}"


# Bundled fonts (public/fonts in the repo, copied to FONTS_DIR in the image).
# Keep in sync with FONT_GROUPS in src/App.tsx.
FONT_FILES: dict[str, dict[str, str]] = {
    "Montserrat": {
        "Regular": "Montserrat-Regular.ttf",
        "Bold": "Montserrat-Bold.ttf",
        "Italic": "Montserrat-Italic.ttf",
        "BoldItalic": "Montserrat-BoldItalic.ttf"
    },
    "Open Sans": {
        "Regular": "OpenSans-Regular.ttf",
        "Bold": "OpenSans-Bold.ttf",
        "Italic": "OpenSans-Italic.ttf",
        "BoldItalic": "OpenSans-BoldItalic.ttf"
    },
    "Roboto": {
        "Regular": "Roboto-Regular.ttf",
        "Bold": "Roboto-Bold.ttf",
        "Italic": "Roboto-Italic.ttf",
        "BoldItalic": "Roboto-BoldItalic.ttf"
    },
    "Lato": {
        "Regular": "Lato-Regular.ttf",
        "Bold": "Lato-Bold.ttf",
        "Italic": "Lato-Italic.ttf",
        "BoldItalic": "Lato-BoldItalic.ttf"
    },
    "Poppins": {
        "Regular": "Poppins-Regular.ttf",
        "Bold": "Poppins-Bold.ttf",
        "Italic": "Poppins-Italic.ttf",
        "BoldItalic": "Poppins-BoldItalic.ttf"
    },
    "Raleway": {
        "Regular": "Raleway-Regular.ttf",
        "Bold": "Raleway-Bold.ttf",
        "Italic": "Raleway-Italic.ttf",
        "BoldItalic": "Raleway-BoldItalic.ttf"
    },
    "Nunito": {
        "Regular": "Nunito-Regular.ttf",
        "Bold": "Nunito-Bold.ttf",
        "Italic": "Nunito-Italic.ttf",
        "BoldItalic": "Nunito-BoldItalic.ttf"
    },
    "Source Sans 3": {
        "Regular": "SourceSans3-Regular.ttf",
        "Bold": "SourceSans3-Bold.ttf",
        "Italic": "SourceSans3-Italic.ttf",
        "BoldItalic": "SourceSans3-BoldItalic.ttf"
    },
    "Oswald": {
        "Regular": "Oswald-Regular.ttf",
        "Bold": "Oswald-Bold.ttf"
    },
    "Playfair Display": {
        "Regular": "PlayfairDisplay-Regular.ttf",
        "Bold": "PlayfairDisplay-Bold.ttf",
        "Italic": "PlayfairDisplay-Italic.ttf",
        "BoldItalic": "PlayfairDisplay-BoldItalic.ttf"
    },
    "Merriweather": {
        "Regular": "Merriweather-Regular.ttf",
        "Bold": "Merriweather-Bold.ttf",
        "Italic": "Merriweather-Italic.ttf",
        "BoldItalic": "Merriweather-BoldItalic.ttf"
    },
    "Lora": {
        "Regular": "Lora-Regular.ttf",
        "Bold": "Lora-Bold.ttf",
        "Italic": "Lora-Italic.ttf",
        "BoldItalic": "Lora-BoldItalic.ttf"
    },
    "Cormorant Garamond": {
        "Regular": "CormorantGaramond-Regular.ttf",
        "Bold": "CormorantGaramond-Bold.ttf",
        "Italic": "CormorantGaramond-Italic.ttf",
        "BoldItalic": "CormorantGaramond-BoldItalic.ttf"
    },
    "Bebas Neue": {
        "Regular": "BebasNeue-Regular.ttf"
    },
    "Anton": {
        "Regular": "Anton-Regular.ttf"
    },
    "Pacifico": {
        "Regular": "Pacifico-Regular.ttf"
    },
    "Dancing Script": {
        "Regular": "DancingScript-Regular.ttf",
        "Bold": "DancingScript-Bold.ttf"
    },
    "Caveat": {
        "Regular": "Caveat-Regular.ttf",
        "Bold": "Caveat-Bold.ttf"
    },
    "Great Vibes": {
        "Regular": "GreatVibes-Regular.ttf"
    }
}
DEJAVU_DIR = Path("/usr/share/fonts/truetype/dejavu")
DEJAVU_FILES = {"Regular": "DejaVuSans.ttf", "Bold": "DejaVuSans-Bold.ttf", "Italic": "DejaVuSans-Oblique.ttf", "BoldItalic": "DejaVuSans-BoldOblique.ttf"}


def font_file(family: str, bold: bool, italic: bool, fonts_dir: Path) -> str:
    """Resolve a family + style to a TTF path FFmpeg drawtext can open.

    Falls back within the family (no italic cut -> upright of same weight),
    then to DejaVu Sans so a missing file never fails a render. Family names
    are matched case-insensitively and ignoring spaces, so both "Open Sans"
    and "OpenSans" work.
    """
    style = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
    order = {
        "Regular": ["Regular", "Bold"],
        "Bold": ["Bold", "Regular"],
        "Italic": ["Italic", "Regular", "BoldItalic", "Bold"],
        "BoldItalic": ["BoldItalic", "Bold", "Italic", "Regular"],
    }[style]
    wanted = re.sub(r"\s+", "", family).lower()
    for name, styles in FONT_FILES.items():
        if re.sub(r"\s+", "", name).lower() != wanted:
            continue
        for candidate in order:
            file = styles.get(candidate)
            if file and (fonts_dir / file).exists():
                return str(fonts_dir / file)
        break
    for candidate in order:
        path = DEJAVU_DIR / DEJAVU_FILES[candidate]
        if path.exists():
            return str(path)
    return str(DEJAVU_DIR / DEJAVU_FILES["Regular"])


def frame_colour_change(item: dict[str, Any]) -> dict[str, Any] | None:
    """Two-colour text frame settings, mirroring the editor's clamping.

    Returns None for single-colour frames. Otherwise: `to` (hex), `transition`
    (friendly label), `time` (seconds, 0.2..hold) and `start` (seconds into
    the visible hold, kept so the change finishes before the frame ends).
    """
    first = str(item.get("frameBackground", "#30382a"))
    second = item.get("frameBackground2")
    if not isinstance(second, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", second) or second.lower() == first.lower():
        return None
    def _num(key: str, default: float) -> float:
        try:
            value = float(item.get(key, default))
        except (TypeError, ValueError):
            return default
        return value if value == value else default
    hold = max(0.2, _num("duration", 5.0))
    time = min(hold, max(0.2, _num("frameTransitionTime", 1.0)))
    start = min(max(0.0, hold - time), max(0.0, _num("frameTransitionStart", 0.0)))
    return {"to": second, "transition": str(item.get("frameTransition") or "Fade"), "time": round(time, 3), "start": round(start, 3)}


# EBU R128 loudness normalisation ------------------------------------------------
LOUDNESS_MIN, LOUDNESS_MAX, LOUDNESS_DEFAULT = -24.0, -8.0, -14.0


def normalization_settings(soundtrack: dict[str, Any]) -> tuple[bool, float]:
    """(enabled, target LUFS) from the project's soundtrack settings.

    Normalisation is on by default; the target is clamped to the UI's range.
    """
    enabled = soundtrack.get("normalize", True)
    enabled = bool(enabled) if enabled is not None else True
    try:
        target = float(soundtrack.get("normalizeTarget", LOUDNESS_DEFAULT))
    except (TypeError, ValueError):
        target = LOUDNESS_DEFAULT
    if target != target:
        target = LOUDNESS_DEFAULT
    return enabled, max(LOUDNESS_MIN, min(LOUDNESS_MAX, target))


def parse_loudnorm_stats(stderr_text: str) -> dict[str, float] | None:
    """Extract the JSON block `loudnorm=print_format=json` writes to stderr."""
    match = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr_text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        stats = {key: float(data[key]) for key in ("input_i", "input_tp", "input_lra", "input_thresh")}
        if "target_offset" in data:
            stats["target_offset"] = float(data["target_offset"])
        return stats
    except (ValueError, KeyError, TypeError):
        return None


def loudnorm_filter(target: float, stats: dict[str, float] | None, true_peak: float = -1.5, lra: float = 11.0) -> str:
    """Second-pass (linear) loudnorm when measurements exist, dynamic otherwise.

    With measured values loudnorm applies a plain gain change, which keeps the
    music's dynamics intact; without them it falls back to its dynamic mode.
    Infinite/-inf measurements (silence) are treated as unmeasured.
    """
    base = f"loudnorm=I={format_ffmpeg_number(target)}:TP={format_ffmpeg_number(true_peak)}:LRA={format_ffmpeg_number(lra)}"
    if stats and all(abs(stats.get(key, float('inf'))) < 1e6 for key in ("input_i", "input_tp", "input_lra", "input_thresh")):
        base += (
            f":measured_I={format_ffmpeg_number(stats['input_i'])}:measured_TP={format_ffmpeg_number(stats['input_tp'])}"
            f":measured_LRA={format_ffmpeg_number(stats['input_lra'])}:measured_thresh={format_ffmpeg_number(stats['input_thresh'])}"
            f":offset={format_ffmpeg_number(stats.get('target_offset', 0.0))}:linear=true"
        )
    return base + ":print_format=none"


def track_edit_filter(track: dict[str, Any]) -> str:
    """Per-track cut/crop and fade filters, as a comma-terminated prefix.

    The editor stores `trimStart`/`trimEnd` (seconds in the source file) and
    `fadeIn`/`fadeOut` (seconds, measured inside the kept region). Only the
    kept region reaches the concat, so the soundtrack length — and the UI's
    total estimate — reflect the real audio time rather than the file length.
    Returns "" when the track is untouched.
    """
    def _num(key: str) -> float:
        try:
            value = float(track.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, value) if value == value else 0.0
    start, end, fade_in, fade_out = _num("trimStart"), _num("trimEnd"), _num("fadeIn"), _num("fadeOut")
    if end and end <= start:
        end = 0.0
    parts: list[str] = []
    if start or end:
        trim = f"atrim=start={format_ffmpeg_number(start)}"
        if end:
            trim += f":end={format_ffmpeg_number(end)}"
        parts += [trim, "asetpts=PTS-STARTPTS"]
    kept = (end - start) if end else None
    if kept is not None:
        fade_in = min(fade_in, kept)
        fade_out = min(fade_out, max(0.0, kept - fade_in))
    if fade_in > 0:
        parts.append(f"afade=t=in:st=0:d={format_ffmpeg_number(fade_in)}")
    if fade_out > 0:
        if kept is not None:
            parts.append(f"afade=t=out:st={format_ffmpeg_number(max(0.0, kept - fade_out))}:d={format_ffmpeg_number(fade_out)}")
        else:
            # Unknown length (no OUT point): reverse, fade in, reverse back.
            parts += ["areverse", f"afade=t=in:st=0:d={format_ffmpeg_number(fade_out)}", "areverse"]
    return "".join(part + "," for part in parts)


def soundtrack_fade_window(soundtrack: dict[str, Any], total_duration: float) -> tuple[float, float]:
    """User-chosen end-of-slideshow fade: (fade seconds, silence seconds).

    `fadeDuration` is how long the music takes to reach silence and `fadeTail`
    how much silence is kept before the final frame. Both are clamped so the
    fade never starts before the slideshow does; the silence is shortened
    first, then the fade itself.
    """
    def _num(key: str, default: float) -> float:
        try:
            value = float(soundtrack.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(0.0, value) if value == value else default
    fade = _num("fadeDuration", 2.0)
    tail = _num("fadeTail", 0.0)
    total = max(0.0, float(total_duration))
    if fade + tail > total:
        tail = max(0.0, min(tail, total - fade))
        fade = min(fade, total - tail)
    return round(fade, 3), round(tail, 3)


def normalize_rotation(value: Any) -> int:
    """Clamp a stored photo rotation to one of 0/90/180/270 degrees clockwise."""
    try:
        degrees = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0
    return ((degrees % 360) + 360) % 360 // 90 * 90


def rotation_filter(rotation: Any) -> str:
    """FFmpeg filter that applies the user's quarter-turn photo orientation.

    `transpose=1` is a lossless 90° clockwise turn and `transpose=2` a 90°
    counter-clockwise one; a half turn is done with flips so no resampling
    happens. Returns an empty string when there is nothing to rotate.
    """
    return {90: "transpose=1", 180: "hflip,vflip", 270: "transpose=2"}.get(normalize_rotation(rotation), "")


def video_trim_window(item: dict[str, Any], native: float | None = None) -> dict[str, float] | None:
    """The sub-clip a movie contributes: ``{"start", "end", "kept", "native"}``.

    Movies normally play end-to-end, but the editor can select a shorter
    section (IN/OUT) so a long recording contributes only the interesting
    part. Everything is in source-file seconds.

    Returns ``None`` when the item is not a video, when its length is unknown,
    or when no trim is set — every project saved before this feature existed
    takes that path and keeps rendering the whole movie.
    """
    if item.get("type") != "video" or not native or native <= 0:
        return None
    start = max(0.0, min(float(item.get("trimStart") or 0.0), native))
    raw_end = float(item.get("trimEnd") or 0.0)
    end = native if raw_end <= 0 else min(raw_end, native)
    # A degenerate window (OUT before IN, or a zero-length keep) would produce
    # an empty segment, so fall back to the whole movie.
    if end - start <= 0.01:
        return None
    if start <= 0.001 and end >= native - 0.001:
        return None
    return {"start": start, "end": end, "kept": end - start, "native": native}


def fit_frame_filter(width: int, height: int, fps: int, zoom_headroom: float = 1.0) -> str:
    """Show the *whole* picture, letterboxed over a blurred copy of itself.

    `scale=...:force_original_aspect_ratio=decrease` never crops, and the bars
    it leaves are filled by the same image scaled to cover, blurred and dimmed.
    `zoom_headroom` shrinks the visible picture by that factor so a later
    zoompan (Ken Burns) can zoom in that far without cutting anything off.
    """
    inner_w, inner_h = _even(width / zoom_headroom), _even(height / zoom_headroom)
    # Blur on a downscaled copy: visually identical after upscaling, far cheaper.
    blur_w, blur_h = _even(max(32, width / 8)), _even(max(32, height / 8))
    sigma = format_ffmpeg_number(max(3.0, blur_w / 32))
    return (
        "split=2[bgsrc][fgsrc];"
        f"[bgsrc]scale={blur_w}:{blur_h}:force_original_aspect_ratio=increase,crop={blur_w}:{blur_h},"
        f"gblur=sigma={sigma},scale={width}:{height},eq=brightness=-0.12:saturation=1.2,setsar=1[bgblur];"
        f"[fgsrc]scale={inner_w}:{inner_h}:force_original_aspect_ratio=decrease,setsar=1[fgfit];"
        f"[bgblur][fgfit]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={fps}"
    )


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
        self._ffmpeg_version: str | None = None
        self._version_probed = False
        self._version_lock = threading.Lock()
        self._xfade_has_easing: bool | None = None
        self._easing_lock = threading.Lock()

    def warm_capabilities(self) -> None:
        """Probe ffmpeg version, the xfade catalogue and Quick Sync once, in a
        background thread, right after startup.

        Container health checks poll this app every 30 s. They used to hit
        /api/health, which ran ``ffmpeg -version`` on every call and could
        queue behind the one-time Quick Sync test encode (up to 30 s under a
        lock) — so a busy NAS blew the probe's time budget three times in a
        row and Docker/Portainer flagged a perfectly working container as
        unhealthy. Warming the caches up front keeps every later capabilities
        read subprocess-free and instant.
        """
        def _warm() -> None:
            try:
                self.ffmpeg_version()
                self.xfade_supported()
                self.xfade_has_easing()
                self.qsv_encodable()
            except Exception:
                log.exception("Capability warm-up failed; capabilities will re-probe lazily")
        threading.Thread(target=_warm, name="capability-warmup", daemon=True).start()

    def _probe_ffmpeg_version(self) -> None:
        with self._version_lock:
            if self._version_probed:
                return
            self._version_probed = True
            path = shutil.which(self.settings.ffmpeg_bin)
            if not path:
                return
            try:
                self._ffmpeg_version = subprocess.run(
                    [path, "-version"], capture_output=True, text=True, timeout=3
                ).stdout.splitlines()[0]
            except Exception:
                self._ffmpeg_version = None

    def ffmpeg_version(self) -> str | None:
        """First line of ``ffmpeg -version``, probed at most once per process."""
        if not self._version_probed:
            self._probe_ffmpeg_version()
        return self._ffmpeg_version

    def xfade_supported(self) -> set[str]:
        """Lazily probe (once) which xfade transitions this FFmpeg build has."""
        with self._xfade_lock:
            if self._xfade_supported is None:
                self._xfade_supported = probe_xfade_transitions(self.settings.ffmpeg_bin)
        return self._xfade_supported

    def xfade_has_easing(self) -> bool:
        """Whether this FFmpeg's xfade supports easing/reverse (custom build)."""
        with self._easing_lock:
            if self._xfade_has_easing is None:
                self._xfade_has_easing = probe_xfade_has_easing(self.settings.ffmpeg_bin)
        return bool(self._xfade_has_easing)

    def resolve_xfade(self, label: str) -> str:
        """Map a UI transition to one this FFmpeg can run, degrading safely.

        Older FFmpeg builds (e.g. 5.x on the DS918+) lack wind/cover/reveal
        constants; rather than failing the whole render, fall back to dissolve.
        An empty probe result means detection failed: keep the mapped name.
        """
        name = xfade_name(label)
        # strip params for support check e.g. gl_cube(persp=0.7) -> gl_cube
        base, _ = parse_transition_label(name)
        # gl transitions may be reported as gl_* in help; compare base
        supported = self.xfade_supported()
        if not supported or base in supported:
            return name
        # If the bare name is not supported but base is gl_* , fallback to dissolve (stock ffmpeg)
        fallback = "dissolve" if "dissolve" in supported else (sorted(supported)[0] if supported else "fade")
        log.warning("Transition '%s' (%s) is not supported by this FFmpeg build; falling back to '%s'", label, name, fallback)
        return fallback

    def build_transition_xfade(self, item: dict[str, Any], duration: float, offset: float) -> str:
        """Build the full xfade=... filter fragment for a media item's transition.

        Reads transition / transitionParams / transitionEasing / transitionReverse
        (and legacy transitionStr) from the item. Stock ffmpeg silently drops
        easing/reverse; custom build renders them. Unknown GL params are left to
        FFmpeg to validate (it will error with a useful message).
        """
        label = str(item.get("transition") or item.get("transitionStr") or "Fade")
        # Normalise label: allow bare id or friendly
        params = item.get("transitionParams") or item.get("transition_params") or {}
        # also accept transitionParams as JSON string (database persistence)
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except Exception:
                params = {}
        if not isinstance(params, dict):
            params = {}
        easing = item.get("transitionEasing") or item.get("transition_easing") or item.get("easing")
        reverse = item.get("transitionReverse") if "transitionReverse" in item else item.get("reverse")
        if reverse is None:
            reverse = item.get("transition_reverse")
        # Also support inline params in label (e.g. gl_cube(persp=0.7)) if params empty
        # Resolve first to get fallback if needed
        resolved = self.resolve_xfade(label)
        base, _ = parse_transition_label(resolved)
        # If fallback happened (custom -> dissolve), don't attach GL params/easing
        if base == "dissolve" and label not in ("dissolve","Dissolve") and xfade_name(label) != "dissolve":
            # fallback case: strip easing/params
            return f"xfade=transition=dissolve:duration={format_ffmpeg_number(duration)}:offset={format_ffmpeg_number(offset)}"
        # Decide whether to include easing (only if custom ffmpeg supports it)
        has_easing = self.xfade_has_easing()
        if not has_easing:
            easing = None
            reverse = 0
        # Build final transition string with params
        ffmpeg_id = xfade_name(label)
        # If caller stored friendly but we have params, rebuild correctly
        base_id, inline = parse_transition_label(ffmpeg_id)
        merged: dict[str, Any] = {}
        merged.update(inline)
        for k,v in params.items():
            if v is None or (isinstance(v,str) and not v.strip()):
                continue
            merged[k] = str(v).strip()
        transition_str = format_transition_params(base_id, merged if merged else None)
        # Re-compose with easing/reverse exactly like build_xfade_filter but without extra probe.
        # Values containing ',' (GL params, CSS easings) must be quoted or the
        # filtergraph parser splits the chain there — see quote_xfade_value.
        parts = [f"transition={quote_xfade_value(transition_str)}", f"duration={format_ffmpeg_number(duration)}", f"offset={format_ffmpeg_number(offset)}"]
        if easing and str(easing).strip().lower() not in ("", "linear"):
            parts.append(f"easing={quote_xfade_value(str(easing).strip())}")
        try:
            rev = int(reverse) if reverse is not None else 0
        except Exception:
            rev = 1 if reverse else 0
        if rev and rev not in (0,1,2,3):
            rev = 1
        if rev:
            parts.append(f"reverse={rev}")
        return "xfade=" + ":".join(parts)

    def capabilities(self) -> dict[str, Any]:
        # Feeds /api/health, so it must answer instantly even mid-render: the
        # version comes from the once-per-process cache and Quick Sync from a
        # non-blocking read of the background probe's result.
        ffmpeg = shutil.which(self.settings.ffmpeg_bin)
        supported = self.xfade_supported() if self._xfade_supported is not None else set()
        has_easing = self._xfade_has_easing if self._xfade_has_easing is not None else False
        # Non-blocking: if not yet probed, report False/unknown; warm thread will fill soon
        if self._xfade_has_easing is None and self._xfade_supported is not None:
            # try quick probe without lock if not yet done? report false until warm
            has_easing = False
        return {"ffmpeg": bool(ffmpeg), "ffmpegVersion": self.ffmpeg_version(), "quickSync": self.qsv_encodable_cached(), "cpuEncoding": bool(ffmpeg), "xfadeTransitions": len(supported) if supported else 0, "hasEasing": bool(has_easing), "hasGL": bool(supported and any(s.startswith("gl_") for s in supported))}

    def qsv_encodable_cached(self) -> bool:
        """Non-blocking view of the Quick Sync probe (False until it finishes).

        Meant for status reporting. Render-time encoder selection keeps using
        the blocking qsv_encodable() so "Auto" always decides on the verified
        answer instead of a probe that is still running.
        """
        return bool(self._qsv_encodable)

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
        return [max(.05, float(item.get("transitionTime", 5))) for item in media[:-1]]

    def _probe_readable(self, path: Path) -> str | None:
        """Check a single file with ffprobe; see module-level `_probe_readable`."""
        return _probe_readable(
            path,
            self.settings.ffprobe_bin,
            timeout=self.settings.ffprobe_timeout,
            retries=self.settings.media_probe_retries,
            retry_delay=self.settings.media_probe_retry_delay,
        )

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
                problems.append(f"{label} — invalid path ({exc})\n    {_ui_path(item)}")
                continue
            reason = self._probe_readable(path)
            if reason:
                problems.append(f"{label} — {reason}\n    {_ui_path(item) or path}")
        for track in project.get("soundtrack", {}).get("tracks", []):
            label = _short_label(str(track.get("name") or "track"))
            try:
                path = source_path(self.settings, track)
            except UnsafePath as exc:
                problems.append(f"soundtrack '{label}' — invalid path ({exc})\n    {_ui_path(track)}")
                continue
            reason = self._probe_readable(path)
            if reason:
                problems.append(f"soundtrack '{label}' — {reason}\n    {_ui_path(track) or path}")
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

    def _cleanup_job_work(self, job_id: str) -> None:
        """Delete a finished job's interim files (segments, soundtrack, ffmpeg.log).

        The render output never lives here — previews go to ``preview_dir`` and
        final MP4s go to the user's ``/output`` folder — so it is always safe to
        remove the working directory once a job has stopped running. Scoped to
        the single job so concurrent renders are never disturbed.
        """
        work = self.settings.work_dir / job_id
        if not work.exists():
            return
        try:
            shutil.rmtree(work, ignore_errors=True)
            log.info("Cleaned temporary work dir for job %s", job_id)
        except Exception as exc:  # pragma: no cover - defensive only
            log.warning("Could not clean work dir %s: %s", work, exc)

    def _prune_previews(self) -> int:
        """Delete stale preview MP4s, keeping only the most recent one.

        Previews are regenerated constantly and are real disk users on the low
        storage DS918+, so each finished generation prunes everything older than
        the newest file. The newest is preserved so the preview the user is
        watching (or just generated) still works; render MP4s in /output are
        never touched here.
        """
        preview_dir = self.settings.preview_dir
        if not preview_dir.exists():
            return 0
        files = [p for p in preview_dir.iterdir() if p.is_file()]
        if not files:
            return 0
        newest = max(files, key=lambda p: p.stat().st_mtime)
        removed = 0
        for path in files:
            if path.resolve() == newest.resolve():
                continue
            try:
                path.unlink()
                removed += 1
            except Exception as exc:  # pragma: no cover - defensive only
                log.warning("Could not delete old preview %s: %s", path, exc)
        if removed:
            log.info("Pruned %d stale preview file(s); kept %s", removed, newest.name)
        return removed

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
            # Temporary files only: the render output (preview in preview_dir,
            # final MP4 in /output) is intentionally preserved. Work dirs are
            # per-job so concurrent renders stay untouched.
            self._cleanup_job_work(job_id)
            # Prune stale proxy previews, keeping the newest so the preview that
            # just finished (or is being watched) still plays.
            try:
                self._prune_previews()
            except Exception as exc:  # pragma: no cover - defensive only
                log.warning("Preview prune for job %s failed: %s", job_id, exc)

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
        # Per-slide opt-out: captions can be disabled without deleting the text.
        # Title frames are the text itself, so the flag never applies to them.
        if item.get("type") != "title" and item.get("textEnabled") is False:
            return None
        start, end = float(item.get("textStart", 0)), float(item.get("textEnd", item.get("duration", 5)))
        fade_in = max(.01, float(item.get("textEnterDuration", .5))); fade_out = max(.01, float(item.get("textExitDuration", .5)))
        x, y = float(item.get("textX", 50)), float(item.get("textY", 72))
        # Title frames carry their own type settings. Picture captions use the
        # project-wide defaults so changing “Default text style” never restyles
        # a standalone text card.
        if item.get("type") == "title":
            size_pt = item.get("fontSize", 48)
            colour_raw = item.get("fontColor") or "#ffffff"
            bold = item.get("textBold", True)
            italic = item.get("textItalic", False)
        else:
            size_pt = defaults.get("fontSize", 48)
            colour_raw = defaults.get("fontColor", "#ffffff")
            bold = defaults.get("bold", True)
            italic = defaults.get("italic", False)
        size = max(8, int(float(size_pt) * width / 1920))
        colour = str(colour_raw).replace("#", "0x")
        family = str((item.get("fontFamily") if item.get("type") == "title" else defaults.get("fontFamily")) or "Montserrat")
        font = font_file(family, bool(bold), bool(italic), self.settings.fonts_dir)
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
            width, height, fps, bitrate = 640, 360, 24, "2M"
        else:
            width, height = RESOLUTIONS.get(output_settings.get("resolution"), (1920, 1080))
            fps = int(parse_number(output_settings.get("frameRate", "30"), 30))
            bitrate = f"{parse_number(output_settings.get('bitrate', '8'), 8):g}M"
        defaults = project.get("textDefaults", {})
        log_file = work / "ffmpeg.log"
        progress(1, "Preparing soundtrack")
        soundtrack = self._make_soundtrack(project, work, cancelled, log_file)
        if project.get("soundtrack",{}).get("policy") == "Fit slideshow to audio":
            # Original movie audio is part of the sound program too.  When it
            # outlasts the music bed, fitting only to the soundtrack would end
            # the calculated audio time early and incorrectly shrink photos.
            audio_duration = self._probe_duration(soundtrack) if soundtrack else 0.0
            source_transitions = self.effective_transitions(media)
            cursor = 0.0
            for index, item in enumerate(media):
                hold = max(.2, float(item.get("duration", 5)))
                if item.get("type") == "video" and item.get("audioSource") == "original":
                    try:
                        hold = max(hold, self._probe_duration(source_path(self.settings, item)))
                    except Exception as exc:
                        log.warning("Could not probe original-audio video duration for %s: %s", item.get("name"), exc)
                    audio_duration = max(audio_duration, cursor + hold)
                cursor += hold + (source_transitions[index] if index < len(source_transitions) else 0.0)
            transition_total = sum(source_transitions)
            duration_total = sum(max(.2, float(x.get("duration",5))) for x in media)
            if audio_duration > transition_total and duration_total > 0:
                # Holds scale to fill the audio; transitions retain their set duration.
                # Videos are excluded from scaling so a full movie is never shortened.
                factor = (audio_duration - transition_total) / duration_total
                scaled: list[dict[str, Any]] = []
                for item in media:
                    if item.get("type") == "video":
                        scaled.append(item)
                        continue
                    hold = max(.2, float(item.get("duration", 5)) * factor)
                    text_end = min(float(item.get("textEnd", item.get("duration", 5))) * factor, hold)
                    scaled.append({**item, "duration": hold, "textEnd": text_end})
                media = scaled
        segments: list[Path] = []
        transitions = self.effective_transitions(media)
        # Resolve hold durations. Videos normally play through to the end of
        # the source file — the configured duration is only a floor, never a
        # ceiling that would cut the movie short before the next transition.
        # A trimmed movie is the exception: its "end" is the OUT point, so the
        # hold follows the kept section instead of the whole file.
        durations: list[float] = []
        native_video_durations: list[float | None] = []
        # Per item: the IN/OUT window of a trimmed movie, else None.
        video_windows: list[dict[str, float] | None] = []
        for item in media:
            if item.get("previewTrim"):
                # The two-clip transition preview is the transition and nothing
                # else: no hold on either side, so the sample opens and closes
                # with the crossfade and lasts exactly as long as it does.  The
                # segments still carry the full transition as their xfade
                # handle, which is where the outgoing and incoming pictures are
                # seen.  No probing: the caller has already cut the sources.
                durations.append(0.0)
                native_video_durations.append(None)
                video_windows.append(None)
                continue
            hold = max(.2, float(item.get("duration", 5)))
            native: float | None = None
            window: dict[str, float] | None = None
            if item.get("type") == "video":
                probed: float | None = None
                try:
                    probed = self._probe_duration(source_path(self.settings, item))
                except Exception as exc:
                    log.warning("Could not probe video duration for %s: %s", item.get("name"), exc)
                    probed = None
                if probed is not None and probed > 0:
                    window = video_trim_window(item, probed)
                    native = window["kept"] if window else probed
                    hold = max(hold, native)
            durations.append(hold)
            native_video_durations.append(native)
            video_windows.append(window)
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
            effect = str(item.get("effect", ""))
            ken_burns = kind_name == "image" and effect.startswith("Ken Burns")
            if kind_name == "image":
                # Photos are never cropped: fit the whole picture in the frame and
                # fill the letterbox bars with a blurred copy. Ken Burns clips are
                # fitted smaller so the zoom still cannot reach the picture edges.
                base_filter = fit_frame_filter(width, height, fps, KEN_BURNS_MAX_ZOOM if ken_burns else 1.0)
            else:
                base_filter = fill_frame_filter(width, height, fps)
            command = [self.settings.ffmpeg_bin, "-hide_banner", "-y"]
            clip_t = format_ffmpeg_number(duration)
            lead_in = transitions[index - 1] if index else 0.0
            lead_out = transitions[index] if index < len(transitions) else 0.0
            if kind_name == "title":
                # CSS gradients are useful in the editor preview but cannot be
                # rendered by FFmpeg's color source. Accept only an exact CSS
                # hex colour and convert it to FFmpeg's unambiguous 0xRRGGBB
                # syntax; this prevents a selected colour being parsed as the
                # black/default background on some FFmpeg builds.
                background = str(item.get("frameBackground", "#30382a"))
                if not re.fullmatch(r"#[0-9a-fA-F]{6}", background):
                    background = "#30382a"
                ffmpeg_background = "0x" + background[1:]
                command += ["-f", "lavfi", "-i", f"color=c={ffmpeg_background}:s={width}x{height}:r={fps}:d={clip_t}"]
                colour_change = frame_colour_change(item)
                if colour_change is not None:
                    # Second colour as another lavfi source; both are xfaded
                    # below with the user's transition. The caption is drawn
                    # afterwards so it stays fixed on top of the changing bed.
                    command += ["-f", "lavfi", "-i", f"color=c=0x{colour_change['to'][1:]}:s={width}x{height}:r={fps}:d={clip_t}"]
            else:
                source = source_path(self.settings, item)
                if not source.exists(): raise RenderError(f"Media file is missing: {source}")
                # Still images default to 25 fps. Without an explicit -framerate
                # matching the output, `-t 5` yields 125 frames which fps=30
                # then shortens to ~4.17s and every xfade offset is late.
                if kind_name == "image":
                    command += ["-loop", "1", "-framerate", str(fps), "-t", clip_t, "-i", str(source)]
                else:
                    # Play the movie once through. Never stream_loop a video:
                    # looping would restart the clip mid-hold or during the
                    # transition handle, cutting the story short visually.
                    # Transition handles and any hold beyond the native length
                    # are filled by freezing the first/last frame via tpad.
                    # A trimmed movie contributes only its IN/OUT section:
                    # -ss before -i is a fast input seek (frame-accurate since
                    # FFmpeg 2.1 because the decoder still walks to the exact
                    # PTS), and -t caps how much is read after it.
                    window = video_windows[index]
                    if item.get("previewTrim"):
                        seek = max(0.0, float(item.get("trimStart") or 0.0))
                        limit: float | None = None
                    elif window:
                        seek, limit = window["start"], window["kept"]
                    else:
                        seek, limit = 0.0, None
                    if seek > 0.001:
                        command += ["-ss", format_ffmpeg_number(seek)]
                    command += ["-i", str(source)]
                    if limit is not None and limit > 0.001:
                        command += ["-t", format_ffmpeg_number(limit)]
            # Everything that reshapes the picture itself (the quarter turn from
            # the preview popup, then straightening and the crop rectangle) runs
            # before the frame fit, so the blurred letterbox backdrop and the
            # Ken Burns zoom see the picture exactly the way the editor shows it.
            prefix: list[str] = []
            if kind_name == "image":
                turn = rotation_filter(item.get("rotation"))
                if turn:
                    prefix.append(turn)
            crop = normalize_crop(item) if kind_name != "title" else None
            if crop:
                prefix += crop_filters(crop)
            filters = prefix + [base_filter]
            if ken_burns:
                delta = "0.0008" if "Zoom in" in effect else "-0.0008" if "Zoom out" in effect else "0.0003"
                start_zoom = "1" if delta.startswith("0") else format_ffmpeg_number(KEN_BURNS_MAX_ZOOM)
                zoom = f"max(1,min({format_ffmpeg_number(KEN_BURNS_MAX_ZOOM)},{start_zoom}+on*{delta}))"
                # Anchor the zoom in the centre; zoompan otherwise defaults to the
                # top-left corner, which would push the picture out of frame.
                filters.append(
                    f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps={fps}"
                )
            if kind_name == "video":
                # Freeze the opening frame for the incoming xfade handle and the
                # closing frame for the outgoing handle (plus any extra hold the
                # user configured beyond the native runtime). The full movie
                # plays uninterrupted between those pads.
                native = native_video_durations[index]
                if native is not None and native > 0:
                    pad_start = lead_in
                    # After the native movie finishes we still need: remaining
                    # hold past native (if user extended duration) + outgoing
                    # transition.
                    pad_end = max(0.0, durations[index] - native) + lead_out
                    if pad_start > 0.0005 or pad_end > 0.0005:
                        filters.append(
                            f"tpad=start_mode=clone:start_duration={format_ffmpeg_number(pad_start)}"
                            f":stop_mode=clone:stop_duration={format_ffmpeg_number(pad_end)}"
                        )
                elif lead_in > 0.0005 or lead_out > 0.0005:
                    # Probe failed: still pad the transition handles by cloning
                    # edge frames, but do not invent a "native" length of 0
                    # which would turn the whole segment into frozen frames.
                    filters.append(
                        f"tpad=start_mode=clone:start_duration={format_ffmpeg_number(lead_in)}"
                        f":stop_mode=clone:stop_duration={format_ffmpeg_number(lead_out)}"
                    )
            # Picture looks (filters/effects chosen in the preview popup). They
            # run after zoompan, so they process output-sized frames instead of
            # 24 MP sources, and before drawtext, so captions keep their own
            # colour. The blurred letterbox backdrop is already part of the
            # frame at this point, which is why it picks the same look up —
            # exactly what the browser preview shows.
            if kind_name != "title":
                look = picture_look(item, width, height)
                if look:
                    filters.append(look)
            text_filter = self._text_filter(item, defaults, width, height)
            if text_filter: filters.append(text_filter)
            filters += ["format=yuv420p", "settb=AVTB", "setpts=PTS-STARTPTS"]
            colour_change = frame_colour_change(item) if kind_name == "title" else None
            # A lasso cut-out needs the mask as a second input, which -vf cannot
            # express: the hole is filled by compositing a blurred copy of the
            # same picture through the mask (see picture_crop.lasso_graph).
            cut_out = lasso_plan(crop)
            if cut_out is not None:
                mask_path = work / f"mask-{index:04d}.pgm"
                mask_path.write_bytes(lasso_mask_pgm(cut_out["points"]))
                command += lasso_inputs(mask_path, fps, duration)
                command += [
                    "-filter_complex", lasso_graph(cut_out, 1, prefix, filters[len(prefix):]),
                    "-map", "[v]",
                ]
            elif colour_change is not None:
                # The visible hold starts after the incoming xfade handle, so the
                # user's "start at" is shifted by lead_in inside this segment.
                offset = lead_in + colour_change["start"]
                graph = (
                    f"[0:v][1:v]xfade=transition={self.resolve_xfade(colour_change['transition'])}"
                    f":duration={format_ffmpeg_number(colour_change['time'])}:offset={format_ffmpeg_number(offset)}[bg];"
                    f"[bg]{','.join(filters)}[v]"
                )
                command += ["-filter_complex", graph, "-map", "[v]"]
            else:
                command += ["-vf", ",".join(filters)]
            command += ["-an", "-t", clip_t, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(segment)]
            self._run_ffmpeg(command, cancelled, log_file)
            segments.append(segment)
            progress(5 + 45 * (index + 1) / len(media), f"Prepared item {index+1} of {len(media)}")

        # Transition specs per gap (include easing/params/reverse). We keep resolved names for fallback logging but build filter via build_transition_xfade.
        xfade_names = [self.resolve_xfade(str(media[index].get("transition", "Fade"))) for index in range(len(media) - 1)]
        total_duration = sum(durations) + sum(transitions)
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

        def encode_args_for(codec: str, *, intermediate: bool) -> list[str]:
            if codec == "h264_qsv":
                args = ["-c:v", codec, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_value * 2:g}M", "-pix_fmt", "nv12"]
            else:
                preset = "veryfast" if intermediate else "medium"
                args = ["-c:v", codec, "-b:v", bitrate, "-maxrate", bitrate, "-bufsize", f"{bitrate_value * 2:g}M", "-preset", preset, "-pix_fmt", "yuv420p"]
            if not intermediate:
                args += ["-movflags", "+faststart"]
            return args

        def run_compose(command: list[str], *, allow_qsv_fallback: bool) -> None:
            try:
                self._run_ffmpeg(command, cancelled, log_file)
            except RenderError:
                if not allow_qsv_fallback or encoder != "h264_qsv":
                    raise
                progress(70, "Quick Sync unavailable; retrying on CPU")
                log.warning("h264_qsv failed; falling back to libx264")
                patched = list(command)
                try:
                    idx = patched.index("h264_qsv")
                    patched[idx] = "libx264"
                    if "-pix_fmt" in patched:
                        pix = patched.index("-pix_fmt")
                        patched[pix + 1] = "yuv420p"
                    if "-preset" not in patched:
                        patched[idx + 1:idx + 1] = ["-preset", "medium"]
                except ValueError:
                    raise
                self._run_ffmpeg(patched, cancelled, log_file)

        # Build the timeline as individual hold and transition units.  Do not
        # chain xfade filters: a chain has quadratic full-resolution work and
        # can stall (or fail) on CPU-only NAS hardware.  Each transition below
        # opens exactly two already-normalized clips, then FFmpeg's concat
        # demuxer joins compatible MP4s without decoding them again.
        timeline_parts: list[Path] = []
        for index, segment in enumerate(segments):
            if cancelled.is_set():
                raise RenderError("Render cancelled by user")
            lead_in = transitions[index - 1] if index else 0.0
            hold = durations[index]
            hold_part = work / f"hold-{index:04d}.mp4"
            hold_graph = (
                f"[0:v]trim=start={format_ffmpeg_number(lead_in)}:"
                f"end={format_ffmpeg_number(lead_in + hold)},"
                f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[vout]"
            )
            hold_command = [
                self.settings.ffmpeg_bin, "-hide_banner", "-y", "-i", str(segment),
                "-filter_complex", hold_graph, "-map", "[vout]", "-an",
                *encode_args_for(encoder, intermediate=True), "-r", str(fps),
                "-t", format_ffmpeg_number(hold), str(hold_part),
            ]
            progress(55 + 25 * (index + 1) / len(media), f"Preparing timeline item {index + 1} of {len(media)}")
            # A zero-length hold (the transition-only preview) must not be
            # rendered or listed at all: concatenating a 0-second clip leaves
            # the join with nothing to start from.
            if hold > 0.0005:
                run_compose(hold_command, allow_qsv_fallback=True)
                timeline_parts.append(hold_part)

            if index >= len(transitions):
                continue
            transition = transitions[index]
            transition_part = work / f"transition-{index:04d}.mp4"
            # Segment N has an outgoing cloned-frame handle immediately after
            # its hold. Segment N+1 starts with its incoming cloned-frame
            # handle. Fading those handles gives an additive transition without
            # stealing time from either clip's configured hold.
            # Build xfade with params/easing/reverse from the outgoing media's transition config
            xfade_fragment = self.build_transition_xfade(media[index], transition, 0.0)
            # xfade_fragment is like "xfade=transition=gl_cube(...):duration=1:offset=0:easing=...:reverse=..."
            transition_graph = (
                f"[0:v]trim=start={format_ffmpeg_number(lead_in + hold)}:"
                f"end={format_ffmpeg_number(lead_in + hold + transition)},"
                f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[outgoing];"
                f"[1:v]trim=start=0:end={format_ffmpeg_number(transition)},"
                f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[incoming];"
                f"[outgoing][incoming]{xfade_fragment},"
                f"settb=AVTB,setpts=PTS-STARTPTS,fps={fps}[vout]"
            )
            transition_command = [
                self.settings.ffmpeg_bin, "-hide_banner", "-y", "-i", str(segment),
                "-i", str(segments[index + 1]), "-filter_complex", transition_graph,
                "-map", "[vout]", "-an", *encode_args_for(encoder, intermediate=True),
                "-r", str(fps), "-t", format_ffmpeg_number(transition), str(transition_part),
            ]
            run_compose(transition_command, allow_qsv_fallback=True)
            timeline_parts.append(transition_part)

        concat_list = work / "timeline.ffconcat"
        # The work directory is renderer-owned. Escape a single quote anyway
        # so a custom CONFIG_DIR cannot make a valid manifest invalid.
        concat_list.write_text(
            "ffconcat version 1.0\n" + "".join(
                f"file '{str(part).replace(chr(39), chr(92) + chr(39))}'\n"
                for part in timeline_parts
            ),
            encoding="utf-8",
        )
        timeline = work / "timeline.mp4"
        concat_command = [
            self.settings.ffmpeg_bin, "-hide_banner", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_list), "-map", "0:v:0", "-an", "-c:v", "copy",
            "-movflags", "+faststart", "-t", format_ffmpeg_number(total_duration), str(timeline),
        ]
        progress(82, "Joining timeline")
        self._run_ffmpeg(concat_command, cancelled, log_file)

        # Audio is composed after the video timeline is complete. Selected
        # movie audio is delayed to the movie's visible hold start, while the
        # soundtrack fades down before and back up after the movie.
        audio_args: list[str] = []
        audio_map: list[str] = []
        audio_filter = ""
        original_movies = [(index, item) for index, item in enumerate(media)
                           if item.get("type") == "video" and item.get("audioSource") == "original"]
        if soundtrack or original_movies:
            # Input 0 is the already-concatenated silent video timeline.
            audio_index = 1
            if soundtrack:
                policy = project.get("soundtrack", {}).get("policy", "Loop & trim")
                if policy == "Loop & trim":
                    audio_args += ["-stream_loop", "-1"]
                audio_args += ["-i", str(soundtrack)]
                base_index = audio_index
                audio_index += 1
            else:
                audio_args += ["-f", "lavfi", "-t", format_ffmpeg_number(total_duration), "-i", "anullsrc=r=48000:cl=stereo"]
                base_index = audio_index
                audio_index += 1

            volume = max(0, min(1, float(project.get("soundtrack", {}).get("volume", 100)) / 100)) if soundtrack else 1
            envelopes: list[str] = []
            starts: list[float] = []
            cursor = 0.0
            for index, duration in enumerate(durations):
                starts.append(cursor)
                cursor += duration + (transitions[index] if index < len(transitions) else 0.0)
            for index, _item in original_movies:
                start_time = starts[index]
                end_time = start_time + durations[index]
                fade_in = transitions[index - 1] if index else 0.0
                fade_out = transitions[index] if index < len(transitions) else 0.0
                down_start = max(0.0, start_time - fade_in)
                up_end = end_time + fade_out
                if fade_in > 0.0005 and fade_out > 0.0005:
                    envelopes.append(f"if(lt(t,{format_ffmpeg_number(down_start)}),1,if(lt(t,{format_ffmpeg_number(start_time)}),({format_ffmpeg_number(start_time)}-t)/{format_ffmpeg_number(fade_in)},if(lt(t,{format_ffmpeg_number(end_time)}),0,if(lt(t,{format_ffmpeg_number(up_end)}),(t-{format_ffmpeg_number(end_time)})/{format_ffmpeg_number(fade_out)},1))))")
                elif fade_in > 0.0005:
                    envelopes.append(f"if(lt(t,{format_ffmpeg_number(down_start)}),1,if(lt(t,{format_ffmpeg_number(start_time)}),({format_ffmpeg_number(start_time)}-t)/{format_ffmpeg_number(fade_in)},if(lt(t,{format_ffmpeg_number(end_time)}),0,1)))")
                elif fade_out > 0.0005:
                    envelopes.append(f"if(lt(t,{format_ffmpeg_number(start_time)}),1,if(lt(t,{format_ffmpeg_number(end_time)}),0,if(lt(t,{format_ffmpeg_number(up_end)}),(t-{format_ffmpeg_number(end_time)})/{format_ffmpeg_number(fade_out)},1)))")
                else:
                    envelopes.append(f"if(between(t,{format_ffmpeg_number(start_time)},{format_ffmpeg_number(end_time)}),0,1)")
            bed_gain = "*".join(envelopes) if envelopes else "1"
            audio_filter = f"[{base_index}:a]volume='{format_ffmpeg_number(volume)}*({bed_gain})':eval=frame[bed]"
            fade = project.get("soundtrack", {}).get("fadeOut", True)
            if soundtrack and fade:
                fade_duration, fade_tail = soundtrack_fade_window(project.get("soundtrack", {}), total_duration)
                if fade_duration > 0:
                    fade_start = max(0.0, total_duration - fade_tail - fade_duration)
                    audio_filter = audio_filter.replace(
                        "[bed]",
                        f",afade=t=out:st={format_ffmpeg_number(fade_start)}:d={format_ffmpeg_number(fade_duration)}"
                        # Hard-mute the tail so looped music cannot creep back in
                        # after the fade has reached silence.
                        + (f",volume=enable='gte(t,{format_ffmpeg_number(fade_start + fade_duration)})':volume=0" if fade_tail > 0 else "")
                        + "[bed]",
                    )
            mix_labels = ["[bed]"]
            for movie_index, item in original_movies:
                source = source_path(self.settings, item)
                # The movie's own sound follows the same IN/OUT section as the
                # picture, otherwise the sub-clip would play the wrong audio.
                window = video_windows[movie_index]
                if window:
                    audio_args += ["-ss", format_ffmpeg_number(window["start"])]
                    audio_length = window["kept"]
                else:
                    audio_length = durations[movie_index]
                audio_args += ["-i", str(source)]
                movie_audio_index = audio_index
                audio_index += 1
                start_time = starts[movie_index]
                duration = durations[movie_index]
                fade_in = transitions[movie_index - 1] if movie_index else 0.0
                fade_out = transitions[movie_index] if movie_index < len(transitions) else 0.0
                original_filter = f"atrim=duration={format_ffmpeg_number(audio_length)},asetpts=PTS-STARTPTS"
                if fade_in > 0.0005:
                    original_filter += f",afade=t=in:st=0:d={format_ffmpeg_number(fade_in)}"
                if fade_out > 0.0005:
                    original_filter += f",afade=t=out:st={format_ffmpeg_number(max(0, duration - fade_out))}:d={format_ffmpeg_number(min(fade_out, duration))}"
                original_filter += f",adelay={int(round(start_time * 1000))}:all=1"
                label = f"moviea{movie_index}"
                audio_filter += f";[{movie_audio_index}:a]{original_filter}[{label}]"
                mix_labels.append(f"[{label}]")
            normalize, target = normalization_settings(project.get("soundtrack", {}))
            if normalize:
                # Final pass over the whole mix (music bed + any original movie
                # audio) so the programme as a whole lands on the target and
                # nothing clips after amix. Dynamic loudnorm keeps the fades,
                # ducking envelopes and the user's volume slider intact, and
                # the 200 ms afade at the very end masks loudnorm's tail.
                audio_filter += ";" + "".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0[mixed];"
                audio_filter += f"[mixed]{loudnorm_filter(target, None)},aresample=48000[aout]"
            else:
                audio_filter += ";" + "".join(mix_labels) + f"amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0[aout]"
            audio_map = ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"]

        command = [self.settings.ffmpeg_bin, "-hide_banner", "-y", "-i", str(timeline), *audio_args]
        if audio_filter:
            command += ["-filter_complex", audio_filter]
        command += ["-map", "0:v:0", *audio_map, "-c:v", "copy", "-t", format_ffmpeg_number(total_duration), "-movflags", "+faststart", str(output)]
        run_compose(command, allow_qsv_fallback=False)
        progress(98, "Finalizing MP4")
        return output

    def measure_loudness(self, source: Path, target: float, edit_filter: str = "", cancelled: threading.Event | None = None, log_file: Path | None = None) -> dict[str, float] | None:
        """First loudnorm pass: integrated loudness / true peak / LRA of a file.

        Returns None when FFmpeg cannot measure (unreadable file, silence), in
        which case callers fall back to dynamic normalisation.
        """
        graph = f"{edit_filter}loudnorm=I={format_ffmpeg_number(target)}:TP=-1.5:LRA=11:print_format=json"
        command = [self.settings.ffmpeg_bin, "-hide_banner", "-nostats", "-i", str(source), "-vn", "-af", graph, "-f", "null", "-"]
        if log_file is not None:
            with log_file.open("a", encoding="utf-8") as logs:
                logs.write("\n$ " + " ".join(command) + "\n")
        try:
            process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            while process.poll() is None:
                if cancelled is not None and cancelled.wait(.2):
                    process.terminate()
                    try: process.wait(5)
                    except subprocess.TimeoutExpired: process.kill()
                    raise RenderError("Render cancelled by user")
            stderr_text = process.stderr.read() if process.stderr else ""
        except OSError as exc:
            log.warning("Loudness measurement failed for %s: %s", source.name, exc)
            return None
        if process.returncode:
            log.warning("Loudness measurement of %s exited with %s", source.name, process.returncode)
            return None
        return parse_loudnorm_stats(stderr_text)

    def _probe_duration(self, path: Path) -> float:
        result=subprocess.run([self.settings.ffprobe_bin,"-v","error","-show_entries","format=duration","-of","json",str(path)],capture_output=True,text=True,timeout=30)
        if result.returncode: raise RenderError(f"Could not probe duration of {path.name}: {result.stderr}")
        return float(json.loads(result.stdout)["format"]["duration"])

    def _make_soundtrack(self, project: dict[str,Any], work: Path, cancelled: threading.Event, log_file: Path) -> Path | None:
        tracks=project.get("soundtrack",{}).get("tracks",[])
        if not tracks: return None
        sources=[]
        for track in tracks:
            source=source_path(self.settings, track)
            if not source.exists(): raise RenderError(f"Soundtrack is missing: {source}")
            sources.append(source)
        output=work/"soundtrack.m4a"
        inputs=[]
        for source in sources: inputs += ["-i",str(source)]
        normalize, target = normalization_settings(project.get("soundtrack", {}))
        per_track: list[str] = []
        for i in range(len(sources)):
            edit = track_edit_filter(tracks[i])
            if normalize:
                # First pass measures the *kept* region (after cut/crop, before
                # the user's fades so ramps do not skew the reading), second
                # pass applies a linear gain so every song matches `target`.
                stats = self.measure_loudness(sources[i], target, edit_filter=track_edit_filter({**tracks[i], "fadeIn": 0, "fadeOut": 0}), cancelled=cancelled, log_file=log_file)
                edit += loudnorm_filter(target, stats) + ","
            per_track.append(f"[{i}:a]{edit}aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
        normalized=";".join(per_track)
        concat="".join(f"[a{i}]" for i in range(len(sources)))+f"concat=n={len(sources)}:v=0:a=1[aout]"
        command=[self.settings.ffmpeg_bin,"-hide_banner","-y",*inputs,"-filter_complex",normalized+";"+concat,"-map","[aout]","-vn","-c:a","aac","-b:a","192k",str(output)]
        self._run_ffmpeg(command,cancelled,log_file)
        return output
