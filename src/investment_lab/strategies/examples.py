from datetime import date

from investment_lab.common import dec

NAMES = {"buy_hold": "买入持有", "dca": "定期投入", "monthly_equal_weight": "月度定投与动态等权", "moving_average": "均线", "drawdown_buy": "回撤买入", "rotation": "同市场轮动", "leverage_rebalance": "杠杆再平衡", "futures_roll": "月份合约换月", "cash": "持有现金"}


def _selected_symbols(ctx):
    symbols = tuple(ctx.symbols)
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("月度动态等权策略需要至少一项且不能重复的标的")
    return symbols


def _weight_snapshot(ctx, symbols):
    equity = dec(ctx.equity)
    if equity <= 0:
        return None
    prices = {}
    for symbol in symbols:
        values = ctx.history(symbol, 1, field="close")
        if not values or values[-1] is None or dec(values[-1]) <= 0:
            return None
        prices[symbol] = dec(values[-1])
    return {
        symbol: dec(ctx.positions.get(symbol, 0)) * prices[symbol] / equity
        for symbol in symbols
    }


def _pending_symbols(ctx):
    return {
        item.get("symbol")
        for item in ctx.pending
        if isinstance(item, dict) and item.get("symbol") is not None
    }


def _target_if_clear(ctx, symbol, target, reason, pending):
    if symbol not in pending:
        ctx.order_target_weight(symbol, target, reason)


def _buy_underweights(ctx, symbols, reason):
    if dec(ctx.cash) <= 0:
        return
    weights = _weight_snapshot(ctx, symbols)
    if weights is None:
        return
    target = dec(1) / dec(len(symbols))
    pending = _pending_symbols(ctx)
    gaps = sorted(
        ((target - weights[symbol], symbol) for symbol in symbols if weights[symbol] < target),
        reverse=True,
    )
    for gap, symbol in gaps:
        if gap > 0:
            _target_if_clear(ctx, symbol, target, reason, pending)


def _restore_equal_weight(ctx, symbols, reason):
    weights = _weight_snapshot(ctx, symbols)
    if weights is None:
        return
    target = dec(1) / dec(len(symbols))
    pending = _pending_symbols(ctx)
    overweight = sorted(
        ((weights[symbol] - target, symbol) for symbol in symbols if weights[symbol] > target),
        reverse=True,
    )
    underweight = sorted(
        ((target - weights[symbol], symbol) for symbol in symbols if weights[symbol] < target),
        reverse=True,
    )
    for _, symbol in overweight:
        _target_if_clear(ctx, symbol, target, reason, pending)
    for _, symbol in underweight:
        _target_if_clear(ctx, symbol, target, reason, pending)


def _initialize_monthly_equal_weight(ctx):
    _selected_symbols(ctx)
    mode = str(ctx.params.get("portfolio_mode", "rebalance"))
    if mode not in ("rebalance", "no_rebalance"):
        raise ValueError("portfolio_mode 只能是 rebalance 或 no_rebalance")
    raw_dates = ctx.params.get("month_end_dates", [])
    if raw_dates is None:
        raw_dates = []
    if not isinstance(raw_dates, (list, tuple)):
        raise ValueError("month_end_dates 必须是 YYYY-MM-DD 字符串数组")
    try:
        review_dates = tuple(date.fromisoformat(str(value)).isoformat() for value in raw_dates)
    except ValueError as exc:
        raise ValueError("month_end_dates 必须包含有效的 YYYY-MM-DD 日期") from exc
    ctx.state.update({
        "portfolio_mode": mode,
        "month_end_dates": review_dates,
        "last_month": None,
        "session_count": 0,
        "rebalance_count": 0,
    })


def _monthly_equal_weight_session(ctx):
    symbols = _selected_symbols(ctx)
    state = ctx.state
    month = ctx.date[:7]
    previous_month = state.get("last_month")
    first_session = state.get("session_count", 0) == 0
    new_month = previous_month is None or month != previous_month
    explicit_dates = set(state.get("month_end_dates", ()))
    review_due = (
        ctx.date in explicit_dates
        if explicit_dates
        else (new_month and previous_month is not None)
    )

    if first_session:
        _buy_underweights(ctx, symbols, "首次按所选标的数量等权配置")
    elif state["portfolio_mode"] == "rebalance" and review_due:
        weights = _weight_snapshot(ctx, symbols)
        target = dec(1) / dec(len(symbols))
        needs_rebalance = weights is not None and any(
            abs(weights[symbol] - target) >= dec("0.05") for symbol in symbols
        )
        if needs_rebalance:
            reason = (
                "指定日期检查，偏离至少5个百分点，恢复等权"
                if explicit_dates
                else "下月首个交易日代理月末检查，偏离至少5个百分点，恢复等权"
            )
            _restore_equal_weight(ctx, symbols, reason)
            state["rebalance_count"] = state.get("rebalance_count", 0) + 1
        elif new_month:
            _buy_underweights(ctx, symbols, "新增资金优先补足低于目标比例的标的")
    elif new_month:
        _buy_underweights(ctx, symbols, "新增资金优先补足低于目标比例的标的")

    state["last_month"] = month
    state["session_count"] = state.get("session_count", 0) + 1


class Builtin:
    def __init__(self, name):
        if name not in NAMES:
            raise ValueError("未知内置策略")
        self.name = name

    def on_session(self, ctx):
        p, state = ctx.params, ctx.state
        symbol = ctx.symbols[0]
        weight = dec(p.get("weight", "0.95"))
        if self.name == "monthly_equal_weight":
            _monthly_equal_weight_session(ctx)
        elif self.name == "buy_hold":
            if not state.get("ordered"):
                ctx.order_target_weight(symbol, weight, "初始配置后持有")
                state["ordered"] = True
        elif self.name == "dca":
            month = ctx.date[:7]
            if state.get("month") != month:
                ctx.order_value(symbol, min(ctx.cash, dec(p.get("amount", 1000))), "每月首次交易日投入")
                state["month"] = month
        elif self.name == "moving_average":
            window = int(p.get("window", 20))
            history = ctx.history(symbol, window)
            if len(history) == window:
                target = weight if history[-1] > sum(history) / window else 0
                ctx.order_target_weight(symbol, target, "已知收盘价与历史均线比较")
        elif self.name == "drawdown_buy":
            history = ctx.history(symbol, int(p.get("window", 60)))
            if history and history[-1] / max(history) - 1 <= -dec(p.get("threshold", "0.1")):
                ctx.order_target_weight(symbol, weight, "历史高点回撤达到阈值")
        elif self.name == "rotation":
            window = int(p.get("window", 20))
            scores = {}
            for candidate in ctx.symbols:
                prices = ctx.history(candidate, window)
                if len(prices) == window:
                    scores[candidate] = prices[-1] / prices[0] - 1
            if scores and state.get("month") != ctx.date[:7]:
                winner = max(scores, key=scores.get)
                for candidate in ctx.symbols:
                    if candidate != winner:
                        ctx.order_target_weight(candidate, 0, "月度轮动退出")
                ctx.order_target_weight(winner, weight, "月度历史动量轮动")
                state["month"] = ctx.date[:7]
        elif self.name == "leverage_rebalance":
            ctx.order_target_weight(symbol, dec(p.get("weight", "1.5")), "每日恢复目标杠杆，包含交易成本")
        elif self.name == "futures_roll":
            # Explicit calendar schedule; no future volume is inspected.
            schedule = p.get("roll_schedule", {})
            eligible = [d for d in schedule if d <= ctx.date]
            if eligible:
                desired = schedule[max(eligible)]
                if desired != state.get("contract"):
                    for candidate, quantity in ctx.positions.items():
                        if quantity:
                            ctx.order_shares(candidate, -quantity, "预先固定日期平多换月")
                    ctx.order_shares(desired, int(p.get("contracts", 1)), "预先固定日期开下一月份多头")
                    state["contract"] = desired

    def initialize(self, ctx):
        if self.name == "monthly_equal_weight":
            _initialize_monthly_equal_weight(ctx)
