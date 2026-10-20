#!/usr/bin/env python3
"""
V8.6.8 -- one reusable offline benchmark-report utility.

ZERO model/API calls. Reads ONLY artifacts already on disk (a batch's
`.work/v86/<batch-id>/batch_metrics.json` plus each job's `output/<job_key>/`
directory and `.work/v86/<batch-id>/jobs/<job_key>/local_pipeline_result.json`)
and prints one structured report per batch. This replaces several ad-hoc
shell snippets used during the V8.6.1-V8.6.7 passes, which had two
independent, now-fixed bugs:

1. A "QUALITY SUMMARY" that always printed `READY: 0` / `READY_WITH_WARNINGS:
   0` even for a batch with real successes, because it looked for
   nonexistent per-job status files (`final_status.json`/`pipeline_status.
   json`/`status.json` -- none of these are ever written by
   `run_batch.py`/`deterministic_pipeline.py`). This tool derives every
   per-job status directly from `batch_metrics.json`'s own `jobs[].status`
   field -- the actual, real source of truth `run_batch.Batch.run()` writes
   (see `run_batch.py`'s `metrics["jobs"]` / `_write_metrics_txt`) -- and,
   defensively, falls back to inspecting each job's own on-disk artifact
   chain (v8_strategy_status.json / v8_contract_validation.json / ... /
   resume_qa.json) ONLY when no `batch_metrics.json` is available at all
   (e.g. a batch directory copied without it).
2. A release-verification snippet that printed `batch_runner: None` /
   `max_workers: None` by reading `config/active_resume_pipeline.json`'s
   top level directly. Those keys are correctly nested under
   `production_entrypoints.batch_runner` / `production_defaults.max_workers`
   (confirmed present and correct in the live config during the V8.6.8
   audit -- this was a reporting-script bug, not a production config gap;
   `read_active_config_summary()` below is the deterministic, tested reader
   any future release/benchmark script should reuse instead of re-deriving
   its own, possibly-wrong key path).

Usage (zsh-safe -- see "Shell usage" below):
    python3 tools/resume/v86/benchmark_report.py --batch-id batch-synthetic-regression
    python3 tools/resume/v86/benchmark_report.py --batch-dir .work/v86/batch-synthetic-regression
    python3 tools/resume/v86/benchmark_report.py --batch-id batch-synthetic-regression --json

Shell usage (zsh-safe):
    This script itself never relies on Bash-only `PIPESTATUS`; its own exit
    code is `0` on a successfully generated report and nonzero only if the
    requested batch cannot be found at all. Callers piping this script's
    output (e.g. `python3 tools/resume/v86/benchmark_report.py ... | tee
    out.txt`) should check `$?` (POSIX, works in both bash and zsh) or, in
    zsh specifically, the `$pipestatus` array (zsh's own equivalent of
    bash's `PIPESTATUS`, NOT the same array/name) -- never bash's
    `PIPESTATUS` variable, which does not exist in zsh and silently expands
    to an empty string there (the root cause of the "printed a blank exit
    code" symptom this pass's audit flagged).

Per job, this tool also reports a `correction_state` breakdown
(`requested`/`attempted`/`succeeded`/`exhausted` -- see `build_report`'s own
comment for the exact distinction), so a `CORRECTION_LIMIT_EXCEEDED` status
with zero attempted correction calls in a given run is flagged in-report as
`correction_invariant_violation`, never silently folded into an aggregate
`jobs_corrected` count.

`company_check` semantics: a generated cover letter can carry a non-empty,
non-placeholder, but still WRONG `company` value (a paraphrased team name
rather than the posting's own literal employer name). Merely asking "does
this look like a known placeholder?" (an `is_recognized_placeholder_or_empty`
check) would silently PASS a wrong-but-nonempty value, since it is not on
the placeholder list. `company_check` therefore calls
`normalize_cover_letter_identity.company_check_status` -- the SAME parser/
helper `backfill_cover_letter_company` itself uses -- to compare the FINAL
on-disk `cover_letter_payload.company` against the posting's own parsed
literal company (read from that job's batch-scoped, invariant
`jobs/<job_key>/job_packet.json` -> `job_posting_text`, never the
job-file-on-disk path recorded in `v8_strategy_status.json`, which goes
stale once `run_batch.py` moves the source posting from its input dir to
its done dir after a batch completes): `"PASS"` only on an exact match,
`"FAIL"` on any other non-empty mismatch, and an explicit
`"NO_LITERAL_SOURCE"` (never a fabricated PASS/FAIL) when no literal
company can be reliably parsed for that job at all. One source of truth;
no second, independently-derived comparison anywhere in this pipeline.

`action_verb_repeated_over_limit` is purely diagnostic: it re-derives the
same STYLE-only finding `deterministic_pipeline.run_job_pipeline` already
computes (see `action_verb_diversity.py`) by re-reading the job's own
on-disk `resume_draft.md` -- never a second, independently-scored gate.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from action_verb_diversity import analyze_action_verbs_from_file  # noqa: E402
from common import ROOT, load_json  # noqa: E402
from normalize_cover_letter_identity import company_check_status  # noqa: E402

SUCCESS_STATUSES = {"RESUME_READY", "RESUME_READY_WITH_WARNINGS"}

EXPECTED_ARTIFACTS = (
    "resume.pdf",
    "resume.docx",
    "cover_letter.pdf",
    "cover_letter.docx",
)

# Literal, narrow markers of PARENT-conversation/session leakage (see the
# V8.6.2 isolation patch's own root-cause writeup, README.md). Never a fuzzy
# heuristic -- exact substring search for known contamination signatures.
CONTAMINATION_MARKERS = (
    "CLAUDE_CODE_", "the human turn", "shell/bash test-harness",
    "claude_code_session_id", "CLAUDECODE",
)


# ---------------------------------------------------------------------------
# Config reader (Phase 9 -- deterministic, correct nesting)
# ---------------------------------------------------------------------------

def read_active_config_summary(root: Path = ROOT) -> dict:
    """The one deterministic reader for `config/active_resume_pipeline.json`
    that every release/benchmark script should reuse, rather than
    re-deriving its own (possibly wrong, possibly flat/top-level) key path.
    Returns `None` for any field genuinely absent from the schema, never a
    guess."""
    config_path = root / "config/active_resume_pipeline.json"
    if not config_path.exists():
        return {"active_version": None, "status": None, "batch_runner": None, "max_workers": None}
    config = load_json(config_path)
    entrypoints = config.get("production_entrypoints", {}) or {}
    defaults = config.get("production_defaults", {}) or {}
    return {
        "active_version": config.get("active_version"),
        "status": config.get("status"),
        "batch_runner": entrypoints.get("batch_runner"),
        "max_workers": defaults.get("max_workers"),
    }


# ---------------------------------------------------------------------------
# Per-job artifact inspection
# ---------------------------------------------------------------------------

def _status_of(path: Path, key: str = "status") -> Optional[str]:
    if not path.exists():
        return None
    try:
        return load_json(path).get(key)
    except Exception:
        return "UNREADABLE"


def job_artifact_summary(job_key: str, root: Path = ROOT, posting_text: Optional[str] = None) -> dict:
    """Every field this reads comes directly from `output/<job_key>/`'s own
    on-disk artifacts -- the real production source of truth -- never a
    fabricated/nonexistent `status.json`/`pipeline_status.json`/
    `final_status.json`.

    `posting_text` (V8.6.8 round 4): the job's own raw posting text, if the
    caller (`build_report`) could resolve it -- see
    `_posting_text_for_job`. Passed through unchanged to
    `normalize_cover_letter_identity.company_check_status`; never re-derived
    here, so there is exactly one place that resolves posting text for a
    historical job."""
    job_dir = root / "output" / job_key
    summary = {
        "job": job_key,
        "strategy_status": _status_of(job_dir / "v8_strategy_status.json"),
        "contract_status": _status_of(job_dir / "v8_contract_validation.json"),
        "project_selection_status": _status_of(job_dir / "v8_project_selection_validation.json"),
        "payload_preflight_status": _status_of(job_dir / "payload_preflight.json"),
        "resume_qa_status": _status_of(job_dir / "resume_qa.json"),
        "ats_gate_pass": None,
        "ats_supported_but_missing_required": [],
        "ats_optional_supported_but_missing": [],
        "selected_project": None,
        "correction_rounds_on_disk": 0,
        "correction_reasons_by_round": {},
        "artifacts_present": {},
        "zero_byte_artifacts": [],
        "cover_letter_company": None,
        "company_check": None,
        "action_verb_repeated_over_limit": {},
    }

    # V8.6.8 (round 5, FINAL): purely diagnostic -- re-derives the same
    # STYLE-only finding `deterministic_pipeline.run_job_pipeline` already
    # computes and stores in `result["action_verb_diversity"]` for THIS run,
    # by re-reading the job's own on-disk `resume_draft.md` (never a second,
    # independently-scored gate; never affects ats_gate_pass/company_check/
    # any other field above).
    resume_draft_path = job_dir / "resume_draft.md"
    if resume_draft_path.exists():
        verb_report = analyze_action_verbs_from_file(resume_draft_path)
        summary["action_verb_repeated_over_limit"] = verb_report["repeated_over_limit"]

    # V8.6.8 round 4 (FINAL): `company_check` now reuses
    # `normalize_cover_letter_identity.company_check_status` -- the SAME
    # parser/helper the production backfill itself uses -- to compare the
    # FINAL on-disk `cover_letter_payload.company` against the posting's own
    # parsed literal company, never merely asking "does this look like a
    # known placeholder?" (that round-2 check would have silently PASSED
    # benchmark-10's real defect: a non-empty, non-placeholder, but WRONG
    # value). "PASS" means an exact match; "FAIL" means a literal company IS
    # parseable and the final value does not match it; "NO_LITERAL_SOURCE"
    # means no literal company could be reliably parsed for this job at all
    # (reported explicitly, never folded into a fabricated PASS/FAIL).
    cover_path = job_dir / "cover_letter_payload.json"
    if cover_path.exists():
        company = load_json(cover_path).get("company")
        summary["cover_letter_company"] = company
        summary["company_check"] = company_check_status(company, posting_text or "")

    ats_path = job_dir / "ats_preflight.json"
    if ats_path.exists():
        ats = load_json(ats_path)
        summary["ats_gate_pass"] = ats.get("gate_pass")
        summary["ats_supported_but_missing_required"] = ats.get("supported_but_missing_required", [])
        summary["ats_optional_supported_but_missing"] = ats.get("optional_supported_but_missing", [])

    strategy_path = job_dir / "v8_strategy_status.json"
    if strategy_path.exists():
        summary["selected_project"] = load_json(strategy_path).get("selected_project_bound")

    # V8.6.8: per-round-numbered correction snapshots (see
    # deterministic_pipeline.write_correction_request). Older/historical
    # batches predating this fix only ever have the single, overwritten
    # correction_request.json -- reported honestly as round "last".
    round_files = sorted(job_dir.glob("correction_request.round*.json"))
    if round_files:
        summary["correction_rounds_on_disk"] = len(round_files)
        for p in round_files:
            data = load_json(p)
            summary["correction_reasons_by_round"][str(data.get("correction_round"))] = data.get("reasons", [])
    elif (job_dir / "correction_request.json").exists():
        data = load_json(job_dir / "correction_request.json")
        summary["correction_rounds_on_disk"] = 1
        summary["correction_reasons_by_round"]["last"] = data.get("reasons", [])

    for name in EXPECTED_ARTIFACTS:
        p = job_dir / name
        exists = p.exists()
        summary["artifacts_present"][name] = exists
        if exists and p.stat().st_size == 0:
            summary["zero_byte_artifacts"].append(name)

    return summary


def _posting_text_for_job(batch_path: Path, job_key: str) -> Optional[str]:
    """The one place `build_report` resolves a historical job's raw posting
    text, for `company_check_status`. Reads the batch-local, invariant-per-
    job-key `jobs/<job_key>/job_packet.json` -> `job_posting_text` -- the
    same source `historical_replay_v868.py` already established as safe
    (unlike `v8_strategy_status.json`'s `job_file` path, which points at the
    source posting's ORIGINAL location and goes stale once `run_batch.py`
    renames that file from its input dir to its done dir after the batch
    completes). Returns None (never raises, never fabricates) if no
    `job_packet.json` exists for this job under this batch."""
    packet_path = batch_path / "jobs" / job_key / "job_packet.json"
    if not packet_path.exists():
        return None
    try:
        return load_json(packet_path).get("job_posting_text") or None
    except Exception:
        return None


def contamination_scan(job_keys: list, root: Path = ROOT) -> dict:
    """Two narrow, deterministic checks, never a fuzzy heuristic:
    (1) no job's rendered resume/cover-letter text contains a known parent-
        session leakage marker (see CONTAMINATION_MARKERS);
    (2) no two jobs in the same batch share a `semantic_session_ids` value
        (the V8.6.2 isolation guarantee -- a fresh UUID per call)."""
    hits = []
    for job_key in job_keys:
        job_dir = root / "output" / job_key
        for name in ("resume_draft.md",):
            p = job_dir / name
            if not p.exists():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for marker in CONTAMINATION_MARKERS:
                if marker in text:
                    hits.append({"job": job_key, "file": name, "marker": marker})
    return {"status": "PASS" if not hits else "FAIL", "hits": hits}


# ---------------------------------------------------------------------------
# Batch-level report
# ---------------------------------------------------------------------------

def resolve_batch_dir(batch_id: Optional[str], batch_dir: Optional[str], root: Path = ROOT) -> Path:
    if batch_dir:
        path = Path(batch_dir)
        return path if path.is_absolute() else root / path
    if batch_id:
        return root / ".work/v86" / batch_id
    raise SystemExit("ERROR: provide --batch-id or --batch-dir")


def build_report(batch_path: Path, root: Path = ROOT) -> dict:
    metrics_path = batch_path / "batch_metrics.json"
    if not metrics_path.exists():
        raise SystemExit(f"ERROR: no batch_metrics.json under {batch_path} (real production runs always write one)")
    metrics = load_json(metrics_path)

    jobs_report = []
    ready = ready_with_warnings = other_final = 0
    for job_row in metrics.get("jobs", []):
        job_key = job_row["job"]
        posting_text = _posting_text_for_job(batch_path, job_key)
        artifact_summary = job_artifact_summary(job_key, root, posting_text=posting_text)
        final_status = job_row.get("status")
        if final_status == "RESUME_READY":
            ready += 1
        elif final_status == "RESUME_READY_WITH_WARNINGS":
            ready_with_warnings += 1
        else:
            other_final += 1

        # V8.6.8 (round 2, Phase 10): distinguish correction REQUESTED (a
        # deterministic_runs entry asked for one, i.e. NEEDS_LLM_CORRECTION)
        # from correction ATTEMPTED (a correction_worker call actually ran,
        # i.e. `correction_rounds` has an entry) from correction SUCCEEDED
        # (that attempt's own `ok` flag) from EXHAUSTED (final status is
        # CORRECTION_LIMIT_EXCEEDED) -- never conflate these the way a bare
        # `jobs_corrected` count alone would.
        deterministic_runs = job_row.get("deterministic_runs", [])
        correction_rounds_batch = job_row.get("correction_rounds", [])
        requested_rounds = sum(1 for d in deterministic_runs if d.get("status") == "NEEDS_LLM_CORRECTION")
        attempted_rounds = len(correction_rounds_batch)
        succeeded_rounds = sum(1 for c in correction_rounds_batch if c.get("ok"))
        exhausted = final_status == "CORRECTION_LIMIT_EXCEEDED"
        # Phase 4 invariant: zero attempted correction calls this run can
        # never legitimately coexist with CORRECTION_LIMIT_EXCEEDED unless a
        # deterministic-only limit reports a DIFFERENT status/reason (this
        # pipeline has none such today -- see README.md/tests). Flagged here
        # as a defensive diagnostic, not silently swallowed, so a regression
        # of the V8.6.8-round-2 stale-correction-state fix is caught by this
        # report even before a test suite run.
        invariant_violation = exhausted and attempted_rounds == 0

        jobs_report.append({
            "job": job_key,
            "final_status": final_status,
            "failure_reason": job_row.get("reason") or job_row.get("contract_failures") or job_row.get("error"),
            "correction_rounds_reported_by_batch": attempted_rounds,
            "correction_requested_rounds": requested_rounds,
            "correction_attempted_rounds": attempted_rounds,
            "correction_succeeded_rounds": succeeded_rounds,
            "correction_exhausted": exhausted,
            "correction_invariant_violation": invariant_violation,
            "ats_warnings": job_row.get("ats_warnings", []),
            "ats_unplaceable_required": job_row.get("ats_unplaceable_required", []),
            "mechanical_preflight_trim": job_row.get("mechanical_preflight_trim", []),
            "cover_letter_company_backfilled": job_row.get("cover_letter_company_backfilled"),
            **artifact_summary,
        })

    contamination = contamination_scan([r["job"] for r in metrics.get("jobs", [])], root)
    config_summary = read_active_config_summary(root)

    return {
        "batch_id": metrics.get("batch_id"),
        "jobs_discovered": metrics.get("jobs_discovered"),
        "jobs_succeeded": metrics.get("jobs_succeeded"),
        "jobs_failed": metrics.get("jobs_failed"),
        "jobs_corrected": metrics.get("jobs_corrected"),
        "quality_summary": {
            "READY": ready,
            "READY_WITH_WARNINGS": ready_with_warnings,
            "OTHER_FINAL_STATUS": other_final,
        },
        "total_claude_requests": metrics.get("total_claude_requests"),
        "total_claude_cost_usd": metrics.get("total_claude_cost_usd"),
        "total_output_tokens": metrics.get("total_output_tokens"),
        "total_cache_read_tokens": metrics.get("total_cache_read_tokens"),
        "total_cache_write_tokens": metrics.get("total_cache_write_tokens"),
        "wall_duration_ms": metrics.get("wall_duration_ms"),
        "peak_semantic_concurrency": metrics.get("peak_semantic_concurrency"),
        "peak_deterministic_concurrency": metrics.get("peak_deterministic_concurrency"),
        "peak_finalization_concurrency": metrics.get("peak_finalization_concurrency"),
        "contamination": contamination,
        "active_config": config_summary,
        "jobs": jobs_report,
    }


def _format_text(report: dict) -> str:
    lines = [
        "=" * 78,
        f"V8.6 BENCHMARK REPORT -- {report['batch_id']}",
        "=" * 78,
        f"jobs_discovered: {report['jobs_discovered']}  jobs_succeeded: {report['jobs_succeeded']}  "
        f"jobs_failed: {report['jobs_failed']}  jobs_corrected: {report['jobs_corrected']}",
        f"quality_summary: READY={report['quality_summary']['READY']}  "
        f"READY_WITH_WARNINGS={report['quality_summary']['READY_WITH_WARNINGS']}  "
        f"OTHER={report['quality_summary']['OTHER_FINAL_STATUS']}",
        f"requests: {report['total_claude_requests']}  cost_usd: {report['total_claude_cost_usd']}  "
        f"output_tokens: {report['total_output_tokens']}  wall_ms: {report['wall_duration_ms']}",
        f"peak concurrency: semantic={report['peak_semantic_concurrency']}  "
        f"deterministic={report['peak_deterministic_concurrency']}  "
        f"finalization={report['peak_finalization_concurrency']}",
        f"contamination: {report['contamination']['status']}"
        + (f"  hits={report['contamination']['hits']}" if report["contamination"]["hits"] else ""),
        f"active_config: version={report['active_config']['active_version']}  "
        f"status={report['active_config']['status']}  "
        f"batch_runner={report['active_config']['batch_runner']}  "
        f"max_workers={report['active_config']['max_workers']}",
        "",
        "per-job:",
    ]
    for j in report["jobs"]:
        lines.append(f"  {j['job']}: {j['final_status']}")
        lines.append(
            f"    strategy={j['strategy_status']} contract={j['contract_status']} "
            f"project_selection={j['project_selection_status']} "
            f"payload_preflight={j['payload_preflight_status']} "
            f"ats_gate_pass={j['ats_gate_pass']} resume_qa={j['resume_qa_status']}"
        )
        if j["ats_supported_but_missing_required"]:
            lines.append(f"    ATS MISSING REQUIRED TERMS: {j['ats_supported_but_missing_required']}")
        if j["resume_qa_status"] == "PASS_WITH_DENSITY_WARNING":
            lines.append("    DENSITY WARNING: usable_bottom_whitespace_in below the 0.25in target (see resume_qa.json)")
        lines.append(f"    COMPANY CHECK: {j['company_check']}  (company={j['cover_letter_company']!r})")
        if j["failure_reason"]:
            lines.append(f"    failure_reason: {j['failure_reason']}")
        if j["correction_rounds_reported_by_batch"]:
            lines.append(
                f"    correction_rounds: {j['correction_rounds_reported_by_batch']} "
                f"reasons_by_round={j['correction_reasons_by_round']}"
            )
        if j["correction_requested_rounds"] or j["correction_attempted_rounds"] or j["correction_exhausted"]:
            lines.append(
                f"    correction_state: requested={j['correction_requested_rounds']} "
                f"attempted={j['correction_attempted_rounds']} succeeded={j['correction_succeeded_rounds']} "
                f"exhausted={j['correction_exhausted']}"
            )
        if j["correction_invariant_violation"]:
            lines.append(
                "    *** INVARIANT VIOLATION: CORRECTION_LIMIT_EXCEEDED with 0 correction "
                "attempts this run (stale correction-state bug?) ***"
            )
        if j["action_verb_repeated_over_limit"]:
            lines.append(f"    ACTION VERB REPETITION (STYLE, non-blocking): {j['action_verb_repeated_over_limit']}")
        if j["cover_letter_company_backfilled"]:
            lines.append(f"    cover_letter_company_backfilled: {j['cover_letter_company_backfilled']}")
        if j["zero_byte_artifacts"]:
            lines.append(f"    ZERO-BYTE ARTIFACTS: {j['zero_byte_artifacts']}")
        missing_artifacts = [k for k, v in j["artifacts_present"].items() if not v]
        if j["final_status"] in SUCCESS_STATUSES and missing_artifacts:
            lines.append(f"    MISSING EXPECTED ARTIFACTS: {missing_artifacts}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--batch-dir", default=None)
    parser.add_argument("--json", action="store_true", help="print the full JSON report instead of the text summary")
    args = parser.parse_args()

    batch_path = resolve_batch_dir(args.batch_id, args.batch_dir)
    if not batch_path.exists():
        raise SystemExit(f"ERROR: batch directory not found: {batch_path}")

    report = build_report(batch_path)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(_format_text(report))


if __name__ == "__main__":
    main()
