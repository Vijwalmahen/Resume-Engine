#!/usr/bin/env python3

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

SOURCE = (
    ROOT
    / "master"
    / "runtime"
    / "runtime_resume_context.json"
)

OUT_DIR = (
    ROOT
    / "master"
    / "runtime"
    / "ats_evidence"
)

OUT_FILE = (
    OUT_DIR
    / "verified_resume_evidence.json"
)


def main():
    if not SOURCE.exists():
        raise SystemExit(
            f"Missing runtime context: {SOURCE}"
        )

    data = json.loads(
        SOURCE.read_text()
    )

    # Deliberately exclude:
    # - truth_constraints
    # - rules/instructions
    # - contact metadata
    # - arbitrary prose outside verified evidence
    skills = data.get("skills", {})

    # ATS support evidence must be conservative.
    #
    # Experience-level technology inventories can contain
    # team/platform technologies that the candidate did not
    # personally own. Keep the substantive experience fields
    # but exclude generic `technologies` and ownership/rule prose
    # from lexical evidence matching.
    safe_experience = []

    for entry in data.get("experience", []):
        if not isinstance(entry, dict):
            continue

        safe_entry = {
            key: value
            for key, value in entry.items()
            if key not in {
                "technologies",
                "ownership_boundary",
            }
        }

        safe_experience.append(safe_entry)

    clean = {
        "candidate_profile": data.get("candidate", {}),
        "education": data.get(
            "education",
            [],
        ),
        "experience": safe_experience,

        # Project-specific technologies are retained because
        # their provenance is explicit.
        "projects": data.get(
            "projects",
            [],
        ),

        # Only actual static resume skills count automatically
        # for deterministic ATS evidence.
        #
        # extended_for_dynamic_evidence remains available to the
        # strategist, but is NOT an automatic ATS-support source.
        "skills": {
            "static_resume_lines":
                skills.get(
                    "static_resume_lines",
                    {},
                ),
        },
    }

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)

    OUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    OUT_FILE.write_text(
        json.dumps(
            clean,
            indent=2,
            ensure_ascii=False,
        ) + "\n"
    )

    print("V8 ATS evidence built:")
    print(OUT_FILE)
    print()
    print(
        "Included sections:",
        ", ".join(clean.keys())
    )


if __name__ == "__main__":
    main()
