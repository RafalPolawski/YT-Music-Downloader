"""Filesystem side of the library: file naming, folder layout, trash and .m3u8 playlists."""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
import unicodedata
from pathlib import Path

log = logging.getLogger(__name__)

# Characters that are illegal on Windows/SMB shares (and '/' everywhere) are replaced by
# their full-width look-alikes, so names stay readable and portable.
_REPLACEMENTS = {
    ":": "：", "*": "＊", "?": "？", '"': "＂", "<": "＜", ">": "＞", "|": "｜", "/": "／", "\\": "＼",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_COMPONENT_BYTES = 180  # well below the 255 byte limit of APFS/ext4, leaves room for suffixes
SIDECAR_EXTS = (".lrc",)
JUNK_FILES = {".DS_Store", "Thumbs.db", "desktop.ini", ".directory"}
COVER_FILE = "cover.jpg"
# A folder that only holds these is considered empty once its tracks are gone.
PRUNABLE = JUNK_FILES | {COVER_FILE}


def sanitize(text: str | None, fallback: str = "Unknown") -> str:
    """Make a single, portable path component out of arbitrary text."""
    text = unicodedata.normalize("NFKC", text or "")
    text = _CONTROL.sub("", text)
    text = "".join(_REPLACEMENTS.get(c, c) for c in text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(".").strip()
    if not text or text in {".", ".."}:
        text = fallback
    if text.upper() in _RESERVED:
        text = f"_{text}"
    if text.startswith("."):
        text = "_" + text[1:]
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_COMPONENT_BYTES:
        text = encoded[:MAX_COMPONENT_BYTES].decode("utf-8", "ignore").rstrip(" .")
    return text


def primary(names: list[str] | None, fallback: str = "Unknown Artist") -> str:
    return names[0] if names else fallback


def track_relpath(track: dict, layout: str, ext: str) -> str:
    """Relative path (POSIX separators, without collision suffix) for a track."""
    title = sanitize(track.get("title"), "Unknown Title")
    artists = track.get("artists") or []
    album = track.get("album")
    album_artist = primary(track.get("album_artists") or artists)
    ext = ext.lstrip(".")

    if layout == "flat":
        joined = ", ".join(artists) if artists else "Unknown Artist"
        return f"{sanitize(joined)} - {title}.{ext}"
    if layout == "artist":
        return f"{sanitize(primary(artists))}/{title}.{ext}"
    # artist_album (default)
    if album:
        number = track.get("track_number")
        prefix = f"{int(number):02d} " if number else ""
        return f"{sanitize(album_artist)}/{sanitize(album)}/{prefix}{title}.{ext}"
    return f"{sanitize(primary(artists))}/Singles/{title}.{ext}"


def with_suffix_tag(relpath: str, tag: str) -> str:
    stem, dot, ext = relpath.rpartition(".")
    return f"{stem} [{tag}].{ext}" if dot else f"{relpath} [{tag}]"


class PathAllocator:
    """Hands out unique relative paths (case-insensitively, because macOS file systems are)."""

    def __init__(self) -> None:
        self._used: dict[str, str] = {}
        self._lock = threading.Lock()

    def claim(self, video_id: str, relpath: str) -> str:
        with self._lock:
            key = relpath.casefold()
            owner = self._used.get(key)
            if owner is None or owner == video_id:
                self._used[key] = video_id
                return relpath
            tagged = with_suffix_tag(relpath, video_id)
            self._used[tagged.casefold()] = video_id
            return tagged

    def release(self, relpath: str) -> None:
        with self._lock:
            self._used.pop(relpath.casefold(), None)


def sidecars(path: Path) -> list[Path]:
    return [path.with_suffix(ext) for ext in SIDECAR_EXTS if path.with_suffix(ext).exists()]


def move_file(src: Path, dst: Path) -> None:
    """Move a file (and its sidecar files) creating parent folders as needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    for side in sidecars(src):
        shutil.move(str(side), str(dst.with_suffix(side.suffix)))
    shutil.move(str(src), str(dst))


def remove_with_sidecars(path: Path) -> None:
    for p in [*sidecars(path), path]:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


class Trash:
    """A folder inside the music dir where removed tracks wait before being deleted for good."""

    def __init__(self, root: Path):
        self.root = root

    def put(self, music_dir: Path, relpath: str) -> str | None:
        """Move ``music_dir/relpath`` into the trash. Returns the path relative to the trash root."""
        src = music_dir / relpath
        if not src.exists():
            return None
        dst = self.root / relpath
        if dst.exists():
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dst = dst.with_name(f"{dst.stem} (removed {stamp}){dst.suffix}")
        move_file(src, dst)
        now = time.time()
        for p in [dst, *sidecars(dst)]:
            os.utime(p, (now, now))  # mtime marks the moment it entered the trash
        return dst.relative_to(self.root).as_posix()

    def restore(self, trash_relpath: str, target: Path) -> bool:
        src = self.root / trash_relpath
        if not src.exists():
            return False
        move_file(src, target)
        return True

    def items(self) -> list[dict]:
        out = []
        if not self.root.exists():
            return out
        for p in self.root.rglob("*"):
            if p.is_file() and p.suffix not in SIDECAR_EXTS and p.name not in JUNK_FILES:
                st = p.stat()
                out.append({"path": p.relative_to(self.root).as_posix(), "size": st.st_size,
                            "trashed_at": st.st_mtime})
        out.sort(key=lambda i: i["trashed_at"], reverse=True)
        return out

    def delete(self, trash_relpath: str) -> bool:
        target = (self.root / trash_relpath).resolve()
        if self.root.resolve() not in target.parents:
            raise ValueError("Path outside of trash")
        if not target.is_file():
            return False
        remove_with_sidecars(target)
        prune_empty_dirs(self.root, keep_root=True)
        return True

    def purge(self, retention_days: int) -> int:
        """Delete everything that has been in the trash longer than ``retention_days``."""
        if not self.root.exists():
            return 0
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for p in list(self.root.rglob("*")):
            if p.is_file() and p.stat().st_mtime <= cutoff:
                p.unlink()
                if p.suffix not in SIDECAR_EXTS:
                    removed += 1
        prune_empty_dirs(self.root, keep_root=True)
        return removed

    def empty(self) -> int:
        return self.purge(-1)


def prune_empty_dirs(root: Path, keep_root: bool = True) -> int:
    """Remove empty folders below ``root`` (folders holding only OS junk files count as empty)."""
    if not root.exists():
        return 0
    removed = 0
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        d = Path(dirpath)
        if keep_root and d == root:
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        if all(e.is_file() and e.name in PRUNABLE for e in entries):
            for e in entries:
                e.unlink(missing_ok=True)
            try:
                d.rmdir()
                removed += 1
            except OSError:
                pass
    return removed


def prune_parents(start: Path, stop_at: Path) -> None:
    """Remove ``start`` and its parents while they are empty, never touching ``stop_at`` itself."""
    stop = stop_at.resolve()
    d = start.resolve()
    while d != stop and stop in d.parents:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        if not all(e.is_file() and e.name in PRUNABLE for e in entries):
            return
        for e in entries:
            e.unlink(missing_ok=True)
        try:
            d.rmdir()
        except OSError:
            return
        d = d.parent


def render_m3u8(title: str, entries: list[dict], playlist_dir: Path, music_dir: Path) -> str:
    """Build an extended M3U playlist with paths relative to the playlist file."""
    lines = ["#EXTM3U", f"#PLAYLIST:{title}"]
    for e in entries:
        artists = ", ".join(e.get("artists") or [])
        label = f"{artists} - {e['title']}" if artists else e["title"]
        lines.append(f"#EXTINF:{int(e.get('duration') or -1)},{label}")
        rel = os.path.relpath(music_dir / e["path"], playlist_dir)
        lines.append(Path(rel).as_posix())
    return "\n".join(lines) + "\n"


def write_if_changed(path: Path, content: str) -> bool:
    try:
        if path.read_text(encoding="utf-8") == content:
            return False
    except (FileNotFoundError, UnicodeDecodeError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
    return True


def disk_usage(path: Path) -> dict:
    try:
        u = shutil.disk_usage(path)
        return {"total": u.total, "used": u.used, "free": u.free}
    except OSError:
        return {"total": 0, "used": 0, "free": 0}
