#!/usr/bin/env python3
"""
V8.6 -- offline test suite (Section 19).

ALL semantic calls in this suite go through llm_invoke.MockCaller. NOTHING
here ever shells out to the real `claude` CLI, calls Apify, or scrapes jobs.
Real money is never spent running this file.

Rendering (docx2pdf / Microsoft Word) is NOT exercised end-to-end here --
that dependency is orthogonal to what this suite is validating (batch
orchestration: concurrency limits, live refill, per-job isolation,
correction-packet constraints, tracker safety, finalizer behavior against a
mocked HTTP service). Tests that need the deterministic pipeline's outcome
inject a fast fake `pipeline_runner` (see tools/resume/v86/run_batch.py,
`Batch.pipeline_runner`) instead of invoking the real Word-dependent
renderer. Tests that exercise real frozen/adapted deterministic scripts
(select_project.py, validate_strategy_contract_v86.py,
validate_project_selection.py) do so directly -- none of those need
Word.

Run with:
    python3 tools/resume/v86/tests/test_v86_offline.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import http.server
import base64
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_job_packet  # noqa: E402
import build_shared_context  # noqa: E402
import correction_worker  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import finalize_local  # noqa: E402
import run_batch  # noqa: E402
import semantic_worker  # noqa: E402
import tracker_writer  # noqa: E402
from common import ROOT as COMMON_ROOT, load_json, write_json  # noqa: E402
from llm_invoke import MockCaller  # noqa: E402

ROOT = COMMON_ROOT


# ---------------------------------------------------------------------------
# Canned, structurally-valid MockCaller responses
# ---------------------------------------------------------------------------

def valid_requirements(_prompt=None):
    return {
        "job_key": "any",
        "concepts": [
            {"term": "Python", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
            {"term": "PyTorch", "category": "PREFERRED_HARD_SKILL", "required": False, "priority": "medium"},
        ],
    }


def valid_content(_prompt=None):
    return {
        "resume_payload": {
            "experience_bullet_orders": {"experience_1": [0, 1, 2, 3]},
            "experience_alternate_bullets": {
                "experience_2": [
                    "Built a data pipeline for retail forecasting.",
                    "Automated ETL jobs cutting runtime 40%.",
                ],
            },
            "project_bullet_orders": {"project_1": [0, 1, 2], "project_2": [0, 1, 2]},
            "selected_project": {
                "owner_id": "project_selectable_1",
                "title": "Project Meridian",
                "technology_line": "Python, FastAPI, PostgreSQL",
                "bullets": ["Built an internal search API.", "Deployed via Docker with sub-200ms latency."],
            },
        },
        "cover_letter_payload": {
            "company": "Acme AI", "role": "ML Intern", "salutation": "Dear Hiring Team,",
            "opening": "I am excited to apply for the ML Intern role at Acme AI given my hands-on applied ML background.",
            "experience_paragraphs": {
                "experience_1": "At Example Research Lab I designed a data-cleaning pipeline, improving classifier validation F1 across a multi-source dataset.",
                "experience_2": "At Example Data Systems I automated ETL workflows, cutting pipeline runtime meaningfully while improving data reliability.",
            },
            "project_paragraph": "In Project Meridian I built an internal search API, deployed via Docker with low latency under load.",
            "closing": "I would welcome the opportunity to bring this experience to Acme AI's ML team this summer.",
        },
        "strategy_status": {"status": "STRATEGY_READY"},
    }


def valid_correction(_prompt=None):
    payload = valid_content()["resume_payload"]
    payload["experience_alternate_bullets"]["experience_2"][0] = "Short bullet."
    return {
        "resume_payload": payload,
        "correction_status": {"status": "CORRECTED", "correction_round": 1, "reasons": ["shortened experience_2 bullet"]},
    }


def make_mock_caller():
    return MockCaller({
        "requirements": valid_requirements,
        "content": valid_content,
        "correction": valid_correction,
    })


def write_fixture_job(path: Path, company="Acme AI", role="ML Intern"):
    path.write_text(f"# {role} at {company}\n\nRequired: Python, PyTorch.\nPreferred: FastAPI.\n")


class CleaningTestCase(unittest.TestCase):
    """Base class that tracks and removes any output/<job>/ and
    .work/v86/<batch>/ directories it creates, so the offline suite never
    leaves test pollution in the real repo tree."""

    def setUp(self):
        self._cleanup_dirs = []

    def track(self, path: Path):
        self._cleanup_dirs.append(Path(path))
        return path

    def tearDown(self):
        for d in self._cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# 1. Shared context + job packet
# ---------------------------------------------------------------------------

class TestSharedContextAndPacket(CleaningTestCase):
    def test_build_shared_context_and_packet(self):
        batch_dir = self.track(ROOT / ".work/v86/test-shared-context-1")
        manifest = build_shared_context.build(batch_dir)
        self.assertTrue((batch_dir / "shared_candidate_context.json").exists())
        self.assertTrue((batch_dir / "shared_ats_evidence").is_dir())

        ctx = load_json(batch_dir / "shared_candidate_context.json")
        self.assertIn("candidate", ctx)
        self.assertIn("experience", ctx)
        self.assertIn("truth_constraints", ctx)
        # No new evidence introduced -- every top-level key must already be
        # a key of the authoritative runtime context.
        runtime = load_json(ROOT / "master/runtime/runtime_resume_context.json")
        self.assertTrue(set(ctx.keys()) <= set(runtime.keys()) | {"schema_version"})

        with tempfile.TemporaryDirectory() as td:
            job_file = Path(td) / "acme-ml-intern-test.md"
            write_fixture_job(job_file)
            packet_path = build_job_packet.build_packet(batch_dir, job_file, "acme-ml-intern-test")
            packet = load_json(packet_path)
            self.assertEqual(packet["job_key"], "acme-ml-intern-test")
            self.assertIn("candidate_context", packet)
            self.assertIn("allowed_selectable_projects", packet)
            self.assertGreaterEqual(len(packet["allowed_selectable_projects"]), 1)


# ---------------------------------------------------------------------------
# 2. Semantic worker schemas via MockCaller (no repo exploration needed)
# ---------------------------------------------------------------------------

class TestSemanticWorkerMocked(CleaningTestCase):
    def test_two_calls_per_normal_job(self):
        batch_dir = self.track(ROOT / ".work/v86/test-semantic-mocked-1")
        build_shared_context.build(batch_dir)
        with tempfile.TemporaryDirectory() as td:
            job_file = Path(td) / "acme-ml-intern-test2.md"
            write_fixture_job(job_file)
            packet = load_json(build_job_packet.build_packet(batch_dir, job_file, "acme-ml-intern-test2"))

        caller = make_mock_caller()
        r1 = semantic_worker.run_requirements_call(packet, caller=caller)
        self.assertTrue(r1.ok)
        self.assertEqual(caller.calls, ["requirements"])

        r2 = semantic_worker.run_content_call(packet, r1.data, {"selection_mode": "MODEL_CHOICE"}, caller=caller)
        self.assertTrue(r2.ok)
        self.assertEqual(caller.calls, ["requirements", "content"])
        self.assertEqual(r2.data["strategy_status"]["status"], "STRATEGY_READY")
        # exactly 2 semantic model requests for a normal job -- Section 5/17.
        self.assertEqual(len(caller.calls), 2)


# ---------------------------------------------------------------------------
# 3. Deterministic building blocks that do NOT need Word
# ---------------------------------------------------------------------------

class TestDeterministicUnits(CleaningTestCase):
    def test_project_selection_and_strategy_contract(self):
        job_dir = self.track(ROOT / "output/v86-test-detunits-job")
        job_dir.mkdir(parents=True, exist_ok=True)

        jd = valid_requirements()
        jd["job_key"] = "v86-test-detunits-job"
        write_json(job_dir / "jd_requirements.json", jd)

        selection = detp.ensure_project_selection(job_dir, job_dir / "jd_requirements.json")
        self.assertTrue(selection["ok"], selection)
        self.assertIn("selection_mode", selection["data"])

        content = valid_content()
        detp.write_strategy_files(
            job_dir, "jobs/batch_inbox/v86-test-detunits-job.md", "v86-test-detunits-job",
            jd, content["resume_payload"], content["cover_letter_payload"],
            content["resume_payload"]["selected_project"]["owner_id"],
        )

        from common import python_cmd, run_step
        report = job_dir / "v8_contract_validation.json"
        run_step(python_cmd(ROOT / "tools/resume/v86/validate_strategy_contract_v86.py", "--job-dir", job_dir, "--report", report))
        result = load_json(report)
        self.assertEqual(result["status"], "PASS", result.get("failures"))

        guard_report = job_dir / "v8_project_selection_validation.json"
        run_step(python_cmd(
            ROOT / "tools/resume/v8/validate_project_selection.py",
            "--job-dir", job_dir, "--selection", job_dir / "project_selection.json", "--report", guard_report,
        ))
        guard = load_json(guard_report)
        self.assertEqual(guard["status"], "PASS", guard.get("failures"))

    def test_correction_packet_and_schema_expose_no_reselection_path(self):
        packet = correction_worker.build_correction_packet(
            "job-x", valid_content()["resume_payload"],
            {"correction_round": 1, "reasons": ["ATS"], "details": {}},
            {"title": "Project Meridian"}, {"projects": []},
        )
        self.assertNotIn("selected_project.title", packet["editable_fields"])
        self.assertNotIn("allowed_selectable_projects", packet)

        schema = correction_worker.correction_output_json_schema()
        resume_props = schema["properties"]["resume_payload"]["properties"]
        selected_project_props = resume_props["selected_project"]["properties"]
        # the correction schema has no field through which a different
        # project *identity* could be selected -- only truthful wording of
        # the already-bound project (technology_line / bullets / title text)
        # plus the owner_id echoed back unchanged (enforced at the Python
        # level by deterministic_pipeline.apply_correction, not by the
        # schema itself).
        self.assertEqual(set(selected_project_props.keys()), {"owner_id", "title", "technology_line", "bullets"})
        self.assertTrue(resume_props["selected_project"]["additionalProperties"] is False)
        self.assertTrue(schema["additionalProperties"] is False)

    def test_apply_correction_rejects_unauthorized_reselection(self):
        job_dir = self.track(ROOT / "output/v86-test-correction-guard-job")
        job_dir.mkdir(parents=True, exist_ok=True)

        content = valid_content()
        write_json(job_dir / "resume_payload.json", content["resume_payload"])

        tampered = json.loads(json.dumps(content["resume_payload"]))
        tampered["selected_project"]["owner_id"] = "project_selectable_2"

        status = detp.apply_correction(
            job_dir, 1, tampered, {"status": "CORRECTED", "reasons": ["closing ats gap"]},
            bound_project_id="project_selectable_1",
        )
        self.assertEqual(status["status"], "CORRECTION_FAILED")
        self.assertIn("REJECTED_UNAUTHORIZED_PROJECT_RESELECTION", status["reasons"][0])
        # resume_payload.json on disk must be UNCHANGED (still the original project)
        on_disk = load_json(job_dir / "resume_payload.json")
        self.assertEqual(on_disk["selected_project"]["owner_id"], "project_selectable_1")

    def test_apply_correction_accepts_authorized_edit(self):
        job_dir = self.track(ROOT / "output/v86-test-correction-ok-job")
        job_dir.mkdir(parents=True, exist_ok=True)
        content = valid_content()
        write_json(job_dir / "resume_payload.json", content["resume_payload"])

        edited = json.loads(json.dumps(content["resume_payload"]))
        edited["experience_alternate_bullets"]["experience_2"][0] = "Shorter bullet."

        status = detp.apply_correction(
            job_dir, 1, edited, {"status": "CORRECTED", "reasons": ["shortened"]},
            bound_project_id="project_selectable_1",
        )
        self.assertEqual(status["status"], "CORRECTED")
        on_disk = load_json(job_dir / "resume_payload.json")
        self.assertEqual(on_disk["experience_alternate_bullets"]["experience_2"][0], "Shorter bullet.")


# ---------------------------------------------------------------------------
# 4. Tracker writer safety
# ---------------------------------------------------------------------------

class TestTrackerWriter(unittest.TestCase):
    def setUp(self):
        self.test_tracker = tracker_writer.TEST_TRACKER
        if self.test_tracker.exists():
            self.test_tracker.unlink()

    def tearDown(self):
        if self.test_tracker.exists():
            self.test_tracker.unlink()
        lock = self.test_tracker.parent / f".{self.test_tracker.name}.lock"
        lock.unlink(missing_ok=True)

    def test_never_touches_production_tracker(self):
        before = None
        if tracker_writer.PROD_TRACKER.exists():
            before = tracker_writer.PROD_TRACKER.stat().st_mtime
        tracker_writer.update_tracker(
            test_mode=True, job_key="v86-test-job-1", company="Acme", role="ML Intern",
            resume_status="COMPLETE", cover_letter_status="COMPLETE", watermark_status="FINALIZED",
            final_resume="output/v86-test-job-1/resume.pdf",
            final_cover_letter="output/v86-test-job-1/cover_letter.pdf",
        )
        self.assertTrue(self.test_tracker.exists())
        self.assertNotEqual(str(tracker_writer.PROD_TRACKER), str(tracker_writer.LIVE_TRACKER))
        if before is not None:
            self.assertEqual(tracker_writer.PROD_TRACKER.stat().st_mtime, before)

    def test_preserves_user_owned_fields(self):
        tracker_writer.update_tracker(test_mode=True, job_key="v86-test-job-2", company="Acme", role="ML Intern")

        from openpyxl import load_workbook
        wb = load_workbook(self.test_tracker)
        ws = wb["Applications"]
        header = [c.value for c in ws[1]]
        row = [c.value for c in ws[2]]
        row[header.index("Application Status")] = "INTERVIEW"
        row[header.index("Notes")] = "Recruiter called."
        for i, v in enumerate(row):
            ws.cell(row=2, column=i + 1, value=v)
        wb.save(self.test_tracker)

        tracker_writer.update_tracker(
            test_mode=True, job_key="v86-test-job-2", company="Acme", role="ML Intern",
            resume_status="COMPLETE", watermark_status="FINALIZED",
        )

        wb2 = load_workbook(self.test_tracker)
        ws2 = wb2["Applications"]
        row2 = [c.value for c in ws2[2]]
        self.assertEqual(row2[header.index("Application Status")], "INTERVIEW")
        self.assertEqual(row2[header.index("Notes")], "Recruiter called.")
        self.assertEqual(row2[header.index("Resume Status")], "COMPLETE")

    def test_never_auto_sets_applied(self):
        tracker_writer.update_tracker(test_mode=True, job_key="v86-test-job-3", resume_status="COMPLETE")
        from openpyxl import load_workbook
        wb = load_workbook(self.test_tracker)
        ws = wb["Applications"]
        header = [c.value for c in ws[1]]
        row = [c.value for c in ws[2]]
        self.assertEqual(row[header.index("Application Status")], "NOT APPLIED")


# ---------------------------------------------------------------------------
# 5. Finalizer against a mocked watermark HTTP service
# ---------------------------------------------------------------------------

class _MockWatermarkHandler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"ok": True, "version": "mock"})
        elif self.path == "/capabilities":
            self._send_json({"tools": {}})
        else:
            self._send_json({"ok": False}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        if self.path == "/inspect":
            self._send_json({"ok": True, "kind": "container", "suspicious": {"verdict": False}, "report": {}})
        elif self.path == "/clean":
            # Pass-through "clean": returns the same bytes, proving text
            # integrity is preserved (a real cleaner would strip metadata
            # only; this mock is sufficient for offline finalize testing).
            self._send_json({"ok": True, "kind": "container", "cleaned": body["file"], "report": {"actions": []}})
        else:
            self._send_json({"ok": False}, 404)

    def log_message(self, fmt, *args):
        pass


class TestFinalizeLocal(CleaningTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _MockWatermarkHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def _make_fixture_docx(self, path: Path, text: str):
        from docx import Document
        doc = Document()
        doc.add_paragraph(text)
        doc.save(str(path))

    def _make_fixture_pdf(self, path: Path, text: str):
        import fitz
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text)
        doc.save(str(path))
        doc.close()

    def test_finalize_pass_with_mocked_service(self):
        job_dir = self.track(ROOT / "output/v86-test-finalize-job")
        job_dir.mkdir(parents=True, exist_ok=True)
        self._make_fixture_docx(job_dir / "resume.docx", "Candidate Example Resume")
        self._make_fixture_pdf(job_dir / "resume.pdf", "Candidate Example Resume")

        base_url = f"http://127.0.0.1:{self.port}"
        self.assertTrue(finalize_local.check_health(base_url))

        record = finalize_local.finalize_job(job_dir, base_url, "healthy")
        self.assertEqual(record["overall_status"], "FINALIZED")
        self.assertEqual(record["layer_b"], "NOT_RUN")
        statuses = {a["file"]: a["status"] for a in record["artifacts"]}
        self.assertEqual(statuses["resume.docx"], "PASS")
        self.assertEqual(statuses["resume.pdf"], "PASS")
        # originals preserved
        self.assertTrue((job_dir / ".work/finalize_originals/resume.original.docx").exists())

    def test_service_unavailable_is_safe(self):
        job_dir = self.track(ROOT / "output/v86-test-finalize-unavailable-job")
        job_dir.mkdir(parents=True, exist_ok=True)
        self._make_fixture_docx(job_dir / "resume.docx", "Candidate Example Resume")
        before_bytes = (job_dir / "resume.docx").read_bytes()

        record = finalize_local.finalize_job(job_dir, "http://127.0.0.1:1", "unavailable")
        self.assertEqual(record["overall_status"], "SERVICE_UNAVAILABLE")
        after_bytes = (job_dir / "resume.docx").read_bytes()
        self.assertEqual(before_bytes, after_bytes)  # untouched, no fake success


# ---------------------------------------------------------------------------
# 6. Full batch orchestration (Section 19: 1 / 3 / 10 / 16 jobs, live refill,
#    state isolation, correction rounds, done/inbox file movement)
# ---------------------------------------------------------------------------

def fake_pipeline_runner_ready(job_dir, evidence_root, job_key):
    time.sleep(0.05)
    return {"job": job_key, "status": "RESUME_READY", "timings": {}}


def make_one_correction_pipeline_runner():
    seen = {}

    def runner(job_dir, evidence_root, job_key):
        time.sleep(0.05)
        n = seen.get(job_key, 0) + 1
        seen[job_key] = n
        if n == 1:
            req = {
                "job": job_key, "correction_round": 1, "reasons": ["ATS"],
                "details": {"ats": {"supported_but_missing": ["Python"], "unsupported_required_terms": [], "instruction": "repair"}},
                "allowed_scope": "dynamic_resume_content_only",
            }
            write_json(job_dir / "correction_request.json", req)
            return {"job": job_key, "status": "NEEDS_LLM_CORRECTION", "correction_request": str(req)}
        return {"job": job_key, "status": "RESUME_READY", "timings": {}}

    return runner


class _Args:
    def __init__(self, **kw):
        self.input_dir = None
        self.done_dir = None
        self.max_workers = 10
        self.test_mode = True
        self.base_url = "http://127.0.0.1:1"  # unreachable -> finalizer stays SERVICE_UNAVAILABLE, no Word needed
        self.batch_id = None
        self.mock_semantic = True
        self.mock_health_status = "unavailable"
        self.mock_responses = {}
        self.__dict__.update(kw)


class TestBatchOrchestration(CleaningTestCase):
    def _make_batch(self, n_jobs, batch_id, pipeline_runner=fake_pipeline_runner_ready, mock_responses=None):
        input_dir = Path(tempfile.mkdtemp(prefix="v86-test-inbox-"))
        done_dir = Path(tempfile.mkdtemp(prefix="v86-test-done-"))
        self.track(input_dir)
        self.track(done_dir)

        job_files = []
        for i in range(n_jobs):
            jf = input_dir / f"v86-test-{batch_id}-job{i}.md"
            write_fixture_job(jf, company=f"Company{i}", role="ML Intern")
            job_files.append(jf)

        args = _Args(
            input_dir=str(input_dir), done_dir=str(done_dir), batch_id=batch_id,
            mock_responses=mock_responses or {"requirements": valid_requirements, "content": valid_content, "correction": valid_correction},
        )
        batch = run_batch.Batch(args)
        batch.pipeline_runner = pipeline_runner
        batch.semantic_caller = MockCaller(args.mock_responses)
        self.track(ROOT / ".work/v86" / batch_id)
        return batch, input_dir, done_dir, job_files

    def _cleanup_outputs(self, batch):
        for r in batch.job_results:
            self.track(ROOT / "output" / r["job"])

    def test_1_job(self):
        batch, input_dir, done_dir, job_files = self._make_batch(1, "test-1job")
        metrics = batch.run()
        self._cleanup_outputs(batch)
        self.assertEqual(metrics["jobs_discovered"], 1)
        self.assertEqual(metrics["jobs_succeeded"], 1)
        self.assertEqual(metrics["peak_semantic_concurrency"], 1)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 1)
        self.assertEqual(len(list(input_dir.glob("*.md"))), 0)

    def test_3_jobs_concurrent(self):
        batch, input_dir, done_dir, job_files = self._make_batch(3, "test-3jobs")
        metrics = batch.run()
        self._cleanup_outputs(batch)
        self.assertEqual(metrics["jobs_discovered"], 3)
        self.assertEqual(metrics["jobs_succeeded"], 3)
        self.assertGreaterEqual(metrics["peak_semantic_concurrency"], 2)
        self.assertLessEqual(metrics["peak_semantic_concurrency"], 3)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 3)

    def test_10_jobs_at_default_cap(self):
        batch, input_dir, done_dir, job_files = self._make_batch(10, "test-10jobs")
        metrics = batch.run()
        self._cleanup_outputs(batch)
        self.assertEqual(metrics["jobs_succeeded"], 10)
        self.assertLessEqual(metrics["peak_semantic_concurrency"], 10)

    def test_16_jobs_never_exceeds_max_workers(self):
        batch, input_dir, done_dir, job_files = self._make_batch(16, "test-16jobs")
        metrics = batch.run()
        self._cleanup_outputs(batch)
        self.assertEqual(metrics["jobs_discovered"], 16)
        self.assertEqual(metrics["jobs_succeeded"], 16)
        self.assertLessEqual(metrics["peak_semantic_concurrency"], 10)
        self.assertGreaterEqual(metrics["peak_semantic_concurrency"], 5)  # actually saturated the cap

    def test_state_isolation_across_jobs(self):
        batch, input_dir, done_dir, job_files = self._make_batch(4, "test-isolation")
        batch.run()
        job_keys = [jk.stem for jk in job_files]
        for job_key in job_keys:
            local_result = load_json(ROOT / ".work/v86/test-isolation/jobs" / job_key / "local_pipeline_result.json")
            self.assertEqual(local_result["job"], job_key)
            self.assertEqual(local_result["status"], "RESUME_READY")
        self._cleanup_outputs(batch)

    def test_correction_round_flows_through_orchestrator(self):
        runner = make_one_correction_pipeline_runner()
        batch, input_dir, done_dir, job_files = self._make_batch(1, "test-correction-flow", pipeline_runner=runner)
        metrics = batch.run()
        self._cleanup_outputs(batch)
        job_result = metrics["jobs"][0]
        self.assertEqual(job_result["status"], "RESUME_READY")
        self.assertEqual(len(job_result["correction_rounds"]), 1)
        self.assertEqual(job_result["semantic_request_count"], 2)
        self.assertEqual(job_result["semantic_request_count"] + len(job_result["correction_rounds"]), 3)

    def test_failed_job_stays_in_inbox(self):
        def always_fails(job_dir, evidence_root, job_key):
            return {"job": job_key, "status": "LOCAL_QUALITY_GATE_FAILED"}

        batch, input_dir, done_dir, job_files = self._make_batch(1, "test-failed-stays", pipeline_runner=always_fails)
        batch.run()
        self._cleanup_outputs(batch)
        self.assertEqual(len(list(input_dir.glob("*.md"))), 1)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 0)

    def test_live_refill_picks_up_new_arrival(self):
        input_dir = Path(tempfile.mkdtemp(prefix="v86-test-inbox-refill-"))
        done_dir = Path(tempfile.mkdtemp(prefix="v86-test-done-refill-"))
        self.track(input_dir)
        self.track(done_dir)
        batch_id = "test-live-refill"
        self.track(ROOT / ".work/v86" / batch_id)

        jf0 = input_dir / "v86-refill-job0.md"
        write_fixture_job(jf0)

        args = _Args(input_dir=str(input_dir), done_dir=str(done_dir), batch_id=batch_id, max_workers=1)
        batch = run_batch.Batch(args)
        batch.semantic_caller = MockCaller({"requirements": valid_requirements, "content": valid_content, "correction": valid_correction})

        added = {"done": False}

        def slow_runner(job_dir, evidence_root, job_key):
            if not added["done"]:
                jf1 = input_dir / "v86-refill-job1.md"
                write_fixture_job(jf1)
                added["done"] = True
            time.sleep(0.3)
            return {"job": job_key, "status": "RESUME_READY", "timings": {}}

        batch.pipeline_runner = slow_runner
        metrics = batch.run()
        self._cleanup_outputs(batch)

        self.assertEqual(metrics["jobs_discovered"], 2)
        self.assertEqual(metrics["jobs_succeeded"], 2)
        self.assertEqual(len(list(done_dir.glob("*.md"))), 2)

    def test_restart_does_not_reprocess_completed_job(self):
        batch, input_dir, done_dir, job_files = self._make_batch(1, "test-restart-safety")
        batch.run()
        self._cleanup_outputs(batch)

        # Simulate a leftover copy still sitting in the inbox after the
        # real file was already moved to done/ in a prior run -- a fresh
        # scan must never resurrect it.
        leftover = input_dir / job_files[0].name
        leftover.write_text("leftover duplicate")
        found = batch.scan_unclaimed()
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
