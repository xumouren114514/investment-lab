from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[2]


def now():
    return datetime.now(timezone.utc).isoformat()


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("数值必须有限")
    return result


def money(value):
    return dec(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temporary.write_bytes(content if isinstance(content, bytes) else encoded(content))
    # Windows readers/scanners can briefly deny replacement of a complete file.
    # Keep the old destination intact and retry only the atomic rename, bounded.
    for delay in (.01, .02, .05, .1, .2, .4, .8):
        try:
            os.replace(temporary, path)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33):
                raise
            time.sleep(delay)
    os.replace(temporary, path)


def data_root(value=None):
    return Path(value or os.getenv("INVESTMENT_LAB_DATA", str(PROJECT.parent / "investment_lab_data"))).resolve()


def within(root, name):
    root = Path(root).resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("路径必须位于指定目录内")
    return path


@contextmanager
def file_lock(path, timeout=30):
    """OS-owned lock with bounded contention wait; released on process death."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + timeout
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("另一个写入/备份任务仍持有锁，请稍后重试") from exc
                    time.sleep(.05)
        else:
            import fcntl
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("另一个写入/备份任务仍持有锁，请稍后重试")
                    time.sleep(.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)
