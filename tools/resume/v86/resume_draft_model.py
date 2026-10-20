#!/usr/bin/env python3
"""
Structural model of the rendered `resume_draft.md`.

`tools/resume/render_resume.py` writes `resume_draft.md` directly from the
paragraphs of the Word document it just produced (`markdown_from_document`),
so that file IS the intended final resume content -- not a reconstruction
from unrelated candidate files. This module turns it into an ordered list
of identified expected fields so the PDF round-trip check
(`pdf_roundtrip.py`) and the short-bullet advisory
(`bullet_length_audit.py`) can both talk about "PROJECTS bullet 2" instead
of doing a single whole-document pass/fail.

Section membership is determined structurally, from the same four fixed
`resume_structure` headings every other script already relies on
(`build_job_packet.py`, `qa_resume.py`, `action_verb_diversity.py`), never
by guessing from a line's wording.

Only EXPERIENCE/PROJECTS bullets are marked `substantive`. Section
headings, company/project heading lines, education lines, the contact row
and the skills lines are all parsed and kept (the round-trip check still
wants to know whether they survived into the PDF, and in what order), but
they are never treated as "a bullet" by any length/style advisory -- see
Feature C's "avoid warning on headings, dates, skills, education entries".

This module reads a string; it never reads candidate master data, never
writes anything, and never raises on malformed input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

EDUCATION_HEADER = "EDUCATION"
EXPERIENCE_HEADER = "PROFESSIONAL EXPERIENCE"
PROJECTS_HEADER = "PROJECTS"
SKILLS_HEADER = "SKILLS"

SECTION_HEADINGS = (
    EDUCATION_HEADER,
    EXPERIENCE_HEADER,
    PROJECTS_HEADER,
    SKILLS_HEADER,
)

# Same bullet shape `action_verb_diversity._BULLET_LINE` already uses, so
# the two modules can never disagree about what counts as a bullet (proved
# by a dedicated regression test rather than by sharing a private regex).
_BULLET_LINE = re.compile(r"^\s*[-•]\s+(.*\S)\s*$")

_SECTION_SLUG = {
    EDUCATION_HEADER: "education",
    EXPERIENCE_HEADER: "experience",
    PROJECTS_HEADER: "projects",
    SKILLS_HEADER: "skills",
}

BULLET_SECTIONS = (EXPERIENCE_HEADER, PROJECTS_HEADER)


def parse_resume_markdown(markdown: str) -> dict:
    """Parse a rendered `resume_draft.md` into ordered expected fields.

    Returns::

        {
          "fields": [
            {"id": "header.name", "kind": "header_line", "section": None,
             "index": 0, "text": "...", "substantive": False},
            {"id": "section_heading.experience", "kind": "section_heading", ...},
            {"id": "experience.bullet.0", "kind": "bullet",
             "section": "PROFESSIONAL EXPERIENCE", "index": 0,
             "text": "...", "substantive": True},
            ...
          ],
          "sections_present": ["EDUCATION", "PROFESSIONAL EXPERIENCE", ...],
        }

    `fields` is in document order -- that order is exactly what the
    reading-order check compares the PDF's extracted text against.
    """
    fields: list = []
    sections_present: list = []
    section = None
    header_index = 0
    bullet_counters = {name: 0 for name in BULLET_SECTIONS}
    line_counters: dict = {}

    for raw_line in (markdown or "").splitlines():
        text = raw_line.strip()
        if not text:
            continue

        if text in SECTION_HEADINGS:
            section = text
            if text not in sections_present:
                sections_present.append(text)
            fields.append({
                "id": f"section_heading.{_SECTION_SLUG[text]}",
                "kind": "section_heading",
                "section": text,
                "index": None,
                "text": text,
                "substantive": False,
            })
            continue

        if section is None:
            # Everything above the first section heading: the name line,
            # then the contact row. Any further pre-section line is kept
            # with a generic id rather than being guessed at.
            kind_id = {0: "header.name", 1: "header.contact"}.get(
                header_index, f"header.line.{header_index}"
            )
            fields.append({
                "id": kind_id,
                "kind": "header_line",
                "section": None,
                "index": header_index,
                "text": text,
                "substantive": False,
            })
            header_index += 1
            continue

        match = _BULLET_LINE.match(raw_line)
        if match and section in BULLET_SECTIONS:
            slug = _SECTION_SLUG[section]
            index = bullet_counters[section]
            bullet_counters[section] += 1
            fields.append({
                "id": f"{slug}.bullet.{index}",
                "kind": "bullet",
                "section": section,
                "index": index,
                "text": match.group(1),
                "substantive": True,
            })
            continue

        slug = _SECTION_SLUG[section]
        index = line_counters.get(slug, 0)
        line_counters[slug] = index + 1
        kind = {
            EDUCATION_HEADER: "education_line",
            SKILLS_HEADER: "skills_line",
        }.get(section, "entry_heading")
        fields.append({
            "id": f"{slug}.line.{index}",
            "kind": kind,
            "section": section,
            "index": index,
            "text": text,
            "substantive": False,
        })

    return {"fields": fields, "sections_present": sections_present}


def parse_resume_markdown_file(path: Union[str, Path]) -> dict:
    """`parse_resume_markdown` over a file. A missing/unreadable draft
    parses as zero fields rather than raising -- every caller here is a
    non-blocking diagnostic and must degrade to `not_evaluated`, never
    break the job."""
    resolved = Path(path)
    if not resolved.exists():
        return {"fields": [], "sections_present": []}
    try:
        text = resolved.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"fields": [], "sections_present": []}
    return parse_resume_markdown(text)


def substantive_bullets(model: dict) -> list:
    """Every EXPERIENCE + PROJECTS bullet field, in document order."""
    return [f for f in model.get("fields", []) if f.get("substantive")]
