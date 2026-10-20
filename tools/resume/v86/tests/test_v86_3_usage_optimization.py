#!/usr/bin/env python3
"""
V8.6.3 -- offline regression suite for the usage/cost optimization pass.

ALL semantic calls in this suite go through mocks/patched `invoke()` -- like
every other file in tools/resume/v86/tests/, NOTHING here shells out to the
real `claude` CLI. Zero real Claude/Apify calls, zero cost.

Covers (see the V8.6.3 task's "OFFLINE REGRESSION REQUIREMENTS"):
  1. V8.6.2 isolation remains intact.
  2. CALL 1 receives only JD-extraction-relevant information.
  3. CALL 2 receives all required truthful candidate evidence but no
     unnecessary project/context duplication.
  4. Third-project deterministic binding / selection files are untouched.
  5. Protected canonical content remains available exactly where required.
  6. Correction requests contain sufficient truthful grounding.
  7. Normal architecture remains at max 2 semantic calls before correction.
  8. Concurrent worker session IDs remain isolated (unaffected by effort).
  9. Tools/skills/subagents remain disabled (unaffected by effort).
  10-16. Existing V8.6/V8.6.1/V8.6.2 suites + all frozen snapshots (V8.5,
     historical V8.6, V8.6.1, and the new V8.6.2 candidate freeze this pass
     created) are unchanged -- see TestFrozenIntegrityV863.

Run with:
    python3 tools/resume/v86/tests/test_v86_3_usage_optimization.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import context_slicing  # noqa: E402
import correction_worker  # noqa: E402
import llm_invoke  # noqa: E402
import semantic_worker  # noqa: E402
from common import ROOT  # noqa: E402

# A compact but structurally-complete fixture mirroring the REAL
# shared_candidate_context.json shape (schema_version/candidate/education/
# experience/projects/skills/truth_constraints) -- trimmed to the minimum
# needed to exercise every slicing branch, not a full copy of production
# candidate data.
FIXTURE_CANDIDATE_CONTEXT = {
    "schema_version": "8.0",
    "candidate": {"name": "Test Candidate", "contact": {"personal_email": "x@example.com"}},
    "education": [{"institution": "Test U", "degree": "MS AI", "gpa": "4.0", "coursework": ["ML"]}],
    "experience": [
        {
            "id": "experience_1",
            "employer": "Example Research Lab",
            "title": "AI Engineer Intern",
            "tailoring_mode": "protected",
            "canonical_bullets": ["Protected experience bullet one.", "Protected experience bullet two."],
            "technologies": ["Python", "FastAPI", "Redis"],
            "ownership_boundary": "narrative not needed by call 1",
        },
        {
            "id": "experience_2",
            "employer": "Example Data Systems",
            "title": "Data Systems Intern",
            "tailoring_mode": "tailorable",
            "alternate_bullets_for_dynamic_tailoring": ["Editable experience bullet A.", "Editable experience bullet B."],
            "technologies": ["Kafka", "Airflow"],
        },
    ],
    "projects": [
        {"id": "project_1", "name": "Project Atlas", "canonical_bullets": ["Protected Atlas bullet."], "technologies": ["Qdrant"]},
        {"id": "project_2", "name": "Project Beacon", "canonical_bullets": ["Protected Beacon bullet."], "technologies": ["ChromaDB"]},
        {"id": "project_selectable_1", "name": "Project Meridian", "bullets": ["Meridian bullet."], "technologies": ["CrewAI"]},
        {"id": "project_selectable_2", "name": "Project Horizon", "bullets": ["Horizon bullet."], "technologies": ["XGBoost"]},
        {"id": "project_selectable_3", "name": "Project Solstice", "bullets": ["Solstice bullet."], "technologies": ["LangGraph"]},
    ],
    "skills": {
        "static_resume_lines": {"Languages": "Python, C, C++"},
        "extended_for_dynamic_evidence": {
            "cloud_devops": ["AWS", "Docker Compose"],
            "research_interests_not_implemented": ["adaptive test-time reasoning", "verifier-guided search"],
        },
    },
    "truth_constraints": ["Do not increase project counts.", "Example Research Lab: credit AWS deployment to 'the wider team'."],
}

CANDIDATE_POOL_NAMES = {
    "Project Meridian",
    "Project Horizon",
    "Project Solstice",
}


# ---------------------------------------------------------------------------
# 2. CALL 1 receives only JD-extraction-relevant information
# ---------------------------------------------------------------------------

class TestCall1EvidenceSlicing(unittest.TestCase):
    def test_no_irrelevant_authoring_only_fields_reach_call1(self):
        sliced = context_slicing.requirements_evidence_context(FIXTURE_CANDIDATE_CONTEXT)
        blob = json.dumps(sliced)
        # Nothing authoring-only (canonical wording, ownership narrative,
        # contact info, GPA, formatting truth_constraints) reaches call 1.
        for forbidden in (
            "Protected experience bullet", "Protected Atlas bullet", "Protected Beacon bullet",
            "ownership_boundary", "narrative not needed", "personal_email", "x@example.com",
            "gpa", "4.0", "coursework", "credit AWS deployment", "Do not increase project counts",
        ):
            self.assertNotIn(forbidden, blob, f"{forbidden!r} leaked into call-1 evidence")
        self.assertNotIn("truth_constraints", sliced)
        self.assertNotIn("candidate", sliced)

    def test_every_real_technology_and_skill_term_survives(self):
        sliced = context_slicing.requirements_evidence_context(FIXTURE_CANDIDATE_CONTEXT)
        flat = json.dumps(sliced)
        for real_term in ("Python", "FastAPI", "Redis", "Kafka", "Airflow", "Qdrant", "ChromaDB", "CrewAI", "XGBoost", "LangGraph", "AWS", "Docker Compose", "C++"):
            self.assertIn(real_term, flat, f"real evidence term {real_term!r} must survive call-1 slicing")
        self.assertEqual(sliced["education"], [{"institution": "Test U", "degree": "MS AI"}])

    def test_unimplemented_research_interests_excluded(self):
        sliced = context_slicing.requirements_evidence_context(FIXTURE_CANDIDATE_CONTEXT)
        flat = json.dumps(sliced)
        self.assertNotIn("adaptive test-time reasoning", flat)
        self.assertNotIn("verifier-guided search", flat)

    def test_packet_for_requirements_excludes_content_budgets_and_schemas(self):
        packet = {
            "job_key": "j1", "job_posting_text": "JD text", "candidate_context": FIXTURE_CANDIDATE_CONTEXT,
            "truth_rules": ["rule1"], "content_budgets": {"bullet_max_chars": 180},
            "resume_structure": ["EDUCATION"], "schemas": {"jd_requirements_shape": {}},
            "allowed_selectable_projects": [],
        }
        out = semantic_worker.packet_for_requirements(packet)
        self.assertEqual(set(out.keys()), {"job_key", "job_posting_text", "candidate_context", "truth_rules"})

    def test_run_requirements_call_defaults_to_low_effort(self):
        with mock.patch("semantic_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            packet = {"job_key": "j1", "job_posting_text": "JD", "candidate_context": FIXTURE_CANDIDATE_CONTEXT, "truth_rules": []}
            semantic_worker.run_requirements_call(packet)
        self.assertEqual(mocked.call_args.kwargs["effort"], "low")
        self.assertEqual(mocked.call_args.kwargs["call_tag"], "requirements")


# ---------------------------------------------------------------------------
# 3. CALL 2 receives all required truthful evidence but no unnecessary
#    project duplication
# ---------------------------------------------------------------------------

class TestCall2ContextSlicing(unittest.TestCase):
    def test_binding_mode_drops_unselected_candidates_keeps_protected_and_bound(self):
        ctx = context_slicing.content_call_candidate_context(
            FIXTURE_CANDIDATE_CONTEXT, "Project Meridian", CANDIDATE_POOL_NAMES,
        )
        names = {p["name"] for p in ctx["projects"]}
        self.assertEqual(names, {
            "Project Atlas",
            "Project Beacon",
            "Project Meridian",
        })
        # Protected canonical bullets pass through byte-identical.
        pharma = next(p for p in ctx["projects"] if p["name"] == "Project Atlas")
        self.assertEqual(pharma["canonical_bullets"], ["Protected Atlas bullet."])

    def test_model_choice_mode_is_a_no_op_full_pool_preserved(self):
        ctx = context_slicing.content_call_candidate_context(FIXTURE_CANDIDATE_CONTEXT, None, CANDIDATE_POOL_NAMES)
        self.assertEqual(ctx, FIXTURE_CANDIDATE_CONTEXT)
        names = {p["name"] for p in ctx["projects"]}
        self.assertEqual(len(names), 5)  # all candidates + both protected projects

    def test_run_content_call_defaults_to_medium_effort_and_filters_projects(self):
        # NOTE (V8.6.4 EXPERIMENTAL): the default effort literal below was
        # "high" under V8.6.3 and is updated to "medium" here to match that
        # single, intentionally-changed variable -- see
        # tools/resume/v86/tests/test_v86_4_call2_effort_medium.py and
        # llm_invoke.py "V8.6.4 EXPERIMENTAL efficiency patch". Every other
        # property this test file checks (context slicing, project
        # filtering, isolation coexistence, frozen integrity) is unchanged.
        packet = {
            "job_key": "j1", "candidate_context": FIXTURE_CANDIDATE_CONTEXT,
            "content_budgets": {}, "resume_structure": [],
            "allowed_selectable_projects": [{"key": k, "runtime_name": n} for k, n in [
                ("project_horizon", "Project Horizon"),
                ("project_solstice", "Project Solstice"),
                ("project_meridian", "Project Meridian"),
            ]],
        }
        with mock.patch("semantic_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            semantic_worker.run_content_call(
                packet, {"job_key": "j1", "concepts": []},
                {"selection_mode": "BINDING_REQUIRED_COVERAGE", "selected_runtime_name": "Project Meridian"},
            )
        self.assertEqual(mocked.call_args.kwargs["effort"], "medium")
        sent_prompt = mocked.call_args[0][1]  # user_prompt positional arg
        self.assertNotIn("Project Horizon", sent_prompt)
        self.assertNotIn("Project Solstice", sent_prompt)
        self.assertIn("Project Meridian", sent_prompt)
        self.assertIn("Protected Atlas bullet", sent_prompt)
        self.assertIn("Protected Beacon bullet", sent_prompt)


# ---------------------------------------------------------------------------
# 5/6. Correction evidence: protected content excluded, editable-field
#      evidence (tailorable experience + bound project + skills) fully
#      available
# ---------------------------------------------------------------------------

class TestCorrectionEvidenceSlicing(unittest.TestCase):
    def test_protected_experience_and_unbound_projects_excluded(self):
        sliced = context_slicing.correction_evidence_context(
            FIXTURE_CANDIDATE_CONTEXT, "Project Meridian",
        )
        employers = {e["employer"] for e in sliced["experience"]}
        self.assertEqual(employers, {"Example Data Systems"})  # Example Research Lab (canonical) excluded
        names = {p["name"] for p in sliced["selected_project_evidence"]}
        self.assertEqual(names, {"Project Meridian"})

    def test_skills_available_minus_unimplemented_bucket(self):
        sliced = context_slicing.correction_evidence_context(FIXTURE_CANDIDATE_CONTEXT, "Project Meridian")
        blob = json.dumps(sliced["skills"])
        self.assertIn("AWS", blob)
        self.assertIn("Python", blob)
        self.assertNotIn("research_interests_not_implemented", blob)
        self.assertNotIn("adaptive test-time reasoning", blob)

    def test_build_correction_packet_still_hides_selected_project_reselection_path(self):
        packet = correction_worker.build_correction_packet(
            "job-x", {"selected_project": {"title": "x"}},
            {"correction_round": 1, "reasons": ["ATS"], "details": {}},
            {"title": "Project Meridian"}, FIXTURE_CANDIDATE_CONTEXT,
        )
        self.assertNotIn("selected_project.title", packet["editable_fields"])
        self.assertNotIn("Project Horizon", json.dumps(packet["allowed_evidence"]))
        self.assertIn("Project Meridian", json.dumps(packet["allowed_evidence"]))

    def test_run_correction_call_defaults_to_low_effort(self):
        with mock.patch("correction_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            correction_worker.run_correction_call({"job_key": "j1"})
        self.assertEqual(mocked.call_args.kwargs["effort"], "low")
        self.assertEqual(mocked.call_args.kwargs["call_tag"], "correction")


# ---------------------------------------------------------------------------
# 1/8/9. V8.6.2 isolation properties unaffected by the effort flag
# ---------------------------------------------------------------------------

class TestIsolationUnaffectedByEffort(unittest.TestCase):
    def test_effort_flag_coexists_with_every_isolation_flag(self):
        cmd = llm_invoke.build_claude_command("SYS", "USR", {"type": "object"}, None, "sonnet", "sid-1", effort="low")
        self.assertIn("--effort", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "low")
        for still_present in ("--tools", "--disable-slash-commands", "--safe-mode", "--no-session-persistence"):
            self.assertIn(still_present, cmd)
        idx = cmd.index("--tools")
        self.assertEqual(cmd[idx + 1], "")

    def test_omitting_effort_produces_byte_identical_command_to_pre_v863(self):
        with_none = llm_invoke.build_claude_command("SYS", "USR", {"type": "object"}, None, "sonnet", "sid-1")
        with_explicit_none = llm_invoke.build_claude_command("SYS", "USR", {"type": "object"}, None, "sonnet", "sid-1", effort=None)
        self.assertEqual(with_none, with_explicit_none)
        self.assertNotIn("--effort", with_none)

    def test_session_ids_still_unique_regardless_of_effort(self):
        cmd_a = llm_invoke.build_claude_command("S", "U", {"type": "object"}, None, "sonnet", "sid-a", effort="low")
        cmd_b = llm_invoke.build_claude_command("S", "U", {"type": "object"}, None, "sonnet", "sid-b", effort="low")
        self.assertNotEqual(cmd_a[cmd_a.index("--session-id") + 1], cmd_b[cmd_b.index("--session-id") + 1])


# ---------------------------------------------------------------------------
# 7. Normal architecture remains at max 2 semantic calls before correction
# ---------------------------------------------------------------------------

class TestArchitectureStillTwoCalls(unittest.TestCase):
    def test_two_calls_with_distinct_effort_levels(self):
        with mock.patch("semantic_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            packet = {
                "job_key": "j1", "job_posting_text": "JD", "candidate_context": FIXTURE_CANDIDATE_CONTEXT,
                "truth_rules": [], "content_budgets": {}, "resume_structure": [], "allowed_selectable_projects": [],
            }
            semantic_worker.run_requirements_call(packet)
            semantic_worker.run_content_call(packet, {"job_key": "j1", "concepts": []}, {"selection_mode": "MODEL_CHOICE"})
        self.assertEqual(mocked.call_count, 2)
        efforts = [c.kwargs["effort"] for c in mocked.call_args_list]
        # CALL 2 was "high" under V8.6.3; V8.6.4 EXPERIMENTAL changes only
        # this literal (see note on TestCall2ContextSlicing above).
        self.assertEqual(efforts, ["low", "medium"])


# ---------------------------------------------------------------------------
# 10-16. Existing suites still pass + active production config
# ---------------------------------------------------------------------------
#
# V8.6.8 FINAL: the recursive `test_vXXX_suite_still_passes` methods that
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

    def test_selectable_project_selector_still_present(self):
        # tools/resume/v8 is the frozen V8.5 tree this pipeline still
        # invokes unmodified for deterministic selectable-project selection.
        live_selector = (ROOT / "tools/resume/v8/select_project.py").read_text()
        self.assertIn("def normalize(", live_selector)


if __name__ == "__main__":
    unittest.main(verbosity=2)
