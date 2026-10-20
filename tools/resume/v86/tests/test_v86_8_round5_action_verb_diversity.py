#!/usr/bin/env python3
"""
V8.6.8 stabilization pass, round 5 (FINAL) -- action-verb diversity.
ZERO real Claude/Apify calls anywhere in this file, like every other
`tools/resume/v86/tests/*.py`.

Adds a small, deterministic STYLE/QUALITY check (see README.md "V8.6.8
stabilization pass, round 5" for the full writeup): the same opening action
verb should normally appear at most twice across a resume's EXPERIENCE +
PROJECT bullets. This is NEVER an ATS/evidence/company/project-selection
rule -- it can only ever add a non-blocking warning or, when at least one
occurrence is truthfully editable, request one bounded, semantic (never
deterministic-synonym-table) correction round.

Covers:
  1. `action_verb_diversity.py`'s pure functions (Task 2/5's full test
     matrix): opening-verb extraction, case-insensitive/punctuation-
     insensitive counting, Experience+Projects section scoping (Skills/
     Education/headings ignored), fixable-vs-unfixable classification.
  2. `semantic_worker.CONTENT_SYSTEM_PROMPT`'s new instruction (Task 1).
  3. `deterministic_pipeline.run_job_pipeline`'s QA integration (Task 3) on
     the checked-in, immutable `live13_11_recommendation` fixture: a
     fixable repeated verb requests exactly one correction round; an
     unfixable one (baked into protected canonical bullets) never does.
  4. The failure policy (Task 4): action-verb diversity, once it is the
     ONLY remaining finding after the 2-round correction budget is
     exhausted, never produces `CORRECTION_LIMIT_EXCEEDED` -- it finishes
     `RESUME_READY_WITH_WARNINGS` with `action_verb_warning` set.
  5. `benchmark_report.py`'s purely-diagnostic `action_verb_repeated_over_
     limit` field.
  6. Active production config is a valid, current V8.6.x PRODUCTION release.

Run with:
    python3 tools/resume/v86/tests/test_v86_8_round5_action_verb_diversity.py
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import action_verb_diversity as avd  # noqa: E402
import benchmark_report as br  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
from common import ROOT, load_json, write_json  # noqa: E402

LIVE1311_DIR = Path(__file__).resolve().parent / "fixtures" / "live13_11_recommendation"
LIVE1311_EVIDENCE_ROOT = LIVE1311_DIR / "shared_ats_evidence"

LIVE1311_NEEDED = [
    "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json",
    "project_selection.json", "v8_contract_validation.json",
    "v8_project_selection_validation.json", "payload_normalization.json",
    "payload_preflight.json", "jd_requirements.json",
]


# ---------------------------------------------------------------------------
# 1a. opening_verb: case-insensitive, punctuation-stripping, exact-word-only
# ---------------------------------------------------------------------------

class TestOpeningVerb(unittest.TestCase):
    def test_simple_bullet(self):
        self.assertEqual(avd.opening_verb("Built a data pipeline for inventory records."), "built")

    def test_case_insensitive(self):
        self.assertEqual(avd.opening_verb("BUILT a pipeline"), "built")
        self.assertEqual(avd.opening_verb("built a pipeline"), "built")
        self.assertEqual(avd.opening_verb("Built a pipeline"), "built")

    def test_bullet_dash_and_whitespace_stripped(self):
        self.assertEqual(avd.opening_verb("  -   Designed an agent"), "designed")

    def test_only_the_first_word_counts_not_words_appearing_later(self):
        # "Built" appears later in the sentence but must never be counted --
        # only the bullet's own first lexical word.
        self.assertEqual(avd.opening_verb("Designed and built a RAG pipeline"), "designed")

    def test_hyphenated_compound_opening_word_kept_as_one_token(self):
        # Hyphenated opening word, kept as one token.
        self.assertEqual(avd.opening_verb("Pre-built the system on a staging database"), "pre-built")

    def test_no_alphabetic_token_returns_empty_string(self):
        self.assertEqual(avd.opening_verb("123% -- 45.6"), "")
        self.assertEqual(avd.opening_verb(""), "")

    def test_alphabetic_token_found_even_after_leading_digits(self):
        # "Extract the first lexical word" -- leading non-letter characters
        # are skipped, not treated as "no word at all".
        self.assertEqual(avd.opening_verb("123% improvement"), "improvement")

    def test_no_fuzzy_or_stemmed_matching(self):
        # "Built" and "Building"/"Build" are different verbs -- never folded
        # together by a stemmer or synonym table.
        self.assertNotEqual(avd.opening_verb("Building a pipeline"), avd.opening_verb("Built a pipeline"))
        self.assertNotEqual(avd.opening_verb("Build a pipeline"), avd.opening_verb("Built a pipeline"))


# ---------------------------------------------------------------------------
# 1b. count_opening_verbs: Task 5's full PASS/WARNING test matrix
# ---------------------------------------------------------------------------

class TestCountOpeningVerbs(unittest.TestCase):
    def test_pass_case_built_exactly_at_limit(self):
        bullets = ["Built X", "Designed Y", "Implemented Z", "Built W", "Developed V"]
        report = avd.count_opening_verbs(bullets)
        self.assertEqual(report["counts"]["built"], 2)
        self.assertEqual(report["repeated_over_limit"], {})
        self.assertEqual(report["limit"], 2)

    def test_warning_case_built_over_limit(self):
        bullets = ["Built X", "Built Y", "Built Z", "Designed W"]
        report = avd.count_opening_verbs(bullets)
        self.assertEqual(report["counts"]["built"], 3)
        self.assertEqual(report["repeated_over_limit"], {"built": 3})

    def test_case_insensitive_counting(self):
        bullets = ["Built X", "BUILT Y", "built Z"]
        report = avd.count_opening_verbs(bullets)
        self.assertEqual(report["counts"], {"built": 3})
        self.assertEqual(report["repeated_over_limit"], {"built": 3})

    def test_punctuation_stripping(self):
        bullets = ["- Built X", "  -  Built Y", "-Built Z"]
        report = avd.count_opening_verbs(bullets)
        self.assertEqual(report["counts"], {"built": 3})

    def test_words_appearing_later_inside_a_bullet_are_ignored(self):
        bullets = ["Designed and built a pipeline", "Reviewed and built a service"]
        report = avd.count_opening_verbs(bullets)
        # "built" appears in both sentences' TAILS but must never be counted.
        self.assertEqual(report["counts"], {"designed": 1, "reviewed": 1})
        self.assertNotIn("built", report["counts"])

    def test_multiple_repeated_verbs_reported_correctly(self):
        bullets = ["Built A", "Built B", "Built C", "Designed D", "Designed E", "Designed F", "Implemented G"]
        report = avd.count_opening_verbs(bullets)
        self.assertEqual(report["repeated_over_limit"], {"built": 3, "designed": 3})
        self.assertEqual(report["counts"]["implemented"], 1)

    def test_bullets_by_verb_present_only_for_repeated_over_limit(self):
        bullets = ["Built A", "Built B", "Built C", "Designed D"]
        report = avd.count_opening_verbs(bullets)
        self.assertEqual(set(report["bullets_by_verb"]), {"built"})
        self.assertEqual(report["bullets_by_verb"]["built"], ["Built A", "Built B", "Built C"])

    def test_custom_limit(self):
        bullets = ["Built A", "Built B", "Built C"]
        report = avd.count_opening_verbs(bullets, limit=3)
        self.assertEqual(report["repeated_over_limit"], {})
        report = avd.count_opening_verbs(bullets, limit=1)
        self.assertEqual(report["repeated_over_limit"], {"built": 3})

    def test_empty_bullet_list(self):
        report = avd.count_opening_verbs([])
        self.assertEqual(report["counts"], {})
        self.assertEqual(report["repeated_over_limit"], {})
        self.assertEqual(report["bullet_count"], 0)


# ---------------------------------------------------------------------------
# 1c. extract_experience_and_project_bullets: section scoping
# ---------------------------------------------------------------------------

SYNTHETIC_RESUME_MD = """Candidate Example
contact line

EDUCATION

Some University
Coursework: Built things, Designed systems, Implemented algorithms

PROFESSIONAL EXPERIENCE

Some Title | SomeCo | Remote\tJan 2024 - Present
- Built a pipeline for something.
- Designed a system for something else.

PROJECTS

Some Project — Python, FastAPI
- Implemented a retrieval service.
- Built a second thing here.

SKILLS

Languages: Built, Designed, Implemented, Python
Frameworks: Built again in a skill line, never a bullet
"""


class TestExtractExperienceAndProjectBullets(unittest.TestCase):
    def test_skills_and_education_ignored_experience_and_projects_combined(self):
        bullets = avd.extract_experience_and_project_bullets(SYNTHETIC_RESUME_MD)
        self.assertEqual(
            bullets,
            [
                "Built a pipeline for something.",
                "Designed a system for something else.",
                "Implemented a retrieval service.",
                "Built a second thing here.",
            ],
        )

    def test_education_coursework_line_never_counted_even_though_it_contains_verb_words(self):
        bullets = avd.extract_experience_and_project_bullets(SYNTHETIC_RESUME_MD)
        self.assertNotIn("Coursework: Built things, Designed systems, Implemented algorithms", bullets)

    def test_skills_lines_never_counted_even_though_they_contain_verb_words(self):
        bullets = avd.extract_experience_and_project_bullets(SYNTHETIC_RESUME_MD)
        joined = " ".join(bullets)
        self.assertNotIn("Languages", joined)
        self.assertNotIn("never a bullet", joined)

    def test_heading_and_title_lines_never_counted_as_bullets(self):
        bullets = avd.extract_experience_and_project_bullets(SYNTHETIC_RESUME_MD)
        for b in bullets:
            self.assertNotIn("SomeCo", b)
            self.assertNotIn("Some Project", b)

    def test_real_fixture_resume_draft_scopes_correctly(self):
        if not (LIVE1311_DIR / "resume_draft.md").exists():
            self.skipTest("live13-11 fixture resume_draft.md not present")
        text = (LIVE1311_DIR / "resume_draft.md").read_text(encoding="utf-8")
        bullets = avd.extract_experience_and_project_bullets(text)
        # 4 Experience bullets (experience_1) + 2 (experience_2) + 3+3+2
        # (project_1/project_2/selected_project) = 14
        self.assertEqual(len(bullets), 14)
        joined = " ".join(bullets)
        self.assertNotIn("SKILLS", joined)
        self.assertNotIn("Languages: Python", joined)


# ---------------------------------------------------------------------------
# 1d. analyze_action_verbs / analyze_action_verbs_from_file
# ---------------------------------------------------------------------------

class TestAnalyzeActionVerbsFromFile(unittest.TestCase):
    def test_analyze_action_verbs_matches_count_opening_verbs_on_extracted_bullets(self):
        direct = avd.count_opening_verbs(avd.extract_experience_and_project_bullets(SYNTHETIC_RESUME_MD))
        via_helper = avd.analyze_action_verbs(SYNTHETIC_RESUME_MD)
        self.assertEqual(direct, via_helper)

    def test_from_file_reads_real_fixture(self):
        path = LIVE1311_DIR / "resume_draft.md"
        if not path.exists():
            self.skipTest("live13-11 fixture resume_draft.md not present")
        report = avd.analyze_action_verbs_from_file(path)
        self.assertEqual(report["repeated_over_limit"], {"designed": 3, "built": 3})

    def test_missing_file_analyzes_as_zero_bullets_never_raises(self):
        report = avd.analyze_action_verbs_from_file(ROOT / "does/not/exist/resume_draft.md")
        self.assertEqual(report["bullet_count"], 0)
        self.assertEqual(report["counts"], {})


# ---------------------------------------------------------------------------
# 1e. editable_bullet_texts / classify_repeated_verbs
# ---------------------------------------------------------------------------

class TestEditableBulletTextsAndClassification(unittest.TestCase):
    def test_editable_bullet_texts_is_exactly_alternates_and_selected_project(self):
        payload = {
            "experience_alternate_bullets": {"experience_2": ["Alternate bullet one", "Alternate bullet two"]},
            "selected_project": {"bullets": ["Selected project bullet"]},
        }
        self.assertEqual(
            avd.editable_bullet_texts(payload),
            {"Alternate bullet one", "Alternate bullet two", "Selected project bullet"},
        )

    def test_editable_bullet_texts_handles_missing_fields(self):
        self.assertEqual(avd.editable_bullet_texts({}), set())
        self.assertEqual(avd.editable_bullet_texts({"selected_project": {}}), set())

    def test_classify_fixable_when_any_occurrence_editable(self):
        verb_report = avd.count_opening_verbs(["Built A (canonical)", "Built B (canonical)", "Built C (editable)"])
        classification = avd.classify_repeated_verbs(verb_report, editable_bullets={"Built C (editable)"})
        self.assertIn("built", classification["fixable"])
        self.assertNotIn("built", classification["unfixable"])
        entry = classification["fixable"]["built"]
        self.assertEqual(entry["total_count"], 3)
        self.assertEqual(entry["fixed_canonical_count"], 2)
        self.assertEqual(entry["editable_bullets"], ["Built C (editable)"])

    def test_classify_unfixable_when_zero_occurrences_editable(self):
        verb_report = avd.count_opening_verbs(["Designed A", "Designed B", "Designed C"])
        classification = avd.classify_repeated_verbs(verb_report, editable_bullets=set())
        self.assertIn("designed", classification["unfixable"])
        self.assertNotIn("designed", classification["fixable"])
        self.assertEqual(classification["unfixable"]["designed"]["fixed_canonical_count"], 3)
        self.assertEqual(classification["unfixable"]["designed"]["editable_bullets"], [])

    def test_real_fixture_matches_manual_expectation(self):
        if not (LIVE1311_DIR / "resume_draft.md").exists() or not (LIVE1311_DIR / "resume_payload.json").exists():
            self.skipTest("live13-11 fixture artifacts not present")
        text = (LIVE1311_DIR / "resume_draft.md").read_text(encoding="utf-8")
        payload = load_json(LIVE1311_DIR / "resume_payload.json")
        report = avd.analyze_action_verbs(text)
        classification = avd.classify_repeated_verbs(report, avd.editable_bullet_texts(payload))
        # "Designed" is 100% baked into the 3 protected canonical bullets
        # (experience_1/project_1/project_2) -- never editable, for ANY
        # candidate, ever.
        self.assertIn("designed", classification["unfixable"])
        # "Built" has exactly one occurrence in the bound selected
        # project's own (editable) bullet.
        self.assertIn("built", classification["fixable"])
        self.assertEqual(classification["fixable"]["built"]["fixed_canonical_count"], 2)


# ---------------------------------------------------------------------------
# 2. semantic_worker.CONTENT_SYSTEM_PROMPT's new instruction (Task 1)
# ---------------------------------------------------------------------------

class TestSemanticWorkerPromptAddition(unittest.TestCase):
    def test_content_prompt_mentions_action_verb_diversity(self):
        import semantic_worker as sw
        prompt = sw.CONTENT_SYSTEM_PROMPT
        self.assertIn("action verb", prompt.lower())
        self.assertIn("more than twice", prompt.lower())

    def test_content_prompt_prioritizes_truthfulness_over_diversity(self):
        import semantic_worker as sw
        prompt = sw.CONTENT_SYSTEM_PROMPT.lower()
        self.assertIn("factual meaning", prompt)
        self.assertIn("truthfulness", prompt)

    def test_requirements_prompt_untouched_by_this_addition(self):
        import semantic_worker as sw
        self.assertNotIn("action verb", sw.REQUIREMENTS_SYSTEM_PROMPT.lower())

    def test_call1_call2_correction_effort_unchanged(self):
        import correction_worker as cw
        import inspect
        import semantic_worker as sw
        self.assertEqual(inspect.signature(sw.run_requirements_call).parameters["effort"].default, "low")
        self.assertEqual(inspect.signature(sw.run_content_call).parameters["effort"].default, "medium")
        self.assertEqual(inspect.signature(cw.run_correction_call).parameters["effort"].default, "low")


# ---------------------------------------------------------------------------
# 3. run_job_pipeline QA integration on the real live13-11 fixture (Task 3)
# ---------------------------------------------------------------------------

class TestRunJobPipelineActionVerbIntegration(unittest.TestCase):
    def setUp(self):
        if not all((LIVE1311_DIR / n).exists() for n in LIVE1311_NEEDED) or not LIVE1311_EVIDENCE_ROOT.exists():
            self.skipTest("real live13-11 artifacts not present")
        self.job_dir = ROOT / "output/v868-test-round5-live1311"
        shutil.rmtree(self.job_dir, ignore_errors=True)
        self.job_dir.mkdir(parents=True)
        for name in LIVE1311_NEEDED:
            shutil.copy(LIVE1311_DIR / name, self.job_dir / name)

    def tearDown(self):
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def test_fixable_verb_requests_exactly_one_correction_round(self):
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-round5-live1311")
        self.assertEqual(result["status"], "NEEDS_LLM_CORRECTION", result)
        request = load_json(self.job_dir / "correction_request.json")
        self.assertEqual(request["reasons"], ["ACTION_VERB_DIVERSITY"])
        self.assertEqual(request["correction_round"], 1)
        repeated = request["details"]["action_verb_diversity"]["repeated_verbs"]
        self.assertEqual(set(repeated), {"built"}, "only the FIXABLE verb may be requested from correction")
        self.assertEqual(repeated["built"]["fixed_canonical_count"], 2)
        self.assertEqual(len(repeated["built"]["editable_bullets"]), 1)

    def test_unfixable_verb_never_appears_in_the_correction_request(self):
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-round5-live1311")
        request = load_json(self.job_dir / "correction_request.json")
        self.assertNotIn("designed", request["details"]["action_verb_diversity"]["repeated_verbs"])

    def test_result_reports_full_counts_and_unfixable_diagnostic(self):
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-round5-live1311")
        self.assertEqual(result["action_verb_diversity"]["repeated_over_limit"], {"designed": 3, "built": 3})
        self.assertEqual(result["action_verb_unfixable"], {"designed": 3})

    def test_never_claims_ats_or_evidence_or_company_or_project_changed(self):
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-round5-live1311")
        # This is a STYLE-only finding -- it must never alter/claim anything
        # about ATS support or evidence (this fixture's real, unrelated
        # optional "Sentence Transformers" ATS warning is proven still
        # reached, unaffected, once the correction round completes --  see
        # TestFailurePolicyNeverHardFails, which observes the same fixture
        # at its post-correction/exhausted-budget point where the success
        # path -- and therefore `ats_warnings` -- is actually reached).
        self.assertNotIn("ats_unplaceable_required", result)
        request = load_json(self.job_dir / "correction_request.json")
        self.assertNotIn("ats", request["details"], "a STYLE-only round must never bundle an ATS finding that was never triggered")
        self.assertNotIn("ATS", request["reasons"])


# ---------------------------------------------------------------------------
# 4. Failure policy: action-verb diversity alone never CORRECTION_LIMIT_EXCEEDED
# ---------------------------------------------------------------------------

class TestFailurePolicyNeverHardFails(unittest.TestCase):
    def setUp(self):
        if not all((LIVE1311_DIR / n).exists() for n in LIVE1311_NEEDED) or not LIVE1311_EVIDENCE_ROOT.exists():
            self.skipTest("real live13-11 artifacts not present")
        self.job_dir = ROOT / "output/v868-test-round5-exhausted"
        shutil.rmtree(self.job_dir, ignore_errors=True)
        self.job_dir.mkdir(parents=True)
        for name in LIVE1311_NEEDED:
            shutil.copy(LIVE1311_DIR / name, self.job_dir / name)
        # Simulate "the 2-round correction budget is already exhausted" --
        # exactly the on-disk shape a real job would have after 2 real
        # correction rounds (see test_v86_8_round2_hardening.py's own
        # `TestCorrectionStateResetInvariant` for the same technique).
        write_json(self.job_dir / "v8_correction_status.json", {"correction_round": 2, "status": "CORRECTED"})

    def tearDown(self):
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def test_only_remaining_finding_is_action_verb_diversity_never_exceeds_limit(self):
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-round5-exhausted")
        self.assertEqual(result["status"], "RESUME_READY_WITH_WARNINGS", result)
        self.assertNotEqual(result["status"], "CORRECTION_LIMIT_EXCEEDED")
        self.assertIsNone(result.get("correction_request"))
        self.assertEqual(result["action_verb_warning"], {"designed": 3, "built": 3})
        # The pre-existing optional ATS warning must still be surfaced
        # unchanged -- this policy affects ONLY action-verb diversity.
        self.assertEqual(result.get("ats_warnings"), ["Sentence Transformers"])

    def test_a_genuinely_different_remaining_reason_still_hard_fails_as_before(self):
        # A synthetic MULTIPAGE finding alongside the exhausted budget must
        # still hard-fail exactly as it always did -- the new policy is
        # scoped to action-verb diversity ONLY, never a general softening
        # of CORRECTION_ROUND_LIMIT.
        payload = load_json(self.job_dir / "resume_payload.json")
        payload["experience_alternate_bullets"]["experience_2"] = (
            payload["experience_alternate_bullets"]["experience_2"]
            + ["Extra bullet to force a synthetic multipage-shaped payload."] * 20
        )
        write_json(self.job_dir / "resume_payload.json", payload)
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "v868-test-round5-exhausted")
        self.assertIn(result["status"], ("CORRECTION_LIMIT_EXCEEDED", "PAYLOAD_PREFLIGHT_FAILED", "STRATEGY_CONTRACT_FAILED"))
        self.assertNotEqual(result["status"], "RESUME_READY_WITH_WARNINGS")
        self.assertNotEqual(result["status"], "RESUME_READY")


# ---------------------------------------------------------------------------
# 5. benchmark_report.py's diagnostic action_verb_repeated_over_limit field
# ---------------------------------------------------------------------------

class TestBenchmarkReportActionVerbField(unittest.TestCase):
    def setUp(self):
        # job_artifact_summary always reads from root/output/<job_key>/ --
        # a small, self-contained scratch copy under the real ROOT/output/
        # (same convention as every other job_dir in this file) lets this
        # test point at the real fixture's resume_draft.md without
        # reshaping job_artifact_summary's own path contract.
        self.job_key = "v868-test-round5-benchmark-report"
        self.job_dir = ROOT / "output" / self.job_key
        shutil.rmtree(self.job_dir, ignore_errors=True)
        self.job_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def test_job_artifact_summary_reports_repeated_verbs_for_real_fixture(self):
        if not (LIVE1311_DIR / "resume_draft.md").exists():
            self.skipTest("real live13-11 resume_draft.md not present")
        shutil.copy(LIVE1311_DIR / "resume_draft.md", self.job_dir / "resume_draft.md")
        summary = br.job_artifact_summary(self.job_key, root=ROOT)
        self.assertEqual(summary["action_verb_repeated_over_limit"], {"designed": 3, "built": 3})

    def test_missing_resume_draft_reports_empty_dict_never_raises(self):
        summary = br.job_artifact_summary(self.job_key, root=ROOT)
        self.assertEqual(summary["action_verb_repeated_over_limit"], {})

    def test_format_text_includes_action_verb_repetition_line(self):
        text = br._format_text({
            "batch_id": "x", "jobs_discovered": 1, "jobs_succeeded": 1, "jobs_failed": 0, "jobs_corrected": 0,
            "quality_summary": {"READY": 1, "READY_WITH_WARNINGS": 0, "OTHER_FINAL_STATUS": 0},
            "total_claude_requests": 0, "total_claude_cost_usd": 0, "total_output_tokens": 0, "wall_duration_ms": 0,
            "peak_semantic_concurrency": 0, "peak_deterministic_concurrency": 0, "peak_finalization_concurrency": 0,
            "contamination": {"status": "PASS", "hits": []},
            "active_config": {"active_version": "V8.6.7", "status": "PRODUCTION", "batch_runner": "x", "max_workers": 10},
            "jobs": [{
                "job": "fake-job", "final_status": "RESUME_READY_WITH_WARNINGS", "failure_reason": None,
                "correction_rounds_reported_by_batch": 0, "correction_requested_rounds": 0,
                "correction_attempted_rounds": 0, "correction_succeeded_rounds": 0, "correction_exhausted": False,
                "correction_invariant_violation": False, "ats_warnings": [], "ats_unplaceable_required": [],
                "mechanical_preflight_trim": [], "cover_letter_company_backfilled": None,
                "strategy_status": "STRATEGY_READY", "contract_status": "PASS", "project_selection_status": "PASS",
                "payload_preflight_status": "PASS", "resume_qa_status": "PASS", "ats_gate_pass": True,
                "ats_supported_but_missing_required": [], "ats_optional_supported_but_missing": [],
                "selected_project": "X", "correction_rounds_on_disk": 0, "correction_reasons_by_round": {},
                "artifacts_present": {}, "zero_byte_artifacts": [], "cover_letter_company": "X", "company_check": "PASS",
                "action_verb_repeated_over_limit": {"designed": 3, "built": 3},
            }],
        })
        self.assertIn("ACTION VERB REPETITION", text)
        self.assertIn("'designed': 3", text)


# ---------------------------------------------------------------------------
# 6. Active production config
# ---------------------------------------------------------------------------
#
# This public repo ships no `backups/` directory and no
# `verify_frozen_v85.py` script (development-time-only artifacts, per
# `config/active_resume_pipeline.json`'s own `_comment`), so the byte-diff-
# against-frozen-snapshot checks that used to live in this suite have no
# reusable baseline here and are removed rather than left permanently
# failing -- matching the pattern already applied in
# test_v86_7_ats_bridge_reciprocal_rank_fusion.py.

class TestActiveProductionConfig(unittest.TestCase):
    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
