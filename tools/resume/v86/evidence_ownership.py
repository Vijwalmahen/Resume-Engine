#!/usr/bin/env python3
"""
Deterministic evidence-ownership resolution.

This module answers exactly one narrow question: for a given ATS
`evidence_hits` entry (as produced by the frozen `tools/ats/preflight.py`
`find_evidence`, and reused unmodified by `ats_preflight_v86.py`), which
master-evidence experience/project OWNS that hit, and is that owner's
content actually reachable through one of THIS job's `editable_fields`
(`correction_worker.build_correction_packet`)?

Editability is entirely DATA-DRIVEN, never identity-driven:
  - An experience entry is editable if and only if its own
    `tailoring_mode` field is `"tailorable"` (as opposed to `"protected"`).
    No employer name is ever hardcoded here.
  - A project entry is editable if and only if it is THIS job's
    already-bound selectable project (by exact `id` match against
    `bound_project_id` -- never a substring/fuzzy match, so an unrelated
    project sharing a word never counts). A `"required"`/protected
    project is never editable, regardless of wording similarity.

Ownership is resolved STRUCTURALLY from the evidence hit's own `source`
JSON-path and `file` (exactly the fields `find_evidence` already
attaches) -- never by string-matching resume text, never by guessing.
`evidence_hits[]` for a term can span an experience entry
(`$.experience[i]...`) or a project entry (`$.projects[i]...`); the owner
is read directly off that entry's own `id` field (falling back to
`employer`/`name` only if `id` is absent, for resilience against older
evidence documents).

If ANY hit's owner cannot be resolved (malformed source path, missing
file, evidence restructured in a future job), this module refuses to
call the concept "unplaceable" -- it reports `"unknown"`, and every
caller in this pass treats `"unknown"` exactly like `"placeable"` (i.e.
it still gets a correction round). Unplaceability must be PROVEN from
ownership metadata; the safe default on any doubt is to keep trying,
never to silently waive a required concept.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional


_TOP_LEVEL_HIT_RE = re.compile(r"^\$\.(?P<container>experience|projects)\[(?P<index>\d+)\]")


@lru_cache(maxsize=None)
def _load_evidence_document(file_path: str) -> dict:
    """Parse one evidence JSON file (cached per absolute path -- a single
    ATS run/replay may resolve ownership for dozens of hits against the
    same batch-local evidence file)."""
    try:
        return json.loads(Path(file_path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def resolve_owner_entry(hit: dict) -> Optional[dict]:
    """Given one `evidence_hits[]` entry (`{"file": ..., "source": ...}`),
    return the owning experience/project entry itself (so callers can read
    its `id` and `tailoring_mode`), or None if the source path does not
    resolve to a known top-level `experience[i]`/`projects[i]` entry.
    Only ever reads the exact evidence file the hit itself already names
    -- never a different job's or a different batch's evidence."""
    source = hit.get("source") or ""
    file_path = hit.get("file")
    if not source or not file_path:
        return None

    match = _TOP_LEVEL_HIT_RE.match(source)
    if not match:
        return None

    document = _load_evidence_document(str(file_path))
    items = document.get(match.group("container"))
    if not isinstance(items, list):
        return None

    index = int(match.group("index"))
    if index >= len(items) or not isinstance(items[index], dict):
        return None

    entry = items[index]
    owner_id = entry.get("id") or entry.get("employer") or entry.get("name")
    if not owner_id or not str(owner_id).strip():
        return None

    return {
        "container": match.group("container"),
        "owner_id": str(owner_id).strip(),
        "tailoring_mode": entry.get("tailoring_mode"),
    }


def resolve_owner(hit: dict) -> Optional[str]:
    """Backwards-compatible convenience wrapper returning just the
    resolved owner_id."""
    entry = resolve_owner_entry(hit)
    return entry["owner_id"] if entry else None


def is_editable_owner(owner_entry: Optional[dict], bound_project_id: Optional[str] = None) -> bool:
    """True only for the two kinds of owner a job could truthfully edit
    right now: an experience entry marked `tailoring_mode == "tailorable"`,
    or THIS job's own bound selectable project (by exact id match)."""
    if not owner_entry:
        return False

    if owner_entry["container"] == "experience":
        return owner_entry.get("tailoring_mode") == "tailorable"

    if owner_entry["container"] == "projects":
        # Defense in depth: even if a caller somehow passed a `required`
        # (protected) project's own id as `bound_project_id`, it is never
        # editable -- only a project whose OWN data marks it tailorable
        # (which, by construction, is the only kind select_project.py ever
        # binds) can be edited.
        return (
            bool(bound_project_id)
            and owner_entry["owner_id"] == bound_project_id
            and owner_entry.get("tailoring_mode") == "tailorable"
        )

    return False


def classify_missing_required(evidence_hits: list, bound_project_id: Optional[str] = None) -> str:
    """Classify one REQUIRED, `supported_but_missing` ATS concept's
    placeability from its own `evidence_hits`, as returned by
    `ats_preflight_v86.evaluate`'s `concepts[].evidence_hits`.

    Returns:
      "placeable"   -- at least one evidence hit's owner is editable
                       (a tailorable experience, or the bound selectable
                       project). A correction round asking for this
                       concept can succeed truthfully.
      "unplaceable" -- EVERY evidence hit's owner was positively resolved,
                       and NONE is editable. Proven, not assumed: a
                       correction round could never truthfully place this
                       concept without either fabricating evidence or
                       rewriting protected canonical content, so requesting
                       one would only burn a bounded correction round for a
                       guaranteed CORRECTION_FAILED.
      "unknown"     -- no evidence hits, or at least one hit's owner could
                       not be resolved. Callers treat this exactly like
                       "placeable" (never silently waive a required
                       concept on incomplete information).
    """
    if not evidence_hits:
        return "unknown"

    owners = []
    for hit in evidence_hits:
        owner_entry = resolve_owner_entry(hit)
        if owner_entry is None:
            return "unknown"
        owners.append(owner_entry)

    if any(is_editable_owner(owner, bound_project_id) for owner in owners):
        return "placeable"
    return "unplaceable"
