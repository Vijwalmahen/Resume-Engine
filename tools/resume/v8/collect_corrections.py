#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

parser = argparse.ArgumentParser()

parser.add_argument(
    "--manifest",
    required=True,
)

parser.add_argument(
    "--output",
    default=".work/v8/correction_batch.json",
)

parser.add_argument(
    "--results",
    default=".work/v8/local_pipeline_results.json",
)

args = parser.parse_args()

manifest = ROOT / args.manifest

jobs = [
    Path(x.strip())
    for x in manifest.read_text().splitlines()
    if x.strip()
]

results_path = ROOT / args.results

if not results_path.exists():
    raise SystemExit(
        f"PIPELINE_RESULTS_MISSING: {results_path}"
    )

pipeline_results = json.loads(
    results_path.read_text()
)

if not isinstance(pipeline_results, list):
    raise SystemExit(
        "PIPELINE_RESULTS_INVALID: expected list"
    )

result_by_job = {
    str(item.get("job")): item
    for item in pipeline_results
    if isinstance(item, dict)
    and item.get("job")
}

missing_results = [
    job.stem
    for job in jobs
    if job.stem not in result_by_job
]

if missing_results:
    raise SystemExit(
        "PIPELINE_RESULTS_INCOMPLETE: "
        + ", ".join(missing_results)
    )

requests = []

for job in jobs:
    state = result_by_job[job.stem]

    # Latest deterministic pipeline state is authoritative.
    # Ignore stale correction_request.json files for READY/failed jobs.
    if state.get("status") != "NEEDS_LLM_CORRECTION":
        continue

    root = ROOT / "output" / job.stem

    req_from_state = state.get(
        "correction_request"
    )

    if req_from_state:
        req = ROOT / req_from_state
    else:
        req = root / "correction_request.json"

    if not req.exists():
        raise SystemExit(
            "CURRENT_CORRECTION_REQUEST_MISSING: "
            + job.stem
        )

    data = json.loads(
        req.read_text()
    )

    if data.get("job") != job.stem:
        raise SystemExit(
            "CORRECTION_REQUEST_JOB_MISMATCH: "
            + job.stem
        )

    requests.append({
        "job": job.stem,
        "resume_payload": str(
            (
                root
                / "resume_payload.json"
            ).relative_to(ROOT)
        ),
        "correction_request": str(
            req.relative_to(ROOT)
        ),
        "request": data,
    })

out = ROOT / args.output

out.parent.mkdir(
    parents=True,
    exist_ok=True,
)

out.write_text(
    json.dumps(
        {
            "count": len(requests),
            "corrections": requests,
        },
        indent=2,
        ensure_ascii=False,
    )
    + "\n"
)

print(
    "Corrections required:",
    len(requests),
)

for item in requests:
    print(
        "-",
        item["job"],
        "=>",
        item["request"].get("reasons"),
    )

print()
print(
    "Output:",
    out.relative_to(ROOT),
)
