"""Run each offline test module in an isolated, time-bounded Python process.

This avoids one Playwright driver affecting unrelated modules. A test process
that reports success but never exits is considered a failure, not a pass.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
TEST_TIMEOUT_SECONDS = 120


def run_all(test_files=None, *, runner=subprocess.run) -> int:
    files = sorted(TESTS.glob("test_*.py")) if test_files is None else test_files
    if not files:
        print("ERROR: No test files found", file=sys.stderr, flush=True)
        return 1
    for path in files:
        name = path.name
        print(f"\nRunning {name}...", flush=True)
        command = [sys.executable, "-m", "unittest", "discover", "-s", str(TESTS),
                   "-p", name, "-v"]
        try:
            result = runner(command, cwd=ROOT, timeout=TEST_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            print(f"ERROR: {name} did not exit within {TEST_TIMEOUT_SECONDS} seconds", file=sys.stderr, flush=True)
            return 1
        except OSError as exc:
            print(f"ERROR: Could not run {name}: {exc}", file=sys.stderr, flush=True)
            return 1
        if result.returncode:
            print(f"ERROR: {name} exited with code {result.returncode}", file=sys.stderr, flush=True)
            return 1
    print("\nAll test modules passed and exited normally.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
