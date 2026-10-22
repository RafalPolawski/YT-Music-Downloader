"""Logging to stdout (docker logs), a rotating file and an in-memory buffer for the web UI."""

from __future__ import annotations

import collections
import itertools
import logging
import logging.handlers
import sys
import threading
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class MemoryHandler(logging.Handler):
    """Keeps the last N log records so the UI can tail them."""

    def __init__(self, capacity: int = 2000):
        super().__init__()
        self._records: collections.deque[dict] = collections.deque(maxlen=capacity)
        self._ids = itertools.count(1)
        self._lock_mem = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info and record.levelno >= logging.ERROR:
                msg += "\n" + logging.Formatter().formatException(record.exc_info)
            with self._lock_mem:
                self._records.append({
                    "id": next(self._ids),
                    "ts": record.created,
                    "level": record.levelname,
                    "logger": record.name,
                    "message": msg,
                })
        except Exception:  # pragma: no cover - logging must never raise
            self.handleError(record)

    def tail(self, after: int = 0, limit: int = 500) -> list[dict]:
        with self._lock_mem:
            items = [r for r in self._records if r["id"] > after]
        return items[-limit:]


memory_handler = MemoryHandler()


def setup_logging(log_dir: Path | None, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    formatter = logging.Formatter(FORMAT, datefmt="%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "ytmd.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    memory_handler.setLevel(logging.INFO)
    root.addHandler(memory_handler)

    for noisy in ("urllib3", "uvicorn.access", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
