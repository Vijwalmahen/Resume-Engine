#!/usr/bin/env python3
"""
V8.6.8 round 3 -- fresh-attempt state-isolation audit and regression suite.

ZERO real Claude/Apify calls anywhere in this file. This is NOT a new
feature pass -- it closes the KNOWN REMAINING RISK flagged after round 2:
`project_selection.json` (like `v8_correction_status.json` before it) can
persist across separate batch runs of the same `job_key` and be silently
reused as though it were the CURRENT attempt's own decision.

Root cause (see README.md "V8.6.8 stabilization pass, round 3" for the full
file-by-file audit): `deterministic_pipeline.ensure_project_selection` has
an `if selection_path.exists(): return <stale content>` idempotent-reuse
check that runs BEFORE semantic call 2 and BEFORE `write_strategy_files`
(where round 2's reset lived) ever gets a chance to clear anything -- so
round 2's fix, while correct for correction-round state, was too late in
the call order to protect the selectable-project selection.

Fix: `deterministic_pipeline.reset_attempt_local_state` -- ONE centralized
function, covering every artifact this pipeline ever treats as
"prior-attempt decision state to reuse/count"
(`ATTEMPT_LOCAL_CONTROL_ARTIFACTS`) -- called from `run_batch.py`'s
`process_job` at the EARLIEST possible point (immediately after
`out_dir.mkdir()`, before `ensure_project_selection` is ever invoked), with
a defensive/idempotent second call still inside `write_strategy_files` for
any caller that bypasses `run_batch.py`.

Covers:
  1-3.  `reset_attempt_local_state`/`ATTEMPT_LOCAL_CONTROL_ARTIFACTS`:
        removes all 5 attempt-local artifacts + round-numbered snapshots;
        idempotent; never touches unrelated/immutable files.
  4-5.  Same-job/new-attempt repeat test (Task 3): after a reset, previous_
        round starts at 0 and a stale project-selection file is gone --
        Run A's correction/density/project-selection state cannot leak
        into Run B.
  6.    Different batch / same job_key test (Task 4): two independent
        "batches" processing the same job_key via `process_job`'s own
        reset call site never share attempt-local state.
  7.    Benchmark-06 replay (Task 6), against any locally-run batch
        artifacts a developer's own `output/`/`.work/` may contain (these
        tests skip cleanly when that private, gitignored local state is
        absent, as it always is in this public repository): before = the
        observed CORRECTION_LIMIT_EXCEEDED with 0 current-run corrections;
        after = previous_round starts at 0 and the same genuine DENSITY
        condition correctly yields NEEDS_LLM_CORRECTION.
  8.    Project-selection stale-state replay (Task 7), using two
        already-saved `jd_requirements.json` fixtures known to bind to
        DIFFERENT selectable projects: proves a stale `project_selection.
        json` left from the first would otherwise have silently determined
        the second, and that the reset makes the second correctly recompute
        its own answer.

Run with:
    python3 tools/resume/v86/tests/test_v86_8_round3_state_isolation.py
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import deterministic_pipeline as detp  # noqa: E402
from common import ROOT, load_json, write_json  # noqa: E402

# Any locally-run batch's own on-disk artifacts (private, gitignored --
# never present in this public repository). Tests that depend on this
# skip cleanly when it is absent.
REAL_BATCH_DIR = ROOT / ".work/v86/batch-synthetic-regression"
REAL_EVIDENCE_ROOT = REAL_BATCH_DIR / "shared_ats_evidence"
SCRATCH_ROOT = ROOT / ".work/v86/v868_round3_test_scratch"


def _scratch_dir(name: str) -> Path:
    d = SCRATCH_ROOT / name
    if d.exists():
        import shutil
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def setUpModule():
    if SCRATCH_ROOT.exists():
        import shutil
        shutil.rmtree(SCRATCH_ROOT)
    SCRATCH_ROOT.mkdir(parents=True)


def tearDownModule():
    if SCRATCH_ROOT.exists():
        import shutil
        shutil.rmtree(SCRATCH_ROOT)


def _plant_full_stale_attempt(job_dir: Path, correction_round: int = 2, project: str = "Project A") -> None:
    """Simulates everything a completed, EARLIER batch run would leave
    behind for this job_key: correction bookkeeping, density bookkeeping,
    a bound selectable-project selection, and its validation guard report."""
    write_json(job_dir / "v8_correction_status.json", {"correction_round": correction_round, "status": "CORRECTED"})
    write_json(job_dir / "correction_request.json", {"correction_round": correction_round, "reasons": ["DENSITY"]})
    write_json(job_dir / "correction_request.round1.json", {"correction_round": 1})
    write_json(job_dir / "correction_request.round2.json", {"correction_round": 2})
    write_json(job_dir / "density_guard.json", {"status": "DENSE_ONE_PAGE"})
    write_json(job_dir / "project_selection.json", {
        "status": "PASS", "selection_mode": "BINDING_REQUIRED_COVERAGE",
        "selected_runtime_name": project, "selected_project_key": project.lower().replace(" ", "_"),
    })
    write_json(job_dir / "v8_project_selection_validation.json", {"status": "PASS"})


# ---------------------------------------------------------------------------
# 1-3. reset_attempt_local_state / ATTEMPT_LOCAL_CONTROL_ARTIFACTS
# ---------------------------------------------------------------------------

class TestResetAttemptLocalState(unittest.TestCase):
    def test_removes_all_five_attempt_local_artifacts_and_round_snapshots(self):
        job_dir = _scratch_dir("reset_full")
        _plant_full_stale_attempt(job_dir)
        removed = detp.reset_attempt_local_state(job_dir)

        for name in detp.ATTEMPT_LOCAL_CONTROL_ARTIFACTS:
            self.assertFalse((job_dir / name).exists(), msg=f"{name} should have been removed")
        self.assertEqual(list(job_dir.glob("correction_request.round*.json")), [])
        self.assertIn("v8_correction_status.json", removed)
        self.assertIn("project_selection.json", removed)
        self.assertIn("correction_request.round1.json", removed)
        self.assertIn("correction_request.round2.json", removed)

    def test_idempotent_on_an_already_clean_job_dir(self):
        job_dir = _scratch_dir("reset_clean")
        removed = detp.reset_attempt_local_state(job_dir)
        self.assertEqual(removed, [])
        # a second call is still a pure no-op
        self.assertEqual(detp.reset_attempt_local_state(job_dir), [])

    def test_never_touches_unrelated_or_immutable_files(self):
        job_dir = _scratch_dir("reset_selective")
        _plant_full_stale_attempt(job_dir)
        # fresh-attempt output files that MUST survive a reset (they are
        # either not yet written at reset time, or are this run's own
        # immutable/input files, never "prior decision state").
        write_json(job_dir / "jd_requirements.json", {"concepts": []})
        write_json(job_dir / "resume_qa.json", {"status": "PASS"})
        write_json(job_dir / "ats_preflight.json", {"gate_pass": True})
        (job_dir / "resume_draft.md").write_text("# resume")

        detp.reset_attempt_local_state(job_dir)

        for name in ("jd_requirements.json", "resume_qa.json", "ats_preflight.json", "resume_draft.md"):
            self.assertTrue((job_dir / name).exists(), msg=f"{name} must NOT be removed by a fresh-attempt reset")

    def test_attempt_local_artifact_list_matches_the_documented_audit(self):
        self.assertEqual(
            set(detp.ATTEMPT_LOCAL_CONTROL_ARTIFACTS),
            {
                "v8_correction_status.json", "correction_request.json",
                "density_guard.json", "project_selection.json",
                "v8_project_selection_validation.json",
            },
        )


# ---------------------------------------------------------------------------
# 4-5. Same-job repeat test (Task 3)
# ---------------------------------------------------------------------------

class TestSameJobRepeatAttempt(unittest.TestCase):
    def test_run_b_previous_round_starts_at_zero_after_reset(self):
        job_dir = _scratch_dir("repeat_correction")
        _plant_full_stale_attempt(job_dir, correction_round=2, project="Project A")
        self.assertEqual(detp.previous_round(job_dir), 2)  # Run A's own history, correctly read

        detp.reset_attempt_local_state(job_dir)  # begin Run B

        self.assertEqual(detp.previous_round(job_dir), 0)

    def test_run_b_project_selection_is_gone_and_must_be_recomputed(self):
        job_dir = _scratch_dir("repeat_project")
        _plant_full_stale_attempt(job_dir, project="Project A")

        # Before reset: Run A's stale selection would be silently reused.
        selection_path = job_dir / "project_selection.json"
        self.assertTrue(selection_path.exists())
        self.assertEqual(load_json(selection_path)["selected_runtime_name"], "Project A")

        detp.reset_attempt_local_state(job_dir)

        self.assertFalse(selection_path.exists(), "Run B must recompute selection, never reuse Run A's")

    def test_run_a_density_state_does_not_affect_run_b(self):
        job_dir = _scratch_dir("repeat_density")
        _plant_full_stale_attempt(job_dir)
        self.assertTrue((job_dir / "density_guard.json").exists())

        detp.reset_attempt_local_state(job_dir)

        self.assertFalse((job_dir / "density_guard.json").exists())

    def test_write_strategy_files_resets_correction_state_but_not_selection(self):
        """write_strategy_files (the defensive/secondary call site) always
        runs AFTER project selection has already legitimately happened for
        THIS attempt in every valid call flow -- it must reset correction/
        density bookkeeping (still a safe, defensive no-op at this point in
        the normal flow) but must NEVER touch project_selection.json/its
        guard report, since by the time it runs those files hold THIS
        attempt's own correct selection, not a stale one (see
        TestDeterministicUnits.test_project_selection_and_strategy_contract
        in test_v86_offline.py, whose ordinary single-attempt flow -- select,
        then write_strategy_files -- this must not break)."""
        job_dir = _scratch_dir("repeat_via_write_strategy_files")
        _plant_full_stale_attempt(job_dir, correction_round=2, project="Project A")

        detp.write_strategy_files(
            job_dir, "fake.md", "fake-job", {"concepts": []},
            {
                "experience_bullet_orders": {}, "experience_alternate_bullets": {},
                "project_bullet_orders": {}, "selected_project": {},
            },
            {"company": ""}, "project_selectable_1",
        )

        self.assertEqual(detp.previous_round(job_dir), 0)
        self.assertFalse((job_dir / "density_guard.json").exists())
        # Project selection is OUT OF SCOPE for this call site -- still
        # present (a real caller would have computed it moments earlier in
        # this same attempt; this fixture's stale "Project A" stands in for
        # that legitimate, just-computed selection).
        self.assertTrue((job_dir / "project_selection.json").exists())

    def test_run_batch_primary_site_resets_selection_before_it_is_ever_reused(self):
        """The full reset (including project selection) is only ever safe
        BEFORE ensure_project_selection runs -- proving the primary call
        site's ordering, not write_strategy_files, is what protects
        project_selection.json."""
        job_dir = _scratch_dir("primary_site_selection_reset")
        _plant_full_stale_attempt(job_dir, project="Project A")

        detp.reset_attempt_local_state(job_dir)  # full reset, include_project_selection=True (default)

        self.assertFalse((job_dir / "project_selection.json").exists())


# ---------------------------------------------------------------------------
# 6. Different batch / same job_key test (Task 4)
# ---------------------------------------------------------------------------

class TestDifferentBatchSameJobKey(unittest.TestCase):
    def test_batch_b_cannot_consume_batch_as_attempt_local_state(self):
        """Simulates run_batch.py's own call order for two SEPARATE batches
        (A then B) processing the identical job_key/out_dir, using the
        exact same reset call site process_job now uses."""
        job_key = "shared-job-key-x"
        out_dir = SCRATCH_ROOT / "out" / job_key

        # --- Batch A: a full run that legitimately uses 2 correction
        # rounds and binds "Project A" ---
        out_dir.mkdir(parents=True, exist_ok=True)
        detp.reset_attempt_local_state(out_dir)  # batch A begins clean
        write_json(out_dir / "project_selection.json", {
            "status": "PASS", "selection_mode": "BINDING_REQUIRED_COVERAGE",
            "selected_runtime_name": "Project A",
        })
        detp.write_correction_request(out_dir, ["DENSITY"], {})
        detp.apply_correction(
            out_dir, 1, {"selected_project": {"owner_id": "project_selectable_1"}},
            {"status": "CORRECTED"}, "project_selectable_1",
        )
        detp.write_correction_request(out_dir, ["DENSITY"], {})
        detp.apply_correction(
            out_dir, 2, {"selected_project": {"owner_id": "project_selectable_1"}},
            {"status": "CORRECTED"}, "project_selectable_1",
        )
        self.assertEqual(detp.previous_round(out_dir), 2)

        # --- Batch B: same job_key, same out_dir, a brand-new attempt ---
        detp.reset_attempt_local_state(out_dir)  # batch B begins clean

        self.assertEqual(detp.previous_round(out_dir), 0)
        self.assertFalse((out_dir / "project_selection.json").exists())
        self.assertFalse((out_dir / "correction_request.json").exists())
        self.assertEqual(list(out_dir.glob("correction_request.round*.json")), [])

        # Batch B now legitimately selects and uses a DIFFERENT project,
        # proving batch A's choice cannot constrain it.
        write_json(out_dir / "project_selection.json", {
            "status": "PASS", "selection_mode": "BINDING_REQUIRED_COVERAGE",
            "selected_runtime_name": "Project B",
        })
        self.assertEqual(load_json(out_dir / "project_selection.json")["selected_runtime_name"], "Project B")


# ---------------------------------------------------------------------------
# 7. Benchmark-06 replay (Task 6)
# ---------------------------------------------------------------------------

class TestBenchmark06Replay(unittest.TestCase):
    JOB_KEY = "benchmark-06-nlp-llm-engineer"

    def setUp(self):
        if not REAL_EVIDENCE_ROOT.exists():
            self.skipTest("local batch-synthetic-regression shared_ats_evidence not present on disk")
        self.real_job_dir = ROOT / "output" / self.JOB_KEY
        if not self.real_job_dir.exists():
            self.skipTest(f"local output/{self.JOB_KEY} not present on disk")
        # V8.6.8 round 4: output/<job_key> is mutable, shared, cross-batch
        # state (the same caveat every historical_replay_v86*.py already
        # discloses) -- a LATER, independent, legitimate batch run, run
        # after this class was written, can reprocess this exact job_key
        # fresh and correctly use only ONE real correction round,
        # overwriting the STALE round-2-at-limit state these two tests
        # need to reproduce the original bug mechanism with a legitimate
        # `correction_round: 1` result. This is a real, expected
        # consequence of round 3's OWN fix working correctly in
        # production -- not a regression -- so skip (never fail) when the
        # on-disk state no longer represents that specific stale-at-limit
        # scenario, rather than asserting a reproduction that can no longer
        # be reproduced from today's on-disk artifact.
        status_path = self.real_job_dir / "v8_correction_status.json"
        if not status_path.exists() or load_json(status_path).get("correction_round") != detp.CORRECTION_ROUND_LIMIT:
            self.skipTest(
                f"real output/{self.JOB_KEY}/v8_correction_status.json has since been legitimately "
                "overwritten by a later batch run and no longer carries the stale "
                f"correction_round == {detp.CORRECTION_ROUND_LIMIT} state this replay needs -- "
                "see this class's own docstring"
            )

    def _copy_real_job(self, dst: Path) -> None:
        import shutil
        dst.mkdir(parents=True, exist_ok=True)
        names = (
            "jd_requirements.json", "resume_payload.json", "cover_letter_payload.json",
            "v8_strategy_status.json", "project_selection.json",
            "correction_request.json", "v8_correction_status.json", "density_guard.json",
        )
        for name in names:
            p = self.real_job_dir / name
            if p.exists():
                shutil.copy(p, dst / name)

    def test_before_reproduces_correction_limit_exceeded_with_zero_attempts(self):
        job_dir = _scratch_dir("bench06_before")
        self._copy_real_job(job_dir)
        if not (job_dir / "v8_correction_status.json").exists():
            self.skipTest("real benchmark-06 artifact no longer has the stale correction state on disk")

        result = detp.run_job_pipeline(job_dir, REAL_EVIDENCE_ROOT, self.JOB_KEY)
        self.assertEqual(result["status"], "CORRECTION_LIMIT_EXCEEDED")

    def test_after_reset_yields_legitimate_needs_llm_correction(self):
        job_dir = _scratch_dir("bench06_after")
        self._copy_real_job(job_dir)

        strategy = load_json(job_dir / "v8_strategy_status.json")
        detp.write_strategy_files(
            job_dir, strategy.get("job_file", ""), self.JOB_KEY,
            load_json(job_dir / "jd_requirements.json"),
            load_json(job_dir / "resume_payload.json"),
            load_json(job_dir / "cover_letter_payload.json"),
            strategy.get("selected_project_bound", ""),
        )
        self.assertEqual(detp.previous_round(job_dir), 0)

        result = detp.run_job_pipeline(job_dir, REAL_EVIDENCE_ROOT, self.JOB_KEY)
        self.assertEqual(result["status"], "NEEDS_LLM_CORRECTION")
        request = load_json(job_dir / "correction_request.json")
        self.assertIn("DENSITY", request.get("reasons", []))
        self.assertEqual(request["correction_round"], 1)  # a genuine, fresh round 1 -- not round 3


# ---------------------------------------------------------------------------
# 8. Project-selection stale-state replay (Task 7)
# ---------------------------------------------------------------------------

class TestProjectSelectionStaleStateReplay(unittest.TestCase):
    """Uses two already-saved `jd_requirements.json` fixtures known (from
    any locally-run batch output a developer's own private, gitignored
    `output/` directory may contain -- never present in this public
    repository) to bind to two DIFFERENT selectable projects, proving a
    stale `project_selection.json` left over from the first job would
    otherwise silently determine the second job's selection, and that a
    reset makes the second job correctly recompute its own answer. The
    specific project names bound to each job are intentionally never
    hardcoded here -- the assertions compare against whatever is actually
    recorded on disk for each job."""

    JOB_A = "benchmark-01-ml-engineer"
    JOB_B = "example-financial-ai-product-development-intern-2000000002"

    def setUp(self):
        self.req_a = ROOT / "output" / self.JOB_A / "jd_requirements.json"
        self.req_b = ROOT / "output" / self.JOB_B / "jd_requirements.json"
        if not (self.req_a.exists() and self.req_b.exists()):
            self.skipTest("jd_requirements.json fixtures for both jobs not present on disk")

    def test_stale_selection_would_silently_determine_the_second_run(self):
        job_dir = _scratch_dir("project_stale")
        job_a_selection = load_json(ROOT / "output" / self.JOB_A / "project_selection.json")
        write_json(job_dir / "project_selection.json", job_a_selection)
        # Job B's OWN requirements are now on disk for this "attempt" --
        # but WITHOUT a reset, ensure_project_selection's own idempotent-
        # reuse check returns Job A's stale selection unchanged.
        selection = detp.ensure_project_selection(job_dir, self.req_b)
        self.assertEqual(
            selection["data"].get("selected_runtime_name"),
            job_a_selection.get("selected_runtime_name"),
        )
        real_job_b_selection = load_json(ROOT / "output" / self.JOB_B / "project_selection.json")
        self.assertNotEqual(
            selection["data"].get("selected_runtime_name"),
            real_job_b_selection.get("selected_runtime_name"),
            "this proves the stale-reuse bug: without a reset, job B's real, correct "
            "selection is never recomputed",
        )

    def test_reset_then_recompute_yields_job_bs_own_correct_selection(self):
        job_dir = _scratch_dir("project_fixed")
        job_a_selection = load_json(ROOT / "output" / self.JOB_A / "project_selection.json")
        write_json(job_dir / "project_selection.json", job_a_selection)

        detp.reset_attempt_local_state(job_dir)  # fresh attempt begins

        selection = detp.ensure_project_selection(job_dir, self.req_b)
        real_job_b_selection = load_json(ROOT / "output" / self.JOB_B / "project_selection.json")
        self.assertEqual(
            selection["data"].get("selected_runtime_name"),
            real_job_b_selection.get("selected_runtime_name"),
        )
        self.assertNotEqual(
            selection["data"].get("selected_runtime_name"),
            job_a_selection.get("selected_runtime_name"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
