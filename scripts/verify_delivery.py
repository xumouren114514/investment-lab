"""Actual isolated-worker, backup, restore, replay and local HTTP delivery exercise."""
import json
import platform
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

from investment_lab.common import PROJECT, atomic_write, now, read_json
from investment_lab.data.store import Store
from investment_lab.jobs import JobManager, create_run, reproduce
from investment_lab.maintenance.backup import backup, backup_root, restore


def main():
    store = Store()
    snapshot = next(d["id"] for d in store.datasets() if d["manifest"]["synthetic"])
    report = {"created": now(), "machine": platform.platform(), "python": sys.version, "snapshot": snapshot, "scenarios": []}
    base = {"snapshot": snapshot, "strategy": "buy_hold", "params": {"weight": "0.95"}, "config": {
        "symbols": ["DEMO.A"], "start": "2023-01-02", "end": "2025-12-31", "initial_cash": "100000", "benchmark": "DEMO.ETF"}}
    cases = [{**base, "kind": "single"},
             {**base, "kind": "rolling", "research": {"interval": "quarter", "horizon": 120, "end_mode": "fixed_length"}},
             {**base, "kind": "holdout", "research": {"test_start": "2025-01-02", "gap_sessions": 5}}]
    first_id = None
    for case in cases:
        began = time.perf_counter()
        run_id = create_run(store, case)
        manager = JobManager(store)
        manager.start(run_id)
        manager.active[run_id].wait()
        run_dir = store.root / "runs" / run_id
        if not (run_dir / "result.json").exists():
            raise RuntimeError((run_dir / "worker.log").read_text(encoding="utf-8"))
        result = read_json(run_dir / "result.json")
        elapsed = round(time.perf_counter() - began, 3)
        report["scenarios"].append({"kind": case["kind"], "run_id": run_id, "seconds": elapsed, "result_hash": result["result_hash"]})
        print(json.dumps(report["scenarios"][-1]), flush=True)
        first_id = first_id or run_id
    point = backup(store)
    destination = PROJECT.parent / ("investment_lab_restore_drill_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
    restored = restore(backup_root(store), point["id"], destination)
    replay = reproduce(Store(destination), first_id)
    if not replay["identical"]:
        raise RuntimeError("恢复后复现结果不同")
    from fastapi.testclient import TestClient
    from investment_lab.web.app import create_app
    with TestClient(create_app(destination)) as client:
        assert client.get("/").status_code == 200
        status = client.get("/api/status").json()
        assert status["datasets"] == len(store.datasets())
        assert client.get("/api/runs").status_code == 200
    report["restore"] = {**restored, "backtest_reproduced": True, "replay": replay, "http_query_verified": True, "backup_id": point["id"]}
    atomic_write(destination / "state/restore-report.json", report["restore"])
    atomic_write(store.root / "state/delivery-verification.json", report)
    atomic_write(PROJECT / "docs/delivery-verification.json", report)
    print(json.dumps(report["restore"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
