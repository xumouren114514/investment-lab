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
    assert guard["status"] in ("not_used", "unavailable", "partial", "applied")
    assert guard["applied"] == result["metadata"]["limit_guard_applied"]
    assert guard["skipped"] == result["metadata"]["limit_guard_skipped"]
    assert guard["source"] == "synthetic", guard
