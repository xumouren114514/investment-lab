from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from types import MappingProxyType
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from investment_lab.common import dec, digest, money
from .account import Account, ZERO
from .models import Config, Order
from .cash_flows import MONTHLY_RULE, resolve_cash_flows
from .reference import REFERENCE_MODE, REFERENCE_BASIS, REFERENCE_WARNING, reference_available

STAR_BOARD_RULE_NOT_IMPLEMENTED = "科创板买入最低数量和增量规则待实现"


def is_shanghai_star_stock(security, symbol):
    if security.get("market") != "CN" or security.get("exchange") != "XSHG" or security.get("kind") != "stock":
        return False
    symbol_code = str(symbol).rsplit(".", 1)[-1]
    identity = str(security.get("universe_id") or symbol_code).rsplit(":", 1)[-1]
    return len(symbol_code) == 6 and symbol_code.startswith("688") and identity == symbol_code


class Context:
    """Only copies of already available bars/account values are exposed. Trusted Python, not a security sandbox."""
    def __init__(self, day, rows, account, equity, pending, submit, symbols, params, state, securities, mode):
        self.date, self.symbols, self.params, self.state = day, tuple(symbols), deepcopy(params), state
        self.cash, self.debt, self.equity = account.cash, account.debt, equity
        self.positions = dict(account.positions)
        self.pending = deepcopy(pending)
        self._rows = MappingProxyType({symbol: _HistoryView(values) for symbol, values in rows.items()})
        self._submit = submit
        self._multipliers = {s: dec(securities[s].get("multiplier", 1)) for s in symbols}
        self._default_field = "close" if mode in ("close_research", REFERENCE_MODE) else "vwap_value"

    def history(self, symbol, count=20, field=None):
        if count < 1:
            raise ValueError("窗口须为正")
        field = field or self._default_field
        return [dec(row[field]) if row.get(field) is not None else None for row in self._rows.get(symbol, [])[-count:]]

    def bars(self, symbol, count=20):
        if count < 1:
            raise ValueError("窗口须为正")
        return [dict(row) for row in self._rows.get(symbol, ())[-count:]]

    def order_shares(self, symbol, quantity, reason="策略订单"):
        return self._submit(symbol, dec(quantity), reason)

    def order_value(self, symbol, amount, reason="金额订单"):
        prices = self.history(symbol, 1, "close")
        if not prices:
            raise ValueError("缺少决策时可知价格")
        return self.order_shares(symbol, dec(amount) / (prices[-1] * self._multipliers[symbol]), reason)

    def order_target_weight(self, symbol, weight, reason="目标仓位"):
        if dec(weight) < 0:
            raise ValueError("禁止负权重")
        price = self.history(symbol, 1, "close")[-1]
        target = self.equity * dec(weight) / (price * self._multipliers[symbol])
        reserved = sum((dec(x["remaining"]) for x in self.pending if x["symbol"] == symbol), ZERO)
        return self.order_shares(symbol, target - self.positions.get(symbol, ZERO) - reserved, reason)


class _HistoryView:
    """Read-only, append-only history view; avoids copying all prior bars each session."""
    __slots__ = ("_values",)

    def __init__(self, values):
        self._values = values

    def __len__(self):
        return len(self._values)

    def __getitem__(self, index):
        value = self._values[index]
        if isinstance(index, slice):
            return tuple(MappingProxyType(row) for row in value)
        return MappingProxyType(value)


def rule_for(config, security, symbol, day):
    star = is_shanghai_star_stock(security, symbol)
    lot = 1 if star else security.get("lot", 1)
    rule = {"commission_bps": config.commission_bps, "minimum_commission": config.minimum_commission,
            "sell_tax_bps": config.sell_tax_bps, "lot": lot,
            "minimum_buy_quantity": security.get("minimum_buy_quantity", 200 if star else lot),
            "minimum_sell_quantity": security.get("minimum_sell_quantity", 200 if star else lot),
            "sell_delay": security.get("sell_delay", 0),
            "cash_settlement_days": config.cash_settlement_days}
    for item in sorted(config.rules, key=lambda r: r["effective"]):
        if item["effective"] <= day and item.get("market", security["market"]) == security["market"] and item.get("symbol", symbol) == symbol:
            rule.update({k: v for k, v in item.items() if k in rule})
    if config.mode == REFERENCE_MODE:
        # Adjusted reference units are not historical exchange board lots.
        rule["lot"] = 1
    if (dec(rule["lot"]) < 1 or dec(rule["minimum_buy_quantity"]) < dec(rule["lot"])
            or dec(rule["minimum_sell_quantity"]) < dec(rule["lot"])
            or any(dec(rule[k]) < 0 for k in ("commission_bps", "minimum_commission", "sell_tax_bps"))
            or int(rule["sell_delay"]) not in (0, 1)):
        raise ValueError("无效交易规则")
    return rule


def preflight(manifest, bars, config):
    from investment_lab.data.compose import validate_composed_selection
    validate_composed_selection(manifest, config)
    securities = manifest["securities"]
    if any(s not in securities for s in config.symbols):
        raise ValueError("所选证券不在固定快照内")
    reference = config.mode == REFERENCE_MODE
    if reference:
        if not reference_available(manifest, config.symbols):
            raise ValueError("参考研究仅支持已识别的 Yahoo 股票/ETF 参考序列；不能绕过其他执行限制")
        for bar in bars:
            if bar["symbol"] in config.symbols and bar["date"] <= config.end:
                if bar.get("price_basis") != REFERENCE_BASIS or bar.get("source") != "Yahoo/chart":
                    raise ValueError(f"{bar['symbol']}/{bar['date']}: 参考价格口径混合或未知，不能自动拼接")
                if dec(bar["close"]) <= 0 or dec(bar["volume"]) < 0:
                    raise ValueError("参考价格或成交量无效")
    selected = [securities[s] for s in config.symbols]
    if len({s["market"] for s in selected}) != 1 or len({s["currency"] for s in selected}) != 1:
        raise ValueError("每次回测仅允许一个市场和一种币种")
    if any(s.get("inverse") for s in selected):
        raise ValueError("禁止反向产品")
    sessions = [d for d in manifest["sessions"] if config.start <= d <= config.end]
    if len(sessions) < 2:
        raise ValueError("交易日历不足；缺失日期不能自动当作休市")
    if config.start < min(manifest["sessions"]) or config.end > max(manifest["sessions"]):
        raise ValueError("请求区间超出快照日历范围")
    resolve_cash_flows(config.cash_flows, sessions, config.start, config.end)
    index = {(b["symbol"], b["date"]): b for b in bars}
    for symbol, sec in zip(config.symbols, selected):
        star_rule_replaces_legacy_block = (
            is_shanghai_star_stock(sec, symbol)
            and sec.get("execution_blocked") == STAR_BOARD_RULE_NOT_IMPLEMENTED
        )
        if sec.get("execution_blocked") and sec.get("kind") != "index" and not reference and not star_rule_replaces_legacy_block:
            raise ValueError(f"{symbol}: {sec['execution_blocked']}")
        if sec.get("delisted") and config.end > sec["delisted"] and sec.get("kind") != "future":
            raise ValueError(f"{symbol}: 退市后资金/对价处理未配置，请缩短区间")
        if not reference and not manifest["synthetic"] and (not sec.get("listing_verified") or not sec.get("listed")):
            raise ValueError(f"{symbol}: 挂牌边界未核实")
        if not reference and not manifest["synthetic"] and not sec.get("actions_verified") and not config.allow_unverified_actions:
            raise ValueError(f"{symbol}: 公司行动未验证，需完成资料或明确选择研究假设")
        if sec.get("kind") == "future":
            if any(k not in sec for k in ("multiplier", "tick", "expiry", "initial_margin", "maintenance_margin")):
                raise ValueError("月份期货合约参数不完整")
            if dec(sec["multiplier"]) <= 0 or dec(sec["tick"]) <= 0 or not 0 < dec(sec["maintenance_margin"]) <= dec(sec["initial_margin"]) <= 1:
                raise ValueError("期货参数无效")
        eligible = [d for d in sessions if (not sec.get("listed") or d >= sec["listed"]) and (not sec.get("delisted") or d <= sec["delisted"])]
        for day in eligible:
            bar = index.get((symbol, day))
            if bar is None:
                raise ValueError(f"{symbol}/{day}: 缺少行情/明确停牌状态")
            cutoff = datetime.combine(date.fromisoformat(day), time(23, 59, 59), ZoneInfo(sec["timezone"]))
            if datetime.fromisoformat(bar["available_at"]) > cutoff:
                raise ValueError(f"{symbol}/{day}: 数据晚于当日决策窗口，须另建执行日偏移")
            if sec.get("kind") == "future" and not bar.get("settlement"):
                raise ValueError("期货缺少结算价")
            if bar.get("status", "trading") == "suspended" or sec.get("kind") == "index":
                continue
            if config.mode == "strict_vwap" and (not bar.get("vwap_value") or bar.get("quality_status") != "verified" or bar.get("vwap_method") not in ("vendor_verified", "amount_volume_verified")):
                raise ValueError(f"{symbol}/{day}: 严格 VWAP 不可用或口径未验证")
            if config.mode == "estimated_vwap" and not bar.get("vwap_value"):
                raise ValueError(f"{symbol}/{day}: 没有明确估算 VWAP，不能从 OHLCV 静默推导")
        warm = [b for b in bars if b["symbol"] == symbol and b["date"] < sessions[0]]
        first_cutoff = datetime.combine(date.fromisoformat(sessions[0]), time(23, 59, 59), ZoneInfo(sec["timezone"]))
        if any(datetime.fromisoformat(b["available_at"]) > first_cutoff for b in warm):
            raise ValueError(f"{symbol}: 预热数据在决策时尚不可用")
        if len(warm) < config.warmup:
            raise ValueError(f"{symbol}: 预热窗口不足")
    return sessions


def simulate(manifest, bars, config: Config, strategy, params=None, progress=None):
    sessions = preflight(manifest, bars, config)
    cash_flows = resolve_cash_flows(config.cash_flows, sessions, config.start, config.end)
    reference = config.mode == REFERENCE_MODE
    securities = manifest["securities"]
    index = {(b["symbol"], b["date"]): b for b in bars}
    account = Account(config.initial_cash)
    pending, trades, orders, curve, notes = [], [], [], [], []
    if reference:
        notes.append(REFERENCE_WARNING)
    histories = {s: [b for b in bars if b["symbol"] == s and b["date"] < sessions[0]] for s in config.symbols}
    prices = {s: dec(histories[s][-1]["close"]) for s in config.symbols if histories[s]}
    state, next_id = {}, 1
    bankrupt = False
    for session_index, day in enumerate(sessions):
        previous_day = sessions[session_index - 1] if session_index else day
        elapsed = (date.fromisoformat(day) - date.fromisoformat(previous_day)).days
        rate = dec(config.annual_interest)
        # Integrate rate changes across calendar days, including weekends.
        if elapsed:
            from datetime import timedelta
            daily = []
            for n in range(elapsed):
                interest_day = (date.fromisoformat(previous_day) + timedelta(days=n)).isoformat()
                candidates = [k for k in config.rate_curve if k <= interest_day]
                daily.append(dec(config.rate_curve[max(candidates)]) if candidates else rate)
            rate = sum(daily, ZERO) / dec(elapsed)
        flow = money(cash_flows.get(day, 0))
        account.cash_events(day, session_index, elapsed, rate, flow)
        rows = {s: index[(s, day)] for s in config.symbols if (s, day) in index}
        for action in ([] if reference else manifest["actions"]):
            if action["date"] == day and action["symbol"] in config.symbols:
                account.action(day, action)
                if action["type"] == "split":
                    symbol, ratio = action["symbol"], dec(action["ratio"])
                    if symbol in prices:
                        prices[symbol] /= ratio
                    for order in pending:
                        if order.symbol == symbol:
                            order.remaining *= ratio
        used_volume = {}
        # Orders are submitted in signal order, with margin liquidations first.
        for order in sorted(list(pending), key=lambda o: (not o.forced, o.id)):
            order.age += 1
            sec = securities[order.symbol]
            bar = rows.get(order.symbol)
            failure = None
            if bar is None or bar.get("status", "trading") != "trading" or dec(bar["volume"]) <= 0:
                failure = "停牌、非挂牌日期或无成交量"
            elif (order.remaining > 0 and bar.get("limit_up") and dec(bar["high"]) >= dec(bar["limit_up"])) or (order.remaining < 0 and bar.get("limit_down") and dec(bar["low"]) <= dec(bar["limit_down"])):
                failure = "触及价格限制，日线保守拒绝成交"
            elif sec.get("kind") == "future" and day >= sec["expiry"] and order.remaining > 0:
                failure = "到期日不新开仓"
            if failure is None:
                rule = rule_for(config, sec, order.symbol, day)
                lot = dec(rule["lot"])
                raw_price = dec(bar["close"] if config.mode in ("close_research", REFERENCE_MODE) else bar["vwap_value"])
                sign = dec(1 if order.remaining > 0 else -1)
                price = raw_price * (1 + sign * dec(config.slippage_bps) / 10000)
                if sec.get("kind") == "future":
                    tick = dec(sec["tick"])
                    price = (price / tick).to_integral_value(rounding=ROUND_CEILING if sign > 0 else ROUND_FLOOR) * tick
                volume_budget = max(ZERO, dec(bar["volume"]) * dec(config.max_participation) - used_volume.get(order.symbol, ZERO))
                requested = min(abs(order.remaining), volume_budget)
                if sign < 0:
                    sellable = account.positions.get(order.symbol, ZERO) - (account.bought_today.get(order.symbol, ZERO) if int(rule["sell_delay"]) else ZERO)
                    requested = min(requested, max(ZERO, sellable))
                qty = (requested / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
                # Full odd-lot liquidation is allowed; never invent cash-in-lieu.
                if sign < 0 and requested == account.positions.get(order.symbol, ZERO):
                    qty = requested
                multiplier = dec(sec.get("multiplier", 1))
                def fees(q):
                    notional = q * price * multiplier
                    return money(max(dec(rule["minimum_commission"]), notional * dec(rule["commission_bps"]) / 10000) + (notional * dec(rule["sell_tax_bps"]) / 10000 if sign < 0 else ZERO)) if q else ZERO
                def affordable(q):
                    cost = q * price * multiplier
                    marks = {**prices, order.symbol: price}
                    eq = account.equity(marks, securities) - fees(q)
                    exp = account.exposure(marks, securities) + cost
                    allowed_leverage = dec(config.max_leverage) if sec.get("margin_eligible", False) or sec.get("kind") == "future" else dec(1)
                    if eq <= 0 or exp > eq * allowed_leverage:
                        return False
                    if sec.get("kind") == "future":
                        required = account.margin(marks, securities) + cost * dec(sec["initial_margin"]) + fees(q)
                        return required <= account.cash and account.debt == 0
                    if allowed_leverage == 1:
                        return money(cost) + fees(q) <= account.cash - account.margin(marks, securities)
                    reserved_margin = account.margin(marks, securities)
                    return not reserved_margin or money(cost) + fees(q) <= account.cash - reserved_margin
                if sign > 0 and qty:
                    low, high = 0, int(qty / lot)
                    while low < high:
                        middle = (low + high + 1) // 2
                        if affordable(dec(middle) * lot):
                            low = middle
                        else:
                            high = middle - 1
                    qty = dec(low) * lot
                minimum_buy = dec(rule["minimum_buy_quantity"])
                minimum_sell = dec(rule["minimum_sell_quantity"])
                if sign > 0 and order.quantity < minimum_buy:
                    failure = f"买入订单低于最低申报数量 {minimum_buy} 股"
                    qty = ZERO
                elif sign > 0 and qty < minimum_buy and not affordable(minimum_buy):
                    failure = f"现金不足以买入最低申报数量 {minimum_buy} 股"
                    qty = ZERO
                elif sign < 0 and order.quantity.copy_abs() < minimum_sell and not order.odd_lot_liquidation:
                    failure = f"卖出订单低于最低申报数量 {minimum_sell} 股，须一次性卖出全部剩余持仓"
                    qty = ZERO
                if qty:
                    fee, old_qty = fees(qty), account.positions.get(order.symbol, ZERO)
                    old_basis = account.cost_basis.get(order.symbol, ZERO)
                    if sec.get("kind") == "future":
                        account.debit(fee)
                        basis = account.futures_basis.get(order.symbol, price)
                        if sign > 0:
                            account.futures_basis[order.symbol] = (old_qty * basis + qty * price) / (old_qty + qty)
                        else:
                            pnl = money(qty * multiplier * (price - basis))
                            account.credit(pnl) if pnl >= 0 else account.debit(-pnl)
                            account.realized.append(float(pnl - fee))
                    elif sign > 0:
                        account.debit(qty * price + fee)
                    else:
                        proceeds = money(qty * price - fee)
                        if proceeds < 0:
                            account.debit(-proceeds)
                        elif int(rule["cash_settlement_days"]):
                            account.unsettled.append({"amount": str(proceeds), "due": session_index + int(rule["cash_settlement_days"])})
                        else:
                            account.credit(proceeds)
                        account.realized.append(float(money(qty * (price - old_basis) - fee)))
                    account.positions[order.symbol] = old_qty + sign * qty
                    if sign > 0:
                        account.cost_basis[order.symbol] = (old_qty * old_basis + qty * price + fee / multiplier) / (old_qty + qty)
                        account.bought_today[order.symbol] = account.bought_today.get(order.symbol, ZERO) + qty
                    account.fees += fee
                    order.remaining -= sign * qty
                    used_volume[order.symbol] = used_volume.get(order.symbol, ZERO) + qty
                    trade = {"order_id": order.id, "signal_date": order.signal_date, "date": day, "symbol": order.symbol,
                             "quantity": str(sign * qty), "price": str(price), "benchmark_price": str(raw_price), "fee": str(fee),
                             "notional": str(qty * price * multiplier), "reason": order.reason, "visible": order.visible, "forced": order.forced}
                    trades.append(trade)
                    account.event(day, "fill", **{k: v for k, v in trade.items() if k != "date"})
                else:
                    failure = failure or "现金、杠杆、保证金、交易单位、参与率或可卖数量不足"
            if failure:
                account.event(day, "order_unfilled", order_id=order.id, reason=failure)
            if order.remaining == 0 or order.age >= config.order_lifetime:
                pending.remove(order)
                if order.remaining:
                    account.event(day, "order_expired", order_id=order.id, remaining=order.remaining)
        for symbol, bar in rows.items():
            prices[symbol] = dec(bar["settlement"] if securities[symbol].get("kind") == "future" else bar["close"])
            histories[symbol].append(bar)
        account.settle_futures(day, rows, securities)
        for symbol, qty in list(account.positions.items()):
            sec = securities[symbol]
            if qty and sec.get("kind") == "future" and day >= sec["expiry"]:
                account.positions[symbol] = ZERO
                account.event(day, "contract_expiry", symbol=symbol, quantity=qty, settlement=prices[symbol])
        equity = account.equity(prices, securities)
        exposure = account.exposure(prices, securities)
        margin = account.margin(prices, securities)
        leverage = exposure / equity if equity > 0 else ZERO
        curve.append({"date": day, "equity": str(equity), "cash": str(account.cash), "debt": str(account.debt), "receivables": str(sum((dec(x["amount"]) for x in account.receivables), ZERO)),
                      "unsettled": str(sum((dec(x["amount"]) for x in account.unsettled), ZERO)), "flow": str(flow), "exposure": str(exposure), "leverage": str(leverage), "margin": str(margin),
                      "interest": str(account.interest), "positions": {s: str(q) for s, q in account.positions.items()}})
        if equity <= 0:
            bankrupt = True
            notes.append("权益不为正：停止新增交易，末值保留跳空损失；未假称可消除负债")
            break
        def submit(symbol, quantity, reason, forced=False):
            nonlocal next_id
            if symbol not in config.symbols or securities[symbol].get("kind") == "index":
                raise ValueError("证券不在本轮交易范围或为不可成交指数")
            sec = securities[symbol]
            if (sec.get("listed") and day < sec["listed"]) or (sec.get("delisted") and day >= sec["delisted"]):
                raise ValueError("下单日期不在挂牌期")
            lot = dec(rule_for(config, sec, symbol, day)["lot"])
            if quantity != -account.positions.get(symbol, ZERO):
                quantity = (abs(quantity) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot * (1 if quantity > 0 else -1)
            if not quantity:
                return None
            queued_sells = sum((abs(o.remaining) for o in pending if o.symbol == symbol and o.remaining < 0), ZERO)
            if quantity < 0 and abs(quantity) + queued_sells > account.positions.get(symbol, ZERO):
                raise ValueError("禁止净空头，卖单超过已有可持有数量")
            visible = {"through": day, "reference_close": str(prices.get(symbol)), "equity": str(equity), "history_hash": digest(histories.get(symbol, []))}
            sell_rule = rule_for(config, sec, symbol, day)
            odd_lot_liquidation = (quantity < 0 and abs(quantity) < dec(sell_rule["minimum_sell_quantity"])
                                   and abs(quantity) == account.positions.get(symbol, ZERO) and queued_sells == 0)
            order = Order(next_id, symbol, quantity, quantity, day, str(reason), visible, forced=forced,
                          odd_lot_liquidation=odd_lot_liquidation)
            next_id += 1
            pending.append(order)
            orders.append({"id": order.id, "symbol": symbol, "quantity": str(quantity), "signal_date": day, "reason": reason, "visible": visible})
            return order.id
        maintenance_margin = sum((q * prices[s] * dec(securities[s].get("multiplier", 1)) * dec(securities[s].get("maintenance_margin", 0)) for s, q in account.positions.items() if securities[s].get("kind") == "future"), ZERO)
        breached = (exposure and equity / exposure < dec(config.maintenance_equity_ratio)) or (maintenance_margin and account.cash - account.debt < maintenance_margin)
        if breached:
            for order in list(pending):
                account.event(day, "order_cancelled_margin", order_id=order.id)
            pending.clear()
            for symbol, qty in account.positions.items():
                if qty:
                    submit(symbol, -qty, "维持保证金不足，下一交易日减仓", True)
            account.event(day, "margin_call", equity=equity, exposure=exposure)
        elif day != sessions[-1]:
            ctx = Context(day, histories, account, equity, [{"symbol": o.symbol, "remaining": str(o.remaining)} for o in pending], submit, config.symbols, params or {}, state, securities, config.mode)
            if session_index == 0 and hasattr(strategy, "initialize"):
                strategy.initialize(ctx)
            strategy.on_session(ctx)
        if progress and ((session_index + 1) % 10 == 0 or day == sessions[-1]):
            progress(session_index + 1, len(sessions))
    for order in pending:
        account.event(curve[-1]["date"], "end_unfilled", order_id=order.id, remaining=order.remaining)
    if hasattr(strategy, "on_finish"):
        strategy.on_finish(SimpleNamespace(curve=deepcopy(curve), trades=deepcopy(trades), state=state))
    from investment_lab.research.metrics import metrics
    return {"curve": curve, "trades": trades, "orders": orders, "ledger": account.ledger,
            "metrics": metrics(curve, trades, config.initial_cash, account.realized),
            "metadata": {"price_model": config.mode, "valuation": "拆股调整参考收盘价" if reference else "股票收盘价/期货结算价", "synthetic": manifest["synthetic"],
                         "cash_flow_schedule": deepcopy(config.cash_flows), "cash_flows": cash_flows,
                         "monthly_deposit_rule": MONTHLY_RULE if "monthly" in config.cash_flows else None,
                         "reference_only": reference, "research_warning": REFERENCE_WARNING if reference else None,
                         "corporate_action_policy": "忽略拆股及现金分红；仅价格收益，不含股息" if reference else "事件分别记账",
                         "reference_units": "调整后参考份额，不是历史实际股数" if reference else None,
                         "point_in_time_prices_verified": False if reference else None,
                         "currency": securities[config.symbols[0]]["currency"], "assumptions": config.assumptions,
                         "notes": notes, "bankrupt": bankrupt, "signal_model": "当日数据可用后决策，数量固定，下一交易日结算成交",
                         "survivorship_bias": "固定事后选择股票池，未消除生存者偏差", "selection_date": manifest.get("source", {}).get("selection_date"),
                         "normal_ranking_eligible": config.mode not in ("close_research", REFERENCE_MODE) and not manifest["synthetic"] and not config.allow_unverified_actions}}
