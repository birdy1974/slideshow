"""Runtime configuration loaded from environment variables.

All writable paths live below mounted NAS volumes. Input roots are treated as
read-only security boundaries by the media browser and renderer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    config_dir: Path = Path(os.getenv("CONFIG_DIR", "/config"))
    photos_dir: Path = Path(os.getenv("PHOTOS_DIR", "/photos"))
    videos_dir: Path = Path(os.getenv("VIDEOS_DIR", "/videos"))
    music_dir: Path = Path(os.getenv("MUSIC_DIR", "/music"))
    output_dir: Path = Path(os.getenv("OUTPUT_DIR", "/output"))
    ffmpeg_bin: str = os.getenv("FFMPEG_BIN", "ffmpeg")
    ffprobe_bin: str = os.getenv("FFPROBE_BIN", "ffprobe")
    # Media probing happens on potentially slow NAS volumes / network mounts.
    # A generous timeout plus retries keeps on-demand-synced files (which can
    # briefly report 0 bytes while they hydrate) from failing a render.
    ffprobe_timeout: float = float(os.getenv("FFPROBE_TIMEOUT", "30"))
    media_probe_retries: int = max(0, int(os.getenv("MEDIA_PROBE_RETRIES", "2")))
    media_probe_retry_delay: float = max(0.0, float(os.getenv("MEDIA_PROBE_RETRY_DELAY", "0.75")))
    render_workers: int = max(1, int(os.getenv("RENDER_WORKERS", "1")))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def database_path(self) -> Path:
        return self.config_dir / "slideshow.db"

    @property
    def work_dir(self) -> Path:
        return self.config_dir / "work"

    @property
    def preview_dir(self) -> Path:
        return self.config_dir / "previews"

    @property
    def media_roots(self) -> dict[str, Path]:
        return {
            "photos": self.photos_dir,
            "videos": self.videos_dir,
            "music": self.music_dir,
            "output": self.output_dir,
        }


settings = Settings()
