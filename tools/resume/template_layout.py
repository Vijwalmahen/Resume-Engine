#!/usr/bin/env python3
"""
Generic, data-driven DOCX layout contract for the resume renderer.

The renderer edits an existing Word document at fixed paragraph indices --
it never lays out a document from scratch. Which paragraph is which
section, which paragraphs are protected/reorderable canonical bullets,
which are dynamically tailorable, and which single slot holds the
candidate's selected project, are ALL read from a layout JSON file the
candidate supplies alongside their own template
(reference/resume_template_layout.json; see
reference/resume_template_layout.example.json for the shape and
reference/README.md for how to build one for a new template).

Nothing in this module knows any candidate's name, employer, university,
or project. Every identity is an opaque `owner_id` string that must also
appear on the corresponding entry in the candidate's own
master/runtime/runtime_resume_context.json (see docs/candidate-data-guide.md).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def load_layout(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _blocks(layout: dict, key: str, tailoring_mode: Optional[str] = None) -> list:
    blocks = layout.get(key, []) or []
    if tailoring_mode is None:
        return blocks
    return [b for b in blocks if b.get("tailoring_mode") == tailoring_mode]


def protected_experience_blocks(layout: dict) -> list:
    return _blocks(layout, "experience_blocks", "protected")


def tailorable_experience_blocks(layout: dict) -> list:
    return _blocks(layout, "experience_blocks", "tailorable")


def protected_project_blocks(layout: dict) -> list:
    return _blocks(layout, "project_blocks", "protected")


def selected_project_block(layout: dict) -> dict:
    candidates = _blocks(layout, "project_blocks", "selected")
    if len(candidates) != 1:
        raise ValueError(
            "Layout must define exactly one project_blocks entry with "
            f"tailoring_mode 'selected'; found {len(candidates)}."
        )
    return candidates[0]


def all_bullet_indexes(layout: dict) -> set:
    indexes = set()
    for block in layout.get("experience_blocks", []) or []:
        indexes.update(block.get("bullet_indices", []))
    for block in layout.get("project_blocks", []) or []:
        indexes.update(block.get("bullet_indices", []))
    return indexes


def validate_template(doc, layout: dict) -> None:
    """Structural-only validation: paragraph count and section-heading
    anchors. This deliberately never requires any literal candidate
    content (name, employer, coursework, project title) -- only that the
    template's own declared geometry (from the layout file) still holds."""
    paragraphs = doc.paragraphs
    min_count = layout.get("min_paragraph_count", 0)

    if len(paragraphs) < min_count:
        raise ValueError(
            f"Template structure changed: expected >= {min_count} "
            f"paragraphs, got {len(paragraphs)}"
        )

    for name, anchor in (layout.get("section_anchors") or {}).items():
        index = anchor["index"]
        expected = anchor["text"]
        actual = paragraphs[index].text.strip()

        if actual != expected:
            raise ValueError(
                f"Template anchor mismatch at paragraph {index} "
                f"({name}): expected {expected!r}, got {actual!r}"
            )


def validate_order(name: str, value, n: int) -> list:
    if value is None:
        return list(range(n))

    if sorted(value) != list(range(n)):
        raise ValueError(
            f"{name} must be a permutation of {list(range(n))}; got {value}"
        )

    return value


def validate_payload(payload: dict, layout: dict) -> None:
    orders = payload.setdefault("experience_bullet_orders", {})
    for block in protected_experience_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])
        orders[owner_id] = validate_order(
            f"experience_bullet_orders[{owner_id}]",
            orders.get(owner_id),
            size,
        )

    project_orders = payload.setdefault("project_bullet_orders", {})
    for block in protected_project_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])
        project_orders[owner_id] = validate_order(
            f"project_bullet_orders[{owner_id}]",
            project_orders.get(owner_id),
            size,
        )

    alternates = payload.get("experience_alternate_bullets", {}) or {}
    for block in tailorable_experience_blocks(layout):
        owner_id = block["owner_id"]
        expected_n = len(block["bullet_indices"])
        bullets = alternates.get(owner_id)

        if not isinstance(bullets, list) or len(bullets) != expected_n:
            raise ValueError(
                f"experience_alternate_bullets[{owner_id}] must contain "
                f"exactly {expected_n} strings."
            )

        if not all(isinstance(x, str) and x.strip() for x in bullets):
            raise ValueError(
                f"Every bullet in experience_alternate_bullets[{owner_id}] "
                "must be a non-empty string."
            )

    slot = selected_project_block(layout)
    selected = payload.get("selected_project")

    if not isinstance(selected, dict):
        raise ValueError("selected_project must be an object.")

    for field in ("owner_id", "title", "technology_line", "bullets"):
        if field not in selected:
            raise ValueError(f"selected_project missing required field: {field}")

    expected_n = len(slot["bullet_indices"])

    if not isinstance(selected["bullets"], list) or len(selected["bullets"]) != expected_n:
        raise ValueError(
            f"selected_project.bullets must contain exactly {expected_n} strings."
        )

    if not str(selected["title"]).strip():
        raise ValueError("selected_project.title cannot be empty.")

    if not str(selected["technology_line"]).strip():
        raise ValueError("selected_project.technology_line cannot be empty.")


def snapshot_content(paragraph):
    from copy import deepcopy

    ppr = paragraph._p.pPr
    return [deepcopy(child) for child in paragraph._p if child is not ppr]


def clear_content(paragraph):
    ppr = paragraph._p.pPr
    for child in list(paragraph._p):
        if child is not ppr:
            paragraph._p.remove(child)


def restore_content(paragraph, content):
    from copy import deepcopy

    clear_content(paragraph)
    for child in content:
        paragraph._p.append(deepcopy(child))


def apply_payload(doc, payload: dict, layout: dict, markdown_runs, set_project_header) -> None:
    paragraphs = doc.paragraphs

    for block in protected_experience_blocks(layout):
        owner_id = block["owner_id"]
        indices = block["bullet_indices"]
        order = payload["experience_bullet_orders"][owner_id]
        snapshots = [snapshot_content(paragraphs[i]) for i in indices]

        for target, source in zip(indices, order):
            restore_content(paragraphs[target], snapshots[source])

    for block in protected_project_blocks(layout):
        owner_id = block["owner_id"]
        indices = block["bullet_indices"]
        order = payload["project_bullet_orders"][owner_id]
        snapshots = [snapshot_content(paragraphs[i]) for i in indices]

        for target, source in zip(indices, order):
            restore_content(paragraphs[target], snapshots[source])

    for block in tailorable_experience_blocks(layout):
        owner_id = block["owner_id"]
        bullets = payload["experience_alternate_bullets"][owner_id]

        for index, text in zip(block["bullet_indices"], bullets):
            markdown_runs(paragraphs[index], text)

    slot = selected_project_block(layout)
    selected = payload["selected_project"]

    set_project_header(
        paragraphs[slot["header_index"]],
        selected["title"],
        selected["technology_line"],
    )

    for index, text in zip(slot["bullet_indices"], selected["bullets"]):
        markdown_runs(paragraphs[index], text)
