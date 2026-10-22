"""The sync engine: makes the music folder mirror the selected YouTube Music playlists.

One run does, in order:

1. Read the library's playlists and the tracks of every enabled playlist.
2. Move files that no playlist references any more to the trash (and restore files from
   the trash that were re-added), relocate files whose desired path changed.
3. Download missing tracks, tag them, add cover art and lyrics.
4. Write one ``.m3u8`` per playlist, purge old trash, optionally poke Jellyfin.

YouTube Music is the source of truth. Two safety nets protect against API hiccups:
an empty/partial library listing never removes playlists en masse, and a playlist that
suddenly loses a large share of its tracks is held until the removal is approved in the UI.
"""

from __future__ import annotations

import concurrent.futures
import logging
import random
import shutil
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

from ytmd import db as dbm
from ytmd import jellyfin
from ytmd.config import Environment, Settings, SettingsStore
from ytmd.downloader import (CANCELLED, NEEDS_LOGIN, RATE_LIMITED, UNAVAILABLE, DownloadError,
                             DownloadOptions, Progress, clean_work_dir, download_audio)
from ytmd.library import (COVER_FILE, PathAllocator, Trash, move_file, prune_parents, remove_with_sidecars,
                          render_m3u8, sanitize, track_relpath, with_suffix_tag, write_if_changed)
from ytmd.tagger import TrackTags, audio_info, fetch_image, square_jpeg, to_lrc, write_tags
from ytmd.ytmusic import (LIKED_MUSIC_ID, VIDEO_TYPE_SONG, AuthError, YTMusicClient, auth_mode, make_client,
                          netscape_cookies_from_auth)

log = logging.getLogger(__name__)

ALBUM_CACHE_SECONDS = 30 * 86400
RETRY_BACKOFF = [3600, 3 * 3600, 12 * 3600, 24 * 3600]
GIVE_UP_RETRY = 7 * 86400
PLAYLIST_REFRESH_SECONDS = 300
# Long download queues are split into runs of this length so playlist changes on
# YouTube Music are still picked up while a big library is being downloaded.
DOWNLOAD_BUDGET_SECONDS = 3600
COOLDOWNS = [3600, 2 * 3600, 4 * 3600, 8 * 3600, 12 * 3600]


class Cancelled(Exception):
    pass


@dataclass
class Summary:
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    reason: str = ""
    playlists: int = 0
    new_tracks: int = 0
    downloaded: int = 0
    failed: int = 0
    removed: int = 0
    restored: int = 0
    relocated: int = 0
    playlist_files_changed: int = 0
    held_playlists: int = 0
    held_library_removals: int = 0
    playlist_errors: int = 0
    trash_purged: int = 0
    rate_limited: bool = False
    more_pending: bool = False
    cancelled: bool = False
    error: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.downloaded or self.removed or self.restored or self.relocated
                    or self.playlist_files_changed)

    def describe(self) -> str:
        parts = []
        if self.downloaded:
            parts.append(f"{self.downloaded} downloaded")
        if self.removed:
            parts.append(f"{self.removed} removed")
        if self.restored:
            parts.append(f"{self.restored} restored from trash")
        if self.relocated:
            parts.append(f"{self.relocated} moved")
        if self.failed:
            parts.append(f"{self.failed} failed")
        if self.held_playlists or self.held_library_removals:
            parts.append(f"{self.held_playlists + self.held_library_removals} removal(s) waiting for approval")
        if self.more_pending:
            parts.append("more downloads in the next run")
        return ", ".join(parts) if parts else "everything up to date"


class LiveState:
    """What the UI shows while a sync is running."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "running": False, "phase": "idle", "message": "", "started_at": None,
            "playlists_done": 0, "playlists_total": 0,
            "downloads_done": 0, "downloads_failed": 0, "downloads_total": 0,
            "active": {}, "last": None,
        }

    def update(self, **kw) -> None:
        with self._lock:
            self._data.update(kw)

    def incr(self, key: str, by: int = 1) -> None:
        with self._lock:
            self._data[key] += by

    def set_active(self, video_id: str, info: dict | None) -> None:
        with self._lock:
            active = dict(self._data["active"])
            if info is None:
                active.pop(video_id, None)
            else:
                active[video_id] = {**active.get(video_id, {}), **info}
            self._data["active"] = active

    def snapshot(self) -> dict:
        with self._lock:
            data = dict(self._data)
            data["active"] = list(self._data["active"].values())
            return data


class SyncEngine:
    def __init__(
        self,
        env: Environment,
        settings: SettingsStore,
        db: dbm.Database,
        client_factory: Callable[[Settings], YTMusicClient] | None = None,
        download_fn: Callable = download_audio,
        image_fetcher: Callable[[str | None, int], bytes | None] = fetch_image,
    ):
        self.env = env
        self.settings_store = settings
        self.db = db
        self.client_factory = client_factory or (lambda s: make_client(env, s.brand_account_id))
        self.download_fn = download_fn
        self.fetch_image = image_fetcher
        self.trash = Trash(env.trash_dir)
        self.state = LiveState()
        self._run_lock = threading.Lock()
        self._cancel = threading.Event()
        self._halt_downloads = threading.Event()
        self._budget_spent = threading.Event()
        self._cover_cache: dict[str, bytes | None] = {}
        self._cover_lock = threading.Lock()
        self.allocator = PathAllocator()
        self._recover_staging()

    # ------------------------------------------------------------------ public API
    @property
    def music_dir(self) -> Path:
        return self.env.music_dir

    @property
    def running(self) -> bool:
        return self._run_lock.locked()

    @property
    def cooldown_until(self) -> float:
        return self.db.get_kv("cooldown_until", 0) or 0

    def cancel(self) -> None:
        self._cancel.set()

    def approve_library_removal(self) -> None:
        self.db.set_kv("approve_library_removal", True)

    def clear_cooldown(self) -> None:
        self.db.set_kv("cooldown_until", 0)
        self.db.set_kv("cooldown_level", 0)

    def refresh_library(self) -> list[dict]:
        """Only read the list of playlists (used right after connecting an account)."""
        settings = self.settings_store.get()
        client = self.client_factory(settings)
        self._update_library(client, settings, Summary())
        return self.db.playlists()

    def run(self, reason: str = "scheduled") -> Summary | None:
        if not self._run_lock.acquire(blocking=False):
            return None
        summary = Summary(reason=reason)
        try:
            self._cancel.clear()
            self._halt_downloads.clear()
            self._budget_spent.clear()
            self._cover_cache.clear()
            settings = self.settings_store.get()
            self.state.update(running=True, phase="connecting", message="Connecting to YouTube Music",
                              started_at=summary.started_at, playlists_done=0, playlists_total=0,
                              downloads_done=0, downloads_failed=0, downloads_total=0, active={})
            log.info("Sync started (%s)", reason)
            try:
                client = self.client_factory(settings)
                self._sync_playlists(client, settings, summary)
                self._reconcile(settings, summary)
                self._write_playlists(settings, summary)
                self._download_all(client, settings, summary)
                self._write_playlists(settings, summary)
                self._finish(settings, summary)
                self.db.set_kv("auth_error", None)
            except Cancelled:
                summary.cancelled = True
                log.warning("Sync cancelled")
                try:
                    self._write_playlists(settings, summary)
                except Exception:
                    log.exception("Could not write playlist files")
            except AuthError as exc:
                summary.error = str(exc)
                self.db.set_kv("auth_error", str(exc))
                log.error("Sync failed: %s", exc)
            except Exception as exc:  # keep the scheduler alive no matter what
                summary.error = f"{type(exc).__name__}: {exc}"
                log.exception("Sync failed")
            summary.finished_at = time.time()
            self._record(summary)
            return summary
        finally:
            self.state.update(running=False, phase="idle", message="", active={},
                              last=asdict(summary) | {"text": summary.describe()})
            self._run_lock.release()

    # ------------------------------------------------------------------ step 1: playlists
    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _update_library(self, client: YTMusicClient, settings: Settings, summary: Summary) -> set[str]:
        """Refresh the playlist list. Returns ids of playlists whose removal is on hold."""
        remote = client.library_playlists()
        known = {p["id"]: p for p in self.db.playlists() if p["in_library"]}
        present = {p["id"] for p in remote}
        missing = [p for pid, p in known.items() if pid not in present]
        for pos, pl in enumerate(remote):
            default = settings.auto_enable_new_playlists and pl["id"] != LIKED_MUSIC_ID
            if self.db.upsert_playlist(pl, pos, default):
                note = "will be synced" if default else "not synced (enable it in Playlists)"
                self.db.add_event("info", f"New playlist '{pl['title']}' - {note}")

        # A glitch on YouTube's side could make many playlists vanish from the listing at
        # once; that is held back until approved instead of trashing whole playlists.
        suspicious = bool(missing) and (not remote or (len(missing) >= 3 and len(missing) * 2 >= len(known)))
        if suspicious and not self.db.get_kv("approve_library_removal"):
            titles = sorted(p["title"] for p in missing)
            if self.db.get_kv("held_library_removal") != titles:
                self.db.add_event("warning", f"{len(missing)} playlist(s) disappeared from your library at once "
                                             f"({', '.join(titles[:5])}{'…' if len(titles) > 5 else ''}) - "
                                             "removal is on hold until you approve it")
            self.db.set_kv("held_library_removal", titles)
            summary.held_library_removals = len(missing)
            return {p["id"] for p in missing}
        self.db.set_kv("held_library_removal", None)
        self.db.set_kv("approve_library_removal", False)
        if missing:
            self.db.mark_missing_playlists(present)
            for p in missing:
                self.db.add_event("warning", f"Playlist '{p['title']}' is no longer in your library")
        return set()

    def _sync_playlists(self, client: YTMusicClient, settings: Settings, summary: Summary) -> None:
        self.state.update(phase="playlists", message="Reading your library")
        held = self._update_library(client, settings, summary)
        active = [p for p in self.db.active_playlists() if p["id"] not in held]
        self.state.update(playlists_total=len(active))
        summary.playlists = len(active)

        for p in active:
            self._check_cancel()
            self.state.update(message=f"Reading playlist '{p['title']}'")
            try:
                tracks = client.playlist_tracks(p["id"])
            except Exception as exc:
                log.warning("Could not read playlist '%s': %s", p["title"], exc)
                self.db.update_playlist(p["id"], last_error=f"Could not read playlist: {exc}")
                summary.playlist_errors += 1
                self.state.incr("playlists_done")
                continue

            old_ids = self.db.playlist_video_ids(p["id"])
            new_ids = [t["video_id"] for t in tracks]
            removed = set(old_ids) - set(new_ids)
            added = set(new_ids) - set(old_ids)

            if p["fetched"] and not p["approve_removals"] and self._guard_trips(len(removed), len(set(old_ids)), settings):
                msg = (f"{len(removed)} of {len(set(old_ids))} tracks disappeared at once - "
                       "removal is on hold until you approve it")
                if p["held_removals"] != len(removed):
                    self.db.add_event("warning", f"'{p['title']}': {msg}")
                self.db.update_playlist(p["id"], held_removals=len(removed), last_error=msg)
                summary.held_playlists += 1
                self.state.incr("playlists_done")
                continue

            for t in tracks:
                if self.db.track(t["video_id"]) is None:
                    summary.new_tracks += 1
                self.db.upsert_track_from_playlist(t)
            self.db.set_playlist_tracks(p["id"], new_ids)
            self.db.update_playlist(p["id"], fetched=1, last_synced_at=time.time(), last_error=None,
                                    held_removals=0, approve_removals=0,
                                    remote_count=len(new_ids))
            if p["fetched"] and (added or removed):
                self.db.add_event("info", f"'{p['title']}': {len(added)} added, {len(removed)} removed on YouTube Music")
            self.state.incr("playlists_done")

        self.db.clear_inactive_memberships()

    @staticmethod
    def _guard_trips(removed: int, previous: int, settings: Settings) -> bool:
        if removed <= settings.removal_guard_min_tracks or previous == 0:
            return False
        return removed * 100 >= previous * settings.removal_guard_percent

    # ------------------------------------------------------------------ step 2: files
    def _reconcile(self, settings: Settings, summary: Summary) -> None:
        self._check_cancel()
        self.state.update(phase="cleanup", message="Updating local files")
        music = self.music_dir

        for t in self.db.unreferenced_tracks():
            if t["status"] == dbm.DOWNLOADED and t["path"]:
                self._remove_track_file(t, settings)
                summary.removed += 1
            elif t["status"] == dbm.TRASHED:
                if not t["trash_path"] or not (self.trash.root / t["trash_path"]).exists():
                    self.db.delete_track(t["video_id"])
            else:
                self.db.delete_track(t["video_id"])

        allocator = PathAllocator()
        referenced = self.db.referenced_tracks()
        downloaded = sorted((t for t in referenced if t["status"] == dbm.DOWNLOADED),
                            key=lambda t: (t["downloaded_at"] or 0, t["video_id"]))
        relocations = []
        for t in downloaded:
            if not t["path"] or not (music / t["path"]).exists():
                log.warning("File for '%s' is missing on disk - it will be downloaded again", t["title"])
                self.db.update_track(t["video_id"], status=dbm.PENDING, path=None, attempts=0,
                                     next_attempt_at=None)
                continue
            ext = PurePosixPath(t["path"]).suffix
            desired = allocator.claim(t["video_id"], track_relpath(t, settings.layout, ext))
            if desired != t["path"]:
                relocations.append((t, desired))
        summary.relocated += self._relocate(relocations)

        for t in referenced:
            if t["status"] != dbm.TRASHED:
                continue
            ext = PurePosixPath(t["trash_path"] or "x.opus").suffix
            desired = allocator.claim(t["video_id"], track_relpath(t, settings.layout, ext))
            if t["trash_path"] and not (music / desired).exists() and self.trash.restore(t["trash_path"], music / desired):
                self.db.update_track(t["video_id"], status=dbm.DOWNLOADED, path=desired, trash_path=None,
                                     trashed_at=None)
                self.db.add_event("info", f"Restored from trash: {self._label(t)}")
                summary.restored += 1
            else:
                allocator.release(desired)
                self.db.update_track(t["video_id"], status=dbm.PENDING, path=None, trash_path=None,
                                     trashed_at=None, attempts=0, next_attempt_at=None)
        self.allocator = allocator

    def _remove_track_file(self, t: dict, settings: Settings) -> None:
        music = self.music_dir
        src = music / t["path"]
        trash_rel = None
        if settings.trash_retention_days > 0:
            trash_rel = self.trash.put(music, t["path"])
        else:
            remove_with_sidecars(src)
        prune_parents(src.parent, music)
        if trash_rel:
            self.db.update_track(t["video_id"], status=dbm.TRASHED, trash_path=trash_rel, path=None,
                                 trashed_at=time.time())
        else:
            self.db.delete_track(t["video_id"])
        where = "moved to trash" if trash_rel else "deleted"
        self.db.add_event("info", f"Removed ({where}): {self._label(t)}")
        log.info("Removed %s (%s)", t["path"], where)

    def _relocate(self, relocations: list[tuple[dict, str]]) -> int:
        """Move files to new paths in two phases so swaps/chains cannot collide."""
        if not relocations:
            return 0
        music = self.music_dir
        staging = self.env.tmp_dir / "relocate"
        staged = []
        for t, desired in relocations:
            src = music / t["path"]
            tmp = staging / f"{t['video_id']}{src.suffix}"
            move_file(src, tmp)
            prune_parents(src.parent, music)
            staged.append((t, desired, tmp))
        for t, desired, tmp in staged:
            if (music / desired).exists():
                desired = with_suffix_tag(desired, t["video_id"])
            move_file(tmp, music / desired)
            self.db.update_track(t["video_id"], path=desired)
        log.info("Reorganized %d file(s) to match the library layout", len(staged))
        return len(staged)

    def _recover_staging(self) -> None:
        """Put back files left in the relocation staging folder by a crash, then clear temp files."""
        staging = self.env.tmp_dir / "relocate"
        if staging.exists():
            for f in staging.iterdir():
                t = self.db.track(f.stem)
                if t and t["path"] and not (self.music_dir / t["path"]).exists():
                    move_file(f, self.music_dir / t["path"])
                    log.info("Recovered %s after an interrupted reorganization", t["path"])
        shutil.rmtree(self.env.tmp_dir, ignore_errors=True)

    # ------------------------------------------------------------------ step 3: playlist files
    def _playlists_dir(self, settings: Settings) -> Path:
        rel = PurePosixPath(settings.playlists_dir.strip().strip("/") or "Playlists")
        parts = [sanitize(p) for p in rel.parts if p not in ("..", ".")]
        return self.music_dir.joinpath(*parts) if parts else self.music_dir / "Playlists"

    def _write_playlists(self, settings: Settings, summary: Summary) -> None:
        pl_dir = self._playlists_dir(settings)
        previous_dir = self.db.get_kv("playlists_dir")
        if previous_dir and previous_dir != str(pl_dir):
            for p in self.db.playlists():
                if p["file_name"]:
                    (Path(previous_dir) / p["file_name"]).unlink(missing_ok=True)
                    self.db.update_playlist(p["id"], file_name=None)
            prune_parents(Path(previous_dir), self.music_dir)
        self.db.set_kv("playlists_dir", str(pl_dir))

        used: set[str] = set()
        active_ids = set()
        for p in self.db.active_playlists():
            active_ids.add(p["id"])
            base = sanitize(p["title"], "Playlist")
            name, n = base, 2
            while name.casefold() in used:
                name, n = f"{base} ({n})", n + 1
            used.add(name.casefold())
            file_name = f"{name}.m3u8"
            if p["file_name"] and p["file_name"] != file_name:
                (pl_dir / p["file_name"]).unlink(missing_ok=True)
            entries = [e for e in self.db.playlist_entries(p["id"]) if e["status"] == dbm.DOWNLOADED and e["path"]]
            if not entries and not (pl_dir / file_name).exists() and not p["fetched"]:
                continue
            if write_if_changed(pl_dir / file_name, render_m3u8(p["title"], entries, pl_dir, self.music_dir)):
                summary.playlist_files_changed += 1
            if p["file_name"] != file_name:
                self.db.update_playlist(p["id"], file_name=file_name)

        for p in self.db.playlists():
            if p["id"] in active_ids:
                continue
            if p["file_name"]:
                (pl_dir / p["file_name"]).unlink(missing_ok=True)
                self.db.update_playlist(p["id"], file_name=None)
                summary.playlist_files_changed += 1
            if not p["in_library"]:
                self.db.delete_playlist(p["id"])

    # ------------------------------------------------------------------ step 4: downloads
    def _download_options(self, settings: Settings) -> DownloadOptions:
        cookies = None
        if self.env.cookies_file.exists():
            cookies = self.env.cookies_file.read_text(encoding="utf-8", errors="ignore")
        elif settings.use_account_cookies and auth_mode(self.env) == "browser":
            cookies = netscape_cookies_from_auth(self.env.auth_file)
        return DownloadOptions(
            audio_format=settings.audio_format,
            cookies_text=cookies,
            sponsorblock=settings.sponsorblock_remove_offtopic,
            extra_args=settings.ytdlp_extra_args,
        )

    def _download_all(self, client: YTMusicClient, settings: Settings, summary: Summary) -> None:
        self._check_cancel()
        now = time.time()
        referenced = self.db.referenced_tracks()
        refresh = [t for t in referenced if t["status"] == dbm.DOWNLOADED and not t["meta_resolved"]]
        queue = [t for t in referenced
                 if t["status"] in (dbm.PENDING, dbm.FAILED) and t["available"]
                 and (t["next_attempt_at"] or 0) <= now]
        if not queue and not refresh:
            return
        if self.cooldown_until > now:
            log.warning("Downloads paused until %s because YouTube rate-limited us",
                        time.strftime("%H:%M", time.localtime(self.cooldown_until)))
            summary.rate_limited = True
            return

        self.state.update(phase="downloading", downloads_total=len(queue),
                          message=f"Downloading {len(queue)} track(s)" if queue else "Updating track metadata")
        opts = self._download_options(settings)
        started = last_write = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=settings.concurrent_downloads,
                                                   thread_name_prefix="dl") as pool:
            futures = [pool.submit(self._refresh_track, client, settings, t, summary) for t in refresh]
            futures += [pool.submit(self._download_track, client, settings, opts, t, summary) for t in queue]
            pending = set(futures)
            while pending:
                done, pending = concurrent.futures.wait(
                    pending, timeout=5, return_when=concurrent.futures.FIRST_COMPLETED)
                for f in done:
                    if f.exception():
                        log.error("Download worker crashed: %s", f.exception())
                if time.time() - last_write > PLAYLIST_REFRESH_SECONDS:
                    self._write_playlists(settings, summary)
                    last_write = time.time()
                if not self._budget_spent.is_set() and time.time() - started > DOWNLOAD_BUDGET_SECONDS:
                    log.info("Download time budget used up - re-checking playlists before continuing")
                    self._budget_spent.set()
        if self._halt_downloads.is_set():
            summary.rate_limited = True
        elif self._budget_spent.is_set():
            summary.more_pending = True
        self._check_cancel()

    def _stopped(self) -> bool:
        return self._cancel.is_set() or self._halt_downloads.is_set() or self._budget_spent.is_set()

    def _download_track(self, client, settings: Settings, opts: DownloadOptions, t: dict, summary: Summary) -> None:
        if self._stopped():
            return
        vid = t["video_id"]
        self.state.set_active(vid, {"video_id": vid, "title": t["title"], "artists": t["artists"],
                                    "percent": 0, "speed": 0, "stage": "preparing"})
        work_dir = self.env.tmp_dir / vid
        relpath = None
        try:
            t = {**t, **self._resolve_metadata(client, settings, t)}
            relpath = self.allocator.claim(vid, track_relpath(t, settings.layout, settings.audio_format))
            target = self.music_dir / relpath
            if target.exists():
                info = audio_info(target)
                if info and info["video_id"] in (None, vid):
                    self._finalize(client, settings, t, target, target)
                    self._mark_downloaded(t, relpath, target, adopted=True)
                    summary.downloaded += 1
                    return
                self.allocator.release(relpath)
                relpath = self.allocator.claim(vid, with_suffix_tag(relpath, vid))
                target = self.music_dir / relpath

            def on_progress(p: Progress) -> None:
                self.state.set_active(vid, {"percent": p.percent, "speed": p.speed, "stage": "downloading"})

            produced = self.download_fn(t.get("source_video_id") or vid, work_dir, opts, on_progress, self._cancel)
            self.state.set_active(vid, {"percent": 100, "stage": "tagging"})
            self._finalize(client, settings, t, produced, target)
            self._mark_downloaded(t, relpath, target)
            summary.downloaded += 1
            self.db.set_kv("cooldown_level", 0)
        except DownloadError as exc:
            if relpath:
                self.allocator.release(relpath)
            self._handle_failure(t, settings, exc.kind, str(exc), summary)
        except Exception as exc:
            if relpath:
                self.allocator.release(relpath)
            log.exception("Unexpected error while processing '%s'", t["title"])
            self._handle_failure(t, settings, "error", f"{type(exc).__name__}: {exc}", summary)
        finally:
            clean_work_dir(work_dir)
            self.state.set_active(vid, None)
            if not self._stopped() and settings.download_delay_seconds:
                self._cancel.wait(settings.download_delay_seconds * random.uniform(0.5, 1.5))

    def _mark_downloaded(self, t: dict, relpath: str, target: Path, adopted: bool = False) -> None:
        self.db.update_track(t["video_id"], status=dbm.DOWNLOADED, path=relpath, size=target.stat().st_size,
                             downloaded_at=time.time(), attempts=0, last_error=None, next_attempt_at=None)
        self.state.incr("downloads_done")
        log.info("%s %s", "Adopted existing file" if adopted else "Downloaded", relpath)

    def _handle_failure(self, t: dict, settings: Settings, kind: str, message: str, summary: Summary) -> None:
        vid = t["video_id"]
        if kind == CANCELLED:
            return
        if kind == RATE_LIMITED:
            self._enter_cooldown(message)
            return
        attempts = (t.get("attempts") or 0) + 1
        if kind in (UNAVAILABLE, NEEDS_LOGIN) or attempts >= settings.max_attempts:
            retry_in = GIVE_UP_RETRY
        else:
            retry_in = RETRY_BACKOFF[min(attempts - 1, len(RETRY_BACKOFF) - 1)]
        if kind == NEEDS_LOGIN:
            message += " (needs a signed-in session: enable account cookies or upload cookies.txt in Settings)"
        self.db.update_track(vid, status=dbm.FAILED, attempts=attempts, last_error=message,
                             next_attempt_at=time.time() + retry_in)
        summary.failed += 1
        self.state.incr("downloads_failed")
        log.warning("Failed to download '%s' (%s): %s", t["title"], kind, message)

    def _enter_cooldown(self, message: str) -> None:
        if self._halt_downloads.is_set():
            return
        self._halt_downloads.set()
        level = self.db.get_kv("cooldown_level", 0) or 0
        pause = COOLDOWNS[min(level, len(COOLDOWNS) - 1)]
        self.db.set_kv("cooldown_level", level + 1)
        self.db.set_kv("cooldown_until", time.time() + pause)
        msg = f"YouTube is rate-limiting downloads ({message}). Pausing downloads for {pause // 3600}h."
        log.warning(msg)
        self.db.add_event("warning", msg)

    # ------------------------------------------------------------------ metadata
    def _album(self, client, album_id: str) -> dict | None:
        cached = self.db.cached_album(album_id, ALBUM_CACHE_SECONDS)
        if cached is not None:
            return cached
        try:
            album = client.album(album_id)
        except Exception as exc:
            log.debug("Album lookup failed for %s: %s", album_id, exc)
            return None
        self.db.cache_album(album_id, album)
        return album

    def _resolve_metadata(self, client, settings: Settings, t: dict) -> dict:
        """Find the album version, album artist, year, track number and lyrics id for a track."""
        if t["meta_resolved"]:
            return {}
        vid = t["video_id"]
        upd: dict = {"meta_resolved": 1}
        wants_counterpart = (settings.prefer_album_version and not t["album_id"]
                             and t["video_type"] != VIDEO_TYPE_SONG)
        if settings.write_lyrics or wants_counterpart:
            try:
                info = client.watch_info(vid)
                upd["lyrics_id"] = info.get("lyrics_id")
                cp = info.get("counterpart")
                if wants_counterpart and cp and cp.get("video_type") == VIDEO_TYPE_SONG and cp.get("album_id"):
                    upd.update(source_video_id=cp["video_id"], title=cp["title"], album=cp["album"],
                               album_id=cp["album_id"], artists=cp["artists"] or t["artists"])
                    log.info("Using album version of '%s' (%s)", t["title"], cp["album"])
            except Exception as exc:
                log.debug("Watch info lookup failed for %s: %s", vid, exc)

        album_id = upd.get("album_id") or t["album_id"]
        if album_id:
            album = self._album(client, album_id)
            if album:
                artists = upd.get("artists") or t["artists"]
                upd.update(album=album.get("title") or upd.get("album") or t["album"],
                           album_artists=album.get("artists") or artists[:1],
                           year=album.get("year"), track_total=album.get("track_count"),
                           album_thumbnail=album.get("thumbnail"))
                wanted_ids = {vid, upd.get("source_video_id")}
                title = (upd.get("title") or t["title"]).casefold()
                match = next((a for a in album["tracks"] if a.get("video_id") in wanted_ids), None) or \
                    next((a for a in album["tracks"] if (a.get("title") or "").casefold() == title), None)
                if match and match.get("track_number"):
                    upd["track_number"] = match["track_number"]
        self.db.update_track(vid, **upd)
        return upd

    def _cover(self, t: dict, settings: Settings) -> bytes | None:
        key = t.get("album_id") if t.get("album_thumbnail") else t["video_id"]
        url = t.get("album_thumbnail") or t.get("thumbnail")
        with self._cover_lock:
            if key in self._cover_cache:
                return self._cover_cache[key]
        raw = self.fetch_image(url, settings.cover_size)
        cover = square_jpeg(raw, settings.cover_size) if raw else None
        with self._cover_lock:
            if len(self._cover_cache) > 200:
                self._cover_cache.clear()
            self._cover_cache[key] = cover
        return cover

    def _finalize(self, client, settings: Settings, t: dict, produced: Path, target: Path) -> None:
        """Tag the downloaded file, move it into the library, add lyrics and folder cover."""
        lyrics = None
        if settings.write_lyrics and t.get("lyrics_id"):
            try:
                lyrics = client.lyrics(t["lyrics_id"])
            except Exception as exc:
                log.debug("Lyrics lookup failed for %s: %s", t["video_id"], exc)
        tags = TrackTags(
            title=t["title"], artists=t["artists"] or [], album=t.get("album"),
            album_artists=(t.get("album_artists") or (t["artists"] or [])[:1]) if t.get("album") else [],
            year=t.get("year"), track_number=t.get("track_number"), track_total=t.get("track_total"),
            video_id=t["video_id"], lyrics=lyrics["text"] if lyrics else None,
        )
        cover = self._cover(t, settings)
        write_tags(produced, tags, cover)
        if produced != target:
            if target.exists():
                remove_with_sidecars(target)
            move_file(produced, target)
        lrc = target.with_suffix(".lrc")
        if lyrics and lyrics.get("synced"):
            lrc.write_text(to_lrc(lyrics["synced"], tags), encoding="utf-8")
        elif lrc.exists() and not settings.write_lyrics:
            lrc.unlink()
        if settings.write_album_covers and settings.layout == "artist_album" and t.get("album") and cover:
            folder_cover = target.parent / COVER_FILE
            if not folder_cover.exists():
                folder_cover.write_bytes(cover)

    def _refresh_track(self, client, settings: Settings, t: dict, summary: Summary) -> None:
        """A track changed on YouTube Music after it was downloaded: re-tag and maybe rename it."""
        if self._stopped():
            return
        try:
            t = {**t, **self._resolve_metadata(client, settings, t)}
            current = self.music_dir / t["path"]
            if not current.exists():
                return
            self.allocator.release(t["path"])
            relpath = self.allocator.claim(t["video_id"], track_relpath(t, settings.layout, current.suffix))
            target = self.music_dir / relpath
            if target.exists() and relpath != t["path"]:
                self.allocator.release(relpath)
                relpath = self.allocator.claim(t["video_id"], with_suffix_tag(relpath, t["video_id"]))
                target = self.music_dir / relpath
            self._finalize(client, settings, t, current, target)
            if relpath != t["path"]:
                prune_parents(current.parent, self.music_dir)
                summary.relocated += 1
            self.db.update_track(t["video_id"], path=relpath, size=target.stat().st_size)
            log.info("Updated metadata of %s", relpath)
        except Exception as exc:
            log.warning("Could not update metadata of '%s': %s", t["title"], exc)

    # ------------------------------------------------------------------ step 5: wrap up
    def _finish(self, settings: Settings, summary: Summary) -> None:
        self.state.update(phase="finishing", message="Cleaning up")
        summary.trash_purged = self.trash.purge(settings.trash_retention_days)
        for t in self.db.tracks_by_status(dbm.TRASHED):
            if not t["trash_path"] or not (self.trash.root / t["trash_path"]).exists():
                self.db.delete_track(t["video_id"])
        if summary.changed and settings.jellyfin_url and settings.jellyfin_api_key:
            jellyfin.refresh_library(settings.jellyfin_url, settings.jellyfin_api_key)

    def _record(self, summary: Summary) -> None:
        self.db.set_kv("last_sync", asdict(summary) | {"text": summary.describe()})
        if summary.error:
            self.db.add_event("error", f"Sync failed: {summary.error}")
        elif summary.cancelled:
            self.db.add_event("warning", f"Sync cancelled ({summary.describe()})")
        else:
            took = int((summary.finished_at or time.time()) - summary.started_at)
            level = ("warning" if summary.failed or summary.rate_limited or summary.held_playlists
                     or summary.held_library_removals else "success")
            self.db.add_event(level, f"Sync finished in {took // 60}m {took % 60}s: {summary.describe()}")
        log.info("Sync finished: %s", summary.describe())

    @staticmethod
    def _label(t: dict) -> str:
        artists = ", ".join(t.get("artists") or [])
        return f"{artists} - {t['title']}" if artists else t["title"]
