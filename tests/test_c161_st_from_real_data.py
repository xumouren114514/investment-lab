r"""C-161 复现：东财来源的 A 股 bar 不带 is_st，ST 5% 涨跌停规则在真实数据形态上失效。

现象（2026-10-30 实测）：
  · `open_sources.py:190-194` 的 Eastmoney 适配器构造 bar 时**不写 is_st**，也不写 preclose；
    只有 BaoStock 适配器（`:85-87`）会写 `is_st=row.get("isST")`。
  · `price_limit.py:82` 是 `security.get("is_st", bar.get("is_st"))`，而
    `open_acquire.py:44-49` 构造的 security **没有 is_st 字段**。
  · 于是东财来源的 ST 股：is_st 取到 None → 走 10% 而非 5%。

后果：preclose=10 的 ST 股当天真实只能涨到 10.50，引擎按 11.00 判涨停，
      认为 10.50「未触及涨停」→ **允许买入**，回测收益系统性偏高。
      这与守则 §13-3「保护必须在真实数据形态上验证」是同一类事故。

这条测试钉的是**缺陷本身**：现在会 FAIL，修好后应 PASS。
修法方向（需方案评审，不在本测试内决定）：
  A. 东财适配器把 is_st 写进 bar（东财 kline 接口没有该字段，需要另找来源）；
  B. security 里补 is_st（universe 层面标注）；
  C. 认账：ST 判定只在 BaoStock 来源可用，并在结果里标注「ST 规则未生效」。

跑法：.venv\Scripts\python -m pytest tests\test_c161_st_from_real_data.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investment_lab.engine.price_limit import limits_for_bar  # noqa: E402

# 真实形态：open_acquire.py:44-49 构造出来的 security（没有 is_st 键）
REAL_SECURITY = {
    "name": "某主板股票",
    "market": "CN",
    "currency": "CNY",
    "exchange": "XSHG",
    "kind": "stock",
    "universe_id": "600001",
    "lot": 100,
    "sell_delay": 1,
}


def test_real_security_has_no_is_st_key():
    """先确认前提：真实 security 确实不带 is_st，否则下面的测试就没意义。"""
    assert "is_st" not in REAL_SECURITY


def test_eastmoney_bar_has_no_is_st():
    """东财 bar 的真实形态：不写 is_st（这是缺陷的源头，钉住它防止悄悄变化）。"""
    from investment_lab.data.open_sources import Eastmoney  # noqa: F401
    src = (Path(__file__).resolve().parents[1] / "src" / "investment_lab" / "data" / "open_sources.py").read_text(encoding="utf-8")
    # 找到 Eastmoney.fetch 里构造 bar 的那段
    start = src.index("class Eastmoney")
    end = src.index("class ", start + 10) if "class " in src[start + 10:] else len(src)
    segment = src[start:start + 4000]
    assert "is_st" not in segment, (
        "Eastmoney 适配器开始写 is_st 了——如果修了这条缺陷，请同步更新本测试与 C-161 记录"
    )


@pytest.mark.xfail(
    strict=True,
    reason="C-161 已知缺陷：东财来源、security 与 bar 都没有 is_st，ST 股被按 10% 判涨跌停。"
           "修好后会 XPASS，届时把 xfail 去掉——这正是本测试存在的意义。",
)
def test_st_stock_hits_limit_at_true_5_percent_ceiling():
    """ST 股真实涨停 5%：preclose=10 → 涨停 10.50。

    东财 bar 里 high=10.50 就是打在涨停价上，应当判「触及涨停、不成交」。
    现在因为拿不到 is_st，按 10% 算成 11.00，认为没触及 → 放行成交。
    """
    bar = {"symbol": "sh.600001", "date": "2024-01-05", "open": "10.00", "high": "10.50",
           "low": "10.00", "close": "10.50", "volume": "1000000", "amount": "10500000",
           "preclose": "10.00"}  # 注意：没有 is_st（东财形态）
    up, down = limits_for_bar(REAL_SECURITY, bar, "10.00")
    assert up == limits_for_bar(REAL_SECURITY, {"symbol": "sh.600001", "is_st": "1"}, "10.00")[0], (
        "ST 股的涨停价应当按 5% 计算；东财来源拿不到 is_st，这条断言会失败"
    )


def test_non_st_main_board_still_10_percent():
    """对照组：普通主板股确实是 10%，防止修 ST 时误伤。"""
    bar = {"symbol": "sh.600001", "preclose": "10.00"}
    up, down = limits_for_bar(REAL_SECURITY, bar, "10.00")
    assert (up, down) == (__import__("decimal").Decimal("11.00"), __import__("decimal").Decimal("9.00"))
