#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

SOURCES = [
    "master/canonical_resume_entries.md",
    "master/candidate_facts.md",
    "master/candidate_profile.json",
    "master/evidence_bank.json",
    "master/metrics_registry.json",
    "master/eligibility_facts.json",
]

CONTEXT = Path(
    "master/runtime/runtime_resume_context.json"
)

MANIFEST = Path(
    "master/runtime/runtime_resume_context.manifest.json"
)


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def current_hashes():
    result = {}

    for rel in SOURCES:
        path = ROOT / rel

        if not path.exists():
            raise SystemExit(
                f"Missing authoritative source: {rel}"
            )

        result[rel] = sha256(path)

    return result


def write_manifest():
    context = ROOT / CONTEXT

    if not context.exists():
        raise SystemExit(
            "runtime_resume_context.json does not exist."
        )

    data = {
        "schema_version": "8.0",
        "context_file": str(CONTEXT),
        "context_sha256": sha256(context),
        "source_hashes": current_hashes(),
    }

    path = ROOT / MANIFEST

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ) + "\n"
    )

    print("Runtime-context manifest written.")
    print(path)


def check_manifest():
    context = ROOT / CONTEXT
    manifest_path = ROOT / MANIFEST

    if not context.exists():
        raise SystemExit(
            "FAIL: runtime context missing"
        )

    if not manifest_path.exists():
        raise SystemExit(
            "FAIL: runtime context manifest missing"
        )

    manifest = json.loads(
        manifest_path.read_text()
    )

    failures = []

    actual_context_hash = sha256(context)

    if (
        actual_context_hash
        != manifest.get("context_sha256")
    ):
        failures.append(
            "runtime context changed after approval"
        )

    actual_sources = current_hashes()

    expected_sources = manifest.get(
        "source_hashes",
        {}
    )

    for rel, digest in actual_sources.items():
        if expected_sources.get(rel) != digest:
            failures.append(
                f"authoritative source changed: {rel}"
            )

    if failures:
        print("RUNTIME CONTEXT: STALE")

        for failure in failures:
            print(" -", failure)

        raise SystemExit(1)

    print("RUNTIME CONTEXT: VALID")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "action",
        choices=["write-manifest", "check"],
    )

    args = parser.parse_args()

    if args.action == "write-manifest":
        write_manifest()
    else:
        check_manifest()


if __name__ == "__main__":
    main()
