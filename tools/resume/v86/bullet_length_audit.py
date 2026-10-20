#!/usr/bin/env python3
"""
Feature C -- unusually-short-bullet advisory (ADVISORY ONLY).

This is a SEPARATE, non-blocking advisory that sits beside -- never on
top of -- the authoritative length machinery, which is unchanged:

  - MAXIMUM characters per field stay in the candidate's own
    reference/resume_template_layout.json and are enforced by
    tools/resume/payload_preflight.py (a real gate).
  - Rendered one-page density stays in tools/resume/qa_resume.py and
    tools/resume/density_guard.py (a real gate).

Nothing here changes, re-derives, or second-guesses either of those.

Scope is exactly the substantive bullets of the rendered resume --
EXPERIENCE and PROJECTS bullets, identified structurally by
`resume_draft_model` -- so section headings, company/date heading lines,
education entries and the skills lines are never measured, let alone
warned about.

Visible length reuses `tools/resume/payload_preflight.clean_length`, the
same "markdown bold markers do not consume visual width" definition the
real budget check already uses, so a bullet can never be measured one way
by the gate and another way by this advisory.

The default threshold (60 visible characters) is a PROVISIONAL HEURISTIC
configured in config/v86/resume_quality_diagnostics.json, not an
industry-standard cutoff. A bullet exactly at the threshold is never
warned about. A concise bullet that states a clear, verified result is
not weak merely because it is short, and nothing in this repository ever
lengthens a bullet to satisfy this number.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from payload_preflight import clean_length  # noqa: E402

DEFAULT_SHORT_BULLET_MIN_VISIBLE_CHARS = 60

_STYLE_MARKERS = re.compile(r"\*\*|__|`")
_LEADING_BULLET = re.compile(r"^\s*[-•]\s*")


def visible_length(text: str) -> int:
    """Visible characters of a bullet: any leading bullet marker and all
    markdown styling removed. `clean_length` owns the markdown-bold rule;
    this only adds the bullet-marker and remaining-marker stripping that
    a rendered markdown line (as opposed to a raw payload field) needs."""
    stripped = _LEADING_BULLET.sub("", str(text or ""))
    stripped = _STYLE_MARKERS.sub("", stripped)
    return clean_length(stripped)


def audit_bullet_lengths(
    bullet_fields: list,
    threshold: int = DEFAULT_SHORT_BULLET_MIN_VISIBLE_CHARS,
) -> dict:
    """Measure every substantive bullet and flag the ones strictly below
    `threshold`.

    `bullet_fields` is `resume_draft_model.substantive_bullets(...)` --
    already restricted to EXPERIENCE/PROJECTS bullets. Any non-substantive
    field handed in anyway is ignored rather than measured, so this can
    never warn about a heading, a date line, a skills line or an education
    entry.
    """
    threshold = int(threshold)
    measurements = []
    short = []

    for field in bullet_fields or []:
        if not field.get("substantive"):
            continue
        length = visible_length(field.get("text", ""))
        row = {
            "id": field.get("id"),
            "section": field.get("section"),
            "visible_chars": length,
            "threshold": threshold,
            "short": length < threshold,
        }
        measurements.append(row)
        if row["short"]:
            short.append(row)

    if not measurements:
        return {
            "status": "NOT_EVALUATED",
            "reason": "no substantive EXPERIENCE/PROJECTS bullets were found in the rendered resume",
            "threshold": threshold,
            "bullets_measured": 0,
            "measurements": [],
            "short_bullets": [],
        }

    return {
        "status": "EVALUATED",
        "reason": None,
        "threshold": threshold,
        "bullets_measured": len(measurements),
        "measurements": measurements,
        "short_bullets": short,
    }
