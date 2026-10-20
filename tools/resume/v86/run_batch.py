#!/usr/bin/env python3
"""
V8.6 -- one-shot batch runner.

    python3 tools/resume/v86/run_batch.py \\
        --input-dir jobs/batch_inbox --done-dir jobs/batch_done --max-workers 10

See tools/resume/v86/README.md for full usage, the production-style
command, --test-mode, and the "Python interpreter note" (this must be run
with `python3`, not `python3.13` -- see tools/resume/v86/common.py).

This is a ONE-SHOT batch process, not a persistent rolling queue:
  1. scans --input-dir for *.md
  2. claims and processes up to --max-workers jobs' SEMANTIC calls at once
  3. whenever a semantic slot frees up, rescans for newly-arrived jobs
  4. pipelines each job independently: semantic -> deterministic ->
     correction (bounded) -> finalize -> tracker -> done, WITHOUT waiting
     for other jobs' semantic phases to finish first
  5. stops when no jobs are active and a final rescan finds nothing new
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, Timer, job_key_for, load_json, now_iso, python_cmd, run_step, write_json  # noqa: E402
import build_job_packet  # noqa: E402
import build_shared_context  # noqa: E402
import correction_worker  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import semantic_worker  # noqa: E402
from llm_invoke import MockCaller  # noqa: E402
from normalize_cover_letter_identity import backfill_cover_letter_company  # noqa: E402
from normalize_jd_concepts import normalize_jd_requirements  # noqa: E402
import resume_quality_report  # noqa: E402

SUCCESS_STATUSES = detp.SUCCESS_STATUSES


class ConcurrencyTracker:
    """Thread-safe in-flight counter + running peak, used for the
    peak_semantic_concurrency / peak_deterministic_concurrency /
    peak_finalization_concurrency batch metrics (Section 16)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._current = 0
        self.peak = 0

    def __enter__(self):
        with self._lock:
            self._current += 1
            self.peak = max(self.peak, self._current)
        return self

    def __exit__(self, exc_type, exc, tb):
        with self._lock:
            self._current -= 1
        return False


class Batch:
    def __init__(self, args):
        self.args = args
        self.batch_id = args.batch_id
        self.batch_dir = ROOT / ".work/v86" / self.batch_id
        self.input_dir = (ROOT / args.input_dir) if not Path(args.input_dir).is_absolute() else Path(args.input_dir)
        self.done_dir = (ROOT / args.done_dir) if not Path(args.done_dir).is_absolute() else Path(args.done_dir)
        self.done_dir.mkdir(parents=True, exist_ok=True)

        self.semantic_semaphore = threading.Semaphore(args.max_workers)
        self.semantic_tracker = ConcurrencyTracker()
        self.deterministic_tracker = ConcurrencyTracker()
        self.finalization_tracker = ConcurrencyTracker()

        self.claimed_lock = threading.Lock()
        self.claimed: set = set()

        self.results_lock = threading.Lock()
        self.job_results: list = []

        self.health_status = "unavailable"
        self.evidence_root: Optional[Path] = None

        self.semantic_caller = MockCaller(args.mock_responses) if args.mock_semantic else None

        # Test seam: offline tests substitute a fast fake so orchestration
        # (concurrency/refill/isolation) can be exercised without invoking
        # the real Word/PDF conversion path. Production always uses the
        # real, unmodified-frozen-script-driven deterministic pipeline.
        self.pipeline_runner = detp.run_job_pipeline

    # -- setup ------------------------------------------------------------

    def setup(self):
        manifest = build_shared_context.build(self.batch_dir)
        self.evidence_root = ROOT / manifest["shared_ats_evidence_dir"]

        if self.args.mock_semantic:
            self.health_status = self.args.mock_health_status
        else:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import finalize_local
            self.health_status = "healthy" if finalize_local.check_health(self.args.base_url) else "unavailable"

    # -- discovery ----------------------------------------------------------

    def scan_unclaimed(self):
        found = []
        for job_file in sorted(self.input_dir.glob("*.md")):
            job_key = job_key_for(job_file)
            if (self.done_dir / job_file.name).exists():
                continue  # already completed in a prior run; never reprocess
            with self.claimed_lock:
                if job_key in self.claimed:
                    continue
                self.claimed.add(job_key)
            found.append(job_file)
        return found

    # -- one job's full pipeline --------------------------------------------

    def process_job(self, job_file: Path) -> dict:
        job_key = job_key_for(job_file)
        out_dir = ROOT / "output" / job_key
        job_local_dir = self.batch_dir / "jobs" / job_key
        job_local_dir.mkdir(parents=True, exist_ok=True)

        # Fresh-attempt state isolation: `out_dir` is keyed only by job_key,
        # not by batch id, so it is silently reused whenever this job_key is
        # reprocessed by a later, independent batch run.
        # `reset_attempt_local_state` is the ONE centralized initializer
        # (see its docstring / deterministic_pipeline.py's module-level
        # `ATTEMPT_LOCAL_CONTROL_ARTIFACTS` comment) for every artifact this
        # pipeline ever treats as "prior decision state to reuse/count"
        # rather than "this run's own fresh output" -- correction-round
        # bookkeeping AND selectable-project selection. This MUST run before
        # `ensure_project_selection` (a few lines below, and BEFORE semantic
        # call 2, which receives that selection as an input) -- placing the
        # reset any later (e.g. only in `write_strategy_files`) would let a
        # stale `project_selection.json` be reused/passed to call 2 before
        # ever being cleared. Safe to call unconditionally, even for a
        # job_key never seen before (a no-op).
        out_dir.mkdir(parents=True, exist_ok=True)
        detp.reset_attempt_local_state(out_dir)

        discovered_at = now_iso()
        claimed_at = now_iso()
        result = {
            "job": job_key, "discovered_at": discovered_at, "claimed_at": claimed_at,
            "correction_rounds": [], "deterministic_runs": [],
        }

        try:
            # Job SETUP (reading this job's own posting file) is the one step
            # that runs before any status could be recorded, and the only one
            # whose failure is caused purely by the input file itself: an
            # unreadable file, a deleted/renamed file, or bytes that are not
            # valid UTF-8 all raise out of `build_packet`'s `read_text()`.
            # `process_job` otherwise has only a `try/finally`, so such an
            # exception escapes to `Batch.run`'s `future.result()` and takes
            # the WHOLE batch down over one bad input file. Classify it as a
            # terminal, job-local JOB_SETUP_FAILED instead — the file stays in
            # the inbox (never moved to done, exactly like any other failed
            # job), every other job in the batch finishes normally, and the
            # success path, correction-round policy and gate behavior below
            # are untouched.
            try:
                with Timer() as t:
                    packet_path = build_job_packet.build_packet(self.batch_dir, job_file, job_key)
            except (OSError, UnicodeDecodeError) as exc:
                result["status"] = "JOB_SETUP_FAILED"
                result["error"] = f"{type(exc).__name__}: {exc}"
                return result
            result.update(t.as_dict("packet_build"))
            packet = load_json(packet_path)

            # --- semantic call 1: requirements ---
            with self.semantic_semaphore, self.semantic_tracker:
                with Timer() as t:
                    call1 = semantic_worker.run_requirements_call(packet, caller=self.semantic_caller)
            sem_start1, sem_end1 = t.start_iso, t.end_iso

            if not call1.ok:
                result["status"] = "SEMANTIC_CALL_FAILED"
                result["error"] = call1.error
                result["semantic_raw_error"] = call1.raw_text
                return result

            jd_requirements = call1.data
            # Fix 1: deterministic JD concept de-duplication, AFTER semantic
            # call 1 and BEFORE contract validation / selectable-project
            # selection ever see the file (see normalize_jd_concepts.py).
            jd_requirements, concept_merge_notes = normalize_jd_requirements(jd_requirements)
            if concept_merge_notes:
                result["jd_concept_merges"] = concept_merge_notes
            out_dir.mkdir(parents=True, exist_ok=True)
            write_json(out_dir / "jd_requirements.json", jd_requirements)

            # --- deterministic selectable-project binding (no LLM) ---
            selection = detp.ensure_project_selection(out_dir, out_dir / "jd_requirements.json")

            # --- semantic call 2: content ---
            with self.semantic_semaphore, self.semantic_tracker:
                with Timer() as t:
                    call2 = semantic_worker.run_content_call(
                        packet, jd_requirements, selection.get("data") or {}, caller=self.semantic_caller,
                    )
            sem_end2 = t.end_iso

            result["semantic_start"] = sem_start1
            result["semantic_end"] = sem_end2
            result["semantic_model"] = call1.model
            result["semantic_request_count"] = 2
            result["semantic_cost_usd"] = round(call1.cost_usd + call2.cost_usd, 6)
            result["semantic_input_tokens"] = call1.input_tokens + call2.input_tokens
            result["semantic_output_tokens"] = call1.output_tokens + call2.output_tokens
            result["semantic_cache_read_tokens"] = call1.cache_read_tokens + call2.cache_read_tokens
            result["semantic_cache_write_tokens"] = call1.cache_write_tokens + call2.cache_write_tokens
            result["semantic_session_ids"] = [call1.session_id, call2.session_id]

            if not call2.ok:
                result["status"] = "SEMANTIC_CALL_FAILED"
                result["error"] = call2.error
                return result

            strategy = call2.data["strategy_status"]
            if strategy.get("status") != "STRATEGY_READY":
                result["status"] = "STRATEGY_FAILED"
                result["reason"] = strategy.get("reason")
                return result

            resume_payload = call2.data["resume_payload"]
            cover_letter_payload = call2.data["cover_letter_payload"]

            # V8.6.8 Fix: deterministic cover_letter_payload.company backfill,
            # AFTER semantic call 2 and BEFORE write_strategy_files/contract
            # validation ever see the file (see
            # normalize_cover_letter_identity.py -- closes both an empty-
            # company STRATEGY_CONTRACT_FAILED and the wider silently-shipped
            # "your organization"-style placeholder defect).
            cover_letter_payload, backfilled_company = backfill_cover_letter_company(
                cover_letter_payload, packet["job_posting_text"],
            )
            if backfilled_company:
                result["cover_letter_company_backfilled"] = backfilled_company

            bound_project_id = resume_payload["selected_project"]["owner_id"]
            bound_project_title = resume_payload["selected_project"]["title"]

            detp.write_strategy_files(
                out_dir, str(job_file), job_key, jd_requirements,
                resume_payload, cover_letter_payload, bound_project_id,
            )

            # --- deterministic pipeline (+ bounded correction rounds) ---
            correction_round = 0
            pipeline_result = None
            while True:
                with self.deterministic_tracker:
                    with Timer() as t:
                        pipeline_result = self.pipeline_runner(out_dir, self.evidence_root, job_key)
                pipeline_result["_wall"] = t.as_dict("deterministic")
                result["deterministic_runs"].append(pipeline_result)

                status = pipeline_result["status"]
                if status in SUCCESS_STATUSES:
                    # Fix 3/Fix 5: RESUME_READY_WITH_WARNINGS is a terminal
                    # SUCCESS (non-blocking optional ATS warnings only) --
                    # finalize/tracker/done-move must still run for it.
                    break
                if status != "NEEDS_LLM_CORRECTION":
                    result["status"] = status
                    return result

                correction_round += 1
                if correction_round > detp.CORRECTION_ROUND_LIMIT:
                    result["status"] = "CORRECTION_LIMIT_EXCEEDED"
                    return result

                correction_request = load_json(out_dir / "correction_request.json")
                current_payload = load_json(out_dir / "resume_payload.json")
                corr_packet = correction_worker.build_correction_packet(
                    job_key, current_payload, correction_request,
                    {"title": bound_project_title, "owner_id": bound_project_id}, packet["candidate_context"],
                )
                with self.semantic_semaphore, self.semantic_tracker:
                    with Timer() as t:
                        corr = correction_worker.run_correction_call(corr_packet, caller=self.semantic_caller)
                result["correction_rounds"].append({
                    "round": correction_round,
                    **t.as_dict("correction"),
                    "cost_usd": corr.cost_usd,
                    "ok": corr.ok,
                })

                if not corr.ok:
                    result["status"] = "CORRECTION_CALL_FAILED"
                    return result

                # Fix 4: enforce mechanical field-length budgets on the
                # correction output BEFORE it is accepted, in this SAME
                # round -- a purely mechanical overflow must never consume
                # a second semantic correction round.
                budget_checked_payload, trimmed_fields = detp.enforce_correction_length_budget(
                    corr.data["resume_payload"]
                )
                if trimmed_fields:
                    result["correction_rounds"][-1]["mechanical_budget_trims"] = trimmed_fields

                detp.apply_correction(
                    out_dir, correction_round, budget_checked_payload,
                    corr.data["correction_status"], bound_project_id,
                )

            # --- finalize (deterministic, no Claude) ---
            with self.finalization_tracker:
                with Timer() as t:
                    finalize_step = run_step(python_cmd(
                        ROOT / "tools/resume/v86/finalize_local.py",
                        "--job-dir", out_dir, "--base-url", self.args.base_url,
                        "--health-status", self.health_status,
                    ))
            result.update({f"finalization_{k}": v for k, v in t.as_dict("finalization").items()})

            watermark_status = "FAILED"
            if finalize_step.stdout.strip():
                try:
                    finalize_record = json.loads(finalize_step.stdout)
                    watermark_status = {
                        "FINALIZED": "FINALIZED", "PARTIAL": "PARTIAL",
                        "SERVICE_UNAVAILABLE": "NOT STARTED", "FAILED": "FAILED",
                    }.get(finalize_record.get("overall_status"), "FAILED")
                except json.JSONDecodeError:
                    pass

            # --- ADVISORY resume-quality report, re-pinned to the SHIPPED
            # artifact ------------------------------------------------------
            # `run_job_pipeline` already wrote this job's
            # `resume_quality_report.json` against the rendered PDF, before
            # finalization could promote cleaned bytes over `resume.pdf`.
            # Re-running it here -- and ONLY here, after `finalize_local.py`
            # has returned -- is what lets the report name the artifact it
            # actually inspected (`artifact.stage` / `artifact.sha256`)
            # instead of claiming to have validated a file it never read.
            # The action-verb result is carried over from the existing
            # report rather than recomputed, so `action_verb_diversity`
            # still runs exactly once per job. Diagnostic only: it cannot
            # change `result["status"]`, spend a correction round, or make
            # a model call, and its generator swallows internal errors.
            # Guarded for the same reason the deterministic pipeline's own
            # call site is: `process_job` has only a try/finally, so
            # anything escaping here would be re-raised by `Batch.run`'s
            # `future.result()` and would take down the entire batch over
            # a purely advisory diagnostic.
            try:
                quality_report = resume_quality_report.refresh_after_finalization(out_dir, job_key)
                result["quality_report"] = resume_quality_report.console_summary(quality_report, out_dir)
            except Exception as exc:  # noqa: BLE001 -- advisory only, never fatal
                result["quality_report"] = {
                    "path": None,
                    "diagnostic_state": "INCOMPLETE",
                    "error": f"{type(exc).__name__}: {exc}",
                    "advisory_only": True,
                }

            # --- tracker (deterministic, no Claude) ---
            with Timer() as t:
                run_step(python_cmd(
                    ROOT / "tools/resume/v86/tracker_writer.py",
                    "--job-key", job_key,
                    "--company", cover_letter_payload.get("company", ""),
                    "--role", cover_letter_payload.get("role", ""),
                    "--resume-status", "COMPLETE",
                    "--cover-letter-status", "COMPLETE",
                    "--watermark-status", watermark_status,
                    "--final-resume", f"output/{job_key}/resume.pdf",
                    "--final-cover-letter", f"output/{job_key}/cover_letter.pdf",
                    *(["--test-mode"] if self.args.test_mode else []),
                ))
            result.update({f"tracker_{k}": v for k, v in t.as_dict("tracker").items()})

            # --- move to done (atomic rename, same filesystem) ---
            self.done_dir.mkdir(parents=True, exist_ok=True)
            job_file.rename(self.done_dir / job_file.name)

            # Preserve RESUME_READY_WITH_WARNINGS if that's what the final
            # deterministic pass reported (Fix 3/Fix 5) instead of masking
            # it back to plain RESUME_READY.
            result["status"] = pipeline_result.get("status", "RESUME_READY")
            if pipeline_result.get("ats_warnings"):
                result["ats_warnings"] = pipeline_result["ats_warnings"]
            return result

        finally:
            result["completed_at"] = now_iso()
            write_json(job_local_dir / "local_pipeline_result.json", result)
            with self.results_lock:
                self.job_results.append(result)

    # -- main scan/dispatch loop --------------------------------------------

    def run(self) -> dict:
        batch_start = now_iso()
        t0 = time.monotonic()
        self.setup()

        executor = ThreadPoolExecutor(max_workers=max(self.args.max_workers * 4, 16))
        futures = []

        while True:
            new_jobs = self.scan_unclaimed()
            for job_file in new_jobs:
                futures.append(executor.submit(self.process_job, job_file))

            active = [f for f in futures if not f.done()]

            if not new_jobs and not active:
                break
            time.sleep(0.3)

        for f in futures:
            f.result()  # propagate exceptions, if any slipped through
        executor.shutdown(wait=True)

        batch_end = now_iso()
        wall_ms = round((time.monotonic() - t0) * 1000, 1)

        succeeded = sum(1 for r in self.job_results if r.get("status") in SUCCESS_STATUSES)
        failed = sum(1 for r in self.job_results if r.get("status") not in SUCCESS_STATUSES)
        corrected = sum(1 for r in self.job_results if r.get("correction_rounds"))

        metrics = {
            "batch_id": self.batch_id,
            "batch_start": batch_start,
            "batch_end": batch_end,
            "wall_duration_ms": wall_ms,
            "jobs_discovered": len(self.job_results),
            "jobs_succeeded": succeeded,
            "jobs_failed": failed,
            "jobs_corrected": corrected,
            # Advisory only -- a count of jobs whose consolidated
            # resume-quality report came back REVIEW/INCOMPLETE. It is a
            # pointer to each job's own report, never a pass/fail signal
            # and never part of jobs_succeeded/jobs_failed.
            "jobs_quality_review": sum(
                1 for r in self.job_results
                if (r.get("quality_report") or {}).get("diagnostic_state") in ("REVIEW", "INCOMPLETE")
            ),
            "max_workers": self.args.max_workers,
            "peak_semantic_concurrency": self.semantic_tracker.peak,
            "peak_deterministic_concurrency": self.deterministic_tracker.peak,
            "peak_finalization_concurrency": self.finalization_tracker.peak,
            "total_claude_requests": sum(
                r.get("semantic_request_count", 0) + len(r.get("correction_rounds", []))
                for r in self.job_results
            ),
            "total_claude_cost_usd": round(sum(
                r.get("semantic_cost_usd", 0.0) + sum(c.get("cost_usd", 0.0) for c in r.get("correction_rounds", []))
                for r in self.job_results
            ), 6),
            "total_input_tokens": sum(r.get("semantic_input_tokens", 0) for r in self.job_results),
            "total_output_tokens": sum(r.get("semantic_output_tokens", 0) for r in self.job_results),
            "total_cache_read_tokens": sum(r.get("semantic_cache_read_tokens", 0) for r in self.job_results),
            "total_cache_write_tokens": sum(r.get("semantic_cache_write_tokens", 0) for r in self.job_results),
            "word_lock_wait_note": (
                "Not independently measurable without modifying the frozen "
                "Word-conversion lock (tools/resume/render_resume.py / "
                "tools/resume/word_to_pdf.py); any wait is already included "
                "inside each job's deterministic render step duration."
            ),
            "jobs": self.job_results,
        }

        write_json(self.batch_dir / "batch_metrics.json", metrics)
        _write_metrics_txt(self.batch_dir / "batch_metrics.txt", metrics)
        return metrics


def _write_metrics_txt(path: Path, m: dict) -> None:
    lines = [
        "=" * 64, f"V8.6 BATCH METRICS -- {m['batch_id']}", "=" * 64,
        f"batch_start: {m['batch_start']}", f"batch_end: {m['batch_end']}",
        f"wall_duration_ms: {m['wall_duration_ms']}",
        f"jobs_discovered: {m['jobs_discovered']}",
        f"jobs_succeeded: {m['jobs_succeeded']}",
        f"jobs_failed: {m['jobs_failed']}",
        f"jobs_corrected: {m['jobs_corrected']}",
        f"jobs_quality_review (advisory): {m.get('jobs_quality_review', 0)}",
        f"max_workers: {m['max_workers']}",
        f"peak_semantic_concurrency: {m['peak_semantic_concurrency']}",
        f"peak_deterministic_concurrency: {m['peak_deterministic_concurrency']}",
        f"peak_finalization_concurrency: {m['peak_finalization_concurrency']}",
        f"total_claude_requests: {m['total_claude_requests']}",
        f"total_claude_cost_usd: {m['total_claude_cost_usd']}",
        "",
        "per-job status:",
    ]
    for r in m["jobs"]:
        # The advisory diagnostic state (and nothing from inside the
        # report) rides along here: the detailed, candidate-specific
        # findings stay in the job-local, git-ignored report file.
        quality = r.get("quality_report") or {}
        suffix = ""
        if quality.get("diagnostic_state"):
            suffix = (
                f"  [quality: {quality['diagnostic_state']}"
                f" @ {quality.get('artifact_stage')}]"
            )
        lines.append(f"  {r['job']}: {r.get('status')}{suffix}")
    path.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", default="jobs/batch_inbox")
    parser.add_argument("--done-dir", default="jobs/batch_done")
    parser.add_argument("--max-workers", type=int, default=10)
    parser.add_argument("--test-mode", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--mock-semantic", action="store_true", help="Offline-test only; never used for real runs.")
    parser.add_argument("--mock-health-status", default="healthy", choices=["healthy", "unavailable"])
    args = parser.parse_args()
    args.mock_responses = {}  # populated programmatically by tests, not via CLI

    if args.batch_id is None:
        from common import new_batch_id
        args.batch_id = new_batch_id()

    batch = Batch(args)
    metrics = batch.run()
    print(json.dumps({k: v for k, v in metrics.items() if k != "jobs"}, indent=2))


if __name__ == "__main__":
    main()
