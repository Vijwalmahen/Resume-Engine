#!/usr/bin/env python3
"""
V8.6.5 EXPERIMENTAL -- offline regression suite for the deterministic
clear-winner selectable-project selection safeguard.

ZERO real Claude/Apify calls anywhere in this file (like every other
tools/resume/v86/tests/*.py). This is the ONE functional change this pass
makes: `clear_winner_safeguard.apply_clear_winner_safeguard`, wired into
`deterministic_pipeline.ensure_project_selection` immediately after the
frozen `tools/resume/v8/select_project.py` subprocess call.

Covers (see README.md "V8.6.5 EXPERIMENTAL clear-winner safeguard" for the
full writeup):
  1. Evidence ownership: the module only ever consumes already-attributed
     `rankings`, never raw evidence -- proven structurally (source
     inspection), not just behaviorally.
  2. Critical/required concepts outweigh optional ones (dominance guard).
  3. Clear winner -> deterministic binding.
  4. Near tie -> MODEL_CHOICE preserved.
  5. Exact tie -> MODEL_CHOICE preserved.
  6. No signal -> prior behavior preserved.
  7. Existing BINDING_* decisions untouched (identity-preserved, no rewrite).
  8. Protected canonical content unaffected (never referenced at all).
  9. No fabrication: only fields already present in some candidate's own
     ranking row are ever copied into the promoted selection.
  10. Active production config is a valid, current V8.6.x PRODUCTION release.

Run with:
    python3 tools/resume/v86/tests/test_v86_5_clear_winner_safeguard.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import clear_winner_safeguard as cws  # noqa: E402
import context_slicing  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
from common import ROOT, load_json  # noqa: E402


def _ranking(key, name, tie_break, *, required=0, crit_high=0, req_score=0, score=0, total=0):
    return {
        "project_key": key,
        "runtime_name": name,
        "score": score,
        "required_score": req_score,
        "required_match_count": required,
        "critical_high_required_match_count": crit_high,
        "total_match_count": total,
        "required_matches": [{"term": "x"}] if required else [],
        "optional_matches": [],
        "safe_evidence_record_count": 1,
        "tie_break_order": tie_break,
    }


CLEAR_CASE_RANKINGS = [
    _ranking("project_a", "Project A", 1, score=60, total=6),
    _ranking("project_b", "Project B", 2, score=7, total=1),
    _ranking("project_c", "Project C", 3, score=0, total=0),
]

NEAR_TIE_RANKINGS = [
    _ranking("project_a", "Project A", 1, score=40, total=4),
    _ranking("project_b", "Project B", 2, score=38, total=3),
]

EXACT_TIE_RANKINGS = [
    _ranking("project_a", "Project A", 1, score=50, total=5),
    _ranking("project_b", "Project B", 2, score=50, total=5),
]

NO_SIGNAL_RANKINGS = [
    _ranking("project_a", "Project A", 1, score=0, total=0),
    _ranking("project_b", "Project B", 2, score=0, total=0),
]


def _selection(mode, rankings, **extra):
    sel = {
        "status": "PASS",
        "selection_policy": "HYBRID_REQUIRED_COVERAGE",
        "selection_mode": mode,
        "job_key": "synthetic",
        "selected_project_key": None,
        "selected_runtime_name": None,
        "recommended_project_key": rankings[0]["project_key"] if rankings else None,
        "recommended_runtime_name": rankings[0]["runtime_name"] if rankings else None,
        "allowed_project_keys": [r["project_key"] for r in rankings],
        "allowed_runtime_names": [r["runtime_name"] for r in rankings],
        "selected_required_score": 0,
        "selected_required_matches": [],
        "fixed_baseline_covered": [],
        "fixed_baseline_required_covered": [],
        "rankings": rankings,
    }
    sel.update(extra)
    return sel


# ---------------------------------------------------------------------------
# evaluate_clear_winner: clear / near-tie / exact-tie / no-signal
# ---------------------------------------------------------------------------

class TestEvaluateClearWinner(unittest.TestCase):
    def test_clear_case_finds_winner(self):
        result = cws.evaluate_clear_winner(CLEAR_CASE_RANKINGS)
        self.assertIsNotNone(result)
        self.assertEqual(result["winner_project_key"], "project_a")
        self.assertEqual(result["match_count_margin"], 5)
        self.assertEqual(result["score_margin"], 53)

    def test_near_tie_stays_none(self):
        self.assertIsNone(cws.evaluate_clear_winner(NEAR_TIE_RANKINGS))

    def test_exact_tie_stays_none(self):
        self.assertIsNone(cws.evaluate_clear_winner(EXACT_TIE_RANKINGS))

    def test_no_signal_stays_none(self):
        self.assertIsNone(cws.evaluate_clear_winner(NO_SIGNAL_RANKINGS))

    def test_empty_rankings_stays_none(self):
        self.assertIsNone(cws.evaluate_clear_winner([]))

    def test_below_score_ratio_stays_none_even_with_match_margin(self):
        rankings = [
            _ranking("a", "A", 1, score=35, total=5),
            _ranking("b", "B", 2, score=20, total=1),  # margin=4 (ok) but ratio 35/20=1.75 < 2.0
        ]
        self.assertIsNone(cws.evaluate_clear_winner(rankings))

    def test_below_floor_even_with_no_runner_up_signal(self):
        rankings = [
            _ranking("a", "A", 1, score=10, total=2),  # below MIN_TOTAL_MATCHES/MIN_SCORE floor
            _ranking("b", "B", 2, score=0, total=0),
        ]
        self.assertIsNone(cws.evaluate_clear_winner(rankings))

    def test_single_candidate_pool_can_still_bind_if_it_clears_the_floor(self):
        rankings = [_ranking("a", "A", 1, score=40, total=4)]
        result = cws.evaluate_clear_winner(rankings)
        self.assertIsNotNone(result)
        self.assertEqual(result["winner_project_key"], "a")
        self.assertIsNone(result["runner_up_project_key"])

    def test_dominance_guard_blocks_a_malformed_ranking_order(self):
        # Defense-in-depth: even if handed rankings NOT sorted the way the
        # frozen selector always sorts them (runner-up beating the "winner"
        # on a required-concept tier), the safeguard must never promote the
        # nominal rankings[0] over a row with better required coverage.
        malformed = [
            _ranking("a", "A", 1, score=60, total=6, required=0),
            _ranking("b", "B", 2, score=10, total=1, required=1, crit_high=1, req_score=70),
        ]
        self.assertIsNone(cws.evaluate_clear_winner(malformed))


# ---------------------------------------------------------------------------
# apply_clear_winner_safeguard: promotion, passthrough, no fabrication
# ---------------------------------------------------------------------------

class TestApplyClearWinnerSafeguard(unittest.TestCase):
    def test_binding_mode_is_identity_preserved(self):
        sel = _selection(
            "BINDING_REQUIRED_COVERAGE", CLEAR_CASE_RANKINGS,
            selected_project_key="project_a", selected_runtime_name="Project A",
        )
        result = cws.apply_clear_winner_safeguard(sel)
        self.assertIs(result, sel, "an already-bound selection must never be rewritten or copied")

    def test_model_choice_promoted_on_clear_case(self):
        sel = _selection("MODEL_CHOICE", CLEAR_CASE_RANKINGS)
        result = cws.apply_clear_winner_safeguard(sel)
        self.assertEqual(result["selection_mode"], "BINDING_REQUIRED_COVERAGE")
        self.assertEqual(result["selected_project_key"], "project_a")
        self.assertEqual(result["selected_runtime_name"], "Project A")
        self.assertTrue(result["clear_winner_safeguard"]["applied"])
        self.assertEqual(
            result["clear_winner_safeguard"]["reason"], "CLEAR_WINNER_OVERALL_EVIDENCE_MARGIN",
        )

    def test_model_choice_stays_when_no_clear_winner(self):
        sel = _selection("MODEL_CHOICE", NEAR_TIE_RANKINGS)
        result = cws.apply_clear_winner_safeguard(sel)
        self.assertEqual(result["selection_mode"], "MODEL_CHOICE")
        self.assertIsNone(result["selected_project_key"])
        self.assertIsNone(result["selected_runtime_name"])
        self.assertFalse(result["clear_winner_safeguard"]["applied"])
        self.assertEqual(result["clear_winner_safeguard"]["reason"], "NO_CLEAR_WINNER_MARGIN")

    def test_never_mutates_input(self):
        sel = _selection("MODEL_CHOICE", CLEAR_CASE_RANKINGS)
        original = json.loads(json.dumps(sel))
        cws.apply_clear_winner_safeguard(sel)
        self.assertEqual(sel, original, "input selection dict must never be mutated in place")

    def test_promoted_fields_come_only_from_the_winning_rankings_row(self):
        # No fabrication: selected_required_score/matches must be exactly
        # the winning row's own required_score/required_matches, not
        # invented or borrowed from another row.
        rankings = [
            _ranking("a", "A", 1, score=60, total=6, req_score=0),
            _ranking("b", "B", 2, score=7, total=1, req_score=999),  # decoy; must never leak into A's binding
        ]
        sel = _selection("MODEL_CHOICE", rankings)
        result = cws.apply_clear_winner_safeguard(sel)
        self.assertEqual(result["selected_required_score"], 0)
        self.assertNotEqual(result["selected_required_score"], 999)


# ---------------------------------------------------------------------------
# Evidence ownership: structural / source-level proof
# ---------------------------------------------------------------------------

class TestEvidenceOwnershipStructural(unittest.TestCase):
    def test_module_never_reads_raw_evidence_or_runtime_files(self):
        src = (ROOT / "tools/resume/v86/clear_winner_safeguard.py").read_text()
        for forbidden in ("open(", "read_text(", "evidence_bank", "runtime_resume_context", "import json", "Path("):
            self.assertNotIn(
                forbidden, src,
                f"clear_winner_safeguard.py must be a pure function of already-attributed "
                f"`rankings` -- found {forbidden!r}, which implies file/evidence access",
            )

    def test_module_has_no_hardcoded_job_or_project_identifiers(self):
        # Proves this is a general algorithm, not a hardcoded fix for one
        # specific job/project. Uses this repo's own fictional
        # "Candidate Example" dataset identifiers as the probe set -- none
        # of them (or any other specific job_key/project name) may appear
        # hardcoded in this module's source.
        src = (ROOT / "tools/resume/v86/clear_winner_safeguard.py").read_text()
        for forbidden in ("live13-11", "Project Meridian", "Project Horizon", "project_selectable_1"):
            self.assertNotIn(forbidden, src)

    def test_evaluate_clear_winner_only_reads_documented_ranking_keys(self):
        # Whatever apply_clear_winner_safeguard promotes, it is copied from
        # the winning ranking row's OWN fields (see
        # test_promoted_fields_come_only_from_the_winning_rankings_row);
        # this test additionally proves evaluate_clear_winner's decision
        # itself never looks past the fields the frozen selector documents.
        allowed = {
            "project_key", "runtime_name", "score", "required_score",
            "required_match_count", "critical_high_required_match_count",
            "total_match_count",
        }
        src = (ROOT / "tools/resume/v86/clear_winner_safeguard.py").read_text()
        # Every ranking[...].get("...") access must be one of the allowed keys.
        import re
        accessed = set(re.findall(r'ranking\w*\.get\("(\w+)"', src))
        accessed |= set(re.findall(r'winner\.get\("(\w+)"', src))
        accessed |= set(re.findall(r'runner_up\.get\("(\w+)"', src))
        self.assertTrue(accessed, "expected at least one dict .get(...) access to inspect")
        self.assertTrue(accessed.issubset(allowed | {"project_key", "runtime_name", "required_matches"}))


# ---------------------------------------------------------------------------
# Integration: promoted binding flows into context slicing
# ---------------------------------------------------------------------------

class TestIntegrationWithContentCallContextSlicing(unittest.TestCase):
    def test_promoted_binding_filters_candidate_pool_exactly_like_a_native_binding(self):
        candidate_context = {
            "projects": [
                {"name": "Project Atlas", "canonical_bullets": ["p"]},
                {"name": "Project A", "technologies": ["x"]},
                {"name": "Project B", "technologies": ["y"]},
                {"name": "Project C", "technologies": ["z"]},
            ],
        }
        pool_names = {"Project A", "Project B", "Project C"}
        sel = _selection("MODEL_CHOICE", CLEAR_CASE_RANKINGS)
        promoted = cws.apply_clear_winner_safeguard(sel)
        self.assertEqual(promoted["selection_mode"], "BINDING_REQUIRED_COVERAGE")

        ctx = context_slicing.content_call_candidate_context(
            candidate_context, promoted["selected_runtime_name"], pool_names,
        )
        names = {p["name"] for p in ctx["projects"]}
        self.assertIn("Project A", names)
        self.assertNotIn("Project B", names)
        self.assertNotIn("Project C", names)
        self.assertIn("Project Atlas", names)  # protected, always present


# ---------------------------------------------------------------------------
# deterministic_pipeline wiring (offline; subprocess call is faked)
# ---------------------------------------------------------------------------

class TestDeterministicPipelineWiring(unittest.TestCase):
    def _fake_run_step_writing(self, fixture: dict):
        def _fake(cmd, cwd=None):
            out_path = Path(str(cmd[cmd.index("--output") + 1]))
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(fixture))
            return mock.Mock(ok=True, returncode=0, stdout="", stderr="")
        return _fake

    def test_clear_case_is_rewritten_to_binding_on_disk(self, tmp_path=None):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            fixture = _selection("MODEL_CHOICE", CLEAR_CASE_RANKINGS)
            with mock.patch.object(detp, "run_step", side_effect=self._fake_run_step_writing(fixture)) as mocked:
                result = detp.ensure_project_selection(job_dir, job_dir / "jd_requirements.json")
            self.assertEqual(mocked.call_count, 1)
            self.assertTrue(result["ok"])
            self.assertEqual(result["data"]["selection_mode"], "BINDING_REQUIRED_COVERAGE")
            on_disk = load_json(job_dir / "project_selection.json")
            self.assertEqual(on_disk["selection_mode"], "BINDING_REQUIRED_COVERAGE")

            # Idempotent reuse: a second call must NOT re-invoke the frozen
            # selector subprocess, and must return the already-safeguarded data.
            with mock.patch.object(detp, "run_step") as mocked2:
                result2 = detp.ensure_project_selection(job_dir, job_dir / "jd_requirements.json")
            mocked2.assert_not_called()
            self.assertEqual(result2["data"]["selection_mode"], "BINDING_REQUIRED_COVERAGE")

    def test_no_clear_winner_case_still_writes_diagnostic_but_stays_model_choice(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            fixture = _selection("MODEL_CHOICE", NO_SIGNAL_RANKINGS)
            with mock.patch.object(detp, "run_step", side_effect=self._fake_run_step_writing(fixture)):
                result = detp.ensure_project_selection(job_dir, job_dir / "jd_requirements.json")
            self.assertEqual(result["data"]["selection_mode"], "MODEL_CHOICE")
            self.assertIsNone(result["data"]["selected_project_key"])
            self.assertFalse(result["data"]["clear_winner_safeguard"]["applied"])

    def test_already_bound_selection_is_never_rewritten(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            job_dir = Path(tmp) / "job"
            job_dir.mkdir()
            fixture = _selection(
                "BINDING_REQUIRED_COVERAGE", CLEAR_CASE_RANKINGS,
                selected_project_key="project_a", selected_runtime_name="Project A",
            )
            with mock.patch.object(detp, "run_step", side_effect=self._fake_run_step_writing(fixture)):
                detp.ensure_project_selection(job_dir, job_dir / "jd_requirements.json")
            on_disk_raw = (job_dir / "project_selection.json").read_text()
            self.assertNotIn("clear_winner_safeguard", on_disk_raw, "an already-bound selection must be byte-identical -- no rewrite, no added key")


# ---------------------------------------------------------------------------
# Active production config
# ---------------------------------------------------------------------------
#
# This public repo ships no `backups/` directory and no
# `verify_frozen_v85.py`/`historical_replay_v865.py` scripts (development-
# time-only artifacts, per `config/active_resume_pipeline.json`'s own
# `_comment`), so the byte-diff-against-frozen-snapshot and historical-
# replay checks that used to live in this suite have no reusable baseline
# here and are removed rather than left permanently failing -- matching the
# pattern already applied in test_v86_7_ats_bridge_reciprocal_rank_fusion.py.

class TestActiveProductionConfig(unittest.TestCase):
    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
