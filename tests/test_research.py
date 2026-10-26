from dataclasses import asdict
from types import SimpleNamespace
import pytest
from investment_lab.data.store import Store
from investment_lab.jobs import create_run, execute_run, reproduce
from investment_lab.research.experiments import rolling, holdout, benchmark_result


def test_rolling_independent_state_and_account(small, config):
    def factory():
        def session(ctx):
            if not ctx.state.get("sent"):
                ctx.order_shares("A", 50)
                ctx.state["sent"] = True
        return SimpleNamespace(on_session=session)
    manifest = {k:v for k,v in small.items() if k != "bars"}
    progress = []
    result = rolling(manifest, small["bars"], config, factory, {}, "day", 3,
                     progress=lambda done, total: progress.append((done, total)))
    assert result["summary"]["count"] == 3
    assert result["summary"]["skipped"] == 2
    assert all(s["result"]["metrics"]["trades"] == 1 for s in result["samples"] if s["status"] == "completed")
    # C-108：summary 必须给出真实回撤聚合，而不是拿“最差收益”冒充回撤
    dd = [s["metrics"]["max_drawdown"] for s in result["samples"] if s["status"] == "completed"]
    assert result["summary"]["worst_drawdown"] == min(dd)
    assert progress[-1][0] == progress[-1][1] == 9
    assert all(done <= next_done and total == 9 for (done, total), (next_done, _) in zip(progress, progress[1:]))


def test_holdout_snapshot_labels_and_reproduction(tmp_path, small, config):
    store = Store(tmp_path / "data")
    snapshot = store.ingest(**small)
    request = {"snapshot": snapshot, "config": asdict(config), "kind": "holdout", "strategy": "buy_hold", "research": {"test_start": "2024-01-09"}}
    first, second = create_run(store, request), create_run(store, request)
    from investment_lab.common import read_json
    assert read_json(store.root / "runs" / first / "request.json")["holdout_label"] == "首次系统内留出测试"
    assert read_json(store.root / "runs" / second / "request.json")["holdout_label"] == "已用于研究"
    result = execute_run(store, first)
    progress = read_json(store.root / "runs" / first / "progress.json")
    assert result["development"]["result"]["curve"][0]["positions"] == {}
    assert result["holdout"]["result"]["curve"][0]["positions"] == {}
    assert result["holdout"]["result"]["trades"][0]["signal_date"] == "2024-01-09"
    assert progress["done"] == progress["total"] > 0
    assert progress["eta_seconds"] == 0


def test_saved_strategy_immutable_reproduce(tmp_path, small, config):
    store = Store(tmp_path / "data")
    snapshot = store.ingest(**small)
    file = store.root / "user_strategies/test.py"
    file.write_text('def on_session(ctx):\n    if not ctx.state.get("sent"):\n        ctx.order_shares("A", 10)\n        ctx.state["sent"] = True\n')
    request = {"snapshot": snapshot, "config": asdict(config), "strategy": "custom", "strategy_file": "test.py"}
    run_id = create_run(store, request)
    file.write_text('raise Exception("new code should never run")')
    execute_run(store, run_id)
    assert reproduce(store, run_id)["identical"]


def test_common_end_does_not_claim_fixed_horizon(small, config):
    """C-110：common_end 下窗口长度不等，metadata 不应再谎报固定 horizon。"""
    manifest = {k: v for k, v in small.items() if k != "bars"}
    result = rolling(manifest, small["bars"], config, lambda: SimpleNamespace(on_session=lambda ctx: None),
                     {}, "day", 3, "common_end")
    assert result["metadata"]["horizon_sessions"] is None
    assert "长度不等" in result["metadata"]["overlap_warning"]


def test_benchmark_weight_is_disclosed(small, config):
    """C-112：基准只投 95% 权益，必须在 metadata 里披露，避免超额收益被高估。"""
    from dataclasses import replace
    manifest = {k: v for k, v in small.items() if k != "bars"}
    bench = benchmark_result(manifest, small["bars"], replace(config, benchmark="A"))
    assert bench["metadata"]["benchmark_weight"] == "1"
    assert "100%" in bench["metadata"]["benchmark_note"]
