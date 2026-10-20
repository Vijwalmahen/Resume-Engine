#!/usr/bin/env python3
"""
V8.6.1 -- offline reliability-patch test suite.

ALL tests here are pure-Python / deterministic-script invocations. NOTHING
in this file ever shells out to the real `claude` CLI, calls Apify, or
spends real money -- there is no LlmCaller of any kind involved. The
regression fixtures under `tools/resume/v86/tests/fixtures/` are synthetic
fixtures that reproduce the shape of real regressions found during
development (dedupe of near-identical ATS concepts, correction-round
budget overflow, and ATS semantic-evidence bridging), not one-off
synthetic approximations of behavior that was never actually observed.

Run with:
    python3 tools/resume/v86/tests/test_v86_reliability_patch.py -v
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ats_preflight_v86  # noqa: E402
import correction_worker  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import normalize_jd_concepts as njc  # noqa: E402
from common import ROOT, load_json, python_cmd, run_step, write_json  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class CleaningTestCase(unittest.TestCase):
    def setUp(self):
        self._cleanup_dirs = []

    def track(self, path: Path):
        self._cleanup_dirs.append(Path(path))
        return path

    def tearDown(self):
        for d in self._cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# FIX 1 -- JD concept de-duplication
# ---------------------------------------------------------------------------

class TestConceptDedupe(unittest.TestCase):
    def test_exact_duplicate_terms_merge(self):
        concepts = [
            {"term": "OpenCV", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "critical",
             "aliases": ["video stream processing with OpenCV"]},
            {"term": "OpenCV", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "high"},
        ]
        merged, notes = njc.normalize_concepts(concepts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(notes), 1)
        c = merged[0]
        self.assertEqual(c["term"], "OpenCV")
        self.assertTrue(c["required"])  # required=true if ANY duplicate is required
        self.assertEqual(c["priority"], "critical")  # strongest priority wins
        self.assertEqual(c["category"], "REQUIRED_HARD_SKILL")  # best category wins
        self.assertEqual(c["aliases"], ["video stream processing with OpenCV"])

    def test_case_insensitive_duplicate_terms_merge(self):
        concepts = [
            {"term": "python", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "low"},
            {"term": "Python", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
        ]
        merged, notes = njc.normalize_concepts(concepts)
        self.assertEqual(len(merged), 1)
        c = merged[0]
        self.assertEqual(c["term"], "Python")  # prefers the properly-cased form
        self.assertTrue(c["required"])
        self.assertEqual(c["priority"], "high")
        self.assertEqual(c["category"], "REQUIRED_HARD_SKILL")

    def test_required_plus_preferred_duplicate(self):
        concepts = [
            {"term": "SQL", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "medium"},
            {"term": "SQL", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "medium"},
        ]
        merged, _ = njc.normalize_concepts(concepts)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["required"])

    def test_different_priority_duplicate_keeps_strongest(self):
        concepts = [
            {"term": "Docker", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "low"},
            {"term": "Docker", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "critical"},
            {"term": "Docker", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "medium"},
        ]
        merged, notes = njc.normalize_concepts(concepts)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["priority"], "critical")
        self.assertEqual(notes[0]["merged_count"], 3)

    def test_genuinely_distinct_concepts_are_never_merged(self):
        concepts = [
            {"term": "OpenCV", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
            {"term": "OpenCV2", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "low"},
        ]
        merged, notes = njc.normalize_concepts(concepts)
        self.assertEqual(len(merged), 2)
        self.assertEqual(notes, [])

    def test_malformed_entries_pass_through_untouched_for_contract_safety_net(self):
        concepts = [
            {"term": "OpenCV", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
            {"term": "OpenCV"},  # missing required keys -- must not be silently merged/hidden
        ]
        merged, notes = njc.normalize_concepts(concepts)
        self.assertEqual(len(merged), 2)  # not merged: still 2 -- the malformed one is untouched
        self.assertEqual(notes, [])


# ---------------------------------------------------------------------------
# Regression 1 -- scale10-04 computer-vision (real duplicate OpenCV)
# ---------------------------------------------------------------------------

class TestRegressionComputerVisionDuplicateOpenCV(CleaningTestCase):
    def test_no_longer_strategy_contract_failed(self):
        job_dir = self.track(ROOT / "output" / "v86-test-fix1-cv-regression")
        job_dir.mkdir(parents=True, exist_ok=True)
        fixture = FIXTURES / "scale10_04_duplicate_opencv"
        for name in ("jd_requirements.json", "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json"):
            shutil.copy(fixture / name, job_dir / name)

        # Confirm the fixture really does reproduce the original failure
        # mode (duplicate "OpenCV") before any normalization runs.
        raw = load_json(job_dir / "jd_requirements.json")
        opencv_terms = [c for c in raw["concepts"] if c["term"].casefold() == "opencv"]
        self.assertEqual(len(opencv_terms), 2, "fixture must contain the real duplicate-OpenCV JD")

        report = job_dir / "v8_contract_validation.json"
        run_step(python_cmd(
            ROOT / "tools/resume/v86/validate_strategy_contract_v86.py",
            "--job-dir", job_dir, "--report", report,
        ))
        # Before Fix 1, this was FAIL with "duplicate concept term: OpenCV".
        # validate_strategy_contract_v86.py itself does not normalize --
        # normalization happens upstream (run_batch.py / run_job_pipeline).
        pre_fix_result = load_json(report)
        self.assertIn("FAIL", pre_fix_result["status"])
        self.assertTrue(any("duplicate concept term" in f for f in pre_fix_result["failures"]))

        # Now apply Fix 1 exactly where the pipeline applies it: after
        # semantic call 1, before contract validation.
        normalized, notes = njc.normalize_jd_requirements(raw)
        self.assertTrue(notes, "expected a merge to have happened")
        write_json(job_dir / "jd_requirements.json", normalized)

        opencv_terms_after = [c for c in normalized["concepts"] if c["term"].casefold() == "opencv"]
        self.assertEqual(len(opencv_terms_after), 1)
        self.assertTrue(opencv_terms_after[0]["required"])  # the required=true duplicate must win

        run_step(python_cmd(
            ROOT / "tools/resume/v86/validate_strategy_contract_v86.py",
            "--job-dir", job_dir, "--report", report,
        ))
        result = load_json(report)
        self.assertEqual(result["status"], "PASS", result.get("failures"))

    def test_defensive_normalize_call_site_runs_before_contract_validation(self):
        # Drives deterministic_pipeline.run_job_pipeline's OWN defensive
        # normalize-then-validate sequence (not run_batch.py's call site),
        # stopping deliberately before the Word-dependent render step (see
        # test_v86_offline.py's documented policy: this offline suite never
        # exercises real docx2pdf/Word rendering). This proves Fix 1 is
        # applied at BOTH call sites, not just the primary one already
        # covered by test_no_longer_strategy_contract_failed above.
        job_dir = self.track(ROOT / "output" / "v86-test-fix1-cv-full-pipeline")
        job_dir.mkdir(parents=True, exist_ok=True)
        fixture = FIXTURES / "scale10_04_duplicate_opencv"
        for name in ("jd_requirements.json", "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json"):
            shutil.copy(fixture / name, job_dir / name)

        # Reproduce exactly the prefix of run_job_pipeline up to (but not
        # including) rendering.
        requirements = job_dir / "jd_requirements.json"
        jd_data = load_json(requirements)
        normalized_jd, notes = njc.normalize_jd_requirements(jd_data)
        self.assertTrue(notes)
        write_json(requirements, normalized_jd)

        contract_report = job_dir / "v8_contract_validation.json"
        run_step(python_cmd(
            ROOT / "tools/resume/v86/validate_strategy_contract_v86.py",
            "--job-dir", job_dir, "--report", contract_report,
        ))
        contract = load_json(contract_report)
        self.assertEqual(contract["status"], "PASS", contract.get("failures"))

        selector = detp.ensure_project_selection(job_dir, requirements)
        self.assertTrue(selector["ok"], selector)
        self.assertEqual(selector["data"].get("status"), "PASS")


# ---------------------------------------------------------------------------
# FIX 2 / FIX 3 -- ATS semantic-evidence bridges + optional-concept policy
# ---------------------------------------------------------------------------

class TestAtsBridgesAndOptionalPolicy(CleaningTestCase):
    def _run_ats(self, requirements_path, resume_path, out_path, use_bridges=True):
        aliases = ROOT / "config/ats_aliases.json"
        extra = ROOT / "config/v86/ats_concept_bridges.json"
        report = ats_preflight_v86.evaluate(
            json.loads(Path(requirements_path).read_text()),
            Path(resume_path).read_text(encoding="utf-8"),
            FIXTURES / "shared_ats_evidence",
            ats_preflight_v86.load_and_merge_aliases(aliases, extra if use_bridges else None),
        )
        write_json(out_path, report)
        return report

    def test_agentic_and_llm_eval_bridges_flip_generative_ai_job(self):
        fixture = FIXTURES / "scale10_02_budget_overflow"
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "ats.json"
            without_bridges = self._run_ats(fixture / "jd_requirements.json", fixture / "resume_draft.md", out, use_bridges=False)
            self.assertFalse(without_bridges["gate_pass"])
            self.assertIn("Agentic workflows", without_bridges["missing_supported_required_terms"])

            with_bridges = self._run_ats(fixture / "jd_requirements.json", fixture / "resume_draft.md", out, use_bridges=True)
        self.assertTrue(with_bridges["gate_pass"], with_bridges)
        self.assertNotIn("Agentic workflows", with_bridges["missing_supported_required_terms"])
        self.assertNotIn("LLM response quality/reliability evaluation", with_bridges["missing_supported_required_terms"])

    def test_llm_evaluation_bridge_recognizes_protected_evidence_without_rewriting(self):
        fixture = FIXTURES / "scale10_03_llm_eval_bridge"
        before_text = (fixture / "resume_draft.md").read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "ats.json"
            without_bridges = self._run_ats(fixture / "jd_requirements.json", fixture / "resume_draft.md", out, use_bridges=False)
            self.assertIn("LLM evaluation", without_bridges["supported_but_missing"])

            with_bridges = self._run_ats(fixture / "jd_requirements.json", fixture / "resume_draft.md", out, use_bridges=True)
        self.assertNotIn("LLM evaluation", with_bridges["supported_but_missing"])
        self.assertTrue(with_bridges["gate_pass"], with_bridges)

        # The protected canonical experience/project bullets must be
        # UNCHANGED -- recognition happened purely through deterministic
        # bridging, never by rewriting canonical content.
        after_text = (fixture / "resume_draft.md").read_text(encoding="utf-8")
        self.assertEqual(before_text, after_text)
        self.assertIn("LLM-as-judge verification step", after_text)
        self.assertIn("LLM-judged faithfulness/correctness", after_text)

    def test_optional_supported_but_missing_never_blocks_gate(self):
        requirements = {
            "job_key": "fix3-test",
            "concepts": [
                {"term": "Python", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
                {"term": "Kubernetes", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "high"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            evidence_root = Path(td) / "evidence"
            evidence_root.mkdir()
            (evidence_root / "evidence.md").write_text("Extensive Python experience.\nKubernetes cluster operations.\n")
            resume_text = "Skills: Python. No mention of the K-word here."
            report = ats_preflight_v86.evaluate(requirements, resume_text, evidence_root, {})

        self.assertIn("Kubernetes", report["optional_supported_but_missing"])
        self.assertNotIn("Kubernetes", report["supported_but_missing_required"])
        self.assertEqual(report["missing_supported_required_terms"], [])
        self.assertTrue(report["gate_pass"], report)  # optional gap alone must not fail the gate

    def test_required_supported_but_missing_still_blocks_gate(self):
        requirements = {
            "job_key": "fix3-required-test",
            "concepts": [
                {"term": "Kubernetes", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            evidence_root = Path(td) / "evidence"
            evidence_root.mkdir()
            (evidence_root / "evidence.md").write_text("Kubernetes cluster operations.\n")
            resume_text = "No mention of the K-word here."
            report = ats_preflight_v86.evaluate(requirements, resume_text, evidence_root, {})

        self.assertFalse(report["gate_pass"])
        self.assertIn("Kubernetes", report["missing_supported_required_terms"])


# ---------------------------------------------------------------------------
# FIX 4 -- correction output budget (same-round mechanical trimming)
# ---------------------------------------------------------------------------

class TestCorrectionLengthBudget(unittest.TestCase):
    def _payload_with_bullet(self, bullet_2: str) -> dict:
        return {
            "experience_bullet_orders": {"experience_1": [0, 1, 2, 3]},
            "experience_alternate_bullets": {"experience_2": ["short one", "short two"]},
            "project_bullet_orders": {"project_1": [0, 1, 2], "project_2": [0, 1, 2]},
            "selected_project": {
                "owner_id": "project_selectable_2",
                "title": "Project Horizon",
                "technology_line": "Python, pandas, Prophet, Docker",
                "bullets": ["Built a time-series demand-forecasting model for a simulated regional logistics network.", bullet_2],
            },
        }

    def test_overlong_selected_project_bullet_is_trimmed_same_round(self):
        # A bullet just over the correction-round length budget (170
        # chars) must be trimmed mechanically, without an extra LLM call.
        overlong = (
            "Built the region-selection workflow, integrated the batch-scoring "
            "APIs, and supported containerized delivery via Docker, weekly "
            "scheduling, and additional monitoring "
            "instrumentation across the full deployment lifecycle end to end."
        )
        self.assertGreater(len(overlong), 170)

        payload, trimmed_fields = detp.enforce_correction_length_budget(self._payload_with_bullet(overlong))
        self.assertIn("selected_project.bullets[1]", trimmed_fields)

        trimmed = payload["selected_project"]["bullets"][1]
        self.assertLessEqual(len(trimmed.replace("**", "").strip()), 170)
        # No fabrication: the trimmed text must be a strict (word-boundary)
        # prefix of the original claim, never new/altered content.
        self.assertTrue(overlong.startswith(trimmed.rstrip(" ,;:-")))

        # And it now mechanically passes the frozen payload_preflight gate.
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "resume_payload.json"
            write_json(p, payload)
            out = Path(td) / "out.json"
            run_step(python_cmd(ROOT / "tools/resume/payload_preflight.py", "--payload", p, "--output", out))
            self.assertEqual(load_json(out)["status"], "PASS")

    def test_in_budget_fields_are_never_touched(self):
        payload = self._payload_with_bullet("A short, well within budget bullet.")
        result, trimmed_fields = detp.enforce_correction_length_budget(payload)
        self.assertEqual(trimmed_fields, [])
        self.assertEqual(result, payload)

    def test_does_not_consume_a_new_semantic_correction_round(self):
        """The mechanical trim happens in Python, with zero LLM calls, in
        the SAME round as the correction that produced the overflow --
        never as a reason to loop back for a second semantic correction
        call."""
        import unittest.mock as mock

        overlong = "x" * 250
        payload = self._payload_with_bullet(overlong)
        with mock.patch("llm_invoke.invoke") as mocked_invoke:
            trimmed_payload, trimmed_fields = detp.enforce_correction_length_budget(payload)
            mocked_invoke.assert_not_called()
        self.assertIn("selected_project.bullets[1]", trimmed_fields)


# ---------------------------------------------------------------------------
# FIX 5 -- final validation after the last permitted correction
# ---------------------------------------------------------------------------

class TestFinalValidationAfterCorrectionLimit(CleaningTestCase):
    def test_resume_ready_with_warnings_is_a_success_status(self):
        self.assertIn("RESUME_READY_WITH_WARNINGS", detp.SUCCESS_STATUSES)
        self.assertIn("RESUME_READY", detp.SUCCESS_STATUSES)

    def test_optional_ats_warning_yields_resume_ready_with_warnings_not_failure(self):
        # A deterministic report where every real gate passes but one
        # OPTIONAL concept remains supported-but-missing must be reported
        # as a non-terminal warning, not a failure -- Fix 3/Fix 5.
        requirements = {
            "job_key": "fix5-test",
            "concepts": [{"term": "Kubernetes", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "high"}],
        }
        with tempfile.TemporaryDirectory() as td:
            evidence_root = Path(td) / "evidence"
            evidence_root.mkdir()
            (evidence_root / "evidence.md").write_text("Kubernetes cluster operations.\n")
            report = ats_preflight_v86.evaluate(requirements, "no mention here", evidence_root, {})
        self.assertTrue(report["gate_pass"])
        self.assertEqual(report["optional_supported_but_missing"], ["Kubernetes"])

    def test_correction_limit_exceeded_only_when_a_real_blocker_survives_the_final_run(self):
        # previous_round() reads v8_correction_status.json's correction_round.
        job_dir = self.track(ROOT / "output" / "v86-test-fix5-limit")
        job_dir.mkdir(parents=True, exist_ok=True)
        write_json(job_dir / "v8_correction_status.json", {"correction_round": detp.CORRECTION_ROUND_LIMIT})
        # A 3rd round request (previous=2, +1=3 > limit of 2) must still be
        # reported as CORRECTION_LIMIT_EXCEEDED when a real gate failure
        # remains -- Fix 5 explicitly does not raise the round ceiling.
        self.assertEqual(detp.previous_round(job_dir), detp.CORRECTION_ROUND_LIMIT)


# ---------------------------------------------------------------------------
# Regression 3 (continued) -- correction worker cannot fabricate LLM
# evaluation evidence into a protected/non-editable field
# ---------------------------------------------------------------------------

class TestCorrectionWorkerCannotFabricateProtectedConcepts(unittest.TestCase):
    def test_schema_exposes_no_protected_text_field(self):
        schema = correction_worker.correction_output_json_schema()
        resume_props = set(schema["properties"]["resume_payload"]["properties"])
        # Only reorder-integer-array fields for the protected sections
        # (experience_bullet_orders / project_bullet_orders) plus the
        # genuinely editable experience_alternate_bullets/selected_project
        # fields are exposed. There is no field through which "LLM
        # evaluation" (or anything else) could be fabricated into any
        # protected experience/project bullet's actual canonical text.
        self.assertEqual(
            resume_props,
            {"experience_bullet_orders", "experience_alternate_bullets", "project_bullet_orders", "selected_project"},
        )
        self.assertTrue(schema["additionalProperties"] is False)

    def test_editable_fields_exclude_all_protected_canonical_text(self):
        packet = correction_worker.build_correction_packet(
            "job-x", {}, {"correction_round": 1, "reasons": ["ATS"], "details": {}},
            {"title": "some-project"}, {},
        )
        for protected in ("experience_bullet_orders", "project_bullet_orders"):
            self.assertNotIn(protected, packet["editable_fields"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
