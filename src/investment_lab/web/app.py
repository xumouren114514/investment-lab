from __future__ import annotations

import json
import gzip
import re
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from investment_lab import __version__, SCHEMA_VERSION
from investment_lab.common import PROJECT, atomic_write, digest, encoded, now, read_json, within
from investment_lab.data.store import Store
from investment_lab.data.validation import coverage
from investment_lab.data.universe import universe, update_universe
from investment_lab.jobs import JobManager, create_run
from investment_lab.engine.reference import reference_available

_CREDENTIAL_FIELDS = {"token", "api_token", "api_key", "access_token", "secret", "secret_key",
                     "password", "authorization", "cookie"}
_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,})\b"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:api[_-]?token|access[_-]?token)\s*[=:]\s*[^&\s]{20,}"),
)
_MAX_IMPORT_BYTES = 128 * 1024 * 1024


class ImportBodyLimitMiddleware:
    def __init__(self, app, max_bytes):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if not (scope.get("type") == "http" and scope.get("method") == "POST" and
                scope.get("path") == "/api/imports"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        raw_length = headers.get(b"content-length")
        if raw_length is not None:
            try:
                content_length = int(raw_length)
            except ValueError:
                await self._reject(send, 400, "数据包长度无效")
                return
            if content_length < 0:
                await self._reject(send, 400, "数据包长度无效")
                return
            if content_length > self.max_bytes:
                await self._reject(send, 413, "解压后的 JSON 数据包不能超过128 MiB，请按标的拆分后导入。")
                return

        chunks = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(chunks) + len(chunk) > self.max_bytes:
                await self._reject(send, 413, "解压后的 JSON 数据包不能超过128 MiB，请按标的拆分后导入。")
                return
            chunks.extend(chunk)
            if not message.get("more_body", False):
                break

        body = bytes(chunks)
        replayed = False

        async def replay_body():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return {"type": "http.disconnect"}

        await self.app(scope, replay_body, send)

    @staticmethod
    async def _reject(send, status, detail):
        payload = encoded({"detail": detail})
        await send({"type": "http.response.start", "status": status,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode("ascii"))]})
        await send({"type": "http.response.body", "body": payload})


def _contains_credentials(value):
    if isinstance(value, dict):
        return any(str(key).lower() in _CREDENTIAL_FIELDS or _contains_credentials(child)
                   for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_credentials(child) for child in value)
    return isinstance(value, str) and any(pattern.search(value) for pattern in _CREDENTIAL_PATTERNS)


def create_app(root=None):
    store = Store(root)
    manager = JobManager(store)
    app = FastAPI(title="投资研究室", docs_url=None, redoc_url=None)
    app.add_middleware(ImportBodyLimitMiddleware, max_bytes=_MAX_IMPORT_BYTES)
    app.state.store, app.state.manager = store, manager
    app.state.open_task = None
    open_task_lock = threading.Lock()
    static = Path(__file__).parent / "static"

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        hostname = request.url.hostname
        if hostname not in ("127.0.0.1", "localhost", "::1", "testserver"):
            return JSONResponse({"detail": "仅接受本机域名"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if (origin and origin != str(request.base_url).rstrip("/")) or request.headers.get("x-lab-request") != "local-ui":
                return JSONResponse({"detail": "拒绝跨站写入；请从本机界面操作"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(RuntimeError)
    async def busy(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.get("/")
    def home():
        return FileResponse(static / "index.html")

    @app.get("/api/status")
    def status():
        from investment_lab.maintenance.backup import backup_root, points
        datasets = store.datasets()
        return {"version": __version__, "schema": SCHEMA_VERSION, "data_path": str(store.root), "project_path": str(PROJECT),
                "features": ["multi_snapshot"],
                "datasets": len(datasets), "real_datasets": sum(not d["manifest"]["synthetic"] for d in datasets), "backups": len(points(backup_root(store))),
                "last_update": read_json(store.root / "state/last-update.json") if (store.root / "state/last-update.json").exists() else None,
                "limits": ["真实供应商账号尚未配置或验证；查看数据覆盖", "日线成交与追保是有说明的近似", "本机可信 Python 进程隔离不等于安全沙箱"]}

    @app.get("/api/datasets")
    def datasets():
        return [{"id": d["id"], "name": d["name"], "created": d["created"], "synthetic": d["manifest"]["synthetic"],
                 "source": d["manifest"]["source"], "symbols": d["manifest"]["securities"],
                 "reference_research_available": reference_available(d["manifest"]),
                 "first": min(d["manifest"]["sessions"], default=None), "last": max(d["manifest"]["sessions"], default=None),
                 "rows": sum(p["count"] for p in d["manifest"]["partitions"])} for d in store.datasets()]

    @app.get("/api/exports/{snapshot}")
    def export_snapshot(snapshot: str):
        manifest, bars = store.load(snapshot)
        if manifest.get("composition"):
            raise ValueError("组合快照请分别导出其中的原始来源快照，以保留各自来源和覆盖边界。")
        # Scan the complete portable package: imported providers may attach fields beyond the standard schema.
        package = {"format": "investment-lab-snapshot", "format_version": 1,
                   "snapshot_id": snapshot, "manifest": manifest, "bars": bars}
        if _contains_credentials(package):
            raise ValueError("快照数据疑似包含凭据；为防止泄露，已阻止导出。请清理相关字段后重新导入。")
        # Keep the immutable manifest (and therefore its snapshot ID) unchanged.
        # Raw provider response objects and credentials are deliberately not part of this portable package.
        payload = gzip.compress(encoded(package), mtime=0)
        return Response(payload, media_type="application/gzip",
                        headers={"Content-Disposition": f'attachment; filename="investment-lab-{snapshot}.json.gz"'})

    @app.post("/api/imports")
    def import_snapshot(body: dict):
        if body.get("format") != "investment-lab-snapshot" or type(body.get("format_version")) is not int or body["format_version"] != 1:
            raise ValueError("不支持的数据包格式或版本。")
        manifest, bars, original = body.get("manifest"), body.get("bars"), body.get("snapshot_id")
        if not isinstance(manifest, dict) or not isinstance(bars, list) or not isinstance(original, str):
            raise ValueError("数据包缺少快照清单、行情或原始编号。")
        if not re.fullmatch(r"[0-9a-f]{64}", original):
            raise ValueError("原始快照编号无效。")
        if manifest.get("composition"):
            raise ValueError("组合快照需先分别导出其原始来源。")
        if digest(manifest) != original:
            raise ValueError("快照清单摘要不匹配；数据包可能损坏或被修改。")
        if _contains_credentials(body):
            raise ValueError("数据包疑似包含凭据；为防止保存或传播，已拒绝导入。")
        required = ("name", "securities", "sessions", "synthetic", "schema", "partitions")
        if (any(key not in manifest for key in required) or type(manifest.get("schema")) is not int or manifest["schema"] != 1 or
                not isinstance(manifest.get("name"), str) or not manifest["name"].strip() or len(manifest["name"]) > 200 or
                not isinstance(manifest.get("securities"), dict) or not manifest["securities"] or
                any(not isinstance(symbol, str) or not symbol or not isinstance(security, dict) or
                    not isinstance(security.get("market"), str) or not isinstance(security.get("currency"), str)
                    for symbol, security in manifest["securities"].items()) or
                not isinstance(manifest.get("sessions"), list) or not isinstance(manifest.get("partitions"), list) or
                not isinstance(manifest.get("synthetic"), bool) or not isinstance(manifest.get("source", {}), dict) or
                not isinstance(manifest.get("actions", []), list) or
                any(not isinstance(action, dict) for action in manifest.get("actions", [])) or
                not bars or
                any(not isinstance(session, str) for session in manifest["sessions"]) or
                any(not isinstance(row, dict) or not isinstance(row.get("symbol"), str) or
                    not isinstance(row.get("date"), str) for row in bars)):
            raise ValueError("快照清单缺少必要字段。")
        imported = store.ingest(name=manifest["name"], securities=manifest["securities"], bars=bars,
                                actions=manifest.get("actions", []), sessions=manifest["sessions"],
                                source=manifest.get("source", {}), synthetic=manifest["synthetic"], raw=None)
        return {"snapshot": imported, "original_snapshot": original, "name": manifest["name"],
                "rows": len(bars), "raw_response_included": False}

    @app.get("/api/coverage/{snapshot}")
    def get_coverage(snapshot: str):
        manifest, bars = store.load(snapshot)
        return {"snapshot": snapshot, "source": manifest["source"], "items": coverage(manifest, bars)}

    @app.get("/api/open-resources")
    def open_resources():
        report_path = store.root / "state/open-resource-report.json"
        proc = app.state.open_task
        active = proc is not None and proc.poll() is None
        report = read_json(report_path) if report_path.exists() else None
        if not active and report and report.get("status") == "running" and report.get("worker"):
            import psutil
            try:
                worker = psutil.Process(report["worker"]["pid"])
                active = worker.create_time() == report["worker"]["created"] and "fetch-open-data" in worker.cmdline()
            except psutil.Error:
                pass
        catalog_path = PROJECT / "config/open_resources.json"
        return {"catalog": read_json(catalog_path) if catalog_path.exists() else {},
                "report": report,
                "active": active, "exit_code": proc.poll() if proc is not None else None}

    @app.post("/api/open-resources")
    def fetch_open_resources(body: dict):
        scope = body.get("scope", "sample")
        if scope not in ("sample", "universe"):
            raise ValueError("下载范围无效")
        with open_task_lock:
            proc = app.state.open_task
            if proc is not None and proc.poll() is None:
                raise RuntimeError("公开资源下载正在进行")
            if open_resources()["active"]:
                raise RuntimeError("命令行公开资源下载正在进行")
            log = store.root / "logs/open-resource-download.log"
            with log.open("ab") as stream:
                proc = subprocess.Popen([sys.executable, "-m", "investment_lab.cli", "--data", str(store.root),
                                         "fetch-open-data", "--scope", scope], stdout=stream, stderr=stream,
                                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            app.state.open_task = proc
        return {"status": "started", "scope": scope, "pid": proc.pid}

    @app.get("/api/bars/{snapshot}")
    def get_bars(snapshot: str, symbol: str, offset: int = 0, limit: int = 50):
        _, bars = store.load(snapshot)
        selected = [b for b in bars if b["symbol"] == symbol]
        return {"total": len(selected), "items": selected[max(0, offset):max(0, offset) + max(1, min(limit, 200))]}

    @app.get("/api/universe")
    def get_universe():
        return universe(store)

    @app.post("/api/universe")
    def save_universe(instrument: dict):
        return update_universe(store, instrument)

    @app.get("/api/strategies")
    def strategies():
        from investment_lab.strategies.examples import CATALOG, NAMES
        return {"builtins": NAMES, "catalog": CATALOG, "custom": [p.relative_to(store.root / "user_strategies").as_posix() for p in (store.root / "user_strategies").rglob("*.py")]}

    @app.get("/api/strategy")
    def strategy_file(name: str):
        path = within(store.root / "user_strategies", name)
        if not path.exists() or path.suffix != ".py":
            raise ValueError("策略不存在")
        return {"name": name, "code": path.read_text(encoding="utf-8")}

    @app.post("/api/strategy")
    def save_strategy(body: dict):
        path = within(store.root / "user_strategies", body["name"])
        if path.suffix != ".py" or len(body["code"].encode()) > 1_000_000:
            raise ValueError("策略须为不超过 1 MB 的 .py 文件")
        try:
            compile(body["code"], body["name"], "exec")
        except SyntaxError as exc:
            raise ValueError(f"Python 语法错误：第 {exc.lineno} 行 {exc.msg}") from None
        with store.lock(), store.connect() as cx:
            previous = store.put_object("raw", {"name": body["name"], "code": path.read_text(encoding="utf-8")}) if path.exists() else None
            atomic_write(path, body["code"].encode("utf-8"))
            store.audit("strategy_save", {"name": body["name"], "previous": previous, "hash": digest(body["code"].encode())}, cx)
        return {"saved": body["name"], "previous_snapshot": previous}

    @app.post("/api/runs")
    def submit(body: dict):
        if any(p.poll() is None for p in manager.active.values()):
            raise ValueError("计算任务正在运行，请等待或取消")
        run_id = create_run(store, body)
        manager.start(run_id)
        return {"run_id": run_id}

    @app.get("/api/runs")
    def runs(offset: int = 0, limit: int = 50):
        return store.runs(max(1, min(limit, 100)), max(0, offset))

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        manager.cancel(run_id)
        return {"status": "cancelled"}

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str, sample: int = 0, section: str = "holdout"):
        path = within(store.root / "runs", run_id)
        with store.connect() as cx:
            row = cx.execute("SELECT status,summary FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="运行记录不存在，请刷新任务列表")
        request = read_json(path / "request.json")
        reference = reference_available(store.manifest(request["snapshot"]), request["config"]["symbols"])
        if not (path / "result.json").exists():
            error = read_json(path / "error.json") if (path / "error.json").exists() else None
            # The worker watchdog can record an exit only in SQLite, without error.json.
            if error is None and row["status"] in ("failed", "cancelled", "interrupted"):
                error = json.loads(row["summary"] or "null")
            return {"request": request, "status": row["status"], "reference_research_available": reference,
                    "progress": read_json(path / "progress.json") if (path / "progress.json").exists() else None,
                    "error": error}
        result = read_json(path / "result.json")
        experiments = None
        if request["kind"] == "rolling":
            experiments = {"metadata": result["metadata"], "summary": result["summary"], "samples": [{k: v for k, v in s.items() if k != "result"} for s in result["samples"]]}
            if not 0 <= sample < len(result["samples"]):
                raise ValueError("窗口索引无效")
            result = result["samples"][sample].get("result", {})
        elif request["kind"] == "holdout":
            if section not in ("development", "holdout"):
                raise ValueError("区间无效")
            experiments = {"metadata": result["metadata"], "development": result["development"]["result"]["metrics"], "holdout": result["holdout"]["result"]["metrics"]}
            result = result[section]["result"]
            with store.lock(), store.connect() as cx:
                cx.execute("UPDATE holdouts SET views=views+1 WHERE id=?", (request["holdout_id"],))
        reduced = {k: v for k, v in result.items() if k not in ("trades", "ledger", "orders", "benchmark")}
        curve = reduced.get("curve", [])
        if len(curve) > 650:
            step = max(1, len(curve) // 600)
            reduced["curve"] = curve[::step] + ([curve[-1]] if curve[-1] not in curve[::step] else [])
        benchmark = result.get("benchmark")
        if benchmark:
            reduced["benchmark"] = {"metrics": benchmark["metrics"], "curve": benchmark["curve"][::max(1, len(benchmark["curve"]) // 600)]}
        return {"request": request, "status": row["status"], "reference_research_available": reference, "result": reduced, "experiments": experiments}

    @app.get("/api/runs/{run_id}/table")
    def run_table(run_id: str, table: str = "trades", offset: int = 0, sample: int = 0, section: str = "holdout"):
        if table not in ("trades", "ledger", "orders"):
            raise ValueError("表名无效")
        path = within(store.root / "runs", run_id)
        request, result = read_json(path / "request.json"), read_json(path / "result.json")
        if request["kind"] == "rolling":
            result = result["samples"][sample].get("result", {})
        elif request["kind"] == "holdout":
            if section not in ("development", "holdout"):
                raise ValueError("区间无效")
            result = result[section]["result"]
        items = result.get(table, [])
        return {"items": items[max(0, offset):max(0, offset) + 100], "total": len(items)}

    @app.get("/api/backups")
    def backups():
        from investment_lab.maintenance.backup import backup_root, points, retention_preview
        root = backup_root(store)
        return {"root": str(root), "points": points(root), "retention": retention_preview(root)}

    @app.post("/api/backups")
    def make_backup():
        from investment_lab.maintenance.backup import backup
        if any(p.poll() is None for p in manager.active.values()):
            raise ValueError("请等待计算任务完成后建立检查点")
        return backup(store)

    @app.post("/api/restore")
    def restore_backup(body: dict):
        from investment_lab.maintenance.backup import backup_root, restore
        return restore(backup_root(store), body["id"], body["destination"])

    @app.post("/api/update")
    def update_data():
        from investment_lab.data.update import update
        # Runs in FastAPI's thread pool; the browser remains responsive and can query other endpoints.
        return update(store)

    @app.get("/api/docs/{name}")
    def document(name: str):
        if name not in ("DATA_SOURCES.md", "OPERATIONS.md", "ENGINE.md", "STRATEGIES.md", "OPEN_RESOURCES.md"):
            raise ValueError("文档不存在")
        return {"text": (PROJECT / "docs" / name).read_text(encoding="utf-8")}

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
