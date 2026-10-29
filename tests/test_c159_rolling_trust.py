"""C-159：滚动结果顶层 metadata 要带上各窗口汇总的可信度字段。

每个窗口的结果里都有 `price_limit_guard` 等，但如果汇总层不给，结果页的可信度块就是空的。
"""
from types import SimpleNamespace

from investment_lab.engine.models import Config
from investment_lab.research.experiments import rolling


def test_rolling_metadata_aggregates_trust_fields(small):
    manifest = {k: v for k, v in small.items() if k != "bars"}
    config = Config(start=small["sessions"][0], end=small["sessions"][-1], symbols=["A"],
                    initial_cash="1000", commission_bps="0", slippage_bps="0", max_participation="1",
                    warmup=2, allow_unverified_actions=True)

    def factory():
        return SimpleNamespace(on_session=lambda ctx: None)

    result = rolling(manifest, small["bars"], config, factory, {}, interval="day", horizon=3)
    meta = result["metadata"]
    guard = meta.get("price_limit_guard")
    assert guard, meta.keys()
    assert guard["status"] in ("applied", "partial", "unavailable", "not_used")
    assert sum(guard["windows"].values()) >= 1
    assert meta.get("corporate_action_gate") in ("fail_closed", "explicit_assumption", "research_mode", "mixed")
    assert "携转" in (meta.get("warmup_flat_summary") or "")
