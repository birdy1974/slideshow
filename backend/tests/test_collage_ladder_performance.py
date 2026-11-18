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

    @unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
    def test_compact_ladders_preserve_photo_geometry_through_entrance_and_exit(self):
        # Compare against the reference ladder over the entire animation. The
        # red pixel area and centroid catch a frozen scale, an uncentred pad,
        # and a missing entrance/exit frame without requiring identical rounding.
        for animation in ("pop", "unfold", "spiral", "flip", "boomerang", "swing"):
            with self.subTest(animation=animation):
                item = {"collage": {"animation": animation, "exit": "flip", "hold": 1.5,
                                    "frame": {"shape": "none", "shadow": False},
                                    "photos": [{"path": "/a.jpg"}]}}
                duration = collage_duration(normalize_collage(item)) + 0.4
                def render(compact):
                    lines, last = collage_graph(item, 160, 90, 12, duration, 0.4, 1, "cb",
                                                compact_ladders=compact)
                    graph = "[0:v]null[cb];" + "".join(lines) + f"[{last}]format=rgb24[v]"
                    cmd = [str(_ffmpeg_bin()), "-v", "error", "-filter_complex_threads", "1"]
                    for source in ("color=gray:s=160x90", "color=red:s=128x96"):
                        cmd += ["-f", "lavfi", "-i", f"{source}:r=12:d={duration}"]
                    cmd += ["-filter_complex", graph, "-map", "[v]", "-f", "rawvideo", "-"]
                    return subprocess.run(cmd, capture_output=True, timeout=30, check=True).stdout
                reference, compact = render(False), render(True)
                self.assertEqual(len(reference), len(compact))
                size = 160 * 90 * 3
                def stats(frame):
                    points = [(p % 160, p // 160) for p in range(160 * 90)
                              if frame[3*p] > frame[3*p+1] + 30]
                    if not points:
                        return None
                    return (len(points), sum(x for x, _ in points) / len(points),
                            sum(y for _, y in points) / len(points))
                for offset in range(0, len(reference), size):
                    a, b = stats(reference[offset:offset+size]), stats(compact[offset:offset+size])
                    self.assertEqual(a is None, b is None, (animation, offset // size, a, b))
                    if a and b:
                        self.assertLessEqual(abs(a[0] - b[0]), max(90, a[0] * 0.12))
                        self.assertLessEqual(abs(a[1] - b[1]), 2.5)
                        self.assertLessEqual(abs(a[2] - b[2]), 2.5)

    @unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
    def test_large_stills_and_late_exits_produce_the_first_frame(self):
        import tempfile
        from pathlib import Path
        photos = [{"path": f"/p{i}.jpg", "anim": anim, "exit": ex, "at": i * 2,
                   "until": 30 - i} for i, (anim, ex) in enumerate((
                       ("swing", "boomerang"), ("unfold", "fade"), ("spiral", "rollover"),
                       ("boomerang", "flip"), ("rise", "cube"), ("develop", "tumble")))]
        item = {"collage": {"layout": "free", "animation": "drop", "shape": "native",
                            "animSpeed": 0.3, "exitSpeed": 0.15, "photos": photos}}
        lines, last = collage_graph(item, 1280, 720, 30, 34.03, 0, 1, "cb",
                                    loop_stills=True, compact_ladders=True)
        graph = "[0:v]null[cb];" + "".join(lines) + f"[{last}]format=yuv420p[v]"
        self.assertEqual(graph.count("loop=loop=-1:size=1"), 6)
        self.assertNotIn("]split=", graph)
        with tempfile.TemporaryDirectory() as directory:
            image = str(Path(directory) / "large.jpg")
            subprocess.run([str(_ffmpeg_bin()), "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=4000x2252:d=0.04", "-frames:v", "1", image],
                           capture_output=True, check=True, timeout=30)
            graph_file = Path(directory) / "graph.filter"
            graph_file.write_text(graph)
            from app.ffmpeg_argv import probe_graph_script_flag
            flag = probe_graph_script_flag(str(_ffmpeg_bin()))
            if not flag:
                self.skipTest("FFmpeg cannot read filter graphs from files")
            cmd = [str(_ffmpeg_bin()), "-v", "error", "-filter_complex_threads", "1",
                   "-f", "lavfi", "-i", "color=gray:s=1280x720:r=30:d=34.03"]
            for _ in photos:
                cmd += ["-framerate", "30", "-i", image]
            cmd += [flag, str(graph_file), "-map", "[v]", "-frames:v", "1", "-f", "null", "-"]
            subprocess.run(cmd, capture_output=True, check=True, timeout=45)

    @unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
    def test_looping_fitted_still_keeps_animation_timing(self):
        import tempfile
        from pathlib import Path
        item = {"collage": {"animation": "develop", "exit": "flip", "hold": 2,
                            "photos": [{"path": "/test.jpg"}]}}
        duration = collage_duration(normalize_collage(item))
        with tempfile.TemporaryDirectory() as directory:
            image = str(Path(directory) / "test.jpg")
            subprocess.run([str(_ffmpeg_bin()), "-v", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=320x240:d=0.1", "-frames:v", "1", image],
                           capture_output=True, timeout=30, check=True)
            def render(loop_stills):
                lines, last = collage_graph(item, 160, 90, 12, duration, 0, 1, "cb",
                                            loop_stills=loop_stills, compact_ladders=True)
                graph = "[0:v]null[cb];" + "".join(lines) + f"[{last}]format=rgb24[v]"
                cmd = [str(_ffmpeg_bin()), "-v", "error", "-filter_complex_threads", "1",
                       "-f", "lavfi", "-i", f"color=gray:s=160x90:r=12:d={duration}"]
                if not loop_stills:
                    cmd += ["-loop", "1"]
                cmd += ["-framerate", "12", "-i", image, "-filter_complex", graph,
                        "-map", "[v]", "-t", str(duration), "-f", "rawvideo", "-"]
                return subprocess.run(cmd, capture_output=True, timeout=30, check=True).stdout
            self.assertEqual(render(False), render(True))
