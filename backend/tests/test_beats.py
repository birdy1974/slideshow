"""Beat (onset) detection — the data behind collage beat sync.

The detector decodes real audio through the real FFmpeg binary, so these
tests synthesise tones with FFmpeg itself (kick-like bursts at known times)
and assert the detected onsets land within one video frame of them.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.renderer import RenderError, Renderer, _detect_beats_pcm


def _ffmpeg_bin() -> str | None:
    bin_ = shutil.which("ffmpeg")
    if bin_:
        return bin_
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _make_audio(path: Path, expression: str, seconds: float) -> None:
    # commas separate filters in a filter graph — escape them inside the expr
    escaped = expression.replace(",", r"\,")
    subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"aevalsrc={escaped}:s=44100:d={seconds}",
         "-c:a", "libmp3lame", "-q:a", "4", "-y", str(path)],
        check=True,
    )


@unittest.skipIf(_ffmpeg_bin() is None, "no FFmpeg binary available")
class BeatDetectionTest(unittest.TestCase):
    def test_detect_beats_finds_the_kicks(self):
        with tempfile.TemporaryDirectory() as base:
            # 0.12 s bursts of a 220 Hz tone every 0.5 s — a drum machine at 120 BPM
            tone = Path(base) / "kicks.mp3"
            _make_audio(tone, "sin(2*PI*220*t)*lt(mod(t,0.5),0.12)", 6.0)
            beats = _detect_beats_pcm(_ffmpeg_bin(), tone)
            expected = [round(0.5 * k, 1) for k in range(12)]
            for e in expected:
                self.assertTrue(any(abs(e - b) <= 0.06 for b in beats), (e, beats))
            # nothing hallucinated between the kicks
            self.assertTrue(
                all(any(abs(e - b) <= 0.06 for e in expected) for b in beats), beats)
            # onsets are thinned to at least 0.22 s apart and start with the downbeat
            self.assertEqual(beats[0], 0.0)
            self.assertTrue(all(b - a >= 0.22 for a, b in zip(beats, beats[1:])))

    def test_detect_beats_silence_has_no_onsets(self):
        with tempfile.TemporaryDirectory() as base:
            # true digital silence: anullsrc, not a quiet tone (MP3 quantisation
            # noise on a near-silent tone is not silence)
            quiet = Path(base) / "quiet.mp3"
            subprocess.run(
                [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", "anullsrc=r=44100:cl=mono", "-t", "4",
                 "-c:a", "libmp3lame", "-q:a", "4", "-y", str(quiet)],
                check=True,
            )
            self.assertEqual(_detect_beats_pcm(_ffmpeg_bin(), quiet), [])

    def test_renderer_detect_beats_wraps_failures(self):
        with tempfile.TemporaryDirectory() as base:
            renderer = Renderer(Database(Path(base) / "t.db"),
                                Settings(ffmpeg_bin=_ffmpeg_bin()))
            tone = Path(base) / "tone.mp3"
            _make_audio(tone, "sin(2*PI*220*t)*lt(mod(t,0.5),0.12)", 3.0)
            self.assertEqual(renderer.detect_beats(tone)[0], 0.0)
            broken = Path(base) / "broken.mp3"
            broken.write_text("not audio at all")
            with self.assertRaises(RenderError):
                renderer.detect_beats(broken)


if __name__ == "__main__":
    unittest.main()
