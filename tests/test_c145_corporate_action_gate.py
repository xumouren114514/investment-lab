"""C-145（§14 第 3 条）：公司行动的 fail-closed。

用户裁决：未复权标的**不许进交易回测**；研究模式允许但必须标注。
门禁只在**有证据**时拒（区间内有公司行动记录，或出现 >25% 的无法解释跳变），
不是"一律拒绝"——那样等于把整个功能关掉。
"""
from copy import deepcopy

import pytest

from investment_lab.engine.core import preflight


def trading(manifest, bars, config, **over):
    from dataclasses import replace
    return preflight(manifest, bars, replace(config, **over))


def unverified(small, verified=False):
    payload = deepcopy(small)
    payload["securities"]["A"]["actions_verified"] = verified
    return payload


def test_unverified_with_recorded_action_is_rejected(small, config):
    payload = unverified(small)
    payload["actions"] = [{"symbol": "A", "date": "2024-01-08", "type": "split", "ratio": "2"}]
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    with pytest.raises(ValueError, match="公司行动未核实"):
        trading(manifest, payload["bars"], config)


def test_unverified_with_unexplained_jump_is_rejected(small, config):
    payload = unverified(small)
    for bar in payload["bars"]:
        if bar["date"] == "2024-01-08":
            bar.update(close="20", high="20", low="20", vwap_value="20")   # 单日 +100%
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    with pytest.raises(ValueError, match="无法解释的"):
        trading(manifest, payload["bars"], config)


def test_unverified_without_evidence_passes(small, config):
    payload = unverified(small)
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    assert trading(manifest, payload["bars"], config)      # 不抛就是通过


def test_verified_actions_pass(small, config):
    payload = deepcopy(small)      # actions_verified=True
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    assert trading(manifest, payload["bars"], config)


def test_research_mode_is_allowed_and_marked(small, config):
    from dataclasses import replace
    from investment_lab.engine.core import simulate

    payload = unverified(small)
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    research = replace(config, mode="close_research")
    from types import SimpleNamespace
    result = simulate(manifest, payload["bars"], research, SimpleNamespace(on_session=lambda ctx: None))
    meta = result["metadata"]
    assert meta["corporate_action_gate"] == "research_mode"
    assert meta["unadjusted_symbols"] == ["A"], "研究模式必须把未核实的标的写出来"
