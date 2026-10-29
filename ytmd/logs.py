"""Logging to stdout (docker logs), one file per day and an in-memory buffer for the web UI."""

from __future__ import annotations

import collections
import itertools
import logging
import logging.handlers
import re
import sys
import threading
import time
from pathlib import Path

FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
LOG_FILE = "ytmd.log"  # today's log; older days are ytmd.log.YYYY-MM-DD
LOG_FILE_NAME = re.compile(r"^ytmd\.log(\.[\w-]+)?$")
_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+(\S+): (.*)$")


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

    def load_history(self, log_dir: Path) -> None:
        """Fill the buffer from the log files, so the web UI keeps its history across restarts."""
        if self._records:
            return
        records: list[dict] = []
        for path in reversed(log_files(log_dir)[:2]):  # yesterday's file too, right after midnight
            records += _parse(_read_tail(path["path"], 2 * 1024 * 1024))
        with self._lock_mem:
            for r in records[-self._records.maxlen:]:
                if r["level"] != "DEBUG":
                    self._records.append({"id": next(self._ids), **r})


def _read_tail(path: Path, max_bytes: int) -> str:
    try:
        with path.open("rb") as f:
            f.seek(max(0, path.stat().st_size - max_bytes))
            data = f.read()
    except OSError:
        return ""
    text = data.decode("utf-8", errors="replace")
    return text if len(data) < max_bytes else text.split("\n", 1)[-1]  # drop the cut-off first line


def _parse(text: str) -> list[dict]:
    records: list[dict] = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if m:
            ts = time.mktime(time.strptime(m[1], DATE_FORMAT))
            records.append({"ts": ts, "level": m[2], "logger": m[3], "message": m[4]})
        elif records and line.strip():  # traceback and other continuation lines
            records[-1]["message"] += "\n" + line
    return records


def log_files(log_dir: Path) -> list[dict]:
    """Log files on disk, newest first."""
    files = []
    try:
        for p in log_dir.iterdir():
            if p.is_file() and LOG_FILE_NAME.match(p.name):
                st = p.stat()
                files.append({"name": p.name, "path": p, "size": st.st_size, "modified": st.st_mtime})
    except OSError:
        return []
    files.sort(key=lambda f: (f["name"] != LOG_FILE, -f["modified"]))
    return files


def purge_logs(log_dir: Path, retention_days: int, now: float | None = None) -> int:
    """Delete log files of days older than the retention period (0 keeps them forever)."""
    if retention_days <= 0:
        return 0
    cutoff = (now or time.time()) - retention_days * 86400
    removed = 0
    for f in log_files(log_dir):
        if f["name"] != LOG_FILE and f["modified"] < cutoff:
            try:
                f["path"].unlink()
                removed += 1
            except OSError:
                pass
    return removed


memory_handler = MemoryHandler()


def setup_logging(log_dir: Path | None, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in root.handlers[:]:
        root.removeHandler(h)

    formatter = logging.Formatter(FORMAT, datefmt=DATE_FORMAT)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        memory_handler.load_history(log_dir)
        # a new file every midnight; old days are only deleted by purge_logs (Settings -> Keep logs for)
        file_handler = logging.handlers.TimedRotatingFileHandler(
            log_dir / LOG_FILE, when="midnight", backupCount=0, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    memory_handler.setLevel(logging.INFO)
    root.addHandler(memory_handler)

    for noisy in ("urllib3", "uvicorn.access", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
