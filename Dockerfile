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
# easing= / reverse= xfade options.
#
# IMPORTANT: the vendored ffmpeg-patch/vf_xfade.c tracks the *latest stable*
# FFmpeg release (it uses the FFFilter / ff_slice_pos APIs introduced in 8.x),
# so FFMPEG_VERSION must stay on the 8.x line. Building against 7.1.x fails
# to compile ("unknown type name 'FFFilter'").
FROM debian:trixie AS ffmpeg-builder
ARG FFMPEG_VERSION=8.1.2
ARG DEBIAN_FRONTEND=noninteractive
# ca-certificates is mandatory for HTTPS downloads: without it wget/curl fail
# TLS verification (wget exit code 5 -> "SSL verification failure"), which is
# exactly what broke CI before.
RUN sed -i 's/^Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources 2>/dev/null || echo "deb http://deb.debian.org/debian trixie non-free non-free-firmware" >> /etc/apt/sources.list ; apt-get update && apt-get install -y --no-install-recommends \
      build-essential yasm nasm pkg-config ca-certificates curl xz-utils \
      libx264-dev libx265-dev libvpx-dev libmp3lame-dev libopus-dev libvorbis-dev \
      libass-dev libfreetype-dev libharfbuzz-dev libfontconfig-dev libfribidi-dev \
      libdrm-dev libva-dev libvpl-dev zlib1g-dev \
    && (apt-get install -y --no-install-recommends libfdk-aac-dev || echo "libfdk-aac-dev not available, building without it") \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /tmp
# Robust source download: try the official ffmpeg.org tarball first, then fall
# back to the GitHub release tag. Each attempt retries transient failures, and
# every candidate is validated (non-empty + tar can list it) before use, so a
# truncated or HTML "error page" download can never slip through to the build.
# The result is always normalised into /tmp/ffmpeg-src regardless of source.
RUN set -eu; \
    dl() { curl -fsSL --retry 5 --retry-delay 3 --retry-all-errors --connect-timeout 20 --max-time 900 -o "$2" "$1"; }; \
    ok=0; \
    for src in \
      "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz|J" \
      "https://www.ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz|J" \
      "https://github.com/FFmpeg/FFmpeg/archive/refs/tags/n${FFMPEG_VERSION}.tar.gz|z" \
      "https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/refs/tags/n${FFMPEG_VERSION}|z" \
    ; do \
      url="${src%|*}"; flag="${src##*|}"; \
      echo ">>> Downloading FFmpeg ${FFMPEG_VERSION} from ${url}"; \
      rm -f ffmpeg-src.tar; \
      if dl "$url" ffmpeg-src.tar && [ -s ffmpeg-src.tar ] && tar -t${flag}f ffmpeg-src.tar >/dev/null 2>&1; then \
        rm -rf ffmpeg-src && mkdir ffmpeg-src \
        && tar -x${flag}f ffmpeg-src.tar -C ffmpeg-src --strip-components=1 \
        && rm -f ffmpeg-src.tar && ok=1 && echo ">>> OK: ${url}" && break; \
      fi; \
      echo ">>> FAILED: ${url} (trying next source)"; \
    done; \
    [ "$ok" = 1 ] || { echo "ERROR: could not download FFmpeg ${FFMPEG_VERSION} from any source" >&2; exit 1; }; \
    test -f ffmpeg-src/configure || { echo "ERROR: extracted tree has no ./configure" >&2; exit 1; }; \
    echo ">>> FFmpeg source version: $(cat ffmpeg-src/RELEASE 2>/dev/null || echo unknown)"
# Inject the patched filter (header-only xfade-easing.h + full vf_xfade.c)
COPY ffmpeg-patch/vf_xfade.c /tmp/ffmpeg-src/libavfilter/vf_xfade.c
COPY ffmpeg-patch/xfade-easing.h /tmp/ffmpeg-src/libavfilter/xfade-easing.h
WORKDIR /tmp/ffmpeg-src
# Notes on the configure flags:
#  * drawtext (used by the renderer for captions) needs libfreetype AND
#    libharfbuzz in FFmpeg >= 7; fontconfig/fribidi are its optional extras.
#  * libvpl enables h264_qsv, which the renderer probes for Intel Quick Sync.
#  * The builder's "configure" output is grepped for the hard requirements so
#    a silently-missing dependency fails the build here instead of at runtime.
#  * ECFLAGS silences the C99 declaration-after-statement warnings that the
#    xfade-easing code triggers (recommended by upstream).
RUN set -eu; \
    if pkg-config --exists fdk-aac 2>/dev/null; then FDK="--enable-libfdk-aac"; else FDK=""; echo "building without libfdk-aac"; fi; \
    ./configure \
      --prefix=/usr/local \
      --enable-gpl --enable-nonfree --enable-version3 \
      --enable-libx264 --enable-libx265 --enable-libvpx --enable-libmp3lame --enable-libopus --enable-libvorbis \
      --enable-libass --enable-libfreetype --enable-libharfbuzz --enable-libfontconfig --enable-libfribidi $FDK \
      --enable-vaapi --enable-libdrm --enable-libvpl \
      --extra-cflags="-O3" \
    || { echo "ERROR: ./configure failed; last lines of ffbuild/config.log:" >&2; tail -n 40 ffbuild/config.log >&2 || true; exit 1; }; \
    for f in LIBX264 LIBX265 LIBFREETYPE LIBHARFBUZZ LIBFONTCONFIG LIBASS LIBVPL VAAPI XFADE_FILTER DRAWTEXT_FILTER H264_QSV_ENCODER; do \
      grep -qx "CONFIG_${f}=yes" ffbuild/config.mak || { echo "ERROR: FFmpeg configured without ${f}" >&2; exit 1; }; \
    done; \
    make -j"$(nproc)" ECFLAGS=-Wno-declaration-after-statement; \
    make install; \
    ffmpeg -hide_banner -version | head -n 1; \
    ffmpeg -hide_banner -h filter=xfade 2>&1 | grep -q "easing" || { echo "ERROR: built ffmpeg lacks xfade easing= option (patch not applied?)" >&2; exit 1; }; \
    ffmpeg -hide_banner -filters 2>/dev/null | grep -q " drawtext " || { echo "ERROR: built ffmpeg lacks drawtext filter" >&2; exit 1; }; \
    ffmpeg -hide_banner -encoders 2>/dev/null | grep -q " libx264 " || { echo "ERROR: built ffmpeg lacks libx264 encoder" >&2; exit 1; }; \
    echo ">>> custom FFmpeg ${FFMPEG_VERSION} + xfade-easing OK"

# Runtime: python:3.13-slim-trixie carries the same library ABIs the builder
# linked against (both are Debian trixie), so the custom binaries run as-is.
FROM python:3.13-slim-trixie AS runtime
LABEL org.opencontainers.image.source="https://github.com/birdy1974/slideshow" \
      org.opencontainers.image.description="Self-hosted FFmpeg photo and video slideshow maker (custom xfade-easing build)"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config PHOTOS_DIR=/photos VIDEOS_DIR=/videos MUSIC_DIR=/music OUTPUT_DIR=/output \
    STATIC_DIR=/app/static
# Runtime shared libs for the custom ffmpeg (matching the builder's --enable-*
# set, using trixie's actual package names) plus the Intel VA-API/QSV stack.
# libfdk-aac2t64 is in non-free, so it gets the same best-effort treatment as
# in the builder: enable non-free first, then install it if it exists.
RUN sed -i 's/^Components: main/Components: main contrib non-free non-free-firmware/' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
    apt-get update && apt-get install -y --no-install-recommends \
      fonts-dejavu-core fontconfig tini ca-certificates \
      intel-media-va-driver i965-va-driver vainfo libmfx-gen1.2 \
      libx264-164 libx265-215 libvpx9 libmp3lame0 libopus0 libvorbis0a libvorbisenc2 \
      libass9 libfreetype6 libharfbuzz0b libfontconfig1 libfribidi0 \
      libdrm2 libva2 libva-drm2 libvpl2 zlib1g \
    && (apt-get install -y --no-install-recommends libfdk-aac2t64 || echo "libfdk-aac2t64 not available") \
    && rm -rf /var/lib/apt/lists/*
# Bring the custom ffmpeg/ffprobe into the runtime image
COPY --from=ffmpeg-builder /usr/local/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-builder /usr/local/bin/ffprobe /usr/local/bin/ffprobe
# Fail the image build (not the first render) if a shared library is missing
# or the xfade-easing options did not make it into the final binary.
RUN set -eu; \
    ffmpeg -hide_banner -version | head -n 1; \
    ffprobe -hide_banner -version | head -n 1; \
    if ldd /usr/local/bin/ffmpeg | grep -q "not found"; then ldd /usr/local/bin/ffmpeg | grep "not found"; echo "ERROR: ffmpeg has unresolved shared libraries" >&2; exit 1; fi; \
    ffmpeg -hide_banner -h filter=xfade 2>&1 | grep -q "easing" && echo "xfade-easing OK" || { echo "ERROR: runtime ffmpeg lacks xfade easing" >&2; exit 1; }
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
