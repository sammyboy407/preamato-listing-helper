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


def _import_failures() -> list:
    """Every module under src/, imported on the interpreter actually in use.
    Returns a list of "module: error" strings, empty when all is well."""
    import importlib
    import pkgutil
    import types

    # The same three stubs the suites use — see tests/test_sizing.py.
    for dep in ("anthropic", "openpyxl", "python_calamine"):
        try:
            __import__(dep)
        except ModuleNotFoundError:
            stub = types.ModuleType(dep)

            class _StubModule(types.ModuleType):
                def __getattr__(self, name):
                    value = type(name, (Exception,), {})
                    setattr(self, name, value)
                    return value

            stub.__class__ = _StubModule
            sys.modules[dep] = stub

    import src

    failures = []
    for module in sorted(m.name for m in pkgutil.iter_modules(src.__path__)):
        try:
            importlib.import_module(f"src.{module}")
        except Exception as exc:  # noqa: BLE001 - reporting, not handling
            failures.append(f"src/{module}.py: {type(exc).__name__}: {exc}")
    return failures


def main() -> int:
    # Printed because a version difference between the machine that
    # writes a fix and the machine that deploys it is exactly what let a
    # Python 3.10-only line reach a deploy on 06.09.26.
    print(f"Python {sys.version.split()[0]}\n")

    # Preflight: every module in src/ has to import before any suite runs.
    #
    # This is a precondition, not a test, and it is checked here so it fails
    # as one clear line rather than as a traceback out of whichever suite
    # happened to import the broken module first. 06.09.26: a PEP 604 union
    # at module level in pipeline.py imported fine on the machine the fix was
    # written on and on Streamlit Cloud, and failed on the Python 3.9 that
    # ships with Apple's Command Line Tools, which is what runs deploy.sh.
    broken = _import_failures()
    if broken:
        print(f"src/ does not import on Python {sys.version.split()[0]}:\n")
        for line in broken:
            print(f"  ✗ {line}")
        print("\nNothing else was run. Fix the import first.")
        return 1

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
