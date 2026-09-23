from __future__ import annotations

import importlib.util
import importlib.metadata
import json
import os
import random
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from investment_lab import ENGINE_VERSION, STRATEGY_API, SCHEMA_VERSION, __version__
from investment_lab.common import PROJECT, atomic_write, digest, encoded, now, read_json, within
from investment_lab.data.store import Store
from investment_lab.engine.models import Config


def code_fingerprint():
    files = {str(p.relative_to(PROJECT)): digest(p.read_bytes()) for p in sorted((PROJECT / "src").rglob("*")) if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".html", ".css", ".js")}
    return digest(files)


def runtime_dependencies():
    return {d.metadata["Name"].lower(): d.version for d in importlib.metadata.distributions() if d.metadata["Name"].lower() != "investment-lab"}


def git_state():
    def git(*args):
        r = subprocess.run(["git", "-C", str(PROJECT), *args], capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else None
    return {"commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain"))}


def create_run(store, request):
    config = Config(**request["config"])
    if "snapshots" in request:
        from investment_lab.data.compose import compose_snapshots
        snapshot = compose_snapshots(store, request["snapshots"])
        if request.get("snapshot") and request["snapshot"] != snapshot:
            raise ValueError("snapshot与snapshots不一致，请重新选择数据")
        request = {**request, "snapshot": snapshot}
    manifest = store.manifest(request["snapshot"])
    from investment_lab.data.compose import validate_composed_selection
    validate_composed_selection(manifest, config)
    sources = manifest.get("composition", {}).get("parents", [{"snapshot": request["snapshot"], "name": manifest["name"]}])
    request = {**request, "snapshots": [s["snapshot"] for s in sources], "data_sources": sources}
    kind = request.get("kind", "single")
    if kind not in ("single", "rolling", "holdout"):
        raise ValueError("任务类型无效")
    strategy = request.get("strategy", "buy_hold")
    run_id = uuid4().hex
    run_dir = store.root / "runs" / run_id
    source = {}
    if strategy == "custom":
        relative = request.get("strategy_file", "")
        path = within(store.root / "user_strategies", relative)
        if path.suffix != ".py" or not path.is_file():
            raise ValueError("策略应为 user_strategies 中的 Python 文件")
        # Snapshot project-local Python dependencies, never credentials/config directories.
        for p in sorted((store.root / "user_strategies").rglob("*.py")):
            if p.is_symlink():
                raise ValueError("策略快照不接受符号链接")
            source[p.relative_to(store.root / "user_strategies").as_posix()] = p.read_text(encoding="utf-8")
        entry = path.relative_to(store.root / "user_strategies").as_posix()
    else:
        from investment_lab.strategies.examples import NAMES
        if strategy not in NAMES:
            raise ValueError("策略不存在")
        source["examples.py"] = (PROJECT / "src/investment_lab/strategies/examples.py").read_text(encoding="utf-8")
        entry = "examples.py"
    if len(encoded(source)) > 10_000_000:
        raise ValueError("策略项目超过 10 MB，请拆出无关文件")
    lock_path = PROJECT / "requirements.lock"
    from investment_lab.data.universe import universe
    frozen_universe = universe(store)
    frozen = {**request, "config": asdict(config), "kind": kind, "strategy": strategy, "run_id": run_id,
              "created": now(), "strategy_hash": digest(source), "strategy_entry": entry,
              "engine_hash": code_fingerprint(), "engine_version": ENGINE_VERSION, "strategy_api": STRATEGY_API,
              "app_version": __version__, "schema": SCHEMA_VERSION, "git": git_state(), "python": sys.version,
              "dependency_hash": digest(lock_path.read_bytes()) if lock_path.exists() else None,
              "universe_hash": digest(frozen_universe), "universe_version": frozen_universe["version"],
              "runtime_dependencies": runtime_dependencies(),
              "synthetic": manifest["synthetic"]}
    with store.lock(), store.connect() as cx:
        run_dir.mkdir()
        for name, content in source.items():
            atomic_write(within(run_dir / "strategy", name), content.encode("utf-8"))
        if lock_path.exists():
            atomic_write(run_dir / "requirements.lock", lock_path.read_bytes())
        atomic_write(run_dir / "universe.json", frozen_universe)
        engine_source = {p.relative_to(PROJECT).as_posix(): p.read_text(encoding="utf-8") for p in (PROJECT / "src").rglob("*.py")}
        frozen["engine_source_object"] = store.put_object("raw", engine_source)
        if kind == "holdout":
            hstart = request.get("research", {})["test_start"]
            # Interval identity persists across strategy/snapshot revisions.
            hid = digest({"market": manifest["securities"][config.symbols[0]]["market"], "start": hstart, "end": config.end})
            row = cx.execute("SELECT runs,views FROM holdouts WHERE id=?", (hid,)).fetchone()
            frozen["holdout_id"] = hid
            frozen["holdout_label"] = "首次系统内留出测试" if row is None else "已用于研究"
            frozen["previous_holdout_runs"] = row[0] if row else 0
            cx.execute("INSERT INTO holdouts VALUES(?,?,?,1,0) ON CONFLICT(id) DO UPDATE SET runs=runs+1", (hid, hstart, config.end))
        atomic_write(run_dir / "request.json", frozen)
        cx.execute("INSERT INTO runs VALUES(?,?,?,?,?,NULL)", (run_id, frozen["created"], "queued", kind, encoded(frozen).decode()))
    return run_id


def execute_run(store, run_id):
    from investment_lab.engine.core import simulate
    from investment_lab.research.experiments import benchmark_result, holdout, rolling
    run_dir = within(store.root / "runs", run_id)
    request = read_json(run_dir / "request.json")
    original_sys_path = list(sys.path)
    def status(value, summary=None):
        with store.lock(), store.connect() as cx:
            cx.execute("UPDATE runs SET status=?,summary=? WHERE id=?", (value, encoded(summary).decode() if summary else None, run_id))
    progress_started = time.monotonic()
    progress_clock = {"started": progress_started, "last_done": 0, "last_at": progress_started, "rate": None}
    def progress(done, total):
        stamp = time.monotonic()
        delta_done = max(0, done - progress_clock["last_done"])
        delta_time = stamp - progress_clock["last_at"]
        if delta_done and delta_time > 0:
            rate = delta_done / delta_time
            previous = progress_clock["rate"]
            progress_clock["rate"] = rate if previous is None else previous * .65 + rate * .35
        if done >= progress_clock["last_done"]:
            progress_clock.update(last_done=done, last_at=stamp)
        rate = progress_clock["rate"]
        remaining = max(0, total - done)
        eta = remaining / rate if rate and remaining else 0 if not remaining else None
        with store.lock():
            atomic_write(run_dir / "progress.json", {"done": done, "total": total, "updated": now(),
                                                       "elapsed_seconds": round(stamp - progress_clock["started"], 2),
                                                       "eta_seconds": round(eta, 1) if eta is not None else None,
                                                       "sessions_per_second": round(rate, 2) if rate else None})
    try:
        status("running")
        if request["engine_hash"] != code_fingerprint():
            raise ValueError("引擎在提交后发生变化；请重新提交任务")
        if request["runtime_dependencies"] != runtime_dependencies():
            raise ValueError("实际运行依赖发生变化，请按依赖锁恢复环境")
        manifest, bars = store.load(request["snapshot"])
        config = Config(**request["config"])
        entry = within(run_dir / "strategy", request["strategy_entry"])
        def factory():
            random.seed(config.seed)
            import numpy as np
            np.random.seed(config.seed)
            # Remove modules imported from this snapshot before each independent window.
            strategy_dir = run_dir / "strategy"
            for name, module in list(sys.modules.items()):
                origin = getattr(module, "__file__", None)
                if origin and Path(origin).resolve().is_relative_to(strategy_dir):
                    del sys.modules[name]
            spec = importlib.util.spec_from_file_location("user_strategy_" + uuid4().hex, entry)
            module = importlib.util.module_from_spec(spec)
            if str(strategy_dir) not in sys.path:
                sys.path.insert(0, str(strategy_dir))
            spec.loader.exec_module(module)
            if request["strategy"] == "custom":
                if not hasattr(module, "on_session"):
                    raise ValueError("Python 策略须定义 on_session(ctx)")
                return module
            return module.Builtin(request["strategy"])
        params = request.get("params", {})
        if request["kind"] == "single":
            session_count = sum(config.start <= day <= config.end for day in manifest["sessions"])
            total_work = session_count * (2 if config.benchmark else 1)
            scaled_progress = lambda done, _total: progress(done, total_work)
            result = simulate(manifest, bars, config, factory(), params, scaled_progress if progress else None)
            benchmark_progress = lambda done, _total: progress(session_count + done, total_work)
            result["benchmark"] = benchmark_result(manifest, bars, config, benchmark_progress if progress and config.benchmark else None)
            summary = result["metrics"]
        elif request["kind"] == "rolling":
            result = rolling(manifest, bars, config, factory, params, progress=progress, **request.get("research", {}))
            summary = result["summary"]
        else:
            result = holdout(manifest, bars, config, factory, params, progress=progress, **request.get("research", {}))
            result["metadata"]["label"] = request["holdout_label"]
            summary = {label: result[label]["result"]["metrics"] for label in ("development", "holdout")}
        result["run_id"] = run_id
        result["result_hash"] = digest(result)
        with store.lock():
            atomic_write(run_dir / "result.json", result)
        status("completed", summary)
        return result
    except BaseException as exc:
        detail = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        with store.lock():
            atomic_write(run_dir / "error.json", detail)
        status("failed", detail)
        raise
    finally:
        sys.path[:] = original_sys_path


class JobManager:
    def __init__(self, store):
        self.store, self.active = store, {}
        self.guard = threading.Lock()

    def start(self, run_id, timeout=600, memory_mb=2048):
        with self.guard:
            if any(p.poll() is None for p in self.active.values()):
                raise ValueError("本机同一时刻运行一个计算任务；等待完成或取消后再提交")
            run_dir = within(self.store.root / "runs", run_id)
            environment = {k: v for k, v in os.environ.items() if not any(word in k.upper() for word in ("TOKEN", "SECRET", "API_KEY", "PASSWORD"))}
            environment["PYTHONUTF8"] = "1"
            with (run_dir / "worker.log").open("w", encoding="utf-8") as log:
                process = subprocess.Popen([sys.executable, "-m", "investment_lab.cli", "--data", str(self.store.root), "worker", run_id],
                                           stdout=log, stderr=log, env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            self.active[run_id] = process
        threading.Thread(target=self._watch, args=(run_id, process, timeout, memory_mb), daemon=True).start()

    def _watch(self, run_id, process, timeout, memory_mb):
        import psutil
        began = time.monotonic()
        while process.poll() is None:
            try:
                ps = psutil.Process(process.pid)
                rss = sum(p.memory_info().rss for p in [ps, *ps.children(recursive=True)] if p.is_running())
                if time.monotonic() - began > timeout or rss > memory_mb * 1024 * 1024:
                    self.cancel(run_id, "资源/时间限制")
                    return
            except psutil.Error:
                pass
            time.sleep(.5)
        with self.store.lock(), self.store.connect() as cx:
            row = cx.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row and row[0] in ("queued", "running"):
                cx.execute("UPDATE runs SET status='failed',summary=? WHERE id=?", (encoded({"message": "工作进程异常退出", "exit_code": process.returncode}).decode(), run_id))

    def cancel(self, run_id, reason="用户取消"):
        import psutil
        process = self.active.get(run_id)
        if process is None or process.poll() is not None:
            raise ValueError("任务已结束或不属于当前服务")
        try:
            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
        process.wait(timeout=10)
        with self.store.lock(), self.store.connect() as cx:
            cx.execute("UPDATE runs SET status='cancelled',summary=? WHERE id=?", (encoded({"message": reason}).decode(), run_id))
            atomic_write(self.store.root / "runs" / run_id / "error.json", {"message": reason})


def reproduce(store, run_id):
    old = read_json(within(store.root / "runs", run_id) / "request.json")
    if old["engine_hash"] != code_fingerprint():
        raise ValueError("请在对应版本独立目录运行复现，当前引擎不同")
    lock_path = PROJECT / "requirements.lock"
    if old["dependency_hash"] != (digest(lock_path.read_bytes()) if lock_path.exists() else None):
        raise ValueError("依赖锁不同，请使用保存的依赖版本")
    # Reproduction uses the archived strategy, never the current user file.
    new_id = uuid4().hex
    import shutil
    with store.lock(), store.connect() as cx:
        target = store.root / "runs" / new_id
        target.mkdir()
        shutil.copytree(store.root / "runs" / run_id / "strategy", target / "strategy")
        for name in ("requirements.lock", "universe.json"):
            source_file = store.root / "runs" / run_id / name
            if source_file.exists():
                shutil.copy2(source_file, target / name)
        copied = {**old, "run_id": new_id, "created": now(), "reproduction_of": run_id}
        atomic_write(target / "request.json", copied)
        cx.execute("INSERT INTO runs VALUES(?,?,?,?,?,NULL)", (new_id, copied["created"], "queued", old["kind"], encoded(copied).decode()))
    result = execute_run(store, new_id)
    original = read_json(store.root / "runs" / run_id / "result.json")
    for item in (result, original):
        item.pop("run_id", None)
        item.pop("result_hash", None)
    return {"run_id": new_id, "identical": digest(result) == digest(original), "tolerance": "Decimal 账本和序列化结果完全一致"}
