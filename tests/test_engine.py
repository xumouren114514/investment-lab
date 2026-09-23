from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import pytest

from conftest import run_small
from investment_lab.strategies.examples import Builtin


def once(quantity):
    def session(ctx):
        if not ctx.state.get("sent"):
            ctx.order_shares("A", quantity, "手算")
            ctx.state["sent"] = True
    return SimpleNamespace(on_session=session)


def test_timing_and_cash_conservation(small, config):
    result = run_small(small, config, once(50))
    assert result["trades"][0]["date"] == "2024-01-05"
    assert result["trades"][0]["signal_date"] == "2024-01-04"
    assert result["curve"][-1]["cash"] == "500.00"
    assert result["metrics"]["final_equity"] == 1000
    assert result["metrics"]["total_return"] == 0


def test_future_price_not_visible_and_quantity_frozen(small, config):
    observations = []
    def session(ctx):
        assert all(row["date"] <= ctx.date for row in ctx.bars("A", 99))
        observations.append((ctx.date, ctx.history("A", 99)))
        if not ctx.state.get("sent"):
            ctx.order_value("A", 1000)
            ctx.state["sent"] = True
    small["bars"][1].update(open="20", close="20", high="21", low="19", vwap_value="20")
    result = run_small(small, config, SimpleNamespace(on_session=session))
    assert result["orders"][0]["quantity"] == "100"
    assert result["trades"][0]["quantity"] == "50"  # affordability checked only at execution
    assert observations[0][1] == [Decimal(10)]


def test_strategy_bar_views_cannot_mutate_shared_history(small, config):
    observations = []

    def session(ctx):
        if ctx.date == small["sessions"][1]:
            exposed = ctx.bars("A", 1)
            exposed[0]["close"] = "999"
            observations.append(ctx.history("A", 1)[-1])

    run_small(small, config, SimpleNamespace(on_session=session))
    assert observations == [Decimal("10")]


def test_progress_reports_completed_sessions_in_batches(small, config):
    from datetime import date, timedelta
    from investment_lab.engine.core import simulate

    days = [(date(2024, 1, 4) + timedelta(days=i)).isoformat() for i in range(25)]
    manifest = {**{k: v for k, v in small.items() if k != "bars"}, "sessions": days}
    bars = [{**small["bars"][0], "date": day, "available_at": day + "T22:00:00+00:00"} for day in days]
    long_config = replace(config, start=days[0], end=days[-1])
    progress = []

    simulate(manifest, bars, long_config, SimpleNamespace(on_session=lambda ctx: None), progress=lambda done, total: progress.append((done, total)))

    assert progress == [(10, 25), (20, 25), (25, 25)]


def shanghai_star_sample(small):
    symbol = "sh.688001"
    security = small["securities"].pop("A")
    security.update(market="CN", currency="CNY", exchange="XSHG", timezone="Asia/Shanghai", universe_id="CN:688001",
                    lot=100, lot_verified=False, minimum_buy_quantity=200,
                    minimum_sell_quantity=200, sell_delay=0,
                    execution_blocked="科创板买入最低数量和增量规则待实现")
    small["securities"][symbol] = security
    for bar in small["bars"]:
        bar["symbol"] = symbol
        bar["available_at"] = bar["date"] + "T10:00:00+08:00"
    return small, symbol


def test_star_board_buy_minimum_and_single_share_increments(small, config):
    payload, symbol = shanghai_star_sample(small)
    config = replace(config, initial_cash="10000", symbols=[symbol])

    def order(quantity):
        def session(ctx):
            if not ctx.state.get("sent"):
                ctx.order_shares(symbol, quantity)
                ctx.state["sent"] = True
        return SimpleNamespace(on_session=session)

    results = {quantity: run_small(payload, config, order(quantity)) for quantity in (199, 200, 201)}
    assert [t["quantity"] for t in results[199]["trades"]] == []
    assert results[199]["curve"][-1]["positions"].get(symbol, "0") == "0"
    assert any(e["type"] == "order_unfilled" and "买入订单低于最低申报数量 200 股" in e["reason"] for e in results[199]["ledger"])
    assert [t["quantity"] for t in results[200]["trades"]] == ["200"]
    assert [t["quantity"] for t in results[201]["trades"]] == ["201"]


def test_star_board_cash_shortfall_cannot_create_subminimum_buy_fill(small, config):
    payload, symbol = shanghai_star_sample(small)
    config = replace(config, symbols=[symbol])

    def order(ctx):
        if not ctx.state.get("sent"):
            ctx.order_shares(symbol, 201)
            ctx.state["sent"] = True
    strategy = SimpleNamespace(on_session=order)

    cash_limited = run_small(payload, replace(config, initial_cash="1990"), strategy)
    assert not cash_limited["trades"]
    assert any(e["type"] == "order_unfilled" and "现金不足" in e["reason"] for e in cash_limited["ledger"])


def test_star_board_volume_limit_can_partially_fill_a_legal_order(small, config):
    payload, symbol = shanghai_star_sample(small)
    config = replace(config, symbols=[symbol], initial_cash="10000", max_participation="0.00199", order_lifetime=2)

    def order(ctx):
        if not ctx.state.get("sent"):
            ctx.order_shares(symbol, 201)
            ctx.state["sent"] = True

    result = run_small(payload, config, SimpleNamespace(on_session=order))
    assert [t["quantity"] for t in result["trades"]] == ["199", "2"]
    assert result["curve"][-1]["positions"][symbol] == "201"


def test_star_board_allows_single_order_sale_of_odd_lot_holding(small, config):
    payload, symbol = shanghai_star_sample(small)
    config = replace(config, symbols=[symbol], max_participation="0.0015", initial_cash="10000")

    def session(ctx):
        if not ctx.state.get("buy_sent"):
            ctx.order_shares(symbol, 200)
            ctx.state["buy_sent"] = True
        elif ctx.positions.get(symbol) == 150 and not ctx.state.get("sell_sent"):
            ctx.order_shares(symbol, -150)
            ctx.state["sell_sent"] = True

    result = run_small(payload, config, SimpleNamespace(on_session=session))
    assert [t["quantity"] for t in result["trades"]] == ["150", "-150"]
    assert result["curve"][-1]["positions"].get(symbol, "0") == "0"


@pytest.mark.parametrize("liquidate", [False, True])
def test_star_board_partial_odd_sale_rejected_but_full_odd_position_allowed(small, config, liquidate):
    payload, symbol = shanghai_star_sample(small)
    config = replace(config, symbols=[symbol])
    submitted_sell = False

    def session(ctx):
        nonlocal submitted_sell
        if not ctx.state.get("buy_sent"):
            ctx.order_shares(symbol, 201)
            ctx.state["buy_sent"] = True
        elif ctx.positions.get(symbol) == 201 and not submitted_sell:
            submitted_sell = True
            ctx.order_shares(symbol, -201 if liquidate else -100)

    result = run_small(payload, replace(config, initial_cash="10000"), SimpleNamespace(on_session=session))
    if liquidate:
        assert [t["quantity"] for t in result["trades"]] == ["201", "-201"]
        assert result["curve"][-1]["positions"][symbol] == "0"
    else:
        assert [t["quantity"] for t in result["trades"]] == ["201"]
        assert any(e["type"] == "order_unfilled" and "一次性卖出全部剩余持仓" in e["reason"] for e in result["ledger"])
        assert result["curve"][-1]["positions"][symbol] == "201"


def test_star_board_recognizes_only_its_own_legacy_acquisition_block(small, config):
    payload, symbol = shanghai_star_sample(small)
    config = replace(config, symbols=[symbol])

    def session(ctx):
        if not ctx.state.get("sent"):
            ctx.order_shares(symbol, 200)
            ctx.state["sent"] = True
    strategy = SimpleNamespace(on_session=session)
    assert run_small(payload, replace(config, initial_cash="10000"), strategy)["trades"][0]["quantity"] == "200"

    payload["securities"][symbol]["execution_blocked"] = "公司行动尚未验证"
    with pytest.raises(ValueError, match="公司行动尚未验证"):
        run_small(payload, config, strategy)


def test_weekend_interest_and_rate_curve(small, config):
    config = replace(config, max_leverage="2", annual_interest="0.0365", rate_curve={"2024-01-07": "0.073"})
    result = run_small(small, config, once(200))
    monday = next(r for r in result["curve"] if r["date"] == "2024-01-08")
    assert monday["interest"] == "0.40"  # Fri .10 + Sat .10 + Sun .20
    assert monday["debt"] == "1000.40"


def test_split_dividend_receivable_no_double_count(small, config):
    small["actions"] = [{"symbol": "A", "date": "2024-01-08", "type": "split", "ratio": 2},
                        {"symbol": "A", "date": "2024-01-09", "type": "dividend", "amount": ".5", "pay_date": "2024-01-10"}]
    for b in small["bars"][2:]:
        price = "5" if b["date"] == "2024-01-08" else "4.5"
        b.update(open=price, close=price, high="6", low="4", vwap_value=price)
    result = run_small(small, config, once(100))
    assert result["curve"][2]["positions"]["A"] == "200"
    assert result["curve"][3]["receivables"] == "100.00"
    assert result["curve"][-1]["cash"] == "100.00"
    assert result["metrics"]["final_equity"] == 1000


def test_strict_missing_and_zero_volume_fail(small, config):
    small["bars"][1]["vwap_value"] = None
    with pytest.raises(ValueError, match="严格 VWAP"):
        run_small(small, config, once(10))


def test_missing_session_is_not_suspension(small, config):
    small["bars"].pop(2)
    with pytest.raises(ValueError, match="缺少行情"):
        run_small(small, config, once(10))


def test_participation_shared_across_orders_and_expiry(small, config):
    config = replace(config, max_participation="0.0001", order_lifetime=2)
    def session(ctx):
        if not ctx.state.get("sent"):
            ctx.order_shares("A", 20)
            ctx.order_shares("A", 20)
            ctx.state["sent"] = True
    result = run_small(small, config, SimpleNamespace(on_session=session))
    assert sum(Decimal(t["quantity"]) for t in result["trades"]) == 20
    assert any(e["type"] == "order_expired" for e in result["ledger"])


def test_suspension_and_price_limits(small, config):
    small["bars"][1]["status"] = "suspended"
    result = run_small(small, replace(config, order_lifetime=2), once(50))
    assert result["trades"][0]["date"] == "2024-01-08"
    small["bars"][2]["limit_up"] = "11"
    result = run_small(small, replace(config, order_lifetime=2), once(50))
    assert not result["trades"]


def test_long_only_and_market_boundary(small, config):
    with pytest.raises(ValueError, match="净空头"):
        run_small(small, config, once(-1))
    small["securities"]["B"] = {**small["securities"]["A"], "market": "HK", "currency": "HKD"}
    with pytest.raises(ValueError, match="一种币种"):
        run_small(small, replace(config, symbols=["A", "B"]), once(1))


def test_cash_flow_not_profit(small, config):
    result = run_small(small, replace(config, cash_flows={"2024-01-08": "1000"}), SimpleNamespace(on_session=lambda ctx: None))
    assert result["metrics"]["final_equity"] == 2000
    assert result["metrics"]["net_profit"] == 0
    assert result["metrics"]["total_return"] == 0


def test_forced_margin_liquidation_next_day(small, config):
    small["bars"][2].update(open="6", high="7", low="5", close="6", vwap_value="6")
    result = run_small(small, replace(config, max_leverage="2", annual_interest="0", maintenance_equity_ratio="0.25"), once(200))
    assert any(e["type"] == "margin_call" and e["date"] == "2024-01-08" for e in result["ledger"])
    assert next(t for t in result["trades"] if t["forced"])["date"] == "2024-01-09"


def test_futures_variation_margin_and_expiry(small, config):
    small["securities"]["A"].update(kind="future", multiplier="10", tick="0.1", expiry="2024-01-10", initial_margin=".1", maintenance_margin=".05")
    for i, b in enumerate(small["bars"]):
        price = 10 if i < 2 else 11
        b.update(open=str(price), close=str(price), high=str(price+1), low=str(price-1), vwap_value=str(price), settlement=str(price))
    result = run_small(small, replace(config, max_leverage="3"), once(10))
    assert result["curve"][1]["cash"] == "1000.00"  # no purchase of full notional
    assert result["curve"][1]["margin"] == "100.0"
    assert result["curve"][2]["cash"] == "1100.00"
    assert result["metrics"]["final_equity"] == 1100
    assert result["curve"][-1]["positions"]["A"] == "0"


def test_fee_effective_date_and_minimum(small, config):
    result = run_small(small, replace(config, rules=[{"effective": "2024-01-05", "minimum_commission": "5"}]), once(50))
    assert result["trades"][0]["fee"] == "5.00"
    assert result["metrics"]["final_equity"] == 995


def test_listing_warmup_and_late_data(small, config):
    with pytest.raises(ValueError, match="预热窗口"):
        run_small(small, replace(config, warmup=10), once(10))
    small["bars"][0]["available_at"] = "2024-01-06T00:00:00+00:00"
    with pytest.raises(ValueError, match="晚于"):
        run_small(small, config, once(10))
