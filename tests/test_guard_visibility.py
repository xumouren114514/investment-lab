"""价限守卫的可见性（2026-10-28）：metadata 必须报告「应用了几次 / 跳过了几次」。

背景：`price_limit.py` 早就实现了涨跌停推算（23 条测试），但方案里要求的
`limit_guard_skipped` 计数一直没做——也就是说「这段回测到底有没有应用价限」
在结果里看不出来。不知道有没有守卫，就等于没有。

这两条测试钉住：
1. 算不出涨跌停时（US 标的、非 6 位 A 股代码）→ `limit_guard_skipped > 0`；
2. 给了 A 股代码与前收 → `limit_guard_applied > 0`。
"""
from copy import deepcopy
from types import SimpleNamespace

from conftest import run_small


def once(quantity):
    def session(ctx):
        if not ctx.state.get("sent"):
            ctx.order_shares("A", quantity, "手算")
            ctx.state["sent"] = True

    return SimpleNamespace(on_session=session)


def test_metadata_reports_guard_counts_and_skips_non_cn(small, config):
    result = run_small(small, config, once(50))
    meta = result["metadata"]

    assert "limit_guard_applied" in meta, "metadata 必须报告价限是否真的应用过"
    assert "limit_guard_skipped" in meta, "metadata 必须报告没算出来的次数"
    # small 里的 "A" 是 US 标的、不是 6 位 A 股代码 → 涨跌停算不出来 → 只能计跳过
    assert meta["limit_guard_skipped"] > 0, meta
    assert meta["limit_guard_applied"] == 0, meta


def test_guard_applied_for_cn_code_with_preclose(small, config):
    payload = deepcopy(small)
    payload["securities"]["A"]["symbol"] = "600519.SH"
    for bar in payload["bars"]:
        bar["preclose"] = "10"

    result = run_small(payload, config, once(50))
    meta = result["metadata"]

    assert meta["limit_guard_applied"] > 0, f"有 A 股代码 + 前收时应当应用价限：{meta}"
    assert meta["limit_guard_skipped"] == 0, meta


def test_touching_limit_up_still_rejects_the_order(small, config):
    """守卫的行为本身没变：high 触及涨停价时买单不成交（这是原有语义，别被计数搞坏）。"""
    payload = deepcopy(small)
    payload["securities"]["A"]["symbol"] = "600519.SH"
    for bar in payload["bars"]:
        bar["preclose"] = "10"      # 涨停价 = 11.00，而 high 就是 11

    result = run_small(payload, config, once(50))
    assert result["trades"] == [], "触及涨停价时不该成交"
    assert result["metadata"]["limit_guard_applied"] > 0, result["metadata"]
