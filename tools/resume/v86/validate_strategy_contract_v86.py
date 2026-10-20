#!/usr/bin/env python3
"""
Adapted copy of tools/resume/v8/validate_strategy_contract.py.

This is intentionally a separate file, not a modification of the v8
script. The structural checks (JD/resume/cover-letter shape,
permutations, bullet counts -- read from the candidate's own
reference/resume_template_layout.json, exactly like the v8 version) are
identical. The only behavioral difference is the expected `skill_name` /
`skill_identity` strings in v8_strategy_status.json: this pipeline's
content is produced by the narrow semantic worker, not by the
`jobapp-resume-batch` skill, so it is stamped with its own identity
rather than impersonating that skill's provenance.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import template_layout as layout_lib  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LAYOUT = ROOT / "reference" / "resume_template_layout.json"

JD_KEYS = {"job_key", "concepts"}
CONCEPT_REQUIRED = {"term", "category", "required", "priority"}
CONCEPT_ALLOWED = CONCEPT_REQUIRED | {"aliases"}

CATEGORIES = {
    "REQUIRED_HARD_SKILL", "PREFERRED_HARD_SKILL", "CORE_RESPONSIBILITY",
    "SOFT_SKILL", "EDUCATION_REQUIREMENT", "ELIGIBILITY",
}
PRIORITIES = {"critical", "high", "medium", "low"}

RESUME_KEYS = {
    "experience_bullet_orders",
    "experience_alternate_bullets",
    "project_bullet_orders",
    "selected_project",
}
SELECTED_PROJECT_KEYS = {"owner_id", "title", "technology_line", "bullets"}
COVER_KEYS = {
    "company", "role", "salutation", "opening",
    "experience_paragraphs", "project_paragraph", "closing",
}
STATUS_KEYS = {
    "status", "skill_name", "skill_identity", "job_file",
    "job_key", "selected_project_bound", "semantic_corrections",
}

EXPECTED_SKILL_NAME = "jobapp-resume-v86-semantic"
EXPECTED_SKILL_IDENTITY = "JOBAPP_RESUME_V86_SEMANTIC_WORKER"


def load(path: Path):
    return json.loads(path.read_text())


def permutation(value, expected) -> bool:
    return isinstance(value, list) and sorted(value) == list(expected)


def validate(root: Path, layout: dict) -> dict:
    failures = []
    paths = {
        "jd": root / "jd_requirements.json",
        "resume": root / "resume_payload.json",
        "cover": root / "cover_letter_payload.json",
        "status": root / "v8_strategy_status.json",
    }

    for name, path in paths.items():
        if not path.exists():
            failures.append(f"missing file: {path.name}")

    if failures:
        return {"status": "FAIL", "failures": failures}

    jd = load(paths["jd"])
    resume = load(paths["resume"])
    cover = load(paths["cover"])
    status = load(paths["status"])

    if set(jd) != JD_KEYS:
        failures.append(f"jd_requirements top-level keys must be {sorted(JD_KEYS)}; got {sorted(jd)}")

    concepts = jd.get("concepts")
    if not isinstance(concepts, list):
        failures.append("jd_requirements.concepts must be a list")
        concepts = []

    seen_terms = set()
    for i, concept in enumerate(concepts):
        if not isinstance(concept, dict):
            failures.append(f"concept[{i}] must be an object")
            continue
        keys = set(concept)
        missing = CONCEPT_REQUIRED - keys
        extra = keys - CONCEPT_ALLOWED
        if missing:
            failures.append(f"concept[{i}] missing keys: {sorted(missing)}")
        if extra:
            failures.append(f"concept[{i}] unexpected keys: {sorted(extra)}")

        term = str(concept.get("term", "")).strip()
        normalized = term.casefold()
        if not term:
            failures.append(f"concept[{i}] empty term")
        elif normalized in seen_terms:
            failures.append(f"duplicate concept term: {term}")
        else:
            seen_terms.add(normalized)

        if concept.get("category") not in CATEGORIES:
            failures.append(f"concept[{i}] invalid category: {concept.get('category')}")
        if not isinstance(concept.get("required"), bool):
            failures.append(f"concept[{i}] required must be boolean")
        if concept.get("priority") not in PRIORITIES:
            failures.append(f"concept[{i}] invalid priority: {concept.get('priority')}")

        aliases = concept.get("aliases")
        if aliases is not None:
            if not isinstance(aliases, list) or not all(isinstance(x, str) and x.strip() for x in aliases):
                failures.append(f"concept[{i}] aliases must be a non-empty string list")

    if set(resume) != RESUME_KEYS:
        failures.append("resume_payload top-level keys mismatch")

    experience_orders = resume.get("experience_bullet_orders", {}) or {}
    for block in layout_lib.protected_experience_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])
        if not permutation(experience_orders.get(owner_id), range(size)):
            failures.append(f"experience_bullet_orders.{owner_id} invalid")

    project_orders = resume.get("project_bullet_orders", {}) or {}
    for block in layout_lib.protected_project_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])
        if not permutation(project_orders.get(owner_id), range(size)):
            failures.append(f"project_bullet_orders.{owner_id} invalid")

    alternates = resume.get("experience_alternate_bullets", {}) or {}
    for block in layout_lib.tailorable_experience_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])
        bullets = alternates.get(owner_id)
        if (
            not isinstance(bullets, list)
            or len(bullets) != size
            or not all(isinstance(x, str) and x.strip() for x in bullets)
        ):
            failures.append(
                f"experience_alternate_bullets.{owner_id} must contain exactly {size} non-empty strings"
            )

    slot = layout_lib.selected_project_block(layout)
    selected = resume.get("selected_project")
    if not isinstance(selected, dict):
        failures.append("selected_project must be an object")
    else:
        if set(selected) != SELECTED_PROJECT_KEYS:
            failures.append("selected_project keys mismatch")
        bullets = selected.get("bullets")
        size = len(slot["bullet_indices"])
        if (
            not isinstance(bullets, list)
            or len(bullets) != size
            or not all(isinstance(x, str) and x.strip() for x in bullets)
        ):
            failures.append(f"selected_project.bullets must contain exactly {size} non-empty strings")

    if set(cover) != COVER_KEYS:
        failures.append(f"cover_letter_payload keys mismatch: got {sorted(cover)}")
    for key in COVER_KEYS - {"experience_paragraphs"}:
        if not str(cover.get(key, "")).strip():
            failures.append(f"cover_letter_payload.{key} empty")

    experience_paragraphs = cover.get("experience_paragraphs")
    if not isinstance(experience_paragraphs, dict):
        failures.append("cover_letter_payload.experience_paragraphs must be an object")
    else:
        for owner_id, text in experience_paragraphs.items():
            if not str(text).strip():
                failures.append(f"cover_letter_payload.experience_paragraphs.{owner_id} empty")

    if set(status) != STATUS_KEYS:
        failures.append("v8_strategy_status keys mismatch")
    if status.get("status") != "STRATEGY_READY":
        failures.append("strategy status is not STRATEGY_READY")
    if status.get("skill_name") != EXPECTED_SKILL_NAME:
        failures.append("incorrect strategy skill_name")
    if status.get("skill_identity") != EXPECTED_SKILL_IDENTITY:
        failures.append("incorrect strategy skill identity")

    return {
        "status": "PASS" if not failures else "FAIL",
        "concept_count": len(concepts),
        "failures": failures,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    args = parser.parse_args()

    layout = layout_lib.load_layout(Path(args.layout))
    result = validate(Path(args.job_dir), layout)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
