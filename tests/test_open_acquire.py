from copy import deepcopy
import pytest

from investment_lab.data.store import Store
from investment_lab.data.open_acquire import acquire, target_for
from investment_lab.data.open_sources import bounded_process


def instrument(code):
    return {"id": "CN:" + code, "code": code, "name": "fixture", "market": "CN", "currency": "CNY", "kind": "stock"}


def test_acquisition_resume_uses_validated_immutable_snapshot(monkeypatch, tmp_path, small):
    from investment_lab.data import open_acquire as module
    calls = []
    class Adapter:
        def fetch(self, symbol, start, end, **options):
            calls.append(symbol)
            bars = deepcopy(small["bars"])
            for b in bars:
                b["symbol"] = symbol
            return {"bars": bars, "sessions": small["sessions"], "raw": {"original": True}, "source": {"provider": "fixture"}}
    monkeypatch.setattr(module, "universe", lambda store: {"instruments": [instrument("600519")]})
    monkeypatch.setattr(module, "BaoStock", Adapter)
    monkeypatch.setattr(module, "completed_session", lambda market: "2024-01-10")
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    store = Store(tmp_path)
    first = acquire(store, "universe", "2024-01-04", "2024-01-10")
    second = acquire(store, "universe", "2024-01-04", "2024-01-10")
    assert len(calls) == 1 and second["items"][0]["cached"]
    assert first["items"][0]["snapshot"] == second["items"][0]["snapshot"]
    assert first["strict_backtest_ready"] is False


def test_repeated_provider_failure_defers_rest_without_fake_completion(monkeypatch, tmp_path):
    from investment_lab.data import open_acquire as module
    calls = []
    class Adapter:
        def fetch(self, *args, **options):
            calls.append(1)
            raise ValueError("network unavailable")
    monkeypatch.setattr(module, "universe", lambda store: {"instruments": [instrument(c) for c in ("600519", "600036", "600900", "600887")]})
    monkeypatch.setattr(module, "BaoStock", Adapter)
    monkeypatch.setattr(module, "completed_session", lambda market: "2024-01-10")
    store = Store(tmp_path)
    report = acquire(store, "universe", "2024-01-04", "2024-01-10")
    assert len(calls) == 3 and report["deferred"] == 1 and report["failed"] == 3
    assert report["status"] == "partial_failure" and not store.datasets()


def test_provider_process_timeout_is_bounded():
    import sys
    import time
    started = time.monotonic()
    with pytest.raises(ValueError, match="超时"):
        bounded_process([sys.executable, "-c", "import time; time.sleep(60)"], {}, timeout=0.3)
    assert time.monotonic() - started < 5


def test_cn_index_mapping_does_not_use_equity_exchange_heuristic():
    i = instrument("INDEX_510300")
    i.update(id="CN:INDEX_510300", kind="index")
    target = target_for(i)
    assert target["symbol"] == "000300" and target["adapter_options"]["secid"] == "1.000300"
    alternate = target_for(i, cn_provider="baostock")
    assert alternate["symbol"] == "sh.000300" and alternate["adapter_options"] == {"kind": "index"}
    assert alternate["dataset"] != target["dataset"]


def test_star_board_target_has_verified_whole_share_order_rule():
    i = instrument("688981")
    i["exchange"] = "XSHG"
    security = target_for(i)["security"]
    assert security["lot"] == 1 and security["lot_verified"] is True
    assert security["minimum_buy_quantity"] == 200
    assert security["minimum_sell_quantity"] == 200
    assert "execution_blocked" not in security


def test_requested_instruments_must_belong_to_selected_scope(tmp_path):
    with pytest.raises(ValueError, match="指定标的"):
        acquire(Store(tmp_path), instruments=["unknown"])


def test_transfer_retains_historical_parents_and_original_snapshot_hashes(tmp_path, small):
    import runpy
    from investment_lab.common import PROJECT
    transfer = runpy.run_path(str(PROJECT / "scripts/import_open_snapshots.py"))["transfer"]
    source, destination = Store(tmp_path / "source"), Store(tmp_path / "target")
    args = {**small, "name": "公开资源 · CN:600519 · fixture", "synthetic": False}
    for b in args["bars"]:
        b["source"] = "fixture"
    parent = source.ingest(**args, raw={"original": True})
    parent_raw = source.manifest(parent)["raw"]
    child = source.ingest(**{**args, "source": {"parent_snapshot": parent}}, raw={"parent_raw": parent_raw})
    result = transfer(source, destination)
    assert result["versions"] == 2 and destination.datasets()[0]["id"] == child
    assert destination.load(parent) == source.load(parent)
    assert destination.get_object("raw", parent_raw) == {"original": True}
    assert transfer(source, destination)["versions"] == 2  # Idempotent replay.
