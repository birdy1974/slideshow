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
    # Files uploaded from the GUI device. Docker mounts a dedicated volume at
    # /uploads (compose sets UPLOADS_DIR accordingly); without it (bare
    # checkout, dev) uploads land below the config directory so they still
    # persist and stay out of the media mounts. Resolved in __post_init__,
    # because a dataclass default cannot depend on this instance's config_dir.
    uploads_dir: Path = Path(os.getenv("UPLOADS_DIR", "/uploads"))
    # Hard cap per uploaded file, so a phone dump cannot fill the volume silently.
    upload_max_mb: int = max(1, int(os.getenv("UPLOAD_MAX_MB", "4096")))
    # Bundled TTFs used by drawtext; the repo's public/fonts in development,
    # /app/fonts inside the container.
    fonts_dir: Path = Path(os.getenv("FONTS_DIR", "/app/fonts"))
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

    def __post_init__(self) -> None:
        # Frozen dataclass: patch the derived uploads root via object.__setattr__.
        if not os.getenv("UPLOADS_DIR"):
            object.__setattr__(self, "uploads_dir", self.config_dir / "uploads")

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
            "uploads": self.uploads_dir,
        }


settings = Settings()
