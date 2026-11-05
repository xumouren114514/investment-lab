"""C-142（§14 第 1 条）：把「价限守卫有没有真的生效」写进 run 报告。

原来 metadata 里只有 applied / skipped 两个数字——没人会去算比例，而"这段成交没有价限保护"
恰恰是最该一眼看见的事。现在多一个 price_limit_guard：status + source + 一句人话。
"""
from types import SimpleNamespace

from investment_lab.engine.price_limit import guard_report


def test_four_statuses():
    assert guard_report(0, 0)["status"] == "not_used"
    assert guard_report(0, 5)["status"] == "unavailable"
    assert guard_report(5, 0)["status"] == "applied"
    partial = guard_report(3, 2)
    assert partial["status"] == "partial"
    assert "2/5" in partial["note"]


def test_unavailable_says_what_is_missing():
    note = guard_report(0, 3)["note"]
    assert "preclose" in note and "没有价限保护" in note, note


def test_source_name_is_carried():
    assert guard_report(1, 0, {"provider": "BaoStock/daily"})["source"] == "BaoStock/daily"
    assert guard_report(1, 0, {"name": "合成"})["source"] == "合成"
    assert guard_report(1, 0, "Tushare/daily")["source"] == "Tushare/daily"
    assert guard_report(1, 0)["source"] == "unknown"


def test_metadata_exposes_guard_report(small, config):
    """接线验证：不依赖任何成交，只看 metadata 里有没有这句话、对不对得上计数。"""
    from investment_lab.engine.core import simulate
    manifest = {k: v for k, v in small.items() if k != "bars"}
    result = simulate(manifest, small["bars"], config, SimpleNamespace(on_session=lambda ctx: None))
    guard = result["metadata"]["price_limit_guard"]
    # ★ 2026-11-05 补强（弱断言扫描的独立甄别）：原来这里是
    #   `assert guard["status"] in ("not_used", "unavailable", "partial", "applied")`，
    #   而**这四个值恰好是 guard_report() 能返回的全部取值** —— 同义反复，永远不会红。
    #   这个 fixture 的成交根本不触碰涨跌停（applied=0 / skipped=0），正确答案只有一个：not_used。
    assert guard["status"] == "not_used", guard
    # ★ 2026-11-05 独立审查拆穿：这里我原先还写了一句"用 metadata 自己的计数重算一遍比对"，
    #   看着像交叉校验，其实是**恒等式** —— core.py 的 metadata 本来就是
    #   `guard_report(applied, skipped)` 生成的，而上面那句已经钉死 status=="not_used"
    #   （等价于 applied+skipped==0），于是它退化成 `guard_report(0,0)=="not_used"`，
    #   而这一点 test_four_statuses 早就覆盖了。
    #   真要有独立价值，得用**碰过涨跌停的 fixture**（applied>0 或 skipped>0）来验；
    #   那需要造场景，属于另一件事。这里宁可删掉，也不留一条看起来更严格、实际不增加覆盖的断言。
    assert guard["applied"] + guard["skipped"] == 0, ("not_used 应当没有任何订单-交易日尝试", guard)
    assert guard["applied"] == result["metadata"]["limit_guard_applied"]
    assert guard["skipped"] == result["metadata"]["limit_guard_skipped"]
    assert guard["source"] == "synthetic", guard


class _BuyOnce:
    """第一天买一次，第二天成交 —— 没有成交就没有"订单-交易日尝试"，guard 永远是 not_used。"""

    def __init__(self):
        self.n = 0

    def on_session(self, ctx):
        self.n += 1
        if self.n == 1:
            ctx.order_shares("A", 10, "测试：第一天买入")


def _run_with_a_fill(small, config, *, computable: bool):
    from copy import deepcopy

    from investment_lab.engine.core import simulate

    payload = deepcopy(small)
    if computable:
        # limit_ratio 是给"符号不是 6 位代码"的合成数据用的入口；preclose 是算涨跌停的基准。
        payload["securities"]["A"]["limit_ratio"] = "0.1"
        for b in payload["bars"]:
            b["preclose"] = b["close"]
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    return simulate(manifest, payload["bars"], config, _BuyOnce())


def test_metadata_wiring_with_a_real_fill_is_applied(small, config):
    """真有成交、且涨跌停算得出来 ⇒ metadata 里必须是 applied（不是 not_used）。

    ★ 2026-11-05 第二次独立审查指出：原来这个文件只测过 `not_used`（`small` 根本不交易），
      所以"接线接反了 / status 写死了"都测不出来。这条补上。
    """
    meta = _run_with_a_fill(small, config, computable=True)["metadata"]
    guard = meta["price_limit_guard"]
    print(f"（信息）applied={meta.get('limit_guard_applied')} skipped={meta.get('limit_guard_skipped')} status={guard['status']}")
    assert meta.get("limit_guard_applied", 0) >= 1, ("这一期应当至少有一次价限判定", meta.get("limit_guard_applied"))
    assert guard["status"] == "applied", guard


def test_metadata_wiring_without_a_computable_limit_is_unavailable(small, config):
    """有成交但算不出涨跌停（缺 preclose / 无 limit_ratio）⇒ 必须是 unavailable，不许假装有守卫。"""
    meta = _run_with_a_fill(small, config, computable=False)["metadata"]
    guard = meta["price_limit_guard"]
    print(f"（信息）applied={meta.get('limit_guard_applied')} skipped={meta.get('limit_guard_skipped')} status={guard['status']}")
    assert meta.get("limit_guard_skipped", 0) >= 1, ("这一期应当至少有一次算不出价限", meta.get("limit_guard_skipped"))
    assert guard["status"] == "unavailable", guard


def test_result_view_renders_trust_block():
    """C-142/C-145/C-146：metadata 里的判决必须显示出来，否则等于没做。"""
    from pathlib import Path as _P
    static = _P(__file__).resolve().parents[1] / "src" / "investment_lab" / "web" / "static"
    views = (static / "result-views.js").read_text(encoding="utf-8")
    runs = (static / "view-runs.js").read_text(encoding="utf-8")
    assert "function trustBlock(" in views, "结果视图里没有 trustBlock"
    assert "trustBlock," in views, "trustBlock 没有导出"
    assert "Results.trustBlock(metadata)" in runs, "运行结果页没有调用 trustBlock"
    for field in ("price_limit_guard", "corporate_action_gate", "warmup_flat_summary", "unadjusted_symbols"):
        assert field in views, field
