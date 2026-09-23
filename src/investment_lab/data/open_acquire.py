"""Reproducible, resumable acquisition of the configured universe without subscriptions."""
from __future__ import annotations

import time
import os
from datetime import date

from investment_lab.common import atomic_write, digest, file_lock, now, read_json
from .adapters import completed_session, trading_sessions, HK_SPECIAL_CLOSURES
from .open_sources import BaoStock, Eastmoney, YahooChart, CFFEX
from .universe import universe
from .validation import coverage

INDEX_MAP = {
    "HK:INDEX_2800": ("HSI", "100.HSI"), "HK:INDEX_2828": ("HSCEI", "100.HSCEI"),
    "HK:INDEX_3033": ("HSTECH", "100.HSTECH"),
    "US:INDEX_SPY": ("^GSPC", None), "US:INDEX_QQQ": ("^NDX", None),
    "US:INDEX_DIA": ("^DJI", None), "US:INDEX_IWM": ("^RUT", None),
    "CN:INDEX_510300": ("000300", "1.000300"), "CN:INDEX_510050": ("000016", "1.000016"),
    "CN:INDEX_510500": ("000905", "1.000905"), "CN:INDEX_512100": ("000852", "1.000852"),
    "CN:INDEX_159915": ("399006", "0.399006"), "CN:INDEX_588000": ("000688", "1.000688"),
}
HK_YAHOO_INDEX = {"HK:INDEX_2800": "^HSI", "HK:INDEX_2828": "^HSCE"}


def target_for(instrument, start="1980-01-01", end=None, cn_provider=None):
    i = instrument
    market, kind, code = i["market"], i["kind"], i["code"]
    secid = None
    if kind == "index":
        code, secid = INDEX_MAP[i["id"]]
    options = {}
    if market == "CN" and kind == "stock":
        provider = "baostock"
        symbol = ("sh." if code.startswith("6") else "sz.") + code
    elif market == "US" and kind == "index":
        provider, symbol = "yahoo", code.replace(".", "-")
    else:
        provider, symbol = "eastmoney", code
        options = {"market": market, "kind": kind}
        if secid:
            options["secid"] = secid
    star = market == "CN" and kind == "stock" and i.get("exchange") == "XSHG" and code.startswith("688")
    security = {"name": i["name"], "market": market, "currency": i["currency"], "kind": kind,
                "timezone": {"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[market],
                "exchange": i.get("exchange"), "listed": None, "listing_verified": False, "actions_verified": False,
                "lot": 1 if star else 100 if market == "CN" else 1, "lot_verified": star or market == "CN" and not code.startswith("688"),
                "sell_delay": 1 if market == "CN" and kind == "stock" else 0, "margin_eligible": False,
                "universe_id": i["id"], "mapping_evidence": "按证券代码请求并校验返回代码；名称保留供复核"}
    if star:
        security.update(minimum_buy_quantity=200, minimum_sell_quantity=200)
    if market == "HK":
        security["execution_blocked"] = "港股每手股数及历史公司行动尚未验证；当前仅用于数据评估"
    name = f"公开资源 · {i['id']} · {i['name']}"
    if market == "CN" and cn_provider:
        if cn_provider not in ("baostock", "eastmoney"):
            raise ValueError("A股来源无效")
        if provider != cn_provider:
            name = f"公开资源 · {cn_provider} · {i['id']} · {i['name']}"
            provider = cn_provider
            if provider == "baostock":
                symbol = ("sh." if (secid and secid.startswith("1.")) or (not secid and code.startswith(("5", "6", "9"))) else "sz.") + code
                options = {"kind": kind}
            else:
                symbol, options = code, {"market": market, "kind": kind}
    return {"dataset": name, "universe_id": i["id"], "symbol": symbol,
            "security": security, "provider": provider, "start": start, "end": end, "adapter_options": options}


def acquire(store, scope="sample", start="1980-01-01", end=None, refresh=False, progress=None, reference=False, instruments=None, cn_provider=None):
    date.fromisoformat(start)
    if end:
        date.fromisoformat(end)
    selected = [i for i in universe(store)["instruments"] if i.get("active", True)]
    if scope == "sample":
        selected = [i for i in selected if i["id"] in ("CN:600519", "HK:0700", "US:AAPL", "CN:510300", "HK:2800", "US:SPY")]
    elif scope != "universe":
        raise ValueError("范围须为 sample 或 universe")
    reference_market = "US" if reference is True else reference
    if reference_market:
        if reference_market not in ("US", "HK"):
            raise ValueError("参考序列市场须为 US 或 HK")
        selected = [i for i in selected if i["market"] == reference_market]
    if instruments:
        if set(instruments) - {i["id"] for i in selected}:
            raise ValueError("指定标的不在当前范围；完整股票池使用 --scope universe")
        selected = [i for i in selected if i["id"] in instruments]
    report = {"created": now(), "scope": scope, "start_requested": start, "end_requested": end,
              "status": "running", "items": [], "failed": 0, "strict_backtest_ready": False, "reference_only": reference, "instruments_requested": instruments}
    import psutil
    report["worker"] = {"pid": os.getpid(), "created": psutil.Process().create_time()}
    path = store.root / "state" / "open-resource-report.json"
    with file_lock(store.root / "state" / "updater.lock"):
        if path.exists():
            previous_report = read_json(path)
            atomic_write(store.root / "state" / "open-resource-history" / (digest(previous_report) + ".json"), previous_report)
        atomic_write(path, report)
        heads = {d["name"]: d for d in store.datasets()}
        provider_failures = {}
        bao_calendar = []
        for head in heads.values():
            m = head["manifest"]
            if m["source"].get("provider") == "BaoStock" and m.get("raw"):
                candidate = store.get_object("raw", m["raw"]).get("calendar", [])
                if len(candidate) > len(bao_calendar):
                    bao_calendar = candidate
        for i in selected:
            target = target_for(i, start, end, cn_provider)
            if reference:
                if reference_market == "HK" and i["kind"] == "index" and i["id"] not in HK_YAHOO_INDEX:
                    report["items"].append({"universe_id": i["id"], "provider": "yahoo", "status": "deferred", "error": "指数供应商身份未映射，未猜测代码"})
                    continue
                symbol = (INDEX_MAP[i["id"]][0] if i["kind"] == "index" else i["code"].replace(".", "-")) if reference_market == "US" else (HK_YAHOO_INDEX[i["id"]] if i["kind"] == "index" else f"{int(i['code']):04d}.HK")
                target.update(provider="yahoo", symbol=symbol, adapter_options={"market": reference_market},
                              dataset=f"参考序列 · {i['id']} · {i['name']}")
            if provider_failures.get(target["provider"], 0) >= 3:
                report["items"].append({"universe_id": i["id"], "provider": target["provider"], "status": "deferred",
                                        "error": "同源连续三次失败，停止访问该供应商；保留已有快照，稍后续传"})
                continue
            target_end = min(end or "9999-12-31", completed_session(i["market"]))
            if start > target_end:
                raise ValueError("开始日期晚于已完成交易日")
            identity = digest({"provider": target["provider"], "symbol": target["symbol"], "start": start, "end": target_end,
                               "options": target["adapter_options"], "adapter_version": 2})
            name = target["dataset"]
            try:
                previous = heads.get(name)
                if not refresh and previous and previous["manifest"]["source"].get("request_identity") == identity:
                    manifest, bars = store.load(previous["id"])
                    snapshot, cached = previous["id"], True
                    if i["market"] == "HK" and any(d in HK_SPECIAL_CLOSURES for d in manifest["sessions"]):
                        source = {**manifest["source"], "calendar_corrections": HK_SPECIAL_CLOSURES, "parent_snapshot": snapshot}
                        snapshot = store.ingest(name, manifest["securities"], bars, actions=manifest["actions"],
                            sessions=[d for d in manifest["sessions"] if d not in HK_SPECIAL_CLOSURES], source=source,
                            raw=store.get_object("raw", manifest["raw"]) if manifest.get("raw") else None)
                        manifest = store.manifest(snapshot)
                else:
                    adapter = {"baostock": BaoStock, "eastmoney": Eastmoney, "yahoo": YahooChart}[target["provider"]]()
                    options = dict(target["adapter_options"])
                    if target["provider"] == "baostock":
                        options["calendar"] = bao_calendar
                    response = adapter.fetch(target["symbol"], start, target_end, **options)
                    if target["provider"] == "baostock" and len(response["raw"].get("calendar", [])) > len(bao_calendar):
                        bao_calendar = response["raw"]["calendar"]
                    bars = response["bars"]
                    if not bars:
                        raise ValueError("无数据，未推进检查点")
                    first, last = min(b["date"] for b in bars), max(b["date"] for b in bars)
                    security = {**target["security"], **response.get("security_patch", {})}
                    sessions = response.get("sessions")
                    if sessions is None:
                        sessions = trading_sessions(i["market"], first, target_end)
                    response["source"].update(request_identity=identity, requested_start=start, requested_end=target_end,
                                              actual_first=first, actual_last=last)
                    if i["market"] == "HK":
                        response["source"]["calendar_corrections"] = HK_SPECIAL_CLOSURES
                    snapshot = store.ingest(name, {target["symbol"]: security}, bars, sessions=sessions,
                                            actions=response.get("actions", []), source=response["source"], raw=response["raw"])
                    manifest = store.manifest(snapshot)
                    cached = False
                    time.sleep(2)
                provider_failures[target["provider"]] = 0
                item = {"universe_id": i["id"], "provider": target["provider"], "snapshot": snapshot,
                        "cached": cached, "status": "downloaded", "coverage": coverage(manifest, bars)[0]}
            except Exception as exc:
                report["failed"] += 1
                provider_failures[target["provider"]] = provider_failures.get(target["provider"], 0) + 1
                item = {"universe_id": i["id"], "provider": target["provider"], "status": "failed", "error": str(exc)[:500]}
            report["items"].append(item)
            atomic_write(path, report)
            if progress:
                progress(item)
        report["deferred"] = sum(i["status"] == "deferred" for i in report["items"])
        report["status"] = "partial_failure" if report["failed"] or report["deferred"] else "completed"
        report["completed"] = now()
        atomic_write(path, report)
        store.audit("open_resource_acquisition", {"status": report["status"], "count": len(report["items"]), "failed": report["failed"]})
    return report


def acquire_cffex(store, day):
    response = CFFEX().fetch_month(day) if len(day) == 7 else CFFEX().fetch_day(day)
    if not response["bars"]:
        raise ValueError("当日没有股指月份期货数据")
    securities = {b["symbol"]: {"name": b["symbol"], "market": "CN", "currency": "CNY", "exchange": "CCFX", "kind": "future",
                   "timezone": "Asia/Shanghai", "multiplier": b["multiplier"], "tick": "0.2", "lot": 1,
                   "listing_verified": False, "actions_verified": True,
                   "execution_blocked": "日统计真实月份合约；挂牌/到期日和历史保证金未绑定，暂不可执行回测"} for b in response["bars"]}
    snapshot = store.ingest(f"中金所官方日统计 · {day}", securities, response["bars"], sessions=response.get("sessions", [day]), source=response["source"], raw=response["raw"])
    return {"snapshot": snapshot, "coverage": coverage(store.manifest(snapshot), response["bars"]), "strict_backtest_ready": False}


def enrich_baostock_actions(store, snapshot, start, end):
    """Create a new immutable version containing cash events, retaining uncertainty on completeness."""
    manifest, bars = store.load(snapshot)
    if len(manifest["securities"]) != 1 or manifest["source"].get("provider") != "BaoStock":
        raise ValueError("请选择单证券 BaoStock 数据集")
    symbol = next(iter(manifest["securities"]))
    if start > end or not bars or start < bars[0]["date"] or end > bars[-1]["date"]:
        raise ValueError("行动查询区间须在已下载行情内")
    response = BaoStock().fetch(symbol, start, end, actions_start=start)
    securities = manifest["securities"]
    securities[symbol]["actions_verified"] = False
    actions = {(a["symbol"], a["type"], a["date"], a.get("pay_date")): a for a in manifest["actions"]}
    # Queried calendar years replace that provider's prior records, including corrections/deletions.
    actions = {key: a for key, a in actions.items() if not (a.get("source") == "BaoStock/query_dividend_data" and start[:4] <= a["date"][:4] <= end[:4])}
    actions.update({(a["symbol"], a["type"], a["date"], a.get("pay_date")): a for a in response["actions"]})
    source = {**manifest["source"], "cash_action_import": response["source"], "parent_snapshot": snapshot}
    raw = {"parent_raw": manifest["raw"], "cash_action_response": response["raw"]}
    new_id = store.ingest(manifest["name"], securities, bars, actions=list(actions.values()), sessions=manifest["sessions"], source=source, raw=raw)
    return {"snapshot": new_id, "cash_events": len(actions), "unresolved": len(response["source"]["action_issues"]), "actions_verified": False}
