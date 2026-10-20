#!/usr/bin/env python3
"""
Generic, layout-driven bullet-order normalizer.

Converts any 1-based bullet-order array the model produced (for any
protected experience/project owner_id declared in the candidate's
reference/resume_template_layout.json) to the canonical 0-based form, and
checks that the dynamic (tailorable/selected) fields exist structurally.
No employer/project name is hardcoded anywhere in this module.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import template_layout as layout_lib  # noqa: E402


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_LAYOUT = ROOT / "reference" / "resume_template_layout.json"


def normalize_order(name, value, size):
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")

    if not all(isinstance(x, int) and not isinstance(x, bool) for x in value):
        raise ValueError(f"{name} must contain integers only")

    zero_based = list(range(size))
    one_based = list(range(1, size + 1))

    if sorted(value) == zero_based:
        return value, False

    if sorted(value) == one_based:
        return [x - 1 for x in value], True

    raise ValueError(f"{name} must be a permutation of {zero_based}; got {value}")


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--payload", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))

    args = parser.parse_args()

    payload_path = Path(args.payload)
    report_path = Path(args.report)

    data = json.loads(payload_path.read_text())
    layout = layout_lib.load_layout(Path(args.layout))

    conversions = []

    def fail(message):
        report = {"status": "FAIL", "error": message}
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
        raise SystemExit(1)

    experience_orders = data.setdefault("experience_bullet_orders", {})
    for block in layout_lib.protected_experience_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])

        if owner_id not in experience_orders:
            fail(f"missing required field: experience_bullet_orders.{owner_id}")

        try:
            after, changed = normalize_order(
                f"experience_bullet_orders.{owner_id}",
                experience_orders[owner_id],
                size,
            )
        except ValueError as e:
            fail(str(e))
            return

        experience_orders[owner_id] = after
        if changed:
            conversions.append({
                "field": f"experience_bullet_orders.{owner_id}",
                "to": after,
                "reason": "safe 1-based to 0-based conversion",
            })

    project_orders = data.setdefault("project_bullet_orders", {})
    for block in layout_lib.protected_project_blocks(layout):
        owner_id = block["owner_id"]
        size = len(block["bullet_indices"])

        if owner_id not in project_orders:
            fail(f"missing required field: project_bullet_orders.{owner_id}")

        try:
            after, changed = normalize_order(
                f"project_bullet_orders.{owner_id}",
                project_orders[owner_id],
                size,
            )
        except ValueError as e:
            fail(str(e))
            return

        project_orders[owner_id] = after
        if changed:
            conversions.append({
                "field": f"project_bullet_orders.{owner_id}",
                "to": after,
                "reason": "safe 1-based to 0-based conversion",
            })

    # Basic dynamic-content structure checks.
    if not isinstance(data.get("experience_alternate_bullets"), dict):
        fail("experience_alternate_bullets must be an object")

    selected = data.get("selected_project")

    if not isinstance(selected, dict):
        fail("selected_project must be an object")
        return

    for field in ["owner_id", "title", "technology_line", "bullets"]:
        if field not in selected:
            fail(f"selected_project.{field} missing")

    payload_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )

    report = {
        "status": "PASS",
        "conversions": conversions,
        "conversion_count": len(conversions),
    }

    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
