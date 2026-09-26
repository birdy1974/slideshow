"""Beat (onset) detection — the data behind collage beat sync.

The detector decodes real audio through the real FFmpeg binary, so these
tests synthesise tones with FFmpeg itself (kick-like bursts at known times)
and assert the detected onsets land within one video frame of them.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

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


needs_ffmpeg = pytest.mark.skipif(_ffmpeg_bin() is None, reason="no FFmpeg binary available")


def _make_audio(path: Path, expression: str, seconds: float) -> None:
    # commas separate filters in a filter graph — escape them inside the expr
    escaped = expression.replace(",", r"\,")
    subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"aevalsrc={escaped}:s=44100:d={seconds}",
         "-c:a", "libmp3lame", "-q:a", "4", "-y", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_detect_beats_finds_the_kicks(tmp_path: Path):
    # 0.12 s bursts of a 220 Hz tone every 0.5 s — a drum machine at 120 BPM
    tone = tmp_path / "kicks.mp3"
    _make_audio(tone, "sin(2*PI*220*t)*lt(mod(t,0.5),0.12)", 6.0)
    beats = _detect_beats_pcm(_ffmpeg_bin(), tone)
    expected = [round(0.5 * k, 1) for k in range(12)]
    for e in expected:
        assert any(abs(e - b) <= 0.06 for b in beats), (e, beats)
    # nothing hallucinated between the kicks
    assert all(any(abs(e - b) <= 0.06 for e in expected) for b in beats), beats
    # onsets are thinned to at least 0.22 s apart and start with the downbeat
    assert beats[0] == 0.0
    assert all(b - a >= 0.22 for a, b in zip(beats, beats[1:]))


@needs_ffmpeg
def test_detect_beats_silence_has_no_onsets(tmp_path: Path):
    # true digital silence: anullsrc, not a quiet tone (MP3 quantisation
    # noise on a near-silent tone is not silence)
    quiet = tmp_path / "quiet.mp3"
    subprocess.run(
        [_ffmpeg_bin(), "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "4",
         "-c:a", "libmp3lame", "-q:a", "4", "-y", str(quiet)],
        check=True,
    )
    assert _detect_beats_pcm(_ffmpeg_bin(), quiet) == []


@needs_ffmpeg
def test_renderer_detect_beats_wraps_failures(tmp_path):
    import tempfile
    with tempfile.TemporaryDirectory() as base:
        renderer = Renderer(Database(Path(base) / "t.db"), Settings(ffmpeg_bin=_ffmpeg_bin()))
        tone = Path(base) / "tone.mp3"
        _make_audio(tone, "sin(2*PI*220*t)*lt(mod(t,0.5),0.12)", 3.0)
        assert renderer.detect_beats(tone)[0] == 0.0
        broken = Path(base) / "broken.mp3"
        broken.write_text("not audio at all")
        with pytest.raises(RenderError):
            renderer.detect_beats(broken)
