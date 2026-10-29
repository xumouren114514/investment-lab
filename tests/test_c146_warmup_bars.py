"""C-146（§14 第 2 条）：预热窗口的「携转假 bar」要能被看见，但不参与计算。

用户裁决：先量化 + 标注，暂不在预热/估值入口过滤停牌 bar（会改所有历史指标）。
"""
from copy import deepcopy
from types import SimpleNamespace

from investment_lab.engine.core import simulate


def base(small):
    payload = deepcopy(small)
    days = ["2023-12-28", "2023-12-29"] + payload["sessions"]
    payload["sessions"] = days
    template = payload["bars"][0]
    extra = []
    extra.append({**template, "date": days[0], "close": "10", "high": "10", "low": "10", "open": "10",
                  "vwap_value": "10", "volume": "0", "amount": "0", "status": "suspended"})
    extra.append({**template, "date": days[1], "close": "10", "high": "10", "low": "10", "open": "10",
                  "vwap_value": "10", "volume": "0", "amount": "0", "status": "trading"})   # 零成交且与前收相同
    payload["bars"] = extra + payload["bars"]
    return payload


def test_warmup_flat_bars_are_counted(small, config):
    payload = base(small)
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    # 把回测起点推到新加的两天之后，让它们成为"预热"；warmup 允许 2 根
    from dataclasses import replace
    cfg = replace(config, start=payload["sessions"][2], warmup=2)
    result = simulate(manifest, payload["bars"], cfg, SimpleNamespace(on_session=lambda ctx: None))
    meta = result["metadata"]
    info = meta["warmup_bars"]["A"]
    assert info == {"bars": 2, "flat": 2, "ratio": 1.0}, info
    assert meta["warmup_flat_summary"] == "A: 2/2 (100%)"


def test_warmup_report_is_empty_when_no_warmup(small, config):
    payload = deepcopy(small)
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    result = simulate(manifest, payload["bars"], config, SimpleNamespace(on_session=lambda ctx: None))
    info = result["metadata"]["warmup_bars"]["A"]
    assert info["bars"] == 0 and info["ratio"] is None


def test_warmup_counting_does_not_change_metrics(small, config):
    """关键：携转 bar 只被"数"、不被"用"——把预热段改成正常成交，指标必须一模一样。"""
    from dataclasses import replace

    flat = base(small)
    live = deepcopy(flat)
    for bar in live["bars"][:2]:
        bar.update(status="trading", volume="5000", amount="50000")
    cfg = replace(config, start=flat["sessions"][2], warmup=2)
    flat_result = simulate({k: v for k, v in flat.items() if k != "bars"}, flat["bars"], cfg,
                           SimpleNamespace(on_session=lambda ctx: None))
    live_result = simulate({k: v for k, v in live.items() if k != "bars"}, live["bars"], cfg,
                           SimpleNamespace(on_session=lambda ctx: None))
    assert flat_result["metrics"] == live_result["metrics"], "预热段的携转与否不能改变任何指标"
    assert flat_result["metadata"]["warmup_bars"]["A"]["flat"] == 2
    assert live_result["metadata"]["warmup_bars"]["A"]["flat"] == 0

def test_only_the_configured_warmup_depth_is_counted(small, config):
    """C-146 修正：窗口之前有 3 根、但 warmup=2 时只数 2 根——策略看不到的那根不该算进分母。"""
    from dataclasses import replace

    payload = base(small)
    payload["sessions"] = ["2023-12-27"] + payload["sessions"]
    payload["bars"] = [{**payload["bars"][0], "date": "2023-12-27"}] + payload["bars"]
    cfg = replace(config, start=payload["sessions"][3], warmup=2)
    result = simulate({k: v for k, v in payload.items() if k != "bars"}, payload["bars"], cfg,
                      SimpleNamespace(on_session=lambda ctx: None))
    info = result["metadata"]["warmup_bars"]["A"]
    assert info["bars"] == 2, info
