from __future__ import annotations

import gzip
import json
import sqlite3
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path

from investment_lab import SCHEMA_VERSION
from investment_lab.common import atomic_write, data_root, digest, encoded, file_lock, now, read_json


class Store:
    def __init__(self, root=None):
        self.root = data_root(root)
        for directory in ("state", "raw", "market", "runs", "logs", "local_config", "user_strategies", "manifests", "quarantine"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        self.db = self.root / "state" / "index.sqlite"
        with self.connect() as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError(f"不兼容的 schema {version}，当前版本仅支持 {SCHEMA_VERSION}")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY, created TEXT, manifest TEXT);
                CREATE TABLE IF NOT EXISTS heads(name TEXT PRIMARY KEY, snapshot TEXT);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, created TEXT, status TEXT, kind TEXT, config TEXT, summary TEXT);
                CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, created TEXT, kind TEXT, detail TEXT);
                CREATE TABLE IF NOT EXISTS holdouts(id TEXT PRIMARY KEY, start TEXT, end TEXT, runs INTEGER DEFAULT 0, views INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
            """)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def lock(self):
        return file_lock(self.root / "state" / "writer.lock")

    def audit(self, kind, detail, conn=None):
        if conn is not None:
            conn.execute("INSERT INTO audit(created,kind,detail) VALUES(?,?,?)", (now(), kind, encoded(detail).decode()))
        else:
            with self.lock(), self.connect() as cx:
                self.audit(kind, detail, cx)

    def put_object(self, area, obj):
        payload = encoded(obj)
        key = digest(payload)
        path = self.root / area / (key + ".json.gz")
        if not path.exists():
            atomic_write(path, gzip.compress(payload, mtime=0))
        return key

    def get_object(self, area, key):
        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("无效内容哈希")
        payload = gzip.decompress((self.root / area / (key + ".json.gz")).read_bytes())
        if digest(payload) != key:
            raise ValueError("数据校验失败")
        return json.loads(payload)

    def ingest(self, name, securities, bars, actions=None, sessions=None, source=None, synthetic=False, raw=None):
        """Full logical dataset; immutable per-symbol/year objects make updates incremental."""
        from .validation import validate_dataset
        bars = sorted(bars, key=lambda x: (x["symbol"], x["date"]))
        errors = validate_dataset(securities, bars, actions or [], sessions or [], synthetic)
        if errors:
            with self.lock():
                key = self.put_object("quarantine", {"errors": errors, "source": source, "bars": bars})
            raise ValueError(f"数据已隔离 {key[:12]}: " + "; ".join(errors[:8]))
        groups = defaultdict(list)
        for bar in bars:
            groups[(bar["symbol"], bar["date"][:4])].append(bar)
        with self.lock(), self.connect() as cx:
            partitions = [{"symbol": key[0], "year": key[1], "hash": self.put_object("market", values), "count": len(values)} for key, values in sorted(groups.items())]
            manifest = {"schema": 1, "name": name, "securities": securities, "partitions": partitions,
                        "actions": actions or [], "sessions": sorted(set(sessions or [])), "source": source or {},
                        "synthetic": synthetic, "raw": self.put_object("raw", raw) if raw is not None else None}
            snapshot = digest(manifest)
            atomic_write(self.root / "manifests" / (snapshot + ".json"), manifest)
            cx.execute("INSERT OR IGNORE INTO datasets VALUES(?,?,?)", (snapshot, now(), encoded(manifest).decode()))
            previous = cx.execute("SELECT snapshot FROM heads WHERE name=?", (name,)).fetchone()
            cx.execute("INSERT OR REPLACE INTO heads VALUES(?,?)", (name, snapshot))
            if previous is None or previous[0] != snapshot:
                self.audit("dataset_revision", {"name": name, "previous": previous[0] if previous else None, "snapshot": snapshot}, cx)
        return snapshot

    def manifest(self, snapshot):
        with self.connect() as cx:
            row = cx.execute("SELECT manifest FROM datasets WHERE id=?", (snapshot,)).fetchone()
        if row is None:
            raise ValueError("数据快照不存在")
        result = json.loads(row[0])
        if digest(result) != snapshot:
            raise ValueError("快照清单校验失败")
        return result

    def load(self, snapshot):
        manifest = self.manifest(snapshot)
        bars = []
        for part in manifest["partitions"]:
            bars.extend(self.get_object("market", part["hash"]))
        return manifest, sorted(bars, key=lambda x: (x["date"], x["symbol"]))

    def datasets(self):
        with self.connect() as cx:
            rows = cx.execute("SELECT heads.name,datasets.id,datasets.created,datasets.manifest FROM heads JOIN datasets ON datasets.id=heads.snapshot ORDER BY created DESC").fetchall()
        return [{"name": r["name"], "id": r["id"], "created": r["created"], "manifest": json.loads(r["manifest"])} for r in rows]

    def runs(self, limit=50, offset=0):
        with self.connect() as cx:
            rows = cx.execute("SELECT * FROM runs ORDER BY created DESC LIMIT ? OFFSET ?", (min(limit, 200), offset)).fetchall()
        return [{**dict(r), "config": json.loads(r["config"]), "summary": json.loads(r["summary"] or "null")} for r in rows]
