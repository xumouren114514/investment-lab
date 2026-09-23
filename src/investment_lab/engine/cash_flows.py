from datetime import date
from decimal import Decimal

from investment_lab.common import dec, money


MONTHLY_RULE = "每月在本回测区间内的首个交易日入金，首月计入；与同日单独资金流相加"


def validate_cash_flows(flows):
    if not isinstance(flows, dict):
        raise ValueError('入金/提款须为 JSON 对象，例如 {"monthly":3000}')
    for day, value in flows.items():
        if day != "monthly":
            try:
                if date.fromisoformat(day).isoformat() != day:
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError(f"资金流字段 {day!r} 无效；只支持 monthly 或 YYYY-MM-DD 日期") from None
        try:
            if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
                raise ValueError()
            amount = dec(value)
        except (ValueError, TypeError, ArithmeticError):
            raise ValueError(f"资金流 {day} 的金额必须是有限数字") from None
        if day == "monthly":
            try:
                valid = amount > 0 and amount == money(amount)
            except ArithmeticError:
                valid = False
            if not valid:
                raise ValueError("monthly 每月入金金额必须大于0且最多两位小数；停用请删除 monthly 字段")


def resolve_cash_flows(flows, sessions, start, end):
    """Resolve from the frozen calendar of this individual research interval."""
    validate_cash_flows(flows)
    days = sorted({day for day in sessions if start <= day <= end})
    available = set(days)
    resolved = {}
    for day, value in flows.items():
        if day == "monthly":
            continue
        if day not in available:
            raise ValueError(f"入金/提款日期 {day} 必须在本次交易日内，请明确休市日归属")
        resolved[day] = dec(value)
    if "monthly" in flows:
        amount = dec(flows["monthly"])
        seen = set()
        for day in days:
            month = day[:7]
            if month not in seen:
                resolved[day] = resolved.get(day, dec(0)) + amount
                seen.add(month)
    return {day: str(money(amount)) for day, amount in sorted(resolved.items())}
