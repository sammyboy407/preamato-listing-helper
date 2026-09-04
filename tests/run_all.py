"""Runs every test suite. One command to type before a batch:

    python3 tests/run_all.py

Exits non-zero if anything fails, so it can be chained with && in front of a
commit or a push and nothing gets through on a red result.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

SUITES = ["test_sizing", "test_checks"]


def main() -> int:
    failed = []
    for name in SUITES:
        print(f"--- {name}")
        module = importlib.import_module(name)
        if module.main() != 0:
            failed.append(name)
        print()

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        print("Do not upload a batch until these pass.")
        return 1
    print("Everything passes. ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
