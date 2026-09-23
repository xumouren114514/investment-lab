"""Consistent backup, isolated restore and exact replay of the new runtime smoke run."""
import argparse
import base64
import json
from pathlib import Path

from fastapi.testclient import TestClient

from investment_lab.common import PROJECT, atomic_write, digest, now, read_json
from investment_lab.data.store import Store
from investment_lab.jobs import reproduce
from investment_lab.maintenance.backup import backup, backup_root, restore
from investment_lab.web.app import create_app


def verify(root, destination):
    store = Store(root)
    smoke = read_json(store.root / "state/open-resource-smoke.json")
    point = backup(store)
    result = restore(backup_root(store), point["id"], destination)
    restored = Store(destination)
    original_heads = {d["name"]: d["id"] for d in store.datasets()}
    if original_heads != {d["name"]: d["id"] for d in restored.datasets()}:
        raise ValueError("恢复后的数据集指针不一致")
    for snapshot in original_heads.values():
        restored.load(snapshot)  # Validate all referenced content-addressed objects.
    evidence_count = 0
    for path in (restored.root / "state/open-source-evidence").glob("*.json"):
        for item in read_json(path)["items"]:
            evidence = restored.get_object("raw", item["object"])
            if digest(base64.b64decode(evidence["body_base64"])) != item["sha256"]:
                raise ValueError("公开来源证据校验失败")
            evidence_count += 1
    replay = reproduce(restored, smoke["run_id"])
    if not replay["identical"]:
        raise ValueError("恢复后回测序列化结果不一致")
    result["backtest_reproduced"] = True
    with TestClient(create_app(destination)) as client:
        status = client.get("/api/status").json()
        resources = client.get("/api/open-resources").json()
        assert status["version"] == "0.2.0" and status["datasets"] == len(original_heads)
        assert len(resources["catalog"]["resources"]) == 19 and not resources["active"]
    report = {"created": now(), "version": "0.2.0",
              "backup": point, "restore": result, "data_heads_verified": len(original_heads), "source_evidence_verified": evidence_count,
              "smoke": smoke, "replay": replay, "restored_api": status,
              "strict_real_backtest_ready": False}
    atomic_write(PROJECT / "docs/open-resource-delivery.json", report)
    atomic_write(store.root / "state/open-resource-delivery.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True)
    parser.add_argument("--destination", required=True)
    args = parser.parse_args()
    verify(args.data, args.destination)
