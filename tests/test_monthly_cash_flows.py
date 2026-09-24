from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from conftest import run_small
from investment_lab.common import read_json
from investment_lab.engine.cash_flows import resolve_cash_flows
from investment_lab.engine.core import simulate
from investment_lab.jobs import create_run, execute_run, reproduce
from investment_lab.research.experiments import benchmark_result, holdout, rolling
from investment_lab.strategies.examples import Builtin
from investment_lab.web.app import create_app


@pytest.fixture
def monthly_data(small):
    days = ["2024-01-04", "2024-01-05", "2024-02-01", "2024-02-02", "2024-03-01", "2024-03-04"]
    first = small["bars"][0]
    small["bars"] = [{**first, "date": day, "available_at": day + "T22:00:00+00:00"} for day in days]
    small["sessions"] = days
    return small


def test_first_available_session_partial_month_year_boundary_and_exact_cents():
    sessions = ["2023-12-29", "2024-01-02", "2024-01-15", "2024-02-01", "2024-02-29", "2024-03-04"]
    flows = {"monthly": "0.10", "2024-01-02": "0.20", "2024-02-01": "-0.05"}
    before = deepcopy(flows)
    assert resolve_cash_flows(flows, sessions, "2023-12-28", "2024-03-03") == {
        "2023-12-29": "0.10", "2024-01-02": "0.30", "2024-02-01": "0.05",
    }
    assert resolve_cash_flows({"monthly": 3000}, sessions, "2024-01-10", "2024-02-29") == {
        "2024-01-15": "3000.00", "2024-02-01": "3000.00",
    }
    assert flows == before


def test_manual_dates_remain_exact_and_non_sessions_are_rejected():
    sessions = ["2024-01-04", "2024-01-05"]
    assert resolve_cash_flows({"2024-01-05": -50}, sessions, sessions[0], sessions[-1]) == {"2024-01-05": "-50.00"}
    assert resolve_cash_flows({}, sessions, sessions[0], sessions[-1]) == {}
    with pytest.raises(ValueError, match="必须在本次交易日"):
        resolve_cash_flows({"monthly": 3000, "2024-01-06": 100}, sessions, sessions[0], "2024-01-07")


@pytest.mark.parametrize("value", [0, -100, True, None, {}, [], "abc", "NaN", "Infinity", "1e999", 0.001])
def test_invalid_monthly_amount_is_actionable(config, value):
    with pytest.raises(ValueError, match="monthly|金额"):
        replace(config, cash_flows={"monthly": value})


@pytest.mark.parametrize("flows", [[], None, {"weekly": 3000}, {"2024-02-30": 3}, {"monthly": 3000, "2024-01-04": {"amount": 3}}])
def test_invalid_schedule_is_rejected_before_task_creation(config, flows):
    with pytest.raises(ValueError, match="JSON|字段|金额"):
        replace(config, cash_flows=flows)


def test_monthly_deposits_are_not_profit_and_are_available_to_dca(monthly_data, config):
    cfg = replace(config, end="2024-03-04", cash_flows={"monthly": 3000})
    before = deepcopy(asdict(cfg))
    manifest = {k: v for k, v in monthly_data.items() if k != "bars"}
    result = simulate(manifest, monthly_data["bars"], cfg, Builtin("dca"), {"amount": 3000})
    assert result["metrics"]["net_flows"] == 9000
    assert result["metrics"]["final_equity"] == 10000
    assert result["metrics"]["net_profit"] == result["metrics"]["total_return"] == 0
    assert [t["date"] for t in result["trades"]] == ["2024-01-05", "2024-02-02", "2024-03-04"]
    assert all(Decimal(t["quantity"]) == 300 for t in result["trades"])
    assert result["metadata"]["cash_flow_schedule"] == {"monthly": 3000}
    assert list(result["metadata"]["cash_flows"]) == ["2024-01-04", "2024-02-01", "2024-03-01"]
    assert asdict(cfg) == before
    cash = run_small(monthly_data, cfg, Builtin("cash"))
    assert cash["metrics"]["trades"] == 0 and cash["metrics"]["net_profit"] == 0


def test_monthly_equal_weight_strategy_handles_multiple_symbols_and_monthly_deposits(monthly_data, config):
    payload = deepcopy(monthly_data)
    payload["securities"]["B"] = {**payload["securities"]["A"], "name": "第二项样本"}
    payload["bars"].extend({**bar, "symbol": "B"} for bar in monthly_data["bars"])
    cfg = replace(
        config,
        end="2024-03-04",
        symbols=["A", "B"],
        cash_flows={"monthly": 100},
    )
    manifest = {key: value for key, value in payload.items() if key != "bars"}
    result = simulate(manifest, payload["bars"], cfg, Builtin("monthly_equal_weight"), {
        "portfolio_mode": "rebalance",
        "month_end_dates": [],
    })

    assert {trade["symbol"] for trade in result["trades"]} == {"A", "B"}
    assert list(result["metadata"]["cash_flows"]) == ["2024-01-04", "2024-02-01", "2024-03-01"]
    assert result["metrics"]["net_flows"] == 300


def test_monthly_equal_weight_strategy_rebalances_on_explicit_review_date(monthly_data, config):
    payload = deepcopy(monthly_data)
    payload["securities"]["B"] = {**payload["securities"]["A"], "name": "第二项样本"}
    second_asset_bars = []
    for bar in monthly_data["bars"]:
        price = "20" if bar["date"] >= "2024-02-01" else "10"
        second_asset_bars.append({
            **bar,
            "symbol": "B",
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "vwap_value": price,
            "amount": str(Decimal(price) * Decimal(bar["volume"])),
        })
    payload["bars"].extend(second_asset_bars)
    cfg = replace(config, end="2024-03-04", symbols=["A", "B"], cash_flows={"monthly": 100})
    manifest = {key: value for key, value in payload.items() if key != "bars"}
    result = simulate(manifest, payload["bars"], cfg, Builtin("monthly_equal_weight"), {
        "portfolio_mode": "rebalance",
        "month_end_dates": ["2024-02-01"],
    })

    review_orders = [order for order in result["orders"] if order["signal_date"] == "2024-02-01"]
    assert len(review_orders) == 2
    assert all("指定日期检查" in order["reason"] for order in review_orders)
    assert {trade["symbol"] for trade in result["trades"] if trade["date"] == "2024-02-02"} == {"A", "B"}


def test_rolling_regenerates_months_instead_of_shifting_monthly_offsets(monthly_data, config):
    manifest = {k: v for k, v in monthly_data.items() if k != "bars"}
    cfg = replace(config, end="2024-03-04", cash_flows={"monthly": 100, "2024-01-05": 7})
    result = rolling(manifest, monthly_data["bars"], cfg, lambda: Builtin("cash"), {}, "day", 3)
    second = result["samples"][1]["result"]
    assert second["metadata"]["cash_flows"] == {"2024-01-05": "100.00", "2024-02-01": "107.00"}
    assert second["metrics"]["net_flows"] == 207
    assert result["metadata"]["monthly_deposit_rule"]


def test_holdout_and_benchmark_have_separate_matching_monthly_cash_flows(monthly_data, config):
    manifest = {k: v for k, v in monthly_data.items() if k != "bars"}
    cfg = replace(config, end="2024-03-04", benchmark="A", benchmark_strategy="cash", cash_flows={"monthly": 100})
    result = holdout(manifest, monthly_data["bars"], cfg, lambda: Builtin("cash"), {}, test_start="2024-03-01", gap_sessions=1)
    assert result["development"]["result"]["metadata"]["cash_flows"] == {"2024-01-04": "100.00", "2024-02-01": "100.00"}
    assert result["holdout"]["result"]["metadata"]["cash_flows"] == {"2024-03-01": "100.00"}
    for label in ("development", "holdout"):
        assert result[label]["result"]["metrics"]["net_flows"] == result[label]["benchmark"]["metrics"]["net_flows"]
    benchmark = benchmark_result(manifest, monthly_data["bars"], cfg)
    assert benchmark["metrics"]["net_flows"] == 300
    assert benchmark["metrics"]["net_profit"] == 0


def test_monthly_archive_api_preview_and_reproduction(tmp_path, monthly_data, config):
    app = create_app(tmp_path / "data")
    store = app.state.store
    snapshot = store.ingest(**monthly_data)
    cfg = replace(config, end="2024-03-04", cash_flows={"monthly": 100})
    run_id = create_run(store, {"snapshot": snapshot, "config": asdict(cfg), "strategy": "cash"})
    assert read_json(store.root / "runs" / run_id / "request.json")["config"]["cash_flows"] == {"monthly": 100}
    execute_run(store, run_id)
    with TestClient(app) as client:
        result = client.get(f"/api/runs/{run_id}").json()["result"]
        assert result["metadata"]["cash_flows"] == {"2024-01-04": "100.00", "2024-02-01": "100.00", "2024-03-01": "100.00"}
        bad = client.post("/api/runs", headers={"X-Lab-Request": "local-ui"}, json={"snapshot": snapshot, "config": {**asdict(cfg), "cash_flows": {"monthly": -1}}})
        assert bad.status_code == 400 and "monthly" in bad.json()["detail"]
    assert reproduce(store, run_id)["identical"]
