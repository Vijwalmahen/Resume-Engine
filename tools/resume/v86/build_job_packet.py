#!/usr/bin/env python3
"""
Build one compact, self-contained job packet.

A semantic worker should be able to do its entire task from this single
JSON document without exploring the repository (it is invoked with zero
filesystem/tool access -- see tools/resume/v86/semantic_worker.py). This
script is the only thing that reads the job posting file and the batch's
shared context; everything the worker needs is embedded here.

Content budgets and the list of protected/tailorable owner_ids are read
from the candidate's own reference/resume_template_layout.json -- this
file never hardcodes a candidate's field names or bullet counts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, job_key_for, load_json, write_json  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import template_layout as layout_lib  # noqa: E402

DEFAULT_LAYOUT = ROOT / "reference" / "resume_template_layout.json"


def content_budgets(layout: dict) -> dict:
    budgets = {}

    for block in layout_lib.tailorable_experience_blocks(layout):
        budgets[f"{block['owner_id']}_bullet_max_chars"] = block.get(
            "max_chars_per_bullet"
        )

    slot = layout_lib.selected_project_block(layout)
    budgets[f"{slot['owner_id']}_title_max_chars"] = slot.get("max_chars_title")
    budgets[f"{slot['owner_id']}_technology_line_max_chars"] = slot.get(
        "max_chars_technology_line"
    )
    budgets[f"{slot['owner_id']}_bullet_max_chars"] = slot.get("max_chars_per_bullet")

    return budgets


def truth_rules(layout: dict) -> list:
    tailorable_ids = [b["owner_id"] for b in layout_lib.tailorable_experience_blocks(layout)]
    slot = layout_lib.selected_project_block(layout)

    return [
        "Never fabricate a skill, technology, experience, or metric.",
        "A skill appearing in the JD is not evidence the candidate possesses it.",
        "Only use evidence contained in the supplied candidate_context.",
        (
            "Protected canonical bullets (any experience/project entry with "
            "tailoring_mode == \"protected\") must be preserved exactly; "
            "only their ORDER may be permuted via the *_bullet_orders fields "
            "(0-based permutations)."
        ),
        (
            "Only dynamic fields may be authored/edited: "
            f"experience_alternate_bullets for {tailorable_ids}, and "
            f"selected_project.title / .technology_line / .bullets "
            f"(owner_id={slot['owner_id']})."
        ),
        "The static Skills section is fixed; never reorder it or inject JD keywords into it.",
        "For an OR requirement (A or B or C), pick ONE truthfully supported alternative; do not mark every alternative as separately required.",
        "For an AND requirement, both/all independently mandatory concepts must be represented.",
        "Eligibility requirements (availability/relocation/work authorization) are not technical skills and must never be inserted into technical bullets.",
        "Prefer atomic concept terms (e.g. 'Python', 'PyTorch') over full JD sentences.",
        "Unsupported requirements remain unsupported -- do not silently drop them from jd_requirements, and never fabricate coverage for them.",
        "Selectable-project selection is NOT performed by the semantic worker. It is bound deterministically between call 1 and call 2 and is provided as a fixed fact.",
    ]


def build_packet(batch_dir: Path, job_file: Path, job_key: str) -> Path:
    shared_context = load_json(batch_dir / "shared_candidate_context.json")
    shapes = load_json(batch_dir / "shapes.json")
    policy = load_json(batch_dir / "project_selection_policy.json")
    layout = load_json(batch_dir / "resume_template_layout.json")

    posting_text = job_file.read_text()

    allowed_projects = [
        {
            "key": p.get("id") or p.get("name"),
            "runtime_name": p.get("name"),
        }
        for p in shared_context.get("projects", [])
        if p.get("project_status") == "selectable"
    ]

    try:
        job_file_display = str(job_file.relative_to(ROOT)) if job_file.is_absolute() else str(job_file)
    except ValueError:
        # job_file lives outside ROOT (e.g. an offline test fixture dir) --
        # record the absolute path rather than crashing.
        job_file_display = str(job_file)

    packet = {
        "job_key": job_key,
        "job_file": job_file_display,
        "job_posting_text": posting_text,
        "candidate_context": shared_context,
        "allowed_selectable_projects": allowed_projects,
        "schemas": {
            "jd_requirements_shape": shapes["jd_requirements_shape"],
            "resume_payload_shape": shapes["resume_payload_shape"],
            "cover_letter_payload_shape": shapes["cover_letter_payload_shape"],
        },
        "content_budgets": content_budgets(layout),
        "truth_rules": truth_rules(layout),
        "resume_structure": ["EDUCATION", "PROFESSIONAL EXPERIENCE", "PROJECTS", "SKILLS"],
    }

    job_dir = batch_dir / "jobs" / job_key
    job_dir.mkdir(parents=True, exist_ok=True)
    packet_path = job_dir / "job_packet.json"
    write_json(packet_path, packet)
    return packet_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-dir", required=True)
    parser.add_argument("--job-file", required=True)
    parser.add_argument("--job-key", default=None)
    args = parser.parse_args()

    batch_dir = Path(args.batch_dir)
    job_file = Path(args.job_file)
    job_key = args.job_key or job_key_for(job_file)

    path = build_packet(batch_dir, job_file, job_key)
    print(f"Job packet written: {path}")


if __name__ == "__main__":
    main()
