#!/usr/bin/env python3
"""
V8.6 -- deterministic (no-Claude) tracker writer.

Writes ONLY to tracking/job_application_tracker.xlsx (or, with
--test-mode, tracking/job_application_tracker_TEST.xlsx). NEVER touches
tracking/application_tracker.xlsx (the V8.5/production tracker).

Column layout matches config/application_tracker_schema.md's "Applications"
sheet so the workbook stays consistent with the existing tracker contract,
but V8.6 only ever populates/updates the fields the batch pipeline actually
knows and owns:

  Job Key, Company, Role, Resume Status, Cover Letter Status,
  Watermark Status, Final Resume, Final Cover Letter

User-owned fields (Application Status, Applied Date, Notes) are never
overwritten if already present, and Application Status is NEVER
automatically set to APPLIED.

Writes are serialized with a dedicated small file lock (NOT the Word-
conversion lock, and not a new giant pipeline lock -- just this one xlsx).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, file_lock  # noqa: E402

PROD_TRACKER = ROOT / "tracking" / "application_tracker.xlsx"  # NEVER written by V8.6
TEST_TRACKER = ROOT / "tracking" / "job_application_tracker_TEST.xlsx"
LIVE_TRACKER = ROOT / "tracking" / "job_application_tracker.xlsx"

HEADERS = [
    "Job Key", "Company", "Role", "Location", "Workplace Type", "Employment Type",
    "Date Posted", "Date Scraped", "Job Link", "Required Skills", "Preferred Skills",
    "Experience Requirement", "Education Requirement", "Key Requirements", "Salary",
    "Resume Status", "Cover Letter Status", "Watermark Status",
    "Final Resume", "Final Cover Letter", "Application Status", "Applied Date", "Notes",
]

PIPELINE_OWNED = {
    "Company", "Role", "Resume Status", "Cover Letter Status",
    "Watermark Status", "Final Resume", "Final Cover Letter",
}

USER_OWNED = {"Application Status", "Applied Date", "Notes"}


def _tracker_path(test_mode: bool) -> Path:
    return TEST_TRACKER if test_mode else LIVE_TRACKER


def _open_or_create(path: Path):
    from openpyxl import Workbook, load_workbook

    if path.exists():
        wb = load_workbook(path)
        if "Applications" not in wb.sheetnames:
            ws = wb.create_sheet("Applications")
            ws.append(HEADERS)
        return wb

    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    ws.append(HEADERS)
    from openpyxl.styles import Font
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    return wb


def update_tracker(
    test_mode: bool,
    job_key: str,
    company: str = "",
    role: str = "",
    resume_status: str = None,
    cover_letter_status: str = None,
    watermark_status: str = None,
    final_resume: str = None,
    final_cover_letter: str = None,
) -> dict:
    path = _tracker_path(test_mode)
    lock_path = path.parent / f".{path.name}.lock"

    updates = {
        "Company": company,
        "Role": role,
        "Resume Status": resume_status,
        "Cover Letter Status": cover_letter_status,
        "Watermark Status": watermark_status,
        "Final Resume": final_resume,
        "Final Cover Letter": final_cover_letter,
    }
    updates = {k: v for k, v in updates.items() if v is not None}

    with file_lock(lock_path):
        wb = _open_or_create(path)
        ws = wb["Applications"]
        col_index = {h: i + 1 for i, h in enumerate(HEADERS)}

        target_row = None
        for row in ws.iter_rows(min_row=2):
            if row[0].value == job_key:
                target_row = row[0].row
                break

        if target_row is None:
            new_row = [""] * len(HEADERS)
            new_row[col_index["Job Key"] - 1] = job_key
            new_row[col_index["Application Status"] - 1] = "NOT APPLIED"
            for key, value in updates.items():
                new_row[col_index[key] - 1] = value
            ws.append(new_row)
        else:
            for key, value in updates.items():
                if key in USER_OWNED:
                    continue  # never written by V8.6 anyway, defense in depth
                ws.cell(row=target_row, column=col_index[key], value=value)

        wb.save(path)

    return {"tracker_path": str(path.relative_to(ROOT)), "job_key": job_key, "updates": updates}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-key", required=True)
    parser.add_argument("--company", default="")
    parser.add_argument("--role", default="")
    parser.add_argument("--resume-status")
    parser.add_argument("--cover-letter-status")
    parser.add_argument("--watermark-status")
    parser.add_argument("--final-resume")
    parser.add_argument("--final-cover-letter")
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()

    result = update_tracker(
        test_mode=args.test_mode,
        job_key=args.job_key,
        company=args.company,
        role=args.role,
        resume_status=args.resume_status,
        cover_letter_status=args.cover_letter_status,
        watermark_status=args.watermark_status,
        final_resume=args.final_resume,
        final_cover_letter=args.final_cover_letter,
    )
    import json
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
