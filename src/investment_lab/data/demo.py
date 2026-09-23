import math
from datetime import date, timedelta
from investment_lab.common import dec


def demo_payload():
    securities = {}
    for symbol, name, kind in (("DEMO.A", "合成成长股", "stock"), ("DEMO.B", "合成防御股", "stock"), ("DEMO.ETF", "合成基准 ETF", "etf")):
        securities[symbol] = {"name": name, "market": "US", "currency": "USD", "exchange": "SYNTHETIC", "kind": kind,
                              "timezone": "America/New_York", "lot": 1, "listed": "2022-01-03", "listing_verified": True,
                              "actions_verified": True, "margin_eligible": True}
    sessions, bars = [], []
    day = date(2022, 1, 3)
    while day <= date(2025, 12, 31):
        if day.weekday() < 5:
            sessions.append(day.isoformat())
        day += timedelta(days=1)
    for i, day in enumerate(sessions):
        for j, symbol in enumerate(securities):
            close = round(80 + j * 25 + i * (.035 + j * .008) + math.sin(i / (24 + j * 9)) * (12 - j * 3), 4)
            vwap = round(close + math.cos(i / 5) * .2, 4)
            volume = 500000 + (i % 10) * 10000
            bars.append({"symbol": symbol, "date": day, "open": str(round(close * .998, 4)), "high": str(round(close + 1, 4)), "low": str(round(close - 1, 4)),
                         "close": str(close), "volume": str(volume), "amount": str(dec(vwap) * volume), "vwap_value": str(vwap),
                         "vwap_method": "amount_volume_verified", "vwap_session": "synthetic_full_session", "quality_status": "verified",
                         "source": "deterministic_synthetic_v1", "available_at": day + "T22:00:00+00:00", "status": "trading"})
    return {"name": "合成演示 · 非真实行情", "securities": securities, "bars": bars, "sessions": sessions, "actions": [], "synthetic": True,
            "source": {"provider": "deterministic_synthetic_v1", "selection_date": "2026-09-20", "calendar": "明确合成工作日，非交易所真实日历", "purpose": "仅验证交互和流程，不用于投资结论"}}


def install_demo(store):
    return store.ingest(**demo_payload())
