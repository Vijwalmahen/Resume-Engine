#!/usr/bin/env python3
"""
Safe candidate bootstrap tool.

Copies the fully-fictional example files from master/examples/ into their
real, private, git-ignored locations under master/, so a new user has a
complete, valid starting point to edit in place -- see INSTRUCTIONS.md.

Usage:
    python3 tools/bootstrap_candidate.py
    python3 tools/bootstrap_candidate.py --overwrite   # DANGEROUS, see below

Never overwrites an existing file unless --overwrite is explicitly passed
(and even then, prints a clear warning per file). Never invents real
personal information -- every value copied is the same synthetic example
data already committed to the repository. Safe to run repeatedly.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES_DIR = ROOT / "master" / "examples"
MASTER_DIR = ROOT / "master"
REFERENCE_DIR = ROOT / "reference"

# (example filename relative to master/examples/, real destination path)
CANDIDATE_FILE_MAP = [
    ("candidate_facts.example.md", MASTER_DIR / "candidate_facts.md"),
    ("candidate_profile.example.json", MASTER_DIR / "candidate_profile.json"),
    ("evidence_bank.example.json", MASTER_DIR / "evidence_bank.json"),
    ("metrics_registry.example.json", MASTER_DIR / "metrics_registry.json"),
    ("eligibility_facts.example.json", MASTER_DIR / "eligibility_facts.json"),
    ("rejections.example.json", MASTER_DIR / "rejections.json"),
    ("canonical_resume_entries.example.md", MASTER_DIR / "canonical_resume_entries.md"),
    (
        "runtime_resume_context.example.json",
        MASTER_DIR / "runtime" / "runtime_resume_context.json",
    ),
]

# (example filename relative to reference/, real destination path)
TEMPLATE_LAYOUT_FILE_MAP = [
    (
        REFERENCE_DIR / "resume_template_layout.example.json",
        REFERENCE_DIR / "resume_template_layout.json",
    ),
    (
        REFERENCE_DIR / "cover_letter_template_layout.example.json",
        REFERENCE_DIR / "cover_letter_template_layout.json",
    ),
]


def copy_one(source: Path, destination: Path, overwrite: bool) -> str:
    if destination.exists() and not overwrite:
        return "SKIPPED_EXISTS"

    if destination.exists() and overwrite:
        print(f"  WARNING: overwriting existing file: {destination.relative_to(ROOT)}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return "CREATED" if not destination.exists() else "OVERWRITTEN" if overwrite else "CREATED"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="DANGEROUS: overwrite existing private candidate files with the fictional examples.",
    )
    args = parser.parse_args()

    print("Bootstrapping candidate files from master/examples/ ...")
    print()

    created = []
    skipped = []

    for example_name, destination in CANDIDATE_FILE_MAP:
        source = EXAMPLES_DIR / example_name
        if not source.exists():
            print(f"  MISSING EXAMPLE (skipping): {example_name}")
            continue

        status = copy_one(source, destination, args.overwrite)
        rel = destination.relative_to(ROOT)
        if status == "SKIPPED_EXISTS":
            print(f"  SKIPPED (already exists): {rel}")
            skipped.append(str(rel))
        else:
            print(f"  {status}: {rel}")
            created.append(str(rel))

    print()
    print("Bootstrapping template layout config from reference/*.example.json ...")
    print()

    for source, destination in TEMPLATE_LAYOUT_FILE_MAP:
        if not source.exists():
            print(f"  MISSING EXAMPLE (skipping): {source.name}")
            continue

        status = copy_one(source, destination, args.overwrite)
        rel = destination.relative_to(ROOT)
        if status == "SKIPPED_EXISTS":
            print(f"  SKIPPED (already exists): {rel}")
            skipped.append(str(rel))
        else:
            print(f"  {status}: {rel}")
            created.append(str(rel))

    print()
    print("=" * 64)
    print(f"Created/updated: {len(created)}")
    print(f"Skipped (already present): {len(skipped)}")
    print("=" * 64)
    print()
    print("These files contain 100% FICTIONAL example data.")
    print("Next steps:")
    print("  1. Edit every file listed above with your own verified information.")
    print("  2. Add your own reference/resume_template.docx and")
    print("     reference/cover_letter_template.docx.")
    print("  3. Run: python3 tools/validate_candidate_setup.py")
    print()
    print("See INSTRUCTIONS.md for a full walkthrough of every field.")


if __name__ == "__main__":
    main()
