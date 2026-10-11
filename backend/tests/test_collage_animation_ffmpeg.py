"""Render photo choreography through the real FFmpeg collage graph.

The JavaScript preview already animates a swing using photoState(). This
regression test checks the exported filter graph too: a photo rotating around
its top pin must move visibly across frames, rather than becoming a static
image because of a malformed rotate-angle expression.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest

from app.collage import (
    ENTRANCE_LENGTH,
    MOTION_RAMP,
    collage_duration,
    collage_graph,
    normalize_collage,
    photo_anim,
    photo_start,
    photo_state,
)

W, H, FPS = 320, 180, 25


def _ffmpeg_bin() -> str | None:
    env = os.environ.get("FFMPEG_BIN")
    if env and ((os.path.isabs(env) and os.path.exists(env)) or shutil.which(env)):
        return env
    binary = shutil.which("ffmpeg")
    if binary:
        return binary
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _render_swing(ffmpeg: str) -> list[bytes]:
    """Render one bright-red, borderless photo over a gray bed and return
    grayscale redness masks, so the photo's centroid can be tracked."""
    item = {"collage": {
        "layout": "grid",
        "animation": "swing",
        "shape": "4:3",
        "seed": 1,
        "hold": 2,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": "/photos/swing-test.jpg"}],
    }}
    spec = normalize_collage(item)
    assert spec is not None
    duration = collage_duration(spec)
    lines, last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
    redness = "clip(r(X,Y)-max(g(X,Y),b(X,Y)),0,255)"
    graph = (
        f"[0:v]null[cb];{''.join(lines)}"
        f"[{last}]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[comp];"
        f"[comp]geq=r='{redness}':g='{redness}':b='{redness}',format=gray[v]"
    )
    frame_count = int(round(duration * FPS))
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x606060:s={W}x{H}:r={FPS}:d={duration}",
        "-f", "lavfi", "-i", f"color=c=red:s=180x120:r={FPS}:d={duration}",
        "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frame_count),
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise AssertionError(f"ffmpeg failed:\n{result.stderr.decode(errors='replace')[-3000:]}\n{graph}")
    frame_size = W * H
    if len(result.stdout) != frame_count * frame_size:
        raise AssertionError(f"expected {frame_count} frames, got {len(result.stdout) / frame_size:.2f}")
    return [result.stdout[i:i + frame_size] for i in range(0, len(result.stdout), frame_size)]


def _centroid(frame: bytes) -> tuple[float, float]:
    total = x_sum = y_sum = 0
    for y in range(H):
        row = y * W
        for x in range(W):
            value = frame[row + x]
            if value <= 12:  # ignore the gray bed and codec-edge noise
                continue
            total += value
            x_sum += x * value
            y_sum += y * value
    if not total:
        raise AssertionError("the red collage photo is missing from the rendered frame")
    return x_sum / total, y_sum / total


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class CollageAnimationRenderTest(unittest.TestCase):
    def test_swing_animation_moves_in_the_rendered_video(self):
        frames = _render_swing(str(_ffmpeg_bin()))
        # Once the entrance fade is over, the preview's damped pendulum curve
        # moves the photo centre substantially as it swings around its top pin.
        early = _centroid(frames[round(0.4 * FPS)])
        later = _centroid(frames[round(0.8 * FPS)])
        travel = ((later[0] - early[0]) ** 2 + (later[1] - early[1]) ** 2) ** 0.5
        self.assertGreater(travel, 6.0, f"rendered swing barely moved: {early} -> {later}")


def _render_depth_collage(ffmpeg: str, workdir: str) -> subprocess.CompletedProcess:
    """Render the first frames of a depth-stacked native-shape collage.

    The depth push-back puts an eq/sendcmd dimming chain on the photos, which
    keeps each scaled photo in 4:2:0. Native photos use a decrease-scale
    followed by an exact pad; with an odd target height FFmpeg can scale one
    row taller than asked, so the pad failed with exit 234 ("Padded dimensions
    cannot be smaller than input dimensions").
    """
    photos = []
    for i in range(3):
        path = os.path.join(workdir, f"photo{i}.jpg")
        subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                        "-i", "testsrc2=s=4624x3468:d=0.04", "-frames:v", "1", path],
                       check=True, timeout=60)
        photos.append(path)
    item = {"collage": {
        "layout": "masonry",
        "animation": "drop",
        "depth": True,
        "shape": "native",
        "seed": 3,
        "hold": 2,
        "frame": {"shape": "classic"},
        "photos": [{"path": p} for p in photos],
    }}
    spec = normalize_collage(item)
    assert spec is not None
    duration = min(collage_duration(spec), 6.0)
    lines, last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
    graph = (f"[0:v]null[cb];{''.join(lines)}"
             f"[{last}]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[v]")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-f", "lavfi", "-i", f"color=c=0x606060:s={W}x{H}:r={FPS}:d={duration}"]
    for path in photos:
        command += ["-loop", "1", "-framerate", str(FPS), "-t", str(duration), "-i", path]
    command += ["-filter_complex", graph, "-map", "[v]", "-frames:v", "8",
                "-f", "null", "-"]
    return subprocess.run(command, capture_output=True, timeout=180)


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class CollageOddNativeHeightRenderTest(unittest.TestCase):
    def test_depth_collage_with_odd_native_mat_heights_renders(self):
        import tempfile
        with tempfile.TemporaryDirectory() as workdir:
            result = _render_depth_collage(str(_ffmpeg_bin()), workdir)
        stderr = result.stderr.decode(errors="replace")
        self.assertEqual(result.returncode, 0, f"collage graph failed to render:\n{stderr[-2000:]}")
        self.assertNotIn("cannot be smaller than input dimensions", stderr)


def _render_free_tilt(ffmpeg: str, tilt: float) -> tuple[int, int, int, int, int]:
    """Render one borderless 4:3 photo on a gray bed at a Free tilt and return
    the red mask's bounding box plus its pixel count.

    The photo is 30 % of the 320 px frame wide (96 px) and 72 px tall, so a
    quarter turn has to come back as a 72 x 96 box carrying the same number of
    pixels: the sprite canvas is padded before ffmpeg rotates it, and a canvas
    sized only for the small tilts clips the turned mat's corners (or fails the
    pad outright once the canvas is narrower than the mat).
    """
    item = {"collage": {
        "layout": "free",
        "animation": "none",
        "shape": "4:3",
        "seed": 1,
        "hold": 1,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": "/photos/tilt.jpg", "cx": 50, "cy": 50, "w": 30, "rot": tilt}],
    }}
    spec = normalize_collage(item)
    assert spec is not None
    duration = collage_duration(spec)
    lines, last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
    redness = "clip(r(X,Y)-max(g(X,Y),b(X,Y)),0,255)"
    graph = (
        f"[0:v]null[cb];{''.join(lines)}"
        f"[{last}]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[comp];"
        f"[comp]geq=r='{redness}':g='{redness}':b='{redness}',format=gray[v]"
    )
    frame_count = int(round(duration * FPS))
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x606060:s={W}x{H}:r={FPS}:d={duration}",
        "-f", "lavfi", "-i", f"color=c=red:s=180x120:r={FPS}:d={duration}",
        "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frame_count),
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise AssertionError(f"ffmpeg failed:\n{result.stderr.decode(errors='replace')[-3000:]}\n{graph}")
    frame = result.stdout[-W * H:]              # the last frame: fully landed
    xs = [x for y in range(H) for x in range(W) if frame[y * W + x] > 12]
    ys = [y for y in range(H) for x in range(W) if frame[y * W + x] > 12]
    if not xs:
        raise AssertionError("the turned photo is missing from the rendered frame")
    return min(xs), min(ys), max(xs), max(ys), len(xs)


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class CollageFreeTiltRenderTest(unittest.TestCase):
    def test_a_hand_turned_free_photo_renders_turned_and_whole(self):
        ffmpeg = str(_ffmpeg_bin())
        x0, y0, x1, y1, area = _render_free_tilt(ffmpeg, 0)
        self.assertGreater(x1 - x0, y1 - y0, "the straight photo is landscape")
        self.assertAlmostEqual(x1 - x0, 96, delta=3)
        self.assertAlmostEqual(y1 - y0, 72, delta=3)

        # a quarter turn: the same mat, standing on its short edge
        tx0, ty0, tx1, ty1, turned = _render_free_tilt(ffmpeg, 90)
        self.assertGreater(ty1 - ty0, tx1 - tx0, "the render must turn the photo with the preview")
        self.assertAlmostEqual(tx1 - tx0, 72, delta=3)
        self.assertAlmostEqual(ty1 - ty0, 96, delta=3)
        # nothing clipped away by the sprite canvas
        self.assertAlmostEqual(turned, area, delta=0.06 * area)

        # half and three-quarter turns keep the whole photo too
        for tilt in (-90, 180, 135, 45):
            hx0, hy0, hx1, hy1, whole = _render_free_tilt(ffmpeg, tilt)
            self.assertGreater(hx1 - hx0, 0)
            self.assertGreater(hy1 - hy0, 0)
            self.assertAlmostEqual(whole, area, delta=0.09 * area,
                                   msg=f"tilt {tilt} lost pixels: {whole} vs {area}")
            if tilt in (180,):
                self.assertAlmostEqual(hx1 - hx0, 96, delta=3)
                self.assertAlmostEqual(hy1 - hy0, 72, delta=3)


def _render_ken_burns(ffmpeg: str) -> tuple[tuple[int, int], tuple[int, int], dict, float]:
    """Render one borderless photo with Ken Burns on and measure the red mask's
    bounding box on an early frame and on the last one.

    Ken Burns keeps scaling the sprite long after the entrance has settled, and
    FFmpeg's `scale` cannot be animated — so the render has to hold a ladder of
    sizes across the whole slide. A ladder that stops sampling at the entrance
    freezes the photo at the size it landed on: the preview zooms 8 % and the
    MP4 never moves. This measures the pixels both ways.
    """
    item = {"collage": {
        "layout": "free",
        "animation": "none",
        "shape": "4:3",
        "seed": 1,
        "hold": 3,
        "kenBurns": True,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": "/photos/kb.jpg", "cx": 50, "cy": 50, "w": 30}],
    }}
    spec = normalize_collage(item)
    assert spec is not None
    duration = collage_duration(spec)
    lines, last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
    redness = "clip(r(X,Y)-max(g(X,Y),b(X,Y)),0,255)"
    graph = (
        f"[0:v]null[cb];{''.join(lines)}"
        f"[{last}]format=yuv420p,settb=AVTB,setpts=PTS-STARTPTS[comp];"
        f"[comp]geq=r='{redness}':g='{redness}':b='{redness}',format=gray[v]"
    )
    frame_count = int(round(duration * FPS))
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-f", "lavfi", "-i", f"color=c=0x606060:s={W}x{H}:r={FPS}:d={duration}",
        "-f", "lavfi", "-i", f"color=c=red:s=180x120:r={FPS}:d={duration}",
        "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frame_count),
        "-f", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=180)
    if result.returncode:
        raise AssertionError(f"ffmpeg failed:\n{result.stderr.decode(errors='replace')[-3000:]}\n{graph}")

    def box(n: int) -> tuple[int, int]:
        frame = result.stdout[n * W * H:(n + 1) * W * H]
        xs = [x for y in range(H) for x in range(W) if frame[y * W + x] > 12]
        ys = [y for y in range(H) for x in range(W) if frame[y * W + x] > 12]
        if not xs:
            raise AssertionError(f"the photo is missing from frame {n}")
        return max(xs) - min(xs) + 1, max(ys) - min(ys) + 1

    return box(1), box(frame_count - 1), spec, duration


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class CollageKenBurnsRenderTest(unittest.TestCase):
    def test_ken_burns_keeps_zooming_in_the_rendered_video(self):
        ffmpeg = str(_ffmpeg_bin())
        (early_w, early_h), (late_w, late_h), spec, duration = _render_ken_burns(ffmpeg)
        # what the twin says the photo's scale is at those two moments
        s_early = photo_state(spec, 0, 1 / FPS, 0, W / H)["scale"]
        s_late = photo_state(spec, 0, duration - 1 / FPS, 0, W / H)["scale"]
        self.assertGreater(s_late, s_early + 0.05, "the slide should still be zooming at the end")
        # the rendered photo grows by the same factor (one quantisation bucket
        # and a pixel of rounding either way)
        self.assertAlmostEqual(late_w / early_w, s_late / s_early, delta=0.04)
        self.assertAlmostEqual(late_h / early_h, s_late / s_early, delta=0.04)
        self.assertGreater(late_w, early_w + 3, "the MP4 must zoom, not hold the landed size")


# --- per-photo lanes, measured in real pixels ------------------------------
# A collage of flat primary colours on a gray bed: counting a colour's pixels
# in a frame says whether that photo is still on screen, and their centroid says
# where it is. That makes one photo's motion, exit and pin anchor measurable in
# the MP4 instead of only in the filter graph.
_LANES = ("red", "lime", "blue")
_LANE_HEX = {"red": "0xff0000", "lime": "0x00ff00", "blue": "0x0000ff"}
_LANE_MATCH = {
    "red": lambda r, g, b: r > 150 and g < 100 and b < 100,
    "lime": lambda r, g, b: g > 150 and r < 100 and b < 100,
    "blue": lambda r, g, b: b > 150 and r < 100 and g < 100,
}


def _render_lanes(ffmpeg: str, item: dict) -> tuple[list[bytes], object, float]:
    """Render the item's collage with one flat colour per photo; returns the
    raw rgb24 frames, the normalised spec and the slide duration."""
    spec = normalize_collage(item)
    assert spec is not None
    duration = collage_duration(spec)
    lines, last = collage_graph(item, W, H, FPS, duration, 0, 1, "cb")
    graph = (f"[0:v]format=rgba[cb];{''.join(lines)}"
             f"[{last}]format=rgb24,settb=AVTB,setpts=PTS-STARTPTS[v]")
    frame_count = int(round(duration * FPS))
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-f", "lavfi", "-i", f"color=c=0x606060:s={W}x{H}:r={FPS}:d={duration}"]
    for k in range(len(spec["photos"])):
        # format=rgba: the colour sources default to yuv420p, where an odd-sized
        # mat loses its last column and row of chroma — a flat photo would come
        # back a pixel short and bias every centroid.
        command += ["-f", "lavfi", "-i",
                    f"color=c={_LANE_HEX[_LANES[k]]}:s=180x120:r={FPS}:d={duration},format=rgba"]
    command += ["-filter_complex", graph, "-map", "[v]", "-frames:v", str(frame_count),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    result = subprocess.run(command, capture_output=True, timeout=240)
    if result.returncode:
        raise AssertionError(f"ffmpeg failed:\n{result.stderr.decode(errors='replace')[-3000:]}\n{graph}")
    size = W * H * 3
    frames = [result.stdout[n * size:(n + 1) * size] for n in range(frame_count)]
    if len(frames[-1]) != size:
        raise AssertionError(f"expected {frame_count} rgb24 frames, got {len(result.stdout) / size:.2f}")
    return frames, spec, duration


def _lane_box(frame: bytes, lane: str) -> tuple[int, float, float]:
    """(pixel count, centroid x, centroid y) of one photo's colour in a frame.

    A colour owns a pixel when its channel is clearly dominant, so the gray
    bed, black shadows and the interpolated edge of a turned sprite are all
    left out and the centroid stays the mat's centre.
    """
    owns = _LANE_MATCH[lane]
    total = x_sum = y_sum = 0
    for y in range(H):
        row = y * W * 3
        for x in range(W):
            o = row + x * 3
            if not owns(frame[o], frame[o + 1], frame[o + 2]):
                continue
            total += 1
            x_sum += x
            y_sum += y
    return total, (x_sum / total if total else 0.0), (y_sum / total if total else 0.0)


def _lane_item(photos: dict, **spec_extra) -> dict:
    """A borderless Free collage of the given per-photo specs, keyed by index."""
    return {"collage": {
        "layout": "free", "shape": "4:3", "seed": 5, "hold": 2,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": f"/photos/{k}.jpg", "delay": 0, **v} for k, v in photos.items()],
        **spec_extra,
    }}


def _lane_centre(spec: dict, i: int, t: float) -> tuple[float, float]:
    """Where the twin puts photo i's mat centre at time t, in render pixels."""
    st = photo_state(spec, i, t, 0, W / H)
    photo = spec["photos"][i]
    return (photo["cx"] / 100 * W + st["dx"] / 100 * W,
            photo["cy"] / 100 * H + st["dy"] / 100 * H)


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class CollagePhotoLanesRenderTest(unittest.TestCase):
    """Per-photo enter / while / exit lanes, measured through the real render."""

    def test_one_photos_motion_moves_only_that_photo(self):
        ffmpeg = str(_ffmpeg_bin())
        # 'drift' at full strength pans this photo ~26 px across the 320 px
        # frame over the slide — far enough to measure, and one-way, so the
        # sampled positions cannot cancel out by phase luck.
        item = _lane_item({
            0: {"cx": 30, "cy": 50, "w": 26, "motion": "drift", "motionAmount": 2},
            1: {"cx": 72, "cy": 50, "w": 26},
        }, animation="none", hold=3)
        frames, spec, duration = _render_lanes(ffmpeg, item)
        # Six moments after the motion has eased in, spread over the slide.
        first = photo_start(spec, 0, 0) + ENTRANCE_LENGTH[photo_anim(spec, 0)] + MOTION_RAMP + 0.05
        step = max(0.05, (duration - 1 / FPS - first) / 5)
        times = [min(first + k * step, duration - 1 / FPS) for k in range(6)]
        centres = [_lane_centre(spec, 0, t) for t in times]
        self.assertGreater(max(c[0] for c in centres) - min(c[0] for c in centres), 8.0,
                           "the motion should travel far enough to measure")
        # A sprite is placed at whole pixels, an odd mat is centred by integer
        # division, and ffmpeg's overlay composites in 4:2:0 — so the counted
        # box of a flat photo sits a pixel or two off the twin's fractional
        # centre, and that offset shifts with the parity of the position. What
        # has to hold is that the offset stays inside that pixel jitter while
        # the photo travels: then the MP4 moves it by the twin's own amounts.
        JITTER = (1.2, 2.0)                     # (x, y) — chroma is halved in y
        offset = {}
        for t in times:
            frame = frames[int(t * FPS)]
            for i, lane in ((0, "red"), (1, "lime")):
                count, cx, cy = _lane_box(frame, lane)
                px, py = _lane_centre(spec, i, t)
                self.assertGreater(count, 200, f"{lane} should be on screen at t={t:.2f}")
                self.assertLess(abs(cx - px), 3.0, f"{lane} x at t={t:.2f}: {cx:.2f} far from {px:.2f}")
                self.assertLess(abs(cy - py), 3.0, f"{lane} y at t={t:.2f}: {cy:.2f} far from {py:.2f}")
                # the photo without a motion renders the very same frame every
                # time, so its measurement cannot move at all
                slack = (0.05, 0.05) if i == 1 else JITTER
                if i not in offset:
                    offset[i] = (cx - px, cy - py)
                self.assertAlmostEqual(cx - px, offset[i][0], delta=slack[0],
                                       msg=f"{lane} x at t={t:.2f} moves unlike the twin")
                self.assertAlmostEqual(cy - py, offset[i][1], delta=slack[1],
                                       msg=f"{lane} y at t={t:.2f} moves unlike the twin")

    def test_a_photo_with_its_own_exit_none_stays_while_the_others_leave(self):
        ffmpeg = str(_ffmpeg_bin())
        item = _lane_item({
            0: {"cx": 22, "cy": 50, "w": 22},
            1: {"cx": 50, "cy": 50, "w": 22, "exit": "none"},
            2: {"cx": 78, "cy": 50, "w": 22},
        }, animation="fade", hold=1.0, exit="sweep")
        frames, spec, duration = _render_lanes(ffmpeg, item)
        early = frames[int(0.6 * FPS)]
        for lane in _LANES:
            self.assertGreater(_lane_box(early, lane)[0], 200, f"{lane} should be on screen early")
        last = frames[-1]
        self.assertLess(_lane_box(last, "red")[0], 25, "photo 0 sweeps out")
        self.assertLess(_lane_box(last, "blue")[0], 25, "photo 2 sweeps out")
        self.assertGreater(_lane_box(last, "lime")[0], 200,
                           "the photo whose own exit is 'none' must still be there at the end")

    def test_a_per_photo_swing_hangs_from_its_own_pin(self):
        ffmpeg = str(_ffmpeg_bin())
        item = _lane_item({
            0: {"cx": 30, "cy": 45, "w": 24, "anim": "swing"},
            1: {"cx": 72, "cy": 45, "w": 24},
        }, animation="fade", hold=1.6)
        frames, spec, duration = _render_lanes(ffmpeg, item)
        graph = "".join(collage_graph(item, W, H, FPS, duration, 0, 1, "cb")[0])
        # The swinging photo's sprite canvas is symmetric about its pin: the
        # mat's TOP edge sits on the canvas centre (mat_y == ch / 2), while the
        # fading neighbour keeps the ordinary centred canvas. With a shadow the
        # same pad appears twice (sprite and silhouette), so either label fits.
        pads = {int(m[4]): (int(m[0]), int(m[1]), int(m[3])) for m in
                re.findall(r"pad=(\d+):(\d+):(\d+):(\d+):color=black@0\.0\[(?:t|sp)(\d+)\]", graph)}
        self.assertEqual(sorted(pads), [0, 1], graph[:300])
        self.assertGreater(pads[0][1], pads[1][1], "a pinned canvas reaches above the mat too")
        self.assertAlmostEqual(pads[0][2], pads[0][1] / 2, delta=1)
        self.assertLess(pads[1][2], pads[1][1] / 2)
        # and on screen: the pendulum moves the hanging photo, not its neighbour
        t0 = photo_start(spec, 0, 0)
        mid = frames[int((t0 + 0.16) * FPS)]
        rest = frames[int((duration - 0.2) * FPS)]
        swung, rested = _lane_box(mid, "red"), _lane_box(rest, "red")
        neighbour_mid, neighbour_rest = _lane_box(mid, "lime"), _lane_box(rest, "lime")
        self.assertGreater(abs(photo_state(spec, 0, t0 + 0.16, 0, W / H)["rot"]), 5.0,
                           "the twin should be mid-swing at this moment")
        self.assertGreater(abs(swung[1] - rested[1]), 2.5,
                           f"the rendered swing barely moved: {swung[1]:.2f} vs {rested[1]:.2f}")
        self.assertAlmostEqual(neighbour_mid[1] - neighbour_rest[1], 0.0, delta=0.6)
