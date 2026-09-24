from datetime import date
import json
import math
from pathlib import Path
from statistics import pstdev

from investment_lab.common import dec


CATALOG = json.loads(Path(__file__).with_name("catalog.json").read_text(encoding="utf-8"))
STRATEGY_CATALOG = tuple(CATALOG["strategies"])
STRATEGY_BY_ID = {item["id"]: item for item in STRATEGY_CATALOG}
NAMES = {item["id"]: item["name"] for item in STRATEGY_CATALOG}


def _selected_symbols(ctx):
    symbols = tuple(ctx.symbols)
    if not symbols or len(set(symbols)) != len(symbols):
        raise ValueError("组合策略需要至少一项且不能重复的标的")
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


def _period_key(day, frequency):
    current = date.fromisoformat(str(day))
    if frequency == "weekly":
        week = current.isocalendar()
        return f"{week.year}-W{week.week:02d}"
    if frequency == "quarterly":
        return f"{current.year}-Q{(current.month - 1) // 3 + 1}"
    if frequency == "yearly":
        return str(current.year)
    return current.strftime("%Y-%m")


def _scheduled(ctx, state, key, frequency):
    period = _period_key(ctx.date, frequency)
    if state.get(key) == period:
        return False
    state[key] = period
    return True


def _initialize_monthly_equal_weight(ctx):
    symbols = _selected_symbols(ctx)
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
    threshold = dec(ctx.params.get("rebalance_threshold", "0.05"))
    if not 0 <= threshold <= 1:
        raise ValueError("rebalance_threshold 必须在0到1之间")
    ctx.state.update({
        "portfolio_mode": mode,
        "rebalance_threshold": threshold,
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
        threshold = state["rebalance_threshold"]
        needs_rebalance = weights is not None and any(
            abs(weights[symbol] - target) >= threshold for symbol in symbols
        )
        if needs_rebalance:
            reason = (
                f"指定日期检查，偏离至少{threshold * 100:g}个百分点，恢复等权"
                if explicit_dates
                else f"下月首个交易日代理月末检查，偏离至少{threshold * 100:g}个百分点，恢复等权"
            )
            _restore_equal_weight(ctx, symbols, reason)
            state["rebalance_count"] = state.get("rebalance_count", 0) + 1
        elif new_month:
            _buy_underweights(ctx, symbols, "新增资金优先补足低于目标比例的标的")
    elif new_month:
        _buy_underweights(ctx, symbols, "新增资金优先补足低于目标比例的标的")

    state["last_month"] = month
    state["session_count"] = state.get("session_count", 0) + 1


def validate_params(name, params, symbols=()):
    if not isinstance(params, dict):
        raise ValueError("策略参数必须是 JSON 对象")
    schema = STRATEGY_BY_ID[name]
    values = {item["key"]: params.get(item["key"], item.get("default")) for item in schema["parameters"]}
    for item in schema["parameters"]:
        key, kind, value = item["key"], item["type"], values[item["key"]]
        if kind in ("number", "integer"):
            try:
                numeric = dec(value)
            except (ValueError, TypeError, ArithmeticError) as exc:
                raise ValueError(f"策略参数 {key} 必须是有效数值") from exc
            if "min" in item and numeric < dec(item["min"]):
                raise ValueError(f"策略参数 {key} 不能小于 {item['min']}")
            if "max" in item and numeric > dec(item["max"]):
                raise ValueError(f"策略参数 {key} 不能大于 {item['max']}")
            if kind == "integer" and numeric != numeric.to_integral_value():
                raise ValueError(f"策略参数 {key} 必须是整数")
            values[key] = int(numeric) if kind == "integer" else numeric
        elif kind == "select":
            choices = {option["value"] for option in item["options"]}
            if value not in choices:
                raise ValueError(f"策略参数 {key} 必须为：{'、'.join(sorted(choices))}")
        elif kind == "date_list":
            if value is None:
                values[key] = []
            elif not isinstance(value, (list, tuple)):
                raise ValueError(f"策略参数 {key} 必须是日期数组")
            else:
                try:
                    parsed_dates = [date.fromisoformat(str(entry)) for entry in value]
                except ValueError as exc:
                    raise ValueError(f"策略参数 {key} 包含无效日期") from exc
                if any(str(entry) != parsed.isoformat() for entry, parsed in zip(value, parsed_dates)):
                    raise ValueError(f"策略参数 {key} 日期必须使用 YYYY-MM-DD")
                if len({parsed.isoformat() for parsed in parsed_dates}) != len(parsed_dates):
                    raise ValueError(f"策略参数 {key} 不能包含重复日期")
                values[key] = [parsed.isoformat() for parsed in parsed_dates]
        elif kind == "weights":
            if value is None:
                value = {}
            if not isinstance(value, dict):
                raise ValueError(f"策略参数 {key} 必须是标的与比例的映射")
            weights = {}
            for symbol, weight in value.items():
                if symbols and symbol not in symbols:
                    raise ValueError(f"目标比例中包含未选择的标的：{symbol}")
                amount = dec(weight)
                if amount < 0 or amount > 1:
                    raise ValueError(f"标的 {symbol} 的目标比例必须在0到1之间")
                weights[symbol] = amount
            if sum(weights.values(), dec(0)) > dec(item.get("maxTotal", 1)):
                raise ValueError("各标的目标比例总和不能超过100%")
            values[key] = weights
        elif kind == "json" and not isinstance(value, dict):
            raise ValueError(f"策略参数 {key} 必须是 JSON 对象")

    if name in ("ema_crossover", "macd") and values["fast_window"] >= values["slow_window"]:
        raise ValueError("快线窗口必须小于慢线窗口")
    if name == "donchian_breakout" and values["exit_window"] > values["entry_window"]:
        raise ValueError("唐奇安退出窗口不能大于入场窗口")
    if name in ("rsi_mean_reversion", "stochastic_mean_reversion") and values["entry"] >= values["exit"]:
        raise ValueError("入场线必须低于退出线")
    if name == "rotation" and symbols and values["top_n"] > len(symbols):
        raise ValueError("领先标的数量不能超过已选择的标的数量")
    if name == "drawdown_ladder" and values["step"] <= 0:
        raise ValueError("分层回撤间隔必须大于0")
    if name == "futures_roll":
        for day, contract in values["roll_schedule"].items():
            try:
                parsed = date.fromisoformat(str(day))
            except ValueError as exc:
                raise ValueError("roll_schedule 的日期必须使用 YYYY-MM-DD") from exc
            if str(day) != parsed.isoformat():
                raise ValueError("roll_schedule 的日期必须使用 YYYY-MM-DD")
            if not isinstance(contract, str) or not contract.strip():
                raise ValueError("roll_schedule 的合约代码不能为空")
    return values


def _closes(ctx, symbol, count):
    values = ctx.history(symbol, count, field="close")
    if len(values) != count or any(value is None or dec(value) <= 0 for value in values):
        return None
    return [float(value) for value in values]


def _bars(ctx, symbol, count):
    values = ctx.bars(symbol, count)
    if len(values) != count:
        return None
    for row in values:
        if any(row.get(field) is None for field in ("high", "low", "close")):
            return None
        if dec(row["high"]) <= 0 or dec(row["low"]) <= 0 or dec(row["close"]) <= 0:
            return None
    return values


def _ema(values, window):
    if len(values) < window:
        return None
    current = sum(values[:window]) / window
    alpha = 2 / (window + 1)
    for value in values[window:]:
        current += alpha * (value - current)
    return current


def _ema_series(values, window):
    result = [None] * len(values)
    if len(values) < window:
        return result
    current = sum(values[:window]) / window
    result[window - 1] = current
    alpha = 2 / (window + 1)
    for index in range(window, len(values)):
        current += alpha * (values[index] - current)
        result[index] = current
    return result


def _rsi(values, window):
    if len(values) < window + 1:
        return None
    changes = [values[index] - values[index - 1] for index in range(len(values) - window, len(values))]
    gains = sum(max(change, 0) for change in changes) / window
    losses = sum(max(-change, 0) for change in changes) / window
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - 100 / (1 + gains / losses)


def _atr(rows, window):
    if len(rows) < window + 1:
        return None
    ranges = []
    for index in range(len(rows) - window, len(rows)):
        high, low = float(dec(rows[index]["high"])), float(dec(rows[index]["low"]))
        previous_close = float(dec(rows[index - 1]["close"]))
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return sum(ranges) / window


def _annualized_volatility(closes, window):
    if closes is None or len(closes) < window + 1:
        return None
    returns = [closes[index] / closes[index - 1] - 1 for index in range(1, len(closes))]
    return pstdev(returns) * math.sqrt(252)


def _allocation_weights(ctx, symbols, weights):
    if not weights:
        equal = dec(1) / dec(len(symbols))
        return {symbol: equal for symbol in symbols}
    return {symbol: dec(weights.get(symbol, 0)) for symbol in symbols}


def _target_allocation(ctx, symbols, targets, threshold, reason):
    current = _weight_snapshot(ctx, symbols)
    if current is None:
        return
    pending = _pending_symbols(ctx)
    for symbol in symbols:
        difference = abs(current[symbol] - targets[symbol])
        if difference > 0 and difference >= threshold:
            _target_if_clear(ctx, symbol, targets[symbol], reason, pending)


class Builtin:
    def __init__(self, name):
        if name not in NAMES:
            raise ValueError("未知内置策略")
        self.name = name

    def initialize(self, ctx):
        validate_params(self.name, ctx.params, ctx.symbols)
        if self.name == "monthly_equal_weight":
            _initialize_monthly_equal_weight(ctx)

    def on_session(self, ctx):
        p, state = ctx.params, ctx.state
        symbol = ctx.symbols[0] if ctx.symbols else None
        weight = dec(p.get("weight", "0.95"))

        if self.name == "monthly_equal_weight":
            _monthly_equal_weight_session(ctx)
        elif self.name == "buy_hold":
            if not state.get("ordered") and symbol:
                ctx.order_target_weight(symbol, weight, "初始配置后持有")
                state["ordered"] = True
        elif self.name == "dca":
            frequency = p.get("frequency", "monthly")
            if symbol and _scheduled(ctx, state, "last_period", frequency):
                ctx.order_value(symbol, min(ctx.cash, dec(p.get("amount", 1000))), "周期首个交易日定额投入")
        elif self.name == "adaptive_dca":
            frequency = p.get("frequency", "monthly")
            if symbol and _scheduled(ctx, state, "last_period", frequency):
                amount = dec(p.get("amount", 1000))
                window = int(p.get("window", 60))
                prices = ctx.history(symbol, window, field="close")
                if len(prices) == window and all(value is not None and dec(value) > 0 for value in prices):
                    peak = max(dec(value) for value in prices)
                    drawdown = dec(1) - dec(prices[-1]) / peak
                    if drawdown >= dec(p.get("drawdown_threshold", "0.1")):
                        amount *= dec(p.get("multiplier", 2))
                ctx.order_value(symbol, min(ctx.cash, amount), "周期定投；达到回撤阈值时加码")
        elif self.name == "target_allocation":
            symbols = _selected_symbols(ctx)
            frequency = p.get("frequency", "monthly")
            if _scheduled(ctx, state, "last_period", frequency):
                targets = _allocation_weights(ctx, symbols, p.get("weights", {}))
                _target_allocation(ctx, symbols, targets, dec(p.get("rebalance_threshold", "0.05")), "按预设目标比例再平衡")
        elif self.name == "moving_average":
            window = int(p.get("window", 20))
            history = ctx.history(symbol, window)
            if symbol and len(history) == window and all(value is not None for value in history):
                target = weight if history[-1] > sum(history) / window else 0
                ctx.order_target_weight(symbol, target, "已知价格与历史均线比较")
        elif self.name == "ema_crossover":
            fast, slow = int(p.get("fast_window", 12)), int(p.get("slow_window", 26))
            values = _closes(ctx, symbol, slow + 1) if symbol else None
            if values:
                previous_fast, previous_slow = _ema(values[:-1], fast), _ema(values[:-1], slow)
                current_fast, current_slow = _ema(values, fast), _ema(values, slow)
                if previous_fast is not None and previous_slow is not None and current_fast is not None and current_slow is not None:
                    target = weight if current_fast > current_slow else 0
                    if (previous_fast <= previous_slow < current_fast) or (previous_fast >= previous_slow > current_fast):
                        ctx.order_target_weight(symbol, target, "EMA 短长均线交叉")
        elif self.name == "macd":
            fast, slow, signal = int(p.get("fast_window", 12)), int(p.get("slow_window", 26)), int(p.get("signal_window", 9))
            values = _closes(ctx, symbol, slow + signal + 2) if symbol else None
            if values:
                fast_values, slow_values = _ema_series(values, fast), _ema_series(values, slow)
                macd_values = [a - b if a is not None and b is not None else None for a, b in zip(fast_values, slow_values)]
                valid_macd = [value for value in macd_values if value is not None]
                signal_values = _ema_series(valid_macd, signal)
                if len(signal_values) >= 2 and len(valid_macd) >= 2:
                    current_macd, previous_macd = valid_macd[-1], valid_macd[-2]
                    current_signal, previous_signal = signal_values[-1], signal_values[-2]
                    if current_signal is not None and previous_signal is not None:
                        target = weight if current_macd > current_signal else 0
                        if (previous_macd <= previous_signal < current_macd) or (previous_macd >= previous_signal > current_macd):
                            ctx.order_target_weight(symbol, target, "MACD 线与信号线交叉")
        elif self.name == "time_series_momentum":
            window = int(p.get("window", 120))
            values = _closes(ctx, symbol, window + 1) if symbol else None
            if values:
                momentum = values[-1] / values[0] - 1
                target = weight if momentum > float(dec(p.get("threshold", 0))) else 0
                ctx.order_target_weight(symbol, target, "按已知滚动区间动量调整仓位")
        elif self.name == "rotation":
            symbols = _selected_symbols(ctx)
            window, top_n = int(p.get("window", 20)), int(p.get("top_n", 1))
            scores = {}
            for candidate in symbols:
                prices = _closes(ctx, candidate, window)
                if prices:
                    scores[candidate] = prices[-1] / prices[0] - 1
            if scores and _scheduled(ctx, state, "last_period", p.get("frequency", "monthly")):
                winners = sorted(scores, key=lambda candidate: (-scores[candidate], candidate))[:top_n]
                each_weight = weight / dec(top_n)
                targets = {candidate: each_weight if candidate in winners else dec(0) for candidate in symbols}
                _target_allocation(ctx, symbols, targets, dec(0), "按周期历史动量轮动")
        elif self.name == "donchian_breakout":
            entry = int(p.get("entry_window", 20))
            exit_window = int(p.get("exit_window", 10))
            rows = _bars(ctx, symbol, max(entry, exit_window) + 1) if symbol else None
            if rows:
                today = rows[-1]
                previous = rows[:-1]
                prior_high = max(dec(row["high"]) for row in previous[-entry:])
                prior_low = min(dec(row["low"]) for row in previous[-exit_window:])
                close = dec(today["close"])
                if close > prior_high:
                    ctx.order_target_weight(symbol, weight, "收盘突破此前唐奇安上轨")
                elif close < prior_low:
                    ctx.order_target_weight(symbol, 0, "收盘跌破此前唐奇安退出下轨")
        elif self.name == "drawdown_buy":
            window = int(p.get("window", 60))
            history = ctx.history(symbol, window, field="close") if symbol else []
            if len(history) == window and all(value is not None and dec(value) > 0 for value in history):
                if dec(history[-1]) / max(dec(value) for value in history) - 1 <= -dec(p.get("threshold", "0.1")):
                    ctx.order_target_weight(symbol, weight, "历史高点回撤达到阈值")
        elif self.name == "drawdown_ladder":
            window = int(p.get("window", 120))
            prices = ctx.history(symbol, window, field="close") if symbol else []
            if len(prices) == window and all(value is not None and dec(value) > 0 for value in prices):
                peak = max(dec(value) for value in prices)
                close = dec(prices[-1])
                drawdown = dec(1) - close / peak
                if close >= peak:
                    state["steps"] = 0
                else:
                    first, step = dec(p.get("first_drawdown", "0.1")), dec(p.get("step", "0.05"))
                    tier = min(int(p.get("max_steps", 5)), max(0, int((drawdown - first) // step) + 1)) if drawdown >= first else 0
                    previous_tier = int(state.get("steps", 0))
                    if tier > previous_tier:
                        amount = dec(p.get("amount", 1000)) * dec(tier - previous_tier)
                        ctx.order_value(symbol, min(ctx.cash, amount), f"回撤加深至第{tier}档，分层投入")
                        state["steps"] = tier
        elif self.name == "rsi_mean_reversion":
            window = int(p.get("window", 14))
            values = _closes(ctx, symbol, window + 1) if symbol else None
            current = _rsi(values, window) if values else None
            if current is not None:
                if current <= float(dec(p.get("entry", 30))):
                    ctx.order_target_weight(symbol, weight, "RSI 达到超卖入场线")
                elif current >= float(dec(p.get("exit", 55))):
                    ctx.order_target_weight(symbol, 0, "RSI 回升至退出线")
        elif self.name == "bollinger_mean_reversion":
            window = int(p.get("window", 20))
            values = _closes(ctx, symbol, window) if symbol else None
            if values:
                middle = sum(values) / window
                deviation = pstdev(values)
                lower = middle - float(dec(p.get("deviations", 2))) * deviation
                if values[-1] <= lower:
                    ctx.order_target_weight(symbol, weight, "收盘触及布林下轨")
                elif values[-1] >= middle:
                    ctx.order_target_weight(symbol, 0, "收盘回到布林中轨")
        elif self.name == "stochastic_mean_reversion":
            window = int(p.get("window", 14))
            rows = _bars(ctx, symbol, window) if symbol else None
            if rows:
                highest = max(dec(row["high"]) for row in rows)
                lowest = min(dec(row["low"]) for row in rows)
                if highest > lowest:
                    oscillator = float((dec(rows[-1]["close"]) - lowest) / (highest - lowest) * 100)
                    if oscillator <= float(dec(p.get("entry", 20))):
                        ctx.order_target_weight(symbol, weight, "随机指标达到超卖入场线")
                    elif oscillator >= float(dec(p.get("exit", 50))):
                        ctx.order_target_weight(symbol, 0, "随机指标回升至退出线")
        elif self.name == "atr_trend_stop":
            trend_window, atr_window = int(p.get("trend_window", 20)), int(p.get("atr_window", 14))
            rows = _bars(ctx, symbol, max(trend_window, atr_window) + 1) if symbol else None
            if rows:
                close = float(dec(rows[-1]["close"]))
                trend = sum(float(dec(row["close"])) for row in rows[-trend_window:]) / trend_window
                atr = _atr(rows, atr_window)
                if state.get("active"):
                    state["peak"] = max(float(state.get("peak", close)), close)
                    stop = state["peak"] - float(dec(p.get("atr_multiplier", 3))) * atr if atr is not None else None
                    if stop is not None and close <= stop:
                        ctx.order_target_weight(symbol, 0, "收盘触发 ATR 跟踪止损，下一交易日模拟退出")
                        state["active"] = False
                elif atr is not None and atr > 0 and close > trend:
                    risk_fraction = dec(p.get("risk_fraction", "0.01"))
                    maximum = dec(p.get("max_weight", "0.95"))
                    stop_distance = dec(atr) * dec(p.get("atr_multiplier", 3))
                    risk_weight = risk_fraction * dec(close) / stop_distance
                    target = min(maximum, risk_weight)
                    ctx.order_target_weight(symbol, target, "收盘位于趋势均线上方，按 ATR 风险预算控制仓位")
                    state["active"], state["peak"] = True, close
        elif self.name == "volatility_target":
            window = int(p.get("window", 20))
            values = _closes(ctx, symbol, window + 1) if symbol else None
            volatility = _annualized_volatility(values, window)
            if volatility is not None:
                target_vol = float(dec(p.get("target_volatility", "0.15")))
                max_weight = dec(p.get("max_weight", "0.95"))
                target = max_weight if volatility <= 0 else min(max_weight, dec(target_vol / volatility))
                ctx.order_target_weight(symbol, target, "按已知实现波动率缩放目标仓位")
        elif self.name == "inverse_volatility":
            symbols = _selected_symbols(ctx)
            if _scheduled(ctx, state, "last_period", p.get("frequency", "monthly")):
                window = int(p.get("window", 60))
                volatilities = {}
                for candidate in symbols:
                    values = _closes(ctx, candidate, window + 1)
                    volatility = _annualized_volatility(values, window)
                    if volatility is None:
                        volatilities = {}
                        break
                    volatilities[candidate] = max(volatility, 1e-12)
                if volatilities:
                    inverse = {candidate: 1 / value for candidate, value in volatilities.items()}
                    total = sum(inverse.values())
                    cap = float(dec(p.get("max_weight", "0.5")))
                    targets = {candidate: dec(min(cap, value / total)) for candidate, value in inverse.items()}
                    _target_allocation(ctx, symbols, targets, dec(0), "按滚动波动率倒数进行组合配置")
        elif self.name == "leverage_rebalance":
            ctx.order_target_weight(symbol, dec(p.get("weight", "1.5")), "每日恢复目标杠杆，包含交易成本")
        elif self.name == "futures_roll":
            # Explicit calendar schedule; no future volume is inspected.
            schedule = p.get("roll_schedule", {})
            eligible = [day for day in schedule if day <= ctx.date]
            if eligible:
                desired = schedule[max(eligible)]
                if desired != state.get("contract"):
                    for candidate, quantity in ctx.positions.items():
                        if quantity:
                            ctx.order_shares(candidate, -quantity, "预先固定日期平多换月")
                    ctx.order_shares(desired, int(p.get("contracts", 1)), "预先固定日期开下一月份多头")
                    state["contract"] = desired
