from investment_lab.common import atomic_write, digest, read_json, now
from investment_lab.maintenance.backup import backup


def backup_if_changed(store):
    # Track irreplaceable inputs and completed run requests, not logs/progress/audit writes.
    entries = {}
    for area, pattern in (("user_strategies", "*.py"), ("local_config", "*.json"), ("manifests", "*.json"), ("runs", "result.json")):
        for path in sorted((store.root / area).rglob(pattern)):
            entries[path.relative_to(store.root).as_posix()] = digest(path.read_bytes())
    key = digest(entries)
    checkpoint = store.root / "state/daily-backup.json"
    old = read_json(checkpoint) if checkpoint.exists() else None
    if old and old["fingerprint"] == key:
        return {"status": "unchanged", "backup_id": old["backup_id"]}
    result = backup(store)
    atomic_write(checkpoint, {"fingerprint": key, "backup_id": result["id"], "created": now()})
    return {"status": "backed_up", **result}
