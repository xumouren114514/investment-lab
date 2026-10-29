r"""ENGINE.md 的价限口径表必须与 `price_limit.py` 的实际行为一致。

为什么值得守：`docs/ENGINE.md` 里本来**一个字都没提高涨跌停幅度**（「ST」出现 0 次），
而 `price_limit.py` 有一整套 10/20/30/5% 的规则。文档与代码各说各话，读者只能猜。
2026-10-30 补了「价限守卫」一节，这条测试把那张表钉在代码上——
**以后谁改了幅度却没改文档，这里立刻红。**

跑法：.venv\Scripts\python -m pytest tests\test_c164_limit_ratio_doc.py -q
"""
import sys
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investment_lab.engine.price_limit import limits_for  # noqa: E402

DOC = Path(__file__).resolve().parents[1] / "docs" / "ENGINE.md"
PRECLOSE = "10.00"

# (symbol, is_st, 期望涨跌停, 板块说明, 文档表格里必须出现的那几个数字)
CASES = [
    ("sh.600519", None, (D("11.00"), D("9.00")), "主板沪", ("11.00", "9.00")),
    ("sz.000001", None, (D("11.00"), D("9.00")), "主板深", ("11.00", "9.00")),
    ("sz.300750", None, (D("12.00"), D("8.00")), "创业板", ("12.00", "8.00")),
    ("sh.688981", None, (D("12.00"), D("8.00")), "科创板", ("12.00", "8.00")),
    ("bj.430047", None, (D("13.00"), D("7.00")), "北交所", ("13.00", "7.00")),
    ("sh.600519", "1", (D("10.50"), D("9.50")), "主板ST", ("10.50", "9.50")),
    ("sz.300750", "1", (D("12.00"), D("8.00")), "创业板ST仍20%", ("12.00", "8.00")),
]


def _doc_text() -> str:
    return DOC.read_text(encoding="utf-8")


def test_doc_has_a_limit_guard_section():
    text = _doc_text()
    assert "价限守卫" in text, (
        "ENGINE.md 没有「价限守卫」一节——涨跌停口径又回到只在代码里、文档只字不提的状态"
    )


def test_doc_mentions_st_rule():
    """ST 5% 是最容易被忽略的一条，也最容易在改幅度时漏改文档。"""
    text = _doc_text()
    assert "ST" in text, "ENGINE.md 完全没提 ST 规则"
    assert "5%" in text, "ENGINE.md 没写主板 ST 的 5% 幅度"


def test_every_ratio_matches_code():
    """逐条比对：代码算出来的涨跌停数字，必须原样出现在文档里。"""
    text = _doc_text()
    for symbol, is_st, expect, desc, must_appear in CASES:
        got = limits_for(symbol, PRECLOSE, is_st)
        assert got == expect, f"{desc} 的代码行为变了：期望 {expect}，实际 {got}"
        for num in must_appear:
            assert num in text, (
                f"{desc} 在文档里找不到 {num}。代码算出来是 {got}（前收 {PRECLOSE}），"
                "改了口径就得同步改文档，否则读者只能猜。"
            )


def test_doc_says_unavailable_is_not_guarded():
    """文档必须说清「算不出来 ≠ 有保护」，这正是 C-142/C-161/C-163 反复强调的事。"""
    text = _doc_text()
    assert "unavailable" in text, "文档没提 unavailable 判决"
    assert "没有价限保护" in text, "文档没说清 unavailable 意味着这段成交没有价限保护"


def test_doc_mentions_ex_dividend_caveat():
    """C-163 的口径：除息日缺前收时不拿除息前价格硬算。"""
    text = _doc_text()
    assert "除息" in text or "分红" in text, (
        "文档没提除息日的前收问题——这正是 2026-10-30 C-163 修掉的那个坑"
    )
