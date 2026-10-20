#!/usr/bin/env python3
"""
V8.6 -- build the shared candidate context ONCE per batch.

This eliminates the V8.5 pattern where every semantic worker independently
re-read the full runtime candidate context. It does NOT introduce any new
candidate evidence and does NOT relax any V8.5 truth/evidence boundary: the
only source read here is the already-validated
master/runtime/runtime_resume_context.json (the same file the
jobapp-resume-batch / jobapp-resume-correct skills are restricted to), plus
the config/v8/* shape files and config/v8/project_selection_policy.json.

Also builds the ATS evidence set ONCE per batch by invoking the frozen,
unmodified tools/resume/v8/build_runtime_ats_evidence.py, then copies the
result into a batch-local directory so the rest of the batch never depends
on (or races with) the shared master/runtime/ats_evidence/ path again.

Usage:
    python3.13 tools/resume/v86/build_shared_context.py --batch-dir .work/v86/<batch-id>
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, Timer, load_json, python_cmd, run_step, write_json  # noqa: E402

RUNTIME_CONTEXT = ROOT / "master" / "runtime" / "runtime_resume_context.json"
SHARED_ATS_EVIDENCE = ROOT / "master" / "runtime" / "ats_evidence"

SHAPE_FILES = {
    "resume_payload_shape": ROOT / "config" / "v8" / "resume_payload_shape.json",
    "jd_requirements_shape": ROOT / "config" / "v8" / "jd_requirements_shape.json",
    "cover_letter_payload_shape": ROOT / "config" / "v8" / "cover_letter_payload_shape.json",
}

PROJECT_SELECTION_POLICY = ROOT / "config" / "v8" / "project_selection_policy.json"
RESUME_TEMPLATE_LAYOUT = ROOT / "reference" / "resume_template_layout.json"


def build(batch_dir: Path) -> dict:
    batch_dir.mkdir(parents=True, exist_ok=True)
    timings = {}

    # 1. Validate the runtime context is not stale -- reuses the frozen,
    #    unmodified V8.5 guard script exactly as jobapp-resume-batch does.
    with Timer() as t:
        guard = run_step(python_cmd(ROOT / "tools/resume/v8/runtime_context_guard.py", "check"))
    timings["runtime_context_guard"] = t.as_dict("runtime_context_guard")
    if not guard.ok:
        raise SystemExit(
            "RUNTIME_CONTEXT_STALE -- refusing to build V8.6 shared context.\n"
            + guard.stdout + guard.stderr
        )

    # 2. Rebuild the deterministic ATS evidence set (frozen, unmodified
    #    script; fixed output path by design -- see build_runtime_ats_evidence.py).
    with Timer() as t:
        build_evidence = run_step(python_cmd(ROOT / "tools/resume/v8/build_runtime_ats_evidence.py"))
    timings["build_runtime_ats_evidence"] = t.as_dict("build_runtime_ats_evidence")
    if not build_evidence.ok:
        raise SystemExit(
            "Failed to build V8.5 ATS evidence set.\n" + build_evidence.stdout + build_evidence.stderr
        )

    # 3. Copy that deterministic, batch-invariant output into a batch-local
    #    directory. Every job in this batch reads ATS evidence from here
    #    (read-only for the rest of the batch), not from the shared
    #    production path -- see .work/v86/<batch-id>/README note below and
    #    tools/resume/v86/README.md Section "Shared ATS evidence" for the
    #    documented rationale (identical content per batch; rebuilding it
    #    once per job would be pure wasted wall-clock time).
    shared_evidence_dir = batch_dir / "shared_ats_evidence"
    if shared_evidence_dir.exists():
        shutil.rmtree(shared_evidence_dir)
    shutil.copytree(SHARED_ATS_EVIDENCE, shared_evidence_dir)

    # 4. Build the compact shared candidate context. Only fields already
    #    present in the validated runtime context are included; nothing new
    #    is introduced.
    runtime = load_json(RUNTIME_CONTEXT)

    shared_context = {
        "schema_version": runtime.get("schema_version"),
        "candidate": runtime.get("candidate", {}),
        "education": runtime.get("education", []),
        "experience": runtime.get("experience", []),
        "projects": runtime.get("projects", []),
        "skills": runtime.get("skills", {}),
        "truth_constraints": runtime.get("truth_constraints", []),
    }

    write_json(batch_dir / "shared_candidate_context.json", shared_context)

    # 5. Carry the shape/policy files forward too, so per-job packet building
    #    (and the semantic workers) never need to re-read config/ directly.
    schemas = {name: load_json(path) for name, path in SHAPE_FILES.items()}
    write_json(batch_dir / "shapes.json", schemas)

    policy = load_json(PROJECT_SELECTION_POLICY)
    write_json(batch_dir / "project_selection_policy.json", policy)

    if not RESUME_TEMPLATE_LAYOUT.exists():
        raise SystemExit(
            f"Resume template layout not found: {RESUME_TEMPLATE_LAYOUT}\n"
            "Copy reference/resume_template_layout.example.json to "
            f"{RESUME_TEMPLATE_LAYOUT} and adjust it to match your own template."
        )

    layout = load_json(RESUME_TEMPLATE_LAYOUT)
    write_json(batch_dir / "resume_template_layout.json", layout)

    manifest = {
        "batch_dir": str(batch_dir.relative_to(ROOT)),
        "runtime_context_sha256_check": "PASS",
        "shared_ats_evidence_dir": str(shared_evidence_dir.relative_to(ROOT)),
        "shared_candidate_context_file": str((batch_dir / "shared_candidate_context.json").relative_to(ROOT)),
        "timings": timings,
    }
    write_json(batch_dir / "shared_context_manifest.json", manifest)

    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    args = parser.parse_args()

    batch_dir = (ROOT / args.batch_dir) if not Path(args.batch_dir).is_absolute() else Path(args.batch_dir)
    manifest = build(batch_dir)
    print("V8.6 shared context built:")
    print(f"  {manifest['shared_candidate_context_file']}")
    print(f"  {manifest['shared_ats_evidence_dir']}")


if __name__ == "__main__":
    main()
