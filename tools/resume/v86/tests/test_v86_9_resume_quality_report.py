#!/usr/bin/env python3
"""
V8.6.9 -- evidence-aware resume quality + ATS readability diagnostics.

ZERO real Claude/Apify calls, ZERO paid benchmarks, ZERO network access,
ZERO reads of any private candidate file. Every candidate, employer,
institution, project, date, bullet and metric below is wholly synthetic
and written inline in this file; the only repository data touched is
`master/examples/*.example.json` (the tracked, fictional example
schemas) and the tracked threshold config.

Covers, in order:

  PDF round-trip (Feature A)
    1  all expected content found, wrapped lines handled
    2  a single bullet entirely missing
    3  a partially extracted bullet
    4  replacement / garbled characters
    5  reading-order inversion
    6  technical strings and metrics preserved by normalization
    7  no source PDF / no expected-content source -> documented
       unevaluated state, no crash

  Metrics (Feature B)
    8  a verified measurement tied to a source bullet is absent from the
       corresponding final bullet
    9  a measurement that IS present is not falsely reported missing
    10 no verified measurement exists -> nothing invented, nothing reported
    11 an unrelated measurement is not attributed to the wrong bullet
    12 ambiguous owner/source association -> not_evaluated, not a guess

  Bullet length and style (Feature C)
    13 an unusually short substantive bullet -> non-blocking advisory
    14 a bullet exactly at the threshold is not warned about
    15 non-bullet content is excluded
    16 the existing action-verb checker runs once and keeps its behavior

  Timeline (Feature D)
    17 a gap fully covered by education -> explained_by_education
    18 a gap partially overlapped by education -> partially_explained
    19 an uncovered interval -> informational review, never a gate failure
    20 overlapping experience entries are not reported as gaps
    21 year-only / ambiguous dates -> uncertain result
    22 current/ongoing entries handled per the existing schema

  Report (Feature E)
    23 schema valid, every finding structurally consistent
    24 a missing professional summary is not_applicable, not a failure
    25 the report is written only to the job-local, git-ignored area

  Plus: pipeline integration, gate-semantics preservation, and the
  repository's existing privacy restrictions.
"""

from __future__ import annotations

import ast
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

V86_DIR = Path(__file__).resolve().parents[1]
ROOT = V86_DIR.parents[2]
sys.path.insert(0, str(V86_DIR))

import action_verb_diversity  # noqa: E402
import bullet_length_audit  # noqa: E402
import deterministic_pipeline  # noqa: E402
import metrics_audit  # noqa: E402
import pdf_roundtrip  # noqa: E402
import resume_draft_model  # noqa: E402
import resume_quality_report  # noqa: E402
import timeline_audit  # noqa: E402


# --------------------------------------------------------------------------
# Wholly synthetic fixtures
# --------------------------------------------------------------------------

SYNTHETIC_DRAFT = """Synthetic Candidate
synthetic@example.com | (555) 010-7777 | Sample Town, ST

EDUCATION

Synthetic Institute -- M.S. Synthetic Systems
GPA: 3.90/4.00 | Sep 2023 -- Jun 2025

PROFESSIONAL EXPERIENCE

Platform Engineer | Synthetic Works | Remote\tJan 2021 -- Dec 2021
- Rebuilt the batch ingestion path in C++ and cut nightly runtime from 4 hours to 35 minutes.
- Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.

Tooling Engineer | Sample Systems | Remote\tJul 2022 -- Aug 2023
- Shipped a .NET service that resolved 18000 lookup requests per minute under load.

PROJECTS

Synthetic Atlas -- Catalog Indexer  —  C#, PostgreSQL
- Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.
- Documented the indexing contract so a second contributor could extend it safely.

SKILLS

Languages: C++, C#, Python
Tools: Docker, PostgreSQL
"""

SYNTHETIC_RUNTIME_CONTEXT = {
    "schema_version": "1.0",
    "candidate": {"full_name": "Synthetic Candidate"},
    "education": [
        {
            "id": "education_1",
            "institution": "Synthetic Institute",
            "dates": "Sep 2023 -- Jun 2025",
        },
    ],
    "experience": [
        {
            "id": "experience_1",
            "employer": "Synthetic Works",
            "title": "Platform Engineer",
            "dates": "Jan 2021 -- Dec 2021",
            "tailoring_mode": "protected",
            "canonical_bullets": [
                "Rebuilt the batch ingestion path in C++ and cut nightly runtime from 4 hours to 35 minutes.",
                "Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.",
            ],
        },
        {
            "id": "experience_2",
            "employer": "Sample Systems",
            "title": "Tooling Engineer",
            "dates": "Jul 2022 -- Aug 2023",
            "tailoring_mode": "tailorable",
            "alternate_bullets_for_dynamic_tailoring": [
                "Shipped a .NET service that resolved 18000 lookup requests per minute under load.",
            ],
        },
    ],
    "projects": [
        {
            "id": "project_selectable_a",
            "name": "Synthetic Atlas",
            "project_status": "selectable",
            "tailoring_mode": "tailorable",
            "alternate_bullets_for_dynamic_tailoring": [
                "Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
                "Documented the indexing contract so a second contributor could extend it safely.",
            ],
        },
        {
            "id": "project_selectable_b",
            "name": "Synthetic Beacon",
            "project_status": "selectable",
            "tailoring_mode": "tailorable",
            "alternate_bullets_for_dynamic_tailoring": [
                "Cut synthetic duplicate-detection error from 12% to 3% on a held-out split.",
            ],
        },
    ],
}

SYNTHETIC_METRICS_REGISTRY = {
    "metrics": [
        {
            "id": "atlas_lookup_latency",
            "owner_id": "project_selectable_a",
            "statement": "Held p99 lookup latency under 45ms across a 90k-record synthetic catalog.",
            "verified": True,
            "source": "synthetic_facts.md#atlas",
        },
        {
            "id": "beacon_error_reduction",
            "owner_id": "project_selectable_b",
            "statement": "Cut synthetic duplicate-detection error from 12% to 3% on a held-out split.",
            "verified": True,
            "source": "synthetic_facts.md#beacon",
        },
        {
            "id": "tooling_throughput",
            "owner_id": "experience_2",
            "statement": "Resolved 18000 lookup requests per minute under load.",
            "verified": True,
            "source": "synthetic_facts.md#tooling",
        },
    ],
}


def payload_with(project_bullets, experience_bullets=None) -> dict:
    return {
        "experience_alternate_bullets": {
            "experience_2": experience_bullets if experience_bullets is not None else [
                "Shipped a .NET service that resolved 18000 lookup requests per minute under load.",
            ],
        },
        "selected_project": {
            "owner_id": "project_selectable_a",
            "title": "Synthetic Atlas",
            "technology_line": "C#, PostgreSQL",
            "bullets": project_bullets,
        },
    }


def synthetic_pdf_text(draft: str = SYNTHETIC_DRAFT) -> str:
    """What a sane PDF text layer for `draft` looks like: markdown bullet
    markers rendered as a bullet glyph, and one mid-sentence line wrap."""
    rendered = "\n".join(
        ("• " + line[2:]) if line.startswith("- ") else line
        for line in draft.splitlines()
    )
    return rendered.replace(
        "cut nightly runtime from 4 hours to 35 minutes.",
        "cut nightly runtime\nfrom 4 hours to 35 minutes.",
    )


def expected_fields(draft: str = SYNTHETIC_DRAFT) -> list:
    return resume_draft_model.parse_resume_markdown(draft)["fields"]


def field_status(result: dict, field_id: str) -> str:
    for row in result["fields"]:
        if row["id"] == field_id:
            return row["status"]
    raise AssertionError(f"no such expected field: {field_id}")


# --------------------------------------------------------------------------
# Feature A -- PDF round-trip
# --------------------------------------------------------------------------

class TestPdfRoundTrip(unittest.TestCase):

    def test_01_all_expected_content_found_with_wrapped_lines(self):
        result = pdf_roundtrip.analyze(synthetic_pdf_text(), expected_fields())
        self.assertEqual(result["status"], "EVALUATED")
        self.assertEqual(result["recall_summary"]["missing"], 0)
        self.assertEqual(result["recall_summary"]["partial"], 0)
        self.assertEqual(result["recall_summary"]["not_evaluated"], 0)
        self.assertGreater(result["recall_summary"]["found"], 10)
        # the wrapped bullet is the point of this test
        self.assertEqual(field_status(result, "experience.bullet.0"), "found")

    def test_01b_every_field_carries_its_own_identifier(self):
        result = pdf_roundtrip.analyze(synthetic_pdf_text(), expected_fields())
        ids = [row["id"] for row in result["fields"]]
        self.assertEqual(len(ids), len(set(ids)), "field identifiers must be unique")
        self.assertIn("experience.bullet.1", ids)
        self.assertIn("projects.bullet.0", ids)
        self.assertIn("section_heading.skills", ids)

    def test_02_single_bullet_entirely_missing(self):
        text = synthetic_pdf_text().replace(
            "• Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.\n",
            "",
        )
        result = pdf_roundtrip.analyze(text, expected_fields())
        self.assertEqual(field_status(result, "experience.bullet.1"), "missing")
        self.assertEqual(result["recall_summary"]["missing"], 1)
        # every other bullet is unaffected
        self.assertEqual(field_status(result, "experience.bullet.0"), "found")
        self.assertEqual(field_status(result, "projects.bullet.0"), "found")

    def test_03_partially_extracted_bullet(self):
        text = synthetic_pdf_text().replace(
            "• Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
            "• Indexed a 90k-record synthetic catalog and held p99 lookup latency",
        )
        result = pdf_roundtrip.analyze(text, expected_fields())
        self.assertEqual(field_status(result, "projects.bullet.0"), "partial")
        row = next(r for r in result["fields"] if r["id"] == "projects.bullet.0")
        self.assertGreaterEqual(row["match_ratio"], result["partial_min_token_ratio"])
        self.assertLess(row["match_ratio"], 1.0)

    def test_04_replacement_and_garbled_characters(self):
        text = synthetic_pdf_text().replace("C++", "C��")
        result = pdf_roundtrip.analyze(text, expected_fields())
        self.assertEqual(result["glyph_corruption"]["replacement_characters"], 4)
        self.assertTrue(result["glyph_corruption"]["corrupt"])

        cid = pdf_roundtrip.analyze(
            synthetic_pdf_text().replace("Docker", "(cid:72)(cid:81)"), expected_fields(),
        )
        self.assertEqual(cid["glyph_corruption"]["cid_glyph_markers"], 2)
        self.assertTrue(cid["glyph_corruption"]["corrupt"])

        control = pdf_roundtrip.analyze(
            synthetic_pdf_text() + "\x01\x02", expected_fields(),
        )
        self.assertEqual(control["glyph_corruption"]["control_characters"], 2)

    def test_05_reading_order_inversion_is_detected(self):
        clean = pdf_roundtrip.analyze(synthetic_pdf_text(), expected_fields())
        self.assertEqual(clean["reading_order"]["status"], "evaluated")
        self.assertEqual(clean["reading_order"]["inversions"], 0)
        self.assertEqual(clean["reading_order"]["order_fidelity"], 1.0)
        self.assertEqual(clean["reading_order"]["misordered_elements"], [])

        # A two-column export that extracts the SKILLS column before the
        # PROJECTS column: same content, inverted sequence.
        text = synthetic_pdf_text()
        head, _, tail = text.partition("PROJECTS")
        projects_block, _, skills_block = tail.partition("SKILLS")
        inverted = head + "SKILLS" + skills_block + "PROJECTS" + projects_block

        result = pdf_roundtrip.analyze(inverted, expected_fields())
        order = result["reading_order"]
        self.assertEqual(order["status"], "evaluated")
        self.assertGreater(order["inversions"], 0)
        self.assertLess(order["order_fidelity"], 1.0)
        self.assertTrue(order["misordered_elements"])
        affected = {m["expected_before"] for m in order["misordered_elements"]}
        affected |= {m["expected_after"] for m in order["misordered_elements"]}
        self.assertTrue(
            any(a.startswith(("projects.", "section_heading.projects", "skills.")) for a in affected),
            f"the affected elements should name the swapped sections: {affected}",
        )

    def test_06_normalization_preserves_technical_strings_and_numbers(self):
        preserved = [
            "C++", "C#", ".NET", "CI/CD", "99.9%", "0.71", "45ms",
            "18000", "3.11", "p99", "90k", "f1-score",
        ]
        for token in preserved:
            with self.subTest(token=token):
                self.assertIn(
                    token.casefold(),
                    pdf_roundtrip.normalize_text(f"Used {token} in production."),
                )

    def test_06b_normalization_folds_only_presentation_differences(self):
        self.assertEqual(
            pdf_roundtrip.normalize_text("**Built** a  thing­ify — fast"),
            pdf_roundtrip.normalize_text("Built a thingify - fast"),
        )
        # a line wrap must not look like missing text
        self.assertEqual(
            pdf_roundtrip.normalize_text("merged 5 heterogeneous\nlab datasets"),
            pdf_roundtrip.normalize_text("merged 5 heterogeneous lab datasets"),
        )
        # ... but a dropped number is never normalized away
        self.assertNotEqual(
            pdf_roundtrip.normalize_text("cut runtime to 35 minutes"),
            pdf_roundtrip.normalize_text("cut runtime to minutes"),
        )

    def test_07_missing_inputs_return_documented_unevaluated_state(self):
        no_pdf = pdf_roundtrip.run(None, expected_fields())
        self.assertEqual(no_pdf["status"], "NOT_EVALUATED")
        self.assertIn("no resume PDF path", no_pdf["reason"])

        with tempfile.TemporaryDirectory() as tmp:
            absent = pdf_roundtrip.run(Path(tmp) / "resume.pdf", expected_fields())
            self.assertEqual(absent["status"], "NOT_EVALUATED")
            self.assertIn("not found", absent["reason"])

            # a real file that is not a readable PDF must diagnose, not raise
            broken = Path(tmp) / "broken.pdf"
            broken.write_text("this is not a pdf")
            self.assertEqual(pdf_roundtrip.run(broken, expected_fields())["status"], "NOT_EVALUATED")

        no_source = pdf_roundtrip.run(Path("/nonexistent.pdf"), [])
        self.assertEqual(no_source["status"], "NOT_EVALUATED")

        self.assertEqual(
            resume_draft_model.parse_resume_markdown_file(Path("/nonexistent/resume_draft.md")),
            {"fields": [], "sections_present": []},
        )

    def test_07b_unreadable_text_layer_never_cascades_into_false_missing(self):
        result = pdf_roundtrip.analyze("   ", expected_fields())
        self.assertTrue(result["text_layer"]["empty"])
        self.assertTrue(result["text_layer"]["effectively_unreadable"])
        self.assertEqual(result["recall_summary"]["missing"], 0)
        self.assertEqual(result["recall_summary"]["found"], 0)
        self.assertEqual(result["recall_summary"]["not_evaluated"], len(expected_fields()))
        self.assertEqual(result["reading_order"]["status"], "not_evaluated")

    def test_07c_round_trip_reads_a_real_pdf_through_the_shared_qa_extractor(self):
        import fitz

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "resume.pdf"
            doc = fitz.open()
            page = doc.new_page()
            y = 40
            for line in SYNTHETIC_DRAFT.splitlines():
                if line.strip():
                    page.insert_text((40, y), line.replace("\t", "  "), fontsize=7)
                y += 9
            doc.save(str(pdf_path))
            doc.close()

            result = pdf_roundtrip.run(pdf_path, expected_fields())

        self.assertEqual(result["status"], "EVALUATED")
        self.assertFalse(result["text_layer"]["effectively_unreadable"])
        self.assertEqual(result["recall_summary"]["missing"], 0)
        self.assertEqual(result["glyph_corruption"]["replacement_characters"], 0)


class TestDraftModelAgreesWithTheExistingBulletDefinition(unittest.TestCase):

    def test_substantive_bullets_match_action_verb_diversity_exactly(self):
        model = resume_draft_model.parse_resume_markdown(SYNTHETIC_DRAFT)
        mine = [f["text"] for f in resume_draft_model.substantive_bullets(model)]
        theirs = action_verb_diversity.extract_experience_and_project_bullets(SYNTHETIC_DRAFT)
        self.assertEqual(mine, theirs, "the two modules must never disagree about what a bullet is")

    def test_real_repository_fixture_also_agrees(self):
        fixture = V86_DIR / "tests/fixtures/live13_11_recommendation/resume_draft.md"
        text = fixture.read_text(encoding="utf-8")
        model = resume_draft_model.parse_resume_markdown(text)
        self.assertEqual(
            [f["text"] for f in resume_draft_model.substantive_bullets(model)],
            action_verb_diversity.extract_experience_and_project_bullets(text),
        )


# --------------------------------------------------------------------------
# Feature B -- evidence-aware quantifiable-impact audit
# --------------------------------------------------------------------------

class TestMetricsAudit(unittest.TestCase):

    def _audit(self, payload, registry=None, context=None):
        return metrics_audit.audit_metrics(
            context or SYNTHETIC_RUNTIME_CONTEXT,
            registry or SYNTHETIC_METRICS_REGISTRY,
            payload,
        )

    def _row(self, audit, metric_id):
        return next(r for r in audit["results"] if r["metric_id"] == metric_id)

    def test_08_verified_measurement_dropped_from_its_own_final_bullet(self):
        audit = self._audit(payload_with([
            "Indexed a large synthetic catalog and kept lookups responsive.",
            "Documented the indexing contract so a second contributor could extend it safely.",
        ]))
        row = self._row(audit, "atlas_lookup_latency")
        self.assertEqual(row["status"], "omitted")
        self.assertEqual(row["owner_id"], "project_selectable_a")
        self.assertIn("45ms", row["measurements"])
        self.assertIsNotNone(row["source_bullet_index"])
        self.assertEqual(row["measurements_present"], [])

    def test_09_present_measurement_is_not_falsely_reported_missing(self):
        audit = self._audit(payload_with([
            "Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
            "Documented the indexing contract so a second contributor could extend it safely.",
        ]))
        self.assertEqual(self._row(audit, "atlas_lookup_latency")["status"], "present")
        self.assertEqual(audit["summary"]["omitted"], 0)

    def test_09b_a_reworded_bullet_that_keeps_the_number_is_still_present(self):
        audit = self._audit(payload_with([
            "Kept p99 lookup latency under 45 ms for the synthetic catalog index.",
            "Documented the indexing contract so a second contributor could extend it safely.",
        ]))
        self.assertEqual(self._row(audit, "atlas_lookup_latency")["status"], "present")

    def test_10_no_verified_measurement_means_nothing_is_invented_or_reported(self):
        registry = {
            "metrics": [
                {
                    "id": "qualitative_only",
                    "owner_id": "project_selectable_a",
                    "statement": "Documented the indexing contract so a teammate could extend it.",
                    "verified": True,
                    "source": "synthetic_facts.md#atlas",
                },
                {
                    "id": "unverified_number",
                    "owner_id": "project_selectable_a",
                    "statement": "Held p99 lookup latency under 45ms.",
                    "verified": False,
                    "source": "synthetic_facts.md#atlas",
                },
                {
                    "id": "dates_and_versions_only",
                    "owner_id": "experience_2",
                    "statement": "Worked with Python 3.11 and .NET 6 from Jan 2022 through 2023, release v1.2.3.",
                    "verified": True,
                    "source": "synthetic_facts.md#tooling",
                },
            ],
        }
        audit = self._audit(payload_with(["A bullet with no numbers at all."]), registry=registry)
        self.assertEqual(audit["summary"]["omitted"], 0)
        self.assertEqual(self._row(audit, "unverified_number")["status"], "not_evaluated")
        self.assertEqual(self._row(audit, "dates_and_versions_only")["status"], "not_evaluated")
        self.assertEqual(self._row(audit, "dates_and_versions_only")["measurements"], [])
        for row in audit["results"]:
            self.assertNotEqual(row["status"], "omitted")

    def test_10b_dates_versions_and_identifiers_are_never_impact_metrics(self):
        self.assertEqual(metrics_audit.extract_measurements("Shipped in Jan 2024 on v2.10.1"), [])
        self.assertEqual(metrics_audit.extract_measurements("Tuned the p95 and f1 numbers"), [])
        self.assertEqual(metrics_audit.extract_measurements("Upgraded to Python 3.11"), [])
        self.assertIn("45ms", metrics_audit.extract_measurements("held latency under 45ms"))
        self.assertIn("70%", metrics_audit.extract_measurements("reduced review time by 70%"))

    def test_11_unrelated_measurement_is_not_attributed_to_the_wrong_bullet(self):
        # project_selectable_b's verified 12% -> 3% metric exists in the
        # registry, but that project is NOT on this resume. Its absence
        # from the bound project's bullets must never be reported.
        audit = self._audit(payload_with([
            "Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
            "Documented the indexing contract so a second contributor could extend it safely.",
        ]))
        beacon = self._row(audit, "beacon_error_reduction")
        self.assertEqual(beacon["status"], "not_applicable")
        self.assertIn("not bound for this job", beacon["reason"])
        self.assertEqual(audit["summary"]["omitted"], 0)

    def test_11b_an_owner_whose_metric_is_dropped_is_named_exactly(self):
        audit = self._audit(payload_with(
            [
                "Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
                "Documented the indexing contract so a second contributor could extend it safely.",
            ],
            experience_bullets=["Shipped a .NET service used across the tooling org."],
        ))
        omitted = [r for r in audit["results"] if r["status"] == "omitted"]
        self.assertEqual([r["owner_id"] for r in omitted], ["experience_2"])
        self.assertIn("18000", omitted[0]["measurements"])

    def test_12_ambiguous_owner_or_source_association_is_not_evaluated(self):
        unknown_owner = {"metrics": [{
            "id": "orphan", "owner_id": "experience_does_not_exist",
            "statement": "Cut processing time by 40%.", "verified": True,
        }]}
        row = self._row(self._audit(payload_with(["x"]), registry=unknown_owner), "orphan")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("does not resolve", row["reason"])

        no_owner = {"metrics": [{
            "id": "ownerless", "statement": "Cut processing time by 40%.", "verified": True,
        }]}
        row = self._row(self._audit(payload_with(["x"]), registry=no_owner), "ownerless")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("no owner_id", row["reason"])

        duplicated = json.loads(json.dumps(SYNTHETIC_RUNTIME_CONTEXT))
        duplicated["experience"].append(dict(duplicated["experience"][1]))
        row = self._row(
            self._audit(payload_with(["x"]), context=duplicated), "tooling_throughput",
        )
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("ambiguous", row["reason"])

    def test_12b_a_measurement_no_approved_source_bullet_carries_is_not_evaluated(self):
        registry = {"metrics": [{
            "id": "unlinked", "owner_id": "project_selectable_a",
            "statement": "Served 4200 synthetic requests per second.", "verified": True,
        }]}
        row = self._row(self._audit(payload_with(["x"]), registry=registry), "unlinked")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("no approved source bullet", row["reason"])

    def test_12c_missing_registry_or_context_is_not_evaluated_not_a_crash(self):
        self.assertEqual(
            metrics_audit.audit_metrics(SYNTHETIC_RUNTIME_CONTEXT, {}, payload_with(["x"]))["status"],
            "NOT_EVALUATED",
        )
        self.assertEqual(
            metrics_audit.audit_metrics({}, SYNTHETIC_METRICS_REGISTRY, payload_with(["x"]))["status"],
            "NOT_EVALUATED",
        )

    def test_12d_tracked_example_schemas_are_understood_as_written(self):
        context = json.loads((ROOT / "master/examples/runtime_resume_context.example.json").read_text())
        registry = json.loads((ROOT / "master/examples/metrics_registry.example.json").read_text())
        payload = {
            "experience_alternate_bullets": {"experience_2": ["A bullet."]},
            "selected_project": {
                "owner_id": "project_selectable_1", "title": "T", "technology_line": "x",
                "bullets": ["Reduced p95 query latency from 480ms to 190ms by adding an ANN index."],
            },
        }
        audit = metrics_audit.audit_metrics(context, registry, payload)
        self.assertEqual(audit["status"], "EVALUATED")
        row = next(r for r in audit["results"] if r["metric_id"] == "meridian_latency_reduction")
        self.assertEqual(row["status"], "present")


# --------------------------------------------------------------------------
# Feature C -- short-bullet and length diagnostics
# --------------------------------------------------------------------------

class TestBulletLengthAdvisory(unittest.TestCase):

    def _bullets(self, draft):
        return resume_draft_model.substantive_bullets(
            resume_draft_model.parse_resume_markdown(draft)
        )

    def test_13_unusually_short_substantive_bullet_is_a_nonblocking_advisory(self):
        draft = SYNTHETIC_DRAFT.replace(
            "- Documented the indexing contract so a second contributor could extend it safely.",
            "- Cut p99 latency 40%.",
        )
        audit = bullet_length_audit.audit_bullet_lengths(self._bullets(draft), threshold=60)
        self.assertEqual(audit["status"], "EVALUATED")
        self.assertEqual([r["id"] for r in audit["short_bullets"]], ["projects.bullet.1"])
        row = audit["short_bullets"][0]
        self.assertEqual(row["visible_chars"], len("Cut p99 latency 40%."))
        self.assertEqual(row["threshold"], 60)

        # ... and it is advisory: low severity, never a failure
        checks, findings = resume_quality_report._bullet_length_findings(audit)
        self.assertEqual(checks[0]["status"], "review")
        self.assertEqual([f["severity"] for f in findings], ["low"])
        self.assertEqual(findings[0]["subject"], "projects.bullet.1")
        self.assertIn("non-blocking", findings[0]["recommendation"])
        self.assertIn("never pad", findings[0]["recommendation"])

    def test_14_a_bullet_exactly_at_the_threshold_is_not_warned_about(self):
        exactly_60 = "A" * 60
        self.assertEqual(bullet_length_audit.visible_length(exactly_60), 60)
        draft = SYNTHETIC_DRAFT.replace(
            "- Documented the indexing contract so a second contributor could extend it safely.",
            "- " + exactly_60,
        )
        audit = bullet_length_audit.audit_bullet_lengths(self._bullets(draft), threshold=60)
        self.assertEqual(audit["short_bullets"], [])

        one_shorter = SYNTHETIC_DRAFT.replace(
            "- Documented the indexing contract so a second contributor could extend it safely.",
            "- " + "A" * 59,
        )
        self.assertEqual(
            len(bullet_length_audit.audit_bullet_lengths(self._bullets(one_shorter), threshold=60)["short_bullets"]),
            1,
        )

    def test_14b_markdown_styling_and_the_bullet_marker_do_not_count_as_width(self):
        self.assertEqual(bullet_length_audit.visible_length("- **Built** it"), len("Built it"))
        self.assertEqual(bullet_length_audit.visible_length("• `Built` it"), len("Built it"))

    def test_14c_threshold_is_configuration_not_a_hardcoded_constant(self):
        config = resume_quality_report.load_config()
        self.assertIn("short_bullet_min_visible_chars", config)
        self.assertEqual(config["short_bullet_min_visible_chars"], 60)
        self.assertEqual(
            config["_source"], "config/v86/resume_quality_diagnostics.json",
        )
        shipped = json.loads((ROOT / "config/v86/resume_quality_diagnostics.json").read_text())
        for key in resume_quality_report.CONFIG_DEFAULTS:
            self.assertIn(key, shipped)

        draft = SYNTHETIC_DRAFT.replace(
            "- Documented the indexing contract so a second contributor could extend it safely.",
            "- A moderately sized synthetic bullet of about seventy characters here.",
        )
        bullets = self._bullets(draft)
        self.assertEqual(bullet_length_audit.audit_bullet_lengths(bullets, threshold=60)["short_bullets"], [])
        self.assertEqual(
            len(bullet_length_audit.audit_bullet_lengths(bullets, threshold=120)["short_bullets"]),
            len(bullets),
        )

    def test_14d_broken_config_falls_back_to_documented_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{ not json")
            self.assertEqual(
                resume_quality_report.load_config(bad)["short_bullet_min_visible_chars"],
                resume_quality_report.CONFIG_DEFAULTS["short_bullet_min_visible_chars"],
            )
            wrong_type = Path(tmp) / "wrong.json"
            wrong_type.write_text(json.dumps({"short_bullet_min_visible_chars": "sixty"}))
            self.assertEqual(
                resume_quality_report.load_config(wrong_type)["short_bullet_min_visible_chars"], 60,
            )

    def test_15_non_bullet_content_is_excluded(self):
        model = resume_draft_model.parse_resume_markdown(SYNTHETIC_DRAFT)
        audit = bullet_length_audit.audit_bullet_lengths(
            resume_draft_model.substantive_bullets(model), threshold=400,
        )
        measured = {row["id"] for row in audit["measurements"]}
        self.assertTrue(all(i.endswith(tuple(f".bullet.{n}" for n in range(10))) for i in measured))
        for excluded in (
            "section_heading.education", "section_heading.skills", "header.name",
            "header.contact", "education.line.0", "education.line.1",
            "skills.line.0", "experience.line.0", "projects.line.0",
        ):
            self.assertNotIn(excluded, measured, f"{excluded} must never be length-audited")

        # even if a caller hands in a non-substantive field, it is ignored
        heading = [f for f in model["fields"] if f["id"] == "skills.line.0"]
        self.assertEqual(
            bullet_length_audit.audit_bullet_lengths(heading, threshold=400)["status"],
            "NOT_EVALUATED",
        )

    def test_15b_sections_with_no_bullets_are_not_evaluated(self):
        audit = bullet_length_audit.audit_bullet_lengths([], threshold=60)
        self.assertEqual(audit["status"], "NOT_EVALUATED")
        self.assertIn("no substantive", audit["reason"])

    def test_16_action_verb_checker_runs_once_and_keeps_its_behavior(self):
        verb_report = action_verb_diversity.analyze_action_verbs(SYNTHETIC_DRAFT)

        # (a) the report module never calls the checker at all -- it only
        #     consumes the result the pipeline already computed.
        source = (V86_DIR / "resume_quality_report.py").read_text(encoding="utf-8")
        self.assertNotIn("import action_verb_diversity", source)
        self.assertNotIn("analyze_action_verbs", source)

        # (b) proven at runtime, not just statically
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "job-synth"
            job.mkdir()
            (job / "resume_draft.md").write_text(SYNTHETIC_DRAFT)
            with mock.patch.object(
                action_verb_diversity, "analyze_action_verbs_from_file",
                side_effect=AssertionError("the report must never re-run the action-verb checker"),
            ):
                report = resume_quality_report.build_report(
                    job, "job-synth", qa={}, action_verb_report=verb_report,
                    runtime_context_path=Path(tmp) / "absent.json",
                    metrics_registry_path=Path(tmp) / "absent.json",
                )

        # (c) the reused result is carried through verbatim
        self.assertEqual(report["reused_results"]["action_verb_diversity"], verb_report)
        style = next(c for c in report["checks"] if c["id"] == "style.action_verb_diversity")
        self.assertIn("reused", style["reason"])

        # (d) the deterministic pipeline calls it exactly once
        pipeline_source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        self.assertEqual(pipeline_source.count("analyze_action_verbs_from_file("), 1)

    def test_16b_style_findings_mirror_the_supplied_result_exactly(self):
        supplied = {"counts": {"built": 4}, "repeated_over_limit": {"built": 4}, "limit": 2}
        checks, findings = resume_quality_report._style_findings(supplied)
        self.assertEqual(checks[0]["status"], "review")
        self.assertEqual(findings[0]["severity"], "low")
        self.assertEqual(findings[0]["evidence"]["repeated_over_limit"], {"built": 4})
        self.assertEqual(findings[0]["evidence"]["limit"], 2)

        clean_checks, clean_findings = resume_quality_report._style_findings(
            {"counts": {"built": 1}, "repeated_over_limit": {}, "limit": 2}
        )
        self.assertEqual(clean_checks[0]["status"], "pass")
        self.assertEqual(clean_findings, [])

        missing_checks, _ = resume_quality_report._style_findings(None)
        self.assertEqual(missing_checks[0]["status"], "not_evaluated")

    def test_16c_authoritative_length_and_density_logic_is_untouched(self):
        preflight = (ROOT / "tools/resume/payload_preflight.py").read_text(encoding="utf-8")
        density = (ROOT / "tools/resume/density_guard.py").read_text(encoding="utf-8")
        # the real budget gate still reads its limits from the layout file
        self.assertIn("max_chars_per_bullet", preflight)
        self.assertIn('"status": "PASS" if not failures else "FAIL"', preflight)
        # the density band is still owned by density_guard
        self.assertIn("MIN_BOTTOM = 0.25", density)
        self.assertIn("MAX_BOTTOM = 0.50", density)
        # and the advisory never redefines either
        advisory = (V86_DIR / "bullet_length_audit.py").read_text(encoding="utf-8")
        self.assertNotIn("max_chars_per_bullet", advisory)
        self.assertNotIn("0.25", advisory)


# --------------------------------------------------------------------------
# Feature D -- education-aware timeline diagnostics
# --------------------------------------------------------------------------

def timeline_context(experience, education=()) -> dict:
    return {
        "experience": [
            {"id": f"experience_{i + 1}", "employer": f"Synthetic Employer {i + 1}", "dates": d}
            for i, d in enumerate(experience)
        ],
        "education": [
            {"id": f"education_{i + 1}", "institution": f"Synthetic Institute {i + 1}", "dates": d}
            for i, d in enumerate(education)
        ],
    }


class TestTimelineAudit(unittest.TestCase):

    def test_17_gap_fully_covered_by_education(self):
        audit = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Sep 2022 -- Aug 2023"],
            education=["Jan 2021 -- Aug 2022"],
        ))
        self.assertEqual(audit["status"], "EVALUATED")
        self.assertEqual(len(audit["intervals"]), 1)
        interval = audit["intervals"][0]
        self.assertEqual(interval["classification"], "explained_by_education")
        self.assertEqual(interval["from"], "2021-01")
        self.assertEqual(interval["to"], "2022-08")
        self.assertEqual(interval["months"], 20)
        self.assertEqual(interval["education_months_covered"], 20)
        self.assertIn("covers the whole interval", interval["reason"])

    def test_18_gap_partially_overlapped_by_education(self):
        audit = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Jan 2022 -- Aug 2023"],
            education=["Jan 2021 -- Jun 2021"],
        ))
        interval = audit["intervals"][0]
        self.assertEqual(interval["classification"], "partially_explained")
        self.assertEqual(interval["months"], 12)
        self.assertEqual(interval["education_months_covered"], 6)
        self.assertIn("6 of the interval's 12 months", interval["reason"])

    def test_19_uncovered_interval_is_informational_never_a_gate_failure(self):
        audit = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Jan 2022 -- Aug 2023"],
        ))
        interval = audit["intervals"][0]
        self.assertEqual(interval["classification"], "unexplained")
        self.assertEqual(interval["months"], 12)

        checks, findings = resume_quality_report._timeline_findings(audit)
        self.assertEqual(checks[0]["status"], "review")
        self.assertEqual([f["severity"] for f in findings], ["info"])
        self.assertEqual(findings[0]["status"], "unexplained")
        self.assertIn("never a gate failure", findings[0]["recommendation"])
        self.assertIn("invent", findings[0]["recommendation"])

        # an info-only report still reads PASS -- this can never fail a job
        summary = resume_quality_report.summarize(findings)
        self.assertEqual(resume_quality_report.diagnostic_state(checks, summary), "PASS")

    def test_19b_an_interval_below_the_review_threshold_is_not_reported(self):
        audit = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Apr 2021 -- Aug 2023"],
        ))
        self.assertEqual(audit["intervals"], [])
        self.assertEqual(audit["status"], "EVALUATED")

        wider = timeline_audit.audit_timeline(
            timeline_context(experience=["Jan 2020 -- Dec 2020", "Apr 2021 -- Aug 2023"]),
            gap_review_months=3,
        )
        self.assertEqual(len(wider["intervals"]), 1)

    def test_20_overlapping_and_adjacent_experience_is_not_a_gap(self):
        overlapping = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2021", "Jun 2020 -- Aug 2023"],
        ))
        self.assertEqual(overlapping["intervals"], [])

        adjacent = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Jan 2021 -- Aug 2023"],
        ))
        self.assertEqual(adjacent["intervals"], [])

        concurrent_then_gap = timeline_audit.audit_timeline(timeline_context(
            experience=[
                "Jan 2020 -- Dec 2020", "Mar 2020 -- Jun 2020", "Mar 2022 -- Aug 2023",
            ],
        ))
        self.assertEqual(len(concurrent_then_gap["intervals"]), 1)
        self.assertEqual(concurrent_then_gap["intervals"][0]["from"], "2021-01")

    def test_21_year_only_or_ambiguous_dates_produce_an_uncertain_result(self):
        year_only = timeline_audit.audit_timeline(timeline_context(
            experience=["2019 -- 2020", "Jan 2022 -- Aug 2023"],
        ))
        self.assertEqual(year_only["status"], "UNDETERMINED")
        self.assertEqual(year_only["intervals"], [])
        self.assertIn("year-only", year_only["reason"])
        self.assertEqual(
            [e["id"] for e in year_only["undated_entries"]["experience"]], ["experience_1"],
        )

        unparseable = timeline_audit.audit_timeline(timeline_context(
            experience=["sometime in the past", "Jan 2022 -- Aug 2023"],
        ))
        self.assertEqual(unparseable["status"], "UNDETERMINED")

        single = timeline_audit.audit_timeline(timeline_context(experience=["Jan 2022 -- Aug 2023"]))
        self.assertEqual(single["status"], "NOT_EVALUATED")
        self.assertIn("fewer than two", single["reason"])

        none_at_all = timeline_audit.audit_timeline({})
        self.assertEqual(none_at_all["status"], "NOT_EVALUATED")

        checks, findings = resume_quality_report._timeline_findings(year_only)
        self.assertEqual(checks[0]["status"], "undetermined")
        self.assertEqual(findings, [])

    def test_21b_endpoint_parsing_is_explicit_about_precision(self):
        self.assertEqual(timeline_audit.parse_endpoint("Jan 2025")["precision"], "month")
        self.assertEqual(timeline_audit.parse_endpoint("January 2025")["precision"], "month")
        self.assertEqual(timeline_audit.parse_endpoint("01/2025")["precision"], "month")
        self.assertEqual(timeline_audit.parse_endpoint("2025")["precision"], "year")
        self.assertEqual(timeline_audit.parse_endpoint("Present")["precision"], "ongoing")
        self.assertEqual(timeline_audit.parse_endpoint("mid-2025")["precision"], "unknown")
        self.assertFalse(timeline_audit.parse_date_range("2019 -- 2020")["usable"])
        self.assertTrue(timeline_audit.parse_date_range("Aug 2024 -- May 2026")["usable"])
        self.assertTrue(timeline_audit.parse_date_range("August 2024 - May 2026")["usable"])
        self.assertTrue(timeline_audit.parse_date_range("Aug 2024 – Present")["usable"])
        # a reversed range cannot be trusted
        self.assertFalse(timeline_audit.parse_date_range("May 2026 -- Aug 2024")["usable"])

    def test_22_current_and_ongoing_entries_are_handled(self):
        ongoing_last = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Jan 2022 -- Present"],
            education=["Jan 2021 -- Dec 2021"],
        ))
        self.assertEqual(ongoing_last["status"], "EVALUATED")
        self.assertEqual(len(ongoing_last["intervals"]), 1)
        self.assertEqual(ongoing_last["intervals"][0]["classification"], "explained_by_education")

        ongoing_education = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2020 -- Dec 2020", "Jan 2022 -- Jun 2022", "Mar 2024 -- Dec 2024"],
            education=["Jan 2021 -- Present"],
        ))
        self.assertEqual(ongoing_education["status"], "EVALUATED")
        self.assertTrue(ongoing_education["intervals"])
        self.assertTrue(all(
            i["classification"] == "explained_by_education" for i in ongoing_education["intervals"]
        ))

        # an ongoing role swallows everything after it -- no trailing gap
        no_trailing_gap = timeline_audit.audit_timeline(timeline_context(
            experience=["Jan 2018 -- Present", "Jan 2020 -- Dec 2020"],
        ))
        self.assertEqual(no_trailing_gap["intervals"], [])

    def test_22b_the_tracked_example_context_parses_cleanly(self):
        context = json.loads((ROOT / "master/examples/runtime_resume_context.example.json").read_text())
        audit = timeline_audit.audit_timeline(context)
        self.assertEqual(audit["status"], "EVALUATED")
        self.assertEqual(audit["undated_entries"]["experience"], [])
        self.assertEqual(audit["undated_entries"]["education"], [])

    def test_22c_nothing_in_this_module_can_propose_an_entry(self):
        source = (V86_DIR / "timeline_audit.py").read_text(encoding="utf-8")
        for forbidden in ("def add_", "suggest_entry", "fabricat"):
            self.assertNotIn(forbidden, source)


# --------------------------------------------------------------------------
# Feature E -- the consolidated report
# --------------------------------------------------------------------------

REQUIRED_FINDING_FIELDS = (
    "id", "category", "severity", "status", "subject", "message", "evidence", "recommendation",
)

ALLOWED_CATEGORIES = {
    "artifact_integrity", "reading_order", "content_quality", "metrics", "timeline", "style",
    # V8.6.9: job fit and render fidelity. Kept as their own categories
    # rather than folded into the existing ones so that objective
    # document-integrity defects stay distinguishable from job-specific
    # content findings.
    "job_match", "design",
}


class TestConsolidatedReport(unittest.TestCase):

    def _job(self, tmp, *, with_pdf=False, finalization=None, qa=None, draft=SYNTHETIC_DRAFT):
        job = Path(tmp) / "synthetic-job-key"
        job.mkdir(parents=True, exist_ok=True)
        (job / "resume_draft.md").write_text(draft)
        (job / "resume_payload.json").write_text(json.dumps(payload_with([
            "Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
            "Documented the indexing contract so a second contributor could extend it safely.",
        ])))
        (job / "resume_qa.json").write_text(json.dumps(qa if qa is not None else {
            "status": "PASS", "page_count": 1, "replacement_character_present": False,
            "density_target_pass": True, "usable_bottom_whitespace_in": 0.33,
        }))
        if finalization is not None:
            (job / "watermark_finalization.json").write_text(json.dumps(finalization))
        if with_pdf:
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            y = 40
            for line in draft.splitlines():
                if line.strip():
                    page.insert_text((40, y), line.replace("\t", "  "), fontsize=7)
                y += 9
            doc.save(str(job / "resume.pdf"))
            doc.close()
        return job

    def _generate(self, job, **kwargs):
        kwargs.setdefault("action_verb_report",
                          action_verb_diversity.analyze_action_verbs(SYNTHETIC_DRAFT))
        kwargs.setdefault("runtime_context_path", job / "_runtime.json")
        kwargs.setdefault("metrics_registry_path", job / "_registry.json")
        (job / "_runtime.json").write_text(json.dumps(SYNTHETIC_RUNTIME_CONTEXT))
        (job / "_registry.json").write_text(json.dumps(SYNTHETIC_METRICS_REGISTRY))
        return resume_quality_report.generate(job, "synthetic-job-key", **kwargs)

    def test_23_schema_is_valid_and_every_finding_is_structurally_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, with_pdf=True)
            report = self._generate(job)

        self.assertEqual(report["schema_version"], "1.0")
        self.assertEqual(report["job"], "synthetic-job-key")
        self.assertTrue(report["advisory_only"])
        for key in ("generated_at", "artifact", "checks", "findings",
                    "summary_by_severity", "diagnostic_state", "notes", "thresholds"):
            self.assertIn(key, report)

        self.assertEqual(report["artifact"]["path"], "resume.pdf")
        self.assertTrue(report["artifact"]["exists"])
        self.assertRegex(report["artifact"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["expected_content_source"]["path"], "resume_draft.md")

        self.assertIn(report["diagnostic_state"], ("PASS", "REVIEW", "INCOMPLETE"))

        for check in report["checks"]:
            self.assertEqual(set(check), {"id", "category", "status", "reason"})
            self.assertIn(check["category"], ALLOWED_CATEGORIES)
            self.assertIn(check["status"],
                          ("pass", "fail", "review", "not_evaluated", "not_applicable", "undetermined"))

        for finding in report["findings"]:
            self.assertEqual(tuple(finding), REQUIRED_FINDING_FIELDS)
            self.assertIn(finding["category"], ALLOWED_CATEGORIES)
            self.assertIn(finding["severity"], resume_quality_report.SEVERITIES)
            self.assertTrue(finding["id"] and finding["message"] and finding["recommendation"])
            self.assertIsInstance(finding["evidence"], dict)

        summary = report["summary_by_severity"]
        self.assertEqual(summary["total"], len(report["findings"]))
        self.assertEqual(
            sum(summary[s] for s in resume_quality_report.SEVERITIES), summary["total"],
        )

        # every required category of check is represented
        check_ids = {c["id"] for c in report["checks"]}
        for expected in (
            "artifact_integrity.text_layer", "artifact_integrity.content_recall",
            "artifact_integrity.replacement_characters", "artifact_integrity.page_density",
            "reading_order.sequence", "metrics.verified_measurement_recall",
            "content_quality.short_bullets", "content_quality.professional_summary",
            "timeline.experience_gaps", "style.action_verb_diversity",
        ):
            self.assertIn(expected, check_ids)

    def test_23b_no_numeric_ats_score_is_produced(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._generate(self._job(tmp, with_pdf=True))
        flattened = json.dumps(report).lower()
        for forbidden in ("ats_score", "overall_score", "quality_score", "match_score"):
            self.assertNotIn(forbidden, flattened)
        self.assertNotIsInstance(report["diagnostic_state"], (int, float))

    def test_23c_diagnostic_state_precedence_is_deterministic(self):
        pass_checks = [resume_quality_report._check("x", "style", "pass")]
        self.assertEqual(
            resume_quality_report.diagnostic_state(pass_checks, resume_quality_report.summarize([])),
            "PASS",
        )
        info_only = [resume_quality_report._finding("i", "timeline", "info", "ok", "s", "m", {}, "r")]
        self.assertEqual(
            resume_quality_report.diagnostic_state(pass_checks, resume_quality_report.summarize(info_only)),
            "PASS",
        )
        low = [resume_quality_report._finding("l", "metrics", "low", "omitted", "s", "m", {}, "r")]
        self.assertEqual(
            resume_quality_report.diagnostic_state(pass_checks, resume_quality_report.summarize(low)),
            "REVIEW",
        )
        unevaluated = [resume_quality_report._check("x", "style", "not_evaluated", "no input")]
        self.assertEqual(
            resume_quality_report.diagnostic_state(unevaluated, resume_quality_report.summarize([])),
            "INCOMPLETE",
        )
        high = [resume_quality_report._finding("h", "artifact_integrity", "high", "fail", "s", "m", {}, "r")]
        self.assertEqual(
            resume_quality_report.diagnostic_state(unevaluated, resume_quality_report.summarize(high)),
            "REVIEW",
        )

    def test_24_missing_professional_summary_is_not_applicable_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = self._generate(self._job(tmp, with_pdf=True))

        check = next(c for c in report["checks"] if c["id"] == "content_quality.professional_summary")
        self.assertEqual(check["status"], "not_applicable")
        self.assertIn("does not use", check["reason"].lower().replace("no professional-summary section", "does not use"))

        finding = next(f for f in report["findings"] if f["id"] == "content_quality.professional_summary")
        self.assertEqual(finding["status"], "not_applicable")
        self.assertEqual(finding["severity"], "info")
        self.assertIn("Do not add a summary", finding["recommendation"])

        # and the resume structure itself is untouched: still the same four
        # sections, with no summary section anywhere
        model = resume_draft_model.parse_resume_markdown(SYNTHETIC_DRAFT)
        self.assertEqual(
            model["sections_present"],
            ["EDUCATION", "PROFESSIONAL EXPERIENCE", "PROJECTS", "SKILLS"],
        )

    def test_25_report_is_written_only_to_the_job_local_ignored_area(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, with_pdf=True)
            before = {p.name for p in job.iterdir()}
            self._generate(job)
            after = {p.name for p in job.iterdir()}

            created = after - before
            self.assertEqual(created, {"resume_quality_report.json", "_runtime.json", "_registry.json"})
            written = job / "resume_quality_report.json"
            self.assertTrue(written.exists())
            json.loads(written.read_text())

            # nothing was written outside the job directory
            self.assertEqual(
                sorted(p.name for p in Path(tmp).iterdir()), ["synthetic-job-key"],
            )

        # the real destination is the already-git-ignored runtime area
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("output/", gitignore)
        self.assertIn(".work/", gitignore)
        self.assertEqual(resume_quality_report.REPORT_FILENAME, "resume_quality_report.json")

        # and no report instance is tracked anywhere in the repository
        tracked = [
            p for p in ROOT.rglob("resume_quality_report.json")
            if p.relative_to(ROOT).parts[0] not in {".git", "output", ".work", "master", "reference"}
        ]
        self.assertEqual(tracked, [], f"report instances must never be tracked: {tracked}")

    def test_25b_artifact_stage_never_overclaims(self):
        with tempfile.TemporaryDirectory() as tmp:
            pre = self._generate(self._job(tmp, with_pdf=True))
            self.assertEqual(pre["artifact"]["stage"], "pre_finalization")
            self.assertIn("may still be replaced", pre["artifact"]["stage_reason"])

        with tempfile.TemporaryDirectory() as tmp:
            finalized = self._generate(self._job(tmp, with_pdf=True, finalization={
                "job": "synthetic-job-key", "overall_status": "FINALIZED",
                "artifacts": [{"file": "resume.pdf", "status": "PASS"}],
            }))
            self.assertEqual(finalized["artifact"]["stage"], "finalized")
            self.assertIn("final deliverable", finalized["artifact"]["stage_reason"])

        with tempfile.TemporaryDirectory() as tmp:
            not_applied = self._generate(self._job(tmp, with_pdf=True, finalization={
                "job": "synthetic-job-key", "overall_status": "SERVICE_UNAVAILABLE", "artifacts": [],
            }))
            self.assertEqual(not_applied["artifact"]["stage"], "finalization_not_applied")

        with tempfile.TemporaryDirectory() as tmp:
            failed = self._generate(self._job(tmp, with_pdf=True, finalization={
                "job": "synthetic-job-key", "overall_status": "FAILED",
                "artifacts": [{"file": "resume.pdf", "status": "FAILED"}],
            }))
            self.assertEqual(failed["artifact"]["stage"], "finalization_not_applied")

    def test_25c_refresh_reuses_the_action_verb_result_and_repins_the_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, with_pdf=True)
            first = self._generate(job)
            self.assertEqual(first["artifact"]["stage"], "pre_finalization")
            original_sha = first["artifact"]["sha256"]

            # finalization promotes different bytes over resume.pdf
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            y = 40
            for line in SYNTHETIC_DRAFT.splitlines():
                if line.strip():
                    page.insert_text((40, y), line.replace("\t", "  "), fontsize=7)
                y += 9
            page.insert_text((40, y + 20), " ", fontsize=7)
            doc.save(str(job / "resume.pdf"))
            doc.close()
            (job / "watermark_finalization.json").write_text(json.dumps({
                "job": "synthetic-job-key", "overall_status": "FINALIZED",
                "artifacts": [{"file": "resume.pdf", "status": "PASS"}],
            }))

            with mock.patch.object(
                action_verb_diversity, "analyze_action_verbs_from_file",
                side_effect=AssertionError("refresh must not re-run the action-verb checker"),
            ):
                refreshed = resume_quality_report.refresh_after_finalization(
                    job, "synthetic-job-key",
                    runtime_context_path=job / "_runtime.json",
                    metrics_registry_path=job / "_registry.json",
                )

            self.assertEqual(refreshed["artifact"]["stage"], "finalized")
            self.assertEqual(
                refreshed["reused_results"]["action_verb_diversity"],
                first["reused_results"]["action_verb_diversity"],
            )
            self.assertIsNotNone(refreshed["artifact"]["sha256"])
            self.assertNotEqual(refreshed["artifact"]["sha256"], original_sha)

    def test_25d_report_generation_never_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty-job"
            empty.mkdir()
            report = resume_quality_report.generate(
                empty, "empty-job",
                runtime_context_path=empty / "absent.json",
                metrics_registry_path=empty / "absent.json",
            )
            self.assertEqual(report["diagnostic_state"], "INCOMPLETE")
            self.assertTrue((empty / "resume_quality_report.json").exists())
            self.assertFalse(report["artifact"]["exists"])

            with mock.patch.object(
                resume_quality_report, "build_report", side_effect=RuntimeError("boom"),
            ):
                degraded = resume_quality_report.generate(empty, "empty-job")
            self.assertEqual(degraded["diagnostic_state"], "INCOMPLETE")
            self.assertTrue(any("boom" in note for note in degraded["notes"]))

    def test_25e_console_summary_carries_no_candidate_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, with_pdf=True)
            report = self._generate(job)
            summary = resume_quality_report.console_summary(report, job)

        self.assertEqual(
            set(summary),
            {"path", "diagnostic_state", "artifact_stage", "summary_by_severity", "advisory_only"},
        )
        flattened = json.dumps(summary)
        for private in ("Synthetic Candidate", "Synthetic Works", "Sample Systems",
                        "synthetic@example.com", "45ms", "Synthetic Institute"):
            self.assertNotIn(private, flattened)

    def test_25f_a_real_pdf_round_trip_reaches_the_report_as_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            # a draft whose PROJECTS bullet is never rendered into the PDF
            job = self._job(tmp, with_pdf=False)
            import fitz
            doc = fitz.open()
            page = doc.new_page()
            y = 40
            for line in SYNTHETIC_DRAFT.splitlines():
                if "Documented the indexing contract" in line:
                    continue
                if line.strip():
                    page.insert_text((40, y), line.replace("\t", "  "), fontsize=7)
                y += 9
            doc.save(str(job / "resume.pdf"))
            doc.close()

            report = self._generate(job)

        recall = next(c for c in report["checks"] if c["id"] == "artifact_integrity.content_recall")
        self.assertEqual(recall["status"], "fail")
        missing = [f for f in report["findings"] if f["id"].endswith("content_recall.missing")]
        self.assertEqual([f["subject"] for f in missing], ["projects.bullet.1"])
        self.assertEqual(missing[0]["severity"], "high")
        self.assertEqual(report["diagnostic_state"], "REVIEW")

    # --- regression: the failing-density branch ---------------------------
    # `_density_check` is an `artifact_integrity` producer, so its finding
    # has to name an artifact like every other finding in that category.
    # It once referenced a `subject` it never bound, so the ONLY branch
    # that builds a finding -- `density_target_pass is False` -- raised
    # NameError. `generate` swallows anything a section builder raises, so
    # the symptom was not a crash: the whole report silently degraded to
    # INCOMPLETE and the density finding was lost. These tests pin the
    # branch itself, not just the happy path around it.

    DENSITY_FAIL_QA = {
        "status": "FAIL", "page_count": 1, "replacement_character_present": False,
        "density_target_pass": False, "usable_bottom_whitespace_in": 0.08,
    }

    def test_25g_failing_page_density_builds_a_structurally_valid_finding(self):
        checks, findings = resume_quality_report._density_check(
            self.DENSITY_FAIL_QA, {"status": "DENSE_ONE_PAGE"},
        )

        self.assertEqual([c["id"] for c in checks], ["artifact_integrity.page_density"])
        self.assertEqual(checks[0]["status"], "review")

        self.assertEqual(len(findings), 1)
        finding = findings[0]
        # the same structural contract test_23 enforces across the report
        self.assertEqual(tuple(finding), REQUIRED_FINDING_FIELDS)
        self.assertEqual(finding["id"], "artifact_integrity.page_density")
        self.assertEqual(finding["category"], "artifact_integrity")
        # Advisory restatement of a gate this report does not own: `info`,
        # so it can never escalate the diagnostic state on its own.
        self.assertEqual(finding["severity"], "info")
        self.assertEqual(finding["status"], "review")
        self.assertTrue(finding["subject"])
        self.assertTrue(finding["message"] and finding["recommendation"])
        # the finding restates the existing measurement rather than recomputing it
        self.assertEqual(finding["evidence"]["source"], "resume_qa.json")
        self.assertIs(finding["evidence"]["density_target_pass"], False)
        self.assertEqual(finding["evidence"]["density_guard_status"], "DENSE_ONE_PAGE")

    def test_25g2_the_density_subject_defaults_to_the_generic_artifact_name(self):
        # No deployment-specific filename may ever be implied by this
        # module. With nothing resolved, the subject is this repository's
        # own generic default; with an artifact resolved, it names the
        # file that was actually inspected -- the same rule
        # `_artifact_findings` uses.
        _checks, findings = resume_quality_report._density_check(self.DENSITY_FAIL_QA, None)
        self.assertEqual(findings[0]["subject"], resume_quality_report.RESUME_PDF_NAME)
        self.assertEqual(findings[0]["subject"], "resume.pdf")

        _checks, resolved_findings = resume_quality_report._density_check(
            self.DENSITY_FAIL_QA, None,
            {"name": "synthetic-candidate-resume.pdf", "expected": "*-resume.pdf"},
        )
        self.assertEqual(resolved_findings[0]["subject"], "synthetic-candidate-resume.pdf")

        # resolution failed, so the name looked for is what gets reported
        _checks, missing_findings = resume_quality_report._density_check(
            self.DENSITY_FAIL_QA, None, {"name": None, "expected": "*-resume.pdf"},
        )
        self.assertEqual(missing_findings[0]["subject"], "*-resume.pdf")

    def test_25g3_every_density_state_is_reachable_without_raising(self):
        # The defect was reachable only on one of four states, which is
        # exactly why it survived. Exercise all of them directly, outside
        # `generate`'s except clause, so a future regression surfaces as a
        # test error instead of a silently INCOMPLETE report.
        for qa, expected_status, expected_findings in (
            (self.DENSITY_FAIL_QA, "review", 1),
            ({"status": "PASS", "density_target_pass": True}, "pass", 0),
            ({"status": "PASS", "density_target_pass": None}, "not_evaluated", 0),
            ({"status": "PASS"}, "not_evaluated", 0),
            ({}, "not_evaluated", 0),
            (None, "not_evaluated", 0),
        ):
            with self.subTest(qa=qa):
                checks, findings = resume_quality_report._density_check(qa, None)
                self.assertEqual(checks[0]["status"], expected_status)
                self.assertEqual(len(findings), expected_findings)

    def test_25h_a_density_failure_alone_does_not_degrade_the_whole_report(self):
        # End-to-end through `generate`. A failing density measurement is
        # ordinary, expected input: it must add its own advisory finding
        # and change nothing else. Comparing against an otherwise
        # identical passing-density job is what distinguishes "this report
        # legitimately needs review" from "a section builder blew up and
        # `generate` caught it".
        with tempfile.TemporaryDirectory() as tmp:
            baseline = self._generate(self._job(tmp, with_pdf=True))
        with tempfile.TemporaryDirectory() as tmp:
            variant = self._generate(
                self._job(tmp, with_pdf=True, qa=dict(self.DENSITY_FAIL_QA)),
            )

        # no section builder failed in either run
        for report in (baseline, variant):
            for note in report["notes"]:
                self.assertNotIn("report generation failed", note)
                self.assertNotIn("NameError", note)

        # the density failure introduced no NEW unevaluated check, which is
        # what an exception inside a section builder would have produced
        self.assertEqual(
            sorted(c["id"] for c in variant["checks"] if c["status"] == "not_evaluated"),
            sorted(c["id"] for c in baseline["checks"] if c["status"] == "not_evaluated"),
        )

        density = next(
            c for c in variant["checks"] if c["id"] == "artifact_integrity.page_density"
        )
        self.assertEqual(density["status"], "review")
        self.assertEqual(
            next(c for c in baseline["checks"]
                 if c["id"] == "artifact_integrity.page_density")["status"],
            "pass",
        )

        # exactly one added finding, and it is the density one
        added = [f for f in variant["findings"]
                 if f["id"] == "artifact_integrity.page_density"]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["severity"], "info")
        self.assertEqual(
            [f for f in baseline["findings"]
             if f["id"] == "artifact_integrity.page_density"], [],
        )
        self.assertEqual(
            variant["summary_by_severity"]["total"],
            baseline["summary_by_severity"]["total"] + 1,
        )

        # an `info` finding cannot escalate the state, so it is unchanged --
        # and in particular it is not INCOMPLETE
        self.assertNotEqual(variant["diagnostic_state"], "INCOMPLETE")
        self.assertEqual(variant["diagnostic_state"], baseline["diagnostic_state"])

    def test_25i_a_raising_section_builder_degrades_the_report_but_never_the_job(self):
        # The failure-safe contract that MASKED the density defect, pinned
        # explicitly. It is correct and must stay: an advisory diagnostic
        # may never propagate into `run_job_pipeline`. What it must not do
        # is fail silently -- the report still has to be written and still
        # has to say, in its own notes, that it could not be built.
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, with_pdf=True, qa=dict(self.DENSITY_FAIL_QA))
            with mock.patch.object(
                resume_quality_report, "_density_check",
                side_effect=NameError("name 'subject' is not defined"),
            ):
                report = self._generate(job)

            self.assertTrue((job / "resume_quality_report.json").exists())
            on_disk = json.loads(
                (job / "resume_quality_report.json").read_text(encoding="utf-8")
            )

        self.assertEqual(report["diagnostic_state"], "INCOMPLETE")
        self.assertTrue(report["advisory_only"])
        self.assertTrue(
            any("subject" in note and "NameError" in note for note in report["notes"]),
            report["notes"],
        )
        self.assertEqual(on_disk["diagnostic_state"], "INCOMPLETE")
        self.assertEqual(report["written_to"], "resume_quality_report.json")



# --------------------------------------------------------------------------
# Pipeline integration + preserved behavior
# --------------------------------------------------------------------------

class TestPipelineIntegration(unittest.TestCase):

    def test_report_is_generated_from_the_deterministic_pipeline_not_standalone(self):
        source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("import resume_quality_report", source)
        self.assertIn("resume_quality_report.generate(", source)
        self.assertIn('result["quality_report"]', source)

    def test_report_is_repinned_to_the_shipped_artifact_after_finalization(self):
        source = (V86_DIR / "run_batch.py").read_text(encoding="utf-8")
        self.assertIn("resume_quality_report.refresh_after_finalization(out_dir, job_key)", source)
        # ... and strictly AFTER the finalizer has run
        self.assertLess(
            source.index("finalize_local.py"),
            source.index("refresh_after_finalization"),
        )
        self.assertLess(
            source.index("refresh_after_finalization"),
            source.index("tracker_writer.py"),
        )

    def test_generation_sits_after_every_gate_decision(self):
        source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        self.assertLess(source.index('result["status"] = "LOCAL_QUALITY_GATE_FAILED"'),
                        source.index("resume_quality_report.generate("))
        self.assertLess(source.index("resume_quality_report.generate("),
                        source.index('result["status"] = "RESUME_READY_WITH_WARNINGS"'))

    def test_gate_semantics_correction_budget_and_success_statuses_are_unchanged(self):
        source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        self.assertEqual(deterministic_pipeline.CORRECTION_ROUND_LIMIT, 2)
        self.assertEqual(
            deterministic_pipeline.SUCCESS_STATUSES,
            ("RESUME_READY", "RESUME_READY_WITH_WARNINGS"),
        )
        # the success predicate still depends only on the pre-existing signals
        predicate = source[source.index("    success = ("):source.index("    if not success:")]
        self.assertIn("ats_pass_effective", predicate)
        self.assertIn('qa.get("status") == "PASS"', predicate)
        self.assertIn('qa.get("density_target_pass") is True', predicate)
        self.assertNotIn("quality_report", predicate)
        self.assertNotIn("resume_quality", predicate)
        # correction reasons are untouched by the new diagnostics
        reasons = re.findall(r'correction_reasons\.append\("([A-Z_]+)"\)', source)
        self.assertEqual(sorted(reasons), ["ACTION_VERB_DIVERSITY", "ATS", "DENSITY", "MULTIPAGE"])

    def test_no_new_dependency_and_no_model_call_was_introduced(self):
        new_modules = [
            "resume_draft_model.py", "pdf_roundtrip.py", "metrics_audit.py",
            "metrics_registry_adapter.py",
            "bullet_length_audit.py", "timeline_audit.py", "resume_quality_report.py",
        ]
        allowed_third_party = {"fitz"}
        for name in new_modules:
            tree = ast.parse((V86_DIR / name).read_text(encoding="utf-8"), filename=name)
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
            with self.subTest(module=name):
                self.assertTrue(
                    imports <= {
                        "__future__", "difflib", "json", "re", "sys", "unicodedata",
                        "pathlib", "typing",
                        "qa_resume", "payload_preflight", "common", "bullet_length_audit",
                        "metrics_audit", "metrics_registry_adapter", "pdf_roundtrip",
                        "resume_draft_model", "timeline_audit",
                    } | allowed_third_party,
                    f"{name} imports something unexpected: {imports}",
                )
                # nothing network-, subprocess- or model-shaped can be
                # reached from a module that never imports any of it
                for forbidden in ("llm_invoke", "anthropic", "urllib", "http", "socket",
                                  "subprocess", "semantic_worker", "correction_worker"):
                    self.assertNotIn(forbidden, imports, f"{name} must not import {forbidden}")

    def test_requirements_file_is_unchanged_by_this_feature(self):
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        self.assertIn("pymupdf", requirements)
        for not_added in ("numpy", "pandas", "scikit", "rapidfuzz", "fuzzywuzzy", "nltk", "spacy"):
            self.assertNotIn(not_added, requirements)

    def test_batch_summary_surfaces_the_state_but_no_candidate_content(self):
        import run_batch

        metrics = {
            "batch_id": "batch-synthetic-regression", "batch_start": "s", "batch_end": "e",
            "wall_duration_ms": 1.0, "jobs_discovered": 1, "jobs_succeeded": 1,
            "jobs_failed": 0, "jobs_corrected": 0, "jobs_quality_review": 1,
            "max_workers": 1, "peak_semantic_concurrency": 1,
            "peak_deterministic_concurrency": 1, "peak_finalization_concurrency": 1,
            "total_claude_requests": 2, "total_claude_cost_usd": 0.0,
            "jobs": [{
                "job": "synthetic-job-key", "status": "RESUME_READY_WITH_WARNINGS",
                "quality_report": {
                    "path": "output/synthetic-job-key/resume_quality_report.json",
                    "diagnostic_state": "REVIEW", "artifact_stage": "finalized",
                    "summary_by_severity": {"high": 0, "medium": 0, "low": 1, "info": 1, "total": 2},
                    "advisory_only": True,
                },
            }],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch_metrics.txt"
            run_batch._write_metrics_txt(path, metrics)
            text = path.read_text()

        self.assertIn("jobs_quality_review (advisory): 1", text)
        self.assertIn("synthetic-job-key: RESUME_READY_WITH_WARNINGS", text)
        self.assertIn("[quality: REVIEW @ finalized]", text)
        # the advisory count is never folded into succeeded/failed
        self.assertIn("jobs_succeeded: 1", text)
        self.assertIn("jobs_failed: 0", text)

        # a job with no report at all still renders exactly as before
        metrics["jobs"][0].pop("quality_report")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "batch_metrics.txt"
            run_batch._write_metrics_txt(path, metrics)
            self.assertIn(
                "  synthetic-job-key: RESUME_READY_WITH_WARNINGS\n", path.read_text(),
            )

    def test_qa_extraction_is_shared_rather_than_duplicated(self):
        qa_source = (ROOT / "tools/resume/qa_resume.py").read_text(encoding="utf-8")
        roundtrip_source = (V86_DIR / "pdf_roundtrip.py").read_text(encoding="utf-8")
        # exactly one place in the repository joins page text into the
        # resume's text layer, and the round-trip check calls into it
        self.assertEqual(
            qa_source.count('"\\n".join(page.get_text("text") for page in doc)'), 1,
        )
        self.assertNotIn("get_text", roundtrip_source)
        self.assertIn("qa_resume.extract_pdf_text", roundtrip_source)
        # QA's own public behavior is unchanged
        self.assertIn('"replacement_character_present": "�" in full_text', qa_source)
        self.assertIn('result["status"] = "FAIL"', qa_source)


class TestRealDeterministicPipelineWritesTheReport(unittest.TestCase):
    """Drives the REAL `run_job_pipeline` success path (same shared
    fixture and same budget-exhausted setup the action-verb suite already
    uses) to prove the report is produced by the pipeline rather than
    existing as an unused standalone utility. Skips cleanly on a checkout
    without the private template/layout the renderer needs."""

    FIXTURE = V86_DIR / "tests" / "fixtures" / "live13_11_recommendation"
    EVIDENCE_ROOT = FIXTURE / "shared_ats_evidence"
    NEEDED = (
        "resume_payload.json", "cover_letter_payload.json", "v8_strategy_status.json",
        "project_selection.json", "v8_contract_validation.json",
        "v8_project_selection_validation.json", "payload_normalization.json",
        "payload_preflight.json", "jd_requirements.json",
    )
    JOB_KEY = "v869-test-quality-report"

    def setUp(self):
        import shutil

        from common import write_json

        if not all((self.FIXTURE / n).exists() for n in self.NEEDED) or not self.EVIDENCE_ROOT.exists():
            self.skipTest("shared pipeline fixture not present")
        if not (ROOT / "reference" / "resume_template_layout.json").exists():
            self.skipTest("no private resume template layout on this checkout")

        self.job_dir = ROOT / "output" / self.JOB_KEY
        shutil.rmtree(self.job_dir, ignore_errors=True)
        self.job_dir.mkdir(parents=True)
        for name in self.NEEDED:
            shutil.copy(self.FIXTURE / name, self.job_dir / name)
        # the same "correction budget already exhausted" shape the
        # action-verb suite uses to reach the terminal success path
        write_json(self.job_dir / "v8_correction_status.json",
                   {"correction_round": 2, "status": "CORRECTED"})

    def tearDown(self):
        import shutil

        shutil.rmtree(getattr(self, "job_dir", Path("/nonexistent")), ignore_errors=True)

    def test_success_path_writes_a_job_local_report_and_surfaces_its_state(self):
        result = deterministic_pipeline.run_job_pipeline(
            self.job_dir, self.EVIDENCE_ROOT, self.JOB_KEY,
        )
        if result["status"] not in deterministic_pipeline.SUCCESS_STATUSES:
            self.skipTest(f"pipeline did not reach its success path here: {result['status']}")

        written = self.job_dir / "resume_quality_report.json"
        self.assertTrue(written.exists(), "the pipeline must write the report itself")
        report = json.loads(written.read_text())

        self.assertEqual(report["job"], self.JOB_KEY)
        self.assertEqual(report["schema_version"], "1.0")
        self.assertTrue(report["artifact"]["exists"])
        self.assertRegex(report["artifact"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(report["artifact"]["stage"], "pre_finalization")

        # the real PDF round-trip actually ran against the rendered file
        text_layer = next(c for c in report["checks"] if c["id"] == "artifact_integrity.text_layer")
        self.assertEqual(text_layer["status"], "pass")
        recall = next(c for c in report["checks"] if c["id"] == "artifact_integrity.content_recall")
        self.assertIn(recall["status"], ("pass", "review", "fail"))

        # the action-verb result was reused, not recomputed
        self.assertEqual(
            report["reused_results"]["action_verb_diversity"],
            result["action_verb_diversity"],
        )

        # the job result carries only the summary, never candidate content
        summary = result["quality_report"]
        self.assertEqual(summary["path"], f"output/{self.JOB_KEY}/resume_quality_report.json")
        self.assertIn(summary["diagnostic_state"], ("PASS", "REVIEW", "INCOMPLETE"))
        self.assertTrue(summary["advisory_only"])

        # and the advisory changed nothing about the gate outcome
        self.assertIn(result["status"], deterministic_pipeline.SUCCESS_STATUSES)
        self.assertIsNone(result.get("correction_request"))


class TestPrivacyRestrictionsStillHold(unittest.TestCase):

    NEW_FILES = (
        "tools/resume/v86/resume_draft_model.py",
        "tools/resume/v86/pdf_roundtrip.py",
        "tools/resume/v86/metrics_audit.py",
        "tools/resume/v86/metrics_registry_adapter.py",
        "tools/resume/v86/bullet_length_audit.py",
        "tools/resume/v86/timeline_audit.py",
        "tools/resume/v86/resume_quality_report.py",
        "config/v86/resume_quality_diagnostics.json",
        "tools/resume/v86/tests/test_v86_9_resume_quality_report.py",
    )

    def test_new_files_contain_no_real_contact_details_or_home_paths(self):
        email = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
        phone = re.compile(r"\(\d{3}\)[\s.\-]?\d{3}[\s.\-]\d{4}\b|\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b")
        # Built at runtime so this detector's own pattern is not a literal
        # home path in this file's source.
        home = re.compile("/" + "Users" + r"/(?!example|fake-home|x)")
        for rel in self.NEW_FILES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(file=rel):
                for match in email.finditer(text):
                    self.assertTrue(
                        match.group(0).rsplit("@", 1)[-1].lower().endswith(("example.com", ".invalid")),
                        f"{rel}: {match.group(0)}",
                    )
                for match in phone.finditer(text):
                    self.assertIn("555", match.group(0), f"{rel}: {match.group(0)}")
                self.assertIsNone(home.search(text), f"{rel} contains an absolute home path")

    def test_new_modules_never_read_private_candidate_files_implicitly(self):
        """Only the consolidated report builds a path into the private
        candidate area, and only as an overridable default that degrades
        to `not_evaluated` when the file is absent."""
        for rel in self.NEW_FILES:
            if not rel.endswith(".py"):
                continue
            if rel.endswith(("resume_quality_report.py", "test_v86_9_resume_quality_report.py")):
                continue
            text = (ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(file=rel):
                self.assertNotIn('ROOT / "master"', text)
                self.assertNotIn('"master" /', text)

        report_source = (V86_DIR / "resume_quality_report.py").read_text(encoding="utf-8")
        self.assertIn("DEFAULT_RUNTIME_CONTEXT", report_source)
        self.assertIn("runtime_context_path", report_source)
        self.assertIn("metrics_registry_path", report_source)

    def test_no_candidate_specific_values_leak_into_tracked_artifacts(self):
        config = (ROOT / "config/v86/resume_quality_diagnostics.json").read_text(encoding="utf-8")
        data = json.loads(config)
        for key, value in data.items():
            if key.startswith("_") or key == "schema_version":
                continue
            self.assertIsInstance(value, (int, float), f"{key} must be a plain threshold")


if __name__ == "__main__":
    unittest.main(verbosity=2)
