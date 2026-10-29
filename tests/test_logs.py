from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from ytmd.app import AppContext
from ytmd.config import Environment
from ytmd.logs import MemoryHandler, log_files, purge_logs
from ytmd.web.api import create_app

LOG = """2026-10-28 23:59:58 INFO    ytmd.sync: Sync started (scheduled)
2026-10-28 23:59:59 DEBUG   ytmd.downloader: noisy detail
"""
TODAY = """2026-10-29 08:00:00 WARNING ytmd.sync: Failed to download 'Song' (transient): HTTP Error 503
2026-10-29 08:00:01 ERROR   ytmd.sync: Sync failed
Traceback (most recent call last):
  File "sync.py", line 1, in run
RuntimeError: boom
"""


def write_logs(log_dir):
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "ytmd.log.2026-10-28").write_text(LOG, encoding="utf-8")
    os.utime(log_dir / "ytmd.log.2026-10-28", (time.time() - 86400,) * 2)
    (log_dir / "ytmd.log").write_text(TODAY, encoding="utf-8")


def test_history_is_loaded_back_after_a_restart(tmp_path):
    write_logs(tmp_path)
    handler = MemoryHandler(capacity=10)
    handler.load_history(tmp_path)
    lines = handler.tail()
    assert [(l["level"], l["logger"]) for l in lines] == [
        ("INFO", "ytmd.sync"), ("WARNING", "ytmd.sync"), ("ERROR", "ytmd.sync")]  # DEBUG skipped
    assert lines[-1]["message"].startswith("Sync failed\nTraceback") and "RuntimeError: boom" in lines[-1]["message"]
    assert lines[0]["id"] < lines[-1]["id"]
    handler.load_history(tmp_path)  # loading twice does not duplicate anything
    assert len(handler.tail()) == 3


def test_old_log_files_are_purged_only_when_a_retention_is_set(tmp_path):
    write_logs(tmp_path)
    old = tmp_path / "ytmd.log.2026-01-01"
    old.write_text("x")
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    (tmp_path / "settings.json").write_text("{}")
    assert purge_logs(tmp_path, 0) == 0 and old.exists()
    assert purge_logs(tmp_path, 30) == 1 and not old.exists()
    assert [f["name"] for f in log_files(tmp_path)] == ["ytmd.log", "ytmd.log.2026-10-28"]


def test_log_files_can_be_listed_and_downloaded(tmp_path):
    env = Environment(config_dir=tmp_path / "config", music_dir=tmp_path / "music", host="127.0.0.1",
                      port=0, web_user="admin", web_password="", log_level="INFO")
    ctx = AppContext(env)
    write_logs(env.log_dir)
    c = TestClient(create_app(ctx), headers={"X-YTMD": "1"})
    r = c.get("/api/logs/files").json()
    assert [f["name"] for f in r["files"]] == ["ytmd.log", "ytmd.log.2026-10-28"]
    assert r["total_bytes"] == len(LOG) + len(TODAY) and r["retention_days"] == 0
    assert "Sync failed" in c.get("/api/logs/files/ytmd.log").text
    assert c.get("/api/logs/files/settings.json").status_code == 404
    assert c.get("/api/logs/files/..%2Fsettings.json").status_code == 404
