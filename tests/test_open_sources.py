from copy import deepcopy
import json
import pytest

from investment_lab.common import dec
from investment_lab.data.open_sources import normalize_baostock, cash_actions, ratio_fields, Eastmoney, YahooChart, parse_cffex_csv
from investment_lab.engine.core import preflight


def test_baostock_exact_share_units_and_suspension():
    raw = {"code": "sh.600519", "date": "2024-01-02", "open": "1715", "close": "1685.01", "high": "1718.19", "low": "1678.10",
           "volume": "3215644", "amount": "5440082548.08", "preclose": "1726", "adjustflag": "3", "tradestatus": "1"}
    bar = normalize_baostock("sh.600519", [raw])[0]
    assert dec(bar["vwap_value"]) == dec(raw["amount"]) / dec(raw["volume"])
    assert bar["quality_status"] == "unverified"  # Unit proof does not establish trade scope.
    raw.update(volume="0", amount="0", tradestatus="0")
    suspended = normalize_baostock("sh.600519", [raw])[0]
    assert suspended["status"] == "suspended" and suspended["vwap_value"] is None
    raw["adjustflag"] = "2"
    with pytest.raises(ValueError, match="复权"):
        normalize_baostock("sh.600519", [raw])


def test_ratio_outside_range_not_clamped_or_marked_verified():
    bar = ratio_fields("5000", "100", "9", "11", verified=True)
    assert bar["amount_volume_candidate"] == "50"
    assert bar["vwap_value"] is None and bar["quality_status"] == "unavailable"


def test_actions_keep_payment_date_and_refuse_stock_dividend_guess():
    row = {"dividOperateDate": "2024-06-19", "dividPayDate": "2024-06-24", "dividCashPsBeforeTax": "1.2", "dividStocksPs": "0"}
    actions, unresolved = cash_actions("A", [row])
    assert actions[0]["pay_date"] == "2024-06-24" and actions[0]["amount"] == "1.2" and not unresolved
    row["dividStocksPs"] = "0.5"
    actions, unresolved = cash_actions("A", [row])
    assert not actions and unresolved
    row.update(dividStocksPs="0", dividPayDate="")
    assert not cash_actions("A", [row])[0]


def test_eastmoney_hand_conversion_and_identity(monkeypatch):
    response = {"rc": 0, "data": {"code": "600519", "name": "test", "klines": ["2024-01-02,10,10,11,9,123,123456,1,1,1,1"]}}
    monkeypatch.setattr("investment_lab.data.open_sources.request_json", lambda u: response)
    bar = Eastmoney().fetch("600519", "2024-01-01", "2024-01-03", market="CN")["bars"][0]
    assert bar["volume"] == "12300" and dec(bar["vwap_value"]) == dec(123456) / 12300
    assert bar["quality_status"] == "unverified"
    response["data"]["code"] = "999999"
    with pytest.raises(ValueError, match="代码不匹配"):
        Eastmoney().fetch("600519", "2024-01-01", "2024-01-03", market="CN")


def test_index_ratio_is_never_an_executable_index_vwap(monkeypatch):
    response = {"rc": 0, "data": {"code": "HSI", "klines": ["2024-01-02,10,10,11,9,123,1230,1,1,1,1"]}}
    monkeypatch.setattr("investment_lab.data.open_sources.request_json", lambda u: response)
    bar = Eastmoney().fetch("HSI", "2024-01-01", "2024-01-03", market="HK", secid="100.HSI", kind="index")["bars"][0]
    assert bar["vwap_value"] is None and bar["amount_volume_candidate"] is None


def test_cffex_multiplier_and_single_side_turnover():
    csv = "合约代码,今开盘,最高价,最低价,成交量,成交金额,今收盘,今结算\nIF2409,3300,3310,3290,100,9900,3305,3302\nIC2409,5000,5010,4990,10,1000,5001,5002\nIF连续,3300,3310,3290,100,9900,3305,3302\n"
    bars = parse_cffex_csv(csv.encode("gb18030"), "2024-09-02")
    assert len(bars) == 2
    assert bars[0]["vwap_value"] == "3300" and bars[1]["vwap_value"] == "5000"
    assert all(b["quality_status"] == "verified" for b in bars)
    assert bars[0]["settlement"] == "3302"


def test_yahoo_split_adjusted_prices_cannot_be_misrepresented(monkeypatch):
    raw = {"chart": {"result": [{"meta": {"symbol": "AAPL", "currency": "USD", "exchangeTimezoneName": "America/New_York"},
             "timestamp": [1704205800], "indicators": {"quote": [{"open": [10], "high": [11], "low": [9], "close": [10], "volume": [100]}]}}]}}
    monkeypatch.setattr("investment_lab.data.open_sources.request_json", lambda u: raw)
    result = YahooChart().fetch("AAPL", "2024-01-01", "2024-01-03")
    assert result["security_patch"]["execution_blocked"]
    assert result["bars"][0]["price_basis"] == "split_adjusted_reference_only"
    assert result["bars"][0]["vwap_value"] is None


def test_unverified_ratio_and_reference_only_data_fail_closed(small, config):
    manifest = {k: v for k, v in small.items() if k != "bars"}
    bars = deepcopy(small["bars"])
    bars[0]["quality_status"] = "unverified"
    with pytest.raises(ValueError, match="严格 VWAP"):
        preflight(manifest, bars, config)
    manifest["securities"]["A"]["execution_blocked"] = "尚未还原交易价格"
    config.mode = "close_research"
    config.allow_unverified_actions = True
    with pytest.raises(ValueError, match="尚未还原"):
        preflight(manifest, small["bars"], config)


def test_hong_kong_weather_closures_use_exchange_notices():
    from investment_lab.data.adapters import trading_sessions
    days = trading_sessions("HK", "2023-08-31", "2023-09-11")
    assert "2023-09-01" not in days and "2023-09-08" not in days
    assert "2023-09-04" in days and "2023-09-11" in days


def test_baostock_silent_pagination_disconnect_is_not_success():
    from investment_lab.data.baostock_worker import collect
    from baostock.common.contants import BAOSTOCK_PER_PAGE_COUNT
    class Result:
        error_code = "0"
        fields = ["date"]
        data = [["2024-01-02"]] * BAOSTOCK_PER_PAGE_COUNT
        cur_row_num = BAOSTOCK_PER_PAGE_COUNT
        def next(self):
            return False
    result = Result()
    with pytest.raises(ValueError, match="分页中断"):
        collect(result)
    result.data = []  # SDK's normal, confirmed empty EOF page.
    assert collect(result) == []


def test_baostock_empty_fields_remain_explicit_gaps():
    row = {"code": "sh.600519", "date": "2024-01-02", "open": "10", "close": "10", "high": "11", "low": "9",
           "volume": "", "amount": "", "preclose": "10", "adjustflag": "3", "tradestatus": "0"}
    excluded = []
    assert normalize_baostock("sh.600519", [row], excluded) == []
    assert excluded[0]["date"] == "2024-01-02" and excluded[0]["raw"]["volume"] == ""
    with pytest.raises(ValueError, match="字段缺失"):
        normalize_baostock("sh.600519", [row])


def test_yahoo_hk_zero_volume_holiday_placeholder_is_excluded(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    dates = ("2023-09-01", "2023-09-04")
    raw = {"chart": {"result": [{"meta": {"symbol": "0005.HK", "currency": "HKD", "exchangeTimezoneName": "Asia/Hong_Kong"},
           "timestamp": [int(datetime.fromisoformat(d).replace(tzinfo=ZoneInfo("Asia/Hong_Kong")).timestamp()) for d in dates],
           "indicators": {"quote": [{"open": [10, 10], "high": [11, 11], "low": [9, 9], "close": [10, 10], "volume": [0, 100]}]}}]}}
    monkeypatch.setattr("investment_lab.data.open_sources.request_json", lambda u: raw)
    result = YahooChart().fetch("0005.HK", dates[0], dates[1], market="HK")
    assert [b["date"] for b in result["bars"]] == [dates[1]]
    assert result["source"]["excluded"][0]["date"] == dates[0]
    assert result["raw"] == raw and result["security_patch"]["execution_blocked"]


def test_baostock_index_identity_and_non_tradable_ratio(monkeypatch):
    from investment_lab.data.open_sources import BaoStock
    raw = {"prices": [{"code": "sh.000300", "date": "2024-01-02", "open": "10", "close": "10", "high": "11", "low": "9",
           "volume": "100", "amount": "1000", "preclose": "10", "adjustflag": "3", "tradestatus": "1"}],
           "basic": [{"code": "sh.000300", "type": "2", "ipoDate": "2005-04-08"}],
           "calendar": [{"calendar_date": "2024-01-02", "is_trading_day": "1"}]}
    monkeypatch.setattr("investment_lab.data.open_sources.bounded_process", lambda *a, **kw: raw)
    result = BaoStock().fetch("sh.000300", "2024-01-02", "2024-01-03", kind="index")
    assert result["bars"][0]["vwap_value"] is None and result["bars"][0]["amount_volume_candidate"] is None
    raw["prices"].insert(0, {**raw["prices"][0], "date": "2005-01-04"})
    result = BaoStock().fetch("sh.000300", "2005-01-01", "2024-01-03", kind="index")
    assert len(result["bars"]) == 1 and result["source"]["excluded"][0]["date"] == "2005-01-04"
    with pytest.raises(ValueError, match="证券类型"):
        BaoStock().fetch("sh.000300", "2024-01-02", "2024-01-03", kind="stock")
