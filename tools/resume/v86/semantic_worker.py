#!/usr/bin/env python3
"""
Lightweight, narrow semantic worker.

Exactly TWO model requests per normal (no-correction) job:

  call 1 (jd_requirements):  job posting + candidate context -> jd_requirements.json
  [deterministic selectable-project binding happens HERE, in Python, between
   the two calls -- see tools/resume/v86/run_batch.py. The semantic worker
   never sees, calls, or can override the selector.]
  call 2 (content):          jd_requirements + bound project + candidate
                              context -> resume_payload + cover_letter_payload
                              + strategy_status

No filesystem exploration, no shell, no subagents, no networking beyond the
one CLI call itself -- see tools/resume/v86/llm_invoke.py.

The resume/cover-letter JSON schemas below are keyed entirely by
`owner_id` strings that come from the candidate's own
master/runtime/runtime_resume_context.json (each experience/project entry
declares its own `id`). Nothing here is specific to any one candidate's
employer, university, or project names.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_invoke import LlmCallResult, invoke  # noqa: E402
import context_slicing  # noqa: E402

# ---------------------------------------------------------------------------
# Call 1: JD requirements extraction
# ---------------------------------------------------------------------------

REQUIREMENTS_SYSTEM_PROMPT = """You are a narrow, single-purpose JD-analysis worker for a resume pipeline.

INPUT: one job packet (job posting text + candidate context + truth rules), given in the user message as JSON.
OUTPUT: ONLY structured JSON matching the required schema. No prose, no markdown, no explanation.

Task: extract every explicit requirement from the job posting into atomic concepts.

Rules:
- Cover the ENTIRE job posting (required skills, preferred skills, core responsibilities,
  education, eligibility/location/availability, tools, frameworks, languages), not just a
  qualifications subsection.
- For "A or B or C" requirements: if candidate_context truthfully supports at least one
  alternative, represent exactly ONE supported atomic alternative (do not require all of them).
  If none are supported, keep the requirement present but unsupported.
- For "A and B" requirements, represent each independently-mandatory concept.
- Prefer atomic canonical terms (e.g. "Python", "PyTorch") over full JD sentences.
- Candidate evidence may influence which OR-alternative you choose; it must never cause you
  to omit a genuine JD requirement.
- Eligibility/availability/relocation/work-authorization are ELIGIBILITY category, not a hard skill.
- Do not fabricate a requirement the JD does not state.
- You have no tools. You cannot read any file other than what is already in this message.
"""


def run_requirements_call(
    packet: dict, caller=None, max_budget_usd: Optional[float] = None, effort: str = "low",
) -> LlmCallResult:
    """`effort="low"`: JD-concept extraction is a narrow, mechanical
    structured-extraction task -- it never authors resume/cover-letter
    prose and its only judgment call (which supported OR-alternative to
    pick) is a name-matching decision against the compact evidence context
    below, not open-ended reasoning."""
    schema = jd_requirements_json_schema()
    user_prompt = (
        "JOB PACKET (JSON):\n"
        + _compact(packet_for_requirements(packet))
        + "\n\nWrite jd_requirements.json content now, matching the schema exactly."
    )
    return invoke(
        REQUIREMENTS_SYSTEM_PROMPT, user_prompt, schema,
        caller=caller, call_tag="requirements", max_budget_usd=max_budget_usd, effort=effort,
    )


def packet_for_requirements(packet: dict) -> dict:
    """candidate_context is reduced to `context_slicing.
    requirements_evidence_context` -- the compact, truthful evidence view
    call 1 actually needs (see that function's docstring). job_posting_text
    and truth_rules are unchanged; content_budgets/resume_structure/schemas
    are excluded from this call."""
    return {
        "job_key": packet["job_key"],
        "job_posting_text": packet["job_posting_text"],
        "candidate_context": context_slicing.requirements_evidence_context(packet["candidate_context"]),
        "truth_rules": packet["truth_rules"],
    }


# ---------------------------------------------------------------------------
# Call 2: content generation (resume + cover letter + strategy status)
# ---------------------------------------------------------------------------

CONTENT_SYSTEM_PROMPT = """You are a narrow, single-purpose resume/cover-letter content worker.

INPUT: jd_requirements.json (already extracted), a DETERMINISTICALLY BOUND selectable project
(you may not change it), candidate context, protected canonical content, and content budgets.
OUTPUT: ONLY structured JSON matching the required schema. No prose, no markdown.

Rules:
- The resume structure is fixed: EDUCATION, PROFESSIONAL EXPERIENCE, PROJECTS, SKILLS. No summary.
- Composition-aware action-verb rule: the protected fixed layer already starts some bullets with
  particular verbs (see candidate_context for exactly which). Therefore dynamic
  experience_alternate_bullets and selected_project.bullets MUST NOT start with any verb that
  would push its total occurrence (across ALL Experience + Project bullets, protected and
  dynamic) past 2. Choose another truthful action verb supported by the same evidence; do not
  change facts, metrics, ownership, seniority, or scope merely to satisfy verb diversity.
- Canonical protected bullets (every experience/project entry in candidate_context with
  tailoring_mode == "protected") must be preserved EXACTLY as given; you may only choose their
  display ORDER via the *_bullet_orders fields (each a complete 0-based permutation of the
  right length for that entry's own bullet count).
- The static Skills section (skills.static_resume_lines) is fixed; never reorder or edit it,
  never inject JD keywords into it.
- You may author/edit ONLY: experience_alternate_bullets (one entry per experience owner_id
  marked tailoring_mode == "tailorable"), selected_project.title, .technology_line, .bullets,
  and the cover-letter paragraphs. Everything else is fixed input.
- selected_project MUST describe exactly the bound project given to you (see
  selected_project_binding.selected_runtime_name / selected_project_binding.selection_mode).
  Never substitute a different project even if it seems more relevant -- this decision is not yours.
- Respect the content budgets (max visible characters per field; ignore markdown ** markers when
  counting).
- Never fabricate a skill, technology, experience, or metric not present in candidate_context.
- The cover letter has one paragraph per tailorable/protected experience owner_id required by
  cover_letter_experience_owner_ids (see the packet), plus opening, project_paragraph, and
  closing. 45-90 words each, no repeated metric/claim across paragraphs.
- Start every Experience/Project bullet with a strong action verb, and vary opening verbs
  across the resume: avoid using the same opening action verb more than twice across
  Experience + Projects combined (this includes the protected canonical bullets already shown
  to you above). Never pick a different verb if doing so would change or exaggerate the
  factual meaning -- truthfulness always takes priority over verb diversity.
- If the job cannot be represented truthfully within these constraints, set
  strategy_status.status = "STRATEGY_FAILED" and explain why in strategy_status.reason.
  Otherwise set strategy_status.status = "STRATEGY_READY".
- You have no tools. You cannot read any file other than what is already in this message.
"""


def run_content_call(
    packet: dict,
    jd_requirements: dict,
    selected_project_binding: dict,
    caller=None,
    max_budget_usd: Optional[float] = None,
    effort: str = "medium",
) -> LlmCallResult:
    """`effort="medium"`: this is the one call that does deep reasoning
    (truthful evidence selection, JD-fit tailoring, prose quality) --
    nothing about its prompt, schema, or candidate_context slicing depends
    on any specific candidate's identity."""
    schema = content_output_json_schema()
    candidate_pool_names = {p["runtime_name"] for p in packet.get("allowed_selectable_projects", [])}
    candidate_context = context_slicing.content_call_candidate_context(
        packet["candidate_context"],
        selected_project_binding.get("selected_runtime_name"),
        candidate_pool_names,
    )
    user_prompt = (
        "JOB PACKET (JSON):\n"
        + _compact({
            "job_key": packet["job_key"],
            "candidate_context": candidate_context,
            "content_budgets": packet["content_budgets"],
            "resume_structure": packet["resume_structure"],
            "cover_letter_experience_owner_ids": [
                e.get("id")
                for e in candidate_context.get("experience", [])
                if e.get("id")
            ],
        })
        + "\n\nJD_REQUIREMENTS (already extracted, do not re-derive):\n"
        + _compact(jd_requirements)
        + "\n\nSELECTED_PROJECT_BINDING (fixed, not yours to change):\n"
        + _compact(selected_project_binding)
        + "\n\nWrite resume_payload, cover_letter_payload, and strategy_status now, matching the schema exactly."
    )
    return invoke(
        CONTENT_SYSTEM_PROMPT, user_prompt, schema,
        caller=caller, call_tag="content", max_budget_usd=max_budget_usd, effort=effort,
    )


def _compact(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# JSON Schemas (embedded here so callers/tests don't need extra file I/O)
# ---------------------------------------------------------------------------

def jd_requirements_json_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["job_key", "concepts"],
        "properties": {
            "job_key": {"type": "string"},
            "concepts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["term", "category", "required", "priority"],
                    "properties": {
                        "term": {"type": "string"},
                        "category": {
                            "type": "string",
                            "enum": [
                                "REQUIRED_HARD_SKILL", "PREFERRED_HARD_SKILL",
                                "CORE_RESPONSIBILITY", "SOFT_SKILL",
                                "EDUCATION_REQUIREMENT", "ELIGIBILITY",
                            ],
                        },
                        "required": {"type": "boolean"},
                        "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                        "aliases": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
        },
    }


def content_output_json_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["resume_payload", "cover_letter_payload", "strategy_status"],
        "properties": {
            "resume_payload": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "experience_bullet_orders", "experience_alternate_bullets",
                    "project_bullet_orders", "selected_project",
                ],
                "properties": {
                    "experience_bullet_orders": {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"type": "integer"}},
                    },
                    "experience_alternate_bullets": {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"type": "string"}},
                    },
                    "project_bullet_orders": {
                        "type": "object",
                        "additionalProperties": {"type": "array", "items": {"type": "integer"}},
                    },
                    "selected_project": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["owner_id", "title", "technology_line", "bullets"],
                        "properties": {
                            "owner_id": {"type": "string"},
                            "title": {"type": "string"},
                            "technology_line": {"type": "string"},
                            "bullets": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
            },
            "cover_letter_payload": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "company", "role", "salutation", "opening",
                    "experience_paragraphs", "project_paragraph", "closing",
                ],
                "properties": {
                    "company": {"type": "string"},
                    "role": {"type": "string"},
                    "salutation": {"type": "string"},
                    "opening": {"type": "string"},
                    "experience_paragraphs": {
                        "type": "object",
                        "additionalProperties": {"type": "string"},
                    },
                    "project_paragraph": {"type": "string"},
                    "closing": {"type": "string"},
                },
            },
            "strategy_status": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status"],
                "properties": {
                    "status": {"type": "string", "enum": ["STRATEGY_READY", "STRATEGY_FAILED"]},
                    "reason": {"type": "string"},
                },
            },
        },
    }
