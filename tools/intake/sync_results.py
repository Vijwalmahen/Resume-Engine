#!/usr/bin/env python3
"""
Post-batch reconciliation: what the pipeline did, written back to `Intake`.

    python3 tools/intake/sync_results.py --batch-id batch-<id>
    python3 tools/intake/sync_results.py --batch-metrics .work/v86/<id>/batch_metrics.json

This is a SEPARATE step from intake on purpose. Intake knows what it queued;
only the batch knows what became of it. This script reads the batch's own
metrics (`.work/v86/<batch-id>/batch_metrics.json`) plus each job's local
advisory resume-quality report, and writes the result onto the matching
`Intake` row: pipeline status, batch id, the final artifact paths that
actually exist, the advisory quality state and artifact stage, and a short
error code/detail for anything that did not finish.

Boundaries
----------
  - It writes ONLY the sync-owned Intake columns
    (`intake_tracker.SYNC_OWNED_HEADERS`), enforced by the writer itself, so
    it cannot touch identity or intake-outcome columns even by mistake.
  - It never touches the `Applications` sheet, the user's
    `Application Status`/`Applied Date`, or any `Notes`.
  - It never copies job-description text, resume bullet text, or a quality
    report's findings anywhere. The report's PATH and its one-word
    `diagnostic_state` are the only parts that leave the job's own directory;
    the findings stay in the job-local, git-ignored file.
  - A job key with no Intake row is reported as unmatched, never invented as
    a half-populated row.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools" / "resume" / "v86"))

from common import now_iso  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402
import resume_quality_report  # noqa: E402
import intake_tracker  # noqa: E402

SUCCESS_STATUSES = detp.SUCCESS_STATUSES

# The advisory vocabulary this sync passes through unchanged; anything else a
# report produces is written verbatim too, but these are the expected values.
QUALITY_STATES = ("PASS", "REVIEW", "INCOMPLETE")

_WHITESPACE_RE = re.compile(r"\s+")
ERROR_DETAIL_MAX_CHARS = 200


def batch_metrics_path(batch_id: str, work_root: Optional[Path] = None) -> Path:
    work_root = Path(work_root) if work_root else (ROOT / ".work" / "v86")
    return work_root / batch_id / "batch_metrics.json"


def _short(text) -> str:
    """One short, single-line detail string. Deliberately truncated: this cell
    explains WHY a job stopped, it is not a log sink."""
    if not text:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", str(text)).strip()
    if len(collapsed) > ERROR_DETAIL_MAX_CHARS:
        collapsed = collapsed[:ERROR_DETAIL_MAX_CHARS - 3] + "..."
    return collapsed


def _relative(path: Path) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)


def quality_fields(job_result: dict, job_dir: Path) -> dict:
    """Advisory quality state for one job: prefer the batch metrics' own
    console summary, and fall back to the job-local report file. Only the
    state, the artifact stage and the report path are read — never a finding."""
    summary = job_result.get("quality_report") or {}
    state = summary.get("diagnostic_state")
    stage = summary.get("artifact_stage")
    path = summary.get("path")

    report_file = resume_quality_report.report_path(job_dir)
    if report_file.exists():
        if not path:
            path = _relative(report_file)
        if not state or not stage:
            try:
                report = json.loads(report_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                report = {}
            state = state or report.get("diagnostic_state")
            stage = stage or (report.get("artifact") or {}).get("stage")

    return {
        "Quality Status": state or "",
        "Quality Artifact Stage": stage or "",
        "Quality Report": path or "",
    }


def artifact_fields(job_dir: Path) -> dict:
    """Final artifact paths — recorded only when the file actually exists, so
    the tracker never points at something that was never produced."""
    fields = {}
    resume = job_dir / "resume.pdf"
    cover = job_dir / "cover_letter.pdf"
    if resume.exists():
        fields["Final Resume"] = _relative(resume)
    if cover.exists():
        fields["Final Cover Letter"] = _relative(cover)
    return fields


def row_for_job(job_result: dict, batch_id: str, output_dir: Path, timestamp: str) -> dict:
    job_key = str(job_result.get("job") or "").strip()
    status = str(job_result.get("status") or "UNKNOWN")
    job_dir = Path(output_dir) / job_key

    row = {
        "Job Key": job_key,
        "Pipeline Status": status,
        "Batch ID": batch_id,
        "Last Sync At": timestamp,
    }
    row.update(artifact_fields(job_dir))
    row.update(quality_fields(job_result, job_dir))

    if status in SUCCESS_STATUSES:
        row["Error Code"] = ""
        row["Error Detail"] = ""
    else:
        row["Error Code"] = status
        row["Error Detail"] = _short(job_result.get("error") or job_result.get("reason"))
    return row


def sync(metrics_path, tracker_path, output_dir=None) -> dict:
    """Reconcile one batch's metrics into the Intake worksheet."""
    # Fail loudly on a workbook that is not the tracker, before reading a
    # single metric — a silent "0 rows synced" would look like success.
    intake_tracker.assert_compatible(tracker_path)

    metrics = json.loads(Path(metrics_path).read_text(encoding="utf-8"))
    batch_id = str(metrics.get("batch_id") or "")
    output_dir = Path(output_dir) if output_dir else (ROOT / "output")
    timestamp = now_iso()

    known = {(r.get("Job Key") or "").strip() for r in intake_tracker.read_intake_rows(tracker_path)}

    rows, unmatched, synced = [], [], []
    for job_result in metrics.get("jobs") or []:
        job_key = str(job_result.get("job") or "").strip()
        if not job_key:
            continue
        if job_key not in known:
            unmatched.append(job_key)
            continue
        rows.append(row_for_job(job_result, batch_id, output_dir, timestamp))
        synced.append({"job_key": job_key, "pipeline_status": job_result.get("status")})

    result = {
        "batch_id": batch_id,
        "jobs_in_metrics": len(metrics.get("jobs") or []),
        "rows_synced": len(rows),
        "unmatched_job_keys": unmatched,
        "synced": synced,
    }
    if rows:
        result["tracker"] = intake_tracker.upsert_rows(
            tracker_path, rows, allowed_headers=intake_tracker.SYNC_OWNED_HEADERS,
        )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile a V8.6 batch's results into the Intake worksheet.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--batch-id")
    group.add_argument("--batch-metrics")
    parser.add_argument("--tracker", default=str(ROOT / "tracking" / "job_application_tracker.xlsx"))
    parser.add_argument("--output-dir", default=str(ROOT / "output"))
    args = parser.parse_args(argv)

    metrics_path = Path(args.batch_metrics) if args.batch_metrics else batch_metrics_path(args.batch_id)
    if not metrics_path.exists():
        print(f"batch metrics not found: {metrics_path}", file=sys.stderr)
        return 2

    try:
        result = sync(metrics_path, Path(args.tracker), Path(args.output_dir))
    except intake_tracker.IntakeSchemaError as exc:
        print(f"IntakeSchemaError: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
