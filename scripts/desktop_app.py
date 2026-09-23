"""Windows desktop entry; keep this outside src to preserve archived engine hashes."""
from __future__ import annotations

import argparse
import ctypes
import errno
import json
import logging
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))
from investment_lab.common import atomic_write, file_lock, now


class LaunchError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_service(data, port, project=PROJECT):
    """Never open an unrelated service or send the local probe through a proxy."""
    try:
        # Windows can delay connection-refused beyond a short connect timeout.
        # A temporary exclusive bind distinguishes an unused port immediately.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as check:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                check.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            check.bind(("127.0.0.1", port))
            return None
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE and getattr(exc, "winerror", None) != 10048:
            raise LaunchError(f"无法检查本机端口 {port}：{exc}") from exc
    try:
        opener = build_opener(ProxyHandler({}), NoRedirect())
        with opener.open(f"http://127.0.0.1:{port}/api/status", timeout=5) as response:
            status = json.loads(response.read(1_000_000).decode("utf-8"))
        if not isinstance(status, dict) or not isinstance(status.get("version"), str):
            raise ValueError("服务没有返回平台版本")
        if Path(status.get("project_path", "")).resolve() != Path(project).resolve():
            raise ValueError("该端口运行的不是此目录下的投资研究室")
        if Path(status.get("data_path", "")).resolve() != Path(data).resolve():
            raise ValueError("该端口使用另一个数据目录")
        return status
    except (OSError, URLError, HTTPError, ValueError, TypeError) as exc:
        raise LaunchError(f"端口 {port} 已有服务，但未能确认是本投资研究室：{exc}。\n"
                          "为避免打开错误应用，已停止启动；请检查端口或稍后重试。") from exc


def log_tail(path):
    try:
        with Path(path).open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 4000))
            return stream.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return "日志尚未生成。"


def spawn_server(data, port, log_path, project=PROJECT):
    python = Path(project) / ".venv/Scripts/python.exe"
    if not python.is_file():
        raise LaunchError(f"Python 环境不存在：{python}\n请在项目目录运行 scripts\\install.ps1。")
    command = [str(python), "-m", "investment_lab.cli", "--data", str(data), "serve", "--port", str(port)]
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"}
    flags = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
    with Path(log_path).open("ab", buffering=0) as output:
        output.write(f"\n=== desktop start {now()} ===\n".encode())
        return subprocess.Popen(command, cwd=project, env=environment, stdin=subprocess.DEVNULL,
                                stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                creationflags=flags, start_new_session=os.name != "nt")


def ensure_service(data, port=8765, timeout=60, progress=lambda _: None, project=PROJECT):
    data = Path(data).resolve()
    (data / "state").mkdir(parents=True, exist_ok=True)
    (data / "logs").mkdir(parents=True, exist_ok=True)
    log_path = data / "logs/desktop-server.log"
    progress("正在检查本机服务…")
    # Concurrent shortcut clicks serialize startup, then reuse the same service.
    with file_lock(data / "state/desktop-launch.lock", timeout=timeout + 10):
        existing = probe_service(data, port, project)
        if existing:
            return {"reused": True, "status": existing, "url": f"http://127.0.0.1:{port}/"}
        progress("正在启动投资研究室，请稍候…")
        process = spawn_server(data, port, log_path, project)
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise LaunchError(f"服务启动失败（退出码 {process.returncode}）。\n{log_tail(log_path)}")
                status = probe_service(data, port, project)
                if status:
                    report = {"reused": False, "status": status, "url": f"http://127.0.0.1:{port}/",
                              "launcher_child_pid": process.pid, "started": now(), "log": str(log_path)}
                    atomic_write(data / "state/desktop-service.json", report)
                    return report
                time.sleep(.25)
            raise LaunchError(f"等待服务启动超过 {timeout} 秒。\n{log_tail(log_path)}")
        except Exception:
            # Only clean up the process tree that this unsuccessful launch owns.
            if process.poll() is None:
                import psutil
                try:
                    parent = psutil.Process(process.pid)
                    children = parent.children(recursive=True)
                    for child in children:
                        try:
                            child.terminate()
                        except psutil.Error:
                            pass
                    parent.terminate()
                    psutil.wait_procs(children + [parent], timeout=3)
                except psutil.Error:
                    pass
            raise


def find_browser():
    roots = [os.getenv("ProgramFiles(x86)"), os.getenv("ProgramFiles"), os.getenv("LOCALAPPDATA")]
    for relative in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe"):
        for root in roots:
            if root and (Path(root) / relative).is_file():
                return Path(root) / relative
    raise LaunchError("未找到 Microsoft Edge 或 Google Chrome。请安装其中一个后重新打开。")


def browser_command(browser, url):
    profile = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "InvestmentLab/browser-profile"
    return [str(browser), f"--app={url}", f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--window-size=1280,900"]


def launch_app(data, port, progress=lambda _: None):
    browser = find_browser()
    result = ensure_service(data, port, progress=progress)
    progress("服务已就绪，正在打开应用窗口…")
    command = browser_command(browser, result["url"])
    process = subprocess.Popen(command, cwd=PROJECT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    # A running browser may hand the request to its existing process and return 0.
    try:
        code = process.wait(timeout=.5)
        if code:
            raise LaunchError(f"应用窗口启动失败（浏览器退出码 {code}）。本机服务仍在运行。")
    except subprocess.TimeoutExpired:
        pass
    result.update(browser=str(browser), browser_launch_pid=process.pid, opened=now())
    atomic_write(Path(data) / "state/desktop-last-launch.json", result)
    logging.info("Desktop opened: %s", json.dumps(result, ensure_ascii=False))
    return result


def show_window(data, port):
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("投资研究室")
    root.geometry("580x190")
    root.minsize(500, 190)
    try:
        root.iconbitmap(str(PROJECT / "scripts/assets/investment-lab.ico"))
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("InvestmentLab.Desktop")
    except (OSError, AttributeError, tk.TclError):
        pass
    panel = ttk.Frame(root, padding=20)
    panel.pack(fill="both", expand=True)
    message = tk.StringVar(value="正在准备投资研究室…")
    ttk.Label(panel, textvariable=message, wraplength=530).pack(anchor="w", pady=(0, 12))
    progressbar = ttk.Progressbar(panel, mode="indeterminate")
    progressbar.pack(fill="x")
    ttk.Label(panel, text="首次启动需要稍候；关闭应用窗口后，后台服务会保留。", wraplength=530).pack(anchor="w", pady=12)
    events = queue.Queue()
    detail = tk.Text(panel, height=10, wrap="word")
    buttons = ttk.Frame(panel)

    def start():
        detail.pack_forget()
        buttons.pack_forget()
        root.geometry("580x190")
        progressbar.pack(fill="x")
        progressbar.start(12)

        def worker():
            try:
                launch_app(data, port, lambda text: events.put(("progress", text)))
                events.put(("done", None))
            except Exception as exc:
                logging.exception("Desktop launch failed")
                events.put(("error", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def poll():
        try:
            while True:
                kind, value = events.get_nowait()
                if kind == "done":
                    root.destroy()
                    return
                if kind == "progress":
                    message.set(value)
                else:
                    progressbar.stop()
                    progressbar.pack_forget()
                    message.set("启动未完成。可查看下方原因，处理后重试。")
                    root.geometry("680x450")
                    detail.configure(state="normal")
                    detail.delete("1.0", "end")
                    detail.insert("1.0", value + f"\n\n日志目录：{Path(data) / 'logs'}")
                    detail.configure(state="disabled")
                    detail.pack(fill="both", expand=True, pady=8)
                    buttons.pack(fill="x")
        except queue.Empty:
            pass
        root.after(100, poll)

    ttk.Button(buttons, text="重试", command=start).pack(side="left")
    ttk.Button(buttons, text="打开日志文件夹", command=lambda: os.startfile(str(Path(data) / "logs"))).pack(side="left", padx=8)
    ttk.Button(buttons, text="关闭", command=root.destroy).pack(side="right")
    start()
    root.after(100, poll)
    root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="投资研究室桌面启动器")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", default=os.getenv("INVESTMENT_LAB_DATA", str(PROJECT.parent / "investment_lab_data")))
    parser.add_argument("--ensure-server", action="store_true", help="仅启动/检查服务，输出诊断JSON，不打开窗口")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在1至65535之间")
    data = Path(args.data).resolve()
    try:
        (data / "logs").mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=data / "logs/desktop-launcher.log", encoding="utf-8",
                            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        if args.ensure_server:
            print(json.dumps(ensure_service(data, args.port), ensure_ascii=False))
        else:
            show_window(data, args.port)
    except Exception as exc:
        logging.exception("Desktop startup error")
        if args.ensure_server:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        elif os.name == "nt":
            ctypes.windll.user32.MessageBoxW(None, f"无法启动投资研究室：\n{exc}\n\n日志目录：{data / 'logs'}", "投资研究室", 0x10)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
