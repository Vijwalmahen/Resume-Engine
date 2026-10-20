#!/usr/bin/env python3
"""
Cross-validates that the selected project agrees across three
independent sources for one job:

  - resume_payload.json's `selected_project` (what the semantic worker
    actually wrote)
  - v8_strategy_status.json's `selected_project_bound` (what the worker
    believes it was bound to)
  - project_selection.json (the deterministic selector's own binding --
    see tools/resume/v8/select_project.py)

Uses only owner_id/runtime_name and (optional, candidate-supplied)
`title_markers` -- never a hardcoded project name -- so this file has no
knowledge of any specific candidate's projects.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RUNTIME = ROOT / "master" / "runtime" / "runtime_resume_context.json"


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).lower()

    for ch in ("–", "—", "−", "‐"):
        text = text.replace(ch, "-")

    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def title_markers_for(runtime: dict, runtime_name: str) -> list:
    for project in runtime.get("projects", []):
        if project.get("name") == runtime_name:
            return project.get("title_markers") or [normalize(runtime_name)]
    return [normalize(runtime_name)]


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--runtime-context", default=str(DEFAULT_RUNTIME))

    args = parser.parse_args()

    job_dir = Path(args.job_dir)
    runtime = json.loads(Path(args.runtime_context).read_text())
    selection = json.loads(Path(args.selection).read_text())

    resume = json.loads((job_dir / "resume_payload.json").read_text())
    status = json.loads((job_dir / "v8_strategy_status.json").read_text())

    failures = []

    selected = resume.get("selected_project", {}) or {}
    payload_owner_id = selected.get("owner_id")
    payload_title = str(selected.get("title", ""))

    allowed_keys = set(selection.get("allowed_project_keys", []) or [])

    if payload_owner_id not in allowed_keys:
        failures.append(
            f"resume_payload.selected_project.owner_id {payload_owner_id!r} "
            f"is not one of the allowed selectable projects {sorted(allowed_keys)}"
        )

    if selection.get("selection_mode") == "BINDING_REQUIRED_COVERAGE":
        bound_key = selection.get("selected_project_key")

        if payload_owner_id != bound_key:
            failures.append(
                f"resume_payload.selected_project.owner_id must be the "
                f"deterministically bound project {bound_key!r}; got {payload_owner_id!r}"
            )

        if status.get("selected_project_bound") != bound_key:
            failures.append(
                "v8_strategy_status.selected_project_bound does not match "
                f"the deterministic binding {bound_key!r}"
            )

    # The title text is authored prose and may paraphrase the runtime
    # project name, but it must still truthfully identify the same
    # project via at least one of its title markers.
    runtime_name = None
    for row in selection.get("rankings", []):
        if row.get("project_key") == payload_owner_id:
            runtime_name = row.get("runtime_name")
            break

    if runtime_name:
        markers = title_markers_for(runtime, runtime_name)
        normalized_title = normalize(payload_title)

        if not any(marker in normalized_title for marker in markers):
            failures.append(
                f"selected_project.title {payload_title!r} does not identify "
                f"the bound project ({runtime_name!r}, markers={markers})"
            )

    result = {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
    }

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
