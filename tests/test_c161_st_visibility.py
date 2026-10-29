r"""C-161 修法 C：把「ST 5% 规则是否真的生效」变成结果里的一句可见说明。

背景见 `问题条目_C161_投资ST涨跌停.md`。修法 C 不改变任何计算，只让
「ST 规则在这些数据上算不出来」这件事可见——守则 §14-2：答不出「什么情况下会红」的判据不算写完，
反过来，**静默失效的保护**比没有保护更糟。

约定：
  · `st_rule_coverage.status`
      "verified"   —— 本次做过 ST 判定，且数据带 ST 标记（is_st 取到了值）；
      "unknown"    —— 做过 ST 判定，但**数据里没有 ST 标记**，无法区分 ST/非 ST；
      "not_used"   —— 本次没有任何主板股票尝试，谈不上这条规则。
  · 无论哪个状态，**都不改变成交与收益**；只是披露。

跑法：.venv\Scripts\python -m pytest tests\test_c161_st_visibility.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from investment_lab.engine.price_limit import st_rule_report  # noqa: E402


def test_unknown_when_no_st_flag_seen():
    """真实形态：东财数据里没有 is_st —— 必须报 unknown，不能假装规则生效。"""
    report = st_rule_report(main_board_checked=120, st_flag_seen=0)
    assert report["status"] == "unknown"
    assert "ST" in report["note"]


def test_verified_when_st_flag_present():
    report = st_rule_report(main_board_checked=120, st_flag_seen=120)
    assert report["status"] == "verified"


def test_not_used_when_no_main_board_attempt():
    report = st_rule_report(main_board_checked=0, st_flag_seen=0)
    assert report["status"] == "not_used"


def test_partial_when_some_bars_have_flag():
    """一根带、一根不带（数据混合来源）——不能报 verified，否则等于给半份数据背书。"""
    report = st_rule_report(main_board_checked=100, st_flag_seen=40)
    assert report["status"] == "partial"
    assert "40" in report["note"] and "100" in report["note"]


def test_counts_are_int_and_never_negative():
    report = st_rule_report(main_board_checked=-5, st_flag_seen=None)
    assert report["main_board_checked"] == 0
    assert report["st_flag_seen"] == 0


def test_result_metadata_exposes_it():
    """跑一次最小回测，结果 metadata 里必须出现这个字段。

    造的是**真实形态**：主板 CN 股票、数据里没有 is_st（东财口径），
    于是 ST 规则在这批数据上不可观测，状态必须是 unknown。
    """
    from copy import deepcopy

    from investment_lab.engine.core import simulate
    from investment_lab.engine.models import Config

    days = ["2024-01-04", "2024-01-05", "2024-01-08"]
    sec = {"sh.600001": {"name": "主板样本", "market": "CN", "currency": "CNY", "timezone": "Asia/Shanghai",
                         "kind": "stock", "lot": 100, "listed": "2024-01-04", "listing_verified": True,
                         "actions_verified": True, "margin_eligible": False, "sell_delay": 0,
                         "exchange": "XSHG", "universe_id": "600001"}}
    bars = [{"symbol": "sh.600001", "date": d, "open": "10", "high": "11", "low": "9", "close": "10",
             "volume": "100000", "amount": "1000000", "vwap_value": "10",
             "vwap_method": "amount_volume_verified", "vwap_session": "full", "quality_status": "verified",
             "source": "test", "available_at": d + "T07:00:00+00:00", "status": "trading",
             "preclose": "10"} for d in days]  # 注意：没有 is_st（东财形态）
    manifest = {"name": "c161", "securities": deepcopy(sec), "sessions": days, "actions": [],
                "synthetic": False, "source": {"provider": "eastmoney"}}
    config = Config(start=days[0], end=days[-1], symbols=["sh.600001"], initial_cash="100000",
                    commission_bps="0", slippage_bps="0", max_participation="1")

    class BuyOnce:
        def on_session(self, ctx):
            if not ctx.state.get("sent"):
                ctx.order_shares("sh.600001", 100, "C-161 探针")
                ctx.state["sent"] = True

    result = simulate(manifest, bars, config, BuyOnce())
    meta = result["metadata"]
    assert "st_rule_coverage" in meta, (
        "结果 metadata 里没有 st_rule_coverage：ST 规则是否生效仍然不可见（C-161 修法 C 未落地）"
    )
    report = meta["st_rule_coverage"]
    assert report["status"] == "unknown", (
        f"主板股票、数据无 is_st，应报 unknown，实际 {report['status']}——等于在给没有数据支撑的规则背书"
    )
    assert report["main_board_checked"] > 0
