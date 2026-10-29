"""C-141（§14 第 5 条）：数据质量不变量 + 覆盖率。

三条：停牌日量额为 0、preclose 与前收一致、异常跳变必须能被公司行动解释。
每条都要区分 checked / uncovered / violations——**"没查"不能看起来像"查过且没问题"**。
"""
from investment_lab.data.validation import invariants

MANIFEST = {"sessions": ["2026-01-05", "2026-01-06", "2026-01-07"], "securities": {}, "actions": []}


def bar(day, close, *, volume="0", amount="0", status="trading", preclose=None, symbol="CN:600519"):
    item = {"symbol": symbol, "date": day, "open": close, "high": close, "low": close, "close": close,
            "volume": volume, "amount": amount, "status": status,
            "available_at": day + "T15:00:00+08:00", "source": "t"}
    if preclose is not None:
        item["preclose"] = preclose
    return item


def test_suspended_with_volume_is_a_violation():
    bars = [bar("2026-01-05", "10", status="suspended", volume="1000")]
    rep = invariants(MANIFEST, bars)
    assert rep["suspended_zero_volume"]["checked"] == 1
    assert rep["suspended_zero_volume"]["violation_count"] == 1
    assert "停牌但量/额非零" in rep["suspended_zero_volume"]["violations"][0]
    assert rep["summary"]["ok"] is False


def test_suspended_zero_and_missing_status_counted_differently():
    bars = [bar("2026-01-05", "10", status="suspended"),
            {**bar("2026-01-06", "10"), "status": None}]
    rep = invariants(MANIFEST, bars)
    assert rep["suspended_zero_volume"]["checked"] == 1
    assert rep["suspended_zero_volume"]["uncovered"] == 1, "没有 status 的 bar 应计入 uncovered，而不是算查过"
    assert rep["suspended_zero_volume"]["violation_count"] == 0


def test_preclose_must_match_previous_close_but_exempt_on_action_day():
    ok = [bar("2026-01-05", "10"), bar("2026-01-06", "10.5", preclose="10")]
    assert invariants(MANIFEST, ok)["preclose_consistency"]["violation_count"] == 0

    bad = [bar("2026-01-05", "10"), bar("2026-01-06", "10.5", preclose="9")]
    rep = invariants(MANIFEST, bad)
    assert rep["preclose_consistency"]["violation_count"] == 1
    assert "preclose" in rep["preclose_consistency"]["violations"][0]

    # 除权日：参考价本来就不等于昨收，不该报
    manifest = {**MANIFEST, "actions": [{"symbol": "CN:600519", "date": "2026-01-06",
                                         "type": "dividend", "amount": "0.5", "pay_date": "2026-01-10"}]}
    rep = invariants(manifest, bad)
    assert rep["preclose_consistency"]["violation_count"] == 0, "有公司行动的当天必须豁免"


def test_missing_preclose_is_uncovered_not_clean():
    bars = [bar("2026-01-05", "10"), bar("2026-01-06", "10.5")]   # 两根都没有 preclose
    rep = invariants(MANIFEST, bars)
    assert rep["preclose_consistency"]["checked"] == 0
    assert rep["preclose_consistency"]["uncovered"] == 1, "逐对比较只对第二根及以后计数"
    assert rep["preclose_consistency"]["coverage"] == 0


def test_big_jump_without_action_is_a_violation_with_action_is_explained():
    plain = [bar("2026-01-05", "10"), bar("2026-01-06", "13")]      # +30%
    rep = invariants(MANIFEST, plain)
    assert rep["unexplained_jumps"]["violation_count"] == 1
    assert "+30.0%" in rep["unexplained_jumps"]["violations"][0]

    manifest = {**MANIFEST, "actions": [{"symbol": "CN:600519", "date": "2026-01-06",
                                         "type": "split", "ratio": "2"}]}
    rep = invariants(manifest, plain)
    assert rep["unexplained_jumps"]["violation_count"] == 0
    assert rep["unexplained_jumps"]["explained"] == 1
    assert rep["summary"]["ok"] is True


def test_small_moves_are_checked_but_not_flagged():
    bars = [bar("2026-01-05", "10"), bar("2026-01-06", "10.5")]
    rep = invariants(MANIFEST, bars)
    assert rep["unexplained_jumps"]["checked"] == 1
    assert rep["unexplained_jumps"]["violation_count"] == 0
    assert rep["unexplained_jumps"]["coverage"] == 1.0


def test_coverage_panel_shows_invariants():
    """C-141：接口返回了 invariants，面板必须真的显示——否则等于没做。"""
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "src" / "investment_lab" / "web" / "static"
          / "view-manage.js").read_text(encoding="utf-8")
    assert "report.invariants" in js and "未覆盖 = 没数据可查" in js
    assert "available_at_basis" in js
