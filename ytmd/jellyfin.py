"""Optional Jellyfin integration: ask the server to rescan its libraries after a sync."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f'MediaBrowser Client="yt-music-downloader", Device="yt-music-downloader", '
                         f'DeviceId="yt-music-downloader", Version="1", Token="{api_key}"',
    }


def test_connection(url: str, api_key: str) -> dict:
    r = requests.get(f"{url.rstrip('/')}/System/Info", headers=_headers(api_key), timeout=10)
    r.raise_for_status()
    info = r.json()
    return {"server_name": info.get("ServerName"), "version": info.get("Version")}


def refresh_library(url: str, api_key: str) -> bool:
    try:
        r = requests.post(f"{url.rstrip('/')}/Library/Refresh", headers=_headers(api_key), timeout=15)
        r.raise_for_status()
        log.info("Asked Jellyfin to rescan its libraries")
        return True
    except requests.RequestException as exc:
        log.warning("Jellyfin library refresh failed: %s", exc)
        return False
