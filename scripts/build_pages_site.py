"""Build the public browser-only edition without copying local app data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


PROJECT = Path(__file__).resolve().parents[1]
PAGE_ASSETS = ("index.html", "app.css", "app.js", "worker.js")
SHARED_PAGE_ASSETS = {"ui-utils.js": PROJECT / "src/investment_lab/web/static/ui-utils.js"}
RUNTIME_FILES = (
    "__init__.py", "common.py",
    "engine/__init__.py", "engine/account.py", "engine/models.py", "engine/cash_flows.py",
    "engine/reference.py", "engine/core.py",
    "data/__init__.py", "data/compose.py", "data/validation.py",
    "research/__init__.py", "research/metrics.py", "research/experiments.py",
    "strategies/__init__.py", "strategies/examples.py",
)


def build(output: Path) -> dict:
    output = output.absolute()
    for entry in (output, *output.parents):
        if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
            raise ValueError(f"Pages 构建路径不能经过链接或 junction：{entry}")
    output = output.resolve()
    expected = set(PAGE_ASSETS) | set(SHARED_PAGE_ASSETS) | {"portable-data.js", "runtime-manifest.json"}
    expected.update(f"runtime/investment_lab/{name}" for name in RUNTIME_FILES)
    if output.exists():
        for entry in output.rglob("*"):
            if entry.is_symlink() or (hasattr(entry, "is_junction") and entry.is_junction()):
                raise ValueError(f"Pages 构建目录包含链接，已停止：{entry}")
            if entry.is_file() and entry.relative_to(output).as_posix() not in expected:
                raise ValueError(f"Pages 构建目录包含白名单外文件，已停止：{entry}")
    output.mkdir(parents=True, exist_ok=True)
    pages = PROJECT / "pages"
    copied = []
    for name in PAGE_ASSETS:
        source = pages / name
        if not source.is_file():
            raise FileNotFoundError(f"Pages 源文件缺失：{source}")
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append((target.relative_to(output).as_posix(), target))

    for name, source in SHARED_PAGE_ASSETS.items():
        if not source.is_file():
            raise FileNotFoundError(f"浏览器共用界面模块缺失：{source}")
        target = output / name
        shutil.copyfile(source, target)
        copied.append((target.relative_to(output).as_posix(), target))

    helper = PROJECT / "src/investment_lab/web/static/portable-data.js"
    target = output / "portable-data.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(helper, target)
    copied.append((target.relative_to(output).as_posix(), target))

    runtime_root = output / "runtime/investment_lab"
    for relative in RUNTIME_FILES:
        source = PROJECT / "src/investment_lab" / relative
        if not source.is_file():
            raise FileNotFoundError(f"浏览器计算模块缺失：{source}")
        target = runtime_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.append((target.relative_to(output).as_posix(), target))

    manifest = {
        "format": "investment-lab-pages-build",
        "version": 1,
        "runtime": "Pyodide v314.0.7",
        "files": {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sorted(copied)},
    }
    (output / "runtime-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=PROJECT / "build/pages")
    args = parser.parse_args()
    result = build(args.output)
    print(json.dumps({"output": str(args.output.resolve()), "files": len(result["files"]),
                      "runtime": result["runtime"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
