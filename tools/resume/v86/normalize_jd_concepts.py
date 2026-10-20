#!/usr/bin/env python3
"""
V8.6.1 -- Fix 1: deterministic JD concept de-duplication.

A computer-vision job's semantic call 1 can produce two concepts both named
"OpenCV" in jd_requirements.json, which `validate_strategy_contract_v86.py`
correctly (but terminally) rejects as `STRATEGY_CONTRACT_FAILED`. Two
duplicate concept entries are not an architecture problem or a
semantic-worker problem worth spending another Claude call over -- they are
a deterministic normalization gap. This module closes it.

Called (idempotently) from two places:
  1. `tools/resume/v86/run_batch.py`, immediately after semantic call 1
     returns and BEFORE `jd_requirements.json` is written / before the
     deterministic third-project selector ever sees it (Section: "AFTER
     semantic call 1 and BEFORE contract validation/project selection").
  2. Defensively, at the top of `deterministic_pipeline.run_job_pipeline`,
     for any caller that constructs `jd_requirements.json` directly
     (offline tests, a future correction path) without going through
     `run_batch.py`'s call site.

Merge policy (Section "FIX 1"):
  - Two concepts are merge-eligible ONLY if they are individually
    well-formed (have all four required keys with valid types/enums) AND
    share the exact same case-insensitive `term` string. Anything else
    (malformed entries, or near-duplicates with genuinely different literal
    term strings, e.g. "OpenCV" vs "OpenCV2") is left completely untouched
    -- "never silently remove two genuinely distinct concepts". The strict
    duplicate-term check in `validate_strategy_contract_v86.py` remains the
    safety net for anything this function does not (and should not) merge.
  - required:  True if ANY duplicate says required=True.
  - priority:  the strongest of the duplicates' priorities
              (critical > high > medium > low).
  - category:  the "best" (most-actionable) category among the duplicates,
              using the same relative ordering `tools/ats/preflight.py`'s
              `priority_value` already treats as more important:
              REQUIRED_HARD_SKILL > CORE_RESPONSIBILITY >
              PREFERRED_HARD_SKILL > SOFT_SKILL > EDUCATION_REQUIREMENT >
              ELIGIBILITY.
  - aliases:  the union of every duplicate's aliases, case-insensitively
              deduplicated, first-seen order preserved.
  - term:     whichever duplicate's term string carries any uppercase
              letter (a likely proper-noun/brand casing, e.g. "OpenCV"),
              else the first-seen term as-is.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

CATEGORY_RANK = {
    "REQUIRED_HARD_SKILL": 0,
    "CORE_RESPONSIBILITY": 1,
    "PREFERRED_HARD_SKILL": 2,
    "SOFT_SKILL": 3,
    "EDUCATION_REQUIREMENT": 4,
    "ELIGIBILITY": 5,
}
PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
REQUIRED_CONCEPT_KEYS = {"term", "category", "required", "priority"}


def _is_well_formed(concept) -> bool:
    return (
        isinstance(concept, dict)
        and REQUIRED_CONCEPT_KEYS <= set(concept)
        and isinstance(concept.get("term"), str)
        and bool(concept.get("term", "").strip())
        and concept.get("category") in CATEGORY_RANK
        and isinstance(concept.get("required"), bool)
        and concept.get("priority") in PRIORITY_RANK
    )


def _merge_aliases(members: list) -> list:
    seen = set()
    merged = []
    for member in members:
        for alias in (member.get("aliases") or []):
            if not isinstance(alias, str) or not alias.strip():
                continue
            key = alias.strip().casefold()
            if key not in seen:
                seen.add(key)
                merged.append(alias.strip())
    return merged


def _canonical_term(members: list) -> str:
    for member in members:
        term = str(member.get("term", "")).strip()
        if any(ch.isupper() for ch in term):
            return term
    return str(members[0].get("term", "")).strip()


def _merge_group(members: list) -> dict:
    merged = dict(members[0])
    merged["term"] = _canonical_term(members)
    merged["required"] = any(bool(m.get("required")) for m in members)
    merged["priority"] = min(
        (m.get("priority") for m in members),
        key=lambda p: PRIORITY_RANK.get(p, 99),
    )
    merged["category"] = min(
        (m.get("category") for m in members),
        key=lambda c: CATEGORY_RANK.get(c, 99),
    )
    aliases = _merge_aliases(members)
    if aliases:
        merged["aliases"] = aliases
    else:
        merged.pop("aliases", None)
    return merged


def normalize_concepts(concepts) -> tuple[list, list]:
    """Returns (normalized_concepts, merge_notes). Never raises; anything
    that is not a merge-eligible exact-casefold duplicate is passed through
    unchanged, in original relative order."""
    if not isinstance(concepts, list):
        return concepts, []

    groups: dict[str, list] = {}
    order: list[str] = []
    passthrough: list = []

    for concept in concepts:
        if not _is_well_formed(concept):
            passthrough.append(concept)
            continue
        key = concept["term"].strip().casefold()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(concept)

    notes = []
    result = []
    for key in order:
        members = groups[key]
        if len(members) == 1:
            result.append(members[0])
            continue
        merged = _merge_group(members)
        notes.append({
            "canonical_term": merged["term"],
            "merged_count": len(members),
            "source_terms": [m["term"] for m in members],
        })
        result.append(merged)

    result.extend(passthrough)
    return result, notes


def normalize_jd_requirements(jd_requirements: dict) -> tuple[dict, list]:
    """Returns (possibly-new dict, merge_notes). If nothing was merged, the
    exact same object is returned (no notes) -- callers can use `bool(notes)`
    to decide whether a rewrite is needed."""
    if not isinstance(jd_requirements, dict):
        return jd_requirements, []
    normalized, notes = normalize_concepts(jd_requirements.get("concepts"))
    if not notes:
        return jd_requirements, []
    out = dict(jd_requirements)
    out["concepts"] = normalized
    return out, notes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--output", help="defaults to overwriting --requirements in place")
    args = parser.parse_args()

    path = Path(args.requirements)
    data = json.loads(path.read_text(encoding="utf-8"))
    normalized, notes = normalize_jd_requirements(data)

    out_path = Path(args.output) if args.output else path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(normalized, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"merged": notes}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
