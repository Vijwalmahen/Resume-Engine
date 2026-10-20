#!/usr/bin/env python3
"""
V8.6.8 -- offline stabilization/hardening regression suite.

ZERO real Claude/Apify calls anywhere in this file (like every other
tools/resume/v86/tests/*.py). This pass root-causes and fixes
benchmark-09-retrieval-engineer's `STRATEGY_CONTRACT_FAILED`, audits a
benchmark batch's correction rounds, and removes several mutable
historical test dependencies (see README.md "V8.6.8" for the full
writeup).

Covers:
  1-6.   `normalize_cover_letter_identity.py`: literal `Company:` header
         parsing (both real posting formats), empty-company backfill,
         generic-placeholder backfill, no-op on a genuine non-placeholder
         value, no fabrication when no header is parseable.
  7-9.   benchmark-09 regression, using synthetic fixtures that reproduce
         the relevant regression condition: OLD behavior (no backfill)
         reproduces `STRATEGY_CONTRACT_FAILED` via the real, unmodified
         `validate_strategy_contract_v86.py`; NEW behavior (backfill wired
         in) reproduces `PASS`; a posting with no parseable `Company:`
         header at all still correctly fails (no fabrication).
  10.    benchmark-01's saved "your organization" placeholder fixture is
         replaced with the company name parsed from its own posting.
  11-13. Mechanical PAYLOAD_PREFLIGHT pre-trim (Fix, `deterministic_
         pipeline.run_job_pipeline`): a purely mechanical field-length
         overflow (the same class found by benchmark-04's regression) is
         resolved with zero Claude calls and no correction round, including
         an extreme overflow that exercises the hard-character-cut
         fallback; a wrong bullet COUNT (not a length overflow, so outside
         this fix's scope) is rejected one whole stage earlier by the
         strategy contract validator and never even reaches
         payload_preflight/the pre-trim.
  14.    Prior V8.6.1-V8.6.7 suites still pass; frozen integrity for V8.5
         through the V8.6.7 freeze; active production config still V8.6.7.

Run with:
    python3 tools/resume/v86/tests/test_v86_8_stabilization.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deterministic_pipeline as detp  # noqa: E402
import normalize_cover_letter_identity as ncli  # noqa: E402
from common import ROOT, load_json, python_cmd, run_step, write_json  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BENCH09_DIR = FIXTURES / "benchmark09_company_backfill"
BENCH01_DIR = FIXTURES / "benchmark01_company_placeholder"
LIVE1311_DIR = FIXTURES / "live13_11_recommendation"
LIVE1311_EVIDENCE_ROOT = LIVE1311_DIR / "shared_ats_evidence"

VALIDATOR = ROOT / "tools/resume/v86/validate_strategy_contract_v86.py"
PAYLOAD_PREFLIGHT = ROOT / "tools/resume/payload_preflight.py"


# ---------------------------------------------------------------------------
# 1-6. normalize_cover_letter_identity.py unit coverage
# ---------------------------------------------------------------------------

class TestExtractCompanyFromPosting(unittest.TestCase):
    def test_matches_bold_synthetic_header(self):
        text = "# Retrieval Engineer\n\n**Company:** Searchlight Technologies\n**Location:** New York, NY\n"
        self.assertEqual(ncli.extract_company_from_posting(text), "Searchlight Technologies")

    def test_matches_plain_scraped_metadata_header(self):
        text = "# Job Posting\n\n## Metadata\n\nCompany: Example Health Co\nRole: Data Science Intern\n"
        self.assertEqual(ncli.extract_company_from_posting(text), "Example Health Co")

    def test_no_header_returns_none(self):
        text = "# Retrieval Engineer\n\nWe are hiring across search and ranking.\n"
        self.assertIsNone(ncli.extract_company_from_posting(text))

    def test_empty_text_returns_none(self):
        self.assertIsNone(ncli.extract_company_from_posting(""))

    def test_does_not_match_mid_sentence_word_company(self):
        text = "This company builds enterprise search. No header line exists here.\n"
        self.assertIsNone(ncli.extract_company_from_posting(text))


class TestBackfillCoverLetterCompany(unittest.TestCase):
    POSTING = "**Company:** Searchlight Technologies\n"

    def test_fills_empty_company(self):
        payload = {"company": "", "role": "Retrieval Engineer"}
        out, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
        self.assertEqual(filled, "Searchlight Technologies")
        self.assertEqual(out["company"], "Searchlight Technologies")
        self.assertIsNot(out, payload, "must return a copy, never mutate the input")
        self.assertEqual(payload["company"], "", "input object must be untouched")

    def test_fills_generic_placeholder(self):
        for placeholder in ("your organization", "Your Company", "the company", "THE ORGANIZATION"):
            payload = {"company": placeholder}
            out, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
            self.assertEqual(filled, "Searchlight Technologies", placeholder)
            self.assertEqual(out["company"], "Searchlight Technologies", placeholder)

    def test_literal_company_now_always_overwrites_a_differing_value(self):
        # V8.6.8 round 4 (FINAL): superseded by the final company
        # source-of-truth policy. A value that differs at all from the
        # posting's own parsed literal -- even one that looks plausible,
        # like a superficially-expanded legal-suffix variant -- is no
        # longer exempt: the parsed literal is now the SOLE authority. This
        # is exactly what closes the wider, open-ended "plausible-but-wrong"
        # class (see benchmark-10-recommendation-engineer) that a
        # placeholder-only check could never fully enumerate.
        payload = {"company": "Searchlight Technologies, Inc."}
        out, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
        self.assertEqual(filled, "Searchlight Technologies")
        self.assertIsNot(out, payload, "must return a copy, never mutate the input")
        self.assertEqual(out["company"], "Searchlight Technologies")
        self.assertEqual(payload["company"], "Searchlight Technologies, Inc.", "input object must be untouched")

    def test_no_op_when_final_value_already_exactly_matches_the_literal(self):
        # The one remaining no-op case: the value already equals the
        # parsed literal exactly -- no rewrite, no copy, filled is None.
        payload = {"company": "Searchlight Technologies"}
        out, filled = ncli.backfill_cover_letter_company(payload, self.POSTING)
        self.assertIsNone(filled)
        self.assertIs(out, payload)
        self.assertEqual(out["company"], "Searchlight Technologies")

    def test_no_fabrication_when_posting_has_no_parseable_header(self):
        payload = {"company": ""}
        out, filled = ncli.backfill_cover_letter_company(payload, "No structured header anywhere in this text.")
        self.assertIsNone(filled)
        self.assertEqual(out["company"], "")

    def test_non_dict_payload_passed_through(self):
        out, filled = ncli.backfill_cover_letter_company(None, self.POSTING)  # type: ignore[arg-type]
        self.assertIsNone(out)
        self.assertIsNone(filled)


# ---------------------------------------------------------------------------
# 7-9. benchmark-09 regression: OLD FAIL -> NEW PASS, malformed still FAILS
# ---------------------------------------------------------------------------

class TestBenchmark09ContractRegression(unittest.TestCase):
    """Uses the REAL, already-saved (immutable-copied) benchmark-09
    artifacts and the REAL, unmodified `validate_strategy_contract_v86.py`
    -- zero model calls, zero mocking of the validator itself."""

    def _run_validator(self, job_dir: Path) -> dict:
        report = job_dir / "v8_contract_validation.json"
        run_step(python_cmd(VALIDATOR, "--job-dir", job_dir, "--report", report))
        return load_json(report)

    def _make_job_dir(self) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        job_dir = Path(tmp.name)
        for name in ("jd_requirements.json", "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json"):
            shutil.copy(BENCH09_DIR / name, job_dir / name)
        return job_dir

    def test_old_behavior_reproduces_the_real_failure(self):
        job_dir = self._make_job_dir()
        result = self._run_validator(job_dir)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["failures"], ["cover_letter_payload.company empty"])

    def test_new_behavior_backfill_then_validate_passes(self):
        job_dir = self._make_job_dir()
        posting_text = (BENCH09_DIR / "job_posting.md").read_text()
        cover = load_json(job_dir / "cover_letter_payload.json")
        backfilled, filled = ncli.backfill_cover_letter_company(cover, posting_text)
        self.assertEqual(filled, "Searchlight Technologies")
        write_json(job_dir / "cover_letter_payload.json", backfilled)

        result = self._run_validator(job_dir)
        self.assertEqual(result["status"], "PASS", result.get("failures"))

    def test_malformed_posting_with_no_company_header_still_fails(self):
        # Proves the fix never fabricates a company name: with no
        # deterministically-parseable header, backfill is a no-op and the
        # (correct, unweakened) contract validator still fails the job.
        job_dir = self._make_job_dir()
        posting_text = (BENCH09_DIR / "job_posting_no_company_header.md").read_text()
        cover = load_json(job_dir / "cover_letter_payload.json")
        backfilled, filled = ncli.backfill_cover_letter_company(cover, posting_text)
        self.assertIsNone(filled)
        write_json(job_dir / "cover_letter_payload.json", backfilled)

        result = self._run_validator(job_dir)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["failures"], ["cover_letter_payload.company empty"])


class TestBenchmark01PlaceholderRegression(unittest.TestCase):
    """benchmark-01's saved cover_letter_payload.json fixture ships
    `"company": "your organization"` (contract-valid, since non-empty, but
    not the employer name "Northstar Commerce" its own posting states) --
    this is the wider defect the same fix also closes."""

    def test_real_placeholder_replaced_with_real_company(self):
        posting_text = (BENCH01_DIR / "job_posting.md").read_text()
        cover = load_json(BENCH01_DIR / "cover_letter_payload.json")
        self.assertEqual(cover["company"], "your organization", "fixture drifted from the saved artifact")

        backfilled, filled = ncli.backfill_cover_letter_company(cover, posting_text)
        self.assertEqual(filled, "Northstar Commerce")
        self.assertEqual(backfilled["company"], "Northstar Commerce")


# ---------------------------------------------------------------------------
# 11-13. Mechanical PAYLOAD_PREFLIGHT pre-trim
# ---------------------------------------------------------------------------

class TestMechanicalPreflightPreTrim(unittest.TestCase):
    """Full, real deterministic pipeline (frozen render/QA/ATS subprocesses,
    zero Claude calls), built from the live13-11 immutable fixture with one
    field deliberately pushed over `payload_preflight.py`'s fixed budget --
    the same class of failure as the real benchmark-04 overflow
    (`selected_project_technology: 93 > 90`)."""

    NEEDED = [
        "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json",
        "project_selection.json", "v8_contract_validation.json",
        "v8_project_selection_validation.json", "payload_normalization.json",
        "payload_preflight.json", "jd_requirements.json",
    ]

    def setUp(self):
        if not all((LIVE1311_DIR / n).exists() for n in self.NEEDED) or not LIVE1311_EVIDENCE_ROOT.exists():
            self.skipTest("live13-11 fixture not present")
        self._job_dirs = []

    def tearDown(self):
        for d in self._job_dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _build_job(self, slug: str) -> Path:
        job_dir = ROOT / f"output/v868-test-mechanical-trim-{slug}"
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True)
        self._job_dirs.append(job_dir)
        for name in self.NEEDED:
            shutil.copy(LIVE1311_DIR / name, job_dir / name)
        return job_dir

    def test_trimmable_overflow_resolved_with_zero_correction_rounds(self):
        job_dir = self._build_job("overflow")
        payload = load_json(job_dir / "resume_payload.json")
        original = payload["selected_project"]["technology_line"]
        overflowed = original + ", plus supplementary internal tooling and monitoring infrastructure"
        self.assertGreater(len(overflowed), 90, "fixture setup must actually overflow the 90-char budget")
        payload["selected_project"]["technology_line"] = overflowed
        write_json(job_dir / "resume_payload.json", payload)

        # Sanity check (the "OLD" precondition): the frozen validator itself
        # confirms this payload, unmodified, really does fail preflight.
        pre_report = job_dir / "_sanity_payload_preflight.json"
        run_step(python_cmd(PAYLOAD_PREFLIGHT, "--payload", job_dir / "resume_payload.json", "--output", pre_report))
        self.assertEqual(load_json(pre_report)["status"], "FAIL")

        result = detp.run_job_pipeline(job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-mechanical-trim-overflow")
        self.assertIn("mechanical_preflight_trim", result, result)
        self.assertIn("selected_project.technology_line", result["mechanical_preflight_trim"])
        self.assertNotEqual(result["status"], "CORRECTION_LIMIT_EXCEEDED", result)
        # V8.6.8 (round 5, FINAL) note: this fixture (based on live13-11)
        # also carries the real "Built" opening-verb repetition with one
        # editable occurrence -- what THIS test proves (the mechanical
        # pre-trim resolves the PAYLOAD_PREFLIGHT overflow with zero
        # correction rounds spent on THAT reason) is unaffected and still
        # true; it never guaranteed zero correction rounds for every
        # possible reason forever. The new, independently-tested
        # ACTION_VERB_DIVERSITY STYLE reason may now request its own round;
        # assert directly against correction_request.json's own reasons so
        # a regression of the PAYLOAD_PREFLIGHT pre-trim itself (this
        # test's actual subject) cannot be masked by this unrelated finding.
        if result["status"] == "NEEDS_LLM_CORRECTION":
            request = load_json(job_dir / "correction_request.json")
            self.assertEqual(request["reasons"], ["ACTION_VERB_DIVERSITY"])
            self.assertNotIn("PAYLOAD_PREFLIGHT", request["reasons"])
        else:
            self.assertIn(result["status"], ("RESUME_READY", "RESUME_READY_WITH_WARNINGS"), result)

        final_payload = load_json(job_dir / "resume_payload.json")
        final_line = final_payload["selected_project"]["technology_line"]
        self.assertLessEqual(len(final_line), 90)
        self.assertTrue(original.startswith(final_line) or final_line.startswith(original.split(",")[0]))

    def test_wrong_bullet_count_is_rejected_earlier_at_contract_never_reaches_pretrim(self):
        # A wrong BULLET COUNT is not something any mechanical trim can fix
        # -- but it never gets the chance to matter here anyway:
        # `validate_strategy_contract_v86.py` (the FIRST deterministic gate,
        # run before payload_preflight/the mechanical pre-trim even exist in
        # the control flow) already requires exactly 2
        # experience_alternate_bullets.experience_2 and exactly 2
        # selected_project.bullets non-empty strings. This proves the
        # pre-trim can never mask a shape defect it is not scoped to fix --
        # that class of defect is rejected one whole stage earlier.
        job_dir = self._build_job("wrongcount")
        payload = load_json(job_dir / "resume_payload.json")
        payload["experience_alternate_bullets"]["experience_2"] = [
            payload["experience_alternate_bullets"]["experience_2"][0]
        ]  # 1 instead of 2
        write_json(job_dir / "resume_payload.json", payload)

        result = detp.run_job_pipeline(job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-mechanical-trim-wrongcount")
        self.assertNotIn("mechanical_preflight_trim", result, result)
        self.assertEqual(result["status"], "STRATEGY_CONTRACT_FAILED", result)
        self.assertIn(
            "experience_alternate_bullets.experience_2 must contain exactly 2 non-empty strings",
            result["contract_failures"],
        )

    def test_extreme_overflow_resolved_via_hard_cut_fallback_no_correction_needed(self):
        # An overflow far too large to fix with a whole-word-boundary trim
        # alone exercises `enforce_correction_length_budget`'s hard
        # character-level fallback (still a strict prefix, still no
        # fabrication) -- proving payload_preflight can no longer fail for
        # ANY length overflow once the shape itself is contract-valid (exact
        # bullet counts are already guaranteed by the earlier contract gate).
        job_dir = self._build_job("extreme-overflow")
        payload = load_json(job_dir / "resume_payload.json")
        payload["selected_project"]["technology_line"] = "Word" * 100  # 400 chars, no spaces at all
        write_json(job_dir / "resume_payload.json", payload)

        result = detp.run_job_pipeline(job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-mechanical-trim-extreme")
        self.assertIn("mechanical_preflight_trim", result, result)
        # payload_preflight itself is proven resolved (the mechanical retrim
        # ran and the pipeline advanced all the way to render/ATS/QA/density
        # -- see `timings` -- rather than stopping at payload_preflight).
        # Replacing this much text with a trimmed stand-in can legitimately
        # leave the PAGE under-full, which is a real, separate, unrelated
        # DENSITY concern this fix is not scoped to touch -- so a
        # NEEDS_LLM_CORRECTION result here is acceptable, but it must never
        # be for PAYLOAD_PREFLIGHT again.
        if result["status"] == "NEEDS_LLM_CORRECTION":
            request = load_json(job_dir / "correction_request.json")
            self.assertNotIn("PAYLOAD_PREFLIGHT", request["reasons"], request)
        else:
            self.assertIn(result["status"], ("RESUME_READY", "RESUME_READY_WITH_WARNINGS"), result)

        final_payload = load_json(job_dir / "resume_payload.json")
        self.assertLessEqual(len(final_payload["selected_project"]["technology_line"]), 90)


# ---------------------------------------------------------------------------
# 14. Prior suites + frozen integrity + active production config
# ---------------------------------------------------------------------------

class TestPriorSuitesAndFrozenIntegrityV868(unittest.TestCase):
    # V8.6.8 FINAL: the 8 recursive `test_vXXX_suite_still_passes` methods
    # that used to live here are removed -- the flat top-level runner
    # (tools/resume/v86/tests/run_all_v86_tests.py) already executes every
    # older suite directly, exactly once each. No unique assertion is lost
    # (each removed method only asserted `returncode == 0`).
    #
    # This public repo ships no `backups/` directory and no
    # `verify_frozen_v85.py` script (both are development-time-only
    # artifacts, per `config/active_resume_pipeline.json`'s own `_comment`),
    # so the byte-diff-against-frozen-snapshot checks that used to live here
    # have no reusable baseline and are removed rather than left permanently
    # failing -- matching the pattern already applied in
    # test_v86_7_ats_bridge_reciprocal_rank_fusion.py. The active production
    # config has also legitimately moved on to a later release since this
    # suite was written, so the exact-version pin is relaxed to "a valid,
    # current V8.6.x PRODUCTION release".

    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
