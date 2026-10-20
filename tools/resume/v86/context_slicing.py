#!/usr/bin/env python3
"""
Deterministic, call-specific candidate-context slicing.

Every function here is PURE Python (no LLM, no I/O) and is strictly a
*subset* operation: each one returns a smaller (or, in the safe-fallback
case, byte-identical) view of the exact same validated
`shared_candidate_context.json` data every call already received.
Nothing here introduces a new fact, rewords an existing one, or changes
which protected canonical bullets exist -- it only removes fields a given
call structurally cannot use, per each call's own editable-fields
contract (semantic_worker.py / correction_worker.py).

Ownership/editability is read entirely from each entry's own
`tailoring_mode` field ("protected" vs "tailorable") -- never from a
hardcoded employer/project name. See docs/retrieval-and-grounding.md.
"""

from __future__ import annotations

from typing import Optional

# Skills explicitly labeled "not implemented" in the runtime candidate
# context are, by construction, never truthful evidence that a requirement
# is supported and never truthful material for authored resume/cover-letter
# content -- including this bucket anywhere risks the model treating a
# research-interest term as demonstrated experience. Excluding it is a
# truthfulness safeguard, not only a size reduction, and it does not shrink
# the set of REAL evidence available to any call.
_UNIMPLEMENTED_SKILLS_KEY = "research_interests_not_implemented"


def _flatten_skills(skills: dict) -> list:
    """Every real (implemented) skill/technology term in `skills`, flattened
    to a single deduplicated list, case-insensitive, first-seen casing kept.
    """
    out: list = []
    for line in (skills.get("static_resume_lines") or {}).values():
        out.extend(part.strip() for part in str(line).split(",") if part.strip())
    extended = skills.get("extended_for_dynamic_evidence") or {}
    for key, values in extended.items():
        if key == _UNIMPLEMENTED_SKILLS_KEY:
            continue
        out.extend(values or [])

    seen = set()
    deduped = []
    for term in out:
        norm = term.casefold()
        if norm not in seen:
            seen.add(norm)
            deduped.append(term)
    return deduped


def requirements_evidence_context(candidate_context: dict) -> dict:
    """CALL 1 (JD requirements extraction) input.

    Call 1's ONLY use of candidate evidence (semantic_worker.py
    REQUIREMENTS_SYSTEM_PROMPT) is deciding, for an "A or B or C" JD
    requirement, whether the candidate truthfully supports at least one
    atomic alternative -- a pure technology/skill/degree name-matching
    decision. It never authors resume/cover-letter text and never touches a
    protected canonical bullet. Every technology/skill term, and every
    education credential, that was reachable through the FULL
    candidate_context remains reachable here in full; what is dropped is
    exclusively content that could only ever matter for AUTHORING content
    (canonical bullet wording, ownership/company-context narrative,
    cautions, metrics phrasing, contact info, resume-formatting
    truth_constraints) -- none of which can change a truthful
    support/no-support determination.
    """
    education = [
        {"institution": e.get("institution"), "degree": e.get("degree")}
        for e in candidate_context.get("education", [])
    ]
    experience = [
        {"employer": e.get("employer"), "title": e.get("title"), "technologies": e.get("technologies", [])}
        for e in candidate_context.get("experience", [])
    ]
    projects = [
        {"name": p.get("name"), "technologies": p.get("technologies", [])}
        for p in candidate_context.get("projects", [])
    ]
    return {
        "education": education,
        "experience": experience,
        "projects": projects,
        "skills": _flatten_skills(candidate_context.get("skills", {})),
    }


def content_call_candidate_context(
    candidate_context: dict,
    selected_runtime_name: Optional[str],
    candidate_pool_names: set,
) -> dict:
    """CALL 2 (resume + cover-letter content) input.

    The deterministic selectable-project selector
    (tools/resume/v8/select_project.py, invoked between call 1 and call 2
    -- see run_batch.py) runs in one of two modes:

      * a BINDING_* mode, which pins `selected_runtime_name` to exactly one
        of the candidate pool's projects -- call 2 is then FORBIDDEN from
        describing any other one (semantic_worker.py CONTENT_SYSTEM_PROMPT:
        "selected_project MUST describe exactly the bound project... Never
        substitute a different project"), so the other pool projects are
        pure duplication once that binding exists.
      * MODEL_CHOICE mode (`selected_runtime_name` is None/empty), where the
        selector could not deterministically pin one project and call 2
        itself must choose among the full candidate pool -- in this mode NO
        filtering happens; every candidate-pool project is passed through
        completely unchanged.

    Required (protected) projects are never in `candidate_pool_names`
    (they are not selectable-project candidates at all) and every other
    candidate_context field is always passed through unchanged in both
    modes.
    """
    if not selected_runtime_name:
        return candidate_context  # MODEL_CHOICE: no safe reduction possible

    ctx = dict(candidate_context)
    ctx["projects"] = [
        p for p in candidate_context.get("projects", [])
        if p.get("name") not in candidate_pool_names or p.get("name") == selected_runtime_name
    ]
    return ctx


def correction_evidence_context(candidate_context: dict, bound_project_runtime_name: Optional[str]) -> dict:
    """Correction-round input.

    correction_worker.py's `editable_fields` is a fixed, hardcoded list:
    every tailorable experience's `experience_alternate_bullets` entry,
    plus `selected_project.technology_line` / `selected_project.bullets`
    -- nothing else can be written by a correction round. The only
    experience entries a correction could truthfully draw on are
    therefore the ones marked `tailoring_mode == "tailorable"` (as
    opposed to `"protected"`); the only project evidence it could
    truthfully draw on for `selected_project.*` is the ALREADY-BOUND
    project itself. Sourcing correction content from protected canonical
    bullets or from an unbound selectable-project candidate would not
    just waste tokens -- it would misattribute evidence to the wrong
    experience/project, so this is a truthfulness improvement as well as
    a size reduction. Skills (both static and dynamic-evidence, minus the
    "not implemented" bucket) remain fully available for exact concept
    term/alias wording.
    """
    experience = [
        e for e in candidate_context.get("experience", [])
        if e.get("tailoring_mode") == "tailorable"
    ]
    projects = [
        p for p in candidate_context.get("projects", [])
        if bound_project_runtime_name and p.get("name") == bound_project_runtime_name
    ]
    skills = candidate_context.get("skills", {})
    extended = skills.get("extended_for_dynamic_evidence") or {}
    trimmed_extended = {k: v for k, v in extended.items() if k != _UNIMPLEMENTED_SKILLS_KEY}
    return {
        "experience": experience,
        "selected_project_evidence": projects,
        "skills": {
            "static_resume_lines": skills.get("static_resume_lines", {}),
            "extended_for_dynamic_evidence": trimmed_extended,
        },
    }
