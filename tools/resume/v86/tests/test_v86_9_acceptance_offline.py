#!/usr/bin/env python3
"""
V8.6.9 -- OFFLINE ACCEPTANCE suite for the resume-quality diagnostics.

This is the independent acceptance layer that sits on top of the
unit/behaviour suite. Where that suite checks each module's contract,
this one exercises the REAL code paths end to end against REAL synthetic
PDFs rendered with the already-present `fitz` dependency, with the only
mock being the external watermark-cleaning HTTP service.

ZERO model calls, ZERO network, ZERO paid APIs, ZERO reads of private
candidate data, ZERO writes outside a temporary directory (except the
two subprocess checks, which write only into their own temp dirs).
Every candidate, employer, institution, project, date, bullet and metric
is wholly synthetic and written inline here.

Sections:

  A. Defect regressions found during acceptance testing
     A1 word-unit measurements (`4 hours`, `35 minutes`) must be
        matchable, not silently unevaluable
     A2 a report-write failure must never escape into the pipeline
     A3 an uncovered interval with unusable education dates is
        `undetermined`, not `unexplained`
     A4 "finalization has not run yet" vs "finalization ran and promoted
        nothing" must not be conflated

  B. Real-PDF diagnostics
     B1 correctly extracted fields, genuinely missing content, partial
        matches, corrupted glyphs -- all on rendered PDFs
     B2 reading order: single-column and in-order two-column produce no
        inversions; a genuinely swapped two-column export is caught
     B3 an image-only PDF yields an honest unevaluated result

  C. Offline end-to-end acceptance
     C1 draft -> fields -> rendered PDF -> consolidated report
     C2 the real finalizer (HTTP mocked) promotes cleaned bytes and the
        refreshed report's stage + sha256 match the final file on disk
     C3 a failed finalization is never reported as `finalized`
     C4 a diagnostic exception cannot change a job status or gate
     C5 the action-verb checker runs exactly once per job

  D. Preserved-behaviour invariants
     D1 success statuses, correction reasons, correction-round limit
     D2 the authoritative layout character budget still gates (real
        subprocess, real exit codes)
     D3 PDF QA status taxonomy and exit codes are unchanged, and the
        refactored extractor is byte-identical to the original inline one
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

V86_DIR = Path(__file__).resolve().parents[1]
ROOT = V86_DIR.parents[2]
sys.path.insert(0, str(V86_DIR))

import fitz  # noqa: E402  (already a hard dependency of qa_resume/render)

import bullet_length_audit  # noqa: E402
import deterministic_pipeline  # noqa: E402
import finalize_local  # noqa: E402
import metrics_audit  # noqa: E402
import pdf_roundtrip  # noqa: E402
import resume_draft_model  # noqa: E402
import resume_quality_report  # noqa: E402
import timeline_audit  # noqa: E402


ACCEPTANCE_DRAFT = """Synthetic Candidate
synthetic@example.com | (555) 010-7777 | Sample Town, ST

EDUCATION

Synthetic Institute -- M.S. Synthetic Systems
GPA: 3.90/4.00 | Sep 2023 -- Jun 2025

PROFESSIONAL EXPERIENCE

Platform Engineer | Synthetic Works | Remote\tJan 2021 -- Dec 2021
- Rebuilt the batch ingestion path in C++ and cut nightly runtime from 4 hours to 35 minutes.
- Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.

PROJECTS

Synthetic Atlas -- Catalog Indexer  -  C#, PostgreSQL
- Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.
- Documented the indexing contract so a second contributor could extend it safely.

SKILLS

Languages: C++, C#, Python
"""

SYNTHETIC_QA = {
    "status": "PASS", "page_count": 1, "replacement_character_present": False,
    "density_target_pass": True, "usable_bottom_whitespace_in": 0.33,
}

SYNTHETIC_VERB_REPORT = {
    "counts": {"rebuilt": 1, "introduced": 1, "indexed": 1, "documented": 1},
    "repeated_over_limit": {}, "limit": 2,
}


def render_pdf(path: Path, lines, two_column: bool = False, swap_columns: bool = False) -> Path:
    """Render synthetic lines into a REAL PDF with a real text layer."""
    doc = fitz.open()
    page = doc.new_page()
    if not two_column:
        y = 40
        for line in lines:
            if line.strip():
                page.insert_text((40, y), line.replace("\t", "   "), fontsize=7)
            y += 10
    else:
        half = len(lines) // 2
        left, right = lines[:half], lines[half:]
        if swap_columns:
            left, right = right, left
        for column_x, column in ((40, left), (320, right)):
            y = 40
            for line in column:
                if line.strip():
                    page.insert_text((column_x, y), line.replace("\t", "   "), fontsize=6)
                y += 9
    doc.save(str(path))
    doc.close()
    return path


def render_pdf_unicode(path: Path, lines) -> Path:
    """Render with an embedded font that actually carries the non-Latin-1
    codepoints.

    `page.insert_text` uses the base-14 fonts, whose WinAnsi encoding
    substitutes U+00B7 for anything it cannot encode -- so an en dash or
    an fi-ligature authored that way never reaches the text layer at all.
    Going through `TextWriter` with a built-in CJK font round-trips them
    for real, which is what makes the normalization test below a genuine
    PDF test rather than a test of PyMuPDF's font fallback.
    """
    doc = fitz.open()
    page = doc.new_page()
    font = fitz.Font("cjk")
    writer = fitz.TextWriter(page.rect)
    y = 40
    for line in lines:
        if line.strip():
            writer.append((40, y), line.replace("\t", "   "), font=font, fontsize=7)
        y += 10
    writer.write_text(page)
    doc.save(str(path))
    doc.close()
    return path


def draft_fields(draft: str = ACCEPTANCE_DRAFT) -> list:
    return resume_draft_model.parse_resume_markdown(draft)["fields"]


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_job(tmp: Path, name: str = "synthetic-acceptance-job", draft: str = ACCEPTANCE_DRAFT,
              with_pdf: bool = True, qa: dict = None) -> Path:
    job = Path(tmp) / name
    job.mkdir(parents=True, exist_ok=True)
    (job / "resume_draft.md").write_text(draft)
    (job / "resume_payload.json").write_text(json.dumps({
        "experience_alternate_bullets": {},
        "selected_project": {"owner_id": "unbound", "title": "t",
                             "technology_line": "t", "bullets": []},
    }))
    (job / "resume_qa.json").write_text(json.dumps(qa if qa is not None else SYNTHETIC_QA))
    if with_pdf:
        render_pdf(job / "resume.pdf", draft.splitlines())
    return job


def generate_for(job: Path, key: str = "synthetic-acceptance-job", **kwargs) -> dict:
    """Always points the private-data defaults at absent paths, so no
    acceptance test can ever read real candidate data."""
    kwargs.setdefault("action_verb_report", SYNTHETIC_VERB_REPORT)
    kwargs.setdefault("runtime_context_path", job / "_absent_runtime.json")
    kwargs.setdefault("metrics_registry_path", job / "_absent_registry.json")
    return resume_quality_report.generate(job, key, **kwargs)


def fake_clean_service(mutate_text: bool = False):
    """Stands in for the local watermark-cleaning HTTP service only.
    Everything else in `finalize_local` runs for real."""
    def _call(url, payload=None, timeout=30.0):
        if url.endswith("/capabilities"):
            return {"layers": ["A"]}
        if url.endswith("/inspect"):
            return {"ok": True, "marks": []}
        if url.endswith("/clean"):
            raw = base64.b64decode(payload["file"])
            if mutate_text:
                doc = fitz.open()
                doc.new_page().insert_text((40, 40), "entirely different content", fontsize=8)
                out = doc.tobytes()
                doc.close()
            else:
                doc = fitz.open(stream=raw, filetype="pdf")
                doc.set_metadata({"producer": "synthetic-cleaner"})
                out = doc.tobytes()
                doc.close()
            return {"ok": True, "cleaned": base64.b64encode(out).decode(), "report": {"layer_a": "ok"}}
        raise AssertionError(f"unexpected service call: {url}")
    return _call


# ===========================================================================
# A. Defect regressions
# ===========================================================================

class TestA1WordUnitMeasurementsAreMatchable(unittest.TestCase):
    """A measurement written with a word unit used to be extracted as a
    joined token (`4hours`) and then searched for in text that spells it
    `4 hours`, so it could never be found -- every hours/minutes/days
    metric silently became `not_evaluated` and a genuinely dropped
    improvement was never reported."""

    CONTEXT = {
        "experience": [{
            "id": "experience_1", "employer": "Synthetic Works",
            "dates": "Jan 2021 -- Dec 2021", "tailoring_mode": "tailorable",
            "alternate_bullets_for_dynamic_tailoring": [
                "Rebuilt the batch ingestion path and cut nightly runtime from 4 hours to 35 minutes.",
            ],
        }],
        "projects": [],
    }
    REGISTRY = {"metrics": [{
        "id": "nightly_runtime_cut", "owner_id": "experience_1",
        "statement": "Cut nightly batch runtime from 4 hours to 35 minutes.",
        "verified": True, "source": "synthetic_facts.md#batch",
    }]}

    def _audit(self, bullets):
        return metrics_audit.audit_metrics(self.CONTEXT, self.REGISTRY, {
            "experience_alternate_bullets": {"experience_1": bullets},
            "selected_project": {"owner_id": "unbound", "bullets": []},
        })["results"][0]

    def test_word_unit_token_is_found_in_spaced_text(self):
        for spelling in ("4 hours", "4hours", "4  hours"):
            with self.subTest(spelling=spelling):
                self.assertEqual(
                    metrics_audit.measurement_present(["4hours"], [f"cut it to {spelling} flat"]),
                    ["4hours"],
                )

    def test_dropped_word_unit_measurement_is_reported_as_omitted(self):
        row = self._audit(["Sped up the nightly batch job considerably."])
        self.assertEqual(row["status"], "omitted")
        self.assertEqual(row["measurements"], ["4hours", "35minutes"])
        self.assertIsNotNone(row["source_bullet_index"])

    def test_retained_word_unit_measurement_is_reported_as_present(self):
        row = self._audit(["Cut nightly runtime from 4 hours to 35 minutes."])
        self.assertEqual(row["status"], "present")
        self.assertEqual(sorted(row["measurements_present"]), ["35minutes", "4hours"])

    def test_boundaries_stay_strict_after_the_fix(self):
        # a longer number must not satisfy a shorter one
        self.assertEqual(metrics_audit.measurement_present(["18000"], ["served 180000 rps"]), [])
        self.assertEqual(metrics_audit.measurement_present(["45ms"], ["took 245ms"]), [])
        # and a unit mismatch is still a miss
        self.assertEqual(metrics_audit.measurement_present(["4hours"], ["took 4 days"]), [])


class TestA2ReportWriteFailureNeverEscapes(unittest.TestCase):
    """`write_json` used to sit outside the failure-safe boundary, so a
    read-only job directory raised out of `generate` -> out of
    `run_job_pipeline` (contractually non-raising) -> out of
    `run_batch.process_job`'s try/finally, where `Batch.run`'s
    `future.result()` would re-raise and kill the whole batch."""

    def test_unwritable_job_directory_is_reported_not_raised(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp), with_pdf=False)
            os.chmod(job, 0o500)  # read + execute only
            try:
                report = generate_for(job)
            finally:
                os.chmod(job, 0o700)

        self.assertIsNone(report["written_to"])
        self.assertEqual(report["diagnostic_state"], "INCOMPLETE")
        self.assertTrue(
            any("could not be written" in note for note in report["notes"]),
            report["notes"],
        )

    def test_successful_write_records_where_it_landed(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            report = generate_for(job)
            self.assertEqual(report["written_to"], "resume_quality_report.json")
            self.assertTrue((job / "resume_quality_report.json").exists())

    def test_both_call_sites_additionally_refuse_to_propagate(self):
        pipeline = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        batch = (V86_DIR / "run_batch.py").read_text(encoding="utf-8")
        for label, source, marker in (
            ("deterministic_pipeline", pipeline, "resume_quality_report.generate("),
            ("run_batch", batch, "resume_quality_report.refresh_after_finalization("),
        ):
            with self.subTest(call_site=label):
                index = source.index(marker)
                window = source[max(0, index - 400):index + 700]
                self.assertIn("try:", window)
                self.assertIn("except Exception", window)


class TestA3UncoveredIntervalWithUnusableEducationDates(unittest.TestCase):
    """An interval that no USABLE education period covers, while some
    education entry's own dates are unusable, cannot be called
    `unexplained` -- whether it is explained is simply unknown."""

    def _audit(self, education):
        return timeline_audit.audit_timeline({
            "experience": [
                {"id": "experience_1", "dates": "Jan 2020 -- Dec 2020"},
                {"id": "experience_2", "dates": "Jan 2022 -- Aug 2023"},
            ],
            "education": [{"id": f"education_{i}", "dates": d} for i, d in enumerate(education)],
        })

    def test_year_only_education_yields_undetermined_not_unexplained(self):
        interval = self._audit(["2021"])["intervals"][0]
        self.assertEqual(interval["classification"], "undetermined")
        self.assertIn("cannot be established", interval["reason"])

    def test_unparseable_education_yields_undetermined(self):
        interval = self._audit(["sometime around then"])["intervals"][0]
        self.assertEqual(interval["classification"], "undetermined")

    def test_no_education_at_all_is_still_honestly_unexplained(self):
        interval = self._audit([])["intervals"][0]
        self.assertEqual(interval["classification"], "unexplained")

    def test_usable_education_still_explains_normally(self):
        covered = self._audit(["Jan 2021 -- Dec 2021"])["intervals"][0]
        self.assertEqual(covered["classification"], "explained_by_education")
        self.assertEqual(covered["education_months_covered"], covered["months"])

        partial = self._audit(["Jan 2021 -- Jun 2021"])["intervals"][0]
        self.assertEqual(partial["classification"], "partially_explained")
        self.assertEqual(partial["education_months_covered"], 6)

    def test_undetermined_is_still_only_informational(self):
        audit = self._audit(["2021"])
        checks, findings = resume_quality_report._timeline_findings(audit)
        self.assertEqual([f["severity"] for f in findings], ["info"])
        self.assertEqual(findings[0]["status"], "undetermined")
        summary = resume_quality_report.summarize(findings)
        self.assertEqual(resume_quality_report.diagnostic_state(checks, summary), "PASS")


class TestA4FinalizationAttemptedIsNotConflatedWithNotRunYet(unittest.TestCase):
    """`finalize_local.finalize_job` writes NO record when the cleaning
    service is unavailable. Before the fix the report then still said the
    PDF "may still be replaced by the finalizer", which is false once the
    finalizer has already run and declined."""

    def test_before_finalization_runs_the_stage_is_pre_finalization(self):
        stage, reason = resume_quality_report.artifact_stage(None, finalization_attempted=False)
        self.assertEqual(stage, "pre_finalization")
        self.assertIn("may still be replaced", reason)

    def test_after_finalization_ran_with_no_record_the_stage_is_not_applied(self):
        stage, reason = resume_quality_report.artifact_stage(None, finalization_attempted=True)
        self.assertEqual(stage, "finalization_not_applied")
        self.assertIn("was not replaced", reason)
        self.assertNotIn("may still be replaced", reason)

    def test_the_real_service_unavailable_path_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            record = finalize_local.finalize_job(job, "http://127.0.0.1:8765", "unavailable")
            self.assertEqual(record["overall_status"], "SERVICE_UNAVAILABLE")
            self.assertFalse((job / "watermark_finalization.json").exists())

            before = generate_for(job)
            self.assertEqual(before["artifact"]["stage"], "pre_finalization")

            after = resume_quality_report.refresh_after_finalization(
                job, "synthetic-acceptance-job",
                runtime_context_path=job / "_absent_runtime.json",
                metrics_registry_path=job / "_absent_registry.json",
            )
            self.assertEqual(after["artifact"]["stage"], "finalization_not_applied")
            self.assertEqual(after["artifact"]["sha256"], sha256_of(job / "resume.pdf"))


# ===========================================================================
# B. Real-PDF diagnostics
# ===========================================================================

class TestB1RealPdfFieldRecall(unittest.TestCase):

    def test_all_fields_recovered_from_a_rendered_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", ACCEPTANCE_DRAFT.splitlines())
            result = pdf_roundtrip.run(pdf, draft_fields())
        self.assertEqual(result["status"], "EVALUATED")
        self.assertEqual(result["recall_summary"]["missing"], 0)
        self.assertEqual(result["recall_summary"]["partial"], 0)
        self.assertEqual(result["recall_summary"]["not_evaluated"], 0)
        self.assertGreaterEqual(result["recall_summary"]["found"], 12)

    def test_a_bullet_never_rendered_is_reported_missing_by_identifier(self):
        lines = [l for l in ACCEPTANCE_DRAFT.splitlines()
                 if "Documented the indexing contract" not in l]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", lines)
            result = pdf_roundtrip.run(pdf, draft_fields())
        missing = [r["id"] for r in result["fields"] if r["status"] == "missing"]
        self.assertEqual(missing, ["projects.bullet.1"])

    def test_a_truncated_bullet_is_reported_partial_with_its_ratio(self):
        lines = [
            l.replace(
                "- Indexed a 90k-record synthetic catalog and held p99 lookup latency under 45ms.",
                "- Indexed a 90k-record synthetic catalog and held p99 lookup latency",
            )
            for l in ACCEPTANCE_DRAFT.splitlines()
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", lines)
            result = pdf_roundtrip.run(pdf, draft_fields())
        row = next(r for r in result["fields"] if r["id"] == "projects.bullet.0")
        self.assertEqual(row["status"], "partial")
        self.assertGreaterEqual(row["match_ratio"], result["partial_min_token_ratio"])
        self.assertLess(row["match_ratio"], 1.0)

    def test_a_clean_real_pdf_reports_no_glyph_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", ACCEPTANCE_DRAFT.splitlines())
            result = pdf_roundtrip.run(pdf, draft_fields())
        self.assertEqual(result["glyph_corruption"]["replacement_characters"], 0)
        self.assertEqual(result["glyph_corruption"]["cid_glyph_markers"], 0)
        self.assertEqual(result["glyph_corruption"]["control_characters"], 0)
        self.assertFalse(result["glyph_corruption"]["corrupt"])

    def test_glyph_corruption_detector_over_genuinely_extracted_text(self):
        """A text layer carrying U+FFFD or `(cid:NN)` markers cannot be
        AUTHORED with PyMuPDF's built-in fonts (base-14 substitutes
        U+00B7 for anything WinAnsi cannot encode, and the CJK fonts map
        the U+FFFD glyph to a CJK codepoint). The production renderer is
        Word -> PDF with an embedded Calibri, which is what actually
        produces these failures in the field. So the detector is checked
        against REAL extracted text that has then been corrupted the way
        a broken export corrupts it."""
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", ACCEPTANCE_DRAFT.splitlines())
            import qa_resume
            real_text, _pages = qa_resume.extract_pdf_text(str(pdf))

        self.assertFalse(pdf_roundtrip.measure_glyph_corruption(real_text)["corrupt"])

        # "C++" occurs in both the experience bullet and the skills line
        occurrences = real_text.count("C++")
        self.assertGreater(occurrences, 0)
        replaced = pdf_roundtrip.measure_glyph_corruption(real_text.replace("C++", "C��"))
        self.assertEqual(replaced["replacement_characters"], 2 * occurrences)
        self.assertTrue(replaced["corrupt"])

        cid = pdf_roundtrip.measure_glyph_corruption(real_text.replace("Python", "(cid:51)(cid:92)"))
        self.assertEqual(cid["cid_glyph_markers"], 2)
        self.assertTrue(cid["corrupt"])

        control = pdf_roundtrip.measure_glyph_corruption(real_text + "\x01\x02")
        self.assertEqual(control["control_characters"], 2)
        self.assertTrue(control["corrupt"])

    def test_a_garbled_word_in_a_real_pdf_shows_up_as_a_recall_failure(self):
        """The most common real corruption -- glyphs that extract as the
        WRONG characters -- is caught by field recall rather than by the
        corruption counters, and only the affected field is implicated."""
        lines = [
            l.replace("Indexed a 90k-record synthetic catalog",
                      "Ifnkhwj b 90k-whvtwi dpoubmph")
            for l in ACCEPTANCE_DRAFT.splitlines()
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", lines)
            result = pdf_roundtrip.run(pdf, draft_fields())

        row = next(r for r in result["fields"] if r["id"] == "projects.bullet.0")
        self.assertIn(row["status"], ("missing", "partial"))
        self.assertEqual(
            [r["id"] for r in result["fields"]
             if r["status"] not in ("found",) and r["id"] != "projects.bullet.0"],
            [],
            "only the garbled bullet may be implicated",
        )

    def test_unicode_variants_in_a_real_pdf_do_not_look_like_missing_text(self):
        """Rendered through an embedded font that really carries them, so
        these codepoints genuinely reach the PDF text layer."""
        rendered = [
            l.replace("CI/CD pipeline", "CI/CD pipeline")
             .replace("90k-record", "90k‑record")
             .replace("Synthetic Institute -- M.S.", "Synthetic Institute – M.S.")
             .replace("Catalog Indexer", "Catalog — Indexer")
            for l in ACCEPTANCE_DRAFT.splitlines()
        ]
        expected = [
            l.replace("Catalog Indexer", "Catalog - Indexer")
            for l in ACCEPTANCE_DRAFT.splitlines()
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf_unicode(Path(tmp) / "r.pdf", rendered)
            import qa_resume
            text, _ = qa_resume.extract_pdf_text(str(pdf))
            # the variants really did survive into the text layer
            self.assertIn("–", text)
            self.assertIn("‑", text)
            result = pdf_roundtrip.run(pdf, draft_fields("\n".join(expected)))

        self.assertEqual(
            result["recall_summary"]["missing"], 0,
            [r["id"] for r in result["fields"] if r["status"] != "found"],
        )
        self.assertEqual(result["recall_summary"]["partial"], 0)

    def test_a_dropped_number_is_never_normalized_away(self):
        lines = [
            l.replace("under 45ms.", "to a low level.") for l in ACCEPTANCE_DRAFT.splitlines()
        ]
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", lines)
            result = pdf_roundtrip.run(pdf, draft_fields())
        row = next(r for r in result["fields"] if r["id"] == "projects.bullet.0")
        self.assertNotEqual(row["status"], "found")


class TestB2RealPdfReadingOrder(unittest.TestCase):

    def _order(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", ACCEPTANCE_DRAFT.splitlines(), **kwargs)
            return pdf_roundtrip.run(pdf, draft_fields())

    def test_single_column_pdf_has_no_inversions(self):
        order = self._order()["reading_order"]
        self.assertEqual(order["status"], "evaluated")
        self.assertEqual(order["inversions"], 0)
        self.assertEqual(order["order_fidelity"], 1.0)
        self.assertEqual(order["misordered_elements"], [])

    def test_a_column_layout_is_not_flagged_merely_for_being_columnar(self):
        result = self._order(two_column=True)
        self.assertEqual(result["recall_summary"]["missing"], 0)
        self.assertEqual(result["reading_order"]["inversions"], 0,
                         "a correctly-ordered two-column export must not be penalised")
        self.assertEqual(result["reading_order"]["order_fidelity"], 1.0)

    def test_a_genuinely_swapped_two_column_export_is_caught(self):
        result = self._order(two_column=True, swap_columns=True)
        order = result["reading_order"]
        # the content is all still there -- only its order changed
        self.assertEqual(result["recall_summary"]["missing"], 0)
        self.assertGreater(order["inversions"], 0)
        self.assertLess(order["order_fidelity"], 1.0)
        self.assertTrue(order["misordered_elements"])


class TestB3ImageOnlyPdf(unittest.TestCase):

    def test_no_text_layer_yields_an_honest_unevaluated_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.pdf"
            doc = fitz.open()
            doc.new_page()
            doc.save(str(path))
            doc.close()
            result = pdf_roundtrip.run(path, draft_fields())

        self.assertTrue(result["text_layer"]["empty"])
        self.assertTrue(result["text_layer"]["effectively_unreadable"])
        self.assertEqual(result["recall_summary"]["missing"], 0, "must not cascade into false misses")
        self.assertEqual(result["recall_summary"]["found"], 0)
        self.assertEqual(result["recall_summary"]["not_evaluated"], len(draft_fields()))
        self.assertEqual(result["reading_order"]["status"], "not_evaluated")

    def test_the_report_calls_that_a_high_severity_artifact_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp), with_pdf=False)
            doc = fitz.open()
            doc.new_page()
            doc.save(str(job / "resume.pdf"))
            doc.close()
            report = generate_for(job)

        finding = next(f for f in report["findings"] if f["id"] == "artifact_integrity.text_layer")
        self.assertEqual(finding["severity"], "high")
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(report["diagnostic_state"], "REVIEW")


# ===========================================================================
# C. Offline end-to-end acceptance
# ===========================================================================

class TestCEndToEndAcceptance(unittest.TestCase):

    def test_c1_draft_to_fields_to_pdf_to_consolidated_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            report = generate_for(job)

            # 1. the draft parsed into identified expected fields
            self.assertEqual(report["expected_content_source"]["path"], "resume_draft.md")
            self.assertTrue(report["expected_content_source"]["exists"])
            self.assertEqual(report["expected_content_source"]["substantive_bullets"], 4)
            self.assertGreaterEqual(report["expected_content_source"]["fields_parsed"], 12)

            # 2. the rendered PDF was checked against them
            self.assertTrue(report["artifact"]["exists"])
            self.assertEqual(report["artifact"]["sha256"], sha256_of(job / "resume.pdf"))

            # 3. the report reflects the ACTUAL extraction, not a stub
            recall = next(c for c in report["checks"] if c["id"] == "artifact_integrity.content_recall")
            self.assertEqual(recall["status"], "pass")
            order = next(c for c in report["checks"] if c["id"] == "reading_order.sequence")
            self.assertEqual(order["status"], "pass")

            # 6. a non-batch invocation is honestly pre-finalization
            self.assertEqual(report["artifact"]["stage"], "pre_finalization")

            # written only into the job directory
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), [job.name])
            on_disk = json.loads((job / "resume_quality_report.json").read_text())
            self.assertEqual(on_disk["artifact"]["sha256"], report["artifact"]["sha256"])

    def test_c2_finalization_refreshes_provenance_and_sha_matches_final_pdf(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            pre_sha = sha256_of(job / "resume.pdf")

            pre = generate_for(job)
            self.assertEqual(pre["artifact"]["stage"], "pre_finalization")
            self.assertEqual(pre["artifact"]["sha256"], pre_sha)

            # the REAL finalizer; only the HTTP service is mocked
            with mock.patch.object(finalize_local, "_http_json", side_effect=fake_clean_service()):
                record = finalize_local.finalize_job(job, "http://127.0.0.1:8765", "healthy")
            self.assertEqual(record["overall_status"], "FINALIZED")
            resume_record = next(a for a in record["artifacts"] if a["file"] == "resume.pdf")
            self.assertEqual(resume_record["status"], "PASS")

            post_sha = sha256_of(job / "resume.pdf")
            self.assertNotEqual(post_sha, pre_sha, "the finalizer must have promoted new bytes")

            post = resume_quality_report.refresh_after_finalization(
                job, "synthetic-acceptance-job",
                runtime_context_path=job / "_absent_runtime.json",
                metrics_registry_path=job / "_absent_registry.json",
            )

            self.assertEqual(post["artifact"]["stage"], "finalized")
            self.assertEqual(post["artifact"]["sha256"], post_sha)
            self.assertNotEqual(post["artifact"]["sha256"], pre_sha)
            self.assertIn("final deliverable", post["artifact"]["stage_reason"])
            # the refreshed report still read the real artifact
            recall = next(c for c in post["checks"] if c["id"] == "artifact_integrity.content_recall")
            self.assertEqual(recall["status"], "pass")
            # and the action-verb result was carried, not recomputed
            self.assertEqual(post["reused_results"]["action_verb_diversity"], SYNTHETIC_VERB_REPORT)

    def test_c3_a_failed_finalization_is_never_reported_as_finalized(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            original_sha = sha256_of(job / "resume.pdf")

            with mock.patch.object(
                finalize_local, "_http_json", side_effect=fake_clean_service(mutate_text=True),
            ):
                record = finalize_local.finalize_job(job, "http://127.0.0.1:8765", "healthy")

            self.assertEqual(record["overall_status"], "FAILED")
            self.assertEqual(sha256_of(job / "resume.pdf"), original_sha,
                             "the original must be preserved when cleaning changes the text")

            report = resume_quality_report.refresh_after_finalization(
                job, "synthetic-acceptance-job",
                runtime_context_path=job / "_absent_runtime.json",
                metrics_registry_path=job / "_absent_registry.json",
            )
            self.assertEqual(report["artifact"]["stage"], "finalization_not_applied")
            self.assertNotEqual(report["artifact"]["stage"], "finalized")
            self.assertEqual(report["artifact"]["sha256"], original_sha)

    def test_c4_a_diagnostic_exception_cannot_change_a_status_or_a_gate(self):
        # (a) the generator absorbs an internal failure
        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            with mock.patch.object(
                resume_quality_report, "build_report", side_effect=RuntimeError("synthetic boom"),
            ):
                report = resume_quality_report.generate(job, "synthetic-acceptance-job")
            self.assertEqual(report["diagnostic_state"], "INCOMPLETE")
            self.assertTrue(any("synthetic boom" in n for n in report["notes"]))

        # (b) even a generator that raises outright cannot escape the
        #     deterministic pipeline's own call site
        pipeline_source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        index = pipeline_source.index("resume_quality_report.generate(")
        guarded = pipeline_source[max(0, index - 400):index + 700]
        self.assertIn("except Exception", guarded)

        # (c) the gate decision is computed BEFORE the report is produced
        self.assertLess(
            pipeline_source.index('result["status"] = "LOCAL_QUALITY_GATE_FAILED"'),
            pipeline_source.index("resume_quality_report.generate("),
        )
        success_block = pipeline_source[
            pipeline_source.index("    success = ("):pipeline_source.index("    if not success:")
        ]
        self.assertNotIn("quality", success_block)

    def test_c5_the_action_verb_checker_is_never_run_by_the_report(self):
        import action_verb_diversity

        with tempfile.TemporaryDirectory() as tmp:
            job = build_job(Path(tmp))
            with mock.patch.object(
                action_verb_diversity, "analyze_action_verbs_from_file",
                side_effect=AssertionError("the report must never run the action-verb checker"),
            ), mock.patch.object(
                action_verb_diversity, "analyze_action_verbs",
                side_effect=AssertionError("the report must never run the action-verb checker"),
            ):
                report = generate_for(job)
                resume_quality_report.refresh_after_finalization(
                    job, "synthetic-acceptance-job",
                    runtime_context_path=job / "_absent_runtime.json",
                    metrics_registry_path=job / "_absent_registry.json",
                )

        self.assertEqual(report["reused_results"]["action_verb_diversity"], SYNTHETIC_VERB_REPORT)
        # and the pipeline itself still has exactly one call
        pipeline_source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        self.assertEqual(pipeline_source.count("analyze_action_verbs_from_file("), 1)


# ===========================================================================
# D. Preserved-behaviour invariants
# ===========================================================================

class TestD1GateSemanticsUnchanged(unittest.TestCase):

    def test_success_statuses_and_correction_budget(self):
        self.assertEqual(
            deterministic_pipeline.SUCCESS_STATUSES,
            ("RESUME_READY", "RESUME_READY_WITH_WARNINGS"),
        )
        self.assertEqual(deterministic_pipeline.CORRECTION_ROUND_LIMIT, 2)

    def test_correction_reasons_are_exactly_the_pre_existing_four(self):
        import re

        source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        reasons = re.findall(r'correction_reasons\.append\("([A-Z_]+)"\)', source)
        self.assertEqual(sorted(reasons), ["ACTION_VERB_DIVERSITY", "ATS", "DENSITY", "MULTIPAGE"])
        self.assertEqual(len(reasons), len(set(reasons)))

    def test_attempt_local_reset_contract_is_untouched(self):
        self.assertEqual(
            set(deterministic_pipeline.ATTEMPT_LOCAL_CONTROL_ARTIFACTS),
            {
                "v8_correction_status.json", "correction_request.json",
                "density_guard.json", "project_selection.json",
                "v8_project_selection_validation.json",
            },
        )


class TestD2LayoutBudgetGateStillGates(unittest.TestCase):
    """Runs the AUTHORITATIVE budget check as a real subprocess against a
    synthetic layout, proving the advisory short-bullet diagnostic did not
    take over, weaken, or duplicate it."""

    LAYOUT = {
        "min_paragraph_count": 10,
        "experience_blocks": [{
            "owner_id": "experience_1", "tailoring_mode": "tailorable",
            "header_index": 1, "bullet_indices": [2],
            "max_chars_per_bullet": 60,
        }],
        "project_blocks": [{
            "owner_id": "selected_project_slot", "tailoring_mode": "selected",
            "header_index": 3, "bullet_indices": [4],
            "max_chars_title": 40, "max_chars_technology_line": 40,
            "max_chars_per_bullet": 60,
        }],
    }

    def _run(self, bullet):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "layout.json").write_text(json.dumps(self.LAYOUT))
            (tmp / "payload.json").write_text(json.dumps({
                "experience_alternate_bullets": {"experience_1": [bullet]},
                "selected_project": {
                    "owner_id": "selected_project_slot", "title": "Synthetic Atlas",
                    "technology_line": "C#, PostgreSQL", "bullets": ["A short synthetic bullet."],
                },
            }))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools/resume/payload_preflight.py"),
                 "--payload", str(tmp / "payload.json"),
                 "--layout", str(tmp / "layout.json"),
                 "--output", str(tmp / "out.json")],
                capture_output=True, text=True, cwd=ROOT,
            )
            return proc, json.loads((tmp / "out.json").read_text())

    def test_under_budget_passes_with_exit_zero(self):
        proc, report = self._run("A synthetic bullet comfortably inside the budget.")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["failures"], [])

    def test_over_budget_still_fails_with_exit_two(self):
        proc, report = self._run("A synthetic bullet that is deliberately far too long for " * 3)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["failures"])

    def test_markdown_bold_still_does_not_count_toward_the_budget(self):
        from payload_preflight import clean_length

        self.assertEqual(clean_length("**Built** it"), len("Built it"))
        # the advisory measures the same visible width, it does not redefine it
        self.assertEqual(bullet_length_audit.visible_length("- **Built** it"), len("Built it"))


class TestD3PdfQaContractUnchanged(unittest.TestCase):

    CONTEXT = {
        "candidate": {"full_name": "Synthetic Candidate"},
        "education": [{"institution": "Synthetic Institute"}],
        "experience": [{"tailoring_mode": "protected",
                        "employer": "Synthetic Works", "title": "Platform Engineer"}],
        "projects": [{"project_status": "required", "name": "Synthetic Atlas"}],
        "skills": {"static_resume_lines": {"Languages": "C++, C#, Python"}},
    }

    def _run_qa(self, lines):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            render_pdf(tmp / "resume.pdf", lines)
            (tmp / "context.json").write_text(json.dumps(self.CONTEXT))
            proc = subprocess.run(
                [sys.executable, str(ROOT / "tools/resume/qa_resume.py"),
                 "--pdf", str(tmp / "resume.pdf"),
                 "--candidate-context", str(tmp / "context.json"),
                 "--output", str(tmp / "qa.json")],
                capture_output=True, text=True, cwd=ROOT,
            )
            report = json.loads((tmp / "qa.json").read_text()) if (tmp / "qa.json").exists() else None
            return proc, report

    def test_missing_required_text_still_fails_with_exit_two(self):
        proc, report = self._run_qa(["EDUCATION", "PROFESSIONAL EXPERIENCE"])
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(report["status"], "FAIL")
        self.assertTrue(report["missing_required_text"])

    def test_the_replacement_character_rule_is_byte_for_byte_unchanged(self):
        # A U+FFFD text layer cannot be authored with PyMuPDF's built-in
        # fonts (see TestB1), so the rule itself is pinned at the source
        # level and its negative case is exercised against a real PDF.
        source = (ROOT / "tools/resume/qa_resume.py").read_text(encoding="utf-8")
        self.assertIn('"replacement_character_present": "�" in full_text', source)
        self.assertIn('if result["replacement_character_present"]:', source)

        _proc, report = self._run_qa([
            "Synthetic Candidate", "EDUCATION", "Synthetic Institute",
            "PROFESSIONAL EXPERIENCE", "Synthetic Works", "Platform Engineer",
            "PROJECTS", "Synthetic Atlas", "SKILLS", "Languages: C++, C#, Python",
        ])
        self.assertFalse(report["replacement_character_present"])

    def test_the_refactored_extractor_is_byte_identical_to_the_inline_one(self):
        sys.path.insert(0, str(ROOT / "tools/resume"))
        import qa_resume

        with tempfile.TemporaryDirectory() as tmp:
            pdf = render_pdf(Path(tmp) / "r.pdf", ACCEPTANCE_DRAFT.splitlines())
            doc = fitz.open(str(pdf))
            try:
                inline = "\n".join(page.get_text("text") for page in doc)
                shared = qa_resume.text_from_document(doc)
            finally:
                doc.close()
            extracted, pages = qa_resume.extract_pdf_text(str(pdf))

        self.assertEqual(shared, inline)
        self.assertEqual(extracted, inline)
        self.assertEqual(pages, 1)

    def test_qa_report_keys_the_pipeline_depends_on_are_all_present(self):
        _proc, report = self._run_qa(["EDUCATION"])
        for key in ("status", "page_count", "one_page", "missing_required_text",
                    "replacement_character_present", "usable_bottom_whitespace_in",
                    "density_target_pass", "font_sizes"):
            self.assertIn(key, report)


class TestDPrivacyOfThisSuite(unittest.TestCase):

    def test_this_suite_reads_no_private_candidate_data(self):
        source = Path(__file__).read_text(encoding="utf-8")
        # Built at runtime so these detector literals are not themselves
        # matches against this file's own source.
        private_markers = [
            'ROOT / "' + "master" + '"',
            "master" + "/runtime",
            "metrics_registry" + ".json",
            "runtime_resume_context" + ".json",
        ]
        for marker in private_markers:
            self.assertNotIn(marker, source, f"this suite must never name {marker}")

        # every report call in this suite points the private-data
        # defaults at paths that deliberately do not exist
        self.assertIn('"_absent_runtime.json"', source)
        self.assertIn('"_absent_registry.json"', source)

    def test_this_suite_makes_no_network_or_model_calls(self):
        import ast

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("urllib", "http", "socket", "requests", "anthropic", "llm_invoke"):
            self.assertNotIn(forbidden, imported)

    def test_synthetic_contact_details_only(self):
        import re

        source = Path(__file__).read_text(encoding="utf-8")
        for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", source):
            self.assertTrue(email.endswith(("example.com", ".invalid")), email)
        for phone in re.findall(r"\(\d{3}\)[\s.\-]?\d{3}[\s.\-]\d{4}", source):
            self.assertIn("555", phone)


if __name__ == "__main__":
    unittest.main(verbosity=2)
