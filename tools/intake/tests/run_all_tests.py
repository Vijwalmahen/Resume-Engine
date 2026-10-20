#!/usr/bin/env python3
"""
Flat, non-recursive runner for every `tools/intake` suite.

    PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/tests/run_all_tests.py

Each suite runs exactly once, as its own `python3 <file>` subprocess (the same
interpreter this runner was invoked with), in the fixed order below. No suite
invokes another suite — the same flat contract
`tools/resume/v86/tests/run_all_v86_tests.py` enforces for the V8.6 suites.

Every suite is offline: `support.block_network()` makes socket access raise,
all fixtures are synthetic, and all workbooks live in temp directories.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[2]

SUITE_ORDER = [
    "test_url_and_identity.py",
    "test_scrape_results.py",
    "test_write_job_file.py",
    "test_dedup.py",
    "test_intake_tracker.py",
    "test_run_intake.py",
    "test_sync_results.py",
    "test_batch_job_setup_failed.py",
]

_RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.MULTILINE)
_FAILED_RE = re.compile(r"^FAILED \(([^)]*)\)", re.MULTILINE)
_COUNT_RE = re.compile(r"(failures|errors)=(\d+)")


def main() -> int:
    present = {p.name for p in TESTS_DIR.glob("test_*.py")}
    missing = present - set(SUITE_ORDER)
    if missing:
        print(f"ERROR: suite file(s) not listed in SUITE_ORDER: {sorted(missing)}")
        return 2

    total_tests = total_failures = total_errors = 0
    failed_suites = []
    t0 = time.monotonic()

    for name in SUITE_ORDER:
        path = TESTS_DIR / name
        if not path.exists():
            print(f"ERROR: missing suite file: {name}")
            return 2

        result = subprocess.run(
            [sys.executable, "-u", str(path)],
            cwd=ROOT, capture_output=True, text=True,
        )
        output = result.stdout + result.stderr

        ran = _RAN_RE.search(output)
        tests = int(ran.group(1)) if ran else 0
        seconds = ran.group(2) if ran else "?"
        total_tests += tests

        failed = _FAILED_RE.search(output)
        if failed:
            counts = dict((k, int(v)) for k, v in _COUNT_RE.findall(failed.group(1)))
            total_failures += counts.get("failures", 0)
            total_errors += counts.get("errors", 0)

        if result.returncode == 0:
            print(f"{name} ........ PASS {tests} tests {seconds}s")
        else:
            failed_suites.append(name)
            print(f"{name} ........ FAIL {tests} tests {seconds}s")
            print(output)

    print()
    print("TOTAL:")
    print(f"  suites: {len(SUITE_ORDER)}")
    print(f"  tests: {total_tests}")
    print(f"  failures: {total_failures}")
    print(f"  errors: {total_errors}")
    print(f"  wall: {round(time.monotonic() - t0, 1)}s")

    if failed_suites:
        print("FAILED SUITES: " + ", ".join(failed_suites))
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
