#!/usr/bin/env python3

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

ENV = os.environ.copy()
ENV["PYTHONDONTWRITEBYTECODE"] = "1"

SUITES = [
    (
        "Public repository safety",
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/public",
            "-p",
            "test_*.py",
            "-v",
        ],
    ),
    (
        "V8.6.8 round-2 hardening",
        [
            sys.executable,
            "-u",
            "tools/resume/v86/tests/"
            "test_v86_8_round2_hardening.py",
        ],
    ),
    (
        "V8.6.8 state isolation",
        [
            sys.executable,
            "-u",
            "tools/resume/v86/tests/"
            "test_v86_8_round3_state_isolation.py",
        ],
    ),
    (
        "V8.6.8 flat-runner contract",
        [
            sys.executable,
            "-u",
            "tools/resume/v86/tests/"
            "test_v86_8_final_flat_runner.py",
        ],
    ),
    (
        "Generic, candidate-agnostic pipeline",
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.public.test_generic_pipeline",
            "-v",
        ],
    ),
]


def main() -> int:
    failed = []

    print("=" * 72)
    print("PUBLIC TEST SUITE")
    print("=" * 72)

    for name, command in SUITES:
        print()
        print("-" * 72)
        print(name)
        print("-" * 72)

        result = subprocess.run(
            command,
            cwd=ROOT,
            env=ENV,
        )

        if result.returncode != 0:
            failed.append(name)

    print()
    print("=" * 72)

    if failed:
        print("PUBLIC TEST RESULT: FAIL")

        for name in failed:
            print(" -", name)

        return 1

    print("PUBLIC TEST RESULT: PASS")
    print("Suites:", len(SUITES))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
