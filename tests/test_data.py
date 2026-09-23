from copy import deepcopy
from datetime import datetime, timezone
import pytest
from investment_lab.data.store import Store
from investment_lab.data.validation import vwap, coverage
from investment_lab.data.adapters import completed_session, trading_sessions
from investment_lab.data import universe as universe_module


def test_vwap_units_and_missing():
    assert vwap(1000, 1000, 1000, 100, True) == "10"
    assert vwap(1000, 0, 1000, 100, True) is None
    assert vwap(1000, 1000, same_scope=False) is None
    assert vwap(None, 1000, same_scope=True) is None


def test_immutable_revision_idempotency_and_old_reads(tmp_path, small):
    store = Store(tmp_path / "data")
    first = store.ingest(**small)
    assert store.ingest(**small) == first
    small["bars"][0]["close"] = "10.5"
    second = store.ingest(**small)
    assert second != first
    assert store.load(first)[1][0]["close"] == "10"
    assert store.load(second)[1][0]["close"] == "10.5"
    with store.connect() as cx:
        assert cx.execute("SELECT count(*) FROM audit").fetchone()[0] == 2


def test_bad_rows_quarantined_and_good_data_survives(tmp_path, small):
    store = Store(tmp_path / "data")
    first = store.ingest(**small)
    small["bars"][0]["high"] = "1"
    with pytest.raises(ValueError, match="隔离"):
        store.ingest(**small)
    assert store.datasets()[0]["id"] == first
    assert len(list((store.root / "quarantine").glob("*"))) == 1


def test_listing_boundary_and_zero_volume_validation(tmp_path, small):
    store = Store(tmp_path / "data")
    small["securities"]["A"]["listed"] = "2024-01-05"
    with pytest.raises(ValueError, match="上市前"):
        store.ingest(**small)
    small["securities"]["A"]["listed"] = "2024-01-04"
    small["bars"][0]["volume"] = "0"
    with pytest.raises(ValueError, match="零成交量"):
        store.ingest(**small)


def test_market_ready_time_dst_and_holiday():
    assert completed_session("US", datetime(2024, 3, 11, 22, tzinfo=timezone.utc)) == "2024-03-08"
    assert completed_session("US", datetime(2024, 3, 12, 1, tzinfo=timezone.utc)) == "2024-03-11"
    assert "2024-01-01" not in trading_sessions("US", "2024-01-01", "2024-01-10")


def test_universe_loads_public_example_when_personal_config_is_absent(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    expected = {"version": 1, "instruments": []}
    (config_dir / "universe.example.yaml").write_text(
        "version: 1\ninstruments: []\n", encoding="utf-8"
    )
    monkeypatch.setattr(universe_module, "PROJECT", tmp_path)

    assert universe_module.universe(Store(tmp_path / "store")) == expected
