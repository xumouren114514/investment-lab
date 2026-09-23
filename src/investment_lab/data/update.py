from datetime import date, timedelta
from investment_lab.common import atomic_write, digest, now, read_json, file_lock
from .adapters import EODHD, Tushare, completed_session, trading_sessions


def update(store, config_file=None):
    path = config_file or store.root / "local_config" / "providers.json"
    if not path.exists():
        return {"status": "needs_configuration", "message": "未配置真实数据源；请按 docs/OPERATIONS.md 配置，不会补造行情", "added": 0, "revised": 0, "failed": 0}
    config = read_json(path)
    report = {"status": "completed", "created": now(), "added": 0, "revised": 0, "failed": 0, "items": []}
    with file_lock(store.root / "state" / "updater.lock"):
        heads = {d["name"]: d["id"] for d in store.datasets()}
        for target in config.get("targets", []):
            name = target["dataset"]
            try:
                symbol, security = target["symbol"], target["security"]
                market = security["market"]
                end = min(target.get("end") or "9999-12-31", completed_session(market))
                existing, old_manifest = [], None
                if name in heads:
                    old_manifest, existing = store.load(heads[name])
                # Recheck recent days; full older revision review is an explicit audit option.
                start = target.get("start", "1980-01-01")
                if existing and not config.get("full_revision_audit", False):
                    start = max(start, (date.fromisoformat(max(b["date"] for b in existing)) - timedelta(days=14)).isoformat())
                adapter = EODHD() if target["provider"] == "eodhd" else Tushare() if target["provider"] == "tushare" else None
                if target["provider"] in ("baostock", "eastmoney", "yahoo"):
                    from .open_sources import BaoStock, Eastmoney, YahooChart
                    adapter = {"baostock": BaoStock, "eastmoney": Eastmoney, "yahoo": YahooChart}[target["provider"]]()
                if adapter is None:
                    raise ValueError("未知供应商")
                fresh = adapter.fetch(symbol, start, end, **target.get("adapter_options", {}))
                if not fresh["bars"]:
                    raise ValueError("请求成功但无数据；不推进有效检查点")
                merged = {(b["symbol"], b["date"]): b for b in existing}
                added = revised = 0
                for bar in fresh["bars"]:
                    key = (bar["symbol"], bar["date"])
                    added += key not in merged
                    revised += key in merged and digest(merged[key]) != digest(bar)
                    merged[key] = bar
                rows = list(merged.values())
                security = {**(old_manifest["securities"][symbol] if old_manifest else {}), **security, **fresh.get("security_patch", {})}
                first = min(b["date"] for b in rows)
                if fresh.get("sessions") is not None:
                    sessions = sorted(set(fresh["sessions"]) | {d for d in (old_manifest["sessions"] if old_manifest else []) if d < start})
                else:
                    sessions = trading_sessions(market, first, end)
                dates = {b["date"] for b in rows}
                gaps = [d for d in sessions if d not in dates]
                if old_manifest and not added and not revised and old_manifest["sessions"] == sessions and old_manifest["securities"] == {symbol: security}:
                    snapshot = heads[name]
                else:
                    snapshot = store.ingest(name, {symbol: security}, rows, actions=old_manifest["actions"] if old_manifest else [], sessions=sessions,
                                            source=fresh["source"], raw=fresh["raw"])
                item = {"name": name, "snapshot": snapshot, "added": added, "revised": revised, "gaps": gaps, "checkpoint": max(dates), "status": "gaps" if gaps else "downloaded_not_scope_verified"}
                report["items"].append(item)
                report["added"] += added
                report["revised"] += revised
                with store.lock():
                    atomic_write(store.root / "state" / ("checkpoint-" + digest(name)[:12] + ".json"), item)
            except Exception as exc:
                report["failed"] += 1
                report["items"].append({"name": name, "status": "failed", "error": str(exc)[:500]})
        if report["failed"]:
            report["status"] = "partial_failure"
        with store.lock():
            atomic_write(store.root / "state" / "last-update.json", report)
        store.audit("update", report)
    return report
