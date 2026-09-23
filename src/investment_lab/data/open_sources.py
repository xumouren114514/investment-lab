"""Account-free data adapters. Provider claims, candidates and verified fields stay distinct."""
from __future__ import annotations

import csv
import base64
import io
import json
import re
import subprocess
import sys
import zipfile
from datetime import date, datetime, timedelta
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from investment_lab.common import dec, now
from .adapters import request_json, trading_sessions

BAO_DOC = "https://www.baostock.com/mainContent?file=stockKData.md"
AK_DOC = "https://akshare.akfamily.xyz/data/stock/stock.html"
CFFEX_DOC = "http://www.cffex.com.cn/rtj/"


def bounded_process(command, payload, timeout=180):
    import psutil
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        stdout, _ = proc.communicate(json.dumps(payload), timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            children = psutil.Process(proc.pid).children(recursive=True)
        except psutil.NoSuchProcess:
            children = []
        for child in reversed(children):
            try:
                child.kill()
            except psutil.NoSuchProcess:
                pass
        proc.kill()
        proc.communicate()
        raise ValueError("供应商工作进程超时，已终止本次进程树") from None
    if proc.returncode:
        raise ValueError("供应商子进程失败；检查网络或可选依赖 baostock==0.9.3")
    return json.loads(stdout)


def available(day, market):
    zone = ZoneInfo({"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[market])
    return datetime.fromisoformat(day + "T23:00:00").replace(tzinfo=zone).isoformat()


def ratio_fields(amount, volume, low, high, *, verified=False, scope="unverified", multiplier=1):
    """Retain the ratio even when unsuitable for execution; never clamp an outlier."""
    candidate = None
    if amount is not None and dec(volume) > 0 and dec(amount) > 0:
        candidate = str(dec(amount) / dec(volume) / dec(multiplier))
    usable = candidate is not None and dec(low) <= dec(candidate) <= dec(high)
    return {"amount_volume_candidate": candidate, "vwap_value": candidate if usable else None,
            "vwap_method": ("amount_volume_verified" if verified else "amount_volume_unverified") if usable else "unavailable",
            "quality_status": ("verified" if verified else "unverified") if usable else "unavailable",
            "vwap_session": scope, "vwap_issue": None if usable else "no_turnover_or_ratio_outside_ohlc"}


def normalize_baostock(symbol, rows, excluded=None):
    bars = []
    for row in rows:
        if row["adjustflag"] != "3":
            raise ValueError("BaoStock 返回复权行情；拒绝入库")
        if row["code"] != symbol or row["tradestatus"] not in ("0", "1"):
            raise ValueError("BaoStock 证券身份或交易状态异常")
        try:
            values = {k: dec(row[k]) for k in ("open", "high", "low", "close", "volume", "amount")}
            if any(not v.is_finite() for v in values.values()) or values["volume"] < 0 or values["amount"] < 0:
                raise ValueError("非有限数字或负量额")
            if values["low"] <= 0 or not values["low"] <= values["open"] <= values["high"] or not values["low"] <= values["close"] <= values["high"]:
                raise ValueError("OHLC 不一致")
        except (KeyError, ValueError, ArithmeticError):
            if excluded is None:
                raise ValueError("BaoStock 行情字段缺失或不一致: " + row["date"]) from None
            excluded.append({"date": row["date"], "reason": "字段缺失或 OHLC/量额异常；不补零，保留日期缺口", "raw": row})
            continue
        bar = {k: row[k] for k in ("date", "open", "high", "low", "close", "volume", "amount", "preclose")}
        bar.update(symbol=symbol, source="BaoStock/daily", available_at=available(row["date"], "CN"),
                   status="suspended" if row["tradestatus"] == "0" else "trading",
                   price_basis="unadjusted", volume_basis="shares", is_st=row.get("isST"))
        # Documentation establishes units; it does not establish auction/block-trade scope.
        # Keep a candidate until that separate source/venue audit is complete.
        bar.update(ratio_fields(row["amount"], row["volume"], row["low"], row["high"], scope="vendor_daily_scope_pending"))
        bars.append(bar)
    return bars


def cash_actions(symbol, rows):
    actions, unresolved = [], []
    for row in rows:
        day, pay = row.get("dividOperateDate"), row.get("dividPayDate")
        if not day:  # A proposal is not an implemented action.
            unresolved.append({"reason": "未实施或缺少除权日", "raw": row})
            continue
        if dec(row.get("dividStocksPs") or 0) or dec(row.get("dividReserveToStockPs") or 0):
            unresolved.append({"reason": "送转股到账日/零股处理待验证", "raw": row})
            continue
        amount = row.get("dividCashPsBeforeTax")
        if amount and dec(amount) > 0 and pay and pay >= day:
            actions.append({"symbol": symbol, "date": day, "pay_date": pay, "type": "dividend",
                            "amount": str(dec(amount)), "tax_basis": "gross_before_tax",
                            "source": "BaoStock/query_dividend_data", "announced": row.get("dividPlanAnnounceDate")})
        else:
            unresolved.append({"reason": "现金金额或支付日不完整", "raw": row})
    return actions, unresolved


class BaoStock:
    def fetch(self, symbol, start, end, actions_start=None, calendar=None, kind="stock"):
        if not re.fullmatch(r"(?:sh|sz)\.\d{6}", symbol):
            raise ValueError("BaoStock 代码须形如 sh.600519")
        # SDK has process-global sockets and stdout logging. Isolate it and bound its lifetime.
        raw = bounded_process([sys.executable, "-m", "investment_lab.data.baostock_worker"],
                              {"symbol": symbol, "start": max(start, "1990-12-19"), "end": end, "actions_start": actions_start, "calendar": calendar})
        if raw.get("error"):
            raise ValueError("BaoStock: " + raw["error"])
        excluded = []
        bars = normalize_baostock(symbol, raw["prices"], excluded)
        basic = raw["basic"][0] if len(raw["basic"]) == 1 else {}
        if basic.get("code") != symbol:
            raise ValueError("BaoStock 证券基础资料不匹配")
        if kind not in ("stock", "index", "etf") or basic.get("type") != {"stock": "1", "index": "2", "etf": "5"}.get(kind):
            raise ValueError("BaoStock 返回证券类型与请求不符")
        if kind == "index":
            if basic.get("ipoDate"):
                excluded.extend({"date": b["date"], "reason": "指数发布前回算历史不能提前用于决策", "raw": b}
                                for b in bars if b["date"] < basic["ipoDate"])
                bars = [b for b in bars if b["date"] >= basic["ipoDate"]]
            for bar in bars:
                bar.update(vwap_value=None, amount_volume_candidate=None, vwap_method="unavailable", quality_status="unavailable",
                           vwap_session="index_not_tradable", vwap_issue="指数点位不能用成分股金额/股数计算")
        actions, unresolved = cash_actions(symbol, raw.get("dividends", []))
        return {"bars": bars, "raw": raw, "actions": actions,
                "sessions": [r["calendar_date"] for r in raw["calendar"] if r["is_trading_day"] == "1"],
                "security_patch": {"listed": basic.get("ipoDate") or None, "delisted": basic.get("outDate") or None,
                                   "listing_verified": bool(basic.get("ipoDate")), "listing_evidence": "BaoStock/query_stock_basic",
                                   "provider_name": basic.get("code_name"), "actions_verified": False},
                "source": {"provider": "BaoStock", "downloaded": now(), "documentation": BAO_DOC,
                           "code_license": "BSD", "account_required": False, "kind": kind,
                           "units": {"volume": "股", "amount": "人民币元"}, "adjustflag": "3",
                           "scope_status": "成交量/额单位已核对；交易场所、集合竞价及大宗交易范围待专项核实",
                           "actions_start": actions_start, "action_issues": unresolved, "excluded": excluded,
                           "actions_status": "仅导入有支付日的税前现金分红；配股/送转和完整性未认证"}}


class Eastmoney:
    def fetch(self, symbol, start, end, market="HK", secid=None, kind="stock"):
        if market not in ("HK", "CN", "US"):
            raise ValueError("未知市场")
        code = symbol.split(":")[-1]
        if not re.fullmatch(r"\d{4,6}", code) and not (market == "US" and re.fullmatch(r"[A-Z.-]{1,12}", code)) and not (kind == "index" and code in ("HSI", "HSCEI", "HSTECH")):
            raise ValueError("证券代码无效")
        mapping = None
        if market == "US" and secid is None:
            mapping = request_json("https://searchapi.eastmoney.com/api/suggest/get?" + urlencode({"input": code, "type": 14, "count": 20}))
            exact = [r for r in mapping.get("QuotationCodeTable", {}).get("Data", [])
                     if r.get("Code") == code and r.get("Classify") == "UsStock" and str(r.get("MktNum")) in ("105", "106", "107")]
            if len(exact) != 1:
                raise ValueError("美股代码映射不唯一或不存在，停止而非猜测交易所")
            secid = exact[0]["QuoteID"]
        expected = code.zfill(5) if market == "HK" and kind != "index" else code
        secid = secid or ("116." + expected if market == "HK" else ("1." if code.startswith(("5", "6", "9")) else "0.") + code)
        if not re.fullmatch(r"\d+\.[A-Z0-9.-]{1,12}", secid) or secid.split(".", 1)[1] != expected:
            raise ValueError("secid 与证券代码不一致")
        query = urlencode({"secid": secid, "klt": 101, "fqt": 0, "beg": start.replace("-", ""), "end": end.replace("-", ""),
                           "fields1": "f1,f2,f3,f4,f5,f6", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61"})
        raw = request_json("https://push2his.eastmoney.com/api/qt/stock/kline/get?" + query)
        data = raw.get("data")
        if raw.get("rc") != 0 or not data or data.get("code") != expected:
            raise ValueError("Eastmoney 响应为空或代码不匹配")
        bars, excluded = [], []
        for line in data.get("klines", []):
            fields = line.split(",")
            if len(fields) != 11:
                raise ValueError("Eastmoney 字段数量变化；停止解析")
            day, op, close, high, low, volume, amount = fields[:7]
            if not start <= day <= end:
                continue
            if any(dec(x) <= 0 for x in (op, high, low, close)) or not dec(low) <= dec(op) <= dec(high) or not dec(low) <= dec(close) <= dec(high):
                excluded.append({"date": day, "reason": "OHLC 不一致或非正价格；保留原始行并形成日期缺口", "raw": line})
                continue
            vol = str(dec(volume) * (100 if market == "CN" else 1))
            bar = {"symbol": symbol, "date": day, "open": op, "close": close, "high": high, "low": low,
                   "volume": vol, "amount": amount, "source": "Eastmoney/kline", "available_at": available(day, market),
                   "status": "trading" if dec(vol) > 0 else "no_trades_unverified", "price_basis": "vendor_fqt0",
                   "volume_basis": "rounded_hands_times_100" if market == "CN" else "vendor_shares"}
            bar.update(ratio_fields(amount, vol, low, high, scope="vendor_daily_scope_pending"))
            if kind == "index":
                bar.update(vwap_value=None, amount_volume_candidate=None, vwap_method="unavailable", quality_status="unavailable",
                           vwap_session="index_not_tradable", vwap_issue="指数点位不等于成分股金额/股数")
            bars.append(bar)
        return {"bars": bars, "raw": {"kline": raw, "mapping": mapping}, "source": {"provider": "Eastmoney/public-kline", "downloaded": now(),
                "interface_reference": AK_DOC, "secid": secid, "fqt": 0, "provider_name": data.get("name"),
                "dktotal": data.get("dktotal"), "excluded": excluded, "scope_status": "未认证；A股手数舍入会影响精度",
                "data_rights": "公开接口个人研究评估；接口代码许可不授予行情再分发权"}}


class YahooChart:
    def fetch(self, symbol, start, end, market="US"):
        if not re.fullmatch(r"[A-Za-z0-9.^=-]{1,24}", symbol):
            raise ValueError("Yahoo 代码无效")
        if market not in ("US", "HK"):
            raise ValueError("Yahoo 当前仅接入美股、港股参考序列")
        zone = ZoneInfo("America/New_York" if market == "US" else "Asia/Hong_Kong")
        begin = int(datetime.fromisoformat(start).replace(tzinfo=zone).timestamp())
        finish = int(datetime.combine(date.fromisoformat(end) + timedelta(days=1), datetime.min.time(), zone).timestamp())
        raw = request_json("https://query1.finance.yahoo.com/v8/finance/chart/" + quote(symbol, safe="") + "?" + urlencode(
            {"period1": begin, "period2": finish, "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true"}))
        if raw.get("chart", {}).get("error") or not raw.get("chart", {}).get("result"):
            raise ValueError("Yahoo Chart 无有效行情")
        result = raw["chart"]["result"][0]
        meta = result["meta"]
        if meta["symbol"].upper() != symbol.upper() or meta.get("currency") != {"US": "USD", "HK": "HKD"}[market]:
            raise ValueError("Yahoo 证券或币种不匹配")
        quotes = result["indicators"]["quote"][0]
        bars, omitted, excluded = [], [], []
        hk_sessions = set(trading_sessions("HK", start, end)) if market == "HK" else None
        for n, stamp in enumerate(result.get("timestamp", [])):
            day = datetime.fromtimestamp(stamp, ZoneInfo(meta["exchangeTimezoneName"])).date().isoformat()
            if not start <= day <= end:
                continue
            values = {key: quotes[key][n] for key in ("open", "high", "low", "close", "volume")}
            if any(v is None for v in values.values()) or any(dec(values[k]) <= 0 for k in ("open", "high", "low", "close")):
                omitted.append(day)
                continue
            if not dec(values["low"]) <= dec(values["open"]) <= dec(values["high"]) or not dec(values["low"]) <= dec(values["close"]) <= dec(values["high"]):
                excluded.append({"date": day, "reason": "OHLC 不一致，保留原始响应并留下缺口", "values": values})
                continue
            if hk_sessions is not None and day not in hk_sessions and dec(values["volume"]) == 0:
                excluded.append({"date": day, "reason": "港股日历休市日的零量占位行，不作为真实交易日", "values": values})
                continue
            # Yahoo close is already split adjusted even without auto_adjust. Never relabel it raw.
            bars.append({"symbol": symbol, "date": day, **{k: str(v) for k, v in values.items()},
                         "amount": None, "vwap_value": None, "vwap_method": "unavailable", "vwap_session": "unverified",
                         "quality_status": "unavailable", "price_basis": "split_adjusted_reference_only",
                         "volume_basis": "vendor_split_adjusted_unverified", "source": "Yahoo/chart",
                         "available_at": available(day, market), "status": "trading" if dec(values["volume"]) > 0 else "no_trades_unverified"})
        return {"bars": bars, "raw": raw, "security_patch": {"execution_blocked": "Yahoo 历史价格为拆股调整参考序列，尚未还原真实交易价；仅可查阅",
                "actions_verified": False, "provider_first_trade": meta.get("firstTradeDate")},
                "source": {"provider": "Yahoo/chart", "downloaded": now(), "data_rights": "Yahoo 数据仅个人用途；不再分发",
                           "reference": "https://github.com/ranaroussi/yfinance/discussions/1682", "omitted_dates": omitted, "excluded": excluded,
                           "actions": "原始 splits/dividends 已保留；不把除息日当作支付日，不重复应用拆股"}}


def parse_cffex_csv(payload, day):
    text = payload.decode("gb18030").lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    required = {"合约代码", "今开盘", "最高价", "最低价", "成交量", "成交金额", "今收盘", "今结算"}
    if not required.issubset(reader.fieldnames or []):
        raise ValueError("中金所日统计格式改变")
    bars = []
    for row in reader:
        symbol = row["合约代码"].strip()
        if not re.fullmatch(r"(?:IF|IH|IC|IM)\d{4}", symbol):
            continue  # Only actual index-future months, not options, summaries or continuous series.
        if not row["今开盘"] or dec(row["成交量"]) <= 0:
            continue
        multiplier = 300 if symbol[:2] in ("IF", "IH") else 200
        amount = str(dec(row["成交金额"]) * 10000)
        bar = {"symbol": symbol, "date": day, "open": row["今开盘"], "high": row["最高价"], "low": row["最低价"],
               "close": row["今收盘"], "settlement": row["今结算"], "volume": row["成交量"], "amount": amount,
               "source": "CFFEX/daily-statistics", "available_at": available(day, "CN"), "status": "trading",
               "price_basis": "unadjusted", "volume_basis": "single_side_contracts", "multiplier": multiplier}
        bar.update(ratio_fields(amount, row["成交量"], row["最低价"], row["最高价"], verified=True,
                                scope="CFFEX_official_daily_single_side_including_EFP", multiplier=multiplier))
        bars.append(bar)
    return bars


class CFFEX:
    def fetch_month(self, month):
        date.fromisoformat(month + "-01")
        compact = month.replace("-", "")
        url = f"http://www.cffex.com.cn/sj/historysj/{compact}/zip/{compact}.zip"
        with urlopen(Request(url, headers={"User-Agent": "InvestmentLab/0.2 personal research"}), timeout=30) as r:
            payload = r.read(10_000_001)
        if len(payload) > 10_000_000:
            raise ValueError("月文件超过预期大小")
        bars, days = [], []
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 40_000_000:
                raise ValueError("月文件解压大小异常")
            for name in sorted(archive.namelist()):
                if not re.fullmatch(compact + r"\d{2}_1\.csv", name):
                    continue
                day = month + "-" + name[6:8]
                date.fromisoformat(day)
                days.append(day)
                bars.extend(parse_cffex_csv(archive.read(name), day))
        return {"bars": bars, "sessions": days, "raw": {"url": url, "zip_base64": base64.b64encode(payload).decode("ascii")},
                "source": {"provider": "CFFEX/official", "downloaded": now(), "url": url, "documentation": CFFEX_DOC,
                           "scope": "官方日统计，成交量和成交额均含期转现、按单边统计；无夜盘",
                           "units": "成交额万元；VWAP点数=成交额*10000/(单边手数*合约乘数)",
                           "data_rights": "官方公开月度文件，本机个人研究；不授予再分发权"}}

    def fetch_day(self, day):
        date.fromisoformat(day)
        compact = day.replace("-", "")
        url = f"http://www.cffex.com.cn/sj/hqsj/rtj/{compact[:6]}/{compact[6:]}/{compact}_1.csv"
        with urlopen(Request(url, headers={"User-Agent": "InvestmentLab/0.2 personal research"}), timeout=30) as r:
            payload = r.read(5_000_001)
        if len(payload) > 5_000_000:
            raise ValueError("中金所日文件超过预期大小")
        return {"bars": parse_cffex_csv(payload, day), "raw": {"url": url, "csv": payload.decode("gb18030")},
                "source": {"provider": "CFFEX/official", "downloaded": now(), "url": url, "documentation": CFFEX_DOC,
                           "units": "成交量单边合约；成交额万元；两者含期转现；价格点数=元/(合约量×每点乘数)",
                           "precision": "VWAP 根据公布成交额精度计算，非逐笔重算", "data_rights": "官方公开日统计；不授予再分发权"}}
