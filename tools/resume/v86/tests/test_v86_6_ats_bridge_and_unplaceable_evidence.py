#!/usr/bin/env python3
"""
V8.6.6 -- offline regression suite for the "Hybrid search" ATS concept
bridge and the unplaceable-evidence correction safety net.

ZERO real Claude/Apify calls anywhere in this file (like every other
tools/resume/v86/tests/*.py). This pass fixes the root cause of a real
historical batch job's CORRECTION_LIMIT_EXCEEDED (see README.md "V8.6.6"):

1. `config/v86/ats_concept_bridges.json` gains one new, explicit, narrow
   bridge: "hybrid search" -> truthful rendered variants ("hybrid
   retrieval", "hybrid dense+sparse retrieval", ...) already used by the
   candidate's canonical evidence. This is Fix 2's existing mechanism
   (ats_preflight_v86.py), extended with ONE new reviewed phrase -- no new
   matching code.
2. `tools/resume/v86/evidence_ownership.py` (new) + `deterministic_
   pipeline.split_missing_required_by_placeability` (new) -- for whatever a
   bridge does NOT resolve, distinguish a REQUIRED, supported-but-missing
   ATS concept that is truthfully PLACEABLE in an editable field from one
   that is PROVEN unplaceable (every evidence hit belongs to protected/
   non-editable content), so the latter can never consume a bounded
   correction round it could never truthfully satisfy.

Covers:
  1-4.   The "hybrid search" bridge: matches truthful narrow variants,
         does not match unrelated generic "search", checked-in fixture
         regression, full offline pipeline replay (zero-model-call,
         end-to-end).
  5-11.  evidence_ownership.py: owner resolution is structural (source
         path + file), editable-owner rules are exact and 100% data-driven
         off `tailoring_mode` (no substring/fuzzy match, no hardcoded
         employer/project allowlist), placeable/unplaceable/unknown
         classification, protected evidence is never treated as editable
         regardless of wording.
  12-13. Unplaceable required concept never triggers a correction round;
         placeable required concept still does (real, full pipeline runs).
  14.    Unsupported concept remains unsupported; optional supported
         concept remains non-blocking (both via the checked-in fixture
         artifact).
  15.    Existing bridge behavior (agentic workflows, etc.) is untouched.
  16.    Active production config is a valid, current V8.6.x PRODUCTION
         release.

Run with:
    python3 tools/resume/v86/tests/test_v86_6_ats_bridge_and_unplaceable_evidence.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ats_preflight_v86 as ats86  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import evidence_ownership as eo  # noqa: E402
from common import ROOT, load_json, write_json  # noqa: E402

ALIASES_PATH = ROOT / "config/ats_aliases.json"
BRIDGES_PATH = ROOT / "config/v86/ats_concept_bridges.json"
# These two constants point at an immutable, checked-in fixture (fictional
# "Candidate Example" dataset) shaped exactly like the real batch-job
# artifacts this fix was written against -- never the shared, mutable
# `output/<job_key>/` job-output folder, which any unrelated LATER batch run
# targeting that same job_key could silently overwrite (see
# tests/fixtures/live13_11_recommendation/README.md).
LIVE1311_DIR = Path(__file__).resolve().parent / "fixtures" / "live13_11_recommendation"
LIVE1311_EVIDENCE_ROOT = LIVE1311_DIR / "shared_ats_evidence"

# This fixture holds the ORIGINAL, pre-"hybrid search"-bridge "Hybrid
# search" evidence_hits (i.e. the scenario evidence_ownership.py/
# split_missing_required_by_placeability were written to fix). It is kept
# separate from LIVE1311_DIR because that fixture's own ats_preflight.json
# is intentionally the POST-fix, already-SUPPORTED_AND_PRESENT snapshot --
# see tests/fixtures/v866_unplaceable_evidence/README.md.
V866_UNPLACEABLE_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "v866_unplaceable_evidence"


def _hybrid_search_concept():
    return {"term": "Hybrid search", "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high"}


def _merged_aliases():
    return ats86.load_and_merge_aliases(ALIASES_PATH, BRIDGES_PATH)


def _load_v866_unplaceable_fixture() -> dict:
    """Load the immutable pre-fix "Hybrid search" ats_preflight.json-shaped
    fixture, substituting the literal `{EVIDENCE_FILE}` placeholder with
    this fixture directory's own `evidence.json` absolute path (keeping the
    fixture JSON itself host-independent)."""
    template = (V866_UNPLACEABLE_FIXTURE_DIR / "ats_preflight_hybrid_search_unplaceable.json").read_text()
    evidence_path = V866_UNPLACEABLE_FIXTURE_DIR / "evidence.json"
    return json.loads(template.replace("{EVIDENCE_FILE}", str(evidence_path)))


# ---------------------------------------------------------------------------
# 1-4. The "hybrid search" bridge
# ---------------------------------------------------------------------------

class TestHybridSearchBridgeConfig(unittest.TestCase):
    def test_bridge_key_present_and_narrow(self):
        bridges = json.loads(BRIDGES_PATH.read_text())
        self.assertIn("hybrid search", bridges)
        forms = bridges["hybrid search"]
        for form in forms:
            self.assertIn("hybrid", form.lower(), f"{form!r} is not an explicit hybrid-prefixed phrase")
        # Narrow: no bare "search" and no bare "hybrid" alone in the bridge list.
        self.assertNotIn("search", [f.strip().lower() for f in forms])
        self.assertNotIn("hybrid", [f.strip().lower() for f in forms])

    def test_bridge_matches_hybrid_dense_sparse_retrieval_variant(self):
        requirements = {"job_key": "synthetic", "concepts": [_hybrid_search_concept()]}
        resume_text = "- Built a recommender using hybrid dense+sparse retrieval and reranking."
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            write_json(evidence_root / "evidence.json", {
                "projects": [{"name": "Synthetic Project", "canonical_bullets": [
                    "Built a recommender using hybrid dense+sparse retrieval and reranking.",
                ]}],
            })
            report = ats86.evaluate(requirements, resume_text, evidence_root, _merged_aliases())
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "SUPPORTED_AND_PRESENT")
        self.assertIn("Hybrid search", report["supported_and_present"])

    def test_bridge_does_not_match_unrelated_generic_search(self):
        requirements = {"job_key": "synthetic", "concepts": [_hybrid_search_concept()]}
        resume_text = "- Implemented a full-text search feature for the internal wiki."
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            write_json(evidence_root / "evidence.json", {
                "projects": [{"name": "Unrelated Project", "canonical_bullets": [
                    "Implemented a full-text search feature for the internal wiki.",
                    "Added a search bar to the marketing site.",
                ]}],
            })
            report = ats86.evaluate(requirements, resume_text, evidence_root, _merged_aliases())
        concept = report["concepts"][0]
        self.assertEqual(concept["status"], "UNSUPPORTED")
        self.assertEqual(concept["evidence_hits"], [])
        self.assertNotIn("Hybrid search", report["supported_and_present"])
        self.assertNotIn("Hybrid search", report["supported_but_missing"])


class TestLive1311FixtureBridgeRegression(unittest.TestCase):
    """Uses the checked-in, already-saved fixture artifacts (fictional
    "Candidate Example" dataset) -- zero model calls, this data ships in
    the repo."""

    def setUp(self):
        if not (LIVE1311_DIR / "jd_requirements.json").exists() or not LIVE1311_EVIDENCE_ROOT.exists():
            self.skipTest("live13-11 fixture artifacts not present")
        self.requirements = load_json(LIVE1311_DIR / "jd_requirements.json")
        self.resume_text = (LIVE1311_DIR / "resume_draft.md").read_text(encoding="utf-8")

    def _evaluate(self):
        return ats86.evaluate(self.requirements, self.resume_text, LIVE1311_EVIDENCE_ROOT, _merged_aliases())

    def test_hybrid_search_now_supported_and_present(self):
        report = self._evaluate()
        by_term = {c["term"]: c for c in report["concepts"]}
        self.assertEqual(by_term["Hybrid search"]["status"], "SUPPORTED_AND_PRESENT")
        self.assertIn("Hybrid search", report["supported_and_present"])
        self.assertNotIn("Hybrid search", report["supported_but_missing_required"])

    def test_gate_now_passes(self):
        self.assertTrue(self._evaluate()["gate_pass"])

    def test_semantic_recommendation_systems_still_unsupported(self):
        # 14. Desired result: unsupported concepts are untouched by this pass.
        report = self._evaluate()
        by_term = {c["term"]: c for c in report["concepts"]}
        self.assertEqual(by_term["Semantic recommendation systems"]["status"], "UNSUPPORTED")
        self.assertIn("Semantic recommendation systems", report["unsupported"])

    def test_sentence_transformers_still_optional_and_nonblocking(self):
        # 14. Desired result: an optional supported-missing concept must
        # never itself block readiness.
        report = self._evaluate()
        by_term = {c["term"]: c for c in report["concepts"]}
        self.assertFalse(by_term["Sentence Transformers"]["required"])
        self.assertEqual(by_term["Sentence Transformers"]["status"], "SUPPORTED_BUT_MISSING")
        self.assertIn("Sentence Transformers", report["optional_supported_but_missing"])
        self.assertTrue(report["gate_pass"], "an optional missing concept must never fail the gate")


class TestLive1311FullOfflinePipelineReplay(unittest.TestCase):
    """Runs the real deterministic pipeline (frozen render/QA/ATS
    subprocesses; zero Claude/Apify calls) against a scratch copy of the
    checked-in live13-11 fixture artifacts, proving the fix end-to-end, not
    just at the ats_preflight_v86.evaluate() level."""

    def setUp(self):
        needed = [
            "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json",
            "project_selection.json", "v8_contract_validation.json",
            "v8_project_selection_validation.json", "payload_normalization.json",
            "payload_preflight.json", "jd_requirements.json",
        ]
        if not all((LIVE1311_DIR / n).exists() for n in needed) or not LIVE1311_EVIDENCE_ROOT.exists():
            self.skipTest("live13-11 fixture artifacts not present")
        # job_dir must live under ROOT, like every real output/<job_key>/
        # dir -- run_job_pipeline's correction-request path calls
        # req.relative_to(ROOT).
        self.job_dir = ROOT / "output/v868-test-live1311-round5"
        shutil.rmtree(self.job_dir, ignore_errors=True)
        self.job_dir.mkdir(parents=True)
        for name in needed:
            shutil.copy(LIVE1311_DIR / name, self.job_dir / name)

    def tearDown(self):
        shutil.rmtree(self.job_dir, ignore_errors=True)

    def test_job_reaches_a_terminal_success_status_with_no_unplaceable_fallback(self):
        result = detp.run_job_pipeline(self.job_dir, LIVE1311_EVIDENCE_ROOT, "live13-11-recommendation")
        self.assertIn(result["status"], (
            "RESUME_READY", "RESUME_READY_WITH_WARNINGS", "NEEDS_LLM_CORRECTION",
        ), result)
        self.assertNotEqual(result["status"], "CORRECTION_LIMIT_EXCEEDED", result)
        self.assertNotIn("ats_unplaceable_required", result, "the bridge alone must resolve this job -- no fallback needed")
        if result["status"] == "NEEDS_LLM_CORRECTION":
            request = json.loads((self.job_dir / "correction_request.json").read_text())
            self.assertNotIn("ATS", request["reasons"], "the hybrid-search bridge must resolve ATS with no correction round")


# ---------------------------------------------------------------------------
# 5-11. evidence_ownership.py
# ---------------------------------------------------------------------------

def _ownership_fixture_dict() -> dict:
    """Deliberately omits `id` on every entry, exercising
    `resolve_owner_entry`'s documented fallback to `employer`/`name` "for
    resilience against older evidence documents"."""
    return {
        "experience": [
            {"employer": "Example Data Systems", "tailoring_mode": "tailorable",
             "alternate_bullets_for_dynamic_tailoring": ["x"]},
            {"employer": "Example Research Lab", "tailoring_mode": "protected", "canonical_bullets": ["y"]},
        ],
        "projects": [
            {"name": "Project Atlas", "tailoring_mode": "protected", "canonical_bullets": ["p"]},
            {"name": "Project Beacon", "tailoring_mode": "protected", "canonical_bullets": ["g"],
             "alternate_facts_for_dynamic_tailoring": ["LangGraph nodes for query parsing, hybrid search, reranking"]},
            {"name": "Project Meridian", "tailoring_mode": "tailorable", "bullets": ["v"]},
            {"name": "Project Solstice", "tailoring_mode": "tailorable", "bullets": ["e"]},
        ],
    }


class TestEvidenceOwnershipResolution(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.evidence_path = Path(self._tmp.name) / "evidence.json"
        self.evidence_path.write_text(json.dumps(_ownership_fixture_dict()))

    def tearDown(self):
        self._tmp.cleanup()

    def _hit(self, source):
        return {"file": str(self.evidence_path), "source": source}

    def test_resolve_owner_from_experience_entry(self):
        self.assertEqual(eo.resolve_owner(self._hit("$.experience[0].alternate_bullets_for_dynamic_tailoring[0]")), "Example Data Systems")
        self.assertEqual(eo.resolve_owner(self._hit("$.experience[1].canonical_bullets[0]")), "Example Research Lab")

    def test_resolve_owner_from_project_entry(self):
        self.assertEqual(eo.resolve_owner(self._hit("$.projects[1].alternate_facts_for_dynamic_tailoring[0]")), "Project Beacon")
        self.assertEqual(eo.resolve_owner(self._hit("$.projects[2].bullets[0]")), "Project Meridian")

    def test_resolve_owner_none_for_malformed_source(self):
        self.assertIsNone(eo.resolve_owner(self._hit("$.skills.static_resume_lines.Languages")))
        self.assertIsNone(eo.resolve_owner({"file": str(self.evidence_path), "source": ""}))
        self.assertIsNone(eo.resolve_owner({"file": None, "source": "$.experience[0].canonical_bullets[0]"}))

    def test_resolve_owner_none_for_out_of_range_index(self):
        self.assertIsNone(eo.resolve_owner(self._hit("$.experience[9].canonical_bullets[0]")))

    def test_resolve_owner_none_for_missing_file(self):
        self.assertIsNone(eo.resolve_owner({"file": "/nonexistent/evidence.json", "source": "$.experience[0].canonical_bullets[0]"}))


class TestEditableOwnerRules(unittest.TestCase):
    BOUND = "Project Meridian"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.evidence_path = Path(self._tmp.name) / "evidence.json"
        self.evidence_path.write_text(json.dumps(_ownership_fixture_dict()))

    def tearDown(self):
        self._tmp.cleanup()

    def _owner(self, source):
        return eo.resolve_owner_entry({"file": str(self.evidence_path), "source": source})

    def test_tailorable_experience_is_editable(self):
        self.assertTrue(eo.is_editable_owner(self._owner("$.experience[0].alternate_bullets_for_dynamic_tailoring[0]"), self.BOUND))

    def test_bound_selectable_project_is_editable(self):
        self.assertTrue(eo.is_editable_owner(self._owner("$.projects[2].bullets[0]"), self.BOUND))

    def test_protected_owners_are_never_editable(self):
        for source in (
            "$.experience[1].canonical_bullets[0]",
            "$.projects[0].canonical_bullets[0]",
            "$.projects[1].canonical_bullets[0]",
        ):
            self.assertFalse(eo.is_editable_owner(self._owner(source), self.BOUND), source)

    def test_unbound_selectable_project_candidate_is_not_editable(self):
        # 6. Evidence ownership stays project-specific: a real selectable-
        # project CANDIDATE that is NOT the one actually bound to this job
        # is not editable, even though it is a legitimate candidate in
        # general.
        self.assertFalse(eo.is_editable_owner(self._owner("$.projects[3].bullets[0]"), self.BOUND))

    def test_no_owner_entry_is_not_editable(self):
        self.assertFalse(eo.is_editable_owner(None, self.BOUND))

    def test_no_substring_or_fuzzy_match(self):
        # 7. "protected evidence cannot be copied to editable fields":
        # proves the match is exact, never a substring or partial-name
        # match that a careless heuristic might allow.
        near_miss = {"container": "projects", "owner_id": "Project Meridian (Legacy)", "tailoring_mode": "tailorable"}
        self.assertFalse(eo.is_editable_owner(near_miss, self.BOUND))
        partial = {"container": "projects", "owner_id": "Meridian", "tailoring_mode": "tailorable"}
        self.assertFalse(eo.is_editable_owner(partial, self.BOUND))
        # A project with the bound id but NOT itself tailorable (defense in
        # depth) is still never editable.
        mislabeled = {"container": "projects", "owner_id": self.BOUND, "tailoring_mode": "protected"}
        self.assertFalse(eo.is_editable_owner(mislabeled, self.BOUND))


class TestClassifyMissingRequired(unittest.TestCase):
    BOUND = "Project Meridian"

    def test_unknown_when_no_hits(self):
        self.assertEqual(eo.classify_missing_required([], self.BOUND), "unknown")

    def test_unknown_when_owner_unresolvable(self):
        hits = [{"file": "/nonexistent.json", "source": "$.experience[0].canonical_bullets[0]"}]
        self.assertEqual(eo.classify_missing_required(hits, self.BOUND), "unknown")

    def test_placeable_when_any_hit_is_a_tailorable_experience(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.json"
            write_json(path, {"experience": [{"employer": "Example Data Systems", "tailoring_mode": "tailorable",
                                               "alternate_bullets_for_dynamic_tailoring": ["a"]}]})
            hits = [{"file": str(path), "source": "$.experience[0].alternate_bullets_for_dynamic_tailoring[0]"}]
            self.assertEqual(eo.classify_missing_required(hits, self.BOUND), "placeable")

    def test_placeable_when_any_hit_is_bound_selectable_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.json"
            write_json(path, {"projects": [{"name": self.BOUND, "tailoring_mode": "tailorable", "bullets": ["a"]}]})
            hits = [{"file": str(path), "source": "$.projects[0].bullets[0]"}]
            self.assertEqual(eo.classify_missing_required(hits, self.BOUND), "placeable")

    def test_unplaceable_when_every_hit_is_protected(self):
        # 8. Real root-cause data shape: this is exactly what the pre-fix
        # "Hybrid search" evidence hit looked like -- proves the fallback
        # would have worked even without the ATS bridge.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.json"
            write_json(path, {"projects": [
                {"name": "Project Atlas", "tailoring_mode": "protected", "canonical_bullets": ["p"]},
                {"name": "Project Beacon", "tailoring_mode": "protected",
                 "alternate_facts_for_dynamic_tailoring": ["hybrid search"]},
            ]})
            hits = [
                {"file": str(path), "source": "$.projects[0].canonical_bullets[0]"},
                {"file": str(path), "source": "$.projects[1].alternate_facts_for_dynamic_tailoring[0]"},
            ]
            self.assertEqual(eo.classify_missing_required(hits, self.BOUND), "unplaceable")

    def test_unplaceable_requires_every_hit_resolved_not_just_one(self):
        # A single unresolved hit forces "unknown" (safe default), even if
        # every OTHER hit is protected.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "e.json"
            write_json(path, {"projects": [{"name": "Project Beacon", "tailoring_mode": "protected", "canonical_bullets": ["p"]}]})
            hits = [
                {"file": str(path), "source": "$.projects[0].canonical_bullets[0]"},
                {"file": "/nonexistent.json", "source": "$.projects[0].canonical_bullets[0]"},
            ]
            self.assertEqual(eo.classify_missing_required(hits, self.BOUND), "unknown")


class TestLive1311OldArtifactWouldHaveBeenClassifiedUnplaceable(unittest.TestCase):
    """Uses the immutable, hand-built reconstruction of the pre-fix
    "Hybrid search" evidence_hits (a protected project's non-canonical
    alternate-facts bucket) to prove Task B's fallback alone -- with no
    bridge at all -- would already have prevented the
    CORRECTION_LIMIT_EXCEEDED failure. See tests/fixtures/
    v866_unplaceable_evidence/README.md.
    """

    def setUp(self):
        self.old_ats = _load_v866_unplaceable_fixture()

    def test_only_the_alternate_fact_hit_resolves_and_it_is_unplaceable(self):
        by_term = {c["term"]: c for c in self.old_ats["concepts"]}
        hits = by_term["Hybrid search"]["evidence_hits"]
        # Reconstructed pre-fix data: exactly the protected Project Beacon's
        # alternate_facts_for_dynamic_tailoring bucket, never a tailorable
        # experience entry, never the bound selectable project.
        classification = eo.classify_missing_required(hits, bound_project_id="Project Meridian")
        self.assertEqual(classification, "unplaceable")


# ---------------------------------------------------------------------------
# 12-13. Full-pipeline proof: unplaceable never corrects, placeable still does
# ---------------------------------------------------------------------------

class TestUnplaceableVsPlaceableFullPipeline(unittest.TestCase):
    """Builds a synthetic REQUIRED concept with NO bridge at all (so this
    test is independent of the "hybrid search" bridge) whose only evidence
    is a single fabricated bullet, and runs it through the real
    deterministic pipeline (frozen render/QA/ATS subprocesses; zero Claude
    calls) -- once owned by a protected experience entry, once owned by a
    tailorable one -- to prove the placeable/unplaceable split changes the
    actual pipeline outcome, not just the classifier's return value."""

    NEEDED = [
        "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json",
        "project_selection.json", "v8_contract_validation.json",
        "v8_project_selection_validation.json", "payload_normalization.json",
        "payload_preflight.json", "jd_requirements.json",
    ]

    def setUp(self):
        if not all((LIVE1311_DIR / n).exists() for n in self.NEEDED) or not LIVE1311_EVIDENCE_ROOT.exists():
            self.skipTest("live13-11 fixture artifacts not present")
        self._tmp = tempfile.TemporaryDirectory()
        self._job_dirs = []

    def tearDown(self):
        self._tmp.cleanup()
        for d in self._job_dirs:
            shutil.rmtree(d, ignore_errors=True)

    def _build_job(self, slug: str, extra_owner_employer: str, tailoring_mode: str):
        # job_dir must live under ROOT (like every real output/<job_key>/
        # dir) -- run_job_pipeline's correction-request path calls
        # req.relative_to(ROOT); a tempdir entirely outside ROOT is not a
        # case production ever hits, so it is not exercised here.
        job_dir = ROOT / f"output/v866-test-synthetic-{slug}"
        shutil.rmtree(job_dir, ignore_errors=True)
        job_dir.mkdir(parents=True)
        self._job_dirs.append(job_dir)
        for name in self.NEEDED:
            shutil.copy(LIVE1311_DIR / name, job_dir / name)

        jd = load_json(job_dir / "jd_requirements.json")
        jd["concepts"].append({
            "term": "Zzz Synthetic Required Skill",
            "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high",
        })
        write_json(job_dir / "jd_requirements.json", jd)

        evidence_dir = Path(self._tmp.name) / f"evidence-{slug}"
        evidence_dir.mkdir()
        shutil.copy(
            LIVE1311_EVIDENCE_ROOT / "verified_resume_evidence.json",
            evidence_dir / "verified_resume_evidence.json",
        )
        write_json(evidence_dir / "extra_evidence.json", {
            "experience": [{
                "employer": extra_owner_employer,
                "tailoring_mode": tailoring_mode,
                "canonical_bullets": ["Demonstrated the Zzz Synthetic Required Skill in production."],
            }],
        })
        return job_dir, evidence_dir

    def test_unplaceable_synthetic_concept_never_triggers_correction(self):
        # protected experience entry -- never editable.
        job_dir, evidence_dir = self._build_job("unplaceable", "Example Research Lab", "protected")
        result = detp.run_job_pipeline(job_dir, evidence_dir, "v866-synthetic-unplaceable")
        self.assertEqual(result.get("ats_unplaceable_required"), ["Zzz Synthetic Required Skill"], result)
        self.assertNotEqual(result["status"], "CORRECTION_LIMIT_EXCEEDED", result)
        # What this test actually proves -- that the unplaceable ATS
        # concept itself is NEVER the trigger for a correction round -- is
        # asserted directly against correction_request.json's own reasons
        # (if any correction round happens at all, e.g. for an unrelated
        # STYLE/DENSITY finding), not the job's overall terminal status.
        if result["status"] == "NEEDS_LLM_CORRECTION":
            request = load_json(job_dir / "correction_request.json")
            self.assertNotIn("ATS", request["reasons"])
        else:
            self.assertIn(result["status"], ("RESUME_READY", "RESUME_READY_WITH_WARNINGS"), result)
            self.assertIsNone(result.get("correction_request"))

    def test_placeable_synthetic_concept_still_triggers_correction(self):
        # tailorable experience entry -- editable.
        job_dir, evidence_dir = self._build_job("placeable", "Example Data Systems", "tailorable")
        result = detp.run_job_pipeline(job_dir, evidence_dir, "v866-synthetic-placeable")
        self.assertNotIn("ats_unplaceable_required", result, result)
        self.assertEqual(result["status"], "NEEDS_LLM_CORRECTION", result)
        self.assertIsNotNone(result.get("correction_request"))
        request = load_json(job_dir / "correction_request.json")
        self.assertIn("ATS", request["reasons"])
        self.assertIn("Zzz Synthetic Required Skill", request["details"]["ats"]["supported_but_missing"])


# ---------------------------------------------------------------------------
# split_missing_required_by_placeability: pure-function unit coverage
# ---------------------------------------------------------------------------

class TestSplitMissingRequiredByPlaceability(unittest.TestCase):
    def test_split_matches_classify_missing_required_per_term(self):
        old_ats = _load_v866_unplaceable_fixture()
        placeable, unplaceable = detp.split_missing_required_by_placeability(old_ats, "Project Meridian")
        self.assertEqual(placeable, [])
        self.assertEqual(unplaceable, ["Hybrid search"])

    def test_empty_missing_list_yields_empty_split(self):
        placeable, unplaceable = detp.split_missing_required_by_placeability(
            {"missing_supported_required_terms": [], "concepts": []}, None,
        )
        self.assertEqual((placeable, unplaceable), ([], []))


# ---------------------------------------------------------------------------
# 15. Existing bridge behavior untouched
# ---------------------------------------------------------------------------

class TestExistingBridgesUntouched(unittest.TestCase):
    def test_agentic_workflows_bridge_still_matches(self):
        concept = {"term": "agentic workflows", "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high"}
        requirements = {"job_key": "synthetic", "concepts": [concept]}
        resume_text = "- Built an agentic loop with LangGraph for retrieval and grading."
        with tempfile.TemporaryDirectory() as tmp:
            evidence_root = Path(tmp)
            write_json(evidence_root / "evidence.json", {"projects": [{"name": "X", "canonical_bullets": [
                "Built an agentic loop with LangGraph for retrieval and grading.",
            ]}]})
            report = ats86.evaluate(requirements, resume_text, evidence_root, _merged_aliases())
        self.assertEqual(report["concepts"][0]["status"], "SUPPORTED_AND_PRESENT")


# ---------------------------------------------------------------------------
# 16. Active production config
# ---------------------------------------------------------------------------
#
# This public repo ships no `backups/` directory and no
# `verify_frozen_v85.py` script (both are development-time-only artifacts,
# per `config/active_resume_pipeline.json`'s own `_comment`), so the
# byte-diff-against-frozen-snapshot checks that used to live in this suite
# have no reusable baseline here and are removed rather than left
# permanently failing -- matching the pattern already applied in
# test_v86_7_ats_bridge_reciprocal_rank_fusion.py.

class TestActiveProductionConfig(unittest.TestCase):
    def test_active_production_config_is_valid(self):
        active = json.loads((ROOT / "config/active_resume_pipeline.json").read_text())
        self.assertEqual(active["status"], "PRODUCTION")
        self.assertTrue(str(active["active_version"]).startswith("V8.6"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
