"""Keeps yt-dlp fresh: YouTube changes often and an outdated yt-dlp is the #1 cause of failures."""

from __future__ import annotations

import importlib.util
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from ytmd.downloader import ytdlp_version

log = logging.getLogger(__name__)

CHECK_EVERY_SECONDS = 24 * 3600


def _site_packages_writable() -> bool:
    spec = importlib.util.find_spec("yt_dlp")
    if spec is None or not spec.origin:
        return False
    return os.access(Path(spec.origin).parent.parent, os.W_OK)


def update_ytdlp() -> dict:
    """Upgrade yt-dlp in the running environment. Returns old/new versions."""
    before = ytdlp_version()
    if not _site_packages_writable():
        return {"updated": False, "before": before, "after": before,
                "error": "Python environment is read-only, cannot update yt-dlp"}
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir",
           "--disable-pip-version-check", "--quiet", "yt-dlp[default]"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"updated": False, "before": before, "after": before, "error": str(exc)}
    after = ytdlp_version()
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["pip failed"]
        return {"updated": False, "before": before, "after": after, "error": err[0]}
    return {"updated": before != after, "before": before, "after": after, "error": None}


class Updater:
    def __init__(self, db):
        self.db = db

    def due(self) -> bool:
        last = self.db.get_kv("ytdlp_last_check", 0) or 0
        return time.time() - last > CHECK_EVERY_SECONDS

    def run(self) -> dict:
        log.info("Checking for yt-dlp updates...")
        result = update_ytdlp()
        self.db.set_kv("ytdlp_last_check", time.time())
        self.db.set_kv("ytdlp_version", result.get("after"))
        if result["error"]:
            log.warning("yt-dlp update failed: %s", result["error"])
        elif result["updated"]:
            log.info("yt-dlp updated %s -> %s", result["before"], result["after"])
            self.db.add_event("info", f"yt-dlp updated to {result['after']}")
        else:
            log.info("yt-dlp is up to date (%s)", result["after"])
        return result
