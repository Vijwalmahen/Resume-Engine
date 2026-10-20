#!/usr/bin/env python3
"""
V8.6.8 FINAL -- flat, non-recursive top-level runner for every V8.6.x test
suite.

Runs each suite file EXACTLY ONCE, as its own `python3 <file>` subprocess
(the same interpreter this runner itself is invoked with, `sys.executable`
-- matching `tools/resume/v86/README.md`'s "use python3, not python3.13"
note), in the fixed, deterministic order below, synchronously and in the
foreground. There is no concurrency, no background process, no polling
loop, and no suite ever invokes another suite -- this file is the ONLY
place that runs more than one suite in a single command.

Before V8.6.8 FINAL, `test_v86_2_isolation_patch.py` through
`test_v86_8_round5_action_verb_diversity.py` each contained a
"prior suites still pass" test class whose test methods re-ran an OLDER
suite as a subprocess (`test_vXXX_suite_still_passes`). Because each of
those older suites (from V8.6.2 onward) contained the SAME kind of
recursive check pointed at suites older than itself, running the single
newest file (round 5) transitively re-executed every older suite many
times over (round 5 -> round 4 -> round 3/round 2/.../offline, round 4
independently -> round 3/round 2/.../offline again, and so on down the
chain) -- see the removed-code comments left in each of those files for
the exact methods this pass deleted. This runner replaces all of that: it
executes every suite directly, exactly once, and nothing in
`tools/resume/v86/tests/*.py` invokes another suite file anymore (verified
by `tests/test_v86_8_final_flat_runner.py`'s
`test_no_suite_file_still_invokes_another_suite_file`).

Usage:
    python3 tools/resume/v86/tests/run_all_v86_tests.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
ROOT = TESTS_DIR.parents[3]

# Deterministic execution order: the actual chronological order of the
# V8.6.x passes (V8.6 -> V8.6.1 -> ... -> V8.6.8 round 5), exactly matching
# tools/resume/v86/README.md's own section ordering. Every file under
# tools/resume/v86/tests/ named test_v86_*.py must appear here exactly
# once (enforced by test_v86_8_final_flat_runner.py).
SUITE_ORDER = [
    "test_v86_offline.py",
    "test_v86_reliability_patch.py",
    "test_v86_2_isolation_patch.py",
    "test_v86_3_usage_optimization.py",
    "test_v86_4_call2_effort_medium.py",
    "test_v86_5_clear_winner_safeguard.py",
    "test_v86_6_ats_bridge_and_unplaceable_evidence.py",
    "test_v86_7_ats_bridge_reciprocal_rank_fusion.py",
    "test_v86_8_stabilization.py",
    "test_v86_8_round2_hardening.py",
    "test_v86_8_round3_state_isolation.py",
    "test_v86_8_round4_company_identity.py",
    "test_v86_8_round5_action_verb_diversity.py",
    "test_v86_9_resume_quality_report.py",
    "test_v86_9_acceptance_offline.py",
    "test_v86_9_batch_and_adapters.py",
    "test_v86_10_jd_match_and_recommendations.py",
    "test_v86_8_final_flat_runner.py",
]

# unittest's own TextTestRunner (verbosity=2, as every suite invokes via
# `unittest.main(verbosity=2)`) writes its summary to stderr, e.g.:
#   Ran 42 tests in 3.512s
#   OK
# or:
#   FAILED (failures=1, errors=2)
import re  # noqa: E402

_RAN_RE = re.compile(r"^Ran (\d+) tests? in ([\d.]+)s", re.MULTILINE)
_FAILED_RE = re.compile(r"^FAILED \(([^)]*)\)", re.MULTILINE)


def _parse_counts(output: str) -> dict:
    ran_match = _RAN_RE.search(output)
    tests_run = int(ran_match.group(1)) if ran_match else 0
    duration = float(ran_match.group(2)) if ran_match else 0.0

    failed_match = _FAILED_RE.search(output)
    failures = errors = 0
    if failed_match:
        for part in failed_match.group(1).split(","):
            part = part.strip()
            if part.startswith("failures="):
                failures = int(part.split("=")[1])
            elif part.startswith("errors="):
                errors = int(part.split("=")[1])

    return {"tests_run": tests_run, "reported_duration_s": duration, "failures": failures, "errors": errors}


def run_suite(filename: str) -> dict:
    path = TESTS_DIR / filename
    if not path.exists():
        return {"suite": filename, "status": "MISSING", "tests_run": 0, "failures": 0, "errors": 1,
                "wall_time_s": 0.0, "output": f"ERROR: {path} does not exist"}

    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(path)],
        cwd=ROOT, capture_output=True, text=True,
    )
    wall_time_s = time.monotonic() - start
    combined = proc.stdout + proc.stderr
    counts = _parse_counts(combined)

    if proc.returncode == 0 and not counts["failures"] and not counts["errors"]:
        status = "PASS"
    else:
        status = "FAIL"

    return {
        "suite": filename,
        "status": status,
        "returncode": proc.returncode,
        "tests_run": counts["tests_run"],
        "reported_duration_s": counts["reported_duration_s"],
        "failures": counts["failures"],
        "errors": counts["errors"],
        "wall_time_s": wall_time_s,
        "output": combined,
    }


def main() -> int:
    overall_start = time.monotonic()
    results = []
    for filename in SUITE_ORDER:
        print(f"{filename} ", end="", flush=True)
        result = run_suite(filename)
        results.append(result)
        label = "PASS" if result["status"] == "PASS" else "FAIL"
        print(
            f"........ {label} {result['tests_run']} tests {result['wall_time_s']:.1f}s",
            flush=True,
        )
        if result["status"] != "PASS":
            print("-" * 78)
            print(result["output"])
            print("-" * 78)

    total_wall = time.monotonic() - overall_start
    total_tests = sum(r["tests_run"] for r in results)
    total_failures = sum(r["failures"] for r in results)
    total_errors = sum(r["errors"] for r in results)
    non_pass = [r["suite"] for r in results if r["status"] != "PASS"]

    print()
    print("TOTAL:")
    print(f"suites: {len(results)}")
    print(f"tests: {total_tests}")
    print(f"failures: {total_failures}")
    print(f"errors: {total_errors}")
    print(f"wall: {total_wall:.1f}s")

    if non_pass:
        print(f"FAILING SUITES: {non_pass}")
        return 1

    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
