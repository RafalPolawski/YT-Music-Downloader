"""SQLite state: playlists, tracks, playlist membership, album cache and activity events."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlists (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    author           TEXT,
    thumbnail        TEXT,
    remote_count     INTEGER,
    owned            INTEGER NOT NULL DEFAULT 0,
    enabled          INTEGER NOT NULL DEFAULT 1,
    in_library       INTEGER NOT NULL DEFAULT 1,
    library_position INTEGER NOT NULL DEFAULT 0,
    file_name        TEXT,
    fetched          INTEGER NOT NULL DEFAULT 0,
    last_synced_at   REAL,
    last_error       TEXT,
    held_removals    INTEGER NOT NULL DEFAULT 0,
    approve_removals INTEGER NOT NULL DEFAULT 0,
    first_seen_at    REAL
);

CREATE TABLE IF NOT EXISTS tracks (
    video_id        TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    artists         TEXT NOT NULL DEFAULT '[]',
    album           TEXT,
    album_id        TEXT,
    album_artists   TEXT NOT NULL DEFAULT '[]',
    year            TEXT,
    track_number    INTEGER,
    track_total     INTEGER,
    duration        INTEGER,
    thumbnail       TEXT,
    album_thumbnail TEXT,
    video_type      TEXT,
    explicit        INTEGER NOT NULL DEFAULT 0,
    available       INTEGER NOT NULL DEFAULT 1,
    source_video_id TEXT,
    lyrics_id       TEXT,
    remote_sig      TEXT,
    meta_resolved   INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'pending',
    path            TEXT,
    size            INTEGER,
    attempts        INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,
    next_attempt_at REAL,
    added_at        REAL,
    downloaded_at   REAL,
    trashed_at      REAL,
    trash_path      TEXT
);
CREATE INDEX IF NOT EXISTS idx_tracks_status ON tracks(status);

CREATE TABLE IF NOT EXISTS playlist_tracks (
    playlist_id TEXT NOT NULL,
    position    INTEGER NOT NULL,
    video_id    TEXT NOT NULL,
    PRIMARY KEY (playlist_id, position)
);
CREATE INDEX IF NOT EXISTS idx_pt_video ON playlist_tracks(video_id);

CREATE TABLE IF NOT EXISTS albums (
    id         TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    fetched_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    level   TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Track statuses
PENDING = "pending"
DOWNLOADED = "downloaded"
FAILED = "failed"
UNAVAILABLE = "unavailable"
TRASHED = "trashed"
STATUSES = (PENDING, DOWNLOADED, FAILED, UNAVAILABLE, TRASHED)

JSON_COLUMNS = ("artists", "album_artists")
MAX_EVENTS = 2000

# Effective playlist selection: enabled and still present in the YT Music library.
ACTIVE_PLAYLIST = "p.enabled = 1 AND p.in_library = 1"


def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    d = dict(row)
    for col in JSON_COLUMNS:
        if col in d and isinstance(d[col], str):
            try:
                d[col] = json.loads(d[col])
            except ValueError:
                d[col] = []
    return d


class Database:
    """Small synchronous DAO. All access is serialized with a lock (SQLite is fast enough)."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ helpers
    def _all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [_row_to_dict(r) for r in self._conn.execute(sql, tuple(params)).fetchall()]

    def _one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            return _row_to_dict(self._conn.execute(sql, tuple(params)).fetchone())

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> int:
        with self._lock:
            return self._conn.execute(sql, tuple(params)).rowcount

    def transaction(self):
        return _Transaction(self)

    # ------------------------------------------------------------------ kv
    def get_kv(self, key: str, default: Any = None) -> Any:
        row = self._one("SELECT value FROM kv WHERE key = ?", (key,))
        if row is None or row["value"] is None:
            return default
        try:
            return json.loads(row["value"])
        except ValueError:
            return default

    def set_kv(self, key: str, value: Any) -> None:
        self._exec(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # ------------------------------------------------------------------ events
    def add_event(self, level: str, message: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO events(ts, level, message) VALUES(?, ?, ?)", (time.time(), level, message)
            )
            self._conn.execute(
                "DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ?", (MAX_EVENTS,)
            )

    def events(self, limit: int = 50) -> list[dict]:
        return self._all("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ playlists
    def playlists(self) -> list[dict]:
        return self._all(
            """
            SELECT p.*,
                   (SELECT COUNT(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id) AS local_count,
                   (SELECT COUNT(*) FROM playlist_tracks pt JOIN tracks t ON t.video_id = pt.video_id
                     WHERE pt.playlist_id = p.id AND t.status = 'downloaded') AS downloaded_count
            FROM playlists p
            ORDER BY p.in_library DESC, p.library_position ASC, p.title COLLATE NOCASE
            """
        )

    def playlist(self, playlist_id: str) -> dict | None:
        return self._one("SELECT * FROM playlists WHERE id = ?", (playlist_id,))

    def active_playlists(self) -> list[dict]:
        return self._all(
            f"SELECT * FROM playlists p WHERE {ACTIVE_PLAYLIST} ORDER BY p.library_position"
        )

    def upsert_playlist(self, pl: dict, position: int, default_enabled: bool) -> bool:
        """Insert or refresh a playlist seen in the library. Returns True if it is new."""
        now = time.time()
        with self._lock:
            existing = self._conn.execute("SELECT id FROM playlists WHERE id = ?", (pl["id"],)).fetchone()
            if existing is None:
                self._conn.execute(
                    """INSERT INTO playlists(id, title, author, thumbnail, remote_count, owned, enabled,
                                             in_library, library_position, first_seen_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
                    (pl["id"], pl["title"], pl.get("author"), pl.get("thumbnail"), pl.get("count"),
                     int(bool(pl.get("owned"))), int(default_enabled), position, now),
                )
                return True
            self._conn.execute(
                """UPDATE playlists SET title = ?, author = ?, thumbnail = ?, remote_count = ?, owned = ?,
                                        in_library = 1, library_position = ? WHERE id = ?""",
                (pl["title"], pl.get("author"), pl.get("thumbnail"), pl.get("count"),
                 int(bool(pl.get("owned"))), position, pl["id"]),
            )
            return False

    def mark_missing_playlists(self, present_ids: set[str]) -> list[dict]:
        """Flag playlists that disappeared from the library. Returns the newly missing ones."""
        missing = [p for p in self._all("SELECT * FROM playlists WHERE in_library = 1")
                   if p["id"] not in present_ids]
        for p in missing:
            self._exec("UPDATE playlists SET in_library = 0 WHERE id = ?", (p["id"],))
        return missing

    def update_playlist(self, playlist_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE playlists SET {cols} WHERE id = ?", (*fields.values(), playlist_id))

    def delete_playlist(self, playlist_id: str) -> None:
        with self.transaction():
            self._exec("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
            self._exec("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    def playlist_video_ids(self, playlist_id: str) -> list[str]:
        rows = self._all(
            "SELECT video_id FROM playlist_tracks WHERE playlist_id = ? ORDER BY position", (playlist_id,)
        )
        return [r["video_id"] for r in rows]

    def set_playlist_tracks(self, playlist_id: str, video_ids: list[str]) -> None:
        with self.transaction():
            self._exec("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
            with self._lock:
                self._conn.executemany(
                    "INSERT INTO playlist_tracks(playlist_id, position, video_id) VALUES(?, ?, ?)",
                    [(playlist_id, i, vid) for i, vid in enumerate(video_ids)],
                )

    def clear_inactive_memberships(self) -> int:
        return self._exec(
            f"""DELETE FROM playlist_tracks WHERE playlist_id IN
                (SELECT id FROM playlists p WHERE NOT ({ACTIVE_PLAYLIST}))"""
        )

    def playlist_entries(self, playlist_id: str) -> list[dict]:
        """Ordered tracks of a playlist, joined with their local state."""
        return self._all(
            """SELECT t.*, pt.position FROM playlist_tracks pt JOIN tracks t ON t.video_id = pt.video_id
               WHERE pt.playlist_id = ? ORDER BY pt.position""",
            (playlist_id,),
        )

    # ------------------------------------------------------------------ tracks
    def track(self, video_id: str) -> dict | None:
        return self._one("SELECT * FROM tracks WHERE video_id = ?", (video_id,))

    def upsert_track_from_playlist(self, t: dict) -> None:
        """Store metadata of a playlist item.

        Title/artists/album may later be improved by metadata resolution, so they are only
        overwritten when the playlist item itself changed on YouTube Music (``remote_sig``).
        """
        now = time.time()
        artists = json.dumps(t.get("artists") or [])
        sig = json.dumps([t["title"], t.get("artists") or [], t.get("album_id"), t.get("video_type")])
        available = int(t.get("available", True))
        with self._lock:
            row = self._conn.execute(
                "SELECT remote_sig, status FROM tracks WHERE video_id = ?", (t["video_id"],)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    """INSERT INTO tracks(video_id, title, artists, album, album_id, duration, thumbnail,
                                          video_type, explicit, available, status, remote_sig, added_at)
                       VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (t["video_id"], t["title"], artists, t.get("album"), t.get("album_id"),
                     t.get("duration"), t.get("thumbnail"), t.get("video_type"), int(bool(t.get("explicit"))),
                     available, PENDING if available else UNAVAILABLE, sig, now),
                )
                return
            status = row["status"]
            if status == UNAVAILABLE and available:
                status = PENDING
            elif status == PENDING and not available:
                status = UNAVAILABLE
            self._conn.execute(
                """UPDATE tracks SET duration = ?, thumbnail = ?, explicit = ?, available = ?, status = ?
                   WHERE video_id = ?""",
                (t.get("duration"), t.get("thumbnail"), int(bool(t.get("explicit"))), available, status,
                 t["video_id"]),
            )
            if row["remote_sig"] != sig:
                self._conn.execute(
                    """UPDATE tracks SET title = ?, artists = ?, album = ?, album_id = ?, video_type = ?,
                                         remote_sig = ?, meta_resolved = 0, source_video_id = NULL,
                                         album_artists = '[]', year = NULL, track_number = NULL,
                                         track_total = NULL, album_thumbnail = NULL
                       WHERE video_id = ?""",
                    (t["title"], artists, t.get("album"), t.get("album_id"), t.get("video_type"), sig,
                     t["video_id"]),
                )

    def update_track(self, video_id: str, **fields: Any) -> None:
        if not fields:
            return
        for col in JSON_COLUMNS:
            if col in fields and not isinstance(fields[col], str):
                fields[col] = json.dumps(fields[col])
        cols = ", ".join(f"{k} = ?" for k in fields)
        self._exec(f"UPDATE tracks SET {cols} WHERE video_id = ?", (*fields.values(), video_id))

    def delete_track(self, video_id: str) -> None:
        self._exec("DELETE FROM tracks WHERE video_id = ?", (video_id,))

    def referenced_tracks(self) -> list[dict]:
        """Tracks that belong to at least one active playlist (ordered by first appearance)."""
        return self._all(
            f"""SELECT t.*, MIN(p.library_position * 100000 + pt.position) AS sort_key
                FROM tracks t
                JOIN playlist_tracks pt ON pt.video_id = t.video_id
                JOIN playlists p ON p.id = pt.playlist_id
                WHERE {ACTIVE_PLAYLIST}
                GROUP BY t.video_id
                ORDER BY sort_key"""
        )

    def unreferenced_tracks(self) -> list[dict]:
        return self._all(
            f"""SELECT t.* FROM tracks t WHERE NOT EXISTS (
                    SELECT 1 FROM playlist_tracks pt JOIN playlists p ON p.id = pt.playlist_id
                    WHERE pt.video_id = t.video_id AND {ACTIVE_PLAYLIST})"""
        )

    def tracks_by_status(self, status: str) -> list[dict]:
        return self._all("SELECT * FROM tracks WHERE status = ?", (status,))

    def downloaded_tracks(self) -> list[dict]:
        return self._all(
            "SELECT * FROM tracks WHERE status = 'downloaded' ORDER BY downloaded_at, video_id"
        )

    def search_tracks(
        self, query: str = "", status: str = "", playlist_id: str = "", offset: int = 0, limit: int = 100
    ) -> tuple[int, list[dict]]:
        where, params = ["1 = 1"], []
        if query:
            where.append("(t.title LIKE ? OR t.artists LIKE ? OR t.album LIKE ? OR t.video_id = ?)")
            like = f"%{query}%"
            params += [like, like, like, query]
        if status:
            where.append("t.status = ?")
            params.append(status)
        if playlist_id:
            where.append("EXISTS (SELECT 1 FROM playlist_tracks pt WHERE pt.video_id = t.video_id"
                         " AND pt.playlist_id = ?)")
            params.append(playlist_id)
        cond = " AND ".join(where)
        total = self._one(f"SELECT COUNT(*) AS n FROM tracks t WHERE {cond}", params)["n"]
        rows = self._all(
            f"""SELECT t.*,
                       (SELECT GROUP_CONCAT(p.title, ' • ') FROM playlist_tracks pt
                          JOIN playlists p ON p.id = pt.playlist_id WHERE pt.video_id = t.video_id) AS playlists
                FROM tracks t WHERE {cond}
                ORDER BY CASE t.status WHEN 'failed' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                         t.added_at DESC, t.title COLLATE NOCASE
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        )
        return total, rows

    def stats(self) -> dict:
        counts = {s: 0 for s in STATUSES}
        for r in self._all("SELECT status, COUNT(*) AS n FROM tracks GROUP BY status"):
            counts[r["status"]] = r["n"]
        size = self._one("SELECT COALESCE(SUM(size), 0) AS s FROM tracks WHERE status = 'downloaded'")["s"]
        trash_size = self._one("SELECT COALESCE(SUM(size), 0) AS s FROM tracks WHERE status = 'trashed'")["s"]
        pl = self._one(
            f"""SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN {ACTIVE_PLAYLIST} THEN 1 ELSE 0 END), 0) AS active
                FROM playlists p WHERE p.in_library = 1"""
        )
        return {
            "tracks": counts,
            "library_bytes": size,
            "trash_bytes": trash_size,
            "playlists_total": pl["total"],
            "playlists_active": pl["active"],
        }

    # ------------------------------------------------------------------ albums
    def cached_album(self, album_id: str, max_age: float) -> dict | None:
        row = self._one("SELECT data, fetched_at FROM albums WHERE id = ?", (album_id,))
        if row is None or time.time() - row["fetched_at"] > max_age:
            return None
        return json.loads(row["data"])

    def cache_album(self, album_id: str, data: dict) -> None:
        self._exec(
            """INSERT INTO albums(id, data, fetched_at) VALUES(?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET data = excluded.data, fetched_at = excluded.fetched_at""",
            (album_id, json.dumps(data), time.time()),
        )


class _Transaction:
    def __init__(self, db: Database):
        self.db = db

    def __enter__(self):
        self.db._lock.acquire()
        self.db._conn.execute("BEGIN")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        try:
            self.db._conn.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self.db._lock.release()
        return False
