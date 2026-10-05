from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from investment_lab import SCHEMA_VERSION, __version__
from investment_lab.common import PROJECT, atomic_write, digest, encoded, now, read_json, within
from investment_lab.data.store import Store
from investment_lab.data.universe import universe

MANAGED = "investment-lab-backup-v1"


def private_project_files():
    """Explicit private handoff scope, never a recursive copy of the checkout."""
    names = {"TASK_STATE.md", "config/universe.yaml", "docs/SPEC.md", "docs/ACCEPTANCE.md",
             "docs/OPEN_RESOURCE_COVERAGE.md", "docs/STRATEGY_ASSISTANT.md", "docs/data_coverage.json"}
    for pattern in ("*-verification.json", "*-delivery.json"):
        names.update(path.relative_to(PROJECT).as_posix() for path in (PROJECT / "docs").glob(pattern))
    names.update(path.relative_to(PROJECT).as_posix() for path in (PROJECT / "docs/changes").rglob("*.md"))
    for name in sorted(names):
        path = PROJECT / name
        if path.exists():
            if path.is_symlink() or not path.resolve().is_relative_to(PROJECT.resolve()):
                raise ValueError("私有交接备份不跟随链接或项目外路径")
            if path.is_file():
                yield name, path


def _has_secret(value):
    if isinstance(value, dict):
        return any(any(word in str(key).lower() for word in ("token", "secret", "password", "api_key"))
                   or _has_secret(child) for key, child in value.items())
    return any(_has_secret(child) for child in value) if isinstance(value, list) else False


def _private_secret(payload, suffix):
    text = payload.decode("utf-8-sig")
    if suffix == ".json" and _has_secret(json.loads(text)):
        return True
    # Detect credential values, not documentation that merely names an environment variable.
    return bool(re.search(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|"
                          r"\bgithub_pat_[A-Za-z0-9_]{30,}|\bAKIA[A-Z0-9]{16}\b|"
                          r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", text))


def backup_root(store):
    config = store.root / "local_config" / "backup.json"
    value = read_json(config).get("root") if config.exists() else None
    root = Path(value or PROJECT.parent / "investment_lab_backups").resolve()
    if root.is_relative_to(store.root) or store.root.is_relative_to(root) or root == PROJECT or root.is_relative_to(PROJECT):
        raise ValueError("备份目录必须与数据和代码目录分离")
    return root


def backup(store, destination=None):
    root = Path(destination).resolve() if destination else backup_root(store)
    if root.is_relative_to(store.root) or store.root.is_relative_to(root) or root.is_relative_to(PROJECT):
        raise ValueError("备份目录不得嵌入源目录")
    root.mkdir(parents=True, exist_ok=True)
    marker = root / "managed.json"
    if marker.exists() and read_json(marker).get("format") != MANAGED:
        raise ValueError("该目录非本工具受管目录")
    if not marker.exists() and any(root.iterdir()):
        raise ValueError("首次备份须使用空目录")
    atomic_write(marker, {"format": MANAGED})
    bid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    staging = root / "points" / (bid + ".pending")
    staging.mkdir(parents=True)
    objects, skipped = {}, []
    with store.lock():
        # Use SQLite's consistent backup API while all application writers are coordinated.
        temp_db = staging / "index.sqlite"
        with store.connect() as cx:
            dest = sqlite3.connect(temp_db)
            try:
                cx.backup(dest)
            finally:
                dest.close()
        def archive_bytes(relative, payload):
            key = digest(payload)
            obj = root / "objects" / key[:2] / key
            if not obj.exists():
                atomic_write(obj, payload)
            if digest(obj.read_bytes()) != key:
                raise ValueError("备份对象校验失败")
            objects[relative] = {"sha256": key, "bytes": len(payload)}
        def archive(relative, path):
            archive_bytes(relative, path.read_bytes())
        archive("state/index.sqlite", temp_db)
        for area in ("user_strategies", "raw", "market", "manifests", "runs", "state", "handover"):
            for path in sorted((store.root / area).rglob("*")):
                if not path.is_file() or path.suffix in (".tmp", ".log") or (area == "state" and path.suffix == ".lock") or "__pycache__" in path.parts or path.name.startswith("index.sqlite"):
                    continue
                if path.is_symlink():
                    raise ValueError("备份不跟随符号链接")
                archive(path.relative_to(store.root).as_posix(), path)
        # Exact allowlist. Provider credentials live only in env/DPAPI, never in these documents.
        for name in ("universe.json", "backup.json", "providers.json"):
            path = store.root / "local_config" / name
            if path.exists():
                value = read_json(path)
                if _has_secret(value):
                    skipped.append("local_config/" + name + "：含敏感键，未备份")
                else:
                    archive("local_config/" + name, path)
        # Capture the effective fallback configuration without modifying the live configuration.
        # Restores then use local_config/universe.json even when the public checkout has no private YAML.
        effective = universe(store)
        if _has_secret(effective):
            skipped.append("local_config/universe.json：有效标的池含敏感键，未备份")
        elif "local_config/universe.json" not in objects:
            archive_bytes("local_config/universe.json", encoded(effective))
        for name, path in private_project_files():
            payload = path.read_bytes()
            if _private_secret(payload, path.suffix):
                skipped.append("handover/project/" + name + "：疑似包含凭据，未备份")
                continue
            archive_bytes("handover/project/" + name, payload)
        temp_db.unlink()
    manifest = {"format": MANAGED, "id": bid, "created": now(), "complete": True, "schema": SCHEMA_VERSION, "app_version": __version__,
                "source": str(store.root), "files": objects, "skipped": skipped, "secrets": "未备份 API 密钥，恢复后重新输入", "protected": False,
                "scope": {"private_project": "恢复到数据目录 handover/project；不覆盖源码目录",
                          "effective_universe": "local_config/universe.json", "browser_storage": "未包含；浏览器需另行导出"}}
    atomic_write(staging / "manifest.json", manifest)
    atomic_write(staging / "manifest.sha256", digest(manifest).encode())
    final = root / "points" / bid
    os.replace(staging, final)
    verify(root, bid)
    store.audit("backup_complete", {"id": bid, "path": str(final), "files": len(objects)})
    return {"id": bid, "path": str(final), "files": len(objects), "skipped": skipped, "protection": "当前仅验证本地恢复能力；未验证另一物理磁盘副本"}


def verify(root, bid):
    root = Path(root).resolve()
    point = within(root / "points", bid)
    manifest = read_json(point / "manifest.json")
    if manifest.get("format") != MANAGED or not manifest.get("complete") or digest(manifest) != (point / "manifest.sha256").read_text().strip():
        raise ValueError("备份清单校验失败或未完成")
    for name, info in manifest["files"].items():
        within(Path("C:/restore-validation") if os.name == "nt" else Path("/restore-validation"), name)
        key = info["sha256"]
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("无效对象哈希")
        path = root / "objects" / key[:2] / key
        payload = path.read_bytes()
        if digest(payload) != key or len(payload) != info["bytes"]:
            raise ValueError(f"备份文件校验失败 {name}")
    return manifest


def restore(root, bid, destination):
    destination = Path(destination).resolve()
    root = Path(root).resolve()
    manifest = verify(root, bid)
    if manifest["schema"] != SCHEMA_VERSION:
        raise ValueError("schema 不兼容，请从相应版本恢复")
    if destination.exists() or destination.is_relative_to(root) or root.is_relative_to(destination) or destination.is_relative_to(PROJECT):
        raise ValueError("恢复目标必须是不存在的独立目录；不能覆盖当前数据")
    destination.mkdir(parents=True)
    for name, info in manifest["files"].items():
        key = info["sha256"]
        atomic_write(within(destination, name), (root / "objects" / key[:2] / key).read_bytes())
    restored = Store(destination)
    with restored.connect() as cx:
        integrity = cx.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = cx.execute("PRAGMA foreign_key_check").fetchall()
        # Processes from a previous machine cannot remain running after restoration.
        cx.execute("UPDATE runs SET status='interrupted' WHERE status IN ('queued','running')")
    if integrity != "ok" or foreign:
        raise ValueError("恢复数据库检查失败")
    for data in restored.datasets():
        restored.load(data["id"])
    atomic_write(destination / "state/restore-report.json", {"backup": bid, "created": now(), "integrity": integrity, "query_verified": True, "backtest_reproduced": False})
    return {"destination": str(destination), "integrity": integrity, "datasets": len(restored.datasets()), "backtest_reproduced": False}


def points(root):
    root = Path(root)
    result = []
    if (root / "points").exists():
        for point in sorted((root / "points").iterdir(), reverse=True):
            if point.is_dir() and not point.name.endswith(".pending"):
                try:
                    data = read_json(point / "manifest.json")
                    result.append({k: data.get(k) for k in ("id", "created", "complete", "app_version", "protected")})
                except (OSError, ValueError):
                    continue
    return result


def retention_preview(root):
    """Preview only. No object deletion endpoint exists in this release."""
    values = points(root)
    keep = {p["id"] for p in values[:3]}  # most recent checkpoints, plus protected points
    daily, weekly, monthly = set(), set(), set()
    for p in values:
        stamp = datetime.fromisoformat(p["created"])
        d, w, m = stamp.date().isoformat(), stamp.strftime("%G-%V"), stamp.strftime("%Y-%m")
        chosen = p.get("protected")
        for key, bucket, maximum in ((d, daily, 7), (w, weekly, 4), (m, monthly, 3)):
            if key not in bucket and len(bucket) < maximum:
                bucket.add(key)
                chosen = True
        if chosen:
            keep.add(p["id"])
    return {"mode": "preview_only", "policy": "7日/4周/3月 + 最近3点 + 固定保护；发布引用另行保留", "keep": sorted(keep),
            "candidates": [p["id"] for p in values if p["id"] not in keep], "deleted": 0,
            "note": "尚未启用删除；首次清理需审阅精确范围及引用关系"}


def release_bundle(store, version):
    root = backup_root(store) / "releases"
    root.mkdir(parents=True, exist_ok=True)
    target = within(root, version + ".bundle")
    if target.exists():
        raise ValueError("已有同名发布包，不覆盖")
    r = subprocess.run(["git", "-C", str(PROJECT), "bundle", "create", str(target), "--all"], capture_output=True, text=True)
    if r.returncode:
        raise ValueError("Git bundle 建立失败，需先完成本地提交")
    verified = subprocess.run(["git", "-C", str(PROJECT), "bundle", "verify", str(target)], capture_output=True, text=True)
    if verified.returncode:
        raise ValueError("Git bundle 验证失败")
    return {"path": str(target), "sha256": digest(target.read_bytes()), "verified": True}
