from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from investment_lab.common import PROJECT, atomic_write, encoded, read_json
from investment_lab.data.store import Store


def main(argv=None):
    parser = argparse.ArgumentParser(description="投资研究室：本机策略回测与维护")
    parser.add_argument("--data", help="独立运行数据目录")
    subs = parser.add_subparsers(dest="command", required=True)
    init = subs.add_parser("init")
    init.add_argument("--demo", action="store_true")
    serve = subs.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    imp = subs.add_parser("import-json")
    imp.add_argument("file")
    run = subs.add_parser("run")
    run.add_argument("file")
    worker = subs.add_parser("worker")
    worker.add_argument("run_id")
    rerun = subs.add_parser("reproduce")
    rerun.add_argument("run_id")
    subs.add_parser("list")
    subs.add_parser("update")
    subs.add_parser("backup")
    subs.add_parser("daily-backup")
    subs.add_parser("retention-preview")
    rest = subs.add_parser("restore")
    rest.add_argument("backup_id")
    rest.add_argument("destination")
    rest.add_argument("--backup-root")
    sample = subs.add_parser("fetch-public-sample")
    sample.add_argument("--start", default="1980-01-01")
    sample.add_argument("--end", default="2026-09-18")
    opened = subs.add_parser("fetch-open-data", help="无需账号下载公开资源，逐标保存和断点续传")
    opened.add_argument("--scope", choices=("sample", "universe"), default="sample")
    opened.add_argument("--start", default="1980-01-01")
    opened.add_argument("--end")
    opened.add_argument("--refresh", action="store_true")
    opened.add_argument("--reference-us", action="store_true", help="独立下载Yahoo美股参考序列，不能作为未复权执行行情")
    opened.add_argument("--reference-market", choices=("US", "HK"), help="独立下载该市场的 Yahoo 参考序列")
    opened.add_argument("--instrument", action="append", help="仅请求指定股票池 ID，可重复，如 CN:600519")
    opened.add_argument("--cn-provider", choices=("baostock", "eastmoney"), help="指定中国市场备用来源；不同来源独立快照，BaoStock ETF历史较短")
    cffex = subs.add_parser("fetch-cffex-day")
    cffex.add_argument("date")
    cffex_month = subs.add_parser("fetch-cffex-month")
    cffex_month.add_argument("date", help="YYYY-MM")
    actions = subs.add_parser("fetch-cash-actions")
    actions.add_argument("snapshot")
    actions.add_argument("--start", required=True)
    actions.add_argument("--end", required=True)
    bundle = subs.add_parser("release-bundle")
    bundle.add_argument("version")
    args = parser.parse_args(argv)
    store = Store(args.data)
    try:
        if args.command == "init":
            result = {"data": str(store.root), "initialized": True}
            if args.demo:
                from investment_lab.data.demo import install_demo
                result["demo_snapshot"] = install_demo(store)
            example = store.root / "user_strategies" / "my_strategy.py"
            if not example.exists():
                atomic_write(example, b'def on_session(ctx):\n    if not ctx.state.get("ordered"):\n        ctx.order_target_weight(ctx.symbols[0], 0.90, "initial allocation")\n        ctx.state["ordered"] = True\n')
        elif args.command == "serve":
            import uvicorn
            from investment_lab.web.app import create_app
            uvicorn.run(create_app(store.root), host="127.0.0.1", port=args.port, log_level="warning")
            return
        elif args.command == "import-json":
            result = {"snapshot": store.ingest(**read_json(args.file))}
        elif args.command in ("run", "worker"):
            from investment_lab.jobs import create_run, execute_run
            run_id = create_run(store, read_json(args.file)) if args.command == "run" else args.run_id
            if args.command == "worker":
                output = execute_run(store, run_id)
                result = {"run_id": run_id, "result_hash": output["result_hash"]}
            else:
                from investment_lab.jobs import JobManager
                manager = JobManager(store)
                manager.start(run_id)
                manager.active[run_id].wait()
                path = store.root / "runs" / run_id
                if not (path / "result.json").exists():
                    raise ValueError(read_json(path / "error.json"))
                result = {"run_id": run_id, "result": str(path / "result.json")}
        elif args.command == "reproduce":
            from investment_lab.jobs import reproduce
            result = reproduce(store, args.run_id)
        elif args.command == "list":
            result = {"datasets": [{"name": d["name"], "snapshot": d["id"], "synthetic": d["manifest"]["synthetic"]} for d in store.datasets()], "runs": store.runs(20)}
        elif args.command == "update":
            from investment_lab.data.update import update
            result = update(store)
        elif args.command == "backup":
            from investment_lab.maintenance.backup import backup
            result = backup(store)
        elif args.command == "daily-backup":
            from investment_lab.maintenance.daily import backup_if_changed
            result = backup_if_changed(store)
        elif args.command == "retention-preview":
            from investment_lab.maintenance.backup import backup_root, retention_preview
            result = retention_preview(backup_root(store))
        elif args.command == "restore":
            from investment_lab.maintenance.backup import backup_root, restore
            result = restore(args.backup_root or backup_root(store), args.backup_id, args.destination)
        elif args.command == "release-bundle":
            from investment_lab.maintenance.backup import release_bundle
            result = release_bundle(store, args.version)
        elif args.command == "fetch-open-data":
            from investment_lab.data.open_acquire import acquire
            def progress(item):
                print(json.dumps({"progress": item["universe_id"], "status": item["status"],
                                  "rows": item.get("coverage", {}).get("rows"), "error": item.get("error")}, ensure_ascii=False), flush=True)
            result = acquire(store, args.scope, args.start, args.end, args.refresh, progress,
                             reference=args.reference_market or args.reference_us, instruments=args.instrument, cn_provider=args.cn_provider)
        elif args.command in ("fetch-cffex-day", "fetch-cffex-month"):
            from investment_lab.data.open_acquire import acquire_cffex
            result = acquire_cffex(store, args.date)
        elif args.command == "fetch-cash-actions":
            from investment_lab.data.open_acquire import enrich_baostock_actions
            result = enrich_baostock_actions(store, args.snapshot, args.start, args.end)
        elif args.command == "fetch-public-sample":
            from investment_lab.data.adapters import EODHD, trading_sessions
            from investment_lab.data.validation import coverage
            response = EODHD("demo").fetch("AAPL.US", args.start, args.end)
            if not response["bars"]:
                raise ValueError("公开样本返回空数据")
            first, last = min(b["date"] for b in response["bars"]), max(b["date"] for b in response["bars"])
            security = {"name": "Apple · 公开 API 样本", "market": "US", "currency": "USD", "exchange": "XNAS",
                        "timezone": "America/New_York", "kind": "stock", "listed": "1980-12-12", "listing_verified": False,
                        "actions_verified": False, "lot": 1, "margin_eligible": False,
                        "verification": "挂牌日期为供应商文档声称；尚未完成独立证券身份/公司行动验证"}
            response["source"]["sample_permission"] = "EODHD 官方公开 demo API，个人接口评估"
            snapshot = store.ingest("AAPL · EODHD 真实公开样本（VWAP 缺失）", {"AAPL.US": security}, response["bars"], sessions=trading_sessions("US", first, last), source=response["source"], raw=response["raw"])
            manifest, bars = store.load(snapshot)
            result = {"snapshot": snapshot, "coverage": coverage(manifest, bars), "strict_backtest_ready": False}
            atomic_write(store.root / "state/public-sample-report.json", result)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if isinstance(result, dict) and result.get("status") == "partial_failure":
            raise SystemExit(1)
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
