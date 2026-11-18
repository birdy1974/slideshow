"""The size ladder should not scale every invisible variant on every frame."""
import re
import subprocess
import unittest

from app.collage import collage_duration, collage_graph, normalize_collage
from tests.test_collage_animation_ffmpeg import _ffmpeg_bin


class CollageLadderTest(unittest.TestCase):
    def test_animated_ladder_filters_before_scaling(self):
        item = {"collage": {"animation": "pop", "exit": "pop", "hold": 2,
                            "photos": [{"path": "/a.jpg"}, {"path": "/b.jpg"}]}}
        duration = collage_duration(normalize_collage(item))
        lines, _ = collage_graph(item, 320, 180, 25, duration, 0, 1, "cb")
        branches = [line for line in lines if re.match(r"\[b\d+_\d+\]", line)]
        self.assertGreater(len(branches), 5)
        self.assertTrue(all("]select=" in line for line in branches))

    def test_slow_entrances_and_exits_bound_the_size_ladder(self):
        # A long, slow animated collage previously generated hundreds of unique
        # sizes per photo, even when neighbouring frames differed by one pixel.
        photos = [{"path": f"/{i}.jpg", "anim": anim, "exit": exit_, "at": i * 2}
                  for i, (anim, exit_) in enumerate((
                      ("swing", "boomerang"), ("unfold", "fade"),
                      ("spiral", "rollover"), ("boomerang", "flip"),
                      ("rise", "cube"), ("develop", "tumble")))]
        item = {"collage": {"layout": "free", "animation": "drop", "shape": "native",
                            "animSpeed": 0.3, "exitSpeed": 0.15, "photos": photos}}
        duration = collage_duration(normalize_collage(item))
        lines, _ = collage_graph(item, 640, 360, 24, duration, 0, 1, "cb")
        branches = [line for line in lines if re.match(r"\[b\d+_\d+\]", line)]
        self.assertLess(len(branches), 350)
        self.assertGreater(len(branches), 40)  # animation was not discarded

    @unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
    def test_sparse_ladder_matches_full_rate_ladder(self):
        # Moving inputs catch accidental timestamp resets or repeated frames;
        # a lead-in, staggered entrance, mirrored exit and breathing tail exercise
        # reused windows, including the first and last visible frames.
        for animation, extra in (("pop", {"exit": "pop"}),
                                 ("fade", {"kenBurns": True, "exit": "flip"}),
                                 ("iris", {"exit": "iris"})):
            with self.subTest(animation=animation):
                item = {"collage": {"animation": animation, "hold": 1.5,
                                    "frame": {"shape": "none", "shadow": False},
                                    "photos": [{"path": "/a.jpg"}, {"path": "/b.jpg"}], **extra}}
                duration = collage_duration(normalize_collage(item)) + 0.4
                lines, last = collage_graph(item, 160, 90, 12, duration, 0.4, 1, "cb")
                full_rate = [re.sub(r"^(\[b\d+_\d+\])select='[^']*',(?=scale=)", r"\1", line)
                             if "geq=" not in line else line for line in lines]
                def render(graph_lines):
                    graph = "[0:v]null[cb];" + "".join(graph_lines) + f"[{last}]format=rgb24[v]"
                    cmd = [str(_ffmpeg_bin()), "-v", "error", "-filter_complex_threads", "1"]
                    for source in ("color=gray:s=160x90", "testsrc2=s=128x96", "testsrc=s=128x96"):
                        cmd += ["-f", "lavfi", "-i", f"{source}:r=12:d={duration}"]
                    cmd += ["-filter_complex", graph, "-map", "[v]", "-f", "rawvideo", "-"]
                    result = subprocess.run(cmd, capture_output=True, timeout=60, check=True)
                    return result.stdout
                self.assertEqual(render(lines), render(full_rate))
