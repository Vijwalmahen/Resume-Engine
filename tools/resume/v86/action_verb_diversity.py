#!/usr/bin/env python3
"""
Deterministic action-verb diversity analyzer.

STYLE/QUALITY check only. This module never touches ATS logic, evidence
ownership, company identity, correction-state isolation, queueing, the
tracker, effort levels, or rendering -- it only COUNTS the opening word of
each EXPERIENCE + PROJECT bullet on the already-rendered resume, so a
downstream caller (deterministic_pipeline.run_job_pipeline) can decide
whether the same opening action verb is repeated more than a small limit.

Scope (exactly what the task specifies, nothing more):
  - COUNTED: every bullet ("- ..." line) under the PROFESSIONAL EXPERIENCE
    heading and every bullet under the PROJECTS heading, in the final
    rendered `resume_draft.md` -- i.e. BOTH the protected canonical bullets
    AND the dynamically-authored ones, because both are equally visible on
    the final, shipped resume. Section membership is determined
    structurally, from the same fixed `resume_structure` headings every
    other script already relies on (`build_job_packet.py`: `["EDUCATION",
    "PROFESSIONAL EXPERIENCE", "PROJECTS", "SKILLS"]`), never by guessing
    from a bullet's own wording.
  - NEVER COUNTED: EDUCATION, SKILLS, section/company/project heading lines,
    the cover letter (a separate file/payload this module never reads), or
    any word other than a bullet's own first lexical token.

Matching is a plain, exact, case-insensitive string comparison of the first
lexical word -- NEVER fuzzy/semantic synonym matching and NEVER a stemmer
applied to unrelated words. "Built" and "Building" are counted as two
different verbs, on purpose; conflating them would be exactly the kind of
guess this module is designed not to make.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Union

EXPERIENCE_HEADER = "PROFESSIONAL EXPERIENCE"
PROJECTS_HEADER = "PROJECTS"
SKILLS_HEADER = "SKILLS"

DEFAULT_LIMIT = 2

_BULLET_LINE = re.compile(r"^\s*[-•]\s+(.*\S)\s*$")
# The first lexical word: a run of letters, optionally hyphen-joined to
# another run of letters (e.g. the real, already-shipped "Pre-built"), so a
# compound opening word is treated as one token rather than being cut at the
# hyphen. Any leading non-letter character (bullet dash already stripped by
# _BULLET_LINE, but also markdown "**", stray whitespace, a leading number,
# ...) is simply skipped by the search -- this IS the "strip bullet
# punctuation" step; it never removes letters from inside the word itself.
_FIRST_WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")


def opening_verb(bullet: str) -> str:
    """Strip whitespace/bullet punctuation, extract the first lexical word,
    normalized to lowercase for case-insensitive comparison. Returns "" if
    the bullet has no alphabetic token at all (never raises)."""
    match = _FIRST_WORD.search(bullet)
    return match.group(0).lower() if match else ""


def extract_experience_and_project_bullets(resume_markdown: str) -> list:
    """Return every EXPERIENCE + PROJECT bullet's text (bullet marker
    stripped), in document order, from a rendered `resume_draft.md`-shaped
    markdown string. EDUCATION, SKILLS, heading/title/date lines, and
    anything outside the EXPERIENCE/PROJECTS run are never included."""
    in_scope = False
    bullets = []
    for line in resume_markdown.splitlines():
        stripped = line.strip()
        if stripped == EXPERIENCE_HEADER:
            in_scope = True
            continue
        if stripped == SKILLS_HEADER:
            in_scope = False
            continue
        if stripped == PROJECTS_HEADER:
            in_scope = True  # already true in practice; explicit for clarity
            continue
        if not in_scope:
            continue
        match = _BULLET_LINE.match(line)
        if match:
            bullets.append(match.group(1))
    return bullets


def count_opening_verbs(bullets: list, limit: int = DEFAULT_LIMIT) -> dict:
    """The core, pure counting function (Task 2). Given a flat list of
    bullet strings (already scoped to Experience + Projects by the caller),
    returns:

        {
          "limit": <int>,
          "bullet_count": <int>,
          "counts": {"built": 3, "developed": 2, ...},
          "repeated_over_limit": {"built": 3},           # count > limit only
          "bullets_by_verb": {"built": [<the 3 bullets>]},  # only for
                                                              # repeated_over_limit
        }

    No fuzzy/semantic matching, no stemming of unrelated words -- exact,
    case-insensitive comparison of each bullet's own first lexical word.
    """
    counts: dict = {}
    bullets_by_verb: dict = {}
    for bullet in bullets:
        verb = opening_verb(bullet)
        if not verb:
            continue
        counts[verb] = counts.get(verb, 0) + 1
        bullets_by_verb.setdefault(verb, []).append(bullet)
    repeated_over_limit = {verb: count for verb, count in counts.items() if count > limit}
    return {
        "limit": limit,
        "bullet_count": len(bullets),
        "counts": counts,
        "repeated_over_limit": repeated_over_limit,
        "bullets_by_verb": {verb: bullets_by_verb[verb] for verb in repeated_over_limit},
    }


def analyze_action_verbs(resume_markdown: str, limit: int = DEFAULT_LIMIT) -> dict:
    """`extract_experience_and_project_bullets` + `count_opening_verbs` in
    one call -- the normal entry point for a full rendered resume."""
    return count_opening_verbs(extract_experience_and_project_bullets(resume_markdown), limit=limit)


def analyze_action_verbs_from_file(path: Union[str, Path], limit: int = DEFAULT_LIMIT) -> dict:
    """Same as `analyze_action_verbs`, reading the rendered markdown
    (`resume_draft.md`) from disk. Never raises for a missing file -- a
    missing/unreadable resume simply analyzes as zero bullets, matching this
    module's own "never block on incomplete information, never fabricate"
    posture; the caller (`deterministic_pipeline.run_job_pipeline`) never
    calls this before the render step has already produced the file."""
    resolved = Path(path)
    if not resolved.exists():
        return analyze_action_verbs("", limit=limit)
    text = resolved.read_text(encoding="utf-8", errors="replace")
    return analyze_action_verbs(text, limit=limit)


def editable_bullet_texts(resume_payload: dict) -> set:
    """The exact, non-fuzzy set of bullet strings a correction round is ever
    permitted to edit for THIS job -- every `experience_alternate_bullets`
    entry and `selected_project.bullets` (see
    `correction_worker.build_correction_packet`'s `editable_fields`). Every
    other Experience/Project bullet on the rendered resume (any protected
    canonical bullet) is immutable and can never appear in this set."""
    editable = set()
    alternates = resume_payload.get("experience_alternate_bullets") or {}
    for bullets in alternates.values():
        for bullet in bullets or []:
            if isinstance(bullet, str):
                editable.add(bullet)
    selected_project = resume_payload.get("selected_project") or {}
    for bullet in selected_project.get("bullets") or []:
        if isinstance(bullet, str):
            editable.add(bullet)
    return editable


def classify_repeated_verbs(verb_report: dict, editable_bullets: set) -> dict:
    """Split `verb_report["repeated_over_limit"]` into:

      - `fixable`: at least one occurrence of the verb sits in an editable
        bullet, so a bounded correction round has a genuine chance of
        reducing this verb's count.
      - `unfixable`: EVERY occurrence is baked into protected/canonical
        content (never editable), so no correction round could ever change
        it -- requesting one anyway would only spend a scarce, bounded
        correction round on a change that structurally cannot happen.

    Membership is exact string equality against `editable_bullets` -- never
    a guess, never fuzzy. Each entry reports `total_count` (this verb's full
    Experience+Project count, matching `repeated_over_limit`),
    `fixed_canonical_count` (occurrences that cannot be changed), and
    `editable_bullets` (the actual bullet text(s) a correction round could
    rephrase), so a caller building a correction request can tell the
    worker exactly how far a repeated verb's count can realistically drop.
    """
    fixable, unfixable = {}, {}
    for verb, total_count in verb_report["repeated_over_limit"].items():
        bullets_for_verb = verb_report["bullets_by_verb"][verb]
        editable_for_verb = [b for b in bullets_for_verb if b in editable_bullets]
        entry = {
            "total_count": total_count,
            "fixed_canonical_count": total_count - len(editable_for_verb),
            "editable_bullets": editable_for_verb,
        }
        (fixable if editable_for_verb else unfixable)[verb] = entry
    return {"fixable": fixable, "unfixable": unfixable}
