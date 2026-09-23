from __future__ import annotations

import json
import os
import time
from http.client import RemoteDisconnected
from datetime import date, datetime, timedelta, timezone
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from investment_lab.common import now, dec
from .validation import vwap


class DataAdapter(Protocol):
    def fetch(self, symbol: str, start: str, end: str) -> dict: ...


# Explicit exchange notices, never inferred from missing bars. Modern severe-weather
# trading rules must not be applied retroactively to these historical closures.
HK_SPECIAL_CLOSURES = {
    "2023-09-01": "https://www.hkex.com.hk/News/Market-Communications/2023/2309012news?sc_lang=en",
    "2023-09-08": "https://www.hkex.com.hk/news/market-communications/2023/2309083news?sc_lang=en",
}


def request_json(url, body=None):
    for attempt in range(3):
        try:
            data = json.dumps(body).encode() if body is not None else None
            req = Request(url, data=data, headers={"User-Agent": "InvestmentLab/0.1 (personal research)", "Content-Type": "application/json"})
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                # Never include query-string credentials or provider response in logs.
                raise ValueError(f"供应商 HTTP {exc.code}；请检查权限/限额") from None
        except (URLError, TimeoutError, json.JSONDecodeError, RemoteDisconnected, ConnectionResetError):
            if attempt == 2:
                raise ValueError("供应商网络失败或非 JSON 响应，密钥未写入日志") from None
        time.sleep(2 ** attempt)


def trading_sessions(market, start, end):
    import exchange_calendars as xcals
    name = {"US": "XNYS", "HK": "XHKG", "CN": "XSHG"}[market]
    try:
        padded_start = (date.fromisoformat(start) - timedelta(days=10)).isoformat()
        padded_end = (date.fromisoformat(end) + timedelta(days=10)).isoformat()
        calendar = xcals.get_calendar(name, start=padded_start, end=padded_end)
        days = [stamp.date().isoformat() for stamp in calendar.sessions_in_range(start, end)]
        return [d for d in days if market != "HK" or d not in HK_SPECIAL_CLOSURES]
    except (ValueError, KeyError) as exc:
        raise ValueError(f"交易日历 {name} 无法覆盖请求区间；不能用工作日补造") from exc


def completed_session(market, current=None):
    current = current or datetime.now(timezone.utc)
    tz = ZoneInfo({"US": "America/New_York", "HK": "Asia/Hong_Kong", "CN": "Asia/Shanghai"}[market])
    local = current.astimezone(tz)
    # Conservative provider-ready cutoff, intentionally after normal closes.
    ready_hour = {"US": 20, "HK": 21, "CN": 20}[market]
    last = local.date() if local.hour >= ready_hour else local.date() - timedelta(days=1)
    days = trading_sessions(market, (last - timedelta(days=35)).isoformat(), last.isoformat())
    if not days:
        raise ValueError("没有已完成交易日")
    return days[-1]


class EODHD:
    def __init__(self, token=None):
        self.token = token or os.getenv("EODHD_API_TOKEN")
        if not self.token:
            raise ValueError("未配置 EODHD_API_TOKEN")

    def endpoint(self, endpoint, symbol, start, end):
        query = urlencode({"api_token": self.token, "fmt": "json", "from": start, "to": end, "period": "d", "order": "a"})
        return request_json(f"https://eodhd.com/api/{endpoint}/{quote(symbol, safe='.-')}?{query}")

    def fetch(self, symbol, start, end):
        raw = self.endpoint("eod", symbol, start, end)
        if not isinstance(raw, list):
            raise ValueError("行情响应结构异常")
        bars = []
        for item in raw:
            day = item["date"]
            if not start <= day <= end:
                continue
            bars.append({"symbol": symbol, "date": day, **{k: str(item[k]) for k in ("open", "high", "low", "close", "volume")},
                         "adjusted_close": str(item.get("adjusted_close")), "amount": None, "vwap_value": None,
                         "vwap_method": "unavailable", "vwap_session": "unverified", "quality_status": "unverified",
                         "volume_basis": "vendor_split_adjusted_unverified_as_traded", "source": "EODHD/eod",
                         "available_at": day + "T23:59:00" + ("-04:00" if datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York")).utcoffset().total_seconds() == -14400 else "-05:00"),
                         "status": "trading"})
        return {"bars": bars, "raw": raw, "source": {"provider": "EODHD/eod", "downloaded": now(), "vwap": "not_provided", "actions": "not_imported", "volume_scope": "不可直接用于真实成交量参与上限，待复原与核实"}}


class Tushare:
    def __init__(self, token=None):
        self.token = token or os.getenv("TUSHARE_TOKEN")
        if not self.token:
            raise ValueError("未配置 TUSHARE_TOKEN")

    def fetch(self, symbol, start, end, api="daily", scope_verified=False, scope_evidence=None):
        if api not in ("daily", "hk_daily", "fund_daily", "fut_daily"):
            raise ValueError("未支持接口")
        if scope_verified and not scope_evidence:
            raise ValueError("确认成交口径必须提供证据记录")
        raw, offset = [], 0
        while True:
            payload = request_json("https://api.tushare.pro", {"api_name": api, "token": self.token, "params": {
                "ts_code": symbol, "start_date": start.replace("-", ""), "end_date": end.replace("-", ""), "limit": 4000, "offset": offset}, "fields": ""})
            if payload.get("code") != 0:
                raise ValueError("Tushare 拒绝请求；请检查账户积分和独立权限")
            response = payload["data"]
            chunk = [dict(zip(response["fields"], row)) for row in response["items"]]
            raw.extend(chunk)
            if len(chunk) < 4000:
                break
            offset += 4000
            time.sleep(1)
        bars = []
        for item in raw:
            d = item["trade_date"]
            day = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            # CN stock daily documents specify hands/1000 CNY. Other interfaces require their own verified schema.
            can_compute = api == "daily" and scope_verified
            val = vwap(item.get("amount"), item.get("vol"), 1000, 100, can_compute)
            bars.append({"symbol": symbol, "date": day, **{k: str(item[k]) for k in ("open", "high", "low", "close")},
                         "volume": str(dec(item["vol"]) * (100 if api == "daily" else 1)), "amount": str(dec(item.get("amount", 0)) * (1000 if api == "daily" else 1)),
                         "vwap_value": val, "vwap_method": "amount_volume_verified" if val else "unavailable", "vwap_session": "documented_daily_scope" if val else "unverified",
                         "quality_status": "verified" if val else "unverified", "source": "Tushare/" + api,
                         "available_at": day + "T21:00:00+08:00", "status": "trading", "settlement": item.get("settle")})
        return {"bars": bars, "raw": raw, "source": {"provider": "Tushare/" + api, "downloaded": now(), "scope_evidence": scope_evidence, "actions": "not_imported"}}
