"""Dynamic text effects — extended with text motion path support (predefined paths + easing)."""
from __future__ import annotations

import json
import logging
import math
import os
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

def _registry_candidates() -> list[Path]:
    cands: list[Path] = []
    env = os.environ.get("SLIDESHOW_REGISTRY")
    if env:
        cands.append(Path(env))
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "registry" / "text-effects.json")
    cands.append(Path("/app/registry/text-effects.json"))
    cands.append(Path.cwd() / "registry" / "text-effects.json")
    return cands

def text_effect_catalog() -> list[dict[str, Any]]:
    for path in _registry_candidates():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = data.get("effects") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            continue
        cleaned: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            slot = str(entry.get("slot", "")).strip()
            label = str(entry.get("label", "")).strip()
            engine = str(entry.get("engine", "ass")).strip() or "ass"
            if slot not in ("enter", "while", "exit") or not label:
                continue
            cleaned.append({**entry, "slot": slot, "label": label, "engine": engine})
        if cleaned:
            return cleaned
    log.warning("registry/text-effects.json not found; text effects degrade to the legacy fade")
    return []

_CATALOG: list[dict[str, Any]] | None = None
_BY_LABEL: dict[str, dict[str, Any]] | None = None

def _by_label() -> dict[str, dict[str, Any]]:
    global _CATALOG, _BY_LABEL
    if _BY_LABEL is None:
        _CATALOG = text_effect_catalog()
        _BY_LABEL = {str(entry["label"]): entry for entry in _CATALOG}
    return _BY_LABEL

def effect_for(label: str | None, slot: str) -> dict[str, Any] | None:
    if label:
        entry = _by_label().get(str(label).strip())
        if entry and entry.get("slot") == slot:
            return entry
    return _by_label().get({"enter": "Fade", "exit": "Fade out", "while": "None (static)"}[slot])

def _n(value: float) -> str:
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text

def _num(item: dict[str, Any], key: str, default: float) -> float:
    try:
        value = float(item.get(key, default))
    except (TypeError, ValueError):
        return default
    return default if value != value else value

def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))

TEXT_TIMING_MIN_SECONDS = 0.1
TEXT_TIMING_MIN_CLIP_SECONDS = 0.2

def normalize_text_window(item: dict[str, Any]) -> tuple[float, float]:
    try:
        clip_duration = float(item.get("duration", 5.0))
    except (TypeError, ValueError):
        clip_duration = 5.0
    if not math.isfinite(clip_duration):
        clip_duration = 5.0
    clip_duration = max(TEXT_TIMING_MIN_CLIP_SECONDS, clip_duration)
    minimum = min(TEXT_TIMING_MIN_SECONDS, clip_duration)
    start_value = _num(item, "textStart", 0.0)
    start = _clamp(start_value, 0.0, max(0.0, clip_duration - minimum))
    end_value = _num(item, "textEnd", clip_duration)
    end = _clamp(end_value, start + minimum, clip_duration)
    return start, end

def ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"

def ass_ms(seconds: float) -> int:
    return int(round(max(0.0, seconds) * 1000))

def ass_colour(hex_colour: str) -> str:
    match = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hex_colour or "").strip())
    if not match:
        return "&HFFFFFF&"
    r, g, b = match.group(1)[0:2], match.group(1)[2:4], match.group(1)[4:6]
    return f"&H{b}{g}{r}&".upper()

def escape_ass(text: str) -> str:
    return str(text).replace("{", "(").replace("}", ")")

def ff_escape_drawtext(value: str) -> str:
    return (
        str(value)
        .replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace("%", r"\%")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )

# --------------------------------------------------------------------------
# Motion path helpers — including predefined paths and easing
# --------------------------------------------------------------------------

def _parse_move_path(raw) -> list[tuple[float,float]] | None:
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[tuple[float,float]] = []
    for pt in raw:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        try:
            x = float(pt[0]); y = float(pt[1])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        out.append((_clamp(x, 0.0, 100.0), _clamp(y, 0.0, 100.0)))
    if len(out) < 2:
        return None
    if len(out) > 120:
        step = len(out) / 120
        sampled = [out[int(i*step)] for i in range(120)]
        sampled[-1] = out[-1]
        out = sampled
    return out

def _path_length(points: list[tuple[float,float]]) -> float:
    total = 0.0
    for i in range(1, len(points)):
        dx = points[i][0] - points[i-1][0]
        dy = points[i][1] - points[i-1][1]
        total += math.hypot(dx, dy)
    return total

def _point_along_path(points: list[tuple[float,float]], progress: float) -> tuple[float,float]:
    if not points:
        return (50.0, 50.0)
    if len(points) == 1:
        return points[0]
    p = max(0.0, min(1.0, progress))
    total = _path_length(points)
    if total < 1e-6:
        return points[0]
    target = total * p
    for i in range(1, len(points)):
        seg = math.hypot(points[i][0]-points[i-1][0], points[i][1]-points[i-1][1])
        if target <= seg:
            t = 0.0 if seg == 0 else target/seg
            return (
                points[i-1][0] + (points[i][0]-points[i-1][0])*t,
                points[i-1][1] + (points[i][1]-points[i-1][1])*t,
            )
        target -= seg
    return points[-1]

def _simplify_for_dt(points: list[tuple[float,float]], max_points=20) -> list[tuple[float,float]]:
    if len(points) <= max_points:
        return points
    step = (len(points)-1) / (max_points-1)
    return [points[int(round(i*step))] for i in range(max_points)]

# Easing — Python side for ASS and for preview
def _ease_progress(p: float, easing: str) -> float:
    p = max(0.0, min(1.0, p))
    if easing == "ease-in":
        return p*p
    if easing == "ease-out":
        return 1 - (1-p)*(1-p)
    if easing == "ease-in-out":
        if p < 0.5:
            return 2*p*p
        return 1 - 2*(1-p)*(1-p)
    if easing == "smooth":
        # cubic in-out
        if p < 0.5:
            return 4*p*p*p
        return 1 - pow(-2*p+2, 3)/2
    return p  # linear

def _eased_dt_expr(p_expr: str, easing: str) -> str:
    """Return FFmpeg eval expression for eased progress from raw p_expr (0..1)."""
    if easing == "linear" or not easing:
        return p_expr
    if easing == "ease-in":
        return f"pow({p_expr},2)"
    if easing == "ease-out":
        return f"(1-pow(1-{p_expr},2))"
    if easing == "ease-in-out":
        return f"if(lt({p_expr},0.5),2*pow({p_expr},2),1-2*pow(1-{p_expr},2))"
    if easing == "smooth":
        return f"if(lt({p_expr},0.5),4*pow({p_expr},3),1-pow(-2*{p_expr}+2,3)/2)"
    return p_expr

def _generate_circle_points(
    from_x: float, from_y: float, to_x: float, to_y: float,
    radius: float | None, turns: float, num_points: int = 64
) -> list[tuple[float,float]]:
    # Center = from point if radius defined, else midpoint
    if radius is not None and radius > 0:
        cx, cy = from_x, from_y
        r = radius
    else:
        # radius from distance
        cx = (from_x + to_x) / 2.0
        cy = (from_y + to_y) / 2.0
        dist = math.hypot(to_x - from_x, to_y - from_y)
        r = dist / 2.0
        if r < 1.0:
            r = 15.0
            cx, cy = from_x, from_y
    turns = max(0.1, min(4.0, turns))
    pts: list[tuple[float,float]] = []
    for i in range(num_points+1):
        ang = (i / num_points) * turns * 2 * math.pi
        # Start angle from from point relative to center, if center is midpoint we start at angle to from
        # Compute start angle offset
        if radius is None or radius <= 0:
            # start angle is angle from center to from
            start_ang = math.atan2(from_y - cy, from_x - cx)
        else:
            start_ang = 0.0
        a = start_ang + ang
        x = cx + r * math.cos(a)
        y = cy + r * math.sin(a)
        pts.append((_clamp(x, 0.0, 100.0), _clamp(y, 0.0, 100.0)))
    return pts

def _generate_sine_points(
    from_x: float, from_y: float, to_x: float, to_y: float,
    amplitude: float, frequency: float, num_points: int = 80
) -> list[tuple[float,float]]:
    amp = max(0.0, min(40.0, amplitude))
    freq = max(0.1, min(10.0, frequency))
    # Base vector
    dx = to_x - from_x
    dy = to_y - from_y
    length = math.hypot(dx, dy)
    if length < 1e-6:
        # vertical sine if no direction
        pts = []
        for i in range(num_points+1):
            p = i / num_points
            x = from_x + amp * math.sin(freq * 2 * math.pi * p)
            y = from_y + (to_y - from_y) * p if abs(to_y-from_y)>1e-6 else from_y + p*20
            pts.append((_clamp(x,0,100), _clamp(y,0,100)))
        return pts
    # Unit along base
    ux = dx / length
    uy = dy / length
    # Perpendicular unit
    px = -uy
    py = ux
    pts: list[tuple[float,float]] = []
    for i in range(num_points+1):
        p = i / num_points
        bx = from_x + dx * p
        by = from_y + dy * p
        offset = amp * math.sin(freq * 2 * math.pi * p)
        x = bx + px * offset
        y = by + py * offset
        pts.append((_clamp(x,0,100), _clamp(y,0,100)))
    return pts

def _effective_motion_points(
    from_x: float, from_y: float, to_x: float, to_y: float,
    path: list[tuple[float,float]] | None,
    path_type: str,
    circle_radius: float | None,
    circle_turns: float,
    sine_amp: float,
    sine_freq: float,
) -> list[tuple[float,float]] | None:
    pt = path_type or "straight"
    if pt == "freehand" and path and len(path) >= 2:
        return path
    if pt == "circle":
        return _generate_circle_points(from_x, from_y, to_x, to_y, circle_radius, circle_turns)
    if pt == "sine":
        return _generate_sine_points(from_x, from_y, to_x, to_y, sine_amp, sine_freq)
    # straight or fallback
    if path and len(path) >= 2 and pt == "straight":
        # if user has drawn path but selected straight, ignore path
        pass
    # For straight, if freehand exists but type is straight, use straight
    if abs(from_x-to_x) < 0.01 and abs(from_y-to_y) < 0.01:
        return None
    return [(from_x, from_y), (to_x, to_y)]

# --------------------------------------------------------------------------
# Shared geometry
# --------------------------------------------------------------------------

@dataclass
class TextGeometry:
    width: int
    height: int
    size: int
    cx: float
    cy: float
    start: float
    end: float
    di: float
    do: float
    speed: float
    text: str
    lines: list[str]
    family: str
    bold: bool
    italic: bool
    underline: bool
    colour: str
    params: dict[str, str]
    outline: bool = True
    # Motion
    move_enabled: bool = False
    move_from_x: float = 50.0
    move_from_y: float = 50.0
    move_to_x: float = 50.0
    move_to_y: float = 50.0
    move_path: list[tuple[float,float]] | None = None
    move_path_type: str = "straight"
    move_easing: str = "linear"
    move_circle_radius: float | None = None
    move_circle_turns: float = 1.0
    move_sine_amp: float = 8.0
    move_sine_freq: float = 2.0

    def motion_points(self) -> list[tuple[float,float]] | None:
        if not self.move_enabled:
            return None
        return _effective_motion_points(
            self.move_from_x, self.move_from_y, self.move_to_x, self.move_to_y,
            self.move_path, self.move_path_type,
            self.move_circle_radius, self.move_circle_turns,
            self.move_sine_amp, self.move_sine_freq,
        )

    def motion_pos_at(self, t: float) -> tuple[float,float]:
        hold = max(1e-6, self.end - self.start)
        prog_raw = _clamp((t - self.start) / hold, 0.0, 1.0)
        prog = _ease_progress(prog_raw, self.move_easing)
        pts = self.motion_points()
        if not pts:
            return (self.cx, self.cy)
        pct = _point_along_path(pts, prog)
        return (self.width * pct[0] / 100.0, self.height * pct[1] / 100.0)

    def motion_pct_at(self, progress_raw: float) -> tuple[float,float]:
        """Percent coords at raw progress 0..1 with easing applied."""
        prog = _ease_progress(max(0.0, min(1.0, progress_raw)), self.move_easing)
        pts = self.motion_points()
        if not pts:
            return (self.move_from_x, self.move_from_y)
        return _point_along_path(pts, prog)

def _geometry(item: dict[str, Any], defaults: dict[str, Any], width: int, height: int) -> TextGeometry | None:
    text = str(item.get("text", "")).strip()
    if not text:
        return None
    if item.get("type") != "title" and item.get("textEnabled") is False:
        return None
    start, end = normalize_text_window(item)
    hold = end - start
    di = _clamp(_num(item, "textEnterDuration", 0.5), 0.05, hold * 0.9)
    do = _clamp(_num(item, "textExitDuration", 0.5), 0.05, hold * 0.9)
    speed_default = 2.0 if item.get("type") == "title" else _num(defaults, "textFxWhileSpeed", 2.0)
    speed = _clamp(_num(item, "textFxWhileSpeed", speed_default), 0.4, 12.0)
    if item.get("type") == "title":
        size_pt = _num(item, "fontSize", 48)
        colour_raw = str(item.get("fontColor") or "#ffffff")
        bold = bool(item.get("textBold", True))
        italic = bool(item.get("textItalic", False))
        underline = bool(item.get("textUnderline", False))
        family = str(item.get("fontFamily") or "Montserrat")
        outline = False
        def_x = _num(item, "textX", 50.0)
        def_y = _num(item, "textY", 50.0)
    else:
        if item.get("type") in ("image", "video") or "textOutline" in item:
            size_pt = _num(item, "fontSize", _num(defaults, "fontSize", 48))
            colour_raw = str(item.get("fontColor") or defaults.get("fontColor") or "#ffffff")
            bold = bool(item["textBold"]) if "textBold" in item else bool(defaults.get("bold", True))
            italic = bool(item["textItalic"]) if "textItalic" in item else bool(defaults.get("italic", False))
            underline = bool(item["textUnderline"]) if "textUnderline" in item else bool(defaults.get("underline", False))
            family = str(item.get("fontFamily") or defaults.get("fontFamily") or "Montserrat")
            outline = item["textOutline"] is not False if "textOutline" in item else defaults.get("outline", True) is not False
            def_x = _num(item, "textX", _num(defaults, "textX", 50.0))
            def_y = _num(item, "textY", _num(defaults, "textY", 72.0))
        else:
            size_pt = _num(defaults, "fontSize", 48)
            colour_raw = str(defaults.get("fontColor") or "#ffffff")
            bold = bool(defaults.get("bold", True))
            italic = bool(defaults.get("italic", False))
            underline = bool(defaults.get("underline", False))
            family = str(defaults.get("fontFamily") or "Montserrat")
            outline = defaults.get("outline", True) is not False
            def_x = _num(defaults, "textX", 50.0)
            def_y = _num(defaults, "textY", 72.0)
    raw_params = item.get("textFxParams")
    params = {str(k): str(v) for k, v in raw_params.items()} if isinstance(raw_params, dict) else {}
    colour = colour_raw if re.fullmatch(r"#[0-9a-fA-F]{6}", colour_raw or "") else "#ffffff"
    cx = width * _clamp(def_x, 0.0, 100.0) / 100.0
    cy = height * _clamp(def_y, 0.0, 100.0) / 100.0

    move_enabled = bool(item.get("textMoveEnabled"))
    move_from_x = _clamp(_num(item, "textMoveFromX", def_x), 0.0, 100.0)
    move_from_y = _clamp(_num(item, "textMoveFromY", def_y), 0.0, 100.0)
    move_to_x = _clamp(_num(item, "textMoveToX", move_from_x), 0.0, 100.0)
    move_to_y = _clamp(_num(item, "textMoveToY", move_from_y), 0.0, 100.0)
    move_path = _parse_move_path(item.get("textMovePath"))
    move_path_type = str(item.get("textMovePathType") or ("freehand" if move_path else "straight")).lower()
    if move_path_type not in ("straight", "freehand", "circle", "sine"):
        move_path_type = "freehand" if move_path else "straight"
    move_easing = str(item.get("textMoveEasing") or "linear").lower()
    if move_easing not in ("linear", "ease-in", "ease-out", "ease-in-out", "smooth"):
        move_easing = "linear"
    # circle params
    circle_radius_raw = item.get("textMoveCircleRadius")
    circle_radius = None
    if circle_radius_raw is not None:
        try:
            circle_radius = _clamp(float(circle_radius_raw), 0.0, 50.0)
        except (TypeError, ValueError):
            circle_radius = None
    circle_turns = _clamp(_num(item, "textMoveCircleTurns", 1.0), 0.1, 4.0)
    sine_amp = _clamp(_num(item, "textMoveSineAmplitude", 8.0), 0.0, 40.0)
    sine_freq = _clamp(_num(item, "textMoveSineFrequency", 2.0), 0.1, 10.0)

    return TextGeometry(
        width=width, height=height,
        size=max(8, int(float(size_pt) * width / 1920)),
        cx=cx, cy=cy,
        start=start, end=end, di=di, do=do, speed=speed,
        text=text, lines=text.split("\n"),
        family=family, bold=bold, italic=italic, underline=underline, colour=colour, params=params,
        outline=outline,
        move_enabled=move_enabled,
        move_from_x=move_from_x, move_from_y=move_from_y,
        move_to_x=move_to_x, move_to_y=move_to_y,
        move_path=move_path,
        move_path_type=move_path_type,
        move_easing=move_easing,
        move_circle_radius=circle_radius,
        move_circle_turns=circle_turns,
        move_sine_amp=sine_amp,
        move_sine_freq=sine_freq,
    )

@dataclass
class FxPlan:
    enter: dict[str, Any]
    while_: dict[str, Any]
    exit_: dict[str, Any]
    def ids(self) -> tuple[str, str, str]:
        return str(self.enter.get("id")), str(self.while_.get("id")), str(self.exit_.get("id"))

# --------------------------------------------------------------------------
# Engine 1 — animated drawtext expressions
# --------------------------------------------------------------------------

def _dt_alpha(g: TextGeometry) -> str:
    si, ei = g.start + g.di, g.end - g.do
    return (
        f"if(lt(t,{_n(g.start)}),0,if(lt(t,{_n(si)}),min(1,(t-{_n(g.start)})/{_n(g.di)}),"
        f"if(lt(t,{_n(ei)}),1,if(lt(t,{_n(g.end)}),max(0,({_n(g.end)}-t)/{_n(g.do)}),0))))"
    )

def _dt_offsets(g: TextGeometry, enter: str, exit_: str, while_: str) -> tuple[str, str]:
    xs: list[str] = []
    ys: list[str] = []
    u_in = f"clip((t-{_n(g.start)})/{_n(g.di)},0,1)"
    u_out = f"clip((t-({_n(g.end)}-{_n(g.do)}))/{_n(g.do)},0,1)"
    u_loop = f"mod((t-{_n(g.start)})/{_n(max(0.4, g.speed))},1)"
    slide = g.width * 0.3
    drop = g.height * 0.24
    if enter == "Slide from left":
        xs.append(f"{_n(slide)}*(1-{u_in})")
    elif enter == "Slide from right":
        xs.append(f"-{_n(slide)}*(1-{u_in})")
    elif enter == "Slide from top":
        ys.append(f"-{_n(g.height * 0.18)}*(1-{u_in})")
    elif enter == "Slide from bottom":
        ys.append(f"{_n(g.height * 0.18)}*(1-{u_in})")
    elif enter == "Rise & settle":
        u = f"clip((t-{_n(g.start)})/{_n(g.di)},0,2)"
        ys.append(f"{_n(g.height * 0.08)}*exp(-5*{u})*cos(7*{u})")
    elif enter == "Drop & bounce":
        ys.append(
            f"if(lt({u_in},0.55),-{_n(drop)}*(1-pow({u_in}/0.55,2)),"
            f"-{_n(drop * 0.22)}*abs(sin(({u_in}-0.55)*12.566))*(1-{u_in}))"
        )
    elif enter == "Slide & overshoot":
        c1, c3 = 1.70158, 2.70158
        u = f"clip((t-{_n(g.start)})/{_n(g.di)},0,1)"
        xs.append(f"-{_n(slide * 0.8)}*({c3}*pow({u}-1,3)+{c1}*pow({u}-1,2)+1)")
    if while_ == "Gentle float":
        ys.append(f"-{_n(g.height * 0.012)}*sin(2*PI*{u_loop})")
    elif while_ == "Horizontal drift":
        xs.append(f"{_n(g.width * 0.02)}*sin(2*PI*{u_loop})")
    if exit_ == "Slide out left":
        xs.append(f"-{_n(slide)}*{u_out}")
    elif exit_ == "Slide out right":
        xs.append(f"{_n(slide)}*{u_out}")
    elif exit_ == "Slide out top":
        ys.append(f"-{_n(g.height * 0.18)}*{u_out}")
    elif exit_ == "Slide out bottom":
        ys.append(f"{_n(g.height * 0.18)}*{u_out}")
    elif exit_ == "Sink & fade":
        ys.append(f"{_n(g.height * 0.05)}*{u_out}*{u_out}")
    return (f"+({'+'.join(xs)})" if xs else "", f"+({'+'.join(ys)})" if ys else "")

def _dt_motion_exprs(g: TextGeometry) -> tuple[str, str] | None:
    pts = g.motion_points()
    if not pts:
        return None
    hold = max(1e-6, g.end - g.start)
    p_raw = f"clip((t-{_n(g.start)})/{_n(hold)},0,1)"
    p_expr = _eased_dt_expr(p_raw, g.move_easing)
    if len(pts) == 2:
        (fx, fy), (tx, ty) = pts[0], pts[1]
        dx = tx - fx
        dy = ty - fy
        x_expr = f"({_n(fx)}+{_n(dx)}*{p_expr})"
        y_expr = f"({_n(fy)}+{_n(dy)}*{p_expr})"
        return (x_expr, y_expr)
    simple = _simplify_for_dt(pts, 20)
    total = _path_length(simple)
    if total < 1e-6:
        return (f"{_n(simple[0][0])}", f"{_n(simple[0][1])}")
    cum = 0.0
    thresholds: list[float] = [0.0]
    for i in range(1, len(simple)):
        seg = math.hypot(simple[i][0]-simple[i-1][0], simple[i][1]-simple[i-1][1])
        cum += seg
        thresholds.append(cum/total)
    def lerp_expr(p0, p1, t0, t1, axis):
        x0 = p0[0] if axis=='x' else p0[1]
        x1 = p1[0] if axis=='x' else p1[1]
        dt = t1 - t0
        if dt < 1e-6:
            return _n(x1)
        return f"({_n(x0)}+({_n(x1-x0)})*({p_expr}-{_n(t0)})/{_n(dt)})"
    x_last = lerp_expr(simple[-2], simple[-1], thresholds[-2], thresholds[-1], 'x')
    y_last = lerp_expr(simple[-2], simple[-1], thresholds[-2], thresholds[-1], 'y')
    x_expr = x_last
    y_expr = y_last
    for i in range(len(simple)-3, -1, -1):
        t1 = thresholds[i+1]
        x_i = lerp_expr(simple[i], simple[i+1], thresholds[i], thresholds[i+1], 'x')
        y_i = lerp_expr(simple[i], simple[i+1], thresholds[i], thresholds[i+1], 'y')
        x_expr = f"if(lt({p_expr},{_n(t1)}),{x_i},{x_expr})"
        y_expr = f"if(lt({p_expr},{_n(t1)}),{y_i},{y_expr})"
    return (x_expr, y_expr)

def _drawtext_filter(g: TextGeometry, font: str, enter: str, exit_: str, while_: str) -> str:
    motion = _dt_motion_exprs(g)
    if enter == "Fade" and while_ in ("", "None (static)") and exit_ == "Fade out" and not motion:
        fade_in = max(0.01, g.di)
        fade_out = max(0.01, g.do)
        alpha = (
            f"if(lt(t,{_n(g.start)}),0,if(lt(t,{_n(g.start + fade_in)}),(t-{_n(g.start)})/{_n(fade_in)},"
            f"if(lt(t,{_n(g.end - fade_out)}),1,if(lt(t,{_n(g.end)}),({_n(g.end)}-t)/{_n(fade_out)},0))))"
        )
        x_expr = f"(w-text_w)*{_n(g.cx / g.width)}"
        y_expr = f"(h-text_h)*{_n(g.cy / g.height)}"
    else:
        alpha = _dt_alpha(g)
        dx, dy = _dt_offsets(g, enter, exit_, while_)
        if motion:
            x_pct, y_pct = motion
            x_expr = f"(w-text_w)*{x_pct}/100{dx}"
            y_expr = f"(h-text_h)*{y_pct}/100{dy}"
        else:
            x_expr = f"(w-text_w)*{_n(g.cx / g.width)}{dx}"
            y_expr = f"(h-text_h)*{_n(g.cy / g.height)}{dy}"
    return (
        f"drawtext=fontfile='{font}':text='{ff_escape_drawtext(g.text)}':fontsize={g.size}"
        f":fontcolor=0x{g.colour[1:]}:alpha='{alpha}':x='{x_expr}':y='{y_expr}'"
        f":shadowcolor=black@0.55:shadowx=2:shadowy=2{_dt_outline(g)}:enable='between(t,{_n(g.start)},{_n(g.end)})'"
    )

def outline_width(g: TextGeometry) -> int:
    return max(1, round(g.size / 16)) if g.outline else 0

def _dt_outline(g: TextGeometry) -> str:
    w = outline_width(g)
    return f":borderw={w}:bordercolor=black@0.85" if w else ""

# --------------------------------------------------------------------------
# Engine 2 — libass
# --------------------------------------------------------------------------

CHAR_W = 0.66
LINE_H = 1.3
SCRAMBLE_GLYPHS = "#@$%&*+=<>?/\\|"
SHADOW_STYLE = "&H73000000&"
OUTLINE_STYLE = "&H26000000&"

def _header(g: TextGeometry) -> str:
    style = (
        f"Style: FX,{g.family.replace(',', ' ')},{g.size},{ass_colour(g.colour)},&HFFFFFF&,{OUTLINE_STYLE},{SHADOW_STYLE},"
        f"{'-1' if g.bold else '0'},{'-1' if g.italic else '0'},{'-1' if g.underline else '0'},0,100,100,0,0,1,{outline_width(g)},2,5,0,0,0,1"
    )
    return (
        "[Script Info]\n"
        "; Slideshow dynamic text effects — generated by backend/app/text_effects.py\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {g.width}\n"
        f"PlayResY: {g.height}\n"
        "ScaledBorderAndShadow: yes\n"
        "WrapStyle: 2\n"
        "\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"{style}\n"
        "\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    )

def _ev(layer: int, start: float, end: float, tags: str, text: str) -> str:
    return f"Dialogue: {layer},{ass_time(start)},{ass_time(max(end, start + 0.02))},FX,,0,0,0,,{{{tags}}}{text}"

def _multiline(g: TextGeometry) -> str:
    return r"\N".join(escape_ass(line) for line in g.lines)

def _pos_tags(g: TextGeometry) -> str:
    return f"\\an5\\pos({g.cx:.0f},{g.cy:.0f})"

def _motion_move_tag(g: TextGeometry) -> str | None:
    pts = g.motion_points()
    if not pts:
        return None
    if len(pts) == 2:
        (fx, fy), (tx, ty) = pts[0], pts[1]
        # For easing, we cannot use single move with easing; we will generate sampled moves in _motion_path_events_eased
        # But for linear without easing, use single move
        if g.move_easing != "linear":
            return None
        x1 = g.width * fx / 100.0
        y1 = g.height * fy / 100.0
        x2 = g.width * tx / 100.0
        y2 = g.height * ty / 100.0
        hold_ms = int(round((g.end - g.start)*1000))
        return f"\\an5\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f},0,{hold_ms})"
    return None

def _text_bbox(g: TextGeometry) -> tuple[int, int, int, int]:
    longest = max((len(line) for line in g.lines), default=1)
    w = longest * g.size * CHAR_W + g.size * 0.4
    h = len(g.lines) * g.size * LINE_H
    cx, cy = g.motion_pos_at(g.start) if g.move_enabled else (g.cx, g.cy)
    return int(cx - w / 2), int(cy - h / 2), int(cx + w / 2), int(cy + h / 2)

def _loop_tags(g: TextGeometry, pattern: Callable[[int, int, int], str]) -> str:
    hold_ms = int(round((g.end - g.start) * 1000))
    period = max(200, int(round(g.speed * 1000)))
    count = max(1, min(30, int(math.ceil(hold_ms / period))))
    return "".join(pattern(cycle, cycle * period, period) for cycle in range(count))

def _while_tags(g: TextGeometry, while_id: str) -> str:
    if while_id == "slow-zoom":
        hold_ms = int(round((g.end - g.start) * 1000))
        return f"\\t(0,{hold_ms},\\fscx110\\fscy110)"
    if while_id == "pulse":
        def pulse(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\fscx112\\fscy112)\\t({t0 + half},{t0 + period},\\fscx100\\fscy100)"
        return _loop_tags(g, pulse)
    if while_id == "neon-glow":
        def breathe(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\blur6)\\t({t0 + half},{t0 + period},\\blur1)"
        return f"\\bord3\\3c{ass_colour(g.colour)}" + _loop_tags(g, breathe)
    if while_id == "colour-cycle":
        palette = ["&H55D7FF&", "&H66FFB2&", "&HFF9C66&", "&HB29CFF&"]
        seg = max(100, int(round(g.speed * 1000)) // len(palette))
        def cycle_colours(cycle: int, t0: int, period: int) -> str:
            parts = []
            for k in range(len(palette)):
                parts.append(f"\\t({t0 + k * seg},{t0 + (k + 1) * seg},\\1c{palette[(cycle + k) % len(palette)]})")
            return "".join(parts)
        return _loop_tags(g, cycle_colours)
    if while_id == "gentle-float":
        def float_bob(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\fscy103\\fscx99)\\t({t0 + half},{t0 + period},\\fscy100\\fscx100)"
        return _loop_tags(g, float_bob)
    if while_id == "horizontal-drift":
        def sway(cycle: int, t0: int, period: int) -> str:
            half = max(1, period // 2)
            return f"\\t({t0},{t0 + half},\\frz0.8)\\t({t0 + half},{t0 + period},\\frz-0.8)"
        return _loop_tags(g, sway)
    return ""

def _stagger_loop_tags(g: TextGeometry, count: int, pattern: Callable[[int, int, int, int], str]) -> list[str]:
    hold_ms = int(round((g.end - g.start) * 1000))
    period = max(200, int(round(g.speed * 1000)))
    cycles = max(1, min(30, int(math.ceil(hold_ms / period))))
    phase = period / max(1, count)
    out = []
    for unit in range(count):
        offset = int(round((unit % max(1, count)) * phase)) % period
        out.append("".join(pattern(cycle, cycle * period, period, offset) for cycle in range(cycles)))
    return out

def _units(g: TextGeometry, by_word: bool) -> list[str]:
    units: list[str] = []
    for line in g.lines:
        if by_word:
            words = line.split(" ")
            units.extend(word + " " for word in words)
            if units:
                units[-1] = units[-1].rstrip(" ") or "\n"
        else:
            units.extend(line)
        units.append("\n")
    return [unit for unit in units if unit != ""]

def _visible_units(units: list[str]) -> list[int]:
    return [i for i, unit in enumerate(units) if unit not in ("\n", " ")]

def _inline_units(units: list[str], tag_for: Callable[[int, str], str]) -> str:
    parts: list[str] = []
    for index, unit in enumerate(units):
        if unit == "\n":
            parts.append(r"\N")
        elif unit == " ":
            parts.append(" ")
        else:
            tags = tag_for(index, unit)
            parts.append(f"{{{tags}}}{escape_ass(unit)}" if tags else escape_ass(unit))
    return "".join(parts)

def _hidden_line_text(units: list[str], visible_index: int, visible_tags: str) -> str:
    parts = ["{\\alpha&HFF&}"]
    for index, unit in enumerate(units):
        if unit == "\n":
            parts.append(r"\N")
        elif index == visible_index:
            parts.append(f"{{\\alpha&H00&{visible_tags}}}{escape_ass(unit)}")
        else:
            parts.append(escape_ass(unit) if unit != " " else " ")
    return "".join(parts)

def _alpha_at(g: TextGeometry, t: float) -> str:
    si, ei = g.start + g.di, g.end - g.do
    if t < g.start or t >= g.end:
        return "&HFF&"
    if si > g.start and t < si:
        return f"&H{int(0xFF * (1 - (t - g.start) / max(1e-6, g.di))):02X}&"
    if g.end > ei and t > ei:
        return f"&H{int(0xFF * min(1.0, (t - ei) / max(1e-6, g.do))):02X}&"
    return "&H00&"

def _structural_while_events(g: TextGeometry, while_id: str) -> list[str]:
    hold = g.end - g.start
    tick = max(1.0 / 10.0, hold / 240.0)
    seed = int(g.start * 1000) + len(g.text) * 7919 + sum(ord(ch) for ch in g.text[:24])
    rng = random.Random(seed)
    events: list[str] = []
    if while_id == "count-up":
        try:
            lo = float(str(g.params.get("from", "0")).strip() or 0)
        except ValueError:
            lo = 0.0
        try:
            hi = float(str(g.params.get("to", "100")).strip() or 0)
        except ValueError:
            hi = 100.0
        steps = max(2, min(240, int(round(hold / tick))))
        for i in range(steps):
            t0 = g.start + hold * i / steps
            value = int(round(lo + (hi - lo) * (i / (steps - 1))))
            mx, my = g.motion_pos_at(t0 + tick/2) if g.move_enabled else (g.cx, g.cy)
            events.append(_ev(0, t0, g.start + hold * (i + 1) / steps,
                              f"\\an5\\pos({mx:.0f},{my:.0f})\\alpha{_alpha_at(g, t0 + tick / 2)}", str(value)))
        return events
    amp = max(2.0, g.size * 0.05)
    t = g.start
    while t < g.end - 1e-3:
        end = min(t + tick, g.end)
        mid = t + tick / 2
        alpha = _alpha_at(g, mid)
        mx, my = g.motion_pos_at(mid) if g.move_enabled else (g.cx, g.cy)
        if while_id == "shake":
            dx, dy = rng.uniform(-amp, amp), rng.uniform(-amp, amp)
            events.append(_ev(0, t, end, f"\\an5\\pos({mx + dx:.0f},{my + dy:.0f})\\alpha{alpha}", _multiline(g)))
        else:
            roll = rng.random()
            if roll >= 0.12:
                jitter = max(1, int(g.size * 0.04))
                dx, dy = rng.randint(-jitter, jitter), rng.randint(-jitter // 2, jitter // 2)
                tags = f"\\an5\\pos({mx + dx:.0f},{my + dy:.0f})\\alpha{alpha}"
                if roll > 0.78:
                    tags += f"\\3c{'&H5533FF&' if roll > 0.89 else '&HFF6644&'}\\bord{max(2, g.size // 26)}"
                events.append(_ev(0, t, end, tags, _multiline(g)))
        t = end
    if not events:
        mx, my = g.motion_pos_at(g.start) if g.move_enabled else (g.cx, g.cy)
        events.append(_ev(0, g.start, g.end, f"\\an5\\pos({mx:.0f},{my:.0f})", _multiline(g)))
    return events

def _char_stream(g: TextGeometry) -> list[str]:
    chars: list[str] = []
    for li, line in enumerate(g.lines):
        chars.extend(line)
        if li < len(g.lines) - 1:
            chars.append("\n")
    return chars

def _join_chars(chars: list[str]) -> str:
    return escape_ass("".join(chars)).replace("\n", r"\N")

def _sliced_enter_events(g: TextGeometry, fx: FxPlan, while_tags: str, exit_tags: str, end_at: float | None = None) -> list[str]:
    enter_id = str(fx.enter.get("id"))
    chars = _char_stream(g)
    total = len(chars)
    if not total:
        pos = _pos_tags(g) if not g.move_enabled else f"\\an5\\pos({g.motion_pos_at(g.start)[0]:.0f},{g.motion_pos_at(g.start)[1]:.0f})"
        return [_ev(0, g.start, end_at or g.end, pos + while_tags + exit_tags, _multiline(g))]
    step = max(0.03, g.di / total)
    seed = int(g.di * 1000) + total * 31 + sum(ord(ch) for ch in g.text[:24])
    rng = random.Random(seed)
    events: list[str] = []
    final_end = end_at or g.end
    for i in range(total):
        t0 = g.start + i * step
        t1 = t0 + step if i < total - 1 else final_end
        mid = (t0 + t1)/2
        mx, my = g.motion_pos_at(mid) if g.move_enabled else (g.cx, g.cy)
        pos_tag = f"\\an5\\pos({mx:.0f},{my:.0f})"
        if enter_id == "typewriter-caret":
            text = _join_chars(chars[: i + 1]) + "{\\alpha&H77&}|"
        else:
            remaining = [c for c in chars[i + 1:] if c != "\n"]
            noise = "".join(rng.choice(SCRAMBLE_GLYPHS) for _ in remaining)
            text = _join_chars(chars[: i + 1]) + escape_ass(noise)
        events.append(_ev(0, t0, t1, pos_tag + while_tags + exit_tags, text))
    return events

def _sliced_exit_appends(g: TextGeometry, exit_id: str) -> tuple[float, list[str]]:
    chars = _char_stream(g)
    total = max(1, len(chars))
    hold_end = max(g.start + 0.1, g.end - g.do)
    step = max(0.03, g.do / total)
    seed = int(g.do * 1000) + total * 17 + sum(ord(ch) for ch in g.text[:24])
    rng = random.Random(seed)
    events: list[str] = []
    for i in range(total):
        t0 = hold_end + i * step
        t1 = t0 + step if i < total - 1 else g.end
        mid = (t0 + t1)/2
        mx, my = g.motion_pos_at(mid) if g.move_enabled else (g.cx, g.cy)
        pos_tag = f"\\an5\\pos({mx:.0f},{my:.0f})"
        if exit_id == "typewriter-delete":
            text = _join_chars(chars[: max(0, total - 1 - i)])
        else:
            keep = max(0, total - 1 - i)
            noise_len = max(0, total - keep - "".join(chars[keep:]).count("\n"))
            noise = "".join(rng.choice(SCRAMBLE_GLYPHS) for _ in range(noise_len))
            text = _join_chars(chars[:keep]) + escape_ass(noise)
        if not text.replace(r"\N", "").strip():
            continue
        events.append(_ev(0, t0, t1, pos_tag, text))
    return hold_end - 0.02, events

def _split_rise_events(g: TextGeometry, while_tags: str, exit_tags: str, end_at: float) -> list[str]:
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    stagger = g.di / count
    move_ms = ass_ms(max(0.22, min(0.45, stagger * 3)))
    rise = int(g.size * 0.9)
    events: list[str] = []
    for order, index in enumerate(visible):
        offset_ms = ass_ms(stagger * order)
        base_cx, base_cy = g.motion_pos_at(g.start) if g.move_enabled else (g.cx, g.cy)
        tags = (
            f"\\an5\\move({base_cx:.0f},{base_cy + rise},{base_cx:.0f},{base_cy:.0f},{offset_ms},{offset_ms + move_ms})"
            f"{while_tags}{exit_tags}"
        )
        char_tags = f"\\alpha&HFF&\\t({offset_ms},{offset_ms + 40},\\alpha&H00&)"
        events.append(_ev(0, g.start, end_at, tags, _hidden_line_text(units, index, char_tags)))
    return events

def _split_centre_events(g: TextGeometry, while_tags: str, exit_tags: str, end_at: float) -> list[str]:
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    stagger = g.di / count
    grow_ms = ass_ms(max(0.25, min(0.5, stagger * 3)))
    events: list[str] = []
    base_cx, base_cy = g.motion_pos_at(g.start) if g.move_enabled else (g.cx, g.cy)
    for order, index in enumerate(visible):
        offset_ms = ass_ms(stagger * order)
        tags = f"\\an5\\pos({base_cx:.0f},{base_cy:.0f})\\org({base_cx:.0f},{base_cy:.0f}){while_tags}{exit_tags}"
        char_tags = (
            f"\\alpha&HFF&\\fscx0\\fscy0\\t({offset_ms},{offset_ms + 40},\\alpha&H00&)"
            f"\\t({offset_ms},{offset_ms + grow_ms},\\fscx100\\fscy100)"
        )
        events.append(_ev(0, g.start, end_at, tags, _hidden_line_text(units, index, char_tags)))
    return events

def _wipe_tags(g: TextGeometry, effect: dict[str, Any], enter: bool) -> str:
    left, top, right, bottom = _text_bbox(g)
    pad = max(4, g.size // 4)
    left, top, right, bottom = left - pad, top - pad, right + pad, bottom + pad
    direction = str(effect.get("id", "")).replace("wipe-from-", "").replace("wipe-out-", "")
    span_ms = int(round((g.end - g.start) * 1000))
    ms = max(60, int(round((g.di if enter else g.do) * 1000)))
    t0, t1 = (0, ms) if enter else (max(0, span_ms - ms), span_ms)
    def box(x1: int, y1: int, x2: int, y2: int) -> str:
        return f"\\clip({min(x1, x2)},{min(y1, y2)},{max(x1, x2)},{max(y1, y2)})"
    closed, full = {
        "left": (box(left, top, left, bottom), box(left, top, right, bottom)),
        "right": (box(right, top, right, bottom), box(left, top, right, bottom)),
        "top": (box(left, top, right, top), box(left, top, right, bottom)),
        "bottom": (box(left, bottom, right, bottom), box(left, top, right, bottom)),
        "top-left": (box(left, top, left, top), box(left, top, right, bottom)),
        "top-right": (box(right, top, right, top), box(left, top, right, bottom)),
        "bottom-left": (box(left, bottom, left, bottom), box(left, top, right, bottom)),
        "bottom-right": (box(right, bottom, right, bottom), box(left, top, right, bottom)),
    }.get(direction, (box(left, top, left, bottom), box(left, top, right, bottom)))
    if enter:
        return f"{closed}\\t({t0},{t1},{full})"
    return f"{full}\\t({t0},{t1},{closed})"

def _lower_third_events(g: TextGeometry, enter_id: str, exit_id: str, while_tags: str, extra_tags: str) -> list[str]:
    left, top, right, bottom = _text_bbox(g)
    pad = max(4, g.size // 3)
    bar_w = (right - left) + pad * 2
    bar_h = int(g.size * 1.55)
    bx, by = int(g.cx - bar_w / 2), int(g.cy - bar_h / 2)
    if g.move_enabled:
        mx, my = g.motion_pos_at(g.start)
        bx = int(mx - bar_w / 2)
        by = int(my - bar_h / 2)
    slide = int(bar_w * 1.2 + g.size * 2)
    enter_ms = ass_ms(g.di) if enter_id == "lower-third" else 240
    retract = exit_id == "lower-third-retract"
    exit_ms = ass_ms(g.do) if retract else 240
    span_ms = int(round((g.end - g.start) * 1000))
    exit_t0 = max(enter_ms + 10, span_ms - exit_ms)
    bar_tags = f"\\an7\\p1\\1c&H000000&\\bord0\\move({bx - slide},{by},{bx},{by},0,{enter_ms})\\alpha&H4D&"
    if retract:
        bar_tags += f"\\org({bx},{by + bar_h // 2})\\t({exit_t0},{span_ms},\\fscx0\\alpha&HFF&)"
    else:
        bar_tags += f"\\fad(0,{exit_ms})"
    bar_body = f"m 0 0 l {bar_w} 0 l {bar_w} {bar_h} l 0 {bar_h}{{\\p0}}"
    if g.move_enabled:
        move_tag = _motion_move_tag(g)
        if move_tag:
            text_tags = move_tag
        else:
            # For complex motion, sample path
            pts = g.motion_points()
            if pts and len(pts) >= 2:
                # Use first segment for bar? For text we will generate motion events separately
                mx, my = g.motion_pos_at(g.start)
                text_tags = f"\\an5\\pos({mx:.0f},{my:.0f})"
            else:
                mx, my = g.motion_pos_at(g.start)
                text_tags = f"\\an5\\pos({mx:.0f},{my:.0f})"
    else:
        text_tags = f"\\move({g.cx - slide:.0f},{g.cy:.0f},{g.cx:.0f},{g.cy:.0f},0,{enter_ms})" if enter_id == "lower-third" else _pos_tags(g)
    return [
        _ev(0, g.start, g.end, bar_tags, bar_body),
        _ev(1, g.start, g.end, text_tags + extra_tags + while_tags, _multiline(g)),
    ]

def _enter_tags(g: TextGeometry, effect: dict[str, Any]) -> tuple[str, str | None]:
    enter_id = str(effect.get("id"))
    di = ass_ms(g.di)
    tags = ""
    body: str | None = None
    move_based_enters = {"slide-from-left", "slide-from-right", "slide-from-top", "slide-from-bottom", "rise-settle", "drop-bounce", "slide-overshoot"}
    if g.move_enabled and enter_id in move_based_enters:
        tags += f"\\fad({di},0)"
        return tags, body
    if enter_id == "fade":
        tags += f"\\fad({di},0)"
    elif enter_id in ("slide-from-left", "slide-from-right", "slide-from-top", "slide-from-bottom", "rise-settle", "drop-bounce", "slide-overshoot"):
        dx, dy = {
            "slide-from-left": (-g.width * 0.3, 0.0),
            "slide-from-right": (g.width * 0.3, 0.0),
            "slide-from-top": (0.0, -g.height * 0.18),
            "slide-from-bottom": (0.0, g.height * 0.18),
            "rise-settle": (0.0, g.height * 0.08),
            "drop-bounce": (0.0, -g.height * 0.24),
            "slide-overshoot": (-g.width * 0.24, 0.0),
        }[enter_id]
        if enter_id in ("rise-settle", "drop-bounce", "slide-overshoot"):
            tags += (f"\\move({g.cx + dx:.0f},{g.cy + dy:.0f},{g.cx:.0f},{g.cy:.0f},0,{di})"
                     f"\\fad({max(1, di // 2)},0)"
                     f"\\t({int(di * 0.6)},{di},\\fscx106\\fscy106)\\t({di},{int(di * 1.35) + 1},\\fscx100\\fscy100)")
        else:
            tags += f"\\move({g.cx + dx:.0f},{g.cy + dy:.0f},{g.cx:.0f},{g.cy:.0f},0,{di})\\fad({di},0)"
    elif enter_id == "blur-in":
        tags += f"\\blur10\\alpha&HFF&\\t(0,{di},\\blur0\\alpha&H00&)"
    elif enter_id == "flicker-in":
        seg = max(1, di // 5)
        jump = max(1, di // 12)
        tags += "\\alpha&HFF&"
        for k, alpha in enumerate(("&H55&", "&HAA&", "&H22&", "&H66&", "&H00&")):
            tags += f"\\t({k * seg},{k * seg + jump},\\alpha{alpha})"
    elif enter_id == "pop-in":
        peak = max(1, int(di * 0.65))
        tags += f"\\fscx18\\fscy18\\alpha&H40&\\t(0,{peak},\\fscx114\\fscy114\\alpha&H00&)\\t({peak},{di},\\fscx100\\fscy100)"
    elif enter_id == "zoom-down":
        tags += f"\\fscx300\\fscy300\\alpha&HFF&\\t(0,{di},\\fscx100\\fscy100\\alpha&H00&)"
    elif enter_id == "flip-in":
        tags += f"\\fscy12\\alpha&H55&\\t(0,{di},\\fscy100\\alpha&H00&)"
    elif enter_id == "rotate-in":
        tags += f"\\frz-84\\alpha&HFF&\\t(0,{di},\\frz0\\alpha&H00&)"
    elif enter_id.startswith("wipe-from-"):
        tags += _wipe_tags(g, effect, enter=True)
    elif enter_id in ("split-fade-chars", "split-fade-words"):
        by_word = enter_id == "split-fade-words"
        units = _units(g, by_word)
        visible = _visible_units(units)
        stagger = g.di / max(1, len(visible))
        fade_ms = max(60 if not by_word else 80, ass_ms(stagger * 2))
        def staggered(index: int, unit: str) -> str:
            order = visible.index(index)
            t0 = ass_ms(stagger * order)
            return f"\\alpha&HFF&\\t({t0},{t0 + fade_ms},\\alpha&H00&)"
        body = _inline_units(units, staggered)
    elif enter_id == "typewriter":
        total = max(1, sum(len(line) for line in g.lines))
        step_cs = max(1, int(round(g.di * 100 / total)))
        tags += "\\2a&HFF&"
        pieces: list[str] = []
        for li, line in enumerate(g.lines):
            for ch in line:
                pieces.append(f"{{\\k{step_cs}}}{escape_ass(ch)}" if ch != " " else " ")
            if li < len(g.lines) - 1:
                pieces.append(r"\N")
        body = "".join(pieces)
    return tags, body

def _exit_tags(g: TextGeometry, effect: dict[str, Any]) -> str:
    exit_id = str(effect.get("id"))
    do = ass_ms(g.do)
    span = int(round((g.end - g.start) * 1000))
    t0 = max(0, span - do)
    move_based_exits = {"slide-out-left", "slide-out-right", "slide-out-top", "slide-out-bottom", "sink-fade"}
    if g.move_enabled and exit_id in move_based_exits:
        return f"\\fad(0,{do})"
    if exit_id in ("fade-out", "none"):
        return f"\\fad(0,{do})"
    if exit_id == "blur-out":
        return f"\\t({t0},{span},\\blur10\\alpha&HFF&)"
    if exit_id == "pop-out":
        return f"\\t({t0},{span},\\fscx0\\fscy0\\alpha&HFF&)"
    if exit_id == "rotate-out":
        return f"\\t({t0},{span},\\frz84\\alpha&HFF&)"
    if exit_id.startswith("wipe-out-"):
        return _wipe_tags(g, effect, enter=False)
    if exit_id in ("slide-out-left", "slide-out-right", "slide-out-top", "slide-out-bottom", "sink-fade"):
        dx, dy = {
            "slide-out-left": (-g.width * 0.3, 0.0),
            "slide-out-right": (g.width * 0.3, 0.0),
            "slide-out-top": (0.0, -g.height * 0.18),
            "slide-out-bottom": (0.0, g.height * 0.18),
            "sink-fade": (0.0, g.height * 0.06),
        }[exit_id]
        return f"\\move({g.cx:.0f},{g.cy:.0f},{g.cx + dx:.0f},{g.cy + dy:.0f},{t0},{span})\\fad(0,{do})"
    if exit_id == "split-out-chars":
        return "\\fad(0," + str(do) + ")"
    return f"\\fad(0,{do})"

def _karaoke_sweep_body(g: TextGeometry) -> tuple[str, str]:
    total = max(1, sum(len(line) for line in g.lines))
    step_cs = max(1, int(round((g.end - g.start) * 100 / total)))
    pieces: list[str] = []
    for li, line in enumerate(g.lines):
        for ch in line:
            pieces.append(f"{{\\kf{step_cs}}}{escape_ass(ch)}" if ch != " " else " ")
        if li < len(g.lines) - 1:
            pieces.append(r"\N")
    return "\\2c&H40C8FF&", "".join(pieces)

def _wave_shimmer_body(g: TextGeometry, while_id: str) -> str:
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    if while_id == "wave":
        def pattern(cycle: int, t0: int, period: int, phase: int) -> str:
            half = max(1, period // 4)
            up0, up1 = t0 + phase, t0 + phase + half * 2
            return f"\\t({up0},{up1},\\fscy128)\\t({up1},{up1 + half * 2},\\fscy100)"
        loops = _stagger_loop_tags(g, count, pattern)
        base = ""
    else:
        def pattern(cycle: int, t0: int, period: int, phase: int) -> str:
            third = max(1, period // 3)
            a, b = t0 + phase, t0 + phase + third
            return f"\\t({a},{b},\\1c&HFFFFFF&)\\t({b},{b + third},\\1c&HC8C8C8&)"
        loops = _stagger_loop_tags(g, count, pattern)
        base = "\\1c&HC8C8C8&"
    order = {index: k for k, index in enumerate(visible)}
    def tag_for(index: int, unit: str) -> str:
        if index not in order:
            return ""
        return base + loops[order[index]]
    return _inline_units(units, tag_for)

def _split_out_body(g: TextGeometry) -> str:
    units = _units(g, by_word=False)
    visible = _visible_units(units)
    count = max(1, len(visible))
    stagger_ms = int(round(g.do * 1000 / count))
    span = int(round((g.end - g.start) * 1000))
    t0_base = max(0, span - ass_ms(g.do))
    def shrink(index: int, unit: str) -> str:
        order = visible.index(index)
        t0 = t0_base + stagger_ms * order
        return f"\\t({t0},{span},\\alpha&HFF&\\fscy84\\fscx90)"
    return _inline_units(units, shrink)

def _motion_path_events(g: TextGeometry, while_tags: str, enter_tags: str, exit_tags: str, body: str) -> list[str]:
    pts = g.motion_points()
    if not pts or len(pts) < 2:
        return []
    total_len = _path_length(pts)
    if total_len < 1e-6 and len(pts) < 3:
        return []
    hold = g.end - g.start

    # If easing is not linear, sample with easing to approximate speed fade
    if g.move_easing != "linear":
        # Sample many points along eased progress
        # Use 60 samples for smooth easing
        num_samples = 60
        # For complex paths with many points, we still sample eased progress along path length
        # Generate eased progress values
        sampled_pts: list[tuple[float,float]] = []
        for i in range(num_samples+1):
            raw = i / num_samples
            eased = _ease_progress(raw, g.move_easing)
            sampled_pts.append(_point_along_path(pts, eased))
        # Now create events between sampled points with equal time slices
        events: list[str] = []
        slice_dur = hold / num_samples
        for i in range(num_samples):
            t0 = g.start + i * slice_dur
            t1 = t0 + slice_dur
            mid = (t0 + t1)/2
            alpha = _alpha_at(g, mid)
            x1 = g.width * sampled_pts[i][0] / 100.0
            y1 = g.height * sampled_pts[i][1] / 100.0
            x2 = g.width * sampled_pts[i+1][0] / 100.0
            y2 = g.height * sampled_pts[i+1][1] / 100.0
            seg_ms = int(round(slice_dur*1000))
            move = f"\\an5\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f},0,{seg_ms})\\alpha{alpha}"
            tags = move + while_tags
            if i == 0 and "\\move(" not in enter_tags:
                tags = f"\\an5\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f},0,{seg_ms})" + enter_tags + while_tags + f"\\alpha{alpha}"
            if i == num_samples-1 and "\\move(" not in exit_tags:
                tags += exit_tags
            events.append(_ev(0, t0, t1, tags, body))
        return events

    # Linear easing — use original logic with distance-proportional timing
    cum = 0.0
    thresholds = [0.0]
    for i in range(1, len(pts)):
        seg = math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1])
        cum += seg
        thresholds.append(cum/total_len if total_len>1e-9 else i/(len(pts)-1))
    events: list[str] = []
    for i in range(len(pts)-1):
        t0 = g.start + hold * thresholds[i]
        t1 = g.start + hold * thresholds[i+1]
        if t1 <= t0 + 1e-3:
            continue
        mid = (t0 + t1)/2
        alpha = _alpha_at(g, mid)
        x1 = g.width * pts[i][0] / 100.0
        y1 = g.height * pts[i][1] / 100.0
        x2 = g.width * pts[i+1][0] / 100.0
        y2 = g.height * pts[i+1][1] / 100.0
        seg_ms = int(round((t1 - t0)*1000))
        move = f"\\an5\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f},0,{seg_ms})\\alpha{alpha}"
        tags = move + while_tags
        if i == 0:
            if "\\move(" not in enter_tags:
                tags = f"\\an5\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f},0,{seg_ms})" + enter_tags + while_tags + f"\\alpha{alpha}"
            else:
                tags = f"\\an5\\move({x1:.0f},{y1:.0f},{x2:.0f},{y2:.0f},0,{seg_ms})" + enter_tags + while_tags
        if i == len(pts)-2:
            if "\\move(" not in exit_tags:
                tags += exit_tags
        events.append(_ev(0, t0, t1, tags, body))
    return events

def build_ass_document(g: TextGeometry, plan: FxPlan) -> str:
    enter_id, while_id, exit_id = plan.ids()
    while_structural = while_id in ("shake", "glitch-flicker", "count-up")

    if while_structural:
        events = _structural_while_events(g, while_id)
        return _header(g) + "\n" + "\n".join(events) + "\n"

    exit_sliced = exit_id in ("typewriter-delete", "scramble-out")
    end_at = g.end
    tail_events: list[str] = []
    if exit_sliced:
        end_at, tail_events = _sliced_exit_appends(g, exit_id)

    while_tags = "" if while_id in ("none", "karaoke-sweep", "wave", "shimmer") else _while_tags(g, while_id)
    if while_id == "karaoke-sweep" and enter_id == "typewriter":
        while_id = "none"
    karaoke_tags = ""

    if enter_id == "lower-third" or exit_id == "lower-third-retract":
        enter_extra = ""
        if enter_id != "lower-third":
            enter_extra, _ = _enter_tags(g, plan.enter)
        events = _lower_third_events(g, enter_id, exit_id, while_tags, enter_extra + _exit_tags(g, plan.exit_))
        return _header(g) + "\n" + "\n".join(events) + "\n"

    if enter_id in ("typewriter-caret", "decrypted-scramble"):
        events = _sliced_enter_events(g, plan, while_tags, _exit_tags(g, plan.exit_), end_at=end_at if exit_sliced else None)
        return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"

    if enter_id == "split-rise-chars":
        events = _split_rise_events(g, while_tags, _degrade_move_exit(_exit_tags(g, plan.exit_)), end_at)
        return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"
    if enter_id == "split-from-centre":
        events = _split_centre_events(g, while_tags, _degrade_move_exit(_exit_tags(g, plan.exit_)), end_at)
        return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"

    enter_tags, enter_body = _enter_tags(g, plan.enter)
    if while_id == "karaoke-sweep":
        karaoke_tags, body = _karaoke_sweep_body(g)
    elif while_id in ("wave", "shimmer"):
        body = _wave_shimmer_body(g, while_id)
    else:
        body = enter_body if enter_body is not None else _multiline(g)
    if exit_id == "split-out-chars":
        body = _split_out_body(g)
    exit_tags = _exit_tags(g, plan.exit_)

    pts = g.motion_points()
    if pts and len(pts) >= 2:
        # For any motion with >=2 points, use motion path events (handles easing, circle, sine)
        # Special case: linear without easing and only 2 points can use single move for efficiency
        if len(pts) == 2 and g.move_easing == "linear" and g.move_path_type == "straight":
            move_tag = _motion_move_tag(g)
            if move_tag:
                events = [_ev(0, g.start, end_at, move_tag + enter_tags + while_tags + karaoke_tags + exit_tags, body)]
                return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"
        # Otherwise use sampled path events (handles easing, circle, sine, freehand)
        motion_events = _motion_path_events(g, while_tags, enter_tags, exit_tags, body)
        if motion_events:
            return _header(g) + "\n" + "\n".join(motion_events + tail_events) + "\n"

    if "\\move(" in enter_tags:
        pos = "\\an5"
        exit_tags = _degrade_move_exit(exit_tags)
    else:
        if g.move_enabled:
            mx, my = g.motion_pos_at(g.start)
            pos = f"\\an5\\pos({mx:.0f},{my:.0f})"
            mt = _motion_move_tag(g)
            if mt:
                pos = mt
        else:
            pos = _pos_tags(g)
    events = [_ev(0, g.start, end_at, pos + enter_tags + while_tags + karaoke_tags + exit_tags, body)]
    return _header(g) + "\n" + "\n".join(events + tail_events) + "\n"

def _degrade_move_exit(exit_tags: str) -> str:
    if "\\move(" in exit_tags:
        match = re.search(r"\\fad\(\d+,\d+\)", exit_tags)
        return match.group(0) if match else ""
    return exit_tags

def overlay_plan(item: dict[str, Any]) -> FxPlan | None:
    probe = _geometry(item, {}, 1920, 1080)
    if probe is None:
        return None
    return FxPlan(
        enter=effect_for(item.get("textFxEnter"), "enter") or effect_for("Fade", "enter"),
        while_=effect_for(item.get("textFxWhile"), "while") or effect_for("None (static)", "while"),
        exit_=effect_for(item.get("textFxExit"), "exit") or effect_for("Fade out", "exit"),
    )

def plan_engine(plan: FxPlan) -> str:
    engines = {str(plan.enter.get("engine")), str(plan.while_.get("engine")), str(plan.exit_.get("engine"))}
    return "dt" if engines <= {"dt", "none"} else "ass"

def build_text_overlay(
    item: dict[str, Any],
    defaults: dict[str, Any],
    width: int,
    height: int,
    fonts_dir: Path | str,
    ass_path: Path | None,
    font_resolver: Callable[[str, bool, bool, Path], str] | None = None,
    force_ass: bool = False,
) -> str | None:
    plan = overlay_plan(item)
    if plan is None:
        return None
    g = _geometry(item, defaults, width, height)
    if g is None:
        return None
    engine = plan_engine(plan)
    if engine == "dt" and (not g.underline or ass_path is None) and not (force_ass and ass_path is not None):
        resolver = font_resolver or (lambda family, bold, italic, fonts: str(fonts))
        font = resolver(g.family, g.bold, g.italic, Path(fonts_dir))
        return _drawtext_filter(
            g, font,
            str(plan.enter.get("label")), str(plan.exit_.get("label")), str(plan.while_.get("label")),
        )
    document = build_ass_document(g, plan)
    if ass_path is None:
        log.warning("Chosen text effects need libass but no ass_path was given; drawing no text")
        return None
    ass_path.parent.mkdir(parents=True, exist_ok=True)
    ass_path.write_text(document, encoding="utf-8")
    escaped_path = str(ass_path).replace("\\", "/").replace("'", r"\'")
    escaped_fonts = str(fonts_dir).replace("\\", "/").replace("'", r"\'")
    return f"ass=filename='{escaped_path}':fontsdir='{escaped_fonts}'"
