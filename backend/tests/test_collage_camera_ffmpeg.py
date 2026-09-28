"""Collage virtual camera — rendered through the real FFmpeg binary.

The camera chain is the one part of the collage graph a string-matched test
cannot vouch for: crop=w='…t…' LOOKED like a moving window and rendered a
static full frame (crop evaluates its size once, at init, with t = NaN —
which FFmpeg's max() turns into progress 0). So these tests build the real
graph (collage_graph) for a static scene, run it through FFmpeg, and track
the LAST photo — the zoom anchor — frame by frame: its position and size in
the output must follow camera_state() (the preview's twin), and it must move
every frame while the twin does (no frames where the picture sits still and
then jumps — the "bumpy" camera the supersampled zoompan replaces).

Skipped when no FFmpeg binary is available (FFMPEG_BIN, PATH, or the
imageio-ffmpeg wheel).
"""
from __future__ import annotations

import contextlib
import math
import os
import shutil
import subprocess
import unittest
from unittest import mock

from app.collage import (
    camera_filter,
    camera_state,
    camera_supersample,
    collage_duration,
    collage_graph,
    normalize_collage,
)

W, H, FPS = 320, 180, 25
LEAD_IN = 0.4
TAIL = 0.4
# A frame counts as "moving" when the twin shifts the anchor photo by at
# least this many output pixels; the render must then move too. Below the
# noise floor a step reads as a stall.
MOVING_PX = 0.5
STALL_PX = 0.15


def _ffmpeg_bin() -> str | None:
    env = os.environ.get("FFMPEG_BIN")
    if env and ((os.path.isabs(env) and os.path.exists(env)) or shutil.which(env)):
        return env
    bin_ = shutil.which("ffmpeg")
    if bin_:
        return bin_
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _item(camera: str | None) -> dict:
    # A static scene: 'none' entrance (photos on screen from frame 0), no
    # exit, bare photos (no mat, no shadow) so the last photo is a clean solid
    # rectangle whose position and area can be measured. Anything that moves
    # between frames is the camera.
    spec = {
        "layout": "grid", "animation": "none", "shape": "4:3", "hold": 2.0, "seed": 3,
        "frame": {"shape": "none", "shadow": False},
        "photos": [{"path": "/photos/blue.jpg", "name": "blue.jpg"},
                   {"path": "/photos/red.jpg", "name": "red.jpg"}],
    }
    if camera:
        spec["camera"] = camera
    return {"collage": spec}


class _Track:
    """Per-frame soft area and centroid of the red photo in the rendered output."""

    def __init__(self, frames: list[bytes]):
        self.area: list[float] = []
        self.cx: list[float] = []
        self.cy: list[float] = []
        for f in frames:
            total = sum(f)
            cols = [sum(f[x::W]) for x in range(W)]
            rows = [sum(f[y * W:(y + 1) * W]) for y in range(H)]
            self.area.append(total / 255.0)
            self.cx.append(sum((x + 0.5) * v for x, v in enumerate(cols)) / total)
            self.cy.append(sum((y + 0.5) * v for y, v in enumerate(rows)) / total)

    def __len__(self) -> int:
        return len(self.area)


def _render(ffmpeg: str, item: dict, frames: int) -> tuple[_Track, list[bytes]]:
    """Render the collage's graph exactly as the renderer wires it (colour bed
    as input 0, one input per photo) and return the red-photo track. The
    output is a redness mask — r - max(g, b) — so the grey bed and the blue
    photo vanish and soft (bicubic-blended) edges count fractionally."""
    duration = LEAD_IN + collage_duration(normalize_collage(item)) + TAIL
    lines, last = collage_graph(item, W, H, FPS, duration, LEAD_IN, 1, "cb")
    redness = "clip(r(X,Y)-max(g(X,Y),b(X,Y)),0,255)"
    graph = ("[0:v]null[cb];" + "".join(lines) +
             f"[{last}]geq=r='{redness}':g='{redness}':b='{redness}',format=gray[v]")
    seconds = (frames + 2) / FPS
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-f", "lavfi", "-i", f"color=c=0x606060:s={W}x{H}:r={FPS}:d={seconds}",
           "-f", "lavfi", "-i", f"color=c=blue:s=160x120:r={FPS}:d={seconds}",
           "-f", "lavfi", "-i", f"color=c=red:s=160x120:r={FPS}:d={seconds}",
           "-filter_complex", graph, "-map", "[v]", "-frames:v", str(frames),
           "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise AssertionError(f"ffmpeg failed ({proc.returncode}):\n{proc.stderr.decode(errors='replace')[-3000:]}\n{graph}")
    size = W * H
    raw = proc.stdout
    if len(raw) != frames * size:
        raise AssertionError(f"expected {frames} frames, got {len(raw) / size:.2f}")
    out = [raw[k * size:(k + 1) * size] for k in range(frames)]
    return _Track(out), out


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class CollageCameraRenderTest(unittest.TestCase):
    ffmpeg: str
    scene: _Track
    frames: int

    @classmethod
    def setUpClass(cls) -> None:
        cls.ffmpeg = str(_ffmpeg_bin())
        spec = normalize_collage(_item(None))
        cls.frames = int(round((LEAD_IN + collage_duration(spec) + TAIL) * FPS))
        # The control: no camera → the scene is static, and its red photo's
        # centroid/area are the reference the camera runs are measured against.
        cls.scene, raw = _render(cls.ffmpeg, _item(None), cls.frames)
        assert all(f == raw[0] for f in raw), "the control scene is not static — the test premise is broken"
        assert cls.scene.area[0] > 400, "the red photo is missing from the control render"

    def _expected(self, spec: dict, k: int) -> tuple[float, float, float, float]:
        """(z, cx, cy, area) of the red photo in output pixels at frame k, per the twin."""
        cs = camera_state(spec, k / FPS, LEAD_IN, W / H)
        z = cs["z"]
        ox = cs["cx"] / 100 * W - W / (2 * z)          # window origin in scene pixels
        oy = cs["cy"] / 100 * H - H / (2 * z)
        return z, (self.scene.cx[0] - ox) * z, (self.scene.cy[0] - oy) * z, self.scene.area[0] * z * z

    def _check_camera(self, camera: str, supersample: int | None = None) -> None:
        item = _item(camera)
        spec = normalize_collage(item)
        forced = mock.patch("app.collage.camera_supersample", return_value=supersample) if supersample else contextlib.nullcontext()
        with forced:
            track, _ = _render(self.ffmpeg, item, self.frames)
        ss = supersample or camera_supersample(W, H)
        twin = [self._expected(spec, k) for k in range(len(track))]

        # 1. Smoothness — whenever the twin moves the anchor photo by more than
        #    MOVING_PX per frame, the render must move that frame too. The old
        #    camera (zoompan on the scene's own pixel grid) sat still for 2-3
        #    frames and then jumped a whole pixel.
        moving = 0
        for k in range(1, len(track)):
            z, ex, ey, _ = twin[k]
            _, px, py, _ = twin[k - 1]
            ideal = math.hypot(ex - px, ey - py)
            if ideal < MOVING_PX:
                continue
            moving += 1
            step = math.hypot(track.cx[k] - track.cx[k - 1], track.cy[k] - track.cy[k - 1])
            self.assertGreater(step, STALL_PX,
                               f"{camera} frame {k} (t={k / FPS:.2f}s): the camera stalled (moved {step:.2f}px, twin {ideal:.2f}px)")
        self.assertGreater(moving, 10, f"{camera}: the twin barely moves the anchor — nothing to check")

        # 2. Accuracy — position and zoom follow camera_state(). zoompan
        #    truncates the window origin to its (supersampled) input grid and
        #    the zoom magnifies that, so allow one grid step plus the
        #    centroid measurement's own slack.
        for k in range(len(track)):
            z, ex, ey, _ = twin[k]
            where = f"{camera} frame {k} (t={k / FPS:.2f}s, z={z:.3f})"
            tol = 0.35 + 1.2 * z / ss
            self.assertLess(abs(track.cx[k] - ex), tol, f"{where}: cx {track.cx[k]:.2f} vs twin {ex:.2f}")
            self.assertLess(abs(track.cy[k] - ey), tol, f"{where}: cy {track.cy[k]:.2f} vs twin {ey:.2f}")
            measured_z = math.sqrt(track.area[k] / self.scene.area[0])
            self.assertLess(abs(measured_z - z), 0.02 + 0.01 * z, f"{where}: measured zoom {measured_z:.3f} vs twin {z:.3f}")

        # 3. And the camera really did move: first vs last frame.
        travel = abs(track.cx[-1] - track.cx[0]) + abs(track.cy[-1] - track.cy[0])
        growth = abs(track.area[-1] - track.area[0]) / self.scene.area[0] * 100
        self.assertGreater(travel + growth, 4.0, f"{camera}: no camera motion in the rendered output")

    def test_pan_drifts_across_the_scene(self):
        self._check_camera("pan")

    def test_zoom_closes_in_on_the_last_photo(self):
        self._check_camera("zoom")

    def test_telescope_closes_in_on_the_last_photo(self):
        self._check_camera("telescope")

    def test_droste_closes_in_on_the_last_photo(self):
        self._check_camera("droste")

    def test_without_supersampling_the_camera_stutters(self):
        """The guard the smoothness assertion provides: on zoompan's native
        pixel grid the pan holds still for whole frames and then jumps, which
        _check_camera must reject — otherwise a future 'simplification' that
        drops the supersampling would pass unnoticed."""
        with self.assertRaises(AssertionError) as caught:
            self._check_camera("pan", supersample=1)
        self.assertIn("stalled", str(caught.exception))

    def test_camera_filter_shape(self):
        spec = normalize_collage(_item("zoom"))
        chain = camera_filter(spec, W, H, FPS, LEAD_IN)
        ss = camera_supersample(W, H)
        self.assertTrue(chain.startswith(f"scale=iw*{ss}:ih*{ss}:flags=bicubic,format=yuv444p,zoompan=z='1+0.35*"), chain)
        self.assertIn(f":d=1:s={W}x{H}:fps={FPS},setsar=1", chain)
        # the timeline variable is zoompan's input time, shifted by the lead-in
        self.assertIn(f"(it-{LEAD_IN})", chain)
        self.assertNotIn("crop", chain)


if __name__ == "__main__":
    unittest.main()
