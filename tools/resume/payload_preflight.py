#!/usr/bin/env python3
"""
Generic, layout-driven character-budget preflight for the dynamic (owner_id
-keyed) fields of a resume payload. Character limits are read from the
candidate's own reference/resume_template_layout.json -- tuned to whatever
that template's column widths actually allow -- never hardcoded to any
specific candidate's field names.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_layout as layout_lib  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LAYOUT = ROOT / "reference" / "resume_template_layout.json"


def clean_length(text):
    # Markdown bold markers do not consume visual text width.
    return len(str(text).replace("**", "").strip())


def limit_for(block: dict, key: str, emergency: bool) -> int:
    suffix = "_emergency" if emergency else ""
    value = block.get(f"{key}{suffix}")
    if value is None and emergency:
        value = block.get(key)
    if value is None:
        raise ValueError(f"Layout block {block.get('owner_id')} missing {key}")
    return int(value)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--payload", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))
    parser.add_argument("--emergency", action="store_true")

    args = parser.parse_args()

    payload_path = Path(args.payload)
    layout_path = Path(args.layout)

    data = json.loads(payload_path.read_text())
    layout = layout_lib.load_layout(layout_path)

    failures = []
    measurements = {}

    def test(name, value, limit):
        n = clean_length(value)
        measurements[name] = {"characters": n, "limit": limit}
        if n > limit:
            failures.append(f"{name}: {n} > {limit}")

    alternates = data.get("experience_alternate_bullets", {}) or {}

    for block in layout_lib.tailorable_experience_blocks(layout):
        owner_id = block["owner_id"]
        bullets = alternates.get(owner_id, [])
        expected_n = len(block["bullet_indices"])

        if len(bullets) != expected_n:
            failures.append(
                f"Expected exactly {expected_n} bullets for "
                f"experience_alternate_bullets[{owner_id}]; got {len(bullets)}"
            )

        limit = limit_for(block, "max_chars_per_bullet", args.emergency)

        for i, bullet in enumerate(bullets):
            test(f"{owner_id}_bullet_{i + 1}", bullet, limit)

    slot = layout_lib.selected_project_block(layout)
    selected = data.get("selected_project", {}) or {}
    slot_owner = slot["owner_id"]

    test(
        f"{slot_owner}_title",
        selected.get("title", ""),
        limit_for(slot, "max_chars_title", args.emergency),
    )

    test(
        f"{slot_owner}_technology_line",
        selected.get("technology_line", ""),
        limit_for(slot, "max_chars_technology_line", args.emergency),
    )

    bullets = selected.get("bullets", [])
    expected_n = len(slot["bullet_indices"])

    if len(bullets) != expected_n:
        failures.append(
            f"Expected exactly {expected_n} selected_project bullets; got {len(bullets)}"
        )

    bullet_limit = limit_for(slot, "max_chars_per_bullet", args.emergency)

    for i, bullet in enumerate(bullets):
        test(f"{slot_owner}_bullet_{i + 1}", bullet, bullet_limit)

    result = {
        "status": "PASS" if not failures else "FAIL",
        "mode": "EMERGENCY" if args.emergency else "NORMAL",
        "measurements": measurements,
        "failures": failures,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")

    print(json.dumps(result, indent=2))

    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
