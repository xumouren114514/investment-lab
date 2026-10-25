"""涨跌停价推算（2026-10-26）与引擎守卫接线。

口径：主板 10%、创业板/科创板 20%、北交所 30%、主板 ST 5%、四舍五入到分；
ETF/基金/期货与缺 preclose 一律返回 None（不假装有守卫）。
"""
from decimal import Decimal
from types import SimpleNamespace

import pytest

from investment_lab.engine import price_limit as pl


@pytest.mark.parametrize("symbol,preclose,is_st,expect", [
    ("600000", "10.00", False, ("11.00", "9.00")),
    ("000001", "10.00", False, ("11.00", "9.00")),
    ("300750", "10.00", False, ("12.00", "8.00")),
    ("688981", "10.00", False, ("12.00", "8.00")),
    ("830799", "10.00", False, ("13.00", "7.00")),
    ("600000", "10.00", True, ("10.50", "9.50")),
    ("300750", "10.00", True, ("12.00", "8.00")),   # 创业 ST 仍 20%
    ("600000", "10.01", False, ("11.01", "9.01")),  # 四舍五入到分
])
def test_limits_for(symbol, preclose, is_st, expect):
    got = pl.limits_for(symbol, preclose, is_st)
    assert got == (Decimal(expect[0]), Decimal(expect[1]))


@pytest.mark.parametrize("symbol,preclose,is_st", [
    ("510300", "4.00", False),      # ETF 代码段不在股票范围
    ("113050", "100.00", False),    # 可转债
    ("600000", None, False),        # 缺前收
    ("600000", "0", False),         # 坏前收
])
def test_unavailable_returns_none(symbol, preclose, is_st):
    assert pl.limits_for(symbol, preclose, is_st) is None


def test_bar_helper_skips_non_stock():
    bar = {"preclose": "10.00", "is_st": False, "high": "11.00", "low": "9.00", "symbol": "600000"}
    assert pl.limits_for_bar({"kind": "stock", "symbol": "600000"}, bar) == (Decimal("11.00"), Decimal("9.00"))
    assert pl.limits_for_bar({"kind": "etf", "symbol": "600000"}, bar) is None
    assert pl.limits_for_bar({"kind": "future", "symbol": "600000"}, bar) is None


def test_engine_guard_reacts_to_computed_limit():
    from investment_lab.engine import core
    security = {"kind": "stock", "symbol": "600000"}
    bar = {"preclose": "10.00", "is_st": False, "high": "11.00", "low": "9.50", "symbol": "600000"}
    buy = SimpleNamespace(remaining=100)
    sell = SimpleNamespace(remaining=-100)
    assert core._touched_limit(buy, security, bar) is True     # 收在涨停价
    assert core._touched_limit(sell, security, bar) is False
    bar2 = dict(bar, high="10.99")
    assert core._touched_limit(buy, security, bar2) is False
