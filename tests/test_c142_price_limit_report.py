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
    # 再交叉验一次：metadata 的 status 必须与「用它自己那两个计数重新算」一致
    # （防止 metadata 里的 status 被写死、而计数是另一套）。
    assert guard_report(guard["applied"], guard["skipped"])["status"] == guard["status"], guard
    assert guard["applied"] == result["metadata"]["limit_guard_applied"]
    assert guard["skipped"] == result["metadata"]["limit_guard_skipped"]
    assert guard["source"] == "synthetic", guard


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
