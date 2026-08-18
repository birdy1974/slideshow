# syntax=docker/dockerfile:1.7
FROM node:22-bookworm-slim AS frontend
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
COPY public ./public
RUN npm run build

# Debian trixie ships FFmpeg 7.1 (bookworm shipped 5.1). The build asserts the
# major version so an accidental downgrade of the base image fails loudly.
FROM python:3.13-slim-trixie AS runtime
LABEL org.opencontainers.image.source="https://github.com/birdy1974/slideshow" \
      org.opencontainers.image.description="Self-hosted FFmpeg photo and video slideshow maker"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    CONFIG_DIR=/config PHOTOS_DIR=/photos VIDEOS_DIR=/videos MUSIC_DIR=/music OUTPUT_DIR=/output \
    STATIC_DIR=/app/static
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg fonts-dejavu-core fontconfig tini ca-certificates \
      intel-media-va-driver vainfo \
    && (apt-get install -y --no-install-recommends i965-va-driver \
        || echo "i965-va-driver not shipped by this Debian release; the iHD driver covers supported iGPUs") \
    && ffmpeg -version \
    && ffmpeg -version | head -1 | grep -qE 'ffmpeg version ([7-9]|[1-9][0-9])' \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/app ./app
COPY --from=frontend /src/dist ./static
RUN mkdir -p /config/work /config/previews /output /photos /videos /music
EXPOSE 8080
VOLUME ["/config", "/output"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)" || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
