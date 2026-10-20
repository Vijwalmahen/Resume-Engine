#!/usr/bin/env python3
"""
Compact correction worker.

Exactly ONE model request per correction round. The packet it receives
contains ONLY:

  - current resume payload
  - the exact validation/ATS failure
  - supported missing concepts
  - the fixed, deterministic selected-project binding (read-only fact)
  - allowed evidence for this job
  - protected canonical content (implicitly: it is simply absent from the
    editable-fields list, so the worker has no field to put it in even if
    it wanted to)
  - the exact fields it may modify

`editable_fields` is derived from the candidate's own tailorable-experience
owner_ids (context_slicing.correction_evidence_context) plus the bound
project's owner_id -- it never hardcodes any specific candidate's field
names.

This worker CANNOT reselect the project: the binding is handed to it as a
fixed fact, it has no tool to invoke the selector, and its schema does not
even expose a field for a different project. It also cannot run the
deterministic pipeline, touch other jobs, or explore the repository --
--tools "" (see llm_invoke.py) removes all of that categorically, not just
by instruction.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_invoke import LlmCallResult, invoke  # noqa: E402
import context_slicing  # noqa: E402

CORRECTION_SYSTEM_PROMPT = """You are a narrow, single-purpose resume correction worker.

INPUT: the current resume payload, the exact deterministic failure reason(s), the fixed
selected-project binding (read-only), allowed supported evidence, and the list of fields you
are permitted to touch.
OUTPUT: ONLY structured JSON matching the required schema. No prose, no markdown.

Rules:
- You may modify ONLY the fields listed in editable_fields. Nothing else.
- You may NEVER change selected_project.title's underlying project identity -- the bound
  project is fixed; you may only edit its truthful wording within the existing budgets.
- You may never fabricate a skill, technology, experience, or metric not present in
  allowed_evidence.
- If a supported_but_missing ATS concept is being repaired, use the exact concept term or an
  accepted alias, inserted into the correct dynamic field (not a protected/canonical field).
- If a requested repair cannot be made truthfully within these constraints, set
  correction_status.status = "CORRECTION_FAILED" and explain why in correction_status.reasons.
  Do not fabricate evidence to force a PASS.
- Do not touch correction_round; echo it back exactly as given.
- You have no tools. You cannot read any file, run any command, or invoke any other skill.
"""


def correction_output_json_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["resume_payload", "correction_status"],
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
            "correction_status": {
                "type": "object",
                "additionalProperties": False,
                "required": ["status", "correction_round"],
                "properties": {
                    "status": {"type": "string", "enum": ["CORRECTED", "CORRECTION_FAILED"]},
                    "correction_round": {"type": "integer"},
                    "reasons": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }


def build_correction_packet(
    job_key: str,
    current_resume_payload: dict,
    correction_request: dict,
    selected_project_binding: dict,
    allowed_evidence: dict,
) -> dict:
    """`allowed_evidence` (the caller's full candidate_context) is reduced
    to `context_slicing.correction_evidence_context` -- the only
    experience/project evidence a correction round could ever truthfully
    draw on for its fixed `editable_fields` (see that function's docstring).
    The caller's contract (pass the full candidate_context) is unchanged;
    the reduction happens here so run_batch.py needed no edit."""
    sliced_evidence = context_slicing.correction_evidence_context(
        allowed_evidence, selected_project_binding.get("title"),
    )
    tailorable_owner_ids = [e.get("id") for e in sliced_evidence.get("experience", []) if e.get("id")]
    editable_fields = [f"experience_alternate_bullets.{oid}" for oid in tailorable_owner_ids] + [
        "selected_project.technology_line", "selected_project.bullets",
    ]
    return {
        "job_key": job_key,
        "current_resume_payload": current_resume_payload,
        "correction_round": correction_request.get("correction_round"),
        "failure_reasons": correction_request.get("reasons", []),
        "failure_details": correction_request.get("details", {}),
        "selected_project_binding": selected_project_binding,
        "allowed_evidence": sliced_evidence,
        "editable_fields": editable_fields,
    }


def run_correction_call(
    packet: dict, caller=None, max_budget_usd: Optional[float] = None, effort: str = "low",
) -> LlmCallResult:
    """`effort="low"`: a correction round is a bounded, mechanical edit
    (repair one flagged ATS gap, or shorten flagged-overlong text)
    against an already-authored payload -- not open-ended authoring."""
    import json
    schema = correction_output_json_schema()
    user_prompt = (
        "CORRECTION PACKET (JSON):\n"
        + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
        + "\n\nWrite the corrected resume_payload and correction_status now, matching the schema exactly."
    )
    return invoke(
        CORRECTION_SYSTEM_PROMPT, user_prompt, schema,
        caller=caller, call_tag="correction", max_budget_usd=max_budget_usd, effort=effort,
    )
