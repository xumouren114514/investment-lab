from investment_lab.common import PROJECT, atomic_write, now, read_json
from investment_lab.data.store import Store
from investment_lab.data.universe import universe

store = Store()
report = {"verified_at": now(), "selection_date": universe(store)["selection_date"], "instruments": []}
sample = read_json(store.root / "state/public-sample-report.json")
for item in universe(store)["instruments"]:
    row = {**item, "listing_verification": "pending", "downloaded_ohlcv": None, "downloaded_amount": None,
           "downloaded_vwap": None, "downloaded_actions": None, "vendor_claimed_start": None, "gaps": "未下载，未知",
           "vwap_scope": "未验证/不可用", "fee_status": "参见 DATA_SOURCES.md，未购买", "retention_license": "需确认长期留存/订阅结束/备份/对他人展示", "sample_verification": "not_downloaded"}
    if item["id"] == "US:AAPL":
        data = sample["coverage"][0]
        row.update(downloaded_ohlcv={"from": data["first"], "to": data["last"], "rows": data["rows"]}, gaps=data["missing_sessions"],
                   vwap_scope="EODHD EOD 不提供 VWAP/成交额，成交量调整口径未独立核实", vendor_claimed_start="1980-12-12",
                   sample_verification="real_ohlcv_downloaded_vwap_blocked", snapshot=sample["snapshot"])
    report["instruments"].append(row)
atomic_write(PROJECT / "docs/data_coverage.json", report)
print({"coverage_rows": len(report["instruments"]), "actual_ohlcv_samples": 1})
