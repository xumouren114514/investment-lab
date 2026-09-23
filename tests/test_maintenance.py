from dataclasses import asdict
from pathlib import Path
import pytest
from investment_lab.common import read_json
from investment_lab.data.store import Store
from investment_lab.jobs import create_run, execute_run, reproduce
from investment_lab.maintenance.backup import backup, restore, verify, retention_preview


def test_real_restore_and_reproduce(tmp_path, small, config):
    store = Store(tmp_path / "data")
    snapshot = store.ingest(**small)
    run_id = create_run(store, {"snapshot": snapshot, "config": asdict(config), "strategy": "buy_hold"})
    execute_run(store, run_id)
    store.root.joinpath("user_strategies/personal.py").write_text("personal = 1")
    point = backup(store, tmp_path / "backups")
    result = restore(tmp_path / "backups", point["id"], tmp_path / "restored")
    assert result["integrity"] == "ok"
    restored = Store(tmp_path / "restored")
    assert restored.load(snapshot)[1] == store.load(snapshot)[1]
    assert (restored.root / "user_strategies/personal.py").read_text() == "personal = 1"
    assert (restored.root / "runs" / run_id / "requirements.lock").read_bytes() == (store.root / "runs" / run_id / "requirements.lock").read_bytes()
    replay = reproduce(restored, run_id)
    assert replay["identical"]
    assert (restored.root / "runs" / replay["run_id"] / "requirements.lock").exists()
    assert (restored.root / "runs" / replay["run_id"] / "universe.json").exists()
    assert retention_preview(tmp_path / "backups")["deleted"] == 0
    with pytest.raises(ValueError, match="独立目录"):
        restore(tmp_path / "backups", point["id"], store.root)


def test_tamper_detection_and_no_nested_backup(tmp_path, small):
    store = Store(tmp_path / "data")
    store.ingest(**small)
    with pytest.raises(ValueError, match="嵌入"):
        backup(store, store.root / "backups")
    result = backup(store, tmp_path / "backups")
    manifest = verify(tmp_path / "backups", result["id"])
    key = next(iter(manifest["files"].values()))["sha256"]
    (tmp_path / "backups/objects" / key[:2] / key).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="校验"):
        verify(tmp_path / "backups", result["id"])


def test_daily_backup_only_when_irreplaceable_content_changes(tmp_path, small):
    from investment_lab.common import atomic_write
    from investment_lab.maintenance.daily import backup_if_changed
    store = Store(tmp_path / "data")
    store.ingest(**small)
    atomic_write(store.root / "local_config/backup.json", {"root": str(tmp_path / "backups")})
    first = backup_if_changed(store)
    assert first["status"] == "backed_up"
    assert backup_if_changed(store)["status"] == "unchanged"
    (store.root / "user_strategies/new.py").write_text("x=1")
    second = backup_if_changed(store)
    assert second["status"] == "backed_up" and second["id"] != first["id"]
