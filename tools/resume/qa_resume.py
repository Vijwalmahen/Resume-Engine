#!/usr/bin/env python3
"""
Generic, data-driven PDF QA for the rendered resume.

Required text is derived entirely from the candidate's own
master/runtime/runtime_resume_context.json (candidate name, section
headings, protected employer/title values, required project names,
static skills lines) -- nothing about any specific candidate is
hardcoded here. See docs/candidate-data-guide.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import fitz


ROOT = Path(__file__).resolve().parents[2]

DEFAULT_CANDIDATE_CONTEXT = (
    ROOT / "master" / "runtime" / "runtime_resume_context.json"
)

RESUME_SECTION_HEADINGS = [
    "EDUCATION",
    "PROFESSIONAL EXPERIENCE",
    "PROJECTS",
    "SKILLS",
]

BOTTOM_MARGIN_IN = 403 / 1440


def build_required_text(candidate_context: dict) -> list:
    required = list(RESUME_SECTION_HEADINGS)

    candidate = candidate_context.get("candidate", {}) or {}
    full_name = candidate.get("full_name")
    if full_name:
        required.append(str(full_name))

    for entry in candidate_context.get("education", []) or []:
        institution = entry.get("institution")
        if institution:
            required.append(str(institution))

    for entry in candidate_context.get("experience", []) or []:
        if entry.get("tailoring_mode") != "protected":
            continue
        for key in ("employer", "title"):
            value = entry.get(key)
            if value:
                required.append(str(value))

    for entry in candidate_context.get("projects", []) or []:
        if entry.get("project_status") != "required":
            continue
        name = entry.get("name")
        if name:
            required.append(str(name))

    static_lines = (
        candidate_context.get("skills", {}) or {}
    ).get("static_resume_lines", {}) or {}

    for label, value in static_lines.items():
        required.append(f"{label}: {value}")

    return required


def text_from_document(doc) -> str:
    """The ONE place the rendered resume's text layer is read off an open
    `fitz` document. Every text-based check in this repository goes
    through it (see `extract_pdf_text`)."""
    return "\n".join(page.get_text("text") for page in doc)


def extract_pdf_text(pdf_path) -> tuple:
    """The ONE text-extraction primitive for the rendered resume PDF.

    Returns `(full_text, page_count)` using exactly the extraction this QA
    script has always used (`fitz`, `page.get_text("text")`, pages joined
    by a newline). It is factored out so the advisory round-trip
    diagnostics (tools/resume/v86/pdf_roundtrip.py) read the PDF the same
    way QA does instead of standing up a second, competing extractor whose
    results could silently disagree with `resume_qa.json`.
    """
    doc = fitz.open(pdf_path)
    try:
        return text_from_document(doc), len(doc)
    finally:
        doc.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--candidate-context",
        default=str(DEFAULT_CANDIDATE_CONTEXT),
    )

    args = parser.parse_args()

    pdf = Path(args.pdf)
    out = Path(args.output)
    candidate_context_path = Path(args.candidate_context)

    if not candidate_context_path.exists():
        raise SystemExit(
            f"Candidate context not found: {candidate_context_path}\n"
            "Run tools/validate_candidate_setup.py and see INSTRUCTIONS.md."
        )

    candidate_context = json.loads(
        candidate_context_path.read_text(encoding="utf-8")
    )

    required_text = build_required_text(candidate_context)

    doc = fitz.open(pdf)

    full_text = text_from_document(doc)

    result = {
        "pdf": str(pdf),
        "page_count": len(doc),
        "one_page": len(doc) == 1,

        "missing_required_text": [],
        "replacement_character_present": "�" in full_text,

        "page_2_nonempty_lines": 0,
        "page_2_text_chars": 0,
        "page_2_text": "",
        "minimal_page_2_spill": False,

        "raw_bottom_whitespace_in": None,
        "usable_bottom_whitespace_in": None,
        "density_target_pass": None,

        "font_sizes": [],
        "status": "PASS",
    }

    for expected in required_text:
        if expected not in full_text:
            result["missing_required_text"].append(expected)

    # --------------------------------------------------------
    # Minimal two-page spill detection
    # --------------------------------------------------------

    if len(doc) == 2:
        page2_text = doc[1].get_text("text").strip()

        lines = [line.strip() for line in page2_text.splitlines() if line.strip()]
        chars = sum(len(line) for line in lines)

        result["page_2_nonempty_lines"] = len(lines)
        result["page_2_text_chars"] = chars
        result["page_2_text"] = page2_text

        # Allow for PDF extraction occasionally splitting a visually
        # single trailing line into two text lines.
        result["minimal_page_2_spill"] = len(lines) <= 2 and chars <= 220

    # --------------------------------------------------------
    # One-page density
    # --------------------------------------------------------

    if len(doc) == 1:
        page = doc[0]

        max_y = 0.0
        font_sizes = set()

        data = page.get_text("dict")

        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    bbox = span.get("bbox")

                    if bbox:
                        max_y = max(max_y, bbox[3])

                    size = span.get("size")

                    if size:
                        font_sizes.add(round(float(size), 2))

        raw_bottom = (page.rect.height - max_y) / 72
        usable_bottom = max(0, raw_bottom - BOTTOM_MARGIN_IN)

        result["raw_bottom_whitespace_in"] = round(raw_bottom, 3)
        result["usable_bottom_whitespace_in"] = round(usable_bottom, 3)
        result["density_target_pass"] = 0.25 <= usable_bottom <= 0.50
        result["font_sizes"] = sorted(font_sizes)

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    if result["replacement_character_present"]:
        result["status"] = "FAIL"

    elif result["missing_required_text"]:
        result["status"] = "FAIL"

    elif len(doc) > 1:
        if len(doc) == 2 and result["minimal_page_2_spill"]:
            result["status"] = "EMERGENCY_MINIMAL_SPILL"
        else:
            result["status"] = "FAIL_MULTIPAGE"

    elif result["usable_bottom_whitespace_in"] is not None:
        if result["usable_bottom_whitespace_in"] > 0.65:
            result["status"] = "FAIL_SEVERE_UNDERFILL"
        elif not result["density_target_pass"]:
            result["status"] = "PASS_WITH_DENSITY_WARNING"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2) + "\n")

    print(json.dumps(result, indent=2))

    # Minimal spill is intentionally NOT a process failure because the
    # correction workflow needs to inspect the JSON and perform one more
    # emergency render.
    if result["status"] in {"FAIL", "FAIL_MULTIPAGE", "FAIL_SEVERE_UNDERFILL"}:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
