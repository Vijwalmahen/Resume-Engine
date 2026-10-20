#!/usr/bin/env python3
"""
V8.6.8 round 2 -- offline regression suite for 3 defects found during a
realistic 10-job benchmark retest. ZERO real Claude/Apify calls anywhere in
this file, like every other `tools/resume/v86/tests/*.py`.

Root causes closed (see README.md "V8.6.8 stabilization pass, round 2" for
the full writeup):

1. **Stale cross-batch correction-round state**
   (`deterministic_pipeline.write_strategy_files`): `output/<job_key>/` is a
   path keyed only by job name, reused by any later, independent batch run
   of the same job. `previous_round()` read `v8_correction_status.json`
   from that shared directory as if it described the CURRENT attempt,
   so a job that had already used both correction rounds in an EARLIER
   batch run instantly reported `CORRECTION_LIMIT_EXCEEDED` on a brand-new
   attempt that had made zero correction calls (`benchmark-01-ml-engineer`,
   `benchmark-06-nlp-llm-engineer`). `write_strategy_files` now clears that
   stale bookkeeping before a fresh attempt's files are written.

2. **Negated ATS evidence treated as truthful support**
   (`ats_preflight_v86.filter_negated_evidence_hits`): the frozen
   `find_evidence`'s substring match has no notion of negation, so
   "...(not random cross-validation)" was accepted as support for the
   concept "Cross-validation" (`benchmark-01-ml-engineer`'s real
   `ats_gate_pass: false`). A concept whose only evidence hit(s) are
   negated in their own clause is now reclassified `UNSUPPORTED` (never
   fabricated, never requested from correction).

3. **Incomplete company-placeholder set**
   (`normalize_cover_letter_identity._GENERIC_PLACEHOLDER_COMPANY_NAMES`):
   "Hiring Company" (and several literal equivalents named by this pass's
   own task instructions) were not recognized, so
   `benchmark-01-ml-engineer`'s cover letter shipped with `company: "Hiring
   Company"` even though its own posting has a parseable
   `**Company:** Northstar Commerce` header. `benchmark_report.py` now also
   surfaces a `company_check` derived from the SAME canonical helper.

Run with:
    python3 tools/resume/v86/tests/test_v86_8_round2_hardening.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ats_preflight_v86 as ats  # noqa: E402
import benchmark_report as br  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import normalize_cover_letter_identity as ncli  # noqa: E402
from common import write_json  # noqa: E402


def _scratch_dir() -> Path:
    return Path(tempfile.mkdtemp(prefix="v868r2-"))


# ---------------------------------------------------------------------------
# 1. Correction-round state-machine invariant (Phase 4/8)
# ---------------------------------------------------------------------------

class TestCorrectionStateResetInvariant(unittest.TestCase):
    """`previous_round()` itself is unchanged -- it is meant to reflect
    correction rounds actually applied DURING the current job's on-disk
    lifetime. The bug was that nothing ever reset that lifetime's start
    when a fresh batch attempt reused the same `output/<job_key>` directory.
    These tests reproduce the OLD failure mode directly (by writing stale
    state and reading `previous_round()` before any reset runs -- exactly
    what happened in production) and then prove `write_strategy_files`
    (the one call site that fires exactly once per fresh attempt) fixes it.
    """

    def test_stale_round_2_state_reproduces_the_old_bug_mechanism(self):
        job_dir = _scratch_dir()
        write_json(job_dir / "v8_correction_status.json", {"correction_round": 2, "status": "CORRECTED"})
        # This IS the exact mechanism that produced CORRECTION_LIMIT_EXCEEDED
        # with 0 correction calls in the benchmark retest that found this
        # bug: nothing in the old code path cleared this before a fresh
        # attempt's checks ran.
        self.assertEqual(detp.previous_round(job_dir), 2)

    def test_write_strategy_files_resets_stale_correction_round_to_zero(self):
        job_dir = _scratch_dir()
        write_json(job_dir / "v8_correction_status.json", {"correction_round": 2, "status": "CORRECTED"})
        write_json(job_dir / "correction_request.json", {"correction_round": 2, "reasons": ["DENSITY"]})
        write_json(job_dir / "correction_request.round1.json", {"correction_round": 1})
        write_json(job_dir / "correction_request.round2.json", {"correction_round": 2})
        write_json(job_dir / "density_guard.json", {"status": "DENSE_ONE_PAGE"})

        detp.write_strategy_files(
            job_dir, "fake_job.md", "fake-job",
            {"concepts": []}, {"experience_alternate_bullets": {}, "selected_project": {}}, {"company": ""},
            "Some Project",
        )

        self.assertEqual(detp.previous_round(job_dir), 0)
        self.assertFalse((job_dir / "v8_correction_status.json").exists())
        self.assertFalse((job_dir / "correction_request.json").exists())
        self.assertEqual(list(job_dir.glob("correction_request.round*.json")), [])
        self.assertFalse((job_dir / "density_guard.json").exists())

    def test_write_strategy_files_is_a_noop_for_a_genuinely_fresh_job_dir(self):
        """No stale files -> no exception, no spurious deletion attempts."""
        job_dir = _scratch_dir()
        detp.write_strategy_files(
            job_dir, "fake_job.md", "fake-job",
            {"concepts": []}, {"experience_alternate_bullets": {}, "selected_project": {}}, {"company": ""},
            "Some Project",
        )
        self.assertEqual(detp.previous_round(job_dir), 0)
        self.assertTrue((job_dir / "resume_payload.json").exists())

    def test_correction_round_counter_still_advances_correctly_within_one_run(self):
        """Once a fresh attempt begins (write_strategy_files has reset state),
        real, in-run correction rounds must still be tracked accurately --
        this pass must not have broken genuine round-by-round counting."""
        job_dir = _scratch_dir()
        detp.write_strategy_files(
            job_dir, "fake_job.md", "fake-job",
            {"concepts": []},
            {"experience_alternate_bullets": {}, "selected_project": {"owner_id": "Bound Project", "title": "Bound Project"}},
            {"company": ""},
            "Bound Project",
        )
        self.assertEqual(detp.previous_round(job_dir), 0)

        req1 = detp.write_correction_request(job_dir, ["DENSITY"], {"density": {}})
        self.assertEqual(json.loads(req1.read_text())["correction_round"], 1)

        detp.apply_correction(
            job_dir, 1, {"selected_project": {"owner_id": "Bound Project", "title": "Bound Project"}},
            {"status": "CORRECTED", "reasons": ["r1"]},
            "Bound Project",
        )
        self.assertEqual(detp.previous_round(job_dir), 1)

        req2 = detp.write_correction_request(job_dir, ["DENSITY"], {"density": {}})
        self.assertEqual(json.loads(req2.read_text())["correction_round"], 2)

        detp.apply_correction(
            job_dir, 2, {"selected_project": {"owner_id": "Bound Project", "title": "Bound Project"}},
            {"status": "CORRECTED", "reasons": ["r2"]},
            "Bound Project",
        )
        self.assertEqual(detp.previous_round(job_dir), 2)
        # A genuine 3rd round really is over the limit -- this is the
        # legitimate case CORRECTION_LIMIT_EXCEEDED exists for.
        self.assertGreater(detp.previous_round(job_dir) + 1, detp.CORRECTION_ROUND_LIMIT)

    def test_mechanical_preflight_pretrim_never_touches_correction_round_state(self):
        """The V8.6.8 (round 1) mechanical pre-trim must remain a pure,
        same-round, zero-Claude-call operation -- it must never write
        v8_correction_status.json or otherwise advance previous_round()."""
        job_dir = _scratch_dir()
        payload = {
            "experience_alternate_bullets": {"experience_2": ["x" * 10, "y" * 10]},
            "selected_project": {
                "title": "T", "technology_line": "z" * 200,
                "bullets": ["b" * 10, "c" * 10],
            },
        }
        trimmed, trimmed_fields = detp.enforce_correction_length_budget(payload)
        self.assertIn("selected_project.technology_line", trimmed_fields)
        self.assertFalse((job_dir / "v8_correction_status.json").exists())
        self.assertEqual(detp.previous_round(job_dir), 0)


# ---------------------------------------------------------------------------
# 2. Negated ATS evidence guard (Phase 2/8)
# ---------------------------------------------------------------------------

class TestNegatedEvidenceGuard(unittest.TestCase):
    def test_real_benchmark01_cross_validation_hit_is_excluded(self):
        hits = [{
            "file": "evidence.json", "source": "$.projects[3].scope_facts[1]",
            "text": (
                "Compares six classifiers with four scheduling-heuristic baselines using "
                "held-out and expanding-window validation (not random cross-validation)"
            ),
        }]
        kept, negated = ats.filter_negated_evidence_hits(hits, ["Cross-validation"])
        self.assertEqual(kept, [])
        self.assertEqual(len(negated), 1)

    def test_genuine_positive_mention_is_never_excluded(self):
        hits = [{
            "file": "evidence.json", "source": "$.projects[0].scope_facts[0]",
            "text": "Implemented k-fold cross-validation to evaluate model generalization.",
        }]
        kept, negated = ats.filter_negated_evidence_hits(hits, ["Cross-validation"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(negated, [])

    def test_negation_in_a_different_sentence_does_not_leak_across_boundary(self):
        """A negation several sentences earlier must never suppress an
        unrelated, later, genuinely-positive mention -- the clause window is
        bounded by sentence-ending punctuation."""
        hits = [{
            "file": "evidence.json", "source": "$.projects[0].scope_facts[0]",
            "text": (
                "The team decided not to use manual feature selection. "
                "Cross-validation was used to tune hyperparameters across five folds."
            ),
        }]
        kept, negated = ats.filter_negated_evidence_hits(hits, ["Cross-validation"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(negated, [])

    def test_unrelated_concept_terms_are_never_affected(self):
        """Historical-replay-style narrowness proof: a handful of other,
        unrelated concept terms/evidence pairs (none negated) must be
        completely unaffected by this guard."""
        cases = [
            (["Python"], "Built data pipelines in Python for five years."),
            (["Docker"], "Containerized services with Docker and Kubernetes."),
            (["REST APIs"], "Developed REST APIs for model-serving workflows."),
        ]
        for forms, text in cases:
            hits = [{"file": "e.json", "source": "$.projects[0].scope_facts[0]", "text": text}]
            kept, negated = ats.filter_negated_evidence_hits(hits, forms)
            self.assertEqual(len(kept), 1, msg=f"{forms} incorrectly excluded from: {text!r}")
            self.assertEqual(negated, [])

    def test_evaluate_reclassifies_negated_only_concept_as_unsupported(self):
        """End-to-end through `evaluate()` (not just the filter helper):
        a REQUIRED concept whose only evidence is negated must resolve to
        UNSUPPORTED, drop out of missing_supported_required_terms, and never
        block the gate on its own -- this is the exact benchmark-01 fix."""
        scratch = _scratch_dir()
        evidence_root = scratch / "evidence"
        evidence_root.mkdir()
        (evidence_root / "verified_resume_evidence.json").write_text(json.dumps({
            "projects": [{
                "name": "Project Cascade",
                "scope_facts": [
                    "Uses pandas and NumPy for data processing.",
                    "Compares several classifiers using expanding-window validation (not random cross-validation).",
                ],
            }],
        }))
        requirements = {
            "job_key": "fixture-job",
            "concepts": [
                {"term": "Cross-validation", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "medium"},
                {"term": "pandas", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "medium"},
            ],
        }
        resume_text = "Used pandas for data processing on the Project Cascade project."
        result = ats.evaluate(requirements, resume_text, evidence_root, aliases={})

        cv = next(c for c in result["concepts"] if c["term"] == "Cross-validation")
        self.assertEqual(cv["status"], "UNSUPPORTED")
        self.assertFalse(cv["supported_by_master_evidence"])
        self.assertEqual(cv["evidence_hits"], [])
        self.assertEqual(len(cv["negated_evidence_hits_excluded"]), 1)
        self.assertNotIn("Cross-validation", result["missing_supported_required_terms"])
        self.assertIn("Cross-validation", result["unsupported_required_terms"])
        # pandas is genuinely supported+present -> still counts toward gate.
        self.assertTrue(result["gate_pass"])


# ---------------------------------------------------------------------------
# 3. Company placeholder hardening (Phase 5/9)
# ---------------------------------------------------------------------------

class TestCompanyPlaceholderHardeningRound2(unittest.TestCase):
    NEW_PLACEHOLDERS = [
        "Hiring Company", "hiring company", "HIRING COMPANY",
        "The Hiring Company",
        "Hiring Organization", "Hiring Organisation",
        "Employer", "The Employer",
    ]

    PRE_EXISTING_PLACEHOLDERS = [
        "Your Company", "The Company", "Your Organization", "The Organization",
        "The Hiring Organization",
    ]

    GENUINE_VALUES = [
        "Northstar Commerce", "OpenAI", "Searchlight Technologies",
        "A Company Called Hiring Company",
    ]

    POSTING = (
        "# Machine Learning Engineer I\n\n"
        "**Company:** Northstar Commerce\n"
        "**Location:** Example City, ST\n"
    )

    def test_new_placeholders_backfill_when_literal_company_parseable(self):
        for placeholder in self.NEW_PLACEHOLDERS:
            with self.subTest(placeholder=placeholder):
                payload = {"company": placeholder, "role": "Machine Learning Engineer"}
                backfilled, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
                self.assertEqual(filled, "Northstar Commerce")
                self.assertEqual(backfilled["company"], "Northstar Commerce")

    def test_pre_existing_placeholders_still_backfill(self):
        for placeholder in self.PRE_EXISTING_PLACEHOLDERS:
            with self.subTest(placeholder=placeholder):
                payload = {"company": placeholder}
                backfilled, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
                self.assertEqual(filled, "Northstar Commerce")

    def test_genuine_company_names_matching_the_posting_are_never_rewritten(self):
        # V8.6.8 round 4 (FINAL): these GENUINE_VALUES are unaffected here
        # because none of them differs from the posting's own parsed
        # literal ("Northstar Commerce") -- a genuinely-DIFFERENT value
        # (even a non-placeholder one) is no longer exempt; see
        # test_v86_8_round4_company_identity.py for that policy directly.
        for genuine in self.GENUINE_VALUES:
            with self.subTest(genuine=genuine):
                payload = {"company": genuine}
                backfilled, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
                if genuine == "Northstar Commerce":
                    self.assertIsNone(filled)
                    self.assertIs(backfilled, payload)  # exact no-op, no copy
                else:
                    # A genuinely different literal name (e.g. "OpenAI",
                    # not this posting's own employer) is now correctly
                    # replaced by the posting's own authoritative literal --
                    # this posting is simply not about that company.
                    self.assertEqual(filled, "Northstar Commerce")
                    self.assertEqual(backfilled["company"], "Northstar Commerce")

    def test_empty_company_still_backfills(self):
        for empty in ("", "   ", None):
            payload = {"company": empty}
            backfilled, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
            self.assertEqual(filled, "Northstar Commerce")

    def test_no_parseable_company_header_means_no_fabrication(self):
        payload = {"company": "Hiring Company"}
        backfilled, filled = ncli.backfill_cover_letter_company(payload, "No company header at all here.")
        self.assertIsNone(filled)
        self.assertEqual(backfilled["company"], "Hiring Company")  # left as-is, never guessed

    def test_all_three_supported_source_formats(self):
        formats = [
            "**Company:** Northstar Commerce\n",                       # synthetic benchmark bold-markdown
            "## Metadata\n\nCompany: Northstar Commerce\nRole: X\n",    # linkedin-job-scraper metadata
            'company: "Northstar Commerce"\n---\n',                    # YAML frontmatter
        ]
        for posting in formats:
            with self.subTest(posting=posting):
                payload = {"company": "Hiring Company"}
                backfilled, filled = ncli.backfill_cover_letter_company(payload, posting)
                self.assertEqual(filled, "Northstar Commerce")

    def test_benchmark01_real_regression_hiring_company_to_northstar_commerce(self):
        """Reproduces the exact real defect: benchmark-01-ml-engineer's real
        posting + the real generated 'Hiring Company' value."""
        real_posting = (Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "benchmark01_company_placeholder" / "job_posting.md")
        if not real_posting.exists():
            self.skipTest("benchmark01_company_placeholder fixture not present")
        posting_text = real_posting.read_text()
        payload = {"company": "Hiring Company"}
        backfilled, filled = ncli.backfill_cover_letter_company(payload, posting_text)
        self.assertEqual(filled, "Northstar Commerce")

    def test_is_recognized_placeholder_or_empty_matches_backfill_eligibility(self):
        """One source of truth: the public read-only helper benchmark_report.py
        uses must agree exactly with the backfill's own eligibility check."""
        for value in self.NEW_PLACEHOLDERS + self.PRE_EXISTING_PLACEHOLDERS + ["", None, "   "]:
            self.assertTrue(ncli.is_recognized_placeholder_or_empty(value), msg=repr(value))
        for value in self.GENUINE_VALUES:
            self.assertFalse(ncli.is_recognized_placeholder_or_empty(value), msg=repr(value))


# ---------------------------------------------------------------------------
# 4. benchmark_report.py: canonical helper reuse + correction-state surfacing
# ---------------------------------------------------------------------------

class TestBenchmarkReportRound2(unittest.TestCase):
    # V8.6.8 round 4 (FINAL): `company_check` now compares against the
    # posting's own parsed literal company (read from a batch-scoped
    # `jobs/<job_key>/job_packet.json`), not merely a placeholder-list
    # lookup -- so these fixtures now supply a real posting_text whenever
    # the test cares about the resulting verdict.
    DEFAULT_POSTING = "**Company:** Northstar Commerce\n**Location:** Example City, ST\n"

    def _make_batch(self, root: Path, job_row: dict, cover_letter_company: str, posting_text: str = None) -> Path:
        batch_dir = root / ".work/v86/batch-test"
        batch_dir.mkdir(parents=True)
        write_json(batch_dir / "batch_metrics.json", {
            "batch_id": "batch-test", "jobs_discovered": 1, "jobs_succeeded": 0,
            "jobs_failed": 1, "jobs_corrected": 0, "wall_duration_ms": 1,
            "peak_semantic_concurrency": 1, "peak_deterministic_concurrency": 1,
            "peak_finalization_concurrency": 1, "total_claude_requests": 2,
            "total_claude_cost_usd": 0.0, "total_output_tokens": 0,
            "total_cache_read_tokens": 0, "total_cache_write_tokens": 0,
            "jobs": [job_row],
        })
        job_out = root / "output" / job_row["job"]
        job_out.mkdir(parents=True)
        write_json(job_out / "cover_letter_payload.json", {"company": cover_letter_company})
        if posting_text is not None:
            packet_dir = batch_dir / "jobs" / job_row["job"]
            packet_dir.mkdir(parents=True)
            write_json(packet_dir / "job_packet.json", {"job_posting_text": posting_text})
        return batch_dir

    def test_company_check_fail_for_a_literal_mismatch(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(
            root, {"job": "fixture-job", "status": "CORRECTION_LIMIT_EXCEEDED",
                   "correction_rounds": [], "deterministic_runs": [{"status": "CORRECTION_LIMIT_EXCEEDED"}]},
            cover_letter_company="Hiring Company", posting_text=self.DEFAULT_POSTING,
        )
        report = br.build_report(batch_dir, root=root)
        job = report["jobs"][0]
        self.assertEqual(job["company_check"], "FAIL")
        self.assertEqual(job["cover_letter_company"], "Hiring Company")

    def test_company_check_no_literal_source_when_posting_unresolvable(self):
        # V8.6.8 round 4: no job_packet.json at all (e.g. a very old batch
        # predating this pass) -> report NO_LITERAL_SOURCE explicitly,
        # never a fabricated FAIL just because it "looks like" a
        # placeholder.
        root = _scratch_dir()
        batch_dir = self._make_batch(
            root, {"job": "fixture-job", "status": "CORRECTION_LIMIT_EXCEEDED",
                   "correction_rounds": [], "deterministic_runs": [{"status": "CORRECTION_LIMIT_EXCEEDED"}]},
            cover_letter_company="Hiring Company",
        )
        report = br.build_report(batch_dir, root=root)
        job = report["jobs"][0]
        self.assertEqual(job["company_check"], "NO_LITERAL_SOURCE")

    def test_company_check_pass_for_genuine_name(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(
            root, {"job": "fixture-job", "status": "RESUME_READY",
                   "correction_rounds": [], "deterministic_runs": [{"status": "RESUME_READY"}]},
            cover_letter_company="Northstar Commerce", posting_text=self.DEFAULT_POSTING,
        )
        report = br.build_report(batch_dir, root=root)
        self.assertEqual(report["jobs"][0]["company_check"], "PASS")

    def test_invariant_violation_flagged_for_zero_attempt_limit_exceeded(self):
        """The exact real bug shape: CORRECTION_LIMIT_EXCEEDED, correction_rounds
        empty -> must be surfaced as an invariant violation, not hidden."""
        root = _scratch_dir()
        batch_dir = self._make_batch(
            root, {"job": "fixture-job", "status": "CORRECTION_LIMIT_EXCEEDED",
                   "correction_rounds": [], "deterministic_runs": [{"status": "CORRECTION_LIMIT_EXCEEDED"}]},
            cover_letter_company="Northstar Commerce",
        )
        report = br.build_report(batch_dir, root=root)
        job = report["jobs"][0]
        self.assertTrue(job["correction_invariant_violation"])
        self.assertEqual(job["correction_attempted_rounds"], 0)
        self.assertTrue(job["correction_exhausted"])

    def test_no_invariant_violation_for_a_genuine_exhausted_job(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(
            root, {
                "job": "fixture-job", "status": "CORRECTION_LIMIT_EXCEEDED",
                "correction_rounds": [
                    {"round": 1, "ok": True}, {"round": 2, "ok": True},
                ],
                "deterministic_runs": [
                    {"status": "NEEDS_LLM_CORRECTION"}, {"status": "NEEDS_LLM_CORRECTION"},
                    {"status": "CORRECTION_LIMIT_EXCEEDED"},
                ],
            },
            cover_letter_company="Northstar Commerce",
        )
        report = br.build_report(batch_dir, root=root)
        job = report["jobs"][0]
        self.assertFalse(job["correction_invariant_violation"])
        self.assertEqual(job["correction_attempted_rounds"], 2)
        self.assertEqual(job["correction_requested_rounds"], 2)
        self.assertEqual(job["correction_succeeded_rounds"], 2)

    def test_format_text_includes_company_check_and_correction_state_lines(self):
        root = _scratch_dir()
        batch_dir = self._make_batch(
            root, {"job": "fixture-job", "status": "CORRECTION_LIMIT_EXCEEDED",
                   "correction_rounds": [], "deterministic_runs": [{"status": "CORRECTION_LIMIT_EXCEEDED"}]},
            cover_letter_company="Hiring Company", posting_text=self.DEFAULT_POSTING,
        )
        report = br.build_report(batch_dir, root=root)
        text = br._format_text(report)
        self.assertIn("COMPANY CHECK: FAIL", text)
        self.assertIn("INVARIANT VIOLATION", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
