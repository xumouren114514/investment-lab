"""Import verified immutable research stores after a consistent destination backup."""
import argparse
import json
from pathlib import Path

from investment_lab.common import atomic_write, digest, encoded, now
from investment_lab.data.store import Store
from investment_lab.maintenance.backup import backup


def transfer(source, destination):
    if source.root == destination.root:
        raise ValueError("源目录与目标目录相同")
    # Include historical raw parents and manifests, not only current heads.
    for path in (source.root / "raw").glob("*.json.gz"):
        key = path.name.removesuffix(".json.gz")
        if destination.put_object("raw", source.get_object("raw", key)) != key:
            raise ValueError("原始对象哈希不一致")
    with source.connect() as cx:
        versions = [dict(r) for r in cx.execute("SELECT id,created FROM datasets ORDER BY created,id")]
    heads = {r["name"]: r["id"] for r in source.datasets()}
    for item in versions:
        manifest, bars = source.load(item["id"])
        if manifest["synthetic"] or not manifest["name"].startswith(("公开资源 · ", "参考序列 · ", "中金所官方日统计 · ")):
            raise ValueError("仅允许导入隔离的公开资源数据集")
        for part in manifest["partitions"]:
            key = destination.put_object("market", source.get_object("market", part["hash"]))
            if key != part["hash"]:
                raise ValueError("行情对象哈希不一致")
        # Store.load verifies all referenced objects. Preserve original creation time and hash.
        with destination.lock(), destination.connect() as cx:
            atomic_write(destination.root / "manifests" / (item["id"] + ".json"), manifest)
            cx.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?)", (item["id"], item["created"], encoded(manifest).decode()))
    with destination.lock(), destination.connect() as cx:
        for name, snapshot in heads.items():
            previous = cx.execute("SELECT snapshot FROM heads WHERE name=?", (name,)).fetchone()
            # Never silently replace a destination's independently advanced version.
            source_ids = {v["id"] for v in versions}
            if previous and previous[0] not in source_ids:
                raise ValueError("目标已有不同版本，请先核查: " + name)
            cx.execute("INSERT OR REPLACE INTO heads VALUES(?,?)", (name, snapshot))
        destination.audit("import_open_snapshots", {"source": str(source.root), "versions": len(versions), "heads": heads}, cx)
    state_files = list((source.root / "state/open-resource-history").glob("*.json"))
    latest = source.root / "state/open-resource-report.json"
    if latest.exists():
        state_files.append(latest)
    for path in state_files:
        value = json.loads(path.read_text(encoding="utf-8"))
        atomic_write(destination.root / "state/open-resource-history" / (digest(value) + ".json"), value)
    evidence = source.root / "state/open-source-evidence.json"
    if evidence.exists():
        value = json.loads(evidence.read_text(encoding="utf-8"))
        atomic_write(destination.root / "state/open-source-evidence" / (digest(value) + ".json"), value)
    return {"source": str(source.root), "versions": len(versions), "heads": len(heads)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--data", required=True)
    args = parser.parse_args()
    target = Store(args.data)
    checkpoint = backup(target)
    report = {"created": now(), "before_backup": checkpoint, "imports": []}
    for root in args.source:
        report["imports"].append(transfer(Store(root), target))
        atomic_write(target.root / "state/open-resource-import.json", report)
    print(json.dumps(report, ensure_ascii=False))
