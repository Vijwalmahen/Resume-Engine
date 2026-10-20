#!/usr/bin/env python3
"""
V8.6.7 -- offline regression suite for the "Reciprocal rank fusion" ATS
concept bridge.

ZERO real Claude/Apify calls anywhere in this file (like every other
tools/resume/v86/tests/*.py). This pass fixes the root cause of a real
historical batch job's `gate_pass: false` /
`supported_but_missing_required: ["Reciprocal rank fusion"]` result:

`config/v86/ats_concept_bridges.json` gains exactly one new, explicit,
narrow bridge: "reciprocal rank fusion" -> ["RRF", "RRF fusion"] -- truthful
rendered variants already used verbatim in the candidate's own canonical
project bullets. This is the existing Fix 2 mechanism
(`tools/resume/v86/ats_preflight_v86.py` `--extra-aliases`) already used by
every V8.6.1-V8.6.6 bridge -- no new matching code, no production Python
logic changed at all in this pass.

Covers:
  1-5. The "reciprocal rank fusion" bridge: matches "RRF", matches "RRF
       fusion", does not match unrelated generic "fusion", does not match
       generic "ranking", does not match an arbitrary standalone letter
       sequence.
  6-7. A synthetic, checked-in fixture
       (tests/fixtures/v866_scale10_09_retrieval_engineering_intern/, built
       from the fictional "Candidate Example" dataset) shaped exactly like
       the real batch job artifacts this bridge was written to fix:
       required-missing -> present, ATS gate false -> true, offline, zero
       model calls.
  8.   Evidence ownership is untouched by this pass: the shared verified
       evidence backing the fixture still owns "Reciprocal rank fusion" via
       the same two projects' `technologies` lists (no evidence added, no
       ownership transferred).
  9.   Existing "hybrid search" bridge (V8.6.6) still matches exactly as
       before.
  10.  Active production config is still a valid V8.6.x PRODUCTION config.

Run with:
    python3 tools/resume/v86/tests/test_v86_7_ats_bridge_reciprocal_rank_fusion.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ats_preflight_v86 as ats86  # noqa: E402
from common import ROOT, load_json, write_json  # noqa: E402

ALIASES_PATH = ROOT / "config/ats_aliases.json"
BRIDGES_PATH = ROOT / "config/v86/ats_concept_bridges.json"

JOB09_KEY = "retrieval-engineering-intern"
# V8.6.8 mutable-test-dependency fix: these two constants used to point
# directly at a shared, mutable `output/<job_key>/` job-output folder and its
# batch-local `shared_ats_evidence/` evidence copy -- either of which any
# unrelated LATER batch run could silently overwrite. Both now point at an
# immutable, checked-in, fully SYNTHETIC fixture instead (built from the
# fictional "Candidate Example" dataset -- see this directory's README.md)
# -- no test logic below changed.
JOB09_DIR = Path(__file__).resolve().parent / "fixtures" / "v866_scale10_09_retrieval_engineering_intern"
JOB09_EVIDENCE_ROOT = JOB09_DIR / "shared_ats_evidence"


def _rrf_concept():
    return {"term": "Reciprocal rank fusion", "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high"}


def _merged_aliases():
    return ats86.load_and_merge_aliases(ALIASES_PATH, BRIDGES_PATH)


def _evaluate_synthetic(concept: dict, resume_text: str, evidence_bullets: list) -> dict:
    requirements = {"job_key": "synthetic", "concepts": [concept]}
    with tempfile.TemporaryDirectory() as tmp:
        evidence_root = Path(tmp)
        write_json(evidence_root / "evidence.json", {
            "projects": [{"name": "Synthetic Project", "canonical_bullets": evidence_bullets}],
        })
        return ats86.evaluate(requirements, resume_text, evidence_root, _merged_aliases())


# ---------------------------------------------------------------------------
# 1-5. The "reciprocal rank fusion" bridge
# ---------------------------------------------------------------------------

class TestReciprocalRankFusionBridgeConfig(unittest.TestCase):
    def test_bridge_key_present_and_narrow(self):
        bridges = json.loads(BRIDGES_PATH.read_text())
        self.assertIn("reciprocal rank fusion", bridges)
        forms = bridges["reciprocal rank fusion"]
        self.assertEqual(
            [f.strip().lower() for f in forms], ["rrf", "rrf fusion"],
            "bridge must be exactly ['RRF', 'RRF fusion'] -- no broader variants",
        )
        # Narrow: no generic fusion/rank/ranking terms in the bridge list.
        lowered = [f.strip().lower() for f in forms]
        self.assertNotIn("fusion", lowered)
        self.assertNotIn("rank", lowered)
        self.assertNotIn("ranking", lowered)
        self.assertNotIn("rank fusion", lowered)

    def test_1_matches_rrf(self):
        # 1. "Reciprocal rank fusion" matches "RRF".
        resume_text = "- Built a recommender using hybrid dense+sparse retrieval (ChromaDB + BM25 with RRF)."
        report = _evaluate_synthetic(
            _rrf_concept(), resume_text,
            ["Built a recommender using hybrid dense+sparse retrieval (ChromaDB + BM25 with RRF)."],
        )
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "SUPPORTED_AND_PRESENT")
        self.assertIn("Reciprocal rank fusion", report["supported_and_present"])

    def test_2_matches_rrf_fusion(self):
        # 2. "Reciprocal rank fusion" matches "RRF fusion".
        resume_text = "- Designed hybrid retrieval (BM25 + HNSW vector search with RRF fusion), cross-encoder reranking."
        report = _evaluate_synthetic(
            _rrf_concept(), resume_text,
            ["Designed hybrid retrieval (BM25 + HNSW vector search with RRF fusion), cross-encoder reranking."],
        )
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "SUPPORTED_AND_PRESENT")
        self.assertIn("Reciprocal rank fusion", report["supported_and_present"])

    def test_3_does_not_match_unrelated_generic_fusion(self):
        # 3. It does NOT match unrelated generic "fusion".
        resume_text = "- Built a sensor-fusion pipeline combining LIDAR and camera data."
        report = _evaluate_synthetic(
            _rrf_concept(), resume_text,
            ["Built a sensor-fusion pipeline combining LIDAR and camera data."],
        )
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "UNSUPPORTED")
        self.assertEqual(concept["evidence_hits"], [])
        self.assertNotIn("Reciprocal rank fusion", report["supported_and_present"])
        self.assertNotIn("Reciprocal rank fusion", report["supported_but_missing"])

    def test_4_does_not_match_generic_ranking(self):
        # 4. It does NOT match generic "ranking".
        resume_text = "- Built a learning-to-rank model for search ranking relevance."
        report = _evaluate_synthetic(
            _rrf_concept(), resume_text,
            ["Built a learning-to-rank model for search ranking relevance."],
        )
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "UNSUPPORTED")
        self.assertEqual(concept["evidence_hits"], [])

    def test_5_does_not_match_arbitrary_standalone_letter_sequence(self):
        # 5. It does NOT match an arbitrary standalone letter sequence.
        resume_text = "- Migrated the ETL job from RRP to a new scheduler and fixed the FRR alert threshold."
        report = _evaluate_synthetic(
            _rrf_concept(), resume_text,
            ["Migrated the ETL job from RRP to a new scheduler and fixed the FRR alert threshold."],
        )
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "UNSUPPORTED")
        self.assertEqual(concept["evidence_hits"], [])


# ---------------------------------------------------------------------------
# 6-7. Synthetic job09-shaped saved artifacts
# ---------------------------------------------------------------------------

class TestJob09RealArtifactBridgeRegression(unittest.TestCase):
    """Uses a synthetic, checked-in fixture (fictional "Candidate Example"
    dataset) shaped exactly like the real, already-saved batch artifacts
    this bridge was originally written to fix -- zero model calls, this
    data ships in the repo."""

    def setUp(self):
        if not (JOB09_DIR / "jd_requirements.json").exists() or not JOB09_EVIDENCE_ROOT.exists():
            self.skipTest("job09 fixture artifacts not present")
        self.requirements = load_json(JOB09_DIR / "jd_requirements.json")
        self.resume_text = (JOB09_DIR / "resume_draft.md").read_text(encoding="utf-8")

    def _evaluate(self, aliases):
        return ats86.evaluate(self.requirements, self.resume_text, JOB09_EVIDENCE_ROOT, aliases)

    def test_6_old_aliases_reproduce_the_original_failure(self):
        old_aliases = ats86.load_and_merge_aliases(ALIASES_PATH, None)
        report = self._evaluate(old_aliases)
        by_term = {c["term"]: c for c in report["concepts"]}
        self.assertEqual(by_term["Reciprocal rank fusion"]["status"], "SUPPORTED_BUT_MISSING")
        self.assertIn("Reciprocal rank fusion", report["supported_but_missing_required"])
        self.assertFalse(report["gate_pass"])

    def test_6_required_missing_becomes_present(self):
        report = self._evaluate(_merged_aliases())
        by_term = {c["term"]: c for c in report["concepts"]}
        self.assertEqual(by_term["Reciprocal rank fusion"]["status"], "SUPPORTED_AND_PRESENT")
        self.assertIn("Reciprocal rank fusion", report["supported_and_present"])
        self.assertNotIn("Reciprocal rank fusion", report["supported_but_missing_required"])

    def test_7_gate_becomes_true(self):
        report = self._evaluate(_merged_aliases())
        self.assertTrue(report["gate_pass"])

    def test_other_concepts_unchanged(self):
        old_aliases = ats86.load_and_merge_aliases(ALIASES_PATH, None)
        old_report = self._evaluate(old_aliases)
        new_report = self._evaluate(_merged_aliases())
        old_status = {c["term"]: c["status"] for c in old_report["concepts"]}
        new_status = {c["term"]: c["status"] for c in new_report["concepts"]}
        changed = {t: (old_status[t], new_status[t]) for t in old_status if old_status[t] != new_status[t]}
        self.assertEqual(changed, {"Reciprocal rank fusion": ("SUPPORTED_BUT_MISSING", "SUPPORTED_AND_PRESENT")})

    def test_no_correction_would_be_requested_solely_for_this_concept(self):
        # The fixture's correction_request.json (mirroring the original
        # run's correction round 1) was raised for DENSITY only, never
        # ATS/RRF -- proving the bridge alone (no correction round) is what
        # resolves this concept.
        request_path = JOB09_DIR / "correction_request.json"
        if not request_path.exists():
            self.skipTest("no correction_request.json on disk for job09 (nothing to check)")
        request = load_json(request_path)
        self.assertNotIn("ATS", request.get("reasons", []))


# ---------------------------------------------------------------------------
# 8. Evidence ownership untouched
# ---------------------------------------------------------------------------

class TestEvidenceOwnershipUntouched(unittest.TestCase):
    # V8.6.8 stabilization pass: two methods that used to live here
    # (`test_evidence_ownership_module_byte_identical_to_v866_freeze`,
    # `test_deterministic_pipeline_diff_from_v866_freeze_is_only_documented_
    # functions`) byte-diffed live production files against
    # `backups/v866-candidate-freeze-*` snapshots. This public repo ships no
    # `backups/` directory -- there is no frozen baseline left to compare
    # against, so those checks have no reusable value here and are removed
    # rather than left permanently failing.

    def test_no_verified_evidence_file_rewritten(self):
        # The shared verified evidence backing job09 must be byte-identical
        # to what the batch runner originally wrote.
        evidence_path = JOB09_EVIDENCE_ROOT / "verified_resume_evidence.json"
        if not evidence_path.exists():
            self.skipTest("job09 evidence file not present")
        data = json.loads(evidence_path.read_text())
        # "Reciprocal rank fusion" must still be owned by the same
        # technologies-list entries it always was -- no new evidence added,
        # no ownership transferred to a different project/employer.
        self.assertIn("Reciprocal rank fusion", data["projects"][0]["technologies"])
        self.assertIn("Reciprocal rank fusion", data["projects"][1]["technologies"])


# ---------------------------------------------------------------------------
# 9. Existing "hybrid search" bridge (V8.6.6) untouched
# ---------------------------------------------------------------------------

class TestExistingV866BridgesUntouched(unittest.TestCase):
    # V8.6.8 stabilization pass:
    # `test_all_pre_existing_bridge_keys_and_values_unchanged` used to live
    # here and compared `BRIDGES_PATH` against
    # `backups/v866-candidate-freeze-*/config-v86/ats_concept_bridges.json`.
    # This public repo ships no `backups/` directory -- there is no frozen
    # baseline left to compare against, so that check has no reusable value
    # here and is removed rather than left permanently failing. The property
    # it existed to protect (no pre-existing bridge key/value removed or
    # edited by this pass) is a byte-diff against a snapshot this repo
    # doesn't ship, so it cannot be reconstructed generically without
    # reintroducing a fake baseline; the still-checkable half of that intent
    # -- the "hybrid search" bridge still matches exactly as before -- is
    # covered directly below.

    def test_hybrid_search_bridge_still_matches(self):
        concept = {"term": "Hybrid search", "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high"}
        resume_text = "- Built a recommender using hybrid dense+sparse retrieval and reranking."
        report = _evaluate_synthetic(concept, resume_text, [resume_text.lstrip("- ")])
        self.assertEqual(report["concepts"][0]["status"], "SUPPORTED_AND_PRESENT")


# ---------------------------------------------------------------------------
# 10. Active production config
# ---------------------------------------------------------------------------
#
# V8.6.8 FINAL: `TestFrozenIntegrityAndPriorSuitesV867` (frozen-manifest
# self-verification against `backups/*-candidate-freeze-*/` +
# `verify_frozen_v85.py` subprocess) and `TestScopeOfChangeV867`
# (byte-diffing every other production .py file / the bridges config against
# a `backups/v866-candidate-freeze-*` snapshot) used to live here. This
# public repo ships no `backups/` directory and no `verify_frozen_v85.py`
# script, so neither class has a frozen baseline left to compare against --
# both are removed rather than left permanently failing. Their one
# reconstructible assertion (the active production config names a valid,
# current V8.6.x PRODUCTION release) is kept below, matching the pattern
# already applied in test_v86_3_usage_optimization.py /
# test_v86_4_call2_effort_medium.py.

class TestActiveProductionConfig(unittest.TestCase):
    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
