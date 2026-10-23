"""Find songs that appear more than once in the same YouTube Music playlist."""

from __future__ import annotations

import re

from ytmd.db import Database
from ytmd.ytmusic import LIKED_MUSIC_ID, VIDEO_TYPE_SONG

# "(Official Video)", "[Lyrics]", "(Official Audio)", "(HD)" ... do not make a different song.
_NOISE = re.compile(r"\s*[(\[][^)\]]*\b(official|video|audio|lyrics?|visuali[sz]er|hd|4k|mv)\b[^)\]]*[)\]]", re.I)


def song_key(title: str | None, artists: list[str] | None) -> str:
    name = _NOISE.sub("", title or "").casefold()
    name = re.sub(r"\W+", " ", name).strip()
    artist = (artists[0] if artists else "").casefold().strip()
    return f"{artist}|{name}"


def find_duplicates(db: Database) -> list[dict]:
    """Groups of entries that are the same song within one synced playlist."""
    groups = []
    for p in db.active_playlists():
        by_song: dict[str, list[dict]] = {}
        for e in db.playlist_entries(p["id"]):
            by_song.setdefault(song_key(e["title"], e["artists"]), []).append(e)
        for entries in by_song.values():
            if len(entries) < 2:
                continue
            # Keep the album version if there is one, otherwise the first entry.
            keep = next((i for i, e in enumerate(entries) if e["video_type"] == VIDEO_TYPE_SONG), 0)
            groups.append({
                "playlist_id": p["id"],
                "playlist": p["title"],
                "editable": bool(p["owned"]) and p["id"] != LIKED_MUSIC_ID,
                "kind": "same" if len({e["video_id"] for e in entries}) == 1 else "similar",
                "items": [{
                    "video_id": e["video_id"],
                    "set_video_id": e["set_video_id"],
                    "title": e["title"],
                    "artists": e["artists"],
                    "album": e["album"],
                    "thumbnail": e["thumbnail"],
                    "position": e["position"] + 1,
                    "album_version": e["video_type"] == VIDEO_TYPE_SONG,
                    "keep": i == keep,
                } for i, e in enumerate(entries)],
            })
    return groups
