from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import run_small
from investment_lab.engine.reference import REFERENCE_BASIS, REFERENCE_MODE, YAHOO_BLOCK
from investment_lab.jobs import create_run, execute_run, reproduce
from investment_lab.research.experiments import benchmark_result, holdout, rolling
from investment_lab.strategies.examples import Builtin
from investment_lab.web.app import create_app


@pytest.fixture
def reference(small):
    small["synthetic"] = False
    small["source"] = {"provider": "Yahoo/chart"}
    small["securities"]["A"].update(
        execution_blocked=YAHOO_BLOCK, listed=None, listing_verified=False,
        actions_verified=False, lot=100, lot_verified=False,
    )
    for bar in small["bars"]:
        bar.update(price_basis=REFERENCE_BASIS, source="Yahoo/chart", vwap_value=None,
                   vwap_method="unavailable", quality_status="unavailable")
    return small


def test_reference_opt_in_keeps_strict_and_close_guards(reference, config):
    for mode in ("strict_vwap", "estimated_vwap", "close_research"):
        with pytest.raises(ValueError, match="仅可查阅"):
            run_small(reference, replace(config, mode=mode), Builtin("buy_hold"))
    result = run_small(reference, replace(config, mode=REFERENCE_MODE), Builtin("buy_hold"))
    assert result["trades"][0]["quantity"] == "95"
    assert result["trades"][0]["date"] == "2024-01-05"
    assert result["metadata"]["reference_only"]
    assert not result["metadata"]["normal_ranking_eligible"]
    assert not result["metadata"]["point_in_time_prices_verified"]


def test_reference_quantity_frozen_and_history_uses_close(reference, config):
    observed = []
    def on_session(ctx):
        observed.append(ctx.history("A", 10))
        if not ctx.state.get("sent"):
            ctx.order_value("A", 1000)
            ctx.state["sent"] = True
    reference["bars"][1].update(open="20", close="20", low="19", high="21")
    result = run_small(reference, replace(config, mode=REFERENCE_MODE), SimpleNamespace(on_session=on_session))
    assert observed[0] == [Decimal(10)]
    assert result["orders"][0]["quantity"] == "100"
    assert result["trades"][0]["quantity"] == "50"
    assert result["trades"][0]["benchmark_price"] == "20"
    assert result["trades"][0]["signal_date"] == "2024-01-04"


def test_reference_does_not_apply_actions_or_mutate_snapshot(reference, config):
    reference["actions"] = [
        {"symbol": "A", "date": "2024-01-08", "type": "split", "ratio": 2},
        {"symbol": "A", "date": "2024-01-09", "type": "dividend", "amount": "1", "pay_date": "2024-01-10"},
    ]
    before = deepcopy(reference)
    result = run_small(reference, replace(config, mode=REFERENCE_MODE), Builtin("buy_hold"))
    assert result["curve"][-1]["positions"]["A"] == "95"
    assert result["metrics"]["final_equity"] == 1000
    assert all(row["receivables"] == "0" for row in result["curve"])
    assert "不含股息" in result["metadata"]["research_warning"]
    assert reference == before


@pytest.mark.parametrize("corruption,match", [
    ("missing", "缺少行情"), ("late", "晚于"), ("basis", "口径混合"),
    ("provider", "仅支持"), ("future", "仅支持"), ("inverse", "仅支持"),
    ("unrelated_block", "仅支持"),
])
def test_reference_does_not_bypass_unrelated_guards(reference, config, corruption, match):
    if corruption == "missing":
        reference["bars"].pop(2)
    elif corruption == "late":
        reference["bars"][0]["available_at"] = "2024-01-06T00:00:00+00:00"
    elif corruption == "basis":
        reference["bars"][1]["price_basis"] = "unadjusted"
    elif corruption == "provider":
        reference["source"]["provider"] = "unknown"
    elif corruption == "future":
        reference["securities"]["A"]["kind"] = "future"
    elif corruption == "inverse":
        reference["securities"]["A"]["inverse"] = True
    else:
        reference["securities"]["A"]["execution_blocked"] = "其他未支持的交易制度"
    with pytest.raises(ValueError, match=match):
        run_small(reference, replace(config, mode=REFERENCE_MODE), Builtin("buy_hold"))


def test_reference_mode_propagates_to_research_and_benchmark(reference, config):
    manifest = {k: v for k, v in reference.items() if k != "bars"}
    cfg = replace(config, mode=REFERENCE_MODE, benchmark="A")
    factory = lambda: Builtin("buy_hold")
    roll = rolling(manifest, reference["bars"], cfg, factory, {}, "day", 3)
    split = holdout(manifest, reference["bars"], cfg, factory, {}, test_start="2024-01-09")
    benchmark = benchmark_result(manifest, reference["bars"], cfg)
    for result in (roll, split, benchmark):
        assert result["metadata"]["reference_only"]
        assert not result["metadata"]["normal_ranking_eligible"]
    for sample in roll["samples"]:
        if sample["status"] == "completed":
            assert sample["result"]["metadata"]["price_model"] == REFERENCE_MODE
    assert split["holdout"]["result"]["curve"][0]["positions"] == {}


def test_reference_api_archive_and_reproduction(tmp_path, reference, config):
    app = create_app(tmp_path / "data")
    store = app.state.store
    snapshot = store.ingest(**reference)
    run_id = create_run(store, {"snapshot": snapshot, "config": asdict(replace(config, mode=REFERENCE_MODE)),
                                "reference_retry_of": "old-run"})
    execute_run(store, run_id)
    with TestClient(app) as client:
        dataset = client.get("/api/datasets").json()[0]
        detail = client.get(f"/api/runs/{run_id}").json()
        assert dataset["reference_research_available"]
        assert detail["reference_research_available"]
        assert detail["result"]["metadata"]["reference_only"]
        assert detail["request"]["config"]["mode"] == REFERENCE_MODE
        assert detail["request"]["reference_retry_of"] == "old-run"
    assert reproduce(store, run_id)["identical"]
