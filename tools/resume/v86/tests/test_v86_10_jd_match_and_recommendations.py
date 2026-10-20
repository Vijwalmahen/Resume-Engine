#!/usr/bin/env python3
"""
V8.6.10 -- job-description matching, evidence-gated recommendations, and
render fidelity.

ZERO model calls, ZERO network, ZERO paid APIs, ZERO reads of private
candidate data, ZERO writes outside a temporary directory. Every
candidate, employer, project, metric and job description below is wholly
synthetic and written inline.

A deliberate non-goal
---------------------

No test here asserts that any resume reaches any score. External ATS
scores belong to external services; a test that demanded 85% would be
pinning this repository to a number it cannot compute and did not earn,
and would reward whatever made the number move rather than whatever made
the output truthful. What IS pinned is behaviour: that matches are
classified the way the evidence justifies, that fabricated numbers are
refused, that verified numbers survive, and that rendering leaves the
design alone.

Sections

  A. Requirement matching (jd_match_report)
     A1  exact keyword match, on the job description's own wording
     A2  defensible synonym resolves, and names the bridge it used
     A3  a required qualification with no evidence is a reported gap
     A4  required and preferred are counted separately, never merged
     A5  an unsupported skill asserted by the resume is ambiguous,
         not a win
     A6  evidence that contradicts itself is ambiguous, not supported
     A7  evidence the resume fails to surface is an opportunity
     A8  no composite score is produced anywhere

  B. Recommendation guard
     B1  the ToolAdda "30% increase in output" case is REJECTED
     B2  a measurement the cited evidence does state is accepted
     B3  a recommendation for a JD-only skill is rejected
     B4  an uncited recommendation is rejected
     B5  an ambiguous requirement yields human review, not acceptance
     B6  no verdict path can silently accept an ungrounded metric

  C. Preservation of verified achievements
     C1  verified measurements survive matching and guarding unchanged
     C2  the guard never rewrites or drops a candidate's own numbers

  D. Render fidelity and layout
     D1  rendering a template changes its words, not its typography
     D2  a non-Calibri template keeps its own font and sizes
     D3  font-family drift is detected
     D4  paragraph-level spacing/indent drift is detected
     D5  lost section-heading borders and bullet styles are detected

  E. Rendered-PDF integrity
     E1  text extraction and reading order on a real rendered PDF
     E2  excessive bottom whitespace is measured and reported
     E3  an unexpected second page is detected

  F. Compatibility
     F1  the consolidated report still builds with the new sections
     F2  absent optional sections never degrade the report to INCOMPLETE
     F3  the ATS gate's pre-existing contract is unchanged
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
V86 = ROOT / "tools" / "resume" / "v86"
sys.path.insert(0, str(V86))
sys.path.insert(0, str(ROOT / "tools" / "ats"))
sys.path.insert(0, str(ROOT / "tools" / "resume"))

import preflight as frozen  # noqa: E402
import ats_preflight_v86  # noqa: E402
import jd_match_report  # noqa: E402
import recommendation_guard  # noqa: E402
import resume_quality_report  # noqa: E402
import template_style_audit  # noqa: E402


# ---------------------------------------------------------------------------
# synthetic fixtures -- nothing here describes a real person
# ---------------------------------------------------------------------------

SYNTHETIC_EVIDENCE = {
    "facts": [
        {"fact": "built a retrieval service over a 50k-document corpus using hybrid dense+sparse retrieval"},
        {"fact": "reduced p95 query latency from 480ms to 190ms by adding an approximate nearest-neighbor index"},
        {"fact": "implements prompt-injection, scope, and PII checks as guardrails"},
        {"fact": "implements groundedness and citation validation for generated answers"},
        {"fact": "improved offline NDCG@10 from 0.41 to 0.53 through hyperparameter tuning"},
        {"fact": "uses held-out and expanding-window validation, never random cross-validation"},
        {"fact": "serves the prototype with FastAPI and Redis-backed caching"},
    ]
}

SYNTHETIC_RESUME = """Example Candidate
candidate@example.com | (555) 010-2000

EDUCATION

Example University -- M.S. Artificial Intelligence
GPA: 3.80/4.00 | Aug 2024 -- May 2026

PROFESSIONAL EXPERIENCE

AI Engineer Intern | Example Data Systems | Remote\tJun 2025 -- Aug 2025
- Built a retrieval service over a 50k-document corpus using hybrid dense+sparse retrieval.
- Reduced p95 query latency from 480ms to 190ms with an approximate nearest-neighbor index.
- Added prompt-injection, scope, and PII checks as guardrails.

PROJECTS

Project Beacon -- Recommendation Prototype
- Improved offline NDCG@10 from 0.41 to 0.53 through hyperparameter tuning.

SKILLS

Languages: Python, SQL
Backend/API: FastAPI, MongoDB, Redis
"""


def write_evidence(directory: Path) -> Path:
    root = directory / "evidence"
    root.mkdir(parents=True, exist_ok=True)
    (root / "evidence_bank.json").write_text(
        json.dumps(SYNTHETIC_EVIDENCE, indent=2), encoding="utf-8"
    )
    return root


def aliases():
    return ats_preflight_v86.load_and_merge_aliases(
        ROOT / "config" / "ats_aliases.json",
        ROOT / "config" / "v86" / "ats_concept_bridges.json",
    )


def match(concepts, resume=SYNTHETIC_RESUME, evidence_root=None, tmp=None):
    """Run the real matcher over synthetic inputs."""
    owns_tmp = tmp is None
    if owns_tmp:
        tmp = tempfile.mkdtemp()
    root = evidence_root or write_evidence(Path(tmp))
    return jd_match_report.build_report(
        {"job_key": "synthetic-job", "concepts": concepts},
        resume,
        root,
        aliases(),
    )


def row_for(report, term):
    for row in report["requirements"]:
        if row["term"] == term:
            return row
    raise AssertionError(f"term {term!r} not in report")


# ===========================================================================
# A. Requirement matching
# ===========================================================================

class TestRequirementMatching(unittest.TestCase):

    def test_a1_exact_keyword_match_on_jd_wording(self):
        report = match([
            {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "high"},
        ])
        row = row_for(report, "FastAPI")
        self.assertEqual(row["match_class"], "exact")
        self.assertEqual(row["match_basis"], "literal_jd_wording")
        self.assertEqual(row["matched_form"], "FastAPI")
        self.assertEqual(row["evidence_support"], "supported")

    def test_a2_defensible_synonym_resolves_and_names_its_bridge(self):
        # The resume never says "hybrid search"; it says "hybrid
        # dense+sparse retrieval". The bridge must resolve it AND say so.
        self.assertNotIn("hybrid search", SYNTHETIC_RESUME.lower())
        report = match([
            {"term": "Hybrid search", "category": "CORE_RESPONSIBILITY",
             "required": True, "priority": "high"},
        ])
        row = row_for(report, "Hybrid search")
        self.assertEqual(row["match_class"], "semantic")
        self.assertEqual(row["match_basis"], "reviewed_alias_or_bridge")
        self.assertIsNotNone(row["matched_form"])
        # The bridging phrase is disclosed, so a reviewer can reject it.
        self.assertIn("hybrid", row["matched_form"].lower())
        self.assertIn(row["matched_form"], row["bridged_forms_checked"])

    def test_a2b_exact_takes_precedence_over_a_bridge(self):
        report = match([
            {"term": "Redis", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "medium"},
        ])
        self.assertEqual(row_for(report, "Redis")["match_class"], "exact")

    def test_a3_missing_required_qualification_is_a_reported_gap(self):
        report = match([
            {"term": "Kubernetes", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "critical"},
        ])
        row = row_for(report, "Kubernetes")
        self.assertEqual(row["match_class"], "none")
        self.assertEqual(row["evidence_support"], "unsupported")
        self.assertFalse(row["opportunity"])

    def test_a4_required_and_preferred_are_counted_separately(self):
        report = match([
            {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "high"},
            {"term": "Kubernetes", "category": "PREFERRED_HARD_SKILL",
             "required": False, "priority": "low"},
        ])
        summary = report["summary"]
        self.assertEqual(summary["required"]["total"], 1)
        self.assertEqual(summary["preferred"]["total"], 1)
        self.assertEqual(summary["required"]["exact"], 1)
        self.assertEqual(summary["preferred"]["none"], 1)
        # and the two buckets are never added together into one verdict
        self.assertNotIn("score", summary)
        self.assertNotIn("match_rate", summary)

    def test_a4b_required_outranks_preferred_in_reading_order(self):
        report = match([
            {"term": "Kubernetes", "category": "PREFERRED_HARD_SKILL",
             "required": False, "priority": "low"},
            {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "critical"},
        ])
        self.assertEqual(report["requirements"][0]["term"], "FastAPI")

    def test_a5_resume_claim_without_evidence_is_ambiguous_not_a_win(self):
        # "MongoDB" is on the synthetic skills line but in no evidence
        # record. A keyword report would score this as covered; it is not.
        self.assertIn("MongoDB", SYNTHETIC_RESUME)
        report = match([
            {"term": "MongoDB", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "high"},
        ])
        row = row_for(report, "MongoDB")
        self.assertEqual(row["match_class"], "ambiguous")
        self.assertEqual(row["resume_match_class"], "exact")
        self.assertEqual(row["evidence_support"], "unsupported")
        self.assertIn("no master evidence", row["ambiguity_reason"])
        self.assertFalse(row["opportunity"])

    def test_a5b_a_broader_concept_bridged_to_an_unevidenced_instance_stays_ambiguous(self):
        # A bridge may change which phrases are searched for. It must not
        # be able to manufacture evidence support that does not exist.
        report = match([
            {"term": "NoSQL", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "high"},
        ])
        row = row_for(report, "NoSQL")
        self.assertEqual(row["evidence_support"], "unsupported")
        self.assertEqual(row["match_class"], "ambiguous")

    def test_a6_contradictory_evidence_is_ambiguous_not_supported(self):
        # The evidence says cross-validation was NEVER used.
        report = match([
            {"term": "cross-validation", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "medium"},
        ])
        row = row_for(report, "cross-validation")
        self.assertEqual(row["evidence_support"], "negated_only")
        self.assertEqual(row["match_class"], "ambiguous")
        self.assertTrue(row["negated_evidence_hits_excluded"])
        self.assertFalse(row["opportunity"])

    def test_a7_supported_evidence_the_resume_omits_is_an_opportunity(self):
        # Citation validation is in the evidence bank but not the resume.
        self.assertNotIn("citation validation", SYNTHETIC_RESUME.lower())
        report = match([
            {"term": "citation validation", "category": "CORE_RESPONSIBILITY",
             "required": True, "priority": "high"},
        ])
        row = row_for(report, "citation validation")
        self.assertEqual(row["evidence_support"], "supported")
        self.assertIn(row["match_class"], ("none", "partial"))
        self.assertTrue(row["opportunity"])

    def test_a7b_partial_token_overlap_is_partial_not_exact(self):
        report = match([
            {"term": "semantic recommendation systems",
             "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high"},
        ])
        row = row_for(report, "semantic recommendation systems")
        # Exactly `partial`: the resume has "recommendation" (Project
        # Beacon) but neither "semantic" nor the full phrase, so coverage
        # must be strictly between nothing and everything.
        self.assertEqual(row["match_class"], "partial")
        self.assertEqual(row["match_basis"], "content_word_overlap_only")
        self.assertIsNone(row["matched_form"])
        coverage = row["token_coverage"]
        self.assertGreater(coverage["coverage"], 0.0)
        self.assertLess(coverage["coverage"], 1.0)
        self.assertIn("recommendation", coverage["present"])
        self.assertIn("semantic", coverage["missing"])

    def test_a8_no_composite_score_is_produced_anywhere(self):
        report = match([
            {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "high"},
            {"term": "Kubernetes", "category": "PREFERRED_HARD_SKILL",
             "required": False, "priority": "low"},
        ])
        blob = json.dumps(report).lower()
        for forbidden in ('"score"', '"overall_score"', '"match_rate"',
                          '"ats_score"', '"total_score"', '"weight"'):
            self.assertNotIn(forbidden, blob, f"{forbidden} must not appear")
        self.assertTrue(report["advisory_only"])

    def test_a8b_presence_is_never_reported_as_proficiency(self):
        report = match([
            {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
             "required": True, "priority": "high"},
        ])
        row = row_for(report, "FastAPI")
        self.assertIsNone(row["proficiency_claim"])
        self.assertIn("not evidence of proficiency", row["proficiency_note"])


# ===========================================================================
# B. Recommendation guard
# ===========================================================================

class TestRecommendationGuard(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = write_evidence(Path(self.tmp))
        self.corpus = recommendation_guard.load_evidence_corpus(self.root)
        self.match = match(
            [
                {"term": "Hybrid search", "category": "CORE_RESPONSIBILITY",
                 "required": True, "priority": "high"},
                {"term": "Kubernetes", "category": "REQUIRED_HARD_SKILL",
                 "required": True, "priority": "high"},
                {"term": "MongoDB", "category": "REQUIRED_HARD_SKILL",
                 "required": True, "priority": "high"},
                {"term": "cross-validation", "category": "REQUIRED_HARD_SKILL",
                 "required": True, "priority": "medium"},
            ],
            evidence_root=self.root,
            tmp=self.tmp,
        )

    def review(self, recommendation):
        return recommendation_guard.review_recommendation(
            recommendation, self.corpus,
            {r["term"]: r for r in self.match["requirements"]},
        )

    GROUNDED_CITATION = (
        "reduced p95 query latency from 480ms to 190ms by adding an "
        "approximate nearest-neighbor index"
    )

    def test_b1_the_tooladda_30_percent_recommendation_is_rejected(self):
        """The motivating case. An external checker proposed a 30%
        improvement that exists in no evidence record. It must be refused,
        and refused for the right reason."""
        result = self.review({
            "id": "tooladda-30-percent",
            "requirement_term": "Hybrid search",
            "proposed_text": "Optimized the retrieval pipeline, increasing output by 30%.",
            "evidence_refs": [self.GROUNDED_CITATION],
        })
        self.assertEqual(result["verdict"], "rejected")
        self.assertEqual(result["reason"], "unsupported_metric")
        self.assertIn("30%", result["measurements_ungrounded"])
        self.assertEqual(result["measurements_grounded"], [])
        self.assertTrue(result["human_review_required"])
        self.assertEqual(result["confidence"], "rejected_no_confidence")

    def test_b1b_any_invented_percentage_is_rejected_not_just_thirty(self):
        # The rule is general; nothing recognises the number 30.
        for invented in ("12%", "47%", "2.5x", "900ms"):
            result = self.review({
                "id": f"invented-{invented}",
                "requirement_term": "Hybrid search",
                "proposed_text": f"Improved the retrieval pipeline by {invented}.",
                "evidence_refs": [self.GROUNDED_CITATION],
            })
            self.assertEqual(result["verdict"], "rejected", invented)
            self.assertEqual(result["reason"], "unsupported_metric", invented)

    def test_b2_a_measurement_the_cited_evidence_states_is_accepted(self):
        result = self.review({
            "id": "grounded-latency",
            "requirement_term": "Hybrid search",
            "proposed_text": (
                "Cut p95 retrieval latency from 480ms to 190ms using an "
                "approximate nearest-neighbor index."
            ),
            "evidence_refs": [self.GROUNDED_CITATION],
        })
        self.assertEqual(result["verdict"], "accepted")
        self.assertIsNone(result["reason"])
        self.assertEqual(
            sorted(result["measurements_grounded"]), sorted(["190ms", "480ms"])
        )
        self.assertEqual(result["measurements_ungrounded"], [])

    def test_b3_a_recommendation_for_a_jd_only_skill_is_rejected(self):
        result = self.review({
            "id": "jd-only",
            "requirement_term": "Kubernetes",
            "proposed_text": "Deployed services on Kubernetes.",
            "evidence_refs": [self.GROUNDED_CITATION],
        })
        self.assertEqual(result["verdict"], "rejected")
        self.assertEqual(result["reason"], "requirement_unsupported")

    def test_b4_an_uncited_recommendation_is_rejected(self):
        for refs in ([], ["nothing in the corpus says this"]):
            result = self.review({
                "id": "uncited",
                "requirement_term": "Hybrid search",
                "proposed_text": "Tuned the retrieval pipeline for recall.",
                "evidence_refs": refs,
            })
            self.assertEqual(result["verdict"], "rejected")
            self.assertEqual(result["reason"], "no_resolvable_evidence")

    def test_b5_an_ambiguous_requirement_yields_human_review(self):
        result = self.review({
            "id": "ambiguous-mongodb",
            "requirement_term": "MongoDB",
            "proposed_text": "Used a document database for prototype storage.",
            "evidence_refs": [self.GROUNDED_CITATION],
        })
        self.assertEqual(result["verdict"], "needs_human_review")
        self.assertTrue(result["human_review_required"])
        self.assertEqual(result["confidence"], "unresolved_pending_review")

    def test_b5b_a_contradicted_requirement_is_rejected_distinctly(self):
        result = self.review({
            "id": "contradicted",
            "requirement_term": "cross-validation",
            "proposed_text": "Applied cross-validation during model selection.",
            "evidence_refs": [self.GROUNDED_CITATION],
        })
        self.assertEqual(result["verdict"], "rejected")
        self.assertEqual(result["reason"], "requirement_contradicted")

    def test_b6_no_accepted_verdict_can_carry_an_ungrounded_metric(self):
        """Exhaustive invariant over every verdict path."""
        proposals = [
            "Increased output by 30%.",
            "Cut p95 retrieval latency from 480ms to 190ms.",
            "Improved NDCG@10 from 0.41 to 0.53.",
            "Reduced cost by 85% and latency by 12ms.",
            "Added prompt-injection and PII guardrails.",
            "Served traffic for 1,000,000 users.",
        ]
        terms = ["Hybrid search", "Kubernetes", "MongoDB", "cross-validation"]
        checked = 0
        for text in proposals:
            for term in terms:
                for refs in ([self.GROUNDED_CITATION], [], ["unresolvable"]):
                    result = self.review({
                        "id": "x", "requirement_term": term,
                        "proposed_text": text, "evidence_refs": refs,
                    })
                    checked += 1
                    if result["verdict"] == "accepted":
                        self.assertEqual(
                            result["measurements_ungrounded"], [],
                            f"accepted an ungrounded metric: {text!r}",
                        )
        self.assertGreater(checked, 50)

    def test_b6b_batch_review_reports_every_rejection_reason(self):
        report = recommendation_guard.review_all(
            [
                {"id": "a", "requirement_term": "Hybrid search",
                 "proposed_text": "Increased output by 30%.",
                 "evidence_refs": [self.GROUNDED_CITATION]},
                {"id": "b", "requirement_term": "Kubernetes",
                 "proposed_text": "Deployed on Kubernetes.",
                 "evidence_refs": [self.GROUNDED_CITATION]},
            ],
            self.corpus, self.match,
        )
        self.assertEqual(report["verdicts"]["rejected"], 2)
        self.assertEqual(report["verdicts"]["accepted"], 0)
        self.assertIn("unsupported_metric", report["rejections_by_reason"])
        self.assertIn("requirement_unsupported", report["rejections_by_reason"])


# ===========================================================================
# C. Preservation of verified achievements
# ===========================================================================

class TestAchievementPreservation(unittest.TestCase):

    VERIFIED = ["480ms", "190ms", "0.41", "0.53", "50k"]

    def test_c1_verified_measurements_survive_matching_unchanged(self):
        # The evidence record for this requirement carries two verified
        # latency figures. They must come back through the report exactly
        # as the evidence states them -- not rounded, restated or dropped.
        report = match([
            {"term": "approximate nearest-neighbor index",
             "category": "CORE_RESPONSIBILITY", "required": True, "priority": "high"},
        ])
        row = row_for(report, "approximate nearest-neighbor index")
        self.assertEqual(row["evidence_support"], "supported")
        hits = json.dumps(row["evidence_hits"])
        for value in ("480ms", "190ms"):
            self.assertIn(value, hits, f"{value} was lost during matching")

    def test_c1b_a_verified_measurement_is_never_reported_as_omitted(self):
        """Previously this asserted a Python string had not mutated,
        which no implementation could fail. What actually needs pinning
        is that a bullet restating its evidence's figures exactly is
        recognised as carrying them."""
        tmp = tempfile.mkdtemp()
        root = write_evidence(Path(tmp))
        corpus = recommendation_guard.load_evidence_corpus(root)
        verified = (
            "Reduced p95 query latency from 480ms to 190ms by adding an "
            "approximate nearest-neighbor index."
        )
        result = recommendation_guard.review_recommendation(
            {"id": "verbatim", "requirement_term": "Hybrid search",
             "proposed_text": verified,
             "evidence_refs": [self.CITATION]},
            corpus,
            {"Hybrid search": {"term": "Hybrid search",
                               "evidence_support": "supported",
                               "match_class": "semantic"}},
        )
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(result["measurements_ungrounded"], [])
        self.assertEqual(
            sorted(result["measurements_grounded"]), sorted(["480ms", "190ms"]))

    CITATION = (
        "reduced p95 query latency from 480ms to 190ms by adding an "
        "approximate nearest-neighbor index"
    )

    def test_c2_the_guard_reviews_but_never_edits(self):
        """The guard must echo the proposal untouched. Asserting only
        that would be tautological, so this also pins that the verdict
        is reached on the real rules and that a DIFFERENT figure for the
        same claim is caught."""
        tmp = tempfile.mkdtemp()
        root = write_evidence(Path(tmp))
        corpus = recommendation_guard.load_evidence_corpus(root)
        index = {"ranking": {"term": "ranking", "evidence_support": "supported",
                             "match_class": "exact"}}
        original = (
            "Improved offline NDCG@10 from 0.41 to 0.53 through hyperparameter tuning."
        )
        citation = ("improved offline NDCG@10 from 0.41 to 0.53 through "
                    "hyperparameter tuning")
        accepted = recommendation_guard.review_recommendation(
            {"id": "preserve", "requirement_term": "ranking",
             "proposed_text": original, "evidence_refs": [citation]},
            corpus, index,
        )
        self.assertEqual(accepted["proposed_text"], original)
        self.assertEqual(accepted["verdict"], "accepted")

        # the same claim with a nudged figure must NOT pass
        inflated = original.replace("0.53", "0.63")
        rejected = recommendation_guard.review_recommendation(
            {"id": "inflated", "requirement_term": "ranking",
             "proposed_text": inflated, "evidence_refs": [citation]},
            corpus, index,
        )
        self.assertEqual(rejected["verdict"], "rejected")
        self.assertEqual(rejected["reason"], "unsupported_metric")
        self.assertIn("0.63", rejected["measurements_ungrounded"])
        # and even when rejected, the wording is returned unedited
        self.assertEqual(rejected["proposed_text"], inflated)

    def test_c2b_calendar_dates_are_not_treated_as_invented_metrics(self):
        """Named for what it now actually exercises: text containing real
        dates. The previous version contained no digits at all, so it
        tested none of the date filtering its name claimed."""
        tmp = tempfile.mkdtemp()
        root = write_evidence(Path(tmp))
        corpus = recommendation_guard.load_evidence_corpus(root)
        for dated in (
            "Reduced p95 query latency from 480ms to 190ms since Jan 2025.",
            "Reduced p95 query latency from 480ms to 190ms during 2024.",
            "Reduced p95 query latency from 480ms to 190ms from 2019 to 2023.",
        ):
            result = recommendation_guard.review_recommendation(
                {"id": "dated", "requirement_term": "Hybrid search",
                 "proposed_text": dated, "evidence_refs": [self.CITATION]},
                corpus,
                {"Hybrid search": {"term": "Hybrid search",
                                   "evidence_support": "supported",
                                   "match_class": "semantic"}},
            )
            self.assertEqual(
                result["measurements_ungrounded"], [],
                f"a calendar date was mistaken for an invented metric: {dated!r}")


# ===========================================================================
# B2. Holes closed after the V8.6.10 integrity audit
# ===========================================================================

class TestGuardBypassesClosed(unittest.TestCase):
    """Each test here corresponds to a confirmed bypass of the evidence
    gate found by an independent audit. Every one of these previously
    returned `accepted`."""

    CITATION = (
        "reduced p95 query latency from 480ms to 190ms by adding an "
        "approximate nearest-neighbor index"
    )

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = write_evidence(Path(self.tmp))
        self.corpus = recommendation_guard.load_evidence_corpus(self.root)
        self.index = {"Hybrid search": {"term": "Hybrid search",
                                        "evidence_support": "supported",
                                        "match_class": "semantic"}}

    def review(self, rec, index=None, ctx=True):
        return recommendation_guard.review_recommendation(
            rec, self.corpus,
            self.index if index is None else index,
            requirement_context_available=ctx,
        )

    def test_g1_an_unknown_requirement_term_is_never_silently_accepted(self):
        """A `requirement_term` absent from the match report used to make
        every requirement comparison compare against None, skipping
        Rule 3 entirely -- so a paraphrased or mistyped term disabled the
        rule that refuses job-description-only experience."""
        result = self.review({
            "id": "unknown", "requirement_term": "Hybrid Search ",  # trailing space
            "proposed_text": "Reduced p95 query latency from 480ms to 190ms.",
            "evidence_refs": [self.CITATION],
        })
        self.assertNotEqual(result["verdict"], "accepted")
        self.assertEqual(result["verdict"], "needs_human_review")
        self.assertEqual(result["reason"], "unknown_requirement")

    def test_g2_a_missing_match_report_cannot_accept_anything(self):
        """Running without requirement context disabled Rule 3 for the
        whole batch while still reporting `accepted`."""
        report = recommendation_guard.review_all(
            [{"id": "a", "requirement_term": "Hybrid search",
              "proposed_text": "Reduced p95 query latency from 480ms to 190ms.",
              "evidence_refs": [self.CITATION]}],
            self.corpus, None,
        )
        self.assertFalse(report["requirement_context_available"])
        self.assertEqual(report["verdicts"]["accepted"], 0)
        self.assertEqual(report["verdicts"]["needs_human_review"], 1)
        self.assertIn("NO REQUIREMENT CONTEXT", report["advisory_note"])

    def test_g3_a_fabricated_non_numeric_claim_is_rejected(self):
        """Only measurements were grounded, so an invented technology or
        responsibility carrying no number passed untouched."""
        for fabricated in (
            "Deployed the service on Kubernetes with Terraform.",
            "Led a team of engineers and owned cluster administration.",
            "Holds an AWS Solutions Architect certification.",
        ):
            result = self.review({
                "id": "fab", "requirement_term": "Hybrid search",
                "proposed_text": fabricated, "evidence_refs": [self.CITATION],
            })
            self.assertEqual(result["verdict"], "rejected", fabricated)
            self.assertEqual(result["reason"], "ungrounded_claim_terms", fabricated)
            self.assertTrue(result["claim_terms_ungrounded"])

    def test_g4_a_four_digit_count_is_not_invisible(self):
        """`metrics_audit` strips 1900-2099 as calendar years. Correct for
        auditing the candidate's own bullets, but in a PROPOSAL it meant
        'scaled to 2000 concurrent users' carried no auditable number."""
        for text, token in (
            ("Scaled the retrieval index to 2000 concurrent queries.", "2000"),
            ("Processed 2048 documents per second.", "2048"),
            ("Cut spend by 1995 dollars.", "1995"),
        ):
            self.assertIn(
                token, recommendation_guard.proposal_measurements(text),
                f"{token} must be auditable in a proposal: {text!r}")

    def test_g4b_proposal_extraction_is_a_superset_of_the_shared_one(self):
        import metrics_audit
        for text in (
            "Reduced p95 latency from 480ms to 190ms.",
            "Improved NDCG@10 from 0.41 to 0.53.",
            "Scaled to 2000 concurrent users in 2024.",
            "Shipped YOLOv7 in Jan 2025.",
        ):
            shared = set(metrics_audit.extract_measurements(text))
            proposal = set(recommendation_guard.proposal_measurements(text))
            self.assertTrue(
                shared <= proposal,
                f"proposal extraction dropped {shared - proposal} for {text!r}")

    def test_g3b_a_reviewed_bridge_grounds_a_renaming_but_not_a_new_claim(self):
        """Claim-term grounding consults the reviewed alias/bridge table,
        so making a true relationship explicit is not mistaken for a new
        claim -- while genuinely novel terminology stays rejected."""
        tmp = Path(tempfile.mkdtemp())
        (tmp / "ev.json").write_text(json.dumps({"facts": [
            {"fact": "stores prototype documents in MongoDB behind a FastAPI service"},
        ]}))
        corpus = recommendation_guard.load_evidence_corpus(tmp)
        index = {"NoSQL": {"term": "NoSQL", "evidence_support": "supported",
                           "match_class": "semantic"}}
        citation = "stores prototype documents in MongoDB behind a FastAPI service"

        # "NoSQL" is absent from the corpus but bridges to MongoDB, which
        # is present. Renaming, not inventing.
        renamed = recommendation_guard.review_recommendation(
            {"id": "rename", "requirement_term": "NoSQL",
             "proposed_text": "Stores prototype documents in MongoDB (NoSQL).",
             "evidence_refs": [citation]},
            corpus, index, aliases=aliases(),
        )
        self.assertEqual(renamed["verdict"], "accepted")
        self.assertEqual(renamed["claim_terms_ungrounded"], [])

        # Without the bridge table the same proposal is refused, which is
        # what makes the table (not a guess) the thing doing the work.
        unbridged = recommendation_guard.review_recommendation(
            {"id": "rename", "requirement_term": "NoSQL",
             "proposed_text": "Stores prototype documents in MongoDB (NoSQL).",
             "evidence_refs": [citation]},
            corpus, index, aliases={},
        )
        self.assertEqual(unbridged["verdict"], "rejected")
        self.assertIn("nosql", unbridged["claim_terms_ungrounded"])

        # A technology with no bridge to anything in the corpus stays out.
        invented = recommendation_guard.review_recommendation(
            {"id": "invent", "requirement_term": "NoSQL",
             "proposed_text": "Stores prototype documents in Cassandra and Terraform.",
             "evidence_refs": [citation]},
            corpus, index, aliases=aliases(),
        )
        self.assertEqual(invented["verdict"], "rejected")
        self.assertEqual(invented["reason"], "ungrounded_claim_terms")

    def test_g5_an_unspecific_citation_does_not_resolve(self):
        """`evidence_refs=['the']` resolved against nearly every record,
        reducing Rule 1 to a formality."""
        for vague in ("the", "a", "data", "the system"):
            result = self.review({
                "id": "vague", "requirement_term": "Hybrid search",
                "proposed_text": "Reduced p95 query latency from 480ms to 190ms.",
                "evidence_refs": [vague],
            })
            self.assertEqual(result["verdict"], "rejected", vague)
            self.assertEqual(result["reason"], "no_resolvable_evidence", vague)

    def test_g5b_a_specific_citation_still_resolves(self):
        result = self.review({
            "id": "specific", "requirement_term": "Hybrid search",
            "proposed_text": "Reduced p95 query latency from 480ms to 190ms.",
            "evidence_refs": [self.CITATION],
        })
        self.assertEqual(result["verdict"], "accepted")
        self.assertEqual(len(result["citations_resolved"]), 1)

    def test_g6_grounding_reads_the_whole_record_not_a_truncated_prefix(self):
        """The resolved citation used to carry only the first 350
        characters, so a figure later in a long record produced a false
        rejection."""
        tmp = Path(tempfile.mkdtemp())
        long_fact = (("distinct preliminary context sentence number one. " * 12)
                     + "the reranking stage improved recall by 42 points overall.")
        (tmp / "ev.json").write_text(json.dumps({"facts": [{"fact": long_fact}]}))
        corpus = recommendation_guard.load_evidence_corpus(tmp)
        self.assertGreater(len(long_fact), 350)
        result = recommendation_guard.review_recommendation(
            {"id": "long", "requirement_term": "recall",
             "proposed_text": "The reranking stage improved recall by 42 points.",
             "evidence_refs": ["the reranking stage improved recall by 42 points overall"]},
            corpus,
            {"recall": {"term": "recall", "evidence_support": "supported",
                        "match_class": "exact"}},
        )
        self.assertEqual(result["measurements_ungrounded"], [])
        self.assertEqual(result["verdict"], "accepted")


# ===========================================================================
# D. Render fidelity and layout
# ===========================================================================

def build_template(path: Path, *, font="Calibri", size=10.5):
    """A small synthetic resume template with real design properties."""
    from docx import Document
    from docx.shared import Pt, Inches
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    document = Document()
    section = document.sections[0]
    section.left_margin = section.right_margin = Inches(0.39)
    section.top_margin = Inches(0.30)
    section.bottom_margin = Inches(0.28)

    def para(text, *, bold=False, italic=False, pt=size, style=None,
             border=False, space_before=None, indent=None):
        paragraph = document.add_paragraph(style=style)
        run = paragraph.add_run(text)
        run.font.name = font
        run.font.size = Pt(pt)
        run.bold, run.italic = bold, italic
        if space_before is not None:
            paragraph.paragraph_format.space_before = Pt(space_before)
        if indent is not None:
            paragraph.paragraph_format.left_indent = Inches(indent)
        if border:
            ppr = paragraph._p.get_or_add_pPr()
            borders = OxmlElement("w:pBdr")
            bottom = OxmlElement("w:bottom")
            bottom.set(qn("w:val"), "single")
            bottom.set(qn("w:sz"), "4")
            borders.append(bottom)
            ppr.append(borders)
        return paragraph

    para("Example Candidate", bold=True, pt=14.0)
    para("candidate@example.com | (555) 010-2000", pt=10.0)
    para("PROFESSIONAL EXPERIENCE", bold=True, pt=12.0, border=True, space_before=3)
    para("AI Engineer Intern | Example Data Systems", bold=True, space_before=2)
    para("PLACEHOLDER_EXP_BULLET_1", style="List Bullet", indent=0.18)
    para("PLACEHOLDER_EXP_BULLET_2", style="List Bullet", indent=0.18)
    para("PROJECTS", bold=True, pt=12.0, border=True, space_before=3)
    para("PLACEHOLDER_SELECTED_HEADER", bold=True, space_before=2)
    para("PLACEHOLDER_SELECTED_BULLET_1", style="List Bullet", indent=0.18)
    para("PLACEHOLDER_SELECTED_BULLET_2", style="List Bullet", indent=0.18)
    # A descriptor-sized run so the template's type scale includes it.
    para("Project Atlas  —  Python, FastAPI", bold=False, italic=True, pt=10.0)

    document.save(str(path))
    return path


LAYOUT = {
    "min_paragraph_count": 11,
    "name_index": 0,
    "contact_index": 1,
    "section_anchors": {
        "experience_heading": {"index": 2, "text": "PROFESSIONAL EXPERIENCE"},
        "projects_heading": {"index": 6, "text": "PROJECTS"},
    },
    "experience_blocks": [
        {"owner_id": "experience_1", "tailoring_mode": "tailorable",
         "header_index": 3, "bullet_indices": [4, 5]},
    ],
    "project_blocks": [
        {"owner_id": "selected_project_slot", "tailoring_mode": "selected",
         "header_index": 7, "bullet_indices": [8, 9]},
    ],
}

PAYLOAD = {
    "experience_alternate_bullets": {
        "experience_1": [
            "Built a retrieval service using **hybrid dense+sparse retrieval**.",
            "Reduced p95 latency from **480ms to 190ms**.",
        ]
    },
    "selected_project": {
        "owner_id": "selected_project_slot",
        "title": "Project Atlas",
        "technology_line": "Python, FastAPI",
        "bullets": [
            "Improved offline **NDCG@10** from 0.41 to 0.53.",
            "Packaged the prototype as a reproducible CLI.",
        ],
    },
}


def render(template: Path, out: Path, layout=None, payload=None):
    import render_resume
    import template_layout as layout_lib
    from docx import Document

    layout = json.loads(json.dumps(layout or LAYOUT))
    payload = json.loads(json.dumps(payload or PAYLOAD))
    document = Document(str(template))
    layout_lib.validate_payload(payload, layout)
    layout_lib.apply_payload(
        document, payload, layout,
        render_resume.markdown_runs, render_resume.set_project_header,
    )
    document.save(str(out))
    return out


class TestRenderFidelity(unittest.TestCase):

    def test_d1_rendering_changes_words_not_typography(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template = build_template(tmp / "t.docx")
            rendered = render(template, tmp / "r.docx")
            audit = template_style_audit.audit_render_fidelity(template, rendered)
            self.assertEqual(audit["status"], "EVALUATED")
            self.assertTrue(
                audit["fidelity_preserved"],
                f"unexpected design drift: {audit['findings']}",
            )
            # and the words really did change
            from docx import Document
            self.assertIn("480ms", "\n".join(
                p.text for p in Document(str(rendered)).paragraphs))

    def test_d2_a_non_calibri_template_keeps_its_own_font_and_sizes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template = build_template(tmp / "t.docx", font="Garamond", size=11.0)
            rendered = render(template, tmp / "r.docx")

            from docx import Document
            document = Document(str(rendered))
            for index in (4, 5, 8, 9):
                for run in document.paragraphs[index].runs:
                    if not run.text.strip():
                        continue
                    self.assertEqual(
                        run.font.name, "Garamond",
                        "the renderer must not impose its own font family",
                    )
                    self.assertEqual(run.font.size.pt, 11.0)

            audit = template_style_audit.audit_render_fidelity(template, rendered)
            self.assertTrue(audit["fidelity_preserved"], audit["findings"])

    def test_d3_font_family_drift_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template = build_template(tmp / "t.docx")
            rendered = render(template, tmp / "r.docx")

            from docx import Document
            document = Document(str(rendered))
            document.paragraphs[4].runs[0].font.name = "Comic Sans MS"
            document.save(str(tmp / "drifted.docx"))

            audit = template_style_audit.audit_render_fidelity(
                template, tmp / "drifted.docx")
            self.assertFalse(audit["fidelity_preserved"])
            subjects = [f["subject"] for f in audit["findings"]]
            self.assertIn("paragraph[4].typography", subjects)
            self.assertTrue(any(f["severity"] == "high" for f in audit["findings"]))

    def test_d4_spacing_and_indent_drift_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template = build_template(tmp / "t.docx")
            rendered = render(template, tmp / "r.docx")

            from docx import Document
            from docx.shared import Pt, Inches
            document = Document(str(rendered))
            document.paragraphs[3].paragraph_format.space_before = Pt(24)
            document.paragraphs[4].paragraph_format.left_indent = Inches(1.5)
            document.save(str(tmp / "drifted.docx"))

            audit = template_style_audit.audit_render_fidelity(
                template, tmp / "drifted.docx")
            subjects = [f["subject"] for f in audit["findings"]]
            self.assertIn("paragraph[3].space_before", subjects)
            self.assertIn("paragraph[4].left_indent", subjects)

    def test_d5_lost_heading_borders_and_bullet_styles_are_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template = build_template(tmp / "t.docx")
            rendered = render(template, tmp / "r.docx")

            from docx import Document
            document = Document(str(rendered))
            # strip the section-heading rule
            ppr = document.paragraphs[2]._p.pPr
            for child in list(ppr):
                if child.tag.endswith("}pBdr"):
                    ppr.remove(child)
            # turn a bullet back into a plain paragraph
            document.paragraphs[4].style = document.styles["Normal"]
            document.save(str(tmp / "drifted.docx"))

            audit = template_style_audit.audit_render_fidelity(
                template, tmp / "drifted.docx")
            subjects = [f["subject"] for f in audit["findings"]]
            self.assertIn("paragraph[2].bottom_border", subjects)
            self.assertIn("paragraph[4].style", subjects)

    def test_d6_page_geometry_drift_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            template = build_template(tmp / "t.docx")
            rendered = render(template, tmp / "r.docx")

            from docx import Document
            from docx.shared import Inches
            document = Document(str(rendered))
            document.sections[0].left_margin = Inches(1.25)
            document.save(str(tmp / "drifted.docx"))

            audit = template_style_audit.audit_render_fidelity(
                template, tmp / "drifted.docx")
            subjects = [f["subject"] for f in audit["findings"]]
            self.assertIn("left_margin", subjects)
            self.assertTrue(any(
                f["check"] == "geometry" and f["severity"] == "high"
                for f in audit["findings"]))


# ===========================================================================
# E. Rendered-PDF integrity
# ===========================================================================

def synthetic_pdf(path: Path, lines, *, page_count=1, start_y=40, leading=14,
                  fontsize=10.5, fontname="helv"):
    """A real PDF built with the already-present fitz dependency."""
    import fitz

    document = fitz.open()
    for page_index in range(page_count):
        page = document.new_page(width=612, height=792)
        y = start_y
        for line in (lines[page_index] if page_count > 1 else lines):
            page.insert_text((40, y), line, fontsize=fontsize, fontname=fontname)
            y += leading
    document.save(str(path))
    document.close()
    return path


class TestRenderedPdfIntegrity(unittest.TestCase):

    # Comfortably above pdf_roundtrip's 200-character readability floor,
    # so field recall is genuinely evaluated rather than short-circuited
    # to `not_evaluated` for a page that is simply very sparse.
    LINES = [
        "Example Candidate",
        "candidate@example.com | (555) 010-2000 | Example City, ST",
        "PROFESSIONAL EXPERIENCE",
        "AI Engineer Intern | Example Data Systems | Remote | Jun 2025 - Aug 2025",
        "Built a retrieval service using hybrid dense+sparse retrieval.",
        "Reduced p95 latency from 480ms to 190ms with an ANN index.",
        "Added prompt-injection, scope, and PII checks as guardrails.",
        "PROJECTS",
        "Project Beacon -- Recommendation Prototype",
        "Improved offline NDCG@10 from 0.41 to 0.53 through tuning.",
    ]

    def test_e1_text_extraction_and_reading_order_on_a_real_pdf(self):
        import pdf_roundtrip
        with tempfile.TemporaryDirectory() as tmp:
            pdf = synthetic_pdf(Path(tmp) / "r.pdf", self.LINES)
            fields = [
                {"id": f"f{i}", "section": "EXPERIENCE", "text": line}
                for i, line in enumerate(self.LINES)
            ]
            result = pdf_roundtrip.run(pdf, fields)
            self.assertEqual(result["status"], "EVALUATED")
            self.assertEqual(
                [r["status"] for r in result["fields"]],
                ["found"] * len(self.LINES),
            )
            self.assertFalse(result["reading_order"]["inversions"])

    def test_e1b_a_missing_bullet_is_reported_missing(self):
        import pdf_roundtrip
        with tempfile.TemporaryDirectory() as tmp:
            pdf = synthetic_pdf(Path(tmp) / "r.pdf", self.LINES[:-1])
            fields = [
                {"id": f"f{i}", "section": "PROJECTS", "text": line}
                for i, line in enumerate(self.LINES)
            ]
            result = pdf_roundtrip.run(pdf, fields)
            self.assertEqual(result["fields"][-1]["status"], "missing")

    def test_e2_excessive_bottom_whitespace_is_measured_and_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            # six lines at the very top of the page -> a mostly empty page
            pdf = synthetic_pdf(Path(tmp) / "sparse.pdf", self.LINES)
            audit = template_style_audit.audit_pdf_appearance(
                pdf,
                bottom_whitespace_in=(0.25, 0.50),
                page_bottom_margin_in=0.28,
            )
            self.assertEqual(audit["status"], "EVALUATED")
            self.assertGreater(audit["usable_bottom_whitespace_in"], 0.50)
            self.assertTrue(any(
                f["subject"] == "usable_bottom_whitespace_in"
                for f in audit["findings"]
            ))

    def test_e2b_a_well_filled_page_reports_no_whitespace_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Fill the page so the last baseline lands inside the
            # 0.25-0.50in usable-whitespace window: 792pt page, 0.28in
            # (20.2pt) bottom margin, so the last line must sit at roughly
            # y >= 735pt.
            many = [f"Line {i} of substantive resume content." for i in range(51)]
            pdf = synthetic_pdf(Path(tmp) / "full.pdf", many, start_y=40, leading=14)
            audit = template_style_audit.audit_pdf_appearance(
                pdf,
                bottom_whitespace_in=(0.25, 0.50),
                page_bottom_margin_in=0.28,
            )
            self.assertEqual(audit["page_count"], 1)
            self.assertLessEqual(audit["usable_bottom_whitespace_in"], 0.50)
            self.assertEqual(audit["findings"], [])

    def test_e3_pagination_is_reported_and_whitespace_stays_page_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = synthetic_pdf(Path(tmp) / "one.pdf", self.LINES)
            two = synthetic_pdf(
                Path(tmp) / "two.pdf",
                [self.LINES, ["Spilled trailing line."]],
                page_count=2,
            )
            self.assertEqual(
                template_style_audit.audit_pdf_appearance(one)["page_count"], 1)

            spilled = template_style_audit.audit_pdf_appearance(two)
            self.assertEqual(spilled["page_count"], 2)
            # Documented scope: whitespace is measured on page 1 only, so
            # a reader of this field knows it does not describe the end
            # of a multi-page document. `qa_resume.py` owns the
            # authoritative one-page/spill verdict.
            self.assertIsNotNone(spilled["usable_bottom_whitespace_in"])
            self.assertEqual(
                spilled["usable_bottom_whitespace_in"],
                template_style_audit.audit_pdf_appearance(one)[
                    "usable_bottom_whitespace_in"],
                "page-1 whitespace must not change because a page 2 exists",
            )

    def test_e4_undersized_body_text_is_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = synthetic_pdf(Path(tmp) / "r.pdf", self.LINES, fontsize=9.0)
            audit = template_style_audit.audit_pdf_appearance(
                pdf, min_body_size_pt=10.5)
            self.assertAlmostEqual(audit["body_size_pt"], 9.0, places=1)
            self.assertTrue(any(
                f["subject"] == "body_size_pt" for f in audit["findings"]))

    def test_e4b_a_smaller_secondary_size_is_not_an_undersized_body(self):
        """A design may legitimately set secondary text below the body
        floor -- this repository's own layout contract puts bullets at
        >=10.5pt and the project technology descriptor at ~10pt. The body
        floor must be applied to the BODY size, not the global minimum,
        or every conforming resume reports a false positive."""
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mixed.pdf"
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            y = 40
            for line in self.LINES:          # body at 10.5pt
                page.insert_text((40, y), line, fontsize=10.5, fontname="helv")
                y += 14
            # one smaller italic-style descriptor line
            page.insert_text((40, y), "Project Atlas — Python, FastAPI",
                             fontsize=10.0, fontname="helv")
            document.save(str(path))
            document.close()

            audit = template_style_audit.audit_pdf_appearance(
                path, min_body_size_pt=10.5, min_any_size_pt=10.0)
            self.assertAlmostEqual(audit["body_size_pt"], 10.5, places=1)
            self.assertAlmostEqual(audit["min_font_size_pt"], 10.0, places=1)
            self.assertEqual(
                [f for f in audit["findings"]
                 if f["check"] == "pdf_typography"], [],
                "a legitimately smaller descriptor must not read as an "
                "undersized body",
            )

    def test_e4c_an_absolute_floor_still_catches_tiny_text(self):
        import fitz
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tiny.pdf"
            document = fitz.open()
            page = document.new_page(width=612, height=792)
            y = 40
            for line in self.LINES:
                page.insert_text((40, y), line, fontsize=10.5, fontname="helv")
                y += 14
            page.insert_text((40, y), "shrunk to fit", fontsize=6.0, fontname="helv")
            document.save(str(path))
            document.close()

            audit = template_style_audit.audit_pdf_appearance(
                path, min_body_size_pt=10.5, min_any_size_pt=10.0)
            self.assertTrue(any(
                f["subject"] == "min_font_size_pt" for f in audit["findings"]),
                "text shrunk below the absolute floor must be caught")

    def test_e5_thresholds_are_opt_in_not_baked_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = synthetic_pdf(Path(tmp) / "r.pdf", self.LINES, fontsize=6.0)
            audit = template_style_audit.audit_pdf_appearance(pdf)
            # measurement only; no caller threshold -> no opinion
            self.assertEqual(audit["findings"], [])
            self.assertIsNotNone(audit["min_font_size_pt"])


# ===========================================================================
# F. Compatibility with the existing pipeline
# ===========================================================================

class TestCompatibility(unittest.TestCase):

    def _job(self, tmp: Path):
        job = tmp / "synthetic-job"
        job.mkdir(parents=True, exist_ok=True)
        (job / "resume_draft.md").write_text(SYNTHETIC_RESUME, encoding="utf-8")
        return job

    def test_f1_the_consolidated_report_builds_with_the_new_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            job = self._job(tmp)
            report = resume_quality_report.build_report(
                job, "synthetic-job",
                jd_match=match([
                    {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
                     "required": True, "priority": "high"},
                    {"term": "citation validation", "category": "CORE_RESPONSIBILITY",
                     "required": True, "priority": "high"},
                ]),
            )
            self.assertTrue(report["job_match"]["available"])
            self.assertIsNone(report["job_match"]["composite_score"])
            categories = {c["category"] for c in report["checks"]}
            self.assertIn("job_match", categories)
            # the opportunity surfaced as an actionable finding
            self.assertTrue(any(
                f["id"] == "job_match.supported_but_not_surfaced"
                for f in report["findings"]
            ))

    def test_f1b_integrity_and_job_fit_stay_separate_categories(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            job = self._job(tmp)
            report = resume_quality_report.build_report(
                job, "synthetic-job",
                jd_match=match([
                    {"term": "Kubernetes", "category": "REQUIRED_HARD_SKILL",
                     "required": True, "priority": "critical"},
                ]),
            )
            job_fit = [f for f in report["findings"] if f["category"] == "job_match"]
            self.assertTrue(job_fit)
            # The substantive claim: an unmet job requirement must be
            # filed as job fit, and must NOT appear under any
            # document-integrity check id. Comparing two lists already
            # filtered on different categories would be true by
            # construction, so assert on the finding ids instead.
            integrity_prefixes = ("artifact_integrity.", "reading_order.")
            for finding in job_fit:
                self.assertFalse(
                    finding["id"].startswith(integrity_prefixes),
                    f"job-fit finding filed as a document defect: {finding['id']}",
                )
            self.assertTrue(any(
                f["id"] == "job_match.required_requirement_unmet" for f in job_fit))
            # and the artifact's own checks are unaffected by job fit
            artifact_checks = [c for c in report["checks"]
                               if c["category"] == "artifact_integrity"]
            self.assertTrue(artifact_checks)

    def test_f2_absent_optional_sections_do_not_degrade_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            job = self._job(tmp)
            report = resume_quality_report.build_report(job, "synthetic-job")
            self.assertFalse(report["job_match"]["available"])
            self.assertFalse(report["template_fidelity"]["available"])
            for check_id in ("job_match.requirement_coverage",
                             "design.template_fidelity"):
                check = next(c for c in report["checks"] if c["id"] == check_id)
                self.assertEqual(
                    check["status"], "not_applicable",
                    "an optional input that was never supplied must not read as "
                    "a failed check",
                )

    def test_f2b_no_composite_score_leaks_into_the_consolidated_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            job = self._job(tmp)
            report = resume_quality_report.build_report(
                job, "synthetic-job",
                jd_match=match([
                    {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
                     "required": True, "priority": "high"},
                ]),
            )
            blob = json.dumps(report).lower()
            for forbidden in ('"ats_score"', '"overall_score"', '"match_rate"'):
                self.assertNotIn(forbidden, blob)
            self.assertTrue(report["advisory_only"])

    def test_f3_the_ats_gate_contract_is_unchanged(self):
        """`resume_present` must still be exactly 'any checked form occurs',
        and the Fix 7 partition must never add or drop a form."""
        alias_map = aliases()
        concepts = [
            {"term": "FastAPI", "required": True},
            {"term": "Hybrid search", "required": True},
            {"term": "SQL", "aliases": ["MySQL"], "required": False},
            {"term": "", "canonical": "Retrieval-Augmented Generation"},
            {"exact": "pgvector"},
            {"term": "nonexistent-xyzzy"},
        ]
        texts = [SYNTHETIC_RESUME, "", "Python FastAPI hybrid search", "nothing"]
        for concept in concepts:
            forms = frozen.concept_forms(concept, alias_map)
            literal, bridged = ats_preflight_v86.partition_forms(concept, alias_map)
            self.assertEqual(
                sorted(frozen.normalize(f) for f in literal + bridged),
                sorted(frozen.normalize(f) for f in forms),
                "partition_forms must partition concept_forms exactly",
            )
            for text in texts:
                legacy = any(frozen.phrase_present(text, f) for f in forms)
                current = bool(
                    ats_preflight_v86.first_present_form(text, literal)
                    or ats_preflight_v86.first_present_form(text, bridged)
                )
                self.assertEqual(legacy, current, f"{concept} / {text[:30]!r}")

    def test_f3c_a_bridge_cannot_satisfy_a_concept_with_no_evidence(self):
        """Concept bridges feed `concept_forms`, which feeds BOTH the
        resume check and the evidence check, so the bridge config is a
        live input to `gate_pass` -- not merely to reporting. The
        invariant that must hold however broad a bridge gets: it can
        widen which phrases are searched for, but it can never create
        evidence support where no record contains any of those phrases.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = write_evidence(Path(tmp))
            # A deliberately over-broad bridge for a concept the
            # synthetic evidence says nothing about.
            overbroad = dict(aliases())
            overbroad["kubernetes"] = [
                "orchestration", "containers", "cluster", "deployment"
            ]
            report = ats_preflight_v86.evaluate(
                {"job_key": "bridge-probe", "concepts": [
                    {"term": "Kubernetes", "category": "REQUIRED_HARD_SKILL",
                     "required": True, "priority": "critical"},
                ]},
                SYNTHETIC_RESUME, root, overbroad,
            )
            concept = report["concepts"][0]
            self.assertFalse(concept["supported_by_master_evidence"])
            self.assertEqual(concept["status"], "UNSUPPORTED")
            self.assertIn("Kubernetes", report["unsupported_required_terms"])

    def test_f3d_shipped_bridges_do_not_flip_any_fixture_gate(self):
        """Pins `gate_pass` for every requirements/resume fixture pair in
        the repository against the bridge file as shipped. A future
        bridge edit that silently flips a gate outcome fails here."""
        alias_map = aliases()
        requirement_files = sorted(ROOT.rglob("jd_requirements.json"))
        draft_files = sorted(ROOT.rglob("resume_draft.md"))
        self.assertTrue(requirement_files, "no requirement fixtures found")

        with tempfile.TemporaryDirectory() as tmp:
            root = write_evidence(Path(tmp))
            evaluated = 0
            for requirements_path in requirement_files:
                try:
                    requirements = json.loads(requirements_path.read_text())
                except Exception:
                    continue
                if not isinstance(requirements.get("concepts"), list):
                    continue
                for draft_path in draft_files:
                    report = ats_preflight_v86.evaluate(
                        requirements,
                        draft_path.read_text(errors="replace"),
                        root, alias_map,
                    )
                    evaluated += 1
                    # Against synthetic evidence that supports none of
                    # these job concepts, no bridge may produce support.
                    for concept in report["concepts"]:
                        if concept["supported_by_master_evidence"]:
                            self.assertTrue(
                                concept["evidence_hits"],
                                f"{concept['term']!r} reported supported with no "
                                "evidence hit",
                            )
                    self.assertIsInstance(report["gate_pass"], bool)
            self.assertGreater(evaluated, 0)

    def test_f3b_the_gate_still_emits_every_pre_existing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_evidence(Path(tmp))
            report = ats_preflight_v86.evaluate(
                {"job_key": "synthetic-job", "concepts": [
                    {"term": "FastAPI", "category": "REQUIRED_HARD_SKILL",
                     "required": True, "priority": "high"},
                ]},
                SYNTHETIC_RESUME, root, aliases(),
            )
            for key in (
                "gate_pass", "supported_required_skill_coverage_percent",
                "high_priority_concept_coverage_percent",
                "required_high_priority_concept_coverage_percent",
                "core_responsibility_coverage_percent", "supported_and_present",
                "supported_but_missing", "supported_but_missing_required",
                "optional_supported_but_missing", "unsupported",
                "missing_supported_required_terms", "unsupported_required_terms",
                "concepts",
            ):
                self.assertIn(key, report)
            concept = report["concepts"][0]
            for key in ("term", "canonical", "category", "required", "priority",
                        "importance", "forms_checked",
                        "supported_by_master_evidence", "resume_present",
                        "status", "evidence_hits",
                        "negated_evidence_hits_excluded"):
                self.assertIn(key, concept)
            # and the additive Fix 7 keys
            self.assertEqual(concept["match_kind"], "exact")
            self.assertEqual(concept["matched_form"], "FastAPI")


if __name__ == "__main__":
    unittest.main(verbosity=2)
