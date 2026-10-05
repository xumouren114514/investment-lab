import importlib
import json

import pytest

from investment_lab.common import atomic_write, digest, read_json
from investment_lab.data.store import Store
from investment_lab.maintenance.backup import backup, restore, verify
from investment_lab.maintenance.daily import backup_if_changed


@pytest.fixture
def private_project(tmp_path, monkeypatch):
    project = tmp_path / "checkout"
    (project / "config").mkdir(parents=True)
    (project / "docs/changes").mkdir(parents=True)
    (project / "config/universe.yaml").write_text("version: 7\ninstruments: []\n", encoding="utf-8")
    (project / "TASK_STATE.md").write_text("当前交接资料", encoding="utf-8")
    (project / "docs/changes/latest.md").write_text("本次修订记录", encoding="utf-8")
    for module in ("investment_lab.maintenance.backup", "investment_lab.data.universe"):
        monkeypatch.setattr(importlib.import_module(module), "PROJECT", project)
    return project


def test_backup_restores_effective_fallback_and_private_handoff(tmp_path, private_project):
    store = Store(tmp_path / "data")
    point = backup(store, tmp_path / "backups")
    manifest = verify(tmp_path / "backups", point["id"])
    assert "local_config/universe.json" in manifest["files"]
    assert "handover/project/config/universe.yaml" in manifest["files"]
    assert "handover/project/TASK_STATE.md" in manifest["files"]
    assert "handover/project/docs/changes/latest.md" in manifest["files"]
    assert not (store.root / "local_config/universe.json").exists()
    restored_path = tmp_path / "restored"
    restore(tmp_path / "backups", point["id"], restored_path)
    # A fresh public checkout no longer needs the private YAML to read restored configuration.
    (private_project / "config/universe.yaml").unlink()
    from investment_lab.data.universe import universe
    assert universe(Store(restored_path)) == {"version": 7, "instruments": []}
    assert (restored_path / "handover/project/TASK_STATE.md").read_text(encoding="utf-8") == "当前交接资料"
    assert (private_project / "TASK_STATE.md").read_text(encoding="utf-8") == "当前交接资料"


def test_local_configuration_takes_precedence_and_suspected_credentials_are_skipped(tmp_path, private_project):
    store = Store(tmp_path / "data")
    atomic_write(store.root / "local_config/universe.json", {"version": 8, "instruments": []})
    atomic_write(store.root / "local_config/providers.json", {"password": "synthetic-secret"})
    (private_project / "docs/sample-verification.json").write_text(json.dumps({"api_key": "synthetic-secret"}))
    (private_project / "docs/changes/credential.md").write_text("ghp_" + "A" * 36)
    result = backup(store, tmp_path / "backups")
    manifest = verify(tmp_path / "backups", result["id"])
    for name in ("local_config/providers.json", "handover/project/docs/sample-verification.json",
                 "handover/project/docs/changes/credential.md"):
        assert name not in manifest["files"]
        assert any(name in message for message in result["skipped"])
    assert "synthetic-secret" not in json.dumps(manifest)
    restore(tmp_path / "backups", result["id"], tmp_path / "restored")
    assert read_json(tmp_path / "restored/local_config/universe.json")["version"] == 8


def test_daily_fingerprint_detects_private_handoff_and_fallback_changes(tmp_path, private_project):
    store = Store(tmp_path / "data")
    atomic_write(store.root / "local_config/backup.json", {"root": str(tmp_path / "backups")})
    first = backup_if_changed(store)
    assert first["status"] == "backed_up"
    assert backup_if_changed(store)["status"] == "unchanged"
    (private_project / "TASK_STATE.md").write_text("新增未完成验证", encoding="utf-8")
    second = backup_if_changed(store)
    assert second["status"] == "backed_up" and second["id"] != first["id"]
    assert backup_if_changed(store)["status"] == "unchanged"
    (private_project / "config/universe.yaml").write_text("version: 9\ninstruments: []\n")
    third = backup_if_changed(store)
    assert third["status"] == "backed_up" and third["id"] != second["id"]


def test_backup_verification_checks_declared_length(tmp_path, private_project):
    result = backup(Store(tmp_path / "data"), tmp_path / "backups")
    point = tmp_path / "backups/points" / result["id"]
    manifest = read_json(point / "manifest.json")
    manifest["files"]["local_config/universe.json"]["bytes"] += 1
    # Recompute the manifest checksum to isolate the object length invariant.
    atomic_write(point / "manifest.json", manifest)
    atomic_write(point / "manifest.sha256", digest(manifest).encode())
    with pytest.raises(ValueError, match="校验失败 local_config/universe.json"):
        verify(tmp_path / "backups", result["id"])
