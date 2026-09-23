"""Summarize actual stored snapshots, including missing requested instruments. No network."""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from investment_lab.common import PROJECT, now
from investment_lab.data.store import Store
from investment_lab.data.universe import universe
from investment_lab.data.validation import coverage


def build(roots):
    instruments = universe(Store(roots[0]))["instruments"]
    candidates = defaultdict(list)
    futures = {}
    datasets = []
    for root in roots:
        store = Store(root)
        for head in store.datasets():
            if head["manifest"]["synthetic"]:
                continue
            manifest, bars = store.load(head["id"])
            items = coverage(manifest, bars)
            datasets.append({"name": head["name"], "snapshot": head["id"], "root": str(store.root),
                             "provider": manifest["source"].get("provider"), "rows": len(bars)})
            for row in items:
                sec = manifest["securities"][row["symbol"]]
                row.update(snapshot=head["id"], dataset=head["name"], provider=manifest["source"].get("provider"),
                           kind=sec.get("kind"), excluded_rows=len(manifest["source"].get("excluded", [])))
                if sec.get("universe_id"):
                    candidates[sec["universe_id"]].append(row)
                if sec.get("kind") == "future":
                    if row["symbol"] not in futures or futures[row["symbol"]]["rows"] < row["rows"]:
                        futures[row["symbol"]] = row
    rows = []
    for i in instruments:
        choices = candidates.get(i["id"], [])
        if choices:
            chosen = max(choices, key=lambda x: (x["strict_vwap_rows"], x["candidate_vwap_rows"], x["rows"]))
            rows.append({"universe_id": i["id"], "status": "downloaded", **chosen})
        else:
            rows.append({"universe_id": i["id"], "name": i["name"], "kind": i["kind"], "market": i["market"], "status": "missing"})
    markets = {}
    for market in ("CN", "HK", "US"):
        subset = [r for r in rows if r["market"] == market]
        markets[market] = {"requested": len(subset), "downloaded": sum(r["status"] == "downloaded" for r in subset),
                           "stocks": sum(r["kind"] == "stock" and r["status"] == "downloaded" for r in subset),
                           "rows": sum(r.get("rows", 0) for r in subset),
                           "candidate_vwap_rows": sum(r.get("candidate_vwap_rows", 0) for r in subset),
                           "strict_vwap_rows": sum(r.get("strict_vwap_rows", 0) for r in subset)}
    return {"created": now(), "markets": markets, "universe": rows, "futures": list(futures.values()), "datasets": datasets,
            "strict_backtest_ready": False, "counting": "股票池每标的选严格口径/候选均价最多的一份；期货每合约选最长快照，避免日月重复计数"}


def render(report):
    lines = ["# 公开资源实际覆盖", "", f"生成时间：{report['created']}。仅统计已校验入库快照；不是供应商宣传范围。", "",
             "**目前仍没有被认证为完整严格回测就绪的真实股票/ETF 数据集。** 候选均价、挂牌、动作、规则和日期缺口分别检查。", "",
             "| 市场 | 已有数据 / 请求标的 | 股票 / 30 | 行数 | 候选均价 | 严格价格字段 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for name, m in report["markets"].items():
        lines.append(f"| {name} | {m['downloaded']} / {m['requested']} | {m['stocks']} | {m['rows']:,} | {m['candidate_vwap_rows']:,} | {m['strict_vwap_rows']:,} |")
    fut = report["futures"]
    lines += ["", f"中金所：{len(fut)} 个真实月份合约，{sum(r['rows'] for r in fut)} 条日线，{sum(r['strict_vwap_rows'] for r in fut)} 条量纲和官方统计范围已核实的均价。含期转现；缺挂牌/到期/保证金绑定，禁止执行回测。", "",
              "下表‘缺口’仅针对保存的日历与供应商边界，不表示交易规则或公司行动完整。指数仅作参考；Yahoo 股票序列仅作查阅。未下载标的仍保留在表中。", "",
              "| 标的 | 来源 | 实际起止 | 行数 | 候选 / 严格均价 | 停牌 / 缺口 | 挂牌核实 / 现金事件 |", "| --- | --- | --- | ---: | ---: | ---: | --- |"]
    for r in report["universe"]:
        if r["status"] == "missing":
            lines.append(f"| {r['universe_id']} {r['name']} | 未下载/本轮接口失败或延后 | — | — | — | 未知 | 未知 |")
        else:
            lines.append(f"| {r['universe_id']} {r['name']} | {r['provider']} | {r['first']}—{r['last']} | {r['rows']} | {r['candidate_vwap_rows']} / {r['strict_vwap_rows']} | {r['suspended_rows']} / {len(r['missing_sessions'])} | {'是' if r['listing_verified'] else '否'} / {r['cash_action_count']} |")
    lines += ["", "快照 ID、具体缺口日期、执行阻止原因和每个来源的独立版本见同目录 open-resource-verification.json。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", action="append", required=True)
    parser.add_argument("--output", default=str(PROJECT / "docs"))
    args = parser.parse_args()
    report = build(args.data)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "open-resource-verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "OPEN_RESOURCE_COVERAGE.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"markets": report["markets"], "futures": len(report["futures"])}, ensure_ascii=False))
