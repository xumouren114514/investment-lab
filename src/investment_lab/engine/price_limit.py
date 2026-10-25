"""涨跌停价推算（2026-10-26，用户裁决"按我的方案"）。

引擎里原来有一处守卫：买单遇到 `bar["limit_up"]` 且当日最高价触及就不成交。但全仓没有任何
代码产出这两个字段，等于守卫从未生效——涨停日照常按收盘价买入，回测收益系统性偏高。

这里按「前收盘价 × 板块幅度」现算，不写进行情数据（写进 bar 会改 manifest 摘要、进而改快照编号）。

口径（我的决定，用户已授权）：
  · 主板（60/00）10%，创业板/科创板（30/688）20%，北交所（4x/8x/92）30%；
  · 主板 ST 5%；创业板/科创板 ST 仍按 20%；北交所 ST 仍 30%；
  · 四舍五入到分（`ROUND_HALF_UP`）；
  · 算不出来（缺 preclose、ETF/基金/可转债/期货、未知板块）返回 None——调用方保持"按现价成交"，
    不假装有守卫。
"""
from decimal import Decimal, ROUND_HALF_UP

from investment_lab.common import dec   # 与引擎其余部分共用 Decimal 口径

CENT = Decimal("0.01")


def _ratio(symbol: str) -> Decimal | None:
    s = str(symbol or "")
    if s.startswith(("688", "689", "300", "301")):
        return Decimal("0.20")
    if s.startswith(("43", "83", "87", "92")):
        return Decimal("0.30")
    if s.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        return Decimal("0.10")
    return None


def limits_for(symbol: str, preclose, is_st) -> tuple[Decimal, Decimal] | None:
    """返回 (涨停价, 跌停价)；无法可靠推算时返回 None。"""
    ratio = _ratio(symbol)
    if ratio is None:
        return None
    try:
        base = dec(preclose)
    except Exception:  # noqa: BLE001 —— 空值/坏值都当作"算不出来"
        return None
    if base is None or base <= 0:
        return None
    if bool(is_st) and str(symbol).startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
        ratio = Decimal("0.05")
    up = (base * (1 + ratio)).quantize(CENT, rounding=ROUND_HALF_UP)
    down = (base * (1 - ratio)).quantize(CENT, rounding=ROUND_HALF_UP)
    return up, down


def limits_for_bar(security: dict, bar: dict) -> tuple[Decimal, Decimal] | None:
    """只有股票才有涨跌停；ETF/基金/可转债/期货一律不推算。"""
    kind = (security or {}).get("kind")
    if kind not in (None, "stock"):
        return None
    symbol = (security or {}).get("symbol") or bar.get("symbol")
    return limits_for(symbol, bar.get("preclose"), bar.get("is_st"))
