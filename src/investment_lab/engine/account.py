from decimal import Decimal, ROUND_FLOOR
from investment_lab.common import dec, money

ZERO = Decimal(0)


class Account:
    def __init__(self, initial):
        self.cash = money(initial)
        self.debt = ZERO
        self.positions = {}
        self.bought_today = {}
        self.receivables = []
        self.unsettled = []
        self.futures_basis = {}
        self.cost_basis = {}
        self.ledger = []
        self.fees = ZERO
        self.interest = ZERO
        self.realized = []

    def event(self, day, kind, **values):
        self.ledger.append({"date": day, "type": kind, **{k: str(v) if isinstance(v, Decimal) else v for k, v in values.items()}, "cash": str(self.cash), "debt": str(self.debt)})

    def credit(self, amount):
        amount = money(amount)
        repaid = min(amount, self.debt)
        self.debt -= repaid
        self.cash += amount - repaid

    def debit(self, amount):
        amount = money(amount)
        used = min(self.cash, amount)
        self.cash -= used
        self.debt += amount - used

    def equity(self, prices, securities):
        value = self.cash - self.debt + sum((dec(x["amount"]) for x in self.receivables), ZERO) + sum((dec(x["amount"]) for x in self.unsettled), ZERO)
        for symbol, qty in self.positions.items():
            if securities[symbol].get("kind") == "future":
                value += qty * dec(securities[symbol]["multiplier"]) * (prices[symbol] - self.futures_basis.get(symbol, prices[symbol]))
            else:
                value += qty * prices[symbol]
        return money(value)

    def exposure(self, prices, securities):
        return sum((qty * prices[symbol] * dec(securities[symbol].get("multiplier", 1)) for symbol, qty in self.positions.items()), ZERO)

    def margin(self, prices, securities):
        return sum((qty * prices[s] * dec(securities[s].get("multiplier", 1)) * dec(securities[s].get("initial_margin", 0)) for s, qty in self.positions.items() if securities[s].get("kind") == "future"), ZERO)

    def cash_events(self, day, session_index, days, annual_rate, flow):
        self.bought_today = {}
        if days and self.debt:
            charge = money(self.debt * annual_rate * dec(days) / dec(365))
            self.debit(charge)
            self.interest += charge
            self.event(day, "interest", amount=charge, calendar_days=days)
        for item in list(self.receivables):
            if item["pay_date"] <= day:
                self.credit(item["amount"])
                self.receivables.remove(item)
                self.event(day, "dividend_payment", **item)
        for item in list(self.unsettled):
            if item["due"] <= session_index:
                self.credit(item["amount"])
                self.unsettled.remove(item)
                self.event(day, "cash_settlement", **item)
        if flow:
            if flow < 0 and -flow > self.cash:
                raise ValueError("提款超过可用现金")
            if flow > 0:
                self.credit(flow)
            else:
                self.cash += flow
            self.event(day, "external_flow", amount=flow)

    def action(self, day, action):
        symbol = action["symbol"]
        qty = self.positions.get(symbol, ZERO)
        if action["type"] == "split":
            ratio = dec(action["ratio"])
            new_qty = qty * ratio
            if new_qty != new_qty.to_integral_value():
                raise ValueError("拆合股碎股现金对价未知，阻止无依据处理")
            self.positions[symbol] = new_qty
            if symbol in self.cost_basis:
                self.cost_basis[symbol] /= ratio
            self.event(day, "split", symbol=symbol, ratio=ratio, old_quantity=qty, new_quantity=new_qty)
        elif qty:
            amount = money(qty * dec(action["amount"]))
            self.receivables.append({"symbol": symbol, "amount": str(amount), "pay_date": action["pay_date"]})
            self.event(day, "dividend_receivable", symbol=symbol, amount=amount, pay_date=action["pay_date"])

    def settle_futures(self, day, rows, securities):
        for symbol, qty in self.positions.items():
            if qty and securities[symbol].get("kind") == "future":
                price = dec(rows[symbol]["settlement"])
                pnl = money(qty * dec(securities[symbol]["multiplier"]) * (price - self.futures_basis[symbol]))
                self.credit(pnl) if pnl >= 0 else self.debit(-pnl)
                self.futures_basis[symbol] = price
                self.event(day, "variation_margin", symbol=symbol, amount=pnl, settlement=price)
