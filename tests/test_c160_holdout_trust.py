"""C-160：留出法结果的顶层 metadata 也要带可信度汇总（与 C-159 的滚动同一个缺口）。"""
from types import SimpleNamespace

from investment_lab.engine.models import Config
from investment_lab.research.experiments import holdout


def test_holdout_metadata_aggregates_trust_fields(small):
    manifest = {k: v for k, v in small.items() if k != "bars"}
    sessions = small["sessions"]
    config = Config(start=sessions[1], end=sessions[-1], symbols=["A"], initial_cash="1000",
                    commission_bps="0", slippage_bps="0", max_participation="1",
                    warmup=1, allow_unverified_actions=True)

    def factory():
        return SimpleNamespace(on_session=lambda ctx: None)

    result = holdout(manifest, small["bars"], config, factory, {}, test_start=sessions[3])
    meta = result["metadata"]
    assert meta.get("price_limit_guard"), meta.keys()
    assert meta["price_limit_guard"]["status"] in ("applied", "partial", "unavailable", "not_used")
    assert meta.get("corporate_action_gate") in ("fail_closed", "explicit_assumption", "research_mode", "mixed")
    assert "携转" in (meta.get("warmup_flat_summary") or "")
    assert meta.get("test_start") == sessions[3], "原有研究字段不能被覆盖掉"
