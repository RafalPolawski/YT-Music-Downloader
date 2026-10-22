"""Background thread that runs a sync every N minutes (or right away when asked to)."""

from __future__ import annotations

import logging
import threading
import time

from ytmd.config import SettingsStore
from ytmd.db import Database
from ytmd.sync import SyncEngine
from ytmd.updater import Updater

log = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 20


class Scheduler:
    def __init__(self, engine: SyncEngine, settings: SettingsStore, db: Database, updater: Updater,
                 auth_ready=lambda: True):
        self.engine = engine
        self.settings = settings
        self.db = db
        self.updater = updater
        self.auth_ready = auth_ready
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._run_now: str | None = None
        self._update_now = False
        self.next_run_at: float = time.time() + STARTUP_DELAY_SECONDS
        self._thread = threading.Thread(target=self._loop, name="scheduler", daemon=True)

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.engine.cancel()
        self._wake.set()
        self._thread.join(timeout=15)

    @property
    def paused(self) -> bool:
        return bool(self.db.get_kv("paused", False))

    def pause(self) -> None:
        self.db.set_kv("paused", True)
        self.engine.cancel()
        self.db.add_event("info", "Automatic sync paused")

    def resume(self) -> None:
        self.db.set_kv("paused", False)
        self.db.add_event("info", "Automatic sync resumed")
        self._wake.set()

    def trigger(self, reason: str = "manual") -> bool:
        """Start a sync as soon as possible. Returns False if one is already running."""
        if self.engine.running:
            return False
        self._run_now = reason
        self._wake.set()
        return True

    def request_update(self) -> None:
        self._update_now = True
        self._wake.set()

    def postpone(self, seconds: float) -> None:
        self.next_run_at = time.time() + seconds
        self._wake.set()

    def reschedule(self) -> None:
        """Recalculate the next run after the interval setting changed."""
        last = (self.db.get_kv("last_sync") or {}).get("finished_at") or time.time()
        interval = self.settings.get().sync_interval_minutes * 60
        self.next_run_at = max(time.time() + 5, last + interval)
        self._wake.set()

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            wait = 30.0 if self.paused else max(0.0, min(30.0, self.next_run_at - time.time()))
            if self._run_now is None and not self._update_now:
                self._wake.wait(timeout=wait)
            self._wake.clear()
            if self._stop.is_set():
                break

            settings = self.settings.get()
            if self._update_now or (settings.ytdlp_auto_update and self.updater.due()):
                self._update_now = False
                try:
                    self.updater.run()
                except Exception:
                    log.exception("yt-dlp update failed")

            manual = self._run_now
            due = time.time() >= self.next_run_at
            if manual is None and (not due or self.paused):
                continue
            self._run_now = None
            if not self.auth_ready():
                self.next_run_at = time.time() + 60
                continue
            try:
                summary = self.engine.run(reason=manual or "scheduled")
            except Exception:  # never let the scheduler thread die
                log.exception("Sync crashed")
                summary = None
            if summary is not None and summary.more_pending and not self.paused:
                self.next_run_at = time.time() + 5
            else:
                self.next_run_at = time.time() + self.settings.get().sync_interval_minutes * 60
