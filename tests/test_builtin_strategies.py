from copy import deepcopy
from decimal import Decimal

import pytest

from investment_lab.strategies.examples import (
    Builtin,
    CATALOG,
    NAMES,
    STRATEGY_CATALOG,
    validate_params,
)


def bar(day, close, high=None, low=None):
    close = float(close)
    return {
        "symbol": "A",
        "date": day,
        "close": str(close),
        "high": str(high if high is not None else close + 1),
        "low": str(low if low is not None else close - 1),
    }


class Context:
    def __init__(self, rows, params=None, symbols=("A",), day=None, default_field="close"):
        self.rows = {symbol: sorted((deepcopy(row) for row in rows if row["symbol"] == symbol), key=lambda x: x["date"])
                     for symbol in symbols}
        self.symbols = list(symbols)
        self.date = day or max(row["date"] for row in rows)
        self.params = params or {}
        self.state = {}
        self.positions = {symbol: 0 for symbol in symbols}
        self.pending = []
        self.cash = 100_000
        self.equity = 100_000
        self.orders = []
        self.diagnostics = []
        self._default_field = default_field

    def history(self, symbol, count=20, field=None):
        known = [row for row in self.rows.get(symbol, ()) if row["date"] <= self.date]
        field = field or self._default_field
        return [Decimal(str(row[field])) if row.get(field) is not None else None for row in known[-count:]]

    def bars(self, symbol, count=20):
        known = [row for row in self.rows.get(symbol, ()) if row["date"] <= self.date]
        return [dict(row) for row in known[-count:]]

    def order_target_weight(self, symbol, weight, reason):
        self.orders.append(("weight", symbol, float(weight), reason))

    def order_value(self, symbol, value, reason):
        self.orders.append(("value", symbol, float(value), reason))

    def order_shares(self, symbol, quantity, reason):
        self.orders.append(("shares", symbol, quantity, reason))

    def note(self, code, message, **details):
        self.diagnostics.append({"date": self.date, "code": code, "message": message, **details})


def run(name, rows, params, day=None, symbols=("A",), default_field="close"):
    ctx = Context(rows, params, symbols, day, default_field)
    strategy = Builtin(name)
    strategy.initialize(ctx)
    strategy.on_session(ctx)
    return ctx


def days(prices):
    return [bar(f"2024-01-{index:02d}", price) for index, price in enumerate(prices, 1)]


def test_catalog_keeps_all_existing_ids_and_defines_22_complete_builtins():
    entries = CATALOG["strategies"]
    ids = [item["id"] for item in entries]
    assert len(ids) == 22 and len(set(ids)) == 22
    assert set(ids) == set(NAMES)
    assert {"buy_hold", "dca", "monthly_equal_weight", "rotation", "futures_roll", "cash"} <= set(ids)
    assert next(item for item in entries if item["id"] == "atr_trend_stop")["parameters"][-2]["key"] == "risk_fraction"


def test_defaults_and_parameter_ranges_are_validated_before_execution():
    for entry in STRATEGY_CATALOG:
        defaults = {field["key"]: field.get("default") for field in entry["parameters"] if "default" in field}
        assert validate_params(entry["id"], defaults, ("A", "B")) is not None
    with pytest.raises(ValueError, match="不能小于"):
        validate_params("ema_crossover", {"fast_window": 1, "slow_window": 26})
    with pytest.raises(ValueError, match="快线窗口"):
        validate_params("macd", {"fast_window": 26, "slow_window": 26})
    with pytest.raises(ValueError, match="退出窗口"):
        validate_params("donchian_breakout", {"entry_window": 10, "exit_window": 11})
    with pytest.raises(ValueError, match="入场线"):
        validate_params("rsi_mean_reversion", {"entry": 45, "exit": 40})
    with pytest.raises(ValueError, match="比例总和"):
        validate_params("target_allocation", {"weights": {"A": 0.7, "B": 0.4}}, ("A", "B"))
    with pytest.raises(ValueError, match="未选择"):
        validate_params("target_allocation", {"weights": {"C": 0.2}}, ("A", "B"))
    with pytest.raises(ValueError, match="日期必须"):
        validate_params("futures_roll", {"roll_schedule": {"20240102": "IF2606"}})


@pytest.mark.parametrize(
    ("name", "prices", "params"),
    [
        ("ema_crossover", [10, 9, 8, 7, 6, 5, 4, 3, 2, 5], {"fast_window": 2, "slow_window": 3}),
        ("macd", [10, 9, 8, 7, 6, 5, 4, 3, 2, 4], {"fast_window": 2, "slow_window": 3, "signal_window": 2}),
        ("rsi_mean_reversion", [100, 100, 99, 98], {"window": 2, "entry": 30, "exit": 55}),
        ("bollinger_mean_reversion", [10, 10, 10, 7], {"window": 3, "deviations": 0.5}),
        ("donchian_breakout", [10, 10, 12], {"entry_window": 2, "exit_window": 2}),
        ("drawdown_buy", [100, 100, 80], {"window": 3, "threshold": 0.1}),
        ("drawdown_ladder", [100, 100, 85], {"window": 3, "first_drawdown": 0.1, "step": 0.05, "amount": 100, "max_steps": 4}),
        ("volatility_target", [10, 11, 9, 12], {"window": 2, "target_volatility": 0.15, "max_weight": 0.8}),
    ],
)
def test_representative_indicator_strategies_emit_signals(name, prices, params):
    ctx = run(name, days(prices), params)
    assert ctx.orders, name


def test_dca_and_adaptive_dca_control_purchase_timing_and_size():
    rows = days([100, 100, 80])
    dca = run("dca", rows, {"amount": 250, "frequency": "monthly"})
    assert dca.orders[0][:3] == ("value", "A", 250.0)
    adaptive = run("adaptive_dca", rows, {"amount": 250, "frequency": "monthly", "window": 3,
                                            "drawdown_threshold": 0.1, "multiplier": 2})
    assert adaptive.orders[0][:3] == ("value", "A", 500.0)


def test_portfolio_weight_inputs_and_rebalance_frequency_are_applied():
    rows = days([10, 10, 10]) + [dict(item, symbol="B") for item in days([20, 20, 20])]
    target = run("target_allocation", rows, {"weights": {"A": 0.6, "B": 0.4},
                                             "frequency": "monthly", "rebalance_threshold": 0.05},
                 symbols=("A", "B"))
    assert {(item[1], item[2]) for item in target.orders} == {("A", 0.6), ("B", 0.4)}
    inverse = run("inverse_volatility", rows, {"window": 2, "frequency": "monthly", "max_weight": 0.5},
                  symbols=("A", "B"))
    assert len(inverse.orders) == 2
    assert all(order[2] <= 0.5 for order in inverse.orders)


def test_atr_position_size_uses_risk_budget_and_close_stop():
    rows = [bar(f"2024-01-{i:02d}", close, high=high, low=low)
            for i, (close, high, low) in enumerate([(10, 11, 9), (11, 12, 10), (12, 13, 11), (13, 14, 12)], 1)]
    ctx = run("atr_trend_stop", rows, {"trend_window": 2, "atr_window": 2, "atr_multiplier": 2,
                                        "risk_fraction": 0.01, "max_weight": 0.95})
    assert ctx.orders and ctx.orders[0][0] == "weight"
    assert 0 < ctx.orders[0][2] < 0.95
    assert "风险预算" in ctx.orders[0][3]


def test_insufficient_warmup_and_missing_ohlc_skip_indicator_signals():
    assert not run("time_series_momentum", days([100, 80]), {"window": 3, "threshold": 0.1}).orders
    incomplete = days([10, 10, 12])
    incomplete[-1]["high"] = None
    assert not run("donchian_breakout", incomplete, {"entry_window": 2, "exit_window": 2}).orders
    assert not run("stochastic_mean_reversion", incomplete, {"window": 2, "entry": 20, "exit": 50}).orders
    assert not run("atr_trend_stop", incomplete, {"trend_window": 2, "atr_window": 2}).orders


def test_indicators_only_see_rows_available_on_the_decision_day():
    known = days([100, 90, 80])
    future = known + [bar("2024-01-04", 1), bar("2024-01-05", 500)]
    first = run("time_series_momentum", known, {"window": 2, "threshold": 0, "weight": 0.95})
    second = run("time_series_momentum", future, {"window": 2, "threshold": 0, "weight": 0.95}, day="2024-01-03")
    assert first.orders == second.orders
    assert second.orders and second.orders[0][2] == 0


@pytest.mark.parametrize(("prices", "target"), [
    # Fast remains above slow: (12,10) -> (28/3,9), no cross.
    ([8, 8, 14, 8], None),
    # Fast remains below slow: (8,10) -> (32/3,11), no cross.
    ([12, 12, 6, 12], None),
    ([12, 12, 6, 20], 0.95),  # (8,10) -> (16,15), crosses up.
    ([8, 8, 14, 4], 0),  # (12,10) -> (20/3,7), crosses down.
])
def test_ema_cross_requires_both_current_lines(prices, target):
    ctx = run("ema_crossover", days(prices), {"fast_window": 2, "slow_window": 3})
    if target is None:
        assert not ctx.orders
    else:
        assert [order[:3] for order in ctx.orders] == [("weight", "A", target)]


@pytest.mark.parametrize(("prices", "target"), [
    # Independent exact recurrence, fast=2, slow=3, signal=2:
    # previous (MACD,signal)=(-97/108,-58/81), current=(157/648,-25/324).
    ([12, 12, 6, 12, 12, 6, 12], 0.95),
    # Reflecting prices about 12 negates both lines: a true downward cross.
    ([12, 12, 18, 12, 12, 18, 12], 0),
    # Previous=(1/6,1/9), current=(11/36,13/54); MACD remains above.
    ([10, 10, 10, 10, 10, 11, 12], None),
    # The reflected series remains below, with no crossover.
    ([10, 10, 10, 10, 10, 9, 8], None),
])
def test_macd_current_line_crossing_with_independent_numeric_examples(prices, target):
    ctx = run("macd", days(prices), {"fast_window": 2, "slow_window": 3, "signal_window": 2})
    if target is None:
        assert not ctx.orders
    else:
        assert [order[:3] for order in ctx.orders] == [("weight", "A", target)]


def test_yearly_dca_buys_once_per_calendar_year():
    params = {"amount": 250, "frequency": "yearly"}
    validate_params("dca", params)
    ctx = Context(days([10]), params)
    strategy = Builtin("dca")
    strategy.initialize(ctx)
    for day in ("2024-01-02", "2024-09-30", "2025-01-02"):
        ctx.date = day
        strategy.on_session(ctx)
    assert [order[:3] for order in ctx.orders] == [("value", "A", 250), ("value", "A", 250)]


def test_drawdown_preserves_partial_window_and_default_price_basis():
    rows = [dict(row, vwap_value=price) for row, price in zip(days([100, 110]), [100, 80])]
    vwap = run("drawdown_buy", rows, {}, default_field="vwap_value")
    close = run("drawdown_buy", rows, {})
    assert [order[:3] for order in vwap.orders] == [("weight", "A", 0.95)]
    assert not close.orders
    assert vwap.diagnostics[0]["code"] == "partial_history"
    assert vwap.diagnostics[0]["required_history"] == 60
    assert vwap.diagnostics[0]["available_history"] == 2


def test_rotation_preserves_default_price_basis_selected_ties_and_sell_first():
    rows = [dict(row, symbol=symbol, vwap_value=price)
            for symbol, closes, prices in (("B", [10, 30], [10, 11]), ("A", [10, 9], [10, 12]))
            for row, price in zip(days(closes), prices)]
    ctx = run("rotation", rows, {"window": 2}, symbols=("B", "A"), default_field="vwap_value")
    assert [order[:3] for order in ctx.orders] == [("weight", "B", 0), ("weight", "A", 0.95)]
    tied = [dict(row, vwap_value="10") for row in rows]
    ctx = run("rotation", tied, {"window": 2}, symbols=("B", "A"), default_field="vwap_value")
    assert [order[:3] for order in ctx.orders] == [("weight", "A", 0), ("weight", "B", 0.95)]


def test_diagnostics_describe_missing_history_and_fields_without_changing_prices():
    short = run("ema_crossover", days([10, 11]), {"fast_window": 2, "slow_window": 3})
    assert short.diagnostics == [{"date": "2024-01-02", "code": "insufficient_history",
        "message": "观察窗口不足，跳过指标信号", "strategy": "ema_crossover", "symbol": "A",
        "required_history": 4, "available_history": 2, "missing_fields": [], "action": "skip_signal"}]
    rows = days([10, 10, 12])
    rows[-1]["high"] = None
    missing = run("donchian_breakout", rows, {"entry_window": 2, "exit_window": 2})
    assert not missing.orders
    assert missing.diagnostics[0]["missing_fields"] == ["high"]
    assert missing.diagnostics[0]["required_history"] == 3
    rows = [dict(row, vwap_value="10") for row in days([100, 80])]
    rows[-1]["vwap_value"] = None
    missing = run("drawdown_buy", rows, {}, default_field="vwap_value")
    assert not missing.orders
    assert missing.diagnostics[-1]["missing_fields"] == ["vwap_value"]


def test_adaptive_dca_keeps_base_purchase_when_indicator_is_not_ready():
    ctx = run("adaptive_dca", days([100, 80]), {"amount": 250, "window": 3})
    assert ctx.orders[0][:3] == ("value", "A", 250)
    assert ctx.diagnostics[0]["action"] == "base_purchase"


def test_legacy_context_without_note_can_skip_indicators():
    ctx = Context(days([100]), {"fast_window": 2, "slow_window": 3})
    ctx.note = None
    strategy = Builtin("ema_crossover")
    strategy.initialize(ctx)
    strategy.on_session(ctx)
    assert not ctx.orders


def test_inverse_volatility_retries_after_warmup_in_same_period():
    rows = days([10, 11, 12]) + [dict(row, symbol="B") for row in days([20, 21, 22])]
    ctx = Context(rows, {"window": 2}, ("A", "B"), day="2024-01-02")
    strategy = Builtin("inverse_volatility")
    strategy.initialize(ctx)
    strategy.on_session(ctx)
    assert not ctx.orders and "last_period" not in ctx.state
    ctx.date = "2024-01-03"
    strategy.on_session(ctx)
    assert len(ctx.orders) == 2 and ctx.state["last_period"] == "2024-01"
    strategy.on_session(ctx)
    assert len(ctx.orders) == 2
