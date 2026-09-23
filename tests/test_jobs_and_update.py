import time
import threading
from dataclasses import asdict
from copy import deepcopy
from types import SimpleNamespace
import pytest

from investment_lab.common import atomic_write, file_lock, read_json
from investment_lab.data.store import Store
from investment_lab.jobs import JobManager, create_run, execute_run


def test_writer_waits_then_acquires(tmp_path):
    path = tmp_path / "test.lock"
    acquired = []
    with file_lock(path):
        def wait_lock():
            with file_lock(path, timeout=2):
                acquired.append(True)
        thread = threading.Thread(target=wait_lock)
        thread.start()
        time.sleep(.1)
        assert not acquired
    thread.join(3)
    assert acquired == [True]


def test_dynamic_helper_reset_each_rolling_window(tmp_path, small, config):
    store = Store(tmp_path / "data")
    snapshot = store.ingest(**small)
    (store.root / "user_strategies/helper.py").write_text('count=0\n')
    (store.root / "user_strategies/main.py").write_text('def on_session(ctx):\n    import helper\n    if helper.count == 0:\n        ctx.order_shares("A", 10)\n    helper.count += 1\n')
    run_id = create_run(store, {"snapshot": snapshot, "config": asdict(config), "kind": "rolling", "strategy": "custom", "strategy_file": "main.py", "research": {"interval":"day", "horizon":3}})
    result = execute_run(store, run_id)
    assert all(s["result"]["metrics"]["trades"] == 1 for s in result["samples"] if s["status"] == "completed")


def test_process_cancellation_and_limits(tmp_path, small, config):
    store = Store(tmp_path / "data")
    snapshot = store.ingest(**small)
    (store.root / "user_strategies/infinite.py").write_text('def on_session(ctx):\n    while True:\n        pass\n')
    request = {"snapshot": snapshot, "config": asdict(config), "strategy": "custom", "strategy_file": "infinite.py"}
    first = create_run(store, request)
    manager = JobManager(store)
    manager.start(first)
    time.sleep(.15)
    manager.cancel(first)
    assert manager.active[first].poll() is not None
    assert store.runs()[0]["status"] == "cancelled"
    second = create_run(store, request)
    manager.start(second, timeout=.5)
    deadline = time.monotonic() + 8
    while manager.active[second].poll() is None and time.monotonic() < deadline:
        time.sleep(.1)
    assert manager.active[second].poll() is not None
    # watcher writes the final status after waiting for process termination
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and store.runs()[0]["status"] not in ("cancelled", "failed"):
        time.sleep(.05)
    assert read_json(store.root / "runs" / second / "error.json")["message"] == "资源/时间限制"


def test_update_idempotent_failure_preserves_data(tmp_path, small, monkeypatch):
    import investment_lab.data.update as updater
    store = Store(tmp_path / "data")
    payload = deepcopy(small)
    def fetch(self, *args, **kwargs):
        return {"bars": payload["bars"], "raw": payload["bars"], "source": {"provider":"test", "downloaded":str(time.monotonic())}}
    monkeypatch.setattr(updater, "EODHD", type("Fake", (), {"fetch":fetch}))
    monkeypatch.setattr(updater, "completed_session", lambda market: small["sessions"][-1])
    monkeypatch.setattr(updater, "trading_sessions", lambda *args: small["sessions"])
    config_path = store.root / "local_config/providers.json"
    atomic_write(config_path, {"targets":[{"dataset":"A", "provider":"eodhd", "symbol":"A", "security":small["securities"]["A"], "start":small["sessions"][0]}]})
    first = updater.update(store)
    assert first["added"] == 5
    original = first["items"][0]["snapshot"]
    second = updater.update(store)
    assert second["added"] == second["revised"] == 0
    assert second["items"][0]["snapshot"] == original
    payload["bars"][0]["close"] = "10.5"
    third = updater.update(store)
    assert third["revised"] == 1
    assert store.load(original)[1][0]["close"] == "10"
    payload["bars"] = []
    assert updater.update(store)["failed"] == 1
    assert store.datasets()[0]["id"] == third["items"][0]["snapshot"]
