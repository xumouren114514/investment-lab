from copy import deepcopy
import pytest


@pytest.fixture
def small():
    days = ["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"]
    sec = {"A": {"name": "手算样本", "market": "US", "currency": "USD", "timezone": "America/New_York", "kind": "stock", "lot": 1,
                 "listed": "2024-01-04", "listing_verified": True, "actions_verified": True, "margin_eligible": True}}
    bars = [{"symbol": "A", "date": d, "open": "10", "high": "11", "low": "9", "close": "10", "volume": "100000", "amount": "1000000",
             "vwap_value": "10", "vwap_method": "amount_volume_verified", "vwap_session": "full", "quality_status": "verified", "source": "test",
             "available_at": d + "T22:00:00+00:00", "status": "trading"} for d in days]
    return {"name": "test", "securities": sec, "bars": bars, "sessions": days, "actions": [], "synthetic": True, "source": {"provider": "synthetic"}}


@pytest.fixture
def config(small):
    from investment_lab.engine.models import Config
    return Config(start=small["sessions"][0], end=small["sessions"][-1], symbols=["A"], initial_cash="1000", commission_bps="0", slippage_bps="0", max_participation="1")


def run_small(payload, config, strategy):
    from investment_lab.engine.core import simulate
    manifest = {k: v for k, v in payload.items() if k != "bars"}
    return simulate(manifest, payload["bars"], config, strategy)
