from __future__ import annotations

import hashlib
from bisect import bisect_left
from copy import deepcopy
from datetime import date, datetime, time, timedelta
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from types import MappingProxyType
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from investment_lab.common import dec, digest, encoded, money
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


_UNCONVERTED = object()
_ATOMIC_TYPES = frozenset((str, int, float, bool, type(None), Decimal))


class _NotPlainData(Exception):
    pass


def _copy_plain(value, seen):
    kind = type(value)
    if kind in _ATOMIC_TYPES:
        return value
    if kind is not dict and kind is not list or id(value) in seen:
        raise _NotPlainData
    seen.add(id(value))
    if kind is list:
        return [_copy_plain(item, seen) for item in value]
    copied = {}
    for key, item in value.items():
        if type(key) not in _ATOMIC_TYPES:
            raise _NotPlainData
        copied[key] = _copy_plain(item, seen)
    return copied


def _deep_copy(value):
    """Same result as deepcopy for JSON-like data; shared, cyclic or other objects use deepcopy."""
    try:
        return _copy_plain(value, set())
    except _NotPlainData:
        return deepcopy(value)


class Context:
    """Only copies of already available bars/account values are exposed. Trusted Python, not a security sandbox."""
    def __init__(self, day, rows, account, equity, pending, submit, symbols, params, state, securities, mode, diagnostic=None,
                 value_cache=None):
        self.date, self.symbols, self.params, self.state = day, tuple(symbols), _deep_copy(params), state
        self.cash, self.debt, self.equity = account.cash, account.debt, equity
        self.positions = dict(account.positions)
        self.pending = _deep_copy(pending)
        self._history_rows = rows
        self._rows = MappingProxyType({symbol: _HistoryView(values) for symbol, values in rows.items()})
        self._submit = submit
        self._diagnostic = diagnostic
        self._multipliers = {s: dec(securities[s].get("multiplier", 1)) for s in symbols}
        self._default_field = "close" if mode in ("close_research", REFERENCE_MODE) else "vwap_value"
        # Decimal values of the append-only histories, shared by the sessions of one simulation.
        self._values = {} if value_cache is None else value_cache

    def note(self, code, message, **details):
        """Record a bounded diagnostic without changing orders or the strategy clock."""
        if self._diagnostic is not None:
            self._diagnostic(self.date, code, message, details)

    def history(self, symbol, count=20, field=None):
        if count < 1:
            raise ValueError("窗口须为正")
        field = field or self._default_field
        rows = self._history_rows.get(symbol)
        # Same window bounds (and errors for invalid counts) as rows[-count:].
        start, end, _ = slice(-count, None).indices(len(rows) if rows is not None else 0)
        if rows is None:
            return []
        key = (symbol, field)
        values = self._values.get(key)
        if values is None:
            values = self._values[key] = []
        if len(values) < end:
            values.extend([_UNCONVERTED] * (end - len(values)))
        # Convert only rows inside the requested window, once per simulation.
        for position in range(start, end):
            if values[position] is _UNCONVERTED:
                row = rows[position]
                values[position] = dec(row[field]) if row.get(field) is not None else None
        return values[start:end]

    def bars(self, symbol, count=20):
        if count < 1:
            raise ValueError("窗口须为正")
        return [dict(row) for row in self._history_rows.get(symbol, ())[-count:]]

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


class _SymbolBars:
    """Per-symbol lookups over prepared bars, reused by every research window.

    Cached values are derived only from the immutable bar dictionaries and are
    exact replacements for the per-call scans they avoid; any irregular input
    (unsorted dates, malformed timestamps, failing reference rows) falls back
    to the original computation so results and error messages are unchanged.
    """
    __slots__ = ("rows", "by_date", "_dates", "_encoded", "_checkpoints", "_late", "_available",
                 "_available_error", "_reference_clean")
    _CHECKPOINT = 32

    def __init__(self, rows):
        self.rows = rows
        self.by_date = {row["date"]: row for row in rows}
        dates = [row["date"] for row in rows]
        ordered = all(isinstance(day, str) for day in dates) and all(a < b for a, b in zip(dates, dates[1:]))
        self._dates = dates if ordered else None
        self._encoded, self._late = {}, {}
        self._checkpoints = [hashlib.sha256(b"[")]
        self._available, self._available_error = [], None
        self._reference_clean = None

    @property
    def ordered(self):
        return self._dates is not None

    def before(self, first):
        """Equals [row for row in rows if row["date"] < first]."""
        if self._dates is not None:
            return self.rows[:bisect_left(self._dates, first)]
        return [row for row in self.rows if row["date"] < first]

    def encoded(self, row):
        # Rows stay referenced by self.rows, so their ids are stable for this cache's lifetime.
        key = id(row)
        value = self._encoded.get(key)
        if value is None:
            value = self._encoded[key] = encoded(row)
        return value

    def prefix_hasher(self, count):
        """sha256 state of '[' plus the first `count` encoded rows joined by commas (ordered rows only)."""
        step, checkpoints, rows = self._CHECKPOINT, self._checkpoints, self.rows
        target = count // step
        while len(checkpoints) <= target:
            index = len(checkpoints) - 1
            hasher = checkpoints[index].copy()
            for position in range(index * step, (index + 1) * step):
                if position:
                    hasher.update(b",")
                hasher.update(self.encoded(rows[position]))
            checkpoints.append(hasher)
        hasher = checkpoints[target].copy()
        for position in range(target * step, count):
            if position:
                hasher.update(b",")
            hasher.update(self.encoded(rows[position]))
        return hasher

    def late(self, row, timezone_name):
        """Equals available_at > 23:59:59 local time of the bar's own session day."""
        key = (id(row), timezone_name)
        value = self._late.get(key)
        if value is None:
            cutoff = datetime.combine(date.fromisoformat(row["date"]), time(23, 59, 59), ZoneInfo(timezone_name))
            value = self._late[key] = datetime.fromisoformat(row["available_at"]) > cutoff
        return value

    def warm_late(self, count, cutoff):
        """any(available_at > cutoff) over the first `count` ordered rows, or None to use the original scan."""
        maxima, rows = self._available, self.rows
        while len(maxima) < count and self._available_error is None:
            position = len(maxima)
            try:
                parsed = datetime.fromisoformat(rows[position]["available_at"])
                aware = parsed.utcoffset() is not None
            except Exception:
                aware = False
            if not aware:
                self._available_error = position
                break
            maxima.append(parsed if not maxima or parsed > maxima[-1] else maxima[-1])
        if self._available_error is not None and self._available_error < count:
            return None
        return count > 0 and maxima[count - 1] > cutoff

    def reference_clean(self):
        """True when no row of this symbol can fail the reference-mode price checks."""
        if self._reference_clean is None:
            try:
                self._reference_clean = all(
                    isinstance(row["date"], str) and row.get("price_basis") == REFERENCE_BASIS
                    and row.get("source") == "Yahoo/chart" and dec(row["close"]) > 0 and dec(row["volume"]) >= 0
                    for row in self.rows)
            except Exception:
                self._reference_clean = False
        return self._reference_clean


class PreparedBars(list):
    """A snapshot's bars plus per-symbol caches shared by research windows and benchmarks.

    It is still the original list of bar dictionaries; those dictionaries must
    not be modified while the prepared object is in use.
    """
    def __init__(self, bars):
        super().__init__(bars)
        groups = {}
        for bar in self:
            groups.setdefault(bar["symbol"], []).append(bar)
        self._groups, self._symbols = groups, {}

    def symbol(self, symbol):
        prepared = self._symbols.get(symbol)
        if prepared is None:
            prepared = self._symbols[symbol] = _SymbolBars(self._groups.get(symbol, []))
        return prepared


def prepare_bars(bars):
    return bars if isinstance(bars, PreparedBars) else PreparedBars(bars)


class _HistoryDigest:
    """Incremental common.digest(rows) for one append-only history list.

    The canonical JSON of a list is "[" + ",".join(item encodings) + "]", so
    each known bar is encoded once instead of re-serializing the full history
    on every order. The hex digest is identical to digest(rows).
    """
    __slots__ = ("_source", "_hasher", "_count")

    def __init__(self, source, seeded=0):
        # `seeded` leading rows equal source.rows[:seeded] and come from shared checkpoints.
        self._source, self._hasher, self._count = source, None, seeded

    def __call__(self, rows):
        hasher = self._hasher
        if hasher is None:
            hasher = self._hasher = self._source.prefix_hasher(self._count) if self._count else hashlib.sha256(b"[")
        for position in range(self._count, len(rows)):
            if position:
                hasher.update(b",")
            hasher.update(self._source.encoded(rows[position]))
        self._count = len(rows)
        final = hasher.copy()
        final.update(b"]")
        return final.hexdigest()


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
    return _preflight(manifest, prepare_bars(bars), config)


def _preflight(manifest, bars, config):
    from investment_lab.data.compose import validate_composed_selection
    validate_composed_selection(manifest, config)
    securities = manifest["securities"]
    if any(s not in securities for s in config.symbols):
        raise ValueError("所选证券不在固定快照内")
    reference = config.mode == REFERENCE_MODE
    if reference:
        if not reference_available(manifest, config.symbols):
            raise ValueError("参考研究仅支持已识别的 Yahoo 股票/ETF 参考序列；不能绕过其他执行限制")
        # The full ordered scan only runs when a selected row could fail, keeping its first error.
        if not all(bars.symbol(s).reference_clean() for s in config.symbols):
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
    for symbol, sec in zip(config.symbols, selected):
        symbol_bars = bars.symbol(symbol)
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
            bar = symbol_bars.by_date.get(day)
            if bar is None:
                raise ValueError(f"{symbol}/{day}: 缺少行情/明确停牌状态")
            if symbol_bars.late(bar, sec["timezone"]):
                raise ValueError(f"{symbol}/{day}: 数据晚于当日决策窗口，须另建执行日偏移")
            if sec.get("kind") == "future" and not bar.get("settlement"):
                raise ValueError("期货缺少结算价")
            if bar.get("status", "trading") == "suspended" or sec.get("kind") == "index":
                continue
            if config.mode == "strict_vwap" and (not bar.get("vwap_value") or bar.get("quality_status") != "verified" or bar.get("vwap_method") not in ("vendor_verified", "amount_volume_verified")):
                raise ValueError(f"{symbol}/{day}: 严格 VWAP 不可用或口径未验证")
            if config.mode == "estimated_vwap" and not bar.get("vwap_value"):
                raise ValueError(f"{symbol}/{day}: 没有明确估算 VWAP，不能从 OHLCV 静默推导")
        warm = symbol_bars.before(sessions[0])
        first_cutoff = datetime.combine(date.fromisoformat(sessions[0]), time(23, 59, 59), ZoneInfo(sec["timezone"]))
        late = symbol_bars.warm_late(len(warm), first_cutoff) if symbol_bars.ordered else None
        if late is None:
            late = any(datetime.fromisoformat(b["available_at"]) > first_cutoff for b in warm)
        if late:
            raise ValueError(f"{symbol}: 预热数据在决策时尚不可用")
        if len(warm) < config.warmup:
            raise ValueError(f"{symbol}: 预热窗口不足")
    return sessions


def simulate(manifest, bars, config: Config, strategy, params=None, progress=None):
    bars = prepare_bars(bars)
    sessions = _preflight(manifest, bars, config)
    cash_flows = resolve_cash_flows(config.cash_flows, sessions, config.start, config.end)
    reference = config.mode == REFERENCE_MODE
    securities = manifest["securities"]
    symbol_bars = {s: bars.symbol(s) for s in config.symbols}
    account = Account(config.initial_cash)
    pending, trades, orders, curve, notes = [], [], [], [], []
    strategy_diagnostics, diagnostic_keys = [], set()
    diagnostic_omitted = 0

    def record_diagnostic(day, code, message, details):
        nonlocal diagnostic_omitted
        item = {"date": day, "code": str(code)[:80], "message": str(message)[:400]}
        for key in ("strategy", "symbol", "required_history", "available_history", "missing_fields", "action"):
            value = details.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                item[key] = [str(part)[:80] for part in value[:8]]
            elif isinstance(value, int) and not isinstance(value, bool):
                item[key] = value
            else:
                item[key] = str(value)[:120]
        key = digest(item)
        if key in diagnostic_keys:
            return
        if len(strategy_diagnostics) >= 200:
            diagnostic_omitted += 1
            return
        diagnostic_keys.add(key)
        strategy_diagnostics.append(item)
    if reference:
        notes.append(REFERENCE_WARNING)
    histories = {s: symbol_bars[s].before(sessions[0]) for s in config.symbols}
    prices = {s: dec(histories[s][-1]["close"]) for s in config.symbols if histories[s]}
    history_digests = {s: _HistoryDigest(symbol_bars[s], len(histories[s]) if symbol_bars[s].ordered else 0)
                       for s in config.symbols}
    history_values = {}
    # Trading rules are pure functions of (symbol, day): reuse them within a session, or for the
    # whole run when no dated rule overrides exist.
    rule_cache = {}

    def trading_rule(symbol, day):
        rule = rule_cache.get(symbol)
        if rule is None:
            rule = rule_cache[symbol] = rule_for(config, securities[symbol], symbol, day)
        return rule
    actions_by_day = {}
    if not reference:
        for action in manifest["actions"]:
            actions_by_day.setdefault(action["date"], []).append(action)
    annual_rate = dec(config.annual_interest)
    slippage = dec(config.slippage_bps)
    participation = dec(config.max_participation)
    max_leverage = dec(config.max_leverage)
    state, next_id = {}, 1
    bankrupt = False
    for session_index, day in enumerate(sessions):
        if config.rules:
            rule_cache.clear()
        previous_day = sessions[session_index - 1] if session_index else day
        elapsed = (date.fromisoformat(day) - date.fromisoformat(previous_day)).days
        rate = annual_rate
        # Integrate rate changes across calendar days, including weekends.
        if elapsed and config.rate_curve:
            daily = []
            for n in range(elapsed):
                interest_day = (date.fromisoformat(previous_day) + timedelta(days=n)).isoformat()
                candidates = [k for k in config.rate_curve if k <= interest_day]
                daily.append(dec(config.rate_curve[max(candidates)]) if candidates else rate)
            rate = sum(daily, ZERO) / dec(elapsed)
        elif elapsed:
            rate = sum([annual_rate] * elapsed, ZERO) / dec(elapsed)
        flow = money(cash_flows.get(day, 0))
        account.cash_events(day, session_index, elapsed, rate, flow)
        rows = {s: row for s in config.symbols if (row := symbol_bars[s].by_date.get(day)) is not None}
        for action in actions_by_day.get(day, ()):
            if action["symbol"] in config.symbols:
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
                rule = trading_rule(order.symbol, day)
                lot = dec(rule["lot"])
                raw_price = dec(bar["close"] if config.mode in ("close_research", REFERENCE_MODE) else bar["vwap_value"])
                sign = dec(1 if order.remaining > 0 else -1)
                price = raw_price * (1 + sign * slippage / 10000)
                if sec.get("kind") == "future":
                    tick = dec(sec["tick"])
                    price = (price / tick).to_integral_value(rounding=ROUND_CEILING if sign > 0 else ROUND_FLOOR) * tick
                volume_budget = max(ZERO, dec(bar["volume"]) * participation - used_volume.get(order.symbol, ZERO))
                requested = min(abs(order.remaining), volume_budget)
                if sign < 0:
                    sellable = account.positions.get(order.symbol, ZERO) - (account.bought_today.get(order.symbol, ZERO) if int(rule["sell_delay"]) else ZERO)
                    requested = min(requested, max(ZERO, sellable))
                qty = (requested / lot).to_integral_value(rounding=ROUND_FLOOR) * lot
                # Full odd-lot liquidation is allowed; never invent cash-in-lieu.
                if sign < 0 and requested == account.positions.get(order.symbol, ZERO):
                    qty = requested
                multiplier = dec(sec.get("multiplier", 1))
                minimum_commission, commission_bps = dec(rule["minimum_commission"]), dec(rule["commission_bps"])
                sell_tax_bps = dec(rule["sell_tax_bps"])
                def fees(q):
                    notional = q * price * multiplier
                    return money(max(minimum_commission, notional * commission_bps / 10000) + (notional * sell_tax_bps / 10000 if sign < 0 else ZERO)) if q else ZERO
                # The account and marks do not change while one order is sized, so the
                # quantity-independent valuations are computed once per order.
                valuation = []
                def affordable(q):
                    if not valuation:
                        marks = {**prices, order.symbol: price}
                        valuation.extend((account.equity(marks, securities), account.exposure(marks, securities),
                                          account.margin(marks, securities)))
                    current_equity, current_exposure, reserved_margin = valuation
                    cost = q * price * multiplier
                    eq = current_equity - fees(q)
                    exp = current_exposure + cost
                    allowed_leverage = max_leverage if sec.get("margin_eligible", False) or sec.get("kind") == "future" else dec(1)
                    if eq <= 0 or exp > eq * allowed_leverage:
                        return False
                    if sec.get("kind") == "future":
                        required = reserved_margin + cost * dec(sec["initial_margin"]) + fees(q)
                        return required <= account.cash and account.debt == 0
                    if allowed_leverage == 1:
                        return money(cost) + fees(q) <= account.cash - reserved_margin
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
            lot = dec(trading_rule(symbol, day)["lot"])
            if quantity != -account.positions.get(symbol, ZERO):
                quantity = (abs(quantity) / lot).to_integral_value(rounding=ROUND_FLOOR) * lot * (1 if quantity > 0 else -1)
            if not quantity:
                return None
            queued_sells = sum((abs(o.remaining) for o in pending if o.symbol == symbol and o.remaining < 0), ZERO)
            if quantity < 0 and abs(quantity) + queued_sells > account.positions.get(symbol, ZERO):
                raise ValueError("禁止净空头，卖单超过已有可持有数量")
            visible = {"through": day, "reference_close": str(prices.get(symbol)), "equity": str(equity), "history_hash": history_digests[symbol](histories[symbol])}
            sell_rule = trading_rule(symbol, day)
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
            ctx = Context(day, histories, account, equity, [{"symbol": o.symbol, "remaining": str(o.remaining)} for o in pending], submit, config.symbols, params or {}, state, securities, config.mode, record_diagnostic,
                          history_values)
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
    if strategy_diagnostics:
        notes.append(f"策略诊断：记录 {len(strategy_diagnostics)} 项历史不足、字段缺失或其他提示；请查看诊断明细。")
    if diagnostic_omitted:
        notes.append(f"策略诊断达到 200 项上限，另有 {diagnostic_omitted} 项未保存；提示不改变撮合或资金计算。")
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
                         "notes": notes, "strategy_diagnostics": strategy_diagnostics, "strategy_diagnostics_omitted": diagnostic_omitted,
                         "bankrupt": bankrupt, "signal_model": "当日数据可用后决策，数量固定，下一交易日结算成交",
                         "survivorship_bias": "固定事后选择股票池，未消除生存者偏差", "selection_date": manifest.get("source", {}).get("selection_date"),
                         "normal_ranking_eligible": config.mode not in ("close_research", REFERENCE_MODE) and not manifest["synthetic"] and not config.allow_unverified_actions}}
