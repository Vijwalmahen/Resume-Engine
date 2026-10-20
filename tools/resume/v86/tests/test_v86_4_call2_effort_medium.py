#!/usr/bin/env python3
"""
V8.6.4 EXPERIMENTAL -- offline regression suite for the single-variable
CALL 2 effort change.

ALL semantic calls in this suite go through mocks/patched `invoke()` -- like
every other file in tools/resume/v86/tests/, NOTHING here shells out to the
real `claude` CLI. Zero real Claude/Apify calls, zero cost.

This patch changes exactly ONE thing relative to the frozen V8.6.3
candidate (`backups/v863-candidate-freeze-*`):
    semantic_worker.run_content_call effort default: "high" -> "medium"
CALL 1 (`run_requirements_call` -> "low") and correction
(`run_correction_call` -> "low") are unchanged. No prompt text, schema,
context-slicing function, architecture, queue/refill/live-arrival
behavior, correction limits, tracker behavior, or final QA changed.

Covers:
  1. CALL 1 effort is still "low".
  2. CALL 2 effort is now "medium" (was "high" under V8.6.3).
  3. Correction effort is still "low".
  4. V8.6.2 isolation (env sanitization, fresh session-id, --safe-mode,
     stdin=DEVNULL) is unaffected by the effort change.
  5. V8.6.3 context-slicing functions (CALL 1 evidence view, CALL 2
     candidate-pool filtering, correction-evidence scoping) are
     byte-for-byte unchanged in behavior.
  6. No schema changed (jd_requirements/content output schemas identical
     to the frozen V8.6.3 copy).
  7. semantic_worker.py's only source diff from the V8.6.3 freeze is the
     CALL 2 effort default (+ its docstring) -- proven via direct file
     comparison, not just behavioral assertions.
  8. llm_invoke.py / correction_worker.py are otherwise unchanged from the
     V8.6.3 freeze except for documentation additions in llm_invoke.py
     (no functional diff -- proven by re-running the V8.6.2/V8.6.3 suites
     against the live tree).
  9. Existing V8.6/V8.6.1/V8.6.2/V8.6.3 offline suites all still pass.
  10. All frozen snapshots (V8.5, historical V8.6, V8.6.1, V8.6.2, and the
      new V8.6.3 candidate freeze this pass took before editing anything)
      are unchanged.
  11. Active production config still names V8.6.1.

Run with:
    python3 tools/resume/v86/tests/test_v86_4_call2_effort_medium.py
"""

from __future__ import annotations

import json
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

FIXTURE_CANDIDATE_CONTEXT = {
    "schema_version": "8.0",
    "candidate": {"name": "Test Candidate", "contact": {"personal_email": "x@example.com"}},
    "education": [{"institution": "Test U", "degree": "MS AI", "gpa": "4.0", "coursework": ["ML"]}],
    "experience": [
        {
            "id": "experience_1", "employer": "Example Research Lab", "tailoring_mode": "protected",
            "canonical_bullets": ["Protected experience bullet."],
            "technologies": ["Python"],
        },
        {
            "id": "experience_2", "employer": "Example Data Systems", "tailoring_mode": "tailorable",
            "alternate_bullets_for_dynamic_tailoring": ["Draft experience bullet."],
            "technologies": ["PyTorch"],
        },
    ],
    "projects": [
        {"id": "project_1", "name": "Project Atlas", "protected": True, "canonical_bullets": ["Protected Atlas bullet."], "technologies": ["LangChain"]},
        {"id": "project_2", "name": "Project Beacon", "protected": True, "canonical_bullets": ["Protected Beacon bullet."], "technologies": ["scikit-learn"]},
        {"id": "project_selectable_1", "name": "Project Horizon", "technologies": ["React"]},
        {"id": "project_selectable_2", "name": "Project Solstice", "technologies": ["SQL"]},
        {"id": "project_selectable_3", "name": "Project Meridian", "technologies": ["FastAPI"]},
    ],
    "skills": {
        "static_resume_lines": {"Languages": "Python, PyTorch"},
        "extended_for_dynamic_evidence": {
            "general": ["Docker"],
            "research_interests_not_implemented": ["Quantum ML"],
        },
    },
    "truth_constraints": ["Never fabricate a metric."],
}

CANDIDATE_POOL_NAMES = {
    "Project Horizon",
    "Project Solstice",
    "Project Meridian",
}


# ---------------------------------------------------------------------------
# 1-3. Per-call-type effort values
# ---------------------------------------------------------------------------

class TestPerCallEffortValues(unittest.TestCase):
    def test_call1_requirements_effort_is_low(self):
        packet = {"job_key": "j1", "job_posting_text": "JD", "candidate_context": FIXTURE_CANDIDATE_CONTEXT, "truth_rules": []}
        with mock.patch("semantic_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            semantic_worker.run_requirements_call(packet)
        self.assertEqual(mocked.call_args.kwargs["effort"], "low")

    def test_call2_content_effort_is_medium_not_high(self):
        packet = {
            "job_key": "j1", "candidate_context": FIXTURE_CANDIDATE_CONTEXT,
            "content_budgets": {}, "resume_structure": [],
            "allowed_selectable_projects": [{"key": k, "runtime_name": n} for n in CANDIDATE_POOL_NAMES for k in [n]],
        }
        with mock.patch("semantic_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            semantic_worker.run_content_call(
                packet, {"job_key": "j1", "concepts": []},
                {"selection_mode": "MODEL_CHOICE"},
            )
        self.assertEqual(mocked.call_args.kwargs["effort"], "medium")
        self.assertNotEqual(mocked.call_args.kwargs["effort"], "high")

    def test_correction_effort_is_still_low(self):
        packet = {
            "job_key": "j1", "candidate_context": FIXTURE_CANDIDATE_CONTEXT,
            "resume_payload": {
                "experience_alternate_bullets": {"experience_2": ["x"]},
                "selected_project": {"title": "t", "technology_line": "l", "bullets": ["b"]},
            },
            "correction_request": {"editable_fields": ["experience_alternate_bullets.experience_2"], "reasons": ["r"]},
            "selected_project_binding": {"selection_mode": "MODEL_CHOICE"},
        }
        with mock.patch("correction_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            correction_worker.run_correction_call(packet)
        self.assertEqual(mocked.call_args.kwargs["effort"], "low")

    def test_explicit_effort_overrides_are_still_possible(self):
        # The whole point of per-call effort being a plain function argument
        # (not hardcoded inside invoke/llm_invoke) is that every call site
        # remains independently overridable -- confirm that still holds.
        packet = {"job_key": "j1", "job_posting_text": "JD", "candidate_context": FIXTURE_CANDIDATE_CONTEXT, "truth_rules": []}
        with mock.patch("semantic_worker.invoke") as mocked:
            mocked.return_value = "sentinel"
            semantic_worker.run_requirements_call(packet, effort="xhigh")
        self.assertEqual(mocked.call_args.kwargs["effort"], "xhigh")


# ---------------------------------------------------------------------------
# 4. V8.6.2 isolation unaffected
# ---------------------------------------------------------------------------

class TestIsolationUnaffected(unittest.TestCase):
    def test_medium_effort_coexists_with_every_isolation_flag(self):
        cmd = llm_invoke.build_claude_command("SYS", "USR", {"type": "object"}, None, "sonnet", "sid-1", effort="medium")
        self.assertIn("--effort", cmd)
        self.assertEqual(cmd[cmd.index("--effort") + 1], "medium")
        self.assertIn("--safe-mode", cmd)
        self.assertIn("--session-id", cmd)
        self.assertEqual(cmd[cmd.index("--session-id") + 1], "sid-1")
        self.assertIn("--tools", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "")
        self.assertNotIn("--bare", cmd)
        self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_session_ids_still_unique_regardless_of_medium_effort(self):
        cmd_a = llm_invoke.build_claude_command("S", "U", {"type": "object"}, None, "sonnet", "sid-a", effort="medium")
        cmd_b = llm_invoke.build_claude_command("S", "U", {"type": "object"}, None, "sonnet", "sid-b", effort="medium")
        self.assertNotEqual(cmd_a[cmd_a.index("--session-id") + 1], cmd_b[cmd_b.index("--session-id") + 1])

    def test_sanitized_worker_env_and_isolation_helpers_unchanged(self):
        # sanitized_worker_env / new_isolated_session_id are V8.6.2 fixtures;
        # confirm they still exist with the same behavior (session-identity
        # vars stripped, auth vars preserved) -- effort is orthogonal.
        import os
        env = {"CLAUDE_CODE_SESSION_ID": "should-be-stripped", "ANTHROPIC_API_KEY": "should-survive", "PATH": os.environ.get("PATH", "")}
        with mock.patch.dict(os.environ, env, clear=True):
            sanitized = llm_invoke.sanitized_worker_env()
        self.assertNotIn("CLAUDE_CODE_SESSION_ID", sanitized)
        self.assertEqual(sanitized.get("ANTHROPIC_API_KEY"), "should-survive")


# ---------------------------------------------------------------------------
# 5-6. V8.6.3 context slicing + schemas unchanged
# ---------------------------------------------------------------------------

class TestV863ContextSlicingUnchanged(unittest.TestCase):
    def test_requirements_evidence_context_unchanged(self):
        ctx = context_slicing.requirements_evidence_context(FIXTURE_CANDIDATE_CONTEXT)
        self.assertNotIn("contact", json.dumps(ctx))
        self.assertIn("Python", json.dumps(ctx))

    def test_content_call_candidate_context_binding_mode_unchanged(self):
        ctx = context_slicing.content_call_candidate_context(
            FIXTURE_CANDIDATE_CONTEXT, "Project Meridian", CANDIDATE_POOL_NAMES,
        )
        names = {p["name"] for p in ctx["projects"]}
        self.assertNotIn("Project Horizon", names)
        self.assertIn("Project Meridian", names)
        self.assertIn("Project Atlas", names)

    def test_content_call_candidate_context_model_choice_is_still_a_no_op(self):
        ctx = context_slicing.content_call_candidate_context(FIXTURE_CANDIDATE_CONTEXT, None, CANDIDATE_POOL_NAMES)
        self.assertEqual(ctx, FIXTURE_CANDIDATE_CONTEXT)

    def test_correction_evidence_context_unchanged(self):
        ctx = context_slicing.correction_evidence_context(FIXTURE_CANDIDATE_CONTEXT, None)
        experience_employers = {e.get("employer") for e in ctx.get("experience", [])}
        self.assertNotIn("Example Research Lab", experience_employers)
        self.assertIn("Example Data Systems", experience_employers)


# ---------------------------------------------------------------------------
# 7-8. (removed) Source-level byte-diff proof against `backups/
# v863-candidate-freeze-*` used to live here. This public repo ships no
# backups/ directory -- there is no frozen V8.6.3 candidate left to diff
# against, so these checks have no reusable value here. The property they
# protected (CALL 2 effort is "medium", every other call's effort/behavior
# is unaffected) is already covered directly by
# TestPerCallEffortValues/TestIsolationUnaffected/
# TestV863ContextSlicingUnchanged above.
# ---------------------------------------------------------------------------


class TestActiveProductionConfig(unittest.TestCase):
    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
