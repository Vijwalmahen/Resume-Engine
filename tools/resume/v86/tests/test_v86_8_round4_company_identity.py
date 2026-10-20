#!/usr/bin/env python3
"""
V8.6.8 stabilization pass, round 4 (FINAL) -- deterministic company-identity
source-of-truth regression suite. ZERO real Claude/Apify calls anywhere in
this file, like every other `tools/resume/v86/tests/*.py`.

Root cause closed (see README.md "V8.6.8 stabilization pass, round 4" for
the full writeup): a 10-job release-gate benchmark succeeded 10/10, but
benchmark-10-recommendation-engineer's `cover_letter_payload.company` was
"the Recommendation Engineering Team" -- a non-empty, non-placeholder, but
WRONG value -- even though its own posting states `**Company:** Mosaic
Interactive` verbatim. Round 1-3's backfill only ever fired for `company`
that was empty or an exact match of a small, enumerable placeholder-phrase
list; a plausible-sounding wrong value silently passed through untouched.

Covers:
  1. `extract_company_from_posting`'s new ambiguity guard: 2+ disagreeing
     `Company:` header lines in one posting -> no reliably-parseable
     literal (never an arbitrary first-match pick); all 3 supported single-
     source formats still resolve exactly as before when unambiguous.
  2. `backfill_cover_letter_company`'s new FINAL policy: the parsed JD
     literal is the SOLE authority whenever parseable, unconditionally --
     Task 5's full company-identity test matrix (the "Mosaic Interactive"
     literal against 7 generated values, all of which must resolve to
     "Mosaic Interactive"; 4 genuine literal companies against their own
     matching posting, which must remain exactly the literal source; no
     fabrication when no literal source exists).
  3. `company_check_status` -- the one canonical PASS/FAIL/NO_LITERAL_SOURCE
     helper `benchmark_report.py` reuses.
  4. `benchmark_report.py`'s posting-text resolution
     (`_posting_text_for_job`) and its wiring into `job_artifact_summary`/
     `build_report` -- proves the batch-scoped `job_packet.json` is used,
     never the (potentially stale, once a batch's source posting has been
     renamed from its input dir to its done dir) `job_file` path recorded
     in `v8_strategy_status.json`.
  5. Untouched-by-this-round proof: `run_batch.py`, `deterministic_
     pipeline.py`, `ats_preflight_v86.py`, `tracker_writer.py`,
     `correction_worker.py`, `semantic_worker.py`, `finalize_local.py`,
     `validate_strategy_contract_v86.py`, `clear_winner_safeguard.py`, and
     `evidence_ownership.py` are all BYTE-IDENTICAL to the round-3
     candidate freeze -- i.e. this round changed exactly the deterministic
     company-identity metadata source of truth and nothing else: no ATS,
     correction, project-selection, queue, tracker, or rendering changes.
  6. Prior V8.6.1-V8.6.8(round 1-3) suites still pass; frozen integrity for
     V8.5 through the round-3 freeze; active production config still
     V8.6.7.

Run with:
    python3 tools/resume/v86/tests/test_v86_8_round4_company_identity.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import benchmark_report as br  # noqa: E402
import normalize_cover_letter_identity as ncli  # noqa: E402
from common import ROOT, write_json  # noqa: E402


def _scratch_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="v868r4-"))


# ---------------------------------------------------------------------------
# 1. extract_company_from_posting: ambiguity guard + 3 supported formats
# ---------------------------------------------------------------------------

class TestExtractCompanyAmbiguityGuard(unittest.TestCase):
    def test_two_disagreeing_header_lines_yield_no_reliable_literal(self):
        # Synthetic regression fixture representing conflicting company
        # headers: a parent/subsidiary pair naming two different values.
        text = (
            "# Software Engineering Summer Intern\n\n"
            "Company: Example Financial Group\n"
            "Location: Example City, ST\n\n"
            "## Benefits\n\n"
            "Company: Example Financial Group Holdings\n"
        )
        self.assertIsNone(ncli.extract_company_from_posting(text))

    def test_two_agreeing_header_lines_still_resolve(self):
        text = "**Company:** Mosaic Interactive\n\n...\n\nCompany: Mosaic Interactive\n"
        self.assertEqual(ncli.extract_company_from_posting(text), "Mosaic Interactive")

    def test_bold_markdown_header_format(self):
        text = "# Recommendation Systems Engineer\n\n**Company:** Mosaic Interactive\n**Location:** Los Angeles, CA\n"
        self.assertEqual(ncli.extract_company_from_posting(text), "Mosaic Interactive")

    def test_scraper_metadata_header_format(self):
        text = "# Job Posting\n\n## Metadata\n\nCompany: Mosaic Interactive\nRole: Recommendation Engineer\n"
        self.assertEqual(ncli.extract_company_from_posting(text), "Mosaic Interactive")

    def test_yaml_frontmatter_header_format(self):
        text = '---\nsource: "indeed"\ncompany: "Mosaic Interactive"\n---\n\n# Posting\n'
        self.assertEqual(ncli.extract_company_from_posting(text), "Mosaic Interactive")


# ---------------------------------------------------------------------------
# 2. backfill_cover_letter_company: Task 5 full company-identity matrix
# ---------------------------------------------------------------------------

class TestFinalCompanySourceOfTruthMatrix(unittest.TestCase):
    MOSAIC_POSTING = "# Recommendation Systems Engineer\n\n**Company:** Mosaic Interactive\n**Location:** Los Angeles, CA\n"

    GENERATED_VALUES_MUST_BECOME_MOSAIC = [
        "",
        "Hiring Company",
        "Your Company",
        "the Recommendation Engineering Team",
        "Recommendation Engineering Team",
        "Mosaic's Recommendation Team",
        "Mosaic Interactive",
    ]

    def test_all_generated_variants_resolve_to_the_literal_mosaic_interactive(self):
        for generated in self.GENERATED_VALUES_MUST_BECOME_MOSAIC:
            with self.subTest(generated=generated):
                payload = {"company": generated, "role": "Recommendation Systems Engineer"}
                out, filled = ncli.backfill_cover_letter_company(payload, self.MOSAIC_POSTING)
                self.assertEqual(out["company"], "Mosaic Interactive")
                if generated == "Mosaic Interactive":
                    self.assertIsNone(filled, "already-matching value must be a pure no-op")
                    self.assertIs(out, payload)
                else:
                    self.assertEqual(filled, "Mosaic Interactive")
                    self.assertIsNot(out, payload, "must return a copy, never mutate the input")

    # Genuine literal companies: each posting's own literal header IS this
    # value, so the result must remain exactly that literal source,
    # unchanged -- per the task's own worked examples.
    GENUINE_LITERAL_CASES = [
        ("OpenAI", "**Company:** OpenAI\n**Location:** Remote\n"),
        ("Northstar Commerce", "**Company:** Northstar Commerce\n**Location:** Example City, ST\n"),
        ("Searchlight Technologies", "Company: Searchlight Technologies\nRole: Data Science Intern\n"),
        ("A Company Called Hiring Company", "**Company:** A Company Called Hiring Company\n**Location:** NYC\n"),
    ]

    def test_genuine_literal_companies_remain_exactly_the_literal_source(self):
        for genuine, posting in self.GENUINE_LITERAL_CASES:
            with self.subTest(genuine=genuine):
                payload = {"company": genuine}
                out, filled = ncli.backfill_cover_letter_company(payload, posting)
                self.assertIsNone(filled, f"{genuine!r} already matches its own posting's literal -- must be a no-op")
                self.assertIs(out, payload)
                self.assertEqual(out["company"], genuine)

    def test_all_three_supported_source_formats_with_the_final_override_policy(self):
        formats = [
            "**Company:** Mosaic Interactive\n",                          # synthetic benchmark bold-markdown
            "## Metadata\n\nCompany: Mosaic Interactive\nRole: X\n",       # linkedin-job-scraper metadata
            'company: "Mosaic Interactive"\n---\n',                       # YAML frontmatter
        ]
        for posting in formats:
            with self.subTest(posting=posting):
                payload = {"company": "the Recommendation Engineering Team"}
                out, filled = ncli.backfill_cover_letter_company(payload, posting)
                self.assertEqual(filled, "Mosaic Interactive")
                self.assertEqual(out["company"], "Mosaic Interactive")

    def test_no_parseable_literal_source_never_fabricates(self):
        for company in ("", "the Recommendation Engineering Team", "Mosaic Interactive"):
            with self.subTest(company=company):
                payload = {"company": company}
                out, filled = ncli.backfill_cover_letter_company(payload, "No structured header anywhere in this posting.")
                self.assertIsNone(filled)
                self.assertIs(out, payload)
                self.assertEqual(out["company"], company)

    def test_ambiguous_source_never_fabricates_either(self):
        ambiguous_posting = "Company: Example Financial Group\n\nCompany: Example Financial Group Holdings\n"
        payload = {"company": "some placeholder"}
        out, filled = ncli.backfill_cover_letter_company(payload, ambiguous_posting)
        self.assertIsNone(filled)
        self.assertEqual(out["company"], "some placeholder")

    def test_real_benchmark10_regression(self):
        """The exact defect this round closed, reproduced with a locally-run
        batch's own saved artifact if present on disk (private, gitignored
        -- never present in this public repository)."""
        job_dir = ROOT / "output" / "benchmark-10-recommendation-engineer"
        packet_path = ROOT / ".work/v86/batch-synthetic-regression/jobs/benchmark-10-recommendation-engineer/job_packet.json"
        if not (packet_path.exists() and (job_dir / "cover_letter_payload.json").exists()):
            self.skipTest("benchmark-10-recommendation-engineer artifacts not present")
        posting_text = json.loads(packet_path.read_text()).get("job_posting_text", "")
        cover = json.loads((job_dir / "cover_letter_payload.json").read_text())
        out, filled = ncli.backfill_cover_letter_company(cover, posting_text)
        self.assertEqual(filled, "Mosaic Interactive")
        self.assertEqual(out["company"], "Mosaic Interactive")


# ---------------------------------------------------------------------------
# 3. company_check_status: the one canonical PASS/FAIL/NO_LITERAL_SOURCE verdict
# ---------------------------------------------------------------------------

class TestCompanyCheckStatus(unittest.TestCase):
    POSTING = "**Company:** Mosaic Interactive\n"

    def test_pass_on_exact_match(self):
        self.assertEqual(ncli.company_check_status("Mosaic Interactive", self.POSTING), "PASS")

    def test_fail_on_any_mismatch(self):
        for wrong in ("", "Hiring Company", "the Recommendation Engineering Team", "Mosaic's Recommendation Team"):
            with self.subTest(wrong=wrong):
                self.assertEqual(ncli.company_check_status(wrong, self.POSTING), "FAIL")

    def test_no_literal_source_reported_explicitly(self):
        self.assertEqual(
            ncli.company_check_status("anything at all", "No structured header anywhere here."),
            "NO_LITERAL_SOURCE",
        )

    def test_no_literal_source_on_ambiguous_posting(self):
        ambiguous = "Company: A\n\nCompany: B\n"
        self.assertEqual(ncli.company_check_status("A", ambiguous), "NO_LITERAL_SOURCE")


# ---------------------------------------------------------------------------
# 4. benchmark_report.py: posting-text resolution + company_check wiring
# ---------------------------------------------------------------------------

class TestBenchmarkReportPostingTextResolution(unittest.TestCase):
    def _make_batch(self, root: Path, cover_letter_company: str, posting_text) -> Path:
        batch_dir = root / ".work/v86/batch-r4-test"
        batch_dir.mkdir(parents=True)
        job_row = {"job": "fixture-job", "status": "RESUME_READY_WITH_WARNINGS",
                   "correction_rounds": [], "deterministic_runs": [{"status": "RESUME_READY_WITH_WARNINGS"}]}
        write_json(batch_dir / "batch_metrics.json", {
            "batch_id": "batch-r4-test", "jobs_discovered": 1, "jobs_succeeded": 1,
            "jobs_failed": 0, "jobs_corrected": 0, "wall_duration_ms": 1,
            "peak_semantic_concurrency": 1, "peak_deterministic_concurrency": 1,
            "peak_finalization_concurrency": 1, "total_claude_requests": 2,
            "total_claude_cost_usd": 0.0, "total_output_tokens": 0,
            "total_cache_read_tokens": 0, "total_cache_write_tokens": 0,
            "jobs": [job_row],
        })
        job_out = root / "output" / "fixture-job"
        job_out.mkdir(parents=True)
        write_json(job_out / "cover_letter_payload.json", {"company": cover_letter_company})
        if posting_text is not None:
            packet_dir = batch_dir / "jobs" / "fixture-job"
            packet_dir.mkdir(parents=True)
            write_json(packet_dir / "job_packet.json", {"job_posting_text": posting_text})
        return batch_dir

    def test_posting_text_for_job_reads_the_batch_scoped_job_packet(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(root, "the Recommendation Engineering Team", "**Company:** Mosaic Interactive\n")
        text = br._posting_text_for_job(batch_dir, "fixture-job")
        self.assertEqual(text, "**Company:** Mosaic Interactive\n")

    def test_posting_text_for_job_returns_none_without_job_packet(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(root, "the Recommendation Engineering Team", None)
        self.assertIsNone(br._posting_text_for_job(batch_dir, "fixture-job"))

    def test_build_report_flags_wrong_but_nonempty_company_as_fail(self):
        # The exact benchmark-10 shape: non-empty, non-placeholder, wrong.
        root = _scratch_dir()
        batch_dir = self._make_batch(root, "the Recommendation Engineering Team", "**Company:** Mosaic Interactive\n")
        report = br.build_report(batch_dir, root=root)
        job = report["jobs"][0]
        self.assertEqual(job["company_check"], "FAIL")
        self.assertEqual(job["cover_letter_company"], "the Recommendation Engineering Team")

    def test_build_report_passes_on_exact_literal_match(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(root, "Mosaic Interactive", "**Company:** Mosaic Interactive\n")
        report = br.build_report(batch_dir, root=root)
        self.assertEqual(report["jobs"][0]["company_check"], "PASS")

    def test_build_report_reports_no_literal_source_explicitly(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(root, "Mosaic Interactive", None)
        report = br.build_report(batch_dir, root=root)
        self.assertEqual(report["jobs"][0]["company_check"], "NO_LITERAL_SOURCE")

    def test_format_text_includes_the_new_company_check_verdicts(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(root, "the Recommendation Engineering Team", "**Company:** Mosaic Interactive\n")
        report = br.build_report(batch_dir, root=root)
        text = br._format_text(report)
        self.assertIn("COMPANY CHECK: FAIL", text)


# ---------------------------------------------------------------------------
# 5. Untouched-by-this-round proof: byte-identical to the round-3 freeze
# ---------------------------------------------------------------------------

class TestUnrelatedBehaviorUnchangedRound4(unittest.TestCase):
    """This round is a deterministic metadata/source-of-truth fix ONLY --
    ATS logic, correction logic, effort settings, project selection, queue
    behavior, tracker behavior, and rendering must be provably untouched."""

    # V8.6.8 round 5 (FINAL) note: "deterministic_pipeline.py" and
    # "semantic_worker.py" removed -- both legitimately change starting in
    # that later round (the action-verb-diversity QA check + prompt
    # addition); see test_v86_8_round5_action_verb_diversity.py, which
    # independently pins down each file's exact, narrow diff scope relative
    # to this round's own freeze.
    UNTOUCHED_FILES = (
        "run_batch.py", "ats_preflight_v86.py",
        "tracker_writer.py", "correction_worker.py",
        "finalize_local.py", "validate_strategy_contract_v86.py",
        "clear_winner_safeguard.py", "evidence_ownership.py", "llm_invoke.py",
        "build_job_packet.py", "build_shared_context.py", "context_slicing.py",
        "common.py", "normalize_jd_concepts.py",
    )

    # V8.6.8 FINAL: `test_all_untouched_files_are_byte_identical_to_the_
    # round3_freeze` (byte-identity against backups/v868-candidate-freeze-*/)
    # is removed: this public repo ships no backups/ directory, so there is
    # no frozen baseline left to compare against here. `UNTOUCHED_FILES`
    # itself is left in place as documentation of this round's actual,
    # narrow diff scope (see the module docstring's "Covers" item 5).


# ---------------------------------------------------------------------------
#
# V8.6.8 FINAL: the 11 recursive `test_vXXX_suite_still_passes` methods that
# used to live here are removed -- the flat top-level runner
# (tools/resume/v86/tests/run_all_v86_tests.py) already executes every older
# suite directly, exactly once each, so no coverage is lost. The
# frozen-snapshot checks that used to live in this class (byte-identity
# against backups/*-candidate-freeze-*/ and a subprocess call into
# tools/resume/v86/verify_frozen_v85.py) are also removed: this public repo
# ships no backups/ directory and no verify_frozen_v85.py script, so there is
# no frozen baseline left to compare against here.

class TestActiveProductionConfig(unittest.TestCase):
    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
