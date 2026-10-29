"""Find playlist entries that can no longer be downloaded: removed, private or blocked videos."""

from __future__ import annotations

import re

from ytmd import db as dbm
from ytmd.db import Database
from ytmd.downloader import UNAVAILABLE, classify_error
from ytmd.duplicates import editable

GREYED_OUT = "Greyed out on YouTube Music: removed, private or not available in your country"
_YTDLP_PREFIX = re.compile(r"^(ERROR:\s*)?\[[^\]]+\]\s*[\w-]+:\s*")  # "[youtube] dQw4w9WgXcQ: "


def unavailable_reason(t: dict) -> str | None:
    """Why a track cannot be downloaded any more, or None if it can (or already is on disk)."""
    if t["status"] == dbm.UNAVAILABLE:
        return GREYED_OUT
    if t["status"] == dbm.FAILED:
        if not t["available"]:
            return GREYED_OUT
        if classify_error(t["last_error"]) == UNAVAILABLE:
            return _YTDLP_PREFIX.sub("", t["last_error"])
    return None


def find_unavailable(db: Database) -> list[dict]:
    """Unavailable entries of every synced playlist, grouped by playlist."""
    groups = []
    for p in db.active_playlists():
        items = []
        for e in db.playlist_entries(p["id"]):
            reason = unavailable_reason(e)
            if reason:
                items.append({
                    "video_id": e["video_id"],
                    "set_video_id": e["set_video_id"],
                    "title": e["title"],
                    "artists": e["artists"],
                    "album": e["album"],
                    "thumbnail": e["thumbnail"],
                    "position": e["position"] + 1,
                    "reason": reason,
                })
        if items:
            groups.append({"playlist_id": p["id"], "playlist": p["title"], "editable": editable(p), "items": items})
    return groups
