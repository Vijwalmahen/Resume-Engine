#!/usr/bin/env python3
"""
Acceptance 7 + 8, plus the Phase-2 schema guards: the Intake worksheet never
disturbs `Applications`, the legacy 35-column writer refuses an incompatible
workbook, and both writers share one lock.

Every workbook here is synthetic and lives in a temp directory. The real
`tracking/` workbooks are never opened by this suite.
"""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import ROOT, TempDirTestCase, block_network  # noqa: E402

block_network()

import contextlib  # noqa: E402
import intake_tracker  # noqa: E402
import tracker_writer  # noqa: E402


@contextlib.contextmanager
def production_writer_pointed_at(path):
    """Run the REAL tools/resume/v86/tracker_writer.py against a synthetic
    workbook. Only its destination constants are redirected -- the writing
    behavior under test is the production code itself."""
    original_tracker, original_root = tracker_writer.TEST_TRACKER, tracker_writer.ROOT
    tracker_writer.TEST_TRACKER = Path(path)
    tracker_writer.ROOT = Path(path).parent.parent
    try:
        yield
    finally:
        tracker_writer.TEST_TRACKER = original_tracker
        tracker_writer.ROOT = original_root

sys.path.insert(0, str(ROOT / "orchestrator"))
import tracker_xlsx  # noqa: E402

APPLICATIONS = intake_tracker.APPLICATIONS_SHEET
INTAKE = intake_tracker.INTAKE_SHEET


def intake_row(job_key, **kw):
    row = {
        "Job Key": job_key, "Source": "linkedin", "Source Job ID": job_key.split("-")[-1],
        "Canonical URL": f"https://www.linkedin.com/jobs/view/{job_key.split('-')[-1]}",
        "URL Status": "SUPPORTED", "Scrape Status": "SCRAPED",
        "Completeness Status": "COMPLETE", "Description Length": 1200,
        "Duplicate Decision": "NEW", "Normalized File": f"jobs/batch_inbox/{job_key}.md",
        "Intake Result": "WRITTEN", "First Seen At": "2026-10-18T00:00:00+00:00",
        "Last Intake At": "2026-10-18T00:00:00+00:00",
    }
    row.update(kw)
    return row


class TrackerTestCase(TempDirTestCase):
    def sheet_values(self, sheet_name):
        from openpyxl import load_workbook

        wb = load_workbook(self.tracker)
        try:
            return [list(r) for r in wb[sheet_name].iter_rows(values_only=True)]
        finally:
            wb.close()

    def seed_applications(self):
        """A workbook that already holds production rows, including values the
        user owns."""
        from openpyxl import load_workbook

        intake_tracker.create_workbook_with_applications(self.tracker)
        wb = load_workbook(self.tracker)
        ws = wb[APPLICATIONS]
        row = [""] * len(intake_tracker.APPLICATIONS_HEADERS)
        index = {h: i for i, h in enumerate(intake_tracker.APPLICATIONS_HEADERS)}
        row[index["Job Key"]] = "linkedin-4012345678"
        row[index["Company"]] = "Example AI Labs"
        row[index["Role"]] = "ML Intern"
        row[index["Application Status"]] = "APPLIED"
        row[index["Applied Date"]] = "2026-10-10"
        row[index["Notes"]] = "Referred by a friend - do not overwrite"
        ws.append(row)
        wb.save(self.tracker)
        wb.close()


class TestApplicationsIsUntouched(TrackerTestCase):
    def test_headers_rows_and_notes_survive_intake_writes(self):
        self.seed_applications()
        before = self.sheet_values(APPLICATIONS)

        intake_tracker.upsert_rows(self.tracker, [
            intake_row("linkedin-4012345678"),
            intake_row("greenhouse-examplecorp-7654321"),
        ])
        intake_tracker.upsert_rows(self.tracker, [
            intake_row("linkedin-4012345678", **{"Pipeline Status": "RESUME_READY"}),
        ])

        after = self.sheet_values(APPLICATIONS)
        self.assertEqual(after, before)
        self.assertEqual(after[0], intake_tracker.APPLICATIONS_HEADERS)
        applied = after[1][intake_tracker.APPLICATIONS_HEADERS.index("Application Status")]
        notes = after[1][intake_tracker.APPLICATIONS_HEADERS.index("Notes")]
        self.assertEqual(applied, "APPLIED")
        self.assertEqual(notes, "Referred by a friend - do not overwrite")

    def test_applications_column_order_is_the_production_order(self):
        self.assertEqual(intake_tracker.APPLICATIONS_HEADERS, list(tracker_writer.HEADERS))
        self.assertEqual(len(intake_tracker.APPLICATIONS_HEADERS), 23)

    def test_intake_sheet_has_the_32_column_schema(self):
        self.seed_applications()
        intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-4012345678")])
        headers = self.sheet_values(INTAKE)[0]
        self.assertEqual(headers, intake_tracker.INTAKE_HEADERS)
        self.assertEqual(len(headers), 32)

    def test_the_production_writer_still_works_on_the_same_workbook(self):
        self.seed_applications()
        intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-4012345678")])

        with production_writer_pointed_at(self.tracker):
            tracker_writer.update_tracker(
                test_mode=True, job_key="linkedin-4012345678", company="Example AI Labs",
                role="ML Intern", resume_status="COMPLETE",
            )

        values = self.sheet_values(APPLICATIONS)
        index = intake_tracker.APPLICATIONS_HEADERS.index
        self.assertEqual(values[0], intake_tracker.APPLICATIONS_HEADERS)
        self.assertEqual(values[1][index("Resume Status")], "COMPLETE")
        self.assertEqual(values[1][index("Application Status")], "APPLIED")
        self.assertEqual(values[1][index("Notes")], "Referred by a friend - do not overwrite")
        # ... and the Intake sheet it does not know about is still intact.
        self.assertEqual(self.sheet_values(INTAKE)[0], intake_tracker.INTAKE_HEADERS)


class TestIdempotentUpsertAndUserFields(TrackerTestCase):
    def test_upsert_is_idempotent_by_job_key(self):
        self.seed_applications()
        for _ in range(3):
            intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-4012345678")])
        rows = intake_tracker.read_intake_rows(self.tracker)
        self.assertEqual([r["Job Key"] for r in rows], ["linkedin-4012345678"])

    def test_notes_are_never_written_by_intake(self):
        from openpyxl import load_workbook

        self.seed_applications()
        intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-4012345678")])

        wb = load_workbook(self.tracker)
        ws = wb[INTAKE]
        notes_col = intake_tracker.INTAKE_HEADERS.index("Notes") + 1
        ws.cell(row=2, column=notes_col, value="my own note")
        wb.save(self.tracker)
        wb.close()

        intake_tracker.upsert_rows(self.tracker, [
            intake_row("linkedin-4012345678", Notes="pipeline tried to write this"),
        ])
        self.assertEqual(intake_tracker.read_intake_rows(self.tracker)[0]["Notes"], "my own note")

    def test_first_seen_at_is_set_once(self):
        self.seed_applications()
        intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-4012345678")])
        intake_tracker.upsert_rows(self.tracker, [
            intake_row("linkedin-4012345678", **{"First Seen At": "2030-01-01T00:00:00+00:00",
                                                 "Last Intake At": "2030-01-01T00:00:00+00:00"}),
        ])
        row = intake_tracker.read_intake_rows(self.tracker)[0]
        self.assertEqual(row["First Seen At"], "2026-10-18T00:00:00+00:00")
        self.assertEqual(row["Last Intake At"], "2030-01-01T00:00:00+00:00")

    def test_a_caller_may_be_restricted_to_its_own_columns(self):
        self.seed_applications()
        intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-4012345678")])
        with self.assertRaises(intake_tracker.IntakeSchemaError):
            intake_tracker.upsert_rows(
                self.tracker, [{"Job Key": "linkedin-4012345678", "Source": "tampered"}],
                allowed_headers=intake_tracker.SYNC_OWNED_HEADERS,
            )
        self.assertEqual(intake_tracker.read_intake_rows(self.tracker)[0]["Source"], "linkedin")


class TestSchemaRefusals(TrackerTestCase):
    def _workbook_with_headers(self, sheet_name, headers):
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name
        ws.append(headers)
        wb.save(self.tracker)
        wb.close()

    def test_intake_refuses_a_workbook_without_applications(self):
        self._workbook_with_headers("Sheet1", ["something", "else"])
        with self.assertRaises(intake_tracker.IntakeSchemaError) as caught:
            intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-1111111111")])
        self.assertIn("Applications", str(caught.exception))

    def test_intake_refuses_a_wrong_applications_schema(self):
        self._workbook_with_headers(APPLICATIONS, ["Job Key", "Company", "Surprise"])
        before = self.tracker.read_bytes()
        with self.assertRaises(intake_tracker.IntakeSchemaError) as caught:
            intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-1111111111")])
        self.assertIn("does not match", str(caught.exception))
        self.assertEqual(self.tracker.read_bytes(), before, "a refusal must change nothing")

    def test_intake_refuses_a_wrong_intake_schema(self):
        from openpyxl import load_workbook

        self.seed_applications()
        wb = load_workbook(self.tracker)
        ws = wb.create_sheet(INTAKE)
        ws.append(["Job Key", "Who knows"])
        wb.save(self.tracker)
        wb.close()
        with self.assertRaises(intake_tracker.IntakeSchemaError):
            intake_tracker.upsert_rows(self.tracker, [intake_row("linkedin-1111111111")])


class TestLegacyWriterFailsSafely(TrackerTestCase):
    """Phase 2: the dormant 35-column `orchestrator/tracker_xlsx.py` writer."""

    def test_it_refuses_the_23_column_applications_workbook(self):
        self.seed_applications()
        before = self.tracker.read_bytes()
        with self.assertRaises(tracker_xlsx.TrackerSchemaError) as caught:
            tracker_xlsx.upsert_tracker_row(self.tracker, "linkedin-4012345678",
                                            {"Company": "Example AI Labs"})
        message = str(caught.exception)
        self.assertIn("Refusing to modify", message)
        self.assertEqual(self.tracker.read_bytes(), before,
                         "the production workbook must be byte-identical after a refusal")

    def test_it_refuses_a_workbook_with_extra_worksheets(self):
        path = self.tracking / "legacy.xlsx"
        tracker_xlsx.upsert_tracker_row(path, "linkedin-4012345678", {
            "Job Key": "linkedin-4012345678", "Company": "Example AI Labs",
        })
        from openpyxl import load_workbook

        wb = load_workbook(path)
        wb.create_sheet("Intake")
        wb.save(path)
        wb.close()
        with self.assertRaises(tracker_xlsx.TrackerSchemaError):
            tracker_xlsx.upsert_tracker_row(path, "linkedin-4012345678", {"Company": "Changed"})

    def test_it_still_owns_its_own_workbook(self):
        path = self.tracking / "legacy.xlsx"
        tracker_xlsx.upsert_tracker_row(path, "linkedin-4012345678", {
            "Job Key": "linkedin-4012345678", "Company": "Example AI Labs",
            "Pipeline Status": "NEW",
        })
        tracker_xlsx.upsert_tracker_row(path, "linkedin-4012345678", {"Pipeline Status": "GENERATING"})
        rows = tracker_xlsx.read_tracker_rows(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Pipeline Status"], "GENERATING")
        self.assertEqual(rows[0]["Application Status"], "NOT_APPLIED")
        self.assertEqual(len(tracker_xlsx.HEADERS), 35)


class TestLockInteroperation(TrackerTestCase):
    def test_both_writers_use_the_same_lock_path(self):
        workbook = ROOT / "tracking" / "job_application_tracker.xlsx"
        self.assertEqual(
            intake_tracker.lock_path_for(workbook),
            workbook.parent / ".job_application_tracker.xlsx.lock",
        )
        self.assertEqual(
            intake_tracker.lock_path_for(workbook),
            workbook.parent / f".{workbook.name}.lock",
        )

    def test_concurrent_intake_and_pipeline_writes_do_not_corrupt_the_workbook(self):
        self.seed_applications()
        errors = []

        # Safety net: prove this test wrote to its own temp workbook and never
        # to the repository's real test-mode tracker.
        repo_tracker = ROOT / "tracking" / "job_application_tracker_TEST.xlsx"
        repo_before = repo_tracker.read_bytes() if repo_tracker.exists() else None

        def intake_worker(n):
            try:
                intake_tracker.upsert_rows(self.tracker, [intake_row(f"linkedin-40100000{n:02d}")])
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                errors.append(f"intake {n}: {type(exc).__name__}: {exc}")

        def pipeline_worker(n):
            try:
                tracker_writer.update_tracker(
                    test_mode=True, job_key=f"greenhouse-examplecorp-90{n:02d}",
                    company="Example AI Labs", role="ML Intern", resume_status="COMPLETE",
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"pipeline {n}: {type(exc).__name__}: {exc}")

        redirect = production_writer_pointed_at(self.tracker)
        redirect.__enter__()
        self.addCleanup(redirect.__exit__, None, None, None)

        threads = []
        for n in range(6):
            threads.append(threading.Thread(target=intake_worker, args=(n,)))
            threads.append(threading.Thread(target=pipeline_worker, args=(n,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        intake_keys = {r["Job Key"] for r in intake_tracker.read_intake_rows(self.tracker)}
        self.assertEqual(intake_keys, {f"linkedin-40100000{n:02d}" for n in range(6)})

        applications = self.sheet_values(APPLICATIONS)
        self.assertEqual(applications[0], intake_tracker.APPLICATIONS_HEADERS)
        app_keys = {row[0] for row in applications[1:]}
        self.assertEqual(
            app_keys, {"linkedin-4012345678"} | {f"greenhouse-examplecorp-90{n:02d}" for n in range(6)},
        )
        notes = applications[1][intake_tracker.APPLICATIONS_HEADERS.index("Notes")]
        self.assertEqual(notes, "Referred by a friend - do not overwrite")

        repo_after = repo_tracker.read_bytes() if repo_tracker.exists() else None
        self.assertEqual(repo_after, repo_before,
                         "the repository's own tracker must never be written by this suite")


if __name__ == "__main__":
    unittest.main(verbosity=2)
