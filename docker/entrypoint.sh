#!/bin/sh
# Runs the app as PUID:PGID so files in /music belong to you, not to root.
set -e

if [ "$(id -u)" = "0" ]; then
    PUID="${PUID:-1000}"
    PGID="${PGID:-1000}"

    if [ "$(id -g ytmd)" != "$PGID" ]; then
        groupmod -o -g "$PGID" ytmd
    fi
    if [ "$(id -u ytmd)" != "$PUID" ]; then
        usermod -o -u "$PUID" ytmd
    fi

    mkdir -p "$YTMD_CONFIG_DIR" "$YTMD_MUSIC_DIR"
    chown -R ytmd:ytmd "$YTMD_CONFIG_DIR"
    # /music is left alone on purpose: set PUID/PGID to the owner of your music folder.

    # yt-dlp updates itself at runtime, so the virtualenv must belong to the app user.
    if [ "$(stat -c %u /opt/venv)" != "$PUID" ]; then
        chown -R ytmd:ytmd /opt/venv
    fi

    exec setpriv --reuid=ytmd --regid=ytmd --init-groups "$@"
fi

exec "$@"
