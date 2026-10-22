# syntax=docker/dockerfile:1
# Multi-arch image (linux/amd64, linux/arm64 - e.g. Apple Silicon Mac mini).

# Base image versions are written out (no ARGs) so Dependabot can keep them current.

# yt-dlp needs a JavaScript runtime to solve YouTube's player challenges.
FROM denoland/deno:bin-2.9.7 AS deno
# Static ffmpeg/ffprobe builds (amd64 + arm64), much smaller than Debian's ffmpeg package.
FROM mwader/static-ffmpeg:9.0.2-1 AS ffmpeg

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PYTHONPATH=/app \
    PATH=/opt/venv/bin:$PATH \
    YTMD_CONFIG_DIR=/config \
    YTMD_MUSIC_DIR=/music \
    YTMD_PORT=8080 \
    HOME=/config \
    XDG_CACHE_HOME=/config/cache \
    DENO_DIR=/config/cache/deno \
    PUID=1000 \
    PGID=1000 \
    TZ=Etc/UTC

RUN groupadd --gid 1000 ytmd \
    && useradd --uid 1000 --gid 1000 --no-create-home --home-dir /config --shell /usr/sbin/nologin ytmd

COPY --from=deno /deno /usr/local/bin/deno
COPY --from=ffmpeg /ffmpeg /ffprobe /usr/local/bin/

COPY requirements.txt /tmp/requirements.txt
RUN python -m venv /opt/venv \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && chown -R ytmd:ytmd /opt/venv

WORKDIR /app
COPY ytmd /app/ytmd
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8080
VOLUME ["/config", "/music"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('YTMD_PORT','8080'), timeout=4)" || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "-m", "ytmd"]

LABEL org.opencontainers.image.title="YT Music Downloader" \
      org.opencontainers.image.description="Keeps local copies of your YouTube Music playlists in sync" \
      org.opencontainers.image.source="https://github.com/RafalPolawski/YT-Music-Downloader" \
      org.opencontainers.image.licenses="MIT"
