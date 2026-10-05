"""Compile explicit Python paths without executing them or writing bytecode."""
from __future__ import annotations

import argparse
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    failed = False
    for path in args.paths:
        try:
            compile(path.read_bytes(), str(path), "exec")
        except (OSError, SyntaxError) as exc:
            print(f"Python syntax check failed: {path}: {exc}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
