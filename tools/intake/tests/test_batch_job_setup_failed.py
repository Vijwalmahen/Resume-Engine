#!/usr/bin/env python3
"""
Acceptance 13 + 14: a malformed input file is classified `JOB_SETUP_FAILED`
and isolated — it does not take the batch down with it — and the V8.6.9
advisory diagnostics behave exactly as they did before.

This exercises the REAL `run_batch.Batch.process_job` / `Batch.run`, with only
two substitutions, both of which the production code already provides as test
seams:

  * `pipeline_runner` — the fast fake the V8.6 offline suite also injects, so
    no Word/PDF conversion is needed;
  * `setup()` — replaced by a synthetic batch directory (public config plus
    invented candidate data), so no private runtime context is read.

Nothing else is mocked: the packet builder, the semantic-call plumbing (with
the pipeline's own `MockCaller`), the finalize/tracker subprocess steps, the
done-directory move, the quality report and the metrics are all the real ones.
"""

from __future__ import annotations

import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import (ROOT, TempDirTestCase, block_network,  # noqa: E402
                     make_synthetic_batch_dir, synthetic_description)

block_network()

import extractors  # noqa: E402
import write_job_file  # noqa: E402
import run_batch  # noqa: E402
from llm_invoke import MockCaller  # noqa: E402

BATCH_ID = "batch-synthetic-intake-job-setup"

VALID_REQUIREMENTS = {
    "required": [{"concept": "Python", "evidence_required": True}],
    "preferred": [], "unsupported": [],
}
VALID_CONTENT = {
    "strategy_status": {"status": "STRATEGY_READY"},
    "resume_payload": {"selected_project": {"owner_id": "project_selectable_1",
                                            "title": "Synthetic Retrieval Service"}},
    "cover_letter_payload": {"company": "Example AI Labs", "role": "ML Intern"},
}


class _Args:
    def __init__(self, **kw):
        self.input_dir = None
        self.done_dir = None
        self.max_workers = 4
        self.test_mode = True
        self.base_url = "http://127.0.0.1:1"  # never contacted: health is 'unavailable'
        self.batch_id = BATCH_ID
        self.mock_semantic = True
        self.mock_health_status = "unavailable"
        self.mock_responses = {}
        self.__dict__.update(kw)


def fake_pipeline_runner(job_dir, evidence_root, job_key):
    return {"job": job_key, "status": "RESUME_READY", "timings": {}}


def make_record(company, job_id):
    return extractors.JobRecord(
        source="linkedin", source_job_id=job_id,
        canonical_url=f"https://www.linkedin.com/jobs/view/{job_id}",
        description=synthetic_description(company), company=company,
        title="Machine Learning Engineer Intern",
    )


class _BatchHarness(TempDirTestCase):
    """Shared fixture: a real `run_batch.Batch` over a synthetic inbox."""

    GOOD_KEYS = ["linkedin-4012345678", "linkedin-4012345679"]
    BAD_KEY = "linkedin-4012345680"

    def setUp(self):
        super().setUp()
        self.batch_dir = ROOT / ".work/v86" / BATCH_ID
        self._cleanup_paths = [self.batch_dir] + [ROOT / "output" / k
                                                  for k in self.GOOD_KEYS + [self.BAD_KEY]]
        # The real tracker step writes the repo's test-mode workbook; restore
        # whatever was there (if anything) so this suite leaves no trace.
        self.test_tracker = ROOT / "tracking" / "job_application_tracker_TEST.xlsx"
        self._tracker_before = (
            self.test_tracker.read_bytes() if self.test_tracker.exists() else None
        )

    def tearDown(self):
        for path in self._cleanup_paths:
            shutil.rmtree(path, ignore_errors=True)
        if self._tracker_before is None:
            self.test_tracker.unlink(missing_ok=True)
        else:
            self.test_tracker.write_bytes(self._tracker_before)
        super().tearDown()

    def make_batch(self):
        # Two valid postings, written by intake itself, plus one file whose
        # bytes are not valid UTF-8 at all.
        for job_key in self.GOOD_KEYS:
            job_id = job_key.split("-")[-1]
            write_job_file.write(make_record("Example AI Labs", job_id), job_key, self.inbox)
        (self.inbox / f"{self.BAD_KEY}.md").write_bytes(
            b"# Job Posting\n\nCompany: \xff\xfe\x00 not valid utf-8 \x80\x81\n"
        )

        args = _Args(input_dir=str(self.inbox), done_dir=str(self.done),
                     mock_responses={"requirements": VALID_REQUIREMENTS, "content": VALID_CONTENT})
        batch = run_batch.Batch(args)
        batch.pipeline_runner = fake_pipeline_runner
        batch.semantic_caller = MockCaller(args.mock_responses)
        # Stand in for Batch.setup(): a synthetic shared context, no private
        # runtime read, and a health status that keeps the finalizer offline.
        make_synthetic_batch_dir(batch.batch_dir)
        batch.setup = lambda: None
        batch.health_status = "unavailable"
        batch.evidence_root = batch.batch_dir / "shared_ats_evidence"
        batch.evidence_root.mkdir(parents=True, exist_ok=True)
        return batch


class TestJobSetupFailedIsolation(_BatchHarness):
    def test_one_unreadable_file_does_not_terminate_the_batch(self):
        batch = self.make_batch()
        metrics = batch.run()   # must return normally, not raise

        by_job = {r["job"]: r for r in metrics["jobs"]}
        self.assertEqual(set(by_job), set(self.GOOD_KEYS + [self.BAD_KEY]))
        self.assertEqual(by_job[self.BAD_KEY]["status"], "JOB_SETUP_FAILED")
        self.assertIn("UnicodeDecodeError", by_job[self.BAD_KEY]["error"])
        for key in self.GOOD_KEYS:
            self.assertEqual(by_job[key]["status"], "RESUME_READY")

        self.assertEqual(metrics["jobs_succeeded"], 2)
        self.assertEqual(metrics["jobs_failed"], 1)

    def test_the_bad_input_stays_in_the_inbox(self):
        batch = self.make_batch()
        batch.run()
        self.assertTrue((self.inbox / f"{self.BAD_KEY}.md").exists())
        self.assertFalse((self.done / f"{self.BAD_KEY}.md").exists())
        self.assertEqual(sorted(p.stem for p in self.done.glob("*.md")), sorted(self.GOOD_KEYS))

    def test_a_missing_file_is_also_a_job_setup_failure(self):
        batch = self.make_batch()
        (self.inbox / f"{self.BAD_KEY}.md").unlink()
        vanishing = self.inbox / "linkedin-4012345681.md"
        write_job_file.write(make_record("Example AI Labs", "4012345681"),
                             "linkedin-4012345681", self.inbox)
        self._cleanup_paths.append(ROOT / "output" / "linkedin-4012345681")

        original = run_batch.build_job_packet.build_packet

        def unlink_then_build(batch_dir, job_file, job_key):
            if job_key == "linkedin-4012345681":
                Path(job_file).unlink()
            return original(batch_dir, job_file, job_key)

        run_batch.build_job_packet.build_packet = unlink_then_build
        try:
            metrics = batch.run()
        finally:
            run_batch.build_job_packet.build_packet = original

        by_job = {r["job"]: r for r in metrics["jobs"]}
        self.assertEqual(by_job["linkedin-4012345681"]["status"], "JOB_SETUP_FAILED")
        self.assertIn("FileNotFoundError", by_job["linkedin-4012345681"]["error"])
        self.assertEqual(sorted(p.stem for p in self.done.glob("*.md")), sorted(self.GOOD_KEYS))
        self.assertIsNotNone(vanishing)

    def test_job_setup_failed_is_not_a_success_status(self):
        self.assertNotIn("JOB_SETUP_FAILED", run_batch.SUCCESS_STATUSES)


class TestDiagnosticsAreUnchanged(_BatchHarness):
    """Acceptance 14: the V8.6.9 advisory diagnostics still run exactly where
    they ran before, still cannot change a job's status, and never run for a
    job that failed setup."""

    def test_the_failed_job_produces_no_diagnostics(self):
        batch = self.make_batch()
        metrics = batch.run()
        by_job = {r["job"]: r for r in metrics["jobs"]}
        self.assertNotIn("quality_report", by_job[self.BAD_KEY])
        self.assertFalse((ROOT / "output" / self.BAD_KEY / "resume_quality_report.json").exists())

    def test_successful_jobs_still_get_an_advisory_report(self):
        batch = self.make_batch()
        metrics = batch.run()
        by_job = {r["job"]: r for r in metrics["jobs"]}
        for key in self.GOOD_KEYS:
            quality = by_job[key]["quality_report"]
            self.assertTrue(quality.get("advisory_only"))
            self.assertIn(quality["diagnostic_state"], ("PASS", "REVIEW", "INCOMPLETE"))
            self.assertEqual(by_job[key]["status"], "RESUME_READY",
                             "an advisory diagnostic must never change a job's status")

    def test_the_quality_review_metric_still_counts_only_reports(self):
        batch = self.make_batch()
        metrics = batch.run()
        counted = sum(
            1 for r in metrics["jobs"]
            if (r.get("quality_report") or {}).get("diagnostic_state") in ("REVIEW", "INCOMPLETE")
        )
        self.assertEqual(metrics["jobs_quality_review"], counted)
        self.assertLessEqual(metrics["jobs_quality_review"], len(self.GOOD_KEYS))

    def test_metrics_files_are_written_for_the_whole_batch(self):
        batch = self.make_batch()
        batch.run()
        metrics_json = batch.batch_dir / "batch_metrics.json"
        self.assertTrue(metrics_json.exists())
        payload = json.loads(metrics_json.read_text(encoding="utf-8"))
        self.assertEqual(payload["jobs_discovered"], 3)
        text = (batch.batch_dir / "batch_metrics.txt").read_text(encoding="utf-8")
        self.assertIn("JOB_SETUP_FAILED", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
