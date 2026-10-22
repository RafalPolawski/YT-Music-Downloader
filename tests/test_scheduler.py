from __future__ import annotations

import threading
import time

import pytest

from ytmd.config import SettingsStore
from ytmd.db import Database
from ytmd.scheduler import Scheduler
from ytmd.sync import Summary


class FakeEngine:
    def __init__(self):
        self.runs: list[str] = []
        self.results: list = []
        self.cancelled = threading.Event()
        self.running = False

    def run(self, reason="scheduled"):
        self.runs.append(reason)
        result = self.results.pop(0) if self.results else Summary(reason=reason)
        if isinstance(result, Exception):
            raise result
        return result

    def cancel(self):
        self.cancelled.set()


class FakeUpdater:
    def __init__(self):
        self.calls = 0

    def due(self):
        return False

    def run(self):
        self.calls += 1


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def sched(tmp_path):
    engine, updater = FakeEngine(), FakeUpdater()
    db = Database(tmp_path / "state.db")
    auth = {"ready": True}
    s = Scheduler(engine, SettingsStore(tmp_path / "settings.json"), db, updater, auth_ready=lambda: auth["ready"])
    s.next_run_at = time.time() + 3600
    s.start()
    yield s, engine, updater, auth
    s.stop()
    db.close()


def test_manual_trigger_runs_immediately(sched):
    s, engine, _, _ = sched
    assert s.trigger("manual")
    assert wait_for(lambda: engine.runs == ["manual"])
    assert s.next_run_at > time.time() + 3000  # back to the regular interval


def test_due_run_happens_on_its_own(sched):
    s, engine, _, _ = sched
    s.postpone(0)
    assert wait_for(lambda: engine.runs == ["scheduled"])


def test_paused_scheduler_skips_scheduled_runs_but_not_manual_ones(sched):
    s, engine, _, _ = sched
    s.pause()
    assert engine.cancelled.is_set()
    s.postpone(0)
    time.sleep(0.3)
    assert engine.runs == []
    s.trigger("manual")
    assert wait_for(lambda: engine.runs == ["manual"])
    s.resume()
    assert not s.paused


def test_unfinished_downloads_continue_right_away(sched):
    s, engine, _, _ = sched
    engine.results = [Summary(more_pending=True)]
    s.trigger("manual")
    assert wait_for(lambda: len(engine.runs) == 1)
    assert wait_for(lambda: s.next_run_at < time.time() + 10)


def test_a_crashing_run_does_not_kill_the_scheduler(sched):
    s, engine, _, _ = sched
    engine.results = [RuntimeError("boom")]
    s.trigger("manual")
    assert wait_for(lambda: len(engine.runs) == 1)
    assert wait_for(lambda: s.trigger("again"))
    assert wait_for(lambda: engine.runs == ["manual", "again"])


def test_nothing_runs_without_an_account(sched):
    s, engine, _, auth = sched
    auth["ready"] = False
    s.trigger("manual")
    time.sleep(0.3)
    assert engine.runs == []


def test_update_request_runs_the_updater(sched):
    s, _, updater, _ = sched
    s.request_update()
    assert wait_for(lambda: updater.calls == 1)


def test_interval_change_reschedules(sched):
    s, _, _, _ = sched
    s.settings.update({"sync_interval_minutes": 5})
    s.reschedule()
    assert s.next_run_at <= time.time() + 5 * 60 + 1
