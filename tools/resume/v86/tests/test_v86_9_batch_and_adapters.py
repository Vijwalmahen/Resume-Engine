#!/usr/bin/env python3
"""
V8.6.9 -- final offline hardening: the REAL threaded batch path, the
configurable resume-artifact name, and the metrics registry-shape
adaptation boundary.

ZERO model calls, ZERO network, ZERO paid APIs, ZERO reads of private
candidate data, ZERO writes outside temporary directories and the
batch's own scratch areas (which are cleaned up). Every candidate,
employer, institution, project, date, bullet, metric and filename below
is wholly synthetic and written inline in this file.

Sections:

  A. The real `Batch.run()` scan/dispatch/future-result path
     A1  three synthetic jobs keep their genuine statuses and each
         carries the expected quality-report metadata
     A2  `jobs_quality_review` aggregates REVIEW/INCOMPLETE correctly
     A3  `batch_metrics.txt` emits the per-job quality suffix
     A4  a raising `refresh_after_finalization` cannot kill the batch or
         change any job's real status
     A5  the action-verb checker is invoked exactly once per job across
         the whole batch (generate + refresh must not re-run it)
     A6  success statuses, correction reasons, the correction-round
         limit and tracker invocation are unchanged by any of this

     `Batch.run()` itself is NEVER mocked: the real scan loop, the real
     ThreadPoolExecutor, the real `process_job` body, the real
     `future.result()` propagation point and the real metrics assembly
     all execute. Only the three genuinely external/expensive seams are
     substituted -- the semantic caller (`MockCaller`, already the
     suite-wide convention), the Word-dependent deterministic pipeline
     (`Batch.pipeline_runner`, an existing injectable seam), and the
     per-batch shared-context build, stubbed so the test reads wholly
     synthetic candidate data instead of the machine's private runtime
     context.

  B. Configurable resume-artifact name
     B1  an exact non-default filename is discovered and inspected
     B2  round-trip, sha256 and finalization stage all follow it
     B3  a glob that matches exactly one file resolves
     B4  a glob matching several files is `ambiguous`, not a guess
     B5  a missing artifact is honest, never a false success

  C. Metrics registry-shape adaptation
     C1  shape detection
     C2  unambiguous explicit mapping links an entity to its owner
     C3  an unmapped entity stays `not_evaluated` even when its name
         exactly equals a runtime entry's name
     C4  duplicate entity labels across items
     C5  ambiguous owner (duplicate ids) stays `not_evaluated`
     C6  unverified items never produce a finding
     C7  a value absent from the supporting bullet is `omitted`; a value
         that differs from it is NOT silently accepted
     C8  value/unit boundaries survive adaptation, including `4 hours`
     C9  an explicit `final_bullets` path reaches a non-canonical
         payload shape, and a bad path is reported rather than guessed
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

V86_DIR = Path(__file__).resolve().parents[1]
ROOT = V86_DIR.parents[2]
sys.path.insert(0, str(V86_DIR))

import fitz  # noqa: E402

import action_verb_diversity  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import metrics_audit  # noqa: E402
import metrics_registry_adapter as adapter  # noqa: E402
import resume_quality_report  # noqa: E402
import run_batch  # noqa: E402
from common import write_json  # noqa: E402
from llm_invoke import MockCaller  # noqa: E402


# ===========================================================================
# Wholly synthetic fixtures
# ===========================================================================

SYNTHETIC_DRAFT = """Example Candidate
example.candidate@example.com | (555) 010-4242 | Sample Town, ST

EDUCATION

Example Institute -- M.S. Example Systems
GPA: 3.90/4.00 | Sep 2023 -- Jun 2025

PROFESSIONAL EXPERIENCE

Platform Engineer | Example Works | Remote\tJan 2021 -- Dec 2021
- Rebuilt the batch ingestion path and cut nightly runtime from 4 hours to 35 minutes.
- Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.

PROJECTS

Example Atlas -- Catalog Indexer  -  Python, PostgreSQL
- Indexed a 90k-record example catalog and held p99 lookup latency under 45ms.

SKILLS

Languages: Python, SQL
"""

SYNTHETIC_QA = {
    "status": "PASS", "page_count": 1, "replacement_character_present": False,
    "density_target_pass": True, "usable_bottom_whitespace_in": 0.33,
}

SYNTHETIC_CANDIDATE_CONTEXT = {
    "schema_version": "1.0",
    "candidate": {"full_name": "Example Candidate"},
    "education": [{"id": "education_1", "institution": "Example Institute",
                   "dates": "Sep 2023 -- Jun 2025"}],
    "experience": [{
        "id": "experience_1", "employer": "Example Works", "title": "Platform Engineer",
        "dates": "Jan 2021 -- Dec 2021", "tailoring_mode": "tailorable",
        "alternate_bullets_for_dynamic_tailoring": [
            "Rebuilt the batch ingestion path and cut nightly runtime from 4 hours to 35 minutes.",
            "Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.",
        ],
    }],
    "projects": [{
        "id": "project_selectable_a", "name": "Example Atlas",
        "project_status": "selectable", "tailoring_mode": "tailorable",
        "alternate_bullets_for_dynamic_tailoring": [
            "Indexed a 90k-record example catalog and held p99 lookup latency under 45ms.",
        ],
    }],
    "skills": {"static_resume_lines": {"Languages": "Python, SQL"}},
    "truth_constraints": [],
}

SYNTHETIC_PAYLOAD = {
    "experience_bullet_orders": {},
    "experience_alternate_bullets": {
        "experience_1": [
            "Rebuilt the batch ingestion path and cut nightly runtime from 4 hours to 35 minutes.",
            "Introduced a CI/CD pipeline that ran 120 regression checks on every merge request.",
        ],
    },
    "project_bullet_orders": {},
    "selected_project": {
        "owner_id": "project_selectable_a", "title": "Example Atlas",
        "technology_line": "Python, PostgreSQL",
        "bullets": ["Indexed a 90k-record example catalog and held p99 lookup latency under 45ms."],
    },
}

# A production-SHAPED registry, expressed entirely with synthetic values.
# The field names here (`items`, entity/id/name/source/unit/value/verified)
# are the generic alternative shape this adapter supports; none of the
# values is real.
SYNTHETIC_ENTITY_REGISTRY = {
    "generated_at": "1970-01-01T00:00:00Z",
    "source_fingerprint": None,
    "items": [
        {"id": "atlas_latency", "name": "Atlas lookup latency", "entity": "Example Atlas",
         "description": "p99 lookup latency held under the stated bound",
         "value": 45, "unit": "ms", "source": "example_facts.md#atlas", "verified": True},
        {"id": "ingestion_runtime", "name": "Nightly ingestion runtime",
         "entity": "Example Works Platform", "description": "nightly runtime after the rebuild",
         "value": 4, "unit": "hours", "source": "example_facts.md#ingestion", "verified": True},
    ],
}


def render_pdf(path: Path, draft: str = SYNTHETIC_DRAFT) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    y = 40
    for line in draft.splitlines():
        if line.strip():
            page.insert_text((40, y), line.replace("\t", "   "), fontsize=7)
        y += 10
    doc.save(str(path))
    doc.close()
    return path


def write_fixture_job(path: Path, company: str) -> None:
    path.write_text(
        f"# Machine Learning Intern\n\n**Company:** {company}\n\n"
        "## Requirements\n- Python\n- SQL\n"
    )


# ===========================================================================
# A. The real Batch.run() path
# ===========================================================================

def synthetic_shared_context(batch_dir: Path) -> dict:
    """Stand-in for `build_shared_context.build`.

    Writes the same four files `build_job_packet.build_packet` reads,
    with wholly synthetic content, so `Batch.run()` never touches the
    machine's private runtime context, private template layout, or the
    real ATS-evidence builder. Everything downstream of it is the real
    code path.
    """
    batch_dir = Path(batch_dir)
    evidence = batch_dir / "shared_ats_evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    write_json(evidence / "verified_resume_evidence.json", {
        "candidate_profile": {}, "education": SYNTHETIC_CANDIDATE_CONTEXT["education"],
        "experience": SYNTHETIC_CANDIDATE_CONTEXT["experience"],
        "projects": SYNTHETIC_CANDIDATE_CONTEXT["projects"],
        "skills": SYNTHETIC_CANDIDATE_CONTEXT["skills"],
    })
    write_json(batch_dir / "shared_candidate_context.json", SYNTHETIC_CANDIDATE_CONTEXT)
    write_json(batch_dir / "shapes.json", {
        "resume_payload_shape": {}, "jd_requirements_shape": {},
        "cover_letter_payload_shape": {},
    })
    write_json(batch_dir / "project_selection_policy.json", {"policy": "synthetic"})
    write_json(batch_dir / "resume_template_layout.json", {
        "experience_blocks": [{"owner_id": "experience_1", "tailoring_mode": "tailorable",
                               "bullet_indices": [0, 1], "max_chars_per_bullet": 200}],
        "project_blocks": [{"owner_id": "selected_project_slot", "tailoring_mode": "selected",
                            "bullet_indices": [0], "max_chars_title": 70,
                            "max_chars_technology_line": 90, "max_chars_per_bullet": 200}],
    })
    return {
        "batch_dir": str(batch_dir.relative_to(ROOT)),
        "runtime_context_sha256_check": "PASS",
        "shared_ats_evidence_dir": str(evidence.relative_to(ROOT)),
        "shared_candidate_context_file": str(
            (batch_dir / "shared_candidate_context.json").relative_to(ROOT)),
        "timings": {},
    }


def mock_requirements(_prompt=None):
    return {"job_key": "any", "concepts": [
        {"term": "Python", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
    ]}


def mock_content(_prompt=None):
    return {
        "resume_payload": json.loads(json.dumps(SYNTHETIC_PAYLOAD)),
        "cover_letter_payload": {
            "company": "Example Co", "role": "ML Intern", "salutation": "Dear Hiring Team,",
            "opening": "o", "experience_paragraphs": {"experience_1": "e"},
            "project_paragraph": "p", "closing": "c",
        },
        "strategy_status": {"status": "STRATEGY_READY"},
    }


class _Args:
    def __init__(self, **kw):
        self.input_dir = None
        self.done_dir = None
        self.max_workers = 3
        self.test_mode = True
        # Unreachable on purpose: the finalizer stays SERVICE_UNAVAILABLE,
        # so no network call is ever attempted.
        self.base_url = "http://127.0.0.1:1"
        self.batch_id = None
        self.mock_semantic = True
        self.mock_health_status = "unavailable"
        self.mock_responses = {}
        self.__dict__.update(kw)


class RealBatchRunTestCase(unittest.TestCase):
    """Drives the genuine `Batch.run()` orchestration."""

    JOB_COUNT = 3

    def setUp(self):
        self._tmp = []
        self._verb_calls = []
        self._verb_lock = threading.Lock()

    def tearDown(self):
        for path in self._tmp:
            shutil.rmtree(path, ignore_errors=True)

    def track(self, path):
        self._tmp.append(Path(path))
        return Path(path)

    # -- the fake deterministic pipeline ---------------------------------
    #
    # Mirrors ONLY the artifact-and-report contract of the real
    # `run_job_pipeline` success path (write the draft, render the PDF,
    # write resume_qa.json, run the action-verb checker exactly once,
    # then generate the advisory report). The Word-dependent render and
    # the ATS/QA subprocesses are what it stands in for.
    def _pipeline_runner(self, resume_pdf_name="resume.pdf", status="RESUME_READY"):
        def runner(job_dir, evidence_root, job_key):
            job_dir = Path(job_dir)
            job_dir.mkdir(parents=True, exist_ok=True)
            (job_dir / "resume_draft.md").write_text(SYNTHETIC_DRAFT)
            write_json(job_dir / "resume_qa.json", SYNTHETIC_QA)
            render_pdf(job_dir / resume_pdf_name)

            verb_report = action_verb_diversity.analyze_action_verbs_from_file(
                job_dir / "resume_draft.md"
            )
            result = {"job": job_key, "status": status, "timings": {},
                      "action_verb_diversity": {
                          "counts": verb_report["counts"],
                          "repeated_over_limit": verb_report["repeated_over_limit"],
                          "limit": verb_report["limit"]}}
            report = resume_quality_report.generate(
                job_dir, job_key, qa=SYNTHETIC_QA,
                action_verb_report=result["action_verb_diversity"],
                runtime_context_path=job_dir / "_absent_runtime.json",
                metrics_registry_path=job_dir / "_absent_registry.json",
                resume_pdf_name=resume_pdf_name,
            )
            result["quality_report"] = resume_quality_report.console_summary(report, job_dir)
            return result
        return runner

    def _counting_verb_checker(self):
        real = action_verb_diversity.analyze_action_verbs_from_file

        def counting(path, *a, **kw):
            with self._verb_lock:
                self._verb_calls.append(str(path))
            return real(path, *a, **kw)
        return counting

    def _make_batch(self, batch_id, pipeline_runner=None, n_jobs=None):
        n_jobs = self.JOB_COUNT if n_jobs is None else n_jobs
        input_dir = self.track(tempfile.mkdtemp(prefix="v869-inbox-"))
        done_dir = self.track(tempfile.mkdtemp(prefix="v869-done-"))
        for i in range(n_jobs):
            write_fixture_job(input_dir / f"v869-{batch_id}-job{i}.md", company=f"Example Co {i}")

        args = _Args(input_dir=str(input_dir), done_dir=str(done_dir), batch_id=batch_id,
                     mock_responses={"requirements": mock_requirements, "content": mock_content})
        batch = run_batch.Batch(args)
        batch.pipeline_runner = pipeline_runner or self._pipeline_runner()
        batch.semantic_caller = MockCaller(args.mock_responses)
        self.track(ROOT / ".work/v86" / batch_id)
        return batch, input_dir, done_dir

    def _run(self, batch):
        # The ONLY substitutions: the private-data shared-context build and
        # the deterministic selectable-project subprocess. `Batch.run()`,
        # `process_job`, the thread pool and `future.result()` are real.
        with mock.patch.object(run_batch.build_shared_context, "build",
                               side_effect=synthetic_shared_context), \
             mock.patch.object(detp, "ensure_project_selection",
                               side_effect=lambda job_dir, req: {
                                   "ok": True, "path": Path(job_dir) / "project_selection.json",
                                   "data": {"status": "PASS",
                                            "selected_runtime_name": "Example Atlas"}}), \
             mock.patch.object(action_verb_diversity, "analyze_action_verbs_from_file",
                               side_effect=self._counting_verb_checker()):
            metrics = batch.run()
        for record in batch.job_results:
            self.track(ROOT / "output" / record["job"])
        return metrics


class TestA_RealBatchRun(RealBatchRunTestCase):

    def test_a1_each_job_keeps_its_status_and_carries_report_metadata(self):
        batch, _inbox, done_dir = self._make_batch("v869-batch-ok")
        metrics = self._run(batch)

        self.assertEqual(metrics["jobs_discovered"], 3)
        self.assertEqual(metrics["jobs_succeeded"], 3)
        self.assertEqual(metrics["jobs_failed"], 0)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 3)

        for record in batch.job_results:
            self.assertEqual(record["status"], "RESUME_READY")
            quality = record["quality_report"]
            self.assertEqual(
                set(quality),
                {"path", "diagnostic_state", "artifact_stage", "summary_by_severity",
                 "advisory_only"},
            )
            self.assertEqual(quality["path"],
                             f"output/{record['job']}/resume_quality_report.json")
            self.assertIn(quality["diagnostic_state"], ("PASS", "REVIEW", "INCOMPLETE"))
            # the batch refresh ran after an unavailable finalizer
            self.assertEqual(quality["artifact_stage"], "finalization_not_applied")
            report = json.loads(
                (ROOT / "output" / record["job"] / "resume_quality_report.json").read_text())
            self.assertEqual(report["job"], record["job"])
            self.assertEqual(report["artifact"]["resolution"], "found")

    def test_a2_jobs_quality_review_aggregate_is_correct(self):
        batch, _inbox, _done = self._make_batch("v869-batch-aggregate")
        metrics = self._run(batch)

        expected = sum(
            1 for r in batch.job_results
            if (r.get("quality_report") or {}).get("diagnostic_state") in ("REVIEW", "INCOMPLETE")
        )
        self.assertEqual(metrics["jobs_quality_review"], expected)
        # advisory only: it never moves the real pass/fail counters
        self.assertEqual(metrics["jobs_succeeded"] + metrics["jobs_failed"],
                         metrics["jobs_discovered"])

    def test_a3_batch_metrics_txt_emits_the_quality_suffix(self):
        batch, _inbox, _done = self._make_batch("v869-batch-metricstxt")
        self._run(batch)

        text = (ROOT / ".work/v86" / "v869-batch-metricstxt" / "batch_metrics.txt").read_text()
        self.assertIn("jobs_quality_review (advisory):", text)
        for record in batch.job_results:
            state = record["quality_report"]["diagnostic_state"]
            stage = record["quality_report"]["artifact_stage"]
            self.assertIn(f"{record['job']}: {record['status']}  [quality: {state} @ {stage}]", text)
        # no candidate content rides along in the summary file
        for leaked in ("Example Candidate", "example.candidate@example.com", "45ms",
                       "Example Institute"):
            self.assertNotIn(leaked, text)

    def test_a4_a_raising_refresh_cannot_kill_the_batch_or_change_a_status(self):
        batch, _inbox, done_dir = self._make_batch("v869-batch-raising")

        with mock.patch.object(
            run_batch.resume_quality_report, "refresh_after_finalization",
            side_effect=RuntimeError("synthetic diagnostic failure"),
        ):
            metrics = self._run(batch)

        self.assertEqual(metrics["jobs_discovered"], 3)
        self.assertEqual(metrics["jobs_succeeded"], 3, "a diagnostic failure must not fail a job")
        self.assertEqual(metrics["jobs_failed"], 0)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 3, "jobs must still complete and move")

        for record in batch.job_results:
            self.assertEqual(record["status"], "RESUME_READY")
            quality = record["quality_report"]
            self.assertIsNone(quality["path"])
            self.assertEqual(quality["diagnostic_state"], "INCOMPLETE")
            self.assertIn("synthetic diagnostic failure", quality["error"])
            self.assertTrue(quality["advisory_only"])
        # every job is counted as needing review, never as failed
        self.assertEqual(metrics["jobs_quality_review"], 3)

    def test_a5_action_verb_checker_runs_exactly_once_per_job(self):
        batch, _inbox, _done = self._make_batch("v869-batch-verbonce")
        self._run(batch)

        self.assertEqual(
            len(self._verb_calls), self.JOB_COUNT,
            f"expected one action-verb analysis per job, got {self._verb_calls}",
        )
        self.assertEqual(len(set(self._verb_calls)), self.JOB_COUNT)

    def test_a6_gate_semantics_and_tracker_behaviour_are_unchanged(self):
        self.assertEqual(detp.SUCCESS_STATUSES, ("RESUME_READY", "RESUME_READY_WITH_WARNINGS"))
        self.assertEqual(run_batch.SUCCESS_STATUSES, detp.SUCCESS_STATUSES)
        self.assertEqual(detp.CORRECTION_ROUND_LIMIT, 2)

        source = (V86_DIR / "deterministic_pipeline.py").read_text(encoding="utf-8")
        reasons = re.findall(r'correction_reasons\.append\("([A-Z_]+)"\)', source)
        self.assertEqual(sorted(reasons), ["ACTION_VERB_DIVERSITY", "ATS", "DENSITY", "MULTIPAGE"])

        tracker_calls = []
        real_run_step = run_batch.run_step

        def recording_run_step(cmd, *a, **kw):
            if any("tracker_writer.py" in str(part) for part in cmd):
                tracker_calls.append([str(part) for part in cmd])
            return real_run_step(cmd, *a, **kw)

        batch, _inbox, _done = self._make_batch("v869-batch-tracker")
        with mock.patch.object(run_batch, "run_step", side_effect=recording_run_step):
            self._run(batch)

        self.assertEqual(len(tracker_calls), self.JOB_COUNT, "one tracker write per job, as before")
        for cmd in tracker_calls:
            self.assertIn("--test-mode", cmd, "offline tests must never touch the live tracker")
            self.assertIn("--resume-status", cmd)
            self.assertNotIn("--quality-state", cmd, "the tracker contract is unchanged")

    def test_a7_a_genuinely_failing_job_still_reports_its_real_status(self):
        def failing_runner(job_dir, evidence_root, job_key):
            return {"job": job_key, "status": "LOCAL_QUALITY_GATE_FAILED", "timings": {}}

        batch, _inbox, done_dir = self._make_batch("v869-batch-failing",
                                                   pipeline_runner=failing_runner)
        metrics = self._run(batch)

        self.assertEqual(metrics["jobs_succeeded"], 0)
        self.assertEqual(metrics["jobs_failed"], 3)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 0, "a failed job is not moved to done")
        for record in batch.job_results:
            self.assertEqual(record["status"], "LOCAL_QUALITY_GATE_FAILED")
            # the refresh never runs for a job that did not reach finalize
            self.assertNotIn("quality_report", record)


# ===========================================================================
# B. Configurable resume-artifact name
# ===========================================================================

SYNTHETIC_PDF_NAME = "Example_Candidate_Resume.pdf"


class TestB_ConfigurableArtifactName(unittest.TestCase):

    def _job(self, tmp, pdf_names=()):
        job = Path(tmp) / "synthetic-artifact-job"
        job.mkdir(parents=True, exist_ok=True)
        (job / "resume_draft.md").write_text(SYNTHETIC_DRAFT)
        write_json(job / "resume_qa.json", SYNTHETIC_QA)
        for name in pdf_names:
            render_pdf(job / name)
        return job

    def _report(self, job, **kwargs):
        kwargs.setdefault("qa", SYNTHETIC_QA)
        kwargs.setdefault("action_verb_report", {"counts": {}, "repeated_over_limit": {}, "limit": 2})
        kwargs.setdefault("runtime_context_path", job / "_absent_runtime.json")
        kwargs.setdefault("metrics_registry_path", job / "_absent_registry.json")
        return resume_quality_report.generate(job, "synthetic-artifact-job", **kwargs)

    def test_b0_the_reusable_default_is_still_resume_pdf(self):
        self.assertEqual(resume_quality_report.RESUME_PDF_NAME, "resume.pdf")
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, ["resume.pdf"])
            resolved = resume_quality_report.resolve_resume_pdf(job)
            self.assertEqual(resolved["resolution"], "found")
            self.assertEqual(resolved["name"], "resume.pdf")

    def test_b1_b2_an_exact_non_default_name_is_discovered_and_inspected(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, [SYNTHETIC_PDF_NAME])
            report = self._report(job, resume_pdf_name=SYNTHETIC_PDF_NAME)
            on_disk = hashlib.sha256((job / SYNTHETIC_PDF_NAME).read_bytes()).hexdigest()

        self.assertEqual(report["artifact"]["resolution"], "found")
        self.assertEqual(report["artifact"]["path"], SYNTHETIC_PDF_NAME)
        self.assertEqual(report["artifact"]["expected"], SYNTHETIC_PDF_NAME)
        self.assertTrue(report["artifact"]["exists"])
        self.assertEqual(report["artifact"]["sha256"], on_disk)

        # the round-trip really inspected that file
        recall = next(c for c in report["checks"] if c["id"] == "artifact_integrity.content_recall")
        self.assertEqual(recall["status"], "pass")
        text_layer = next(c for c in report["checks"] if c["id"] == "artifact_integrity.text_layer")
        self.assertEqual(text_layer["status"], "pass")
        resolution = next(
            c for c in report["checks"] if c["id"] == "artifact_integrity.artifact_resolution")
        self.assertEqual(resolution["status"], "pass")
        self.assertIn(SYNTHETIC_PDF_NAME, resolution["reason"])

    def test_b2b_finalization_stage_follows_the_configured_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, [SYNTHETIC_PDF_NAME])
            write_json(job / "watermark_finalization.json", {
                "job": "synthetic-artifact-job", "overall_status": "FINALIZED",
                "artifacts": [{"file": SYNTHETIC_PDF_NAME, "status": "PASS"}],
            })
            finalized = self._report(job, resume_pdf_name=SYNTHETIC_PDF_NAME)
            self.assertEqual(finalized["artifact"]["stage"], "finalized")
            self.assertIn(SYNTHETIC_PDF_NAME, finalized["artifact"]["stage_reason"])

            # a record for a DIFFERENT artifact name must not be claimed
            write_json(job / "watermark_finalization.json", {
                "job": "synthetic-artifact-job", "overall_status": "FINALIZED",
                "artifacts": [{"file": "some_other_file.pdf", "status": "PASS"}],
            })
            mismatched = self._report(job, resume_pdf_name=SYNTHETIC_PDF_NAME)
            self.assertEqual(mismatched["artifact"]["stage"], "finalization_not_applied")

            failed = {"job": "synthetic-artifact-job", "overall_status": "FAILED",
                      "artifacts": [{"file": SYNTHETIC_PDF_NAME, "status": "FAILED"}]}
            write_json(job / "watermark_finalization.json", failed)
            not_applied = self._report(job, resume_pdf_name=SYNTHETIC_PDF_NAME)
            self.assertEqual(not_applied["artifact"]["stage"], "finalization_not_applied")

    def test_b3_a_glob_matching_exactly_one_file_resolves(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, [SYNTHETIC_PDF_NAME])
            report = self._report(job, resume_pdf_name="*_Resume.pdf")
        self.assertEqual(report["artifact"]["resolution"], "found")
        self.assertEqual(report["artifact"]["path"], SYNTHETIC_PDF_NAME)
        self.assertEqual(report["artifact"]["expected"], "*_Resume.pdf")
        self.assertTrue(report["artifact"]["sha256"])

    def test_b4_a_glob_matching_several_files_is_ambiguous_not_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, [SYNTHETIC_PDF_NAME, "Example_Candidate_Alt_Resume.pdf"])
            report = self._report(job, resume_pdf_name="*_Resume.pdf")

        artifact = report["artifact"]
        self.assertEqual(artifact["resolution"], "ambiguous")
        self.assertIsNone(artifact["sha256"])
        self.assertFalse(artifact["exists"])
        self.assertEqual(sorted(artifact["candidates"]),
                         ["Example_Candidate_Alt_Resume.pdf", SYNTHETIC_PDF_NAME])

        finding = next(
            f for f in report["findings"] if f["id"] == "artifact_integrity.artifact_resolution")
        self.assertEqual(finding["severity"], "high")
        self.assertEqual(finding["status"], "fail")
        self.assertEqual(report["diagnostic_state"], "REVIEW", "ambiguity is never a silent pass")
        # nothing was inspected rather than a file being picked at random
        recall = next(c for c in report["checks"] if c["id"] == "artifact_integrity.content_recall")
        self.assertEqual(recall["status"], "not_evaluated")

    def test_b5_a_missing_artifact_is_honest_never_a_false_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, [])
            report = self._report(job, resume_pdf_name=SYNTHETIC_PDF_NAME)

        self.assertEqual(report["artifact"]["resolution"], "missing")
        self.assertFalse(report["artifact"]["exists"])
        self.assertIsNone(report["artifact"]["sha256"])
        self.assertEqual(report["artifact"]["expected"], SYNTHETIC_PDF_NAME)
        self.assertEqual(report["diagnostic_state"], "INCOMPLETE")
        self.assertNotEqual(report["diagnostic_state"], "PASS")
        resolution = next(
            c for c in report["checks"] if c["id"] == "artifact_integrity.artifact_resolution")
        self.assertEqual(resolution["status"], "not_evaluated")
        self.assertIn(SYNTHETIC_PDF_NAME, resolution["reason"])

    def test_b6_a_wrongly_configured_name_does_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = self._job(tmp, ["resume.pdf"])
            report = self._report(job, resume_pdf_name=SYNTHETIC_PDF_NAME)
        # resume.pdf IS present, but it is not what the caller asked for
        self.assertEqual(report["artifact"]["resolution"], "missing")
        self.assertFalse(report["artifact"]["exists"])

    def test_b7_no_production_filename_is_hardcoded_in_the_reusable_code(self):
        for name in ("resume_quality_report.py", "pdf_roundtrip.py", "metrics_audit.py",
                     "metrics_registry_adapter.py", "bullet_length_audit.py",
                     "timeline_audit.py", "resume_draft_model.py"):
            source = (V86_DIR / name).read_text(encoding="utf-8")
            self.assertNotIn("_Resume.pdf", source, f"{name} must stay candidate-agnostic")
            self.assertNotIn("_Cover_Letter.pdf", source)


# ===========================================================================
# C. Metrics registry-shape adaptation
# ===========================================================================

EXPLICIT_MAP = {
    "Example Atlas": {"owner_id": "project_selectable_a"},
    "Example Works Platform": {"owner_id": "experience_1"},
}


class TestC_MetricsRegistryAdapter(unittest.TestCase):

    def _audit(self, registry, entity_map, payload=None, context=None):
        canonical, content_map, report = adapter.normalize_registry(registry, entity_map)
        audit = metrics_audit.audit_metrics(
            context or SYNTHETIC_CANDIDATE_CONTEXT,
            canonical,
            payload if payload is not None else json.loads(json.dumps(SYNTHETIC_PAYLOAD)),
            content_map,
        )
        # Same two-stage call the report makes: unlinked/skipped items
        # are folded back in so they stay visible as not_evaluated rows.
        audit = adapter.merge_into_audit(audit, report)
        return audit, report

    def _row(self, audit, metric_id):
        return next(r for r in audit["results"] if r["metric_id"] == metric_id)

    def test_c1_shape_detection(self):
        self.assertEqual(adapter.detect_shape({"metrics": []}), "canonical")
        self.assertEqual(adapter.detect_shape(SYNTHETIC_ENTITY_REGISTRY), "entity_items")
        self.assertEqual(adapter.detect_shape({}), "empty")
        self.assertEqual(adapter.detect_shape(None), "empty")
        self.assertEqual(adapter.detect_shape({"something_else": [1]}), "unknown")

    def test_c1b_a_canonical_registry_passes_through_untouched(self):
        canonical = {"metrics": [{"id": "m", "owner_id": "experience_1",
                                  "statement": "Cut runtime by 70%.", "verified": True}]}
        out, content_map, report = adapter.normalize_registry(canonical, None)
        self.assertIs(out, canonical)
        self.assertEqual(content_map, {})
        self.assertEqual(report["shape"], "canonical")

    def test_c1c_an_unknown_shape_yields_no_metrics_and_says_so(self):
        out, _map, report = adapter.normalize_registry({"rows": [{"id": "x"}]}, EXPLICIT_MAP)
        self.assertEqual(out, {"metrics": []})
        self.assertEqual(report["shape"], "unknown")
        self.assertIn("not recognised", report["reason"])

    def test_c2_unambiguous_explicit_mapping_links_entity_to_owner(self):
        audit, report = self._audit(SYNTHETIC_ENTITY_REGISTRY, EXPLICIT_MAP)
        self.assertEqual(report["adapted"], 2)
        self.assertEqual(report["unmapped"], [])
        self.assertEqual(self._row(audit, "atlas_latency")["status"], "present")
        self.assertEqual(self._row(audit, "atlas_latency")["measurements"], ["45ms"])
        self.assertEqual(self._row(audit, "ingestion_runtime")["status"], "present")

    def test_c3_an_unmapped_entity_is_never_linked_by_name_resemblance(self):
        # "Example Atlas" is EXACTLY a project name in the runtime context,
        # and the registry entity is spelled identically -- still unmapped.
        audit, report = self._audit(SYNTHETIC_ENTITY_REGISTRY, {})
        self.assertEqual(report["adapted"], 0)
        self.assertEqual(len(report["unmapped"]), 2)
        for row in report["unmapped"]:
            self.assertIn("name-only resemblance", row["reason"])
        for metric_id in ("atlas_latency", "ingestion_runtime"):
            row = self._row(audit, metric_id)
            self.assertEqual(row["status"], "not_evaluated")
            self.assertIsNone(row["owner_id"])
        self.assertEqual(audit["summary"]["omitted"], 0)
        self.assertEqual(audit["summary"]["not_evaluated"], 2)

    def test_c3b_a_partial_map_links_only_what_it_declares(self):
        audit, report = self._audit(
            SYNTHETIC_ENTITY_REGISTRY, {"Example Atlas": {"owner_id": "project_selectable_a"}})
        self.assertEqual(report["adapted"], 1)
        self.assertEqual(self._row(audit, "atlas_latency")["status"], "present")
        self.assertEqual(self._row(audit, "ingestion_runtime")["status"], "not_evaluated")

    def test_c3c_a_mapping_without_an_owner_id_is_rejected(self):
        audit, report = self._audit(
            SYNTHETIC_ENTITY_REGISTRY, {"Example Atlas": {"final_bullets": ["x"]}})
        self.assertEqual(report["adapted"], 0)
        self.assertIn("declares no owner_id", report["unmapped"][0]["reason"])
        self.assertEqual(self._row(audit, "atlas_latency")["status"], "not_evaluated")

    def test_c4_duplicate_entity_labels_across_items_both_resolve(self):
        registry = {"items": [
            dict(SYNTHETIC_ENTITY_REGISTRY["items"][0], id="atlas_latency_a"),
            dict(SYNTHETIC_ENTITY_REGISTRY["items"][0], id="atlas_latency_b",
                 value=90, unit="k", description="catalog records indexed"),
        ]}
        audit, report = self._audit(registry, EXPLICIT_MAP)
        self.assertEqual(report["adapted"], 2)
        self.assertEqual(self._row(audit, "atlas_latency_a")["status"], "present")
        self.assertEqual(self._row(audit, "atlas_latency_b")["measurements"], ["90k"])

    def test_c5_an_ambiguous_owner_stays_not_evaluated(self):
        context = json.loads(json.dumps(SYNTHETIC_CANDIDATE_CONTEXT))
        context["projects"].append(dict(context["projects"][0]))  # duplicate id
        audit, _report = self._audit(SYNTHETIC_ENTITY_REGISTRY, EXPLICIT_MAP, context=context)
        row = self._row(audit, "atlas_latency")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("ambiguous", row["reason"])

    def test_c5b_a_mapping_pointing_at_a_nonexistent_owner_is_not_evaluated(self):
        audit, _report = self._audit(
            SYNTHETIC_ENTITY_REGISTRY, {"Example Atlas": {"owner_id": "project_does_not_exist"}})
        row = self._row(audit, "atlas_latency")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("does not resolve", row["reason"])

    def test_c6_unverified_items_never_produce_a_finding(self):
        registry = {"items": [dict(SYNTHETIC_ENTITY_REGISTRY["items"][0], verified=False)]}
        audit, report = self._audit(registry, EXPLICIT_MAP)
        self.assertEqual(report["adapted"], 1)
        row = self._row(audit, "atlas_latency")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("not marked verified", row["reason"])
        self.assertEqual(audit["summary"]["omitted"], 0)

        missing_flag = {"items": [
            {k: v for k, v in SYNTHETIC_ENTITY_REGISTRY["items"][0].items() if k != "verified"}]}
        audit2, _ = self._audit(missing_flag, EXPLICIT_MAP)
        self.assertEqual(self._row(audit2, "atlas_latency")["status"], "not_evaluated")

    def test_c7_a_value_dropped_from_the_final_bullet_is_omitted(self):
        payload = json.loads(json.dumps(SYNTHETIC_PAYLOAD))
        payload["selected_project"]["bullets"] = [
            "Indexed a large example catalog and kept lookups responsive."
        ]
        audit, _report = self._audit(SYNTHETIC_ENTITY_REGISTRY, EXPLICIT_MAP, payload=payload)
        row = self._row(audit, "atlas_latency")
        self.assertEqual(row["status"], "omitted")
        self.assertEqual(row["measurements"], ["45ms"])
        self.assertEqual(row["measurements_present"], [])

    def test_c7b_a_value_that_differs_from_the_bullet_is_not_silently_accepted(self):
        # The registry says 45ms; the shipped bullet says 60ms. That is NOT
        # "present" -- the audit reports the registry's measurement as not
        # communicated, and never rewrites either number.
        payload = json.loads(json.dumps(SYNTHETIC_PAYLOAD))
        payload["selected_project"]["bullets"] = [
            "Indexed a 90k-record example catalog and held p99 lookup latency under 60ms."
        ]
        audit, _report = self._audit(SYNTHETIC_ENTITY_REGISTRY, EXPLICIT_MAP, payload=payload)
        row = self._row(audit, "atlas_latency")
        self.assertEqual(row["status"], "omitted")
        self.assertEqual(row["measurements_present"], [])

    def test_c7c_a_metric_with_no_supporting_source_bullet_is_not_evaluated(self):
        registry = {"items": [dict(SYNTHETIC_ENTITY_REGISTRY["items"][0],
                                   id="unsupported", value=4200, unit="rps")]}
        audit, _report = self._audit(registry, EXPLICIT_MAP)
        row = self._row(audit, "unsupported")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("no approved source bullet", row["reason"])

    def test_c8_value_and_unit_boundaries_survive_adaptation(self):
        self.assertEqual(adapter._measurement_tokens(45, "ms"), ["45ms"])
        self.assertEqual(adapter._measurement_tokens(4, "hours"), ["4hours"])
        self.assertEqual(adapter._measurement_tokens(70, "%"), ["70%"])
        self.assertEqual(adapter._measurement_tokens("1,200", None), ["1200"])
        # no value -> nothing invented
        self.assertEqual(adapter._measurement_tokens(None, "ms"), [])
        self.assertEqual(adapter._measurement_tokens("", "ms"), [])
        self.assertEqual(adapter._measurement_tokens("not a number", "ms"), [])

        # the word-unit fix still holds end to end through the adapter
        registry = {"items": [dict(SYNTHETIC_ENTITY_REGISTRY["items"][1])]}
        audit, _report = self._audit(registry, EXPLICIT_MAP)
        self.assertEqual(self._row(audit, "ingestion_runtime")["measurements"], ["4hours"])
        self.assertEqual(self._row(audit, "ingestion_runtime")["status"], "present")

        # and strict boundaries are unchanged
        self.assertEqual(metrics_audit.measurement_present(["45ms"], ["took 245ms"]), [])
        self.assertEqual(metrics_audit.measurement_present(["4hours"], ["took 4 days"]), [])

    def test_c8b_a_valueless_item_is_skipped_with_a_reason_not_invented(self):
        registry = {"items": [dict(SYNTHETIC_ENTITY_REGISTRY["items"][0], value=None)]}
        _canonical, _map, report = adapter.normalize_registry(registry, EXPLICIT_MAP)
        self.assertEqual(report["adapted"], 0)
        self.assertEqual(len(report["skipped"]), 1)
        self.assertIn("no numeric value", report["skipped"][0]["reason"])

    def test_c8c_a_description_number_never_widens_the_metric(self):
        # the description mentions 3 regions; only the structured
        # value/unit (45 ms) is the metric
        registry = {"items": [dict(
            SYNTHETIC_ENTITY_REGISTRY["items"][0],
            description="p99 lookup latency across 3 example regions")]}
        canonical, _map, _report = adapter.normalize_registry(registry, EXPLICIT_MAP)
        self.assertEqual(canonical["metrics"][0]["measurements"], ["45ms"])

    def test_c9_an_explicit_final_bullets_path_reaches_a_different_payload_shape(self):
        alt_payload = {
            "alternate_group_bullets": {
                "group_a": ["Indexed a 90k-record example catalog and held p99 lookup latency under 45ms."],
            },
        }
        entity_map = {"Example Atlas": {"owner_id": "project_selectable_a",
                                        "final_bullets": ["alternate_group_bullets", "group_a"]}}
        audit, _report = self._audit(SYNTHETIC_ENTITY_REGISTRY, entity_map, payload=alt_payload)
        self.assertEqual(self._row(audit, "atlas_latency")["status"], "present")

        alt_payload["alternate_group_bullets"]["group_a"] = ["A bullet with no measurement."]
        audit2, _ = self._audit(SYNTHETIC_ENTITY_REGISTRY, entity_map, payload=alt_payload)
        self.assertEqual(self._row(audit2, "atlas_latency")["status"], "omitted")

    def test_c9b_a_bad_explicit_path_is_reported_not_guessed_around(self):
        entity_map = {"Example Atlas": {"owner_id": "project_selectable_a",
                                        "final_bullets": ["no_such_key", "nope"]}}
        audit, _report = self._audit(SYNTHETIC_ENTITY_REGISTRY, entity_map, payload={})
        row = self._row(audit, "atlas_latency")
        self.assertEqual(row["status"], "not_evaluated")
        self.assertIn("does not resolve", row["reason"])

    def test_c10_the_report_surfaces_adaptation_outcomes_as_not_evaluated_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "synthetic-adapter-job"
            job.mkdir()
            (job / "resume_draft.md").write_text(SYNTHETIC_DRAFT)
            write_json(job / "resume_payload.json", SYNTHETIC_PAYLOAD)
            write_json(job / "resume_qa.json", SYNTHETIC_QA)
            write_json(job / "_context.json", SYNTHETIC_CANDIDATE_CONTEXT)
            write_json(job / "_registry.json", SYNTHETIC_ENTITY_REGISTRY)
            render_pdf(job / "resume.pdf")

            unmapped = resume_quality_report.generate(
                job, "synthetic-adapter-job", qa=SYNTHETIC_QA,
                action_verb_report={"counts": {}, "repeated_over_limit": {}, "limit": 2},
                runtime_context_path=job / "_context.json",
                metrics_registry_path=job / "_registry.json",
            )
            mapped = resume_quality_report.generate(
                job, "synthetic-adapter-job", qa=SYNTHETIC_QA,
                action_verb_report={"counts": {}, "repeated_over_limit": {}, "limit": 2},
                runtime_context_path=job / "_context.json",
                metrics_registry_path=job / "_registry.json",
                metrics_entity_content_map=EXPLICIT_MAP,
            )

        unmapped_check = next(
            c for c in unmapped["checks"] if c["id"] == "metrics.verified_measurement_recall")
        self.assertEqual(unmapped_check["status"], "pass")
        self.assertIn("2 not evaluated", unmapped_check["reason"])
        self.assertTrue(any("adapted from shape 'entity_items'" in n for n in unmapped["notes"]))
        self.assertEqual(
            [f for f in unmapped["findings"] if f["category"] == "metrics"], [],
            "an unlinked registry must never produce a metrics finding",
        )

        mapped_check = next(
            c for c in mapped["checks"] if c["id"] == "metrics.verified_measurement_recall")
        self.assertIn("2 present", mapped_check["reason"])


class TestPrivacyOfThisSuite(unittest.TestCase):

    def test_only_synthetic_contact_details(self):
        source = Path(__file__).read_text(encoding="utf-8")
        for email in re.findall(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", source):
            self.assertTrue(email.endswith(("example.com", ".invalid")), email)
        for phone in re.findall(r"\(\d{3}\)[\s.\-]?\d{3}[\s.\-]\d{4}", source):
            self.assertIn("555", phone)
        home = re.compile("/" + "Users" + r"/(?!example|fake-home|x)")
        self.assertIsNone(home.search(source))

    def test_no_private_candidate_paths_are_read(self):
        source = Path(__file__).read_text(encoding="utf-8")
        for marker in ('ROOT / "' + "master" + '"', "master" + "/runtime",
                       "metrics_registry" + ".json", "runtime_resume_context" + ".json"):
            self.assertNotIn(marker, source)

    def test_no_network_or_model_imports(self):
        import ast

        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for forbidden in ("urllib", "http", "socket", "requests", "anthropic"):
            self.assertNotIn(forbidden, imported)


if __name__ == "__main__":
    unittest.main(verbosity=2)
