r"""C-163 复现：除息日缺 preclose 时，价限基准用**除息前**收盘，一字涨停会被放行。

实测（2026-10-30，真 `simulate`，不是手算）：
    除息 1.00 元/股，6-03 收 10.00，真实前收 9.00，一字涨停 9.90
    limit_guard_applied=1  skipped=0  price_limit_guard=applied
    成交笔数=1    trade price=9.90    ← 一字涨停日被放行

根因：`account.action` 只对 `split` 调整 `prices`，分红除息**不调整**；
`_preclose_for` 因此回退到除息前的收盘价。

与 C-162 的区别（那次我判错了）：拆股当天 `prices[symbol] /= ratio` **确实**执行了，
守卫是好的；分红除息没有这一步，所以这条成立。

修法 A（本次）：除息日拿不到可靠前收时返回 None，让守卫判「算不出来」并计入
`limit_guard_skipped`。**不改变成交**（`_limit_verdict` 在 lim is None 时返回 (False, False)，
调用方本就按"未触发"处理），只让"这部分没被算"这件事可见。

跑法：.venv\Scripts\python -m pytest tests\test_c163_dividend_preclose.py -q
"""
import sys
from decimal import Decimal
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investment_lab.engine.core import _limit_verdict, _preclose_for  # noqa: E402

CN_SEC = {"market": "CN", "kind": "stock", "exchange": "XSHG", "universe_id": "600000"}
EX_DIV_BAR = {"symbol": "sh.600000", "date": "2024-06-04", "open": "9.90",
              "high": "9.90", "low": "9.90", "close": "9.90"}  # 东财形态：无 preclose


def _order(qty="100"):
    return SimpleNamespace(remaining=Decimal(qty))


def test_exdividend_day_without_preclose_is_not_guessed():
    """除息日、bar 无 preclose、引擎只有除息前收盘 → 必须返回 None。"""
    prices = {"sh.600000": Decimal("10.00")}  # 除息前收盘
    got = _preclose_for("sh.600000", EX_DIV_BAR, prices, {"2024-06-04"})
    assert got is None, (
        f"除息日拿不到可靠前收时必须返回 None，实际 {got}。"
        "用除息前收盘当基准会让涨停价虚高（10→11 vs 真实 9→9.90），导致一字涨停被判未触及。"
    )


def test_verdict_marks_skipped_on_exdividend_day():
    """方案 A 的可观测后果：这一天应当计入 skipped，而不是假装 guard 生效。"""
    touched, applied = _limit_verdict(_order(), CN_SEC, EX_DIV_BAR, None)
    assert applied is False, "算不出涨跌停时必须报「未应用守卫」，不能声称 applied"
    assert touched is False, "算不出来时按未触发处理（行为不变）"


def test_execution_result_is_unchanged():
    """关键约束：修法 A **不得改变成交判定**。"""
    with_wrong_base = _limit_verdict(_order(), CN_SEC, EX_DIV_BAR, Decimal("10.00"))[0]
    with_none = _limit_verdict(_order(), CN_SEC, EX_DIV_BAR, None)[0]
    assert with_wrong_base == with_none == False, (
        "两种基准下 touched 必须一致（都是 False）：这条改动只做披露，不碰撮合"
    )


def test_normal_day_unaffected():
    """非除息日行为不变，仍然用引擎记的上一收盘。"""
    prices = {"sh.600000": Decimal("9.90")}
    bar = dict(EX_DIV_BAR, date="2024-06-05")
    got = _preclose_for("sh.600000", bar, prices, {"2024-06-04"})
    assert str(got) == "9.90"


def test_exdividend_with_real_preclose_uses_it():
    """bar 自带真实 preclose（BaoStock 形态）时，除息日照常用它。"""
    prices = {"sh.600000": Decimal("10.00")}
    bar = dict(EX_DIV_BAR, preclose="9.00")
    got = _preclose_for("sh.600000", bar, prices, {"2024-06-04"})
    assert str(got) == "9.00"


def test_split_day_still_guarded():
    """C-162 的教训：拆股日 prices 已被引擎调整，这条改动不能把拆股日也判成算不出。

    拆股当天 `core.py` 会执行 `prices[symbol] /= ratio`，所以 prices 里是**除权后**价格，
    这是可靠基准。因此 `_preclose_for` 只有在「当日有非拆股公司行动」时才返回 None。
    """
    prices = {"sh.600000": Decimal("10.00")}  # 拆股后（20 / 2）
    bar = dict(EX_DIV_BAR, date="2024-06-04")
    got = _preclose_for("sh.600000", bar, prices, ex_dividend_days={"2024-06-04"},
                        ex_split_days={"2024-06-04"})
    assert str(got) == "10.00", "拆股日 prices 已折算，必须照常用它，不能误判为算不出"


def test_backward_compatible_default():
    """老调用点（不传任何集合）行为不变。"""
    prices = {"sh.600000": Decimal("10.00")}
    got = _preclose_for("sh.600000", EX_DIV_BAR, prices)
    assert str(got) == "10.00"
