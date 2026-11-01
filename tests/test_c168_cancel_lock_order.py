r"""C-168：`jobs.cancel()` 先杀进程后取锁——锁竞争时子进程死了但状态没更新，监控线程还崩掉。

## 现场（2026-11-01 23:5x，跑 verify_delivery.py 时冒出来）

stderr 里出现：

    File "src\investment_lab\jobs.py", line 297, in _watch
        with self.store.lock(), self.store.connect() as cx:
    RuntimeError: 另一个写入/备份任务仍持有锁，请稍后重试

**而 `verify_delivery.py` 退出码是 0**（它把交付验证算通过了）。

## 缺陷链

`_watch()` 每 0.5 秒检查一次资源；超时或超内存时调 `cancel(run_id, "资源/时间限制")`。
`cancel()` 的顺序是：

```python
process.kill()          # ① 先把子进程杀掉
...
with self.store.lock(): # ② 再取数据库锁写 status='cancelled'
```

如果第 ② 步取不到锁（备份/别的写入正在跑），`file_lock` 抛 `RuntimeError`：

- **子进程已经死了**——回测被强行终止；
- **数据库里的 run 仍是 running**——界面上一只看不到它结束；
- **`_watch` 线程直接死了**（异常没被捕获）——这个 run 从此**没有任何监控**，
  即使它其实还在跑（另一个分支），超时保护也失效了。

## 判据

1. `cancel()` 在杀进程**之前**就要能安全失败——取锁失败时**不能**已经杀了进程；
   正确顺序：先取锁 → 再杀 → 再写状态；或者杀之前先确认能取到锁。
2. `_watch` 循环里的异常必须被捕获并记录，**不能让它静默死掉**。
3. 真跑：模拟锁被占用时调 `cancel()`，断言子进程**还活着**（没被误杀）。

⚠️ 本文件**不碰**正式数据目录：用临时目录造一个最小 store。
"""
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass


def _jobs_src() -> str:
    return (ROOT / "src" / "investment_lab" / "jobs.py").read_bytes().decode("utf-8")


def test_cancel_takes_lock_before_killing():
    """静态结构核对：`cancel()` 里取锁必须**早于** kill。

    这不是"查源码里有没有某个字符串"，而是核对**两条语句的先后顺序**——
    顺序错就是缺陷（先杀后取锁 = 锁竞争时杀死任务却记不下结果）。
    """
    src = _jobs_src()
    start = src.index("def cancel(")
    # 到下一个顶层 def 为止
    rest = src[start:]
    nxt = rest.find("\ndef ", 10)
    body = rest[: nxt if nxt > 0 else len(rest)]

    lock_at = body.find("self.store.lock()")
    kill_at = body.find(".kill()")
    assert lock_at >= 0, "cancel() 里找不到取锁"
    assert kill_at >= 0, "cancel() 里找不到 kill"
    assert lock_at < kill_at, (
        f"cancel() 先 kill（位置 {kill_at}）后取锁（位置 {lock_at}）——"
        f"锁被占用时会出现「子进程已死、状态仍是 running、监控线程崩溃」"
    )


def test_watch_loop_does_not_die_silently():
    """`_watch` 的循环体必须包住异常，不能让线程静默死掉。"""
    src = _jobs_src()
    start = src.index("def _watch(")
    rest = src[start:]
    nxt = rest.find("\n    def ", 10)
    body = rest[: nxt if nxt > 0 else len(rest)]
    assert "except" in body, (
        "_watch 的循环里没有任何 except——cancel() 抛异常时监控线程会直接死掉，"
        "这个 run 从此失去超时/内存保护"
    )
    # 必须有记录，不能只 pass
    assert ("_watch_error" in body) or ("record" in body) or ("print(" in body) or ("log" in body), (
        "_watch 捕获了异常但没有任何记录——下次还是没人知道"
    )


def test_cancel_failure_keeps_child_alive():
    """行为：锁被占用时调 cancel()，**子进程不能被杀**。

    ★ 关键：`file_lock` 在 Windows 上用 `msvcrt.locking`，**同进程内的两个线程/两个句柄
      不互斥**（实测：第二个线程照样拿到锁）。所以必须用**另一个进程**持有写锁，
      才能造出真正的竞争——第一版我在同进程里开线程抢锁，根本挡不住，
      那条测试的红是**假阳性**（它红是因为 cancel 走了别的分支，不是因为锁）。
    """
    import subprocess

    from investment_lab import jobs
    from investment_lab.data.store import Store

    d = Path(tempfile.mkdtemp(prefix="c168_"))
    st = Store(d)
    (st.root / "state").mkdir(parents=True, exist_ok=True)
    lock_path = st.root / "state" / "writer.lock"

    worker = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    # ★ 持锁进程必须**比 file_lock 的默认超时（30 秒）活得更久**：
    #   第一版让它睡 20 秒，于是 cancel() 等了 20 秒**拿到锁**、正常杀了进程、
    #   再因为 run 不在库里抛 ValueError——测试红得看起来像"缺陷没修"，
    #   实际是**测试场景没造出真竞争**。现在睡 90 秒，cancel() 必定超时抛错。
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time\n"
         "sys.path.insert(0, r'%s')\n"
         "from investment_lab.common import file_lock\n"
         "from pathlib import Path\n"
         "with file_lock(Path(r'%s')):\n"
         "    print('HOLDING', flush=True)\n"
         "    time.sleep(90)\n" % (ROOT / "src", lock_path)],
        stdout=subprocess.PIPE, text=True)
    try:
        line = holder.stdout.readline()
        assert "HOLDING" in line, f"持锁进程没起来：{line!r}"

        mgr = jobs.JobManager(st)
        mgr.active["probe-run"] = worker

        raised = None
        try:
            mgr.cancel("probe-run", "测试：锁竞争")
        except Exception as e:  # noqa: BLE001
            raised = e

        alive = worker.poll() is None
        assert raised is not None, "锁一直被占着，cancel() 却没报错——说明它没等锁"
        assert isinstance(raised, RuntimeError), (
            f"应当是『锁被占用』的 RuntimeError，实际 {type(raised).__name__}: {raised}"
        )
        assert alive, (
            "锁被占用、cancel() 报错之后，**子进程却被杀了**——"
            "结果是：回测被强行终止、数据库里仍是 running、监控线程也崩溃"
        )
    finally:
        for p in (holder, worker):
            try:
                p.kill()
                p.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass


if __name__ == "__main__":
    fails = 0
    ran = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            ran += 1
            try:
                fn(); print("PASS", name)
            except AssertionError as e:
                fails += 1; print("FAIL", name, e)
    print(f"{fails} 条失败（共 {ran} 条）" if fails else f"全部通过（{ran} 条）")
    sys.exit(1 if fails else 0)
