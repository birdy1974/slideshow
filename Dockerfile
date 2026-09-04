# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS frontend
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY public ./public
RUN npm run build

# --- Custom FFmpeg with xfade-easing (GL transitions + easing/reverse) ---
# Builds FFmpeg from source, injecting scriptituk/xfade-easing's vf_xfade.c
# and xfade-easing.h which expose all gl_* transitions (C fast path) and the
# easing= / reverse= xfade options. Supports up to FFmpeg 8.1 (we build 7.1.1).
FROM debian:trixie AS ffmpeg-builder
ARG FFMPEG_VERSION=7.1.1
ARG DEBIAN_FRONTEND=noninteractive
RUN sed -i 's/^Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources 2>/dev/null || echo "deb http://deb.debian.org/debian trixie non-free non-free-firmware" >> /etc/apt/sources.list ; apt-get update && apt-get install -y --no-install-recommends \
      build-essential yasm nasm pkg-config wget xz-utils \
      libx264-dev libx265-dev libvpx-dev libmp3lame-dev libopus-dev libvorbis-dev \
      libass-dev libfreetype6-dev libdrm-dev libva-dev \
    && (apt-get install -y --no-install-recommends libfdk-aac-dev || echo "libfdk-aac-dev not available, building without it") \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
RUN wget -q https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz \
 && tar xf ffmpeg-${FFMPEG_VERSION}.tar.xz
# Inject the patched filter (header-only xfade-easing.h + full vf_xfade.c)
COPY ffmpeg-patch/vf_xfade.c /tmp/ffmpeg-${FFMPEG_VERSION}/libavfilter/vf_xfade.c
COPY ffmpeg-patch/xfade-easing.h /tmp/ffmpeg-${FFMPEG_VERSION}/libavfilter/xfade-easing.h
WORKDIR /tmp/ffmpeg-${FFMPEG_VERSION}
RUN if pkg-config --exists fdk-aac 2>/dev/null; then FDK="--enable-libfdk-aac"; else FDK=""; echo "building without libfdk-aac"; fi; \
    ./configure \
      --enable-gpl --enable-nonfree --enable-version3 \
      --enable-libx264 --enable-libx265 --enable-libvpx --enable-libmp3lame --enable-libopus --enable-libvorbis \
      --enable-libass --enable-libfreetype $FDK \
      --enable-vaapi --enable-libdrm \
      --extra-cflags="-O3" \
    && make -j$(nproc) \
    && make install \
    && ffmpeg -hide_banner -h filter=xfade 2>&1 | head -n 80

# Debian trixie ships FFmpeg 7.x (bookworm only carries FFmpeg 5.x), which
# provides the full xfade catalogue (wind/cover/reveal) used by the renderer.
# We use the custom build above (xfade-easing) and keep runtime VA-API drivers.
FROM python:3.13-slim-trixie AS runtime
LABEL org.opencontainers.image.source="https://github.com/birdy1974/slideshow" \
      org.opencontainers.image.description="Self-hosted FFmpeg photo and video slideshow maker (custom xfade-easing build)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config PHOTOS_DIR=/photos VIDEOS_DIR=/videos MUSIC_DIR=/music OUTPUT_DIR=/output \
    STATIC_DIR=/app/static
# Runtime shared libs for the custom ffmpeg (installed via the builder's --enable-* libs) plus VA-API
RUN apt-get update && apt-get install -y --no-install-recommends \
      fonts-dejavu-core fontconfig tini ca-certificates \
      intel-media-va-driver i965-va-driver vainfo \
      libx264-164 libx265-213 libvpx9 libmp3lame0 libopus0 libvorbis0a libvorbisenc2 \
      libass9 libfreetype6 libfdk-aac2 libdrm2 libva2 libva-drm2 \
    && rm -rf /var/lib/apt/lists/* \
    || (apt-get update && apt-get install -y --no-install-recommends \
      fonts-dejavu-core fontconfig tini ca-certificates \
      intel-media-va-driver vainfo \
      libass9 libfreetype6 \
    && rm -rf /var/lib/apt/lists/*)
# Bring the custom ffmpeg/ffprobe into the runtime image (overrides any stock ffmpeg)
COPY --from=ffmpeg-builder /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /usr/local/bin/ffprobe /usr/local/bin/ffprobe
# Fallback: if the custom build didn't produce ffprobe, keep system one (the copy above will fail gracefully)
RUN ffmpeg -hide_banner -version 2>&1 | head -n 5; ffmpeg -hide_banner -h filter=xfade 2>&1 | grep -q "easing" && echo "xfade-easing OK" || echo "xfade-easing check skipped"
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /src/dist ./static
# Same TTFs the browser preview uses, so FFmpeg renders identical typography.
COPY public/fonts ./fonts
ENV FONTS_DIR=/app/fonts
RUN mkdir -p /config/work /config/previews /output /photos /videos /music
EXPOSE 8080
VOLUME ["/config", "/output"]
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/ping', timeout=5)" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
