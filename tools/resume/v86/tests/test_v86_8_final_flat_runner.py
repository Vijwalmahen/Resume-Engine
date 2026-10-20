#!/usr/bin/env python3
"""
V8.6.8 FINAL pre-production cleanup -- flat test-architecture regression
suite (Part B of the FINAL cleanup task).

ZERO real Claude/Apify calls, ZERO pipeline calls. This file is itself a
LEAF suite: it never invokes another suite file (as a subprocess or
otherwise) and is never invoked by another suite file -- it only proves,
statically and by monkeypatching `run_all_v86_tests.run_suite`, that the
new flat runner (`tools/resume/v86/tests/run_all_v86_tests.py`) executes
every suite exactly once, in one deterministic order, and that no suite
file anywhere under `tools/resume/v86/tests/` still contains the old
recursive "run an older suite as a subprocess" pattern this pass removed.

Covers:
  1. No suite file contains a live `self._run_suite(...)` call or a
     `def _run_suite` definition anymore (the pattern this pass deleted).
  2. `run_all_v86_tests.SUITE_ORDER` lists every `test_v86_*.py` file under
     `tools/resume/v86/tests/` exactly once (nothing missing, nothing
     duplicated, nothing stale).
  3. Calling `run_all_v86_tests.main()` (with `run_suite` monkeypatched to
     a zero-subprocess stub) invokes every suite in `SUITE_ORDER` exactly
     once, in the declared order -- proof of "duplicate execution proof"
     (task Part B7) without paying the cost of a second real full run.
  4. `run_all_v86_tests._parse_counts` correctly parses a real
     `unittest.TextTestRunner(verbosity=2)` summary (both the OK and the
     FAILED shapes).
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_all_v86_tests as runner  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
THIS_FILE = Path(__file__).name

_RECURSIVE_CALL_RE = re.compile(r"self\._run_suite\(")
_RECURSIVE_DEF_RE = re.compile(r"def _run_suite\(")


class TestNoSuiteFileInvokesAnotherSuiteFile(unittest.TestCase):
    def _all_suite_files(self):
        return sorted(
            p for p in TESTS_DIR.glob("test_v86_*.py")
        )

    def test_no_suite_file_still_defines_or_calls_run_suite(self):
        offenders = []
        for path in self._all_suite_files():
            if path.name == THIS_FILE:
                continue  # this file's own source defines the detection regexes themselves
            text = path.read_text(encoding="utf-8")
            if _RECURSIVE_CALL_RE.search(text) or _RECURSIVE_DEF_RE.search(text):
                offenders.append(path.name)
        self.assertEqual(
            offenders, [],
            "these suite files still contain the recursive _run_suite pattern "
            "this pass was required to remove",
        )

    def test_no_suite_file_names_another_suite_file_as_a_subprocess_argv(self):
        all_names = {p.name for p in self._all_suite_files()}
        offenders = {}
        for path in self._all_suite_files():
            if path.name == THIS_FILE:
                continue  # this file legitimately imports run_all_v86_tests, never runs a suite
            text = path.read_text(encoding="utf-8")
            hits = [
                other for other in all_names
                if other != path.name and f'"{other}"' in text
            ]
            if hits:
                offenders[path.name] = hits
        self.assertEqual(
            offenders, {},
            f"suite files still reference another suite filename as a literal "
            f"(likely a leftover recursive subprocess argv): {offenders}",
        )


class TestSuiteOrderIsCompleteAndDeduplicated(unittest.TestCase):
    def test_suite_order_has_no_duplicates(self):
        self.assertEqual(len(runner.SUITE_ORDER), len(set(runner.SUITE_ORDER)))

    def test_suite_order_matches_every_test_v86_file_on_disk(self):
        on_disk = {p.name for p in TESTS_DIR.glob("test_v86_*.py")}
        self.assertEqual(
            set(runner.SUITE_ORDER), on_disk,
            "run_all_v86_tests.SUITE_ORDER must list every test_v86_*.py file "
            "exactly once -- nothing missing, nothing stale",
        )

    def test_suite_order_matches_documented_chronology(self):
        # The exact chronological order from tools/resume/v86/README.md.
        expected_prefix = [
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
        ]
        self.assertEqual(runner.SUITE_ORDER[: len(expected_prefix)], expected_prefix)


class TestRunnerExecutesEverySuiteExactlyOnce(unittest.TestCase):
    def test_main_invokes_each_suite_exactly_once_in_order_zero_subprocesses(self):
        calls = []

        def fake_run_suite(filename: str) -> dict:
            calls.append(filename)
            return {
                "suite": filename, "status": "PASS", "returncode": 0,
                "tests_run": 1, "reported_duration_s": 0.01,
                "failures": 0, "errors": 0, "wall_time_s": 0.01, "output": "",
            }

        with mock.patch.object(runner, "run_suite", side_effect=fake_run_suite):
            exit_code = runner.main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, runner.SUITE_ORDER, "every suite must run exactly once, in declared order")
        self.assertEqual(len(calls), len(set(calls)), "no suite may run more than once")

    def test_main_reports_failure_and_nonzero_exit_on_any_suite_failure(self):
        def fake_run_suite(filename: str) -> dict:
            status = "FAIL" if filename == runner.SUITE_ORDER[-1] else "PASS"
            return {
                "suite": filename, "status": status, "returncode": 0 if status == "PASS" else 1,
                "tests_run": 1, "reported_duration_s": 0.01,
                "failures": 0 if status == "PASS" else 1,
                "errors": 0, "wall_time_s": 0.01, "output": "boom",
            }

        with mock.patch.object(runner, "run_suite", side_effect=fake_run_suite):
            exit_code = runner.main()
        self.assertEqual(exit_code, 1)


class TestParseCounts(unittest.TestCase):
    def test_parses_ok_summary(self):
        output = "test_x (module.Test) ... ok\n\n----------------------------------------------------------------------\nRan 42 tests in 3.512s\n\nOK\n"
        counts = runner._parse_counts(output)
        self.assertEqual(counts["tests_run"], 42)
        self.assertAlmostEqual(counts["reported_duration_s"], 3.512)
        self.assertEqual(counts["failures"], 0)
        self.assertEqual(counts["errors"], 0)

    def test_parses_failed_summary(self):
        output = "Ran 10 tests in 1.234s\n\nFAILED (failures=2, errors=1)\n"
        counts = runner._parse_counts(output)
        self.assertEqual(counts["tests_run"], 10)
        self.assertEqual(counts["failures"], 2)
        self.assertEqual(counts["errors"], 1)

    def test_missing_summary_defaults_to_zero(self):
        counts = runner._parse_counts("garbage output, no summary line")
        self.assertEqual(counts, {"tests_run": 0, "reported_duration_s": 0.0, "failures": 0, "errors": 0})


if __name__ == "__main__":
    unittest.main(verbosity=2)
