#!/usr/bin/env python3
"""
Generic, layout-driven density-repair advisor.

Given a QA report showing a one-page resume is under-filled or
over-filled, proposes which DYNAMIC field to shorten -- restricted to the
owner_ids the candidate's own reference/resume_template_layout.json marks
as tailorable/selected. Protected canonical content is never a repair
candidate: it is out of scope for any correction round by construction
(see docs/deterministic-validation.md).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_layout as layout_lib  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAYOUT = ROOT / "reference" / "resume_template_layout.json"

MIN_BOTTOM = 0.25
MAX_BOTTOM = 0.50

# Approximate one rendered resume line from the validated golden geometry.
LINE_HEIGHT_IN = 12 / 72


def clean_len(value):
    return len(str(value).replace("**", "").strip())


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--qa", required=True)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))

    args = parser.parse_args()

    qa = json.loads(Path(args.qa).read_text())
    payload = json.loads(Path(args.payload).read_text())
    layout = layout_lib.load_layout(Path(args.layout))

    pages = qa.get("page_count")
    bottom = qa.get("usable_bottom_whitespace_in")

    result = {
        "status": None,
        "page_count": pages,
        "usable_bottom_whitespace_in": bottom,
        "target_min_in": MIN_BOTTOM,
        "target_max_in": MAX_BOTTOM,
        "estimated_lines_to_remove": 0,
        "repair_candidates": [],
        "instructions": [],
    }

    if pages != 1:
        result["status"] = "NOT_APPLICABLE_MULTIPAGE"

    elif bottom is None:
        result["status"] = "FAIL_NO_DENSITY_MEASUREMENT"

    elif MIN_BOTTOM <= bottom <= MAX_BOTTOM:
        result["status"] = "PASS"

    elif bottom > MAX_BOTTOM:
        result["status"] = "UNDERFILLED"

    else:
        result["status"] = "DENSE_ONE_PAGE"

        deficit = MIN_BOTTOM - bottom
        lines = max(1, math.ceil(deficit / LINE_HEIGHT_IN))
        result["estimated_lines_to_remove"] = lines

        candidates = []
        alternates = payload.get("experience_alternate_bullets", {}) or {}

        for block in layout_lib.tailorable_experience_blocks(layout):
            owner_id = block["owner_id"]
            for i, text in enumerate(alternates.get(owner_id, []), 1):
                candidates.append({
                    "field": f"{owner_id}_bullet_{i}",
                    "characters": clean_len(text),
                    "priority": 3,
                })

        slot = layout_lib.selected_project_block(layout)
        selected = payload.get("selected_project", {}) or {}
        slot_owner = slot["owner_id"]

        candidates.append({
            "field": f"{slot_owner}_technology_line",
            "characters": clean_len(selected.get("technology_line", "")),
            "priority": 2,
        })

        for i, text in enumerate(selected.get("bullets", []), 1):
            candidates.append({
                "field": f"{slot_owner}_bullet_{i}",
                "characters": clean_len(text),
                "priority": 1,
            })

        # Prefer the longest selected-project bullet, then technology
        # line, then tailorable-experience bullets.
        candidates.sort(key=lambda x: (x["priority"], -x["characters"]))

        result["repair_candidates"] = candidates

        primary = candidates[0]

        # We observed ~one line being removed reliably when the longest
        # dynamic field was reduced materially rather than by a few words.
        reduction = max(45, 55 * lines)
        target = max(120, primary["characters"] - reduction)

        result["instructions"] = [
            f"Primary repair field: {primary['field']}.",
            (
                f"Reduce it from approximately {primary['characters']} "
                f"characters to <= {target} characters."
            ),
            "Preserve factual meaning, strongest JD terminology, and useful metrics.",
            "Remove filler, repeated context, secondary explanation, and low-value technologies first.",
            "Do not modify any protected canonical bullet.",
            "After rewriting, rerender exactly once and rerun deterministic QA.",
        ]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
