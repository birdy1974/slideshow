"""Render photo choreography through the real FFmpeg collage graph.

The JavaScript preview already animates a swing using photoState(). This
regression test checks the exported filter graph too: a photo rotating around
its top pin must move visibly across frames, rather than becoming a static
image because of a malformed rotate-angle expression.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import unittest

from app.collage import collage_duration, collage_graph, normalize_collage

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
