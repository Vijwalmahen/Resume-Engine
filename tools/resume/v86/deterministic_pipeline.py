#!/usr/bin/env python3
"""
Job-local deterministic pipeline.

This is an adapted, job-local, parallel-safe copy of the control flow in
tools/resume/v8/run_local_resume_pipeline.py. It invokes the same
per-step scripts (select_project.py, validate_project_selection.py,
normalize_resume_payload.py, tools/resume/payload_preflight.py,
tools/resume/render_resume.py, tools/ats/preflight.py,
tools/resume/qa_resume.py, tools/resume/density_guard.py,
tools/resume/v8/render_cover_letter.py, tools/resume/word_to_pdf.py) with
job-local --job-dir/--payload/--output arguments -- exactly the same CLI
contract the v8 pipeline already uses, just pointed at each job's own
directory instead of being run one-job-after-another in a single
sequential process.

The ONE structural difference from run_local_resume_pipeline.py is
validate_strategy_contract.py: this pipeline uses the adapted
validate_strategy_contract_v86.py (see that file's docstring) because the
v8 version hardcodes the v8 skill's own identity strings.

Concurrency safety already present in the invoked scripts and reused as-is:
  - render_resume.py / word_to_pdf.py share a single fcntl-based
    Word-conversion lock (~/Documents/ResumeRenderer/.word-conversion.lock).
    Its own code comment states it exists "for concurrent job processing".
    No new lock is added around it.
  - Every other step here reads/writes ONLY inside the job's own directory
    (or a batch-local, read-only evidence copy), so jobs never collide.

Unplaceable-evidence safety: `run_job_pipeline`'s ATS handling splits
`missing_supported_required_terms` into `placeable`/`unplaceable` subsets
via `evidence_ownership.classify_missing_required` before deciding whether
to spend a bounded correction round -- see that module's docstring.

Action-verb diversity: `run_job_pipeline` also runs
`action_verb_diversity.analyze_action_verbs_from_file` against the
just-rendered `resume_draft.md` and bundles a repeated (>2) opening verb
that has at least one editable occurrence into the same correction_reasons/
details mechanism as ATS/DENSITY/MULTIPAGE -- a STYLE-only finding, never an
ATS/evidence/company/project-selection change. A repeated verb baked
entirely into protected canonical bullets is never requested from
correction (nothing editable to change) and is reported separately as
`result["action_verb_unfixable"]`. The ONE difference from ATS/DENSITY/
MULTIPAGE: when action-verb diversity is the ONLY remaining finding once
the 2-round correction budget is exhausted, the job is never hard-failed
(`CORRECTION_LIMIT_EXCEEDED`) -- it finishes `RESUME_READY_WITH_WARNINGS`
with `result["action_verb_warning"]` set instead. See
action_verb_diversity.py's own docstring.

Resume-quality report: on the SUCCESS path only (every gate decision
already made), `run_job_pipeline` writes one job-local, ADVISORY
`resume_quality_report.json` via `resume_quality_report.generate` and
surfaces a small, candidate-content-free summary as
`result["quality_report"]`. It consolidates the PDF round-trip/reading-
order diagnostics, an evidence-aware quantifiable-impact audit, a
short-bullet advisory, an education-aware timeline check, and the
ALREADY-COMPUTED action-verb and QA/density results (reused verbatim,
never recomputed). It cannot change a status, add a correction round, or
cause a model call. See resume_quality_report.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import action_verb_diversity  # noqa: E402
import evidence_ownership  # noqa: E402
import resume_quality_report  # noqa: E402
from clear_winner_safeguard import apply_clear_winner_safeguard  # noqa: E402
from common import ROOT, Timer, load_json, python_cmd, run_step, write_json  # noqa: E402
from normalize_cover_letter_identity import backfill_cover_letter_company  # noqa: E402
from normalize_jd_concepts import normalize_jd_requirements  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import template_layout as layout_lib  # noqa: E402

SUCCESS_STATUSES = ("RESUME_READY", "RESUME_READY_WITH_WARNINGS")

V86_SKILL_NAME = "jobapp-resume-v86-semantic"
V86_SKILL_IDENTITY = "JOBAPP_RESUME_V86_SEMANTIC_WORKER"

CORRECTION_ROUND_LIMIT = 2

DEFAULT_LAYOUT_PATH = ROOT / "reference" / "resume_template_layout.json"


def previous_round(job_dir: Path) -> int:
    p = job_dir / "v8_correction_status.json"
    if not p.exists():
        return 0
    try:
        return int(load_json(p).get("correction_round", 0))
    except Exception:
        return 0


def write_correction_request(job_dir: Path, reasons: list, details: dict) -> Path:
    requested_round = previous_round(job_dir) + 1
    request = {
        "job": job_dir.name,
        "correction_round": requested_round,
        "reasons": reasons,
        "details": details,
        "allowed_scope": "dynamic_resume_content_only",
    }
    path = job_dir / "correction_request.json"
    write_json(path, request)
    # ALSO preserve a per-round-numbered snapshot, purely additive (never
    # read by any pipeline step -- `correction_request.json` itself
    # remains the one file run_batch.py/deterministic_pipeline.py read).
    # A job with >1 correction round would otherwise permanently lose
    # round 1's exact trigger/reason the moment round 2 is written.
    # Diagnostic tooling (`benchmark_report.py`) reads these snapshots
    # when present.
    write_json(job_dir / f"correction_request.round{requested_round}.json", request)
    return path


# --- fresh-attempt state isolation ------------------------------------------
# `output/<job_key>/` (`job_dir`/`out_dir`) is a path keyed ONLY by job name,
# not by batch id -- it is silently reused whenever the same job_key is
# reprocessed by a later, independent batch run (a job that failed, or was
# re-submitted). ANY on-disk file this pipeline treats as "prior decision
# state to reuse/count" (as opposed to "this run's own fresh output, written
# and consumed within the same call") is therefore at risk of leaking a
# PRIOR, unrelated run's decision into a brand-new attempt.
#
# Two groups of attempt-local control artifacts, split because they become
# "legitimately fresh for this attempt" at DIFFERENT points in the call
# order (see `run_batch.py.process_job`: ... -> ensure_project_selection ->
# semantic call 2 -> write_strategy_files -> run_job_pipeline -> [correction
# round(s)] ...):
#   - CORRECTION_ATTEMPT_ARTIFACTS never have a legitimate reason to exist
#     before `write_strategy_files` runs (nothing in this pipeline requests
#     or applies a correction before a fresh resume_payload/cover_letter_
#     payload exist) -- safe to reset at EITHER the true start of an
#     attempt, or defensively again inside `write_strategy_files` itself.
#   - PROJECT_SELECTION_ATTEMPT_ARTIFACTS, by contrast, are ALWAYS written
#     BEFORE `write_strategy_files` runs in every valid call flow (selection
#     must happen before semantic call 2, which needs it as an input) --
#     resetting them again inside `write_strategy_files` would destroy this
#     SAME attempt's own already-legitimate selection, not a stale one. They
#     may only be reset at the true start of a fresh attempt, before
#     `ensure_project_selection` is ever invoked.
# `ATTEMPT_LOCAL_CONTROL_ARTIFACTS` (the union) is the one, centralized list
# of job-directory filenames this pipeline ever treats as "prior-attempt
# decision state" (reused-if-present, or counted as this-run's history).
# Every other file this pipeline reads from `job_dir` is either this SAME
# run's own freshly (re)written output, read back only within the same
# `run_job_pipeline` call that just wrote it (safe), or an immutable/
# read-only input (`evidence_root`, the original job posting file).
CORRECTION_ATTEMPT_ARTIFACTS = (
    "v8_correction_status.json",
    "correction_request.json",
    "density_guard.json",
)
PROJECT_SELECTION_ATTEMPT_ARTIFACTS = (
    "project_selection.json",
    "v8_project_selection_validation.json",
)
ATTEMPT_LOCAL_CONTROL_ARTIFACTS = CORRECTION_ATTEMPT_ARTIFACTS + PROJECT_SELECTION_ATTEMPT_ARTIFACTS


def reset_attempt_local_state(job_dir: Path, *, include_project_selection: bool = True) -> list:
    """The ONE centralized fresh-attempt initializer. Removes every
    attempt-local control artifact that might otherwise survive from an
    earlier, unrelated batch run of the same `job_key` and be silently
    mistaken for this attempt's own decision history. Never touches
    immutable/input files (the job posting itself, `evidence_root`) or this
    run's own not-yet-written output.

    `include_project_selection=True` (the default -- use this at the TRUE
    start of a fresh attempt, before `ensure_project_selection` is ever
    invoked, e.g. `run_batch.py`'s primary call site) additionally removes
    `PROJECT_SELECTION_ATTEMPT_ARTIFACTS`. Pass `False` from any call site
    that is known to run AFTER project selection has already legitimately
    happened for THIS SAME attempt (`write_strategy_files`'s own
    defensive call) -- otherwise this would destroy this attempt's own
    correct selection, not a stale one.

    Idempotent and safe to call multiple times per attempt (a no-op on
    repeat calls with the same or narrower scope) and safe on a job_dir that
    has never been used before (a no-op there too). Returns the list of
    filenames actually removed, for callers that want to log/report a reset
    (e.g. diagnostics); never raises if a listed file is simply absent.
    """
    removed = []
    for stale in sorted(job_dir.glob("correction_request.round*.json")):
        stale.unlink()
        removed.append(stale.name)
    names = CORRECTION_ATTEMPT_ARTIFACTS
    if include_project_selection:
        names = names + PROJECT_SELECTION_ATTEMPT_ARTIFACTS
    for name in names:
        stale_path = job_dir / name
        if stale_path.exists():
            stale_path.unlink()
            removed.append(name)
    return removed


def write_strategy_files(
    job_dir: Path,
    job_file: str,
    job_key: str,
    jd_requirements: dict,
    resume_payload: dict,
    cover_letter_payload: dict,
    selected_project_owner_id: str,
    semantic_corrections: int = 0,
) -> None:
    """Adapt the two semantic-call outputs into the exact per-job artifact
    files the deterministic pipeline (and its sub-scripts) expect."""
    job_dir.mkdir(parents=True, exist_ok=True)

    # Defensive/idempotent fresh-attempt reset (see `reset_attempt_local_
    # state`'s docstring). The PRIMARY call site is `run_batch.py`'s
    # `process_job`, immediately after `out_dir.mkdir()` and BEFORE
    # `ensure_project_selection` is ever invoked. `write_strategy_files`
    # always runs AFTER project selection has already legitimately
    # happened for THIS SAME attempt (it is called only once call 2 --
    # which itself requires the selection as an input -- has already
    # returned) -- so `include_project_selection=False` here: resetting
    # the project-selection files at this point would destroy this
    # attempt's own correct, just-computed selection, not a stale one.
    # Correction/density bookkeeping, by contrast, never legitimately
    # exists yet at this point in ANY call flow, so resetting it here
    # remains a safe, pure no-op in the normal batch flow and a real
    # defensive reset for any caller that writes strategy files directly
    # without going through `run_batch.py`'s primary call site (offline
    # tests, a future correction path).
    reset_attempt_local_state(job_dir, include_project_selection=False)

    write_json(job_dir / "jd_requirements.json", jd_requirements)
    write_json(job_dir / "resume_payload.json", resume_payload)
    write_json(job_dir / "cover_letter_payload.json", cover_letter_payload)
    write_json(job_dir / "v8_strategy_status.json", {
        "status": "STRATEGY_READY",
        "skill_name": V86_SKILL_NAME,
        "skill_identity": V86_SKILL_IDENTITY,
        "job_file": job_file,
        "job_key": job_key,
        "selected_project_bound": selected_project_owner_id,
        "semantic_corrections": semantic_corrections,
    })


def ensure_project_selection(job_dir: Path, requirements_path: Path) -> dict:
    """Deterministic-only step (no LLM). Idempotent: if a selection already
    exists for this job (written earlier, between semantic call 1 and call
    2), it is reused rather than recomputed.

    Immediately after the selector produces a fresh report,
    `clear_winner_safeguard.apply_clear_winner_safeguard` is applied exactly
    once (see that module's docstring) -- a pure post-processing step over
    the selector's own `rankings`, never a re-implementation of its evidence
    scoring. When the selector already bound a project
    (`BINDING_REQUIRED_COVERAGE`) or found no clear MODEL_CHOICE winner, the
    report is unchanged (identity-preserved, no rewrite). Only when a clear
    winner is found is the on-disk report rewritten, so the amended
    selection_mode/selected_* fields are what call 2, the project-selection
    guard, and this idempotent reuse path all subsequently see."""
    selection_path = job_dir / "project_selection.json"
    if selection_path.exists():
        return {"ok": True, "path": selection_path, "data": load_json(selection_path)}

    result = run_step(python_cmd(
        ROOT / "tools/resume/v8/select_project.py",
        "--requirements", requirements_path,
        "--output", selection_path,
    ))
    if not result.ok or not selection_path.exists():
        return {"ok": False, "path": selection_path, "data": None, "stderr": result.stderr}

    selection_data = load_json(selection_path)
    safeguarded = apply_clear_winner_safeguard(selection_data)
    if safeguarded is not selection_data:
        write_json(selection_path, safeguarded)
        selection_data = safeguarded
    return {"ok": True, "path": selection_path, "data": selection_data}


def split_missing_required_by_placeability(
    ats: dict, bound_project_id: Optional[str],
) -> tuple[list, list]:
    """Given one job's `ats_preflight.json` report (already loaded as a
    dict) and its bound selectable-project owner_id, split
    `missing_supported_required_terms` into `(placeable, unplaceable)`.

    A term is `unplaceable` only when `evidence_ownership.
    classify_missing_required` PROVES every one of its evidence hits is
    owned by protected/non-editable content; anything it cannot prove
    (`"unknown"`, including a term with no evidence_hits recorded) is kept
    in `placeable` so a required concept is never silently waived on
    incomplete information. Pure function of its arguments -- no I/O, no
    subprocess, safe to unit-test directly against a saved
    `ats_preflight.json` fixture."""
    concept_lookup = {c.get("term"): c for c in ats.get("concepts", [])}
    placeable, unplaceable = [], []
    for term in ats.get("missing_supported_required_terms", []):
        hits = concept_lookup.get(term, {}).get("evidence_hits", [])
        classification = evidence_ownership.classify_missing_required(hits, bound_project_id)
        (unplaceable if classification == "unplaceable" else placeable).append(term)
    return placeable, unplaceable


def run_job_pipeline(
    job_dir: Path, evidence_root: Path, job_key: str, layout_path: Path = DEFAULT_LAYOUT_PATH,
) -> dict:
    """Job-local equivalent of run_local_resume_pipeline.process(). Never
    raises for an ordinary job failure; returns a status dict, exactly like
    the v8 pipeline does, so one job's failure cannot break others.
    """
    result = {"job": job_key, "status": None, "correction_request": None, "timings": {}}
    timings = result["timings"]

    strategy = job_dir / "v8_strategy_status.json"
    payload = job_dir / "resume_payload.json"
    requirements = job_dir / "jd_requirements.json"

    missing = [str(p) for p in (strategy, payload, requirements) if not p.exists()]
    if missing:
        result["status"] = "MISSING_STRATEGY_FILES"
        result["missing"] = missing
        return result

    # --- Deterministic JD concept de-duplication -------------------------
    # Defensive/idempotent here (the primary call site is run_batch.py,
    # immediately after semantic call 1, BEFORE this function or the
    # project selector ever runs -- see normalize_jd_concepts.py's
    # docstring). Re-normalizing an already-normalized file is a no-op.
    jd_data = load_json(requirements)
    normalized_jd, merge_notes = normalize_jd_requirements(jd_data)
    if merge_notes:
        write_json(requirements, normalized_jd)
        result["jd_concept_merges"] = merge_notes

    # --- Deterministic cover_letter_payload.company backfill --------------
    # Defensive/idempotent here (the primary call site is run_batch.py,
    # immediately after semantic call 2 returns, BEFORE this function or the
    # contract validator ever see the file -- see
    # normalize_cover_letter_identity.py's docstring). Re-applying to an
    # already-non-empty, non-placeholder company is a no-op.
    cover_path = job_dir / "cover_letter_payload.json"
    if cover_path.exists():
        job_file_for_posting = None
        if strategy.exists():
            job_file_for_posting = load_json(strategy).get("job_file")
        posting_text = ""
        if job_file_for_posting and Path(job_file_for_posting).exists():
            posting_text = Path(job_file_for_posting).read_text(encoding="utf-8", errors="replace")
        cover_data = load_json(cover_path)
        backfilled_cover, backfilled_company = backfill_cover_letter_company(cover_data, posting_text)
        if backfilled_company:
            write_json(cover_path, backfilled_cover)
            result["cover_letter_company_backfilled"] = backfilled_company

    # --- strict strategy contract validation (adapted, not v8 file) ---
    contract_report = job_dir / "v8_contract_validation.json"
    with Timer() as t:
        run_step(python_cmd(
            ROOT / "tools/resume/v86/validate_strategy_contract_v86.py",
            "--job-dir", job_dir, "--report", contract_report,
        ))
    timings["contract_validation"] = t.as_dict("contract_validation")

    if not contract_report.exists():
        result["status"] = "STRATEGY_CONTRACT_FAILED"
        return result
    contract = load_json(contract_report)
    if contract.get("status") != "PASS":
        result["status"] = "STRATEGY_CONTRACT_FAILED"
        result["contract_failures"] = contract.get("failures", [])
        return result

    strategy_data = load_json(strategy)
    if strategy_data.get("status") != "STRATEGY_READY":
        result["status"] = "STRATEGY_NOT_READY"
        return result

    # --- deterministic selectable-project binding + guard ---
    with Timer() as t:
        selector = ensure_project_selection(job_dir, requirements)
    timings["project_selection"] = t.as_dict("project_selection")

    if not selector["ok"] or selector["data"].get("status") != "PASS":
        result["status"] = "PROJECT_SELECTION_FAILED"
        result["project_selection"] = selector.get("data")
        return result

    selection_guard_report = job_dir / "v8_project_selection_validation.json"
    with Timer() as t:
        guard = run_step(python_cmd(
            ROOT / "tools/resume/v8/validate_project_selection.py",
            "--job-dir", job_dir,
            "--selection", selector["path"],
            "--report", selection_guard_report,
        ))
    timings["project_selection_guard"] = t.as_dict("project_selection_guard")

    if not selection_guard_report.exists():
        result["status"] = "PROJECT_SELECTION_MISMATCH"
        return result
    guard_data = load_json(selection_guard_report)
    if guard_data.get("status") != "PASS":
        result["status"] = "PROJECT_SELECTION_MISMATCH"
        result["project_selection_failures"] = guard_data.get("failures", [])
        return result

    # --- normalize/validate structural payload ---
    normalization_report = job_dir / "payload_normalization.json"
    with Timer() as t:
        run_step(python_cmd(
            ROOT / "tools/resume/v8/normalize_resume_payload.py",
            "--payload", payload, "--report", normalization_report,
        ))
    timings["normalization"] = t.as_dict("normalization")

    if not normalization_report.exists():
        result["status"] = "PAYLOAD_NORMALIZATION_FAILED"
        return result
    normalization = load_json(normalization_report)
    if normalization.get("status") != "PASS":
        result["status"] = "PAYLOAD_NORMALIZATION_FAILED"
        result["normalization"] = normalization
        return result

    # --- payload-fit preflight ---
    payload_report = job_dir / "payload_preflight.json"
    with Timer() as t:
        run_step(python_cmd(
            ROOT / "tools/resume/payload_preflight.py",
            "--payload", payload, "--output", payload_report,
        ))
    timings["payload_preflight"] = t.as_dict("payload_preflight")

    if not payload_report.exists():
        result["status"] = "PAYLOAD_PREFLIGHT_FAILED"
        return result
    payload_data = load_json(payload_report)
    if payload_data.get("status") != "PASS":
        # --- Deterministic same-stage mechanical pre-trim -----------------
        # Many observed PAYLOAD_PREFLIGHT correction rounds are a pure
        # character-count overflow against payload_preflight.py's layout-
        # driven limits -- the exact class of problem
        # `enforce_correction_length_budget` (below) already trusts to trim
        # safely, at a word boundary, with no fabrication, when it runs
        # AFTER a correction call returns. Running that SAME, already-
        # reviewed trim one stage earlier -- on the semantic worker's own
        # over-budget payload, before a correction round is even requested
        # -- can only help: if the trim brings every field back under
        # budget, payload_preflight.py is simply re-run against the
        # trimmed payload (deterministic, no model call); if it does not
        # (the overflow is too large to fix by trimming filler alone, or
        # budgets are exceeded for a genuinely different reason), the
        # untouched correction-request path below runs exactly as before.
        # This can never turn a real failure into a false PASS: the exact
        # same payload_preflight.py validator is re-run against the
        # trimmed payload and must independently report PASS.
        trimmed_payload, trimmed_fields = enforce_correction_length_budget(
            load_json(payload), layout_path,
        )
        if trimmed_fields:
            write_json(payload, trimmed_payload)
            with Timer() as t:
                run_step(python_cmd(
                    ROOT / "tools/resume/payload_preflight.py",
                    "--payload", payload, "--output", payload_report,
                ))
            timings["payload_preflight_mechanical_retrim"] = t.as_dict("payload_preflight_mechanical_retrim")
            payload_data = load_json(payload_report) if payload_report.exists() else payload_data
            result["mechanical_preflight_trim"] = trimmed_fields

    if payload_data.get("status") != "PASS":
        round_number = previous_round(job_dir) + 1
        if round_number > CORRECTION_ROUND_LIMIT:
            result["status"] = "CORRECTION_LIMIT_EXCEEDED"
            result["reason"] = "PAYLOAD_PREFLIGHT"
            return result
        req = write_correction_request(job_dir, ["PAYLOAD_PREFLIGHT"], {
            "payload_preflight": payload_data,
            "instruction": "Bring only dynamic fields inside the normal payload budgets while preserving supported JD concepts and approved metrics.",
        })
        result["status"] = "NEEDS_LLM_CORRECTION"
        result["correction_request"] = str(req.relative_to(ROOT)) if req.is_absolute() else str(req)
        return result

    # --- render resume (Word-lock serialized internally) ---
    markdown = job_dir / "resume_draft.md"
    docx = job_dir / "resume.docx"
    pdf = job_dir / "resume.pdf"

    with Timer() as t:
        render = run_step(python_cmd(
            ROOT / "tools/resume/render_resume.py",
            "--payload", payload, "--markdown-output", markdown,
            "--output-docx", docx, "--output-pdf", pdf,
        ))
    timings["render"] = t.as_dict("render")
    if not render.ok:
        result["status"] = "RENDER_FAILED"
        result["stderr"] = render.stderr
        return result

    # --- ATS (adapted script: concept bridges + optional policy layered on
    # top of tools/ats/preflight.py and config/ats_aliases.json; batch-local
    # evidence root) ---
    ats_report = job_dir / "ats_preflight.json"
    with Timer() as t:
        run_step(python_cmd(
            ROOT / "tools/resume/v86/ats_preflight_v86.py",
            "--requirements", requirements, "--resume", markdown,
            "--evidence-root", evidence_root, "--aliases", ROOT / "config/ats_aliases.json",
            "--extra-aliases", ROOT / "config/v86/ats_concept_bridges.json",
            "--output", ats_report,
        ))
    timings["ats"] = t.as_dict("ats")

    if not ats_report.exists():
        result["status"] = "ATS_EXECUTION_FAILED"
        return result
    ats = load_json(ats_report)

    # --- QA ---
    qa_report = job_dir / "resume_qa.json"
    with Timer() as t:
        run_step(python_cmd(ROOT / "tools/resume/qa_resume.py", "--pdf", pdf, "--output", qa_report))
    timings["qa"] = t.as_dict("qa")

    if not qa_report.exists():
        result["status"] = "QA_EXECUTION_FAILED"
        return result
    qa = load_json(qa_report)

    correction_reasons = []
    details = {}

    optional_missing = ats.get("optional_supported_but_missing", [])

    # --- unplaceable-evidence safety --------------------------------------
    # A REQUIRED, supported-but-missing ATS concept is only correctable if
    # at least one of its evidence hits is owned by an editable field for
    # THIS job (a tailorable experience entry, or the already-bound
    # selectable project). If every hit is owned by protected/non-editable
    # content, no correction round can ever truthfully place it -- see
    # split_missing_required_by_placeability / evidence_ownership.py.
    bound_project_id = (load_json(payload).get("selected_project") or {}).get("owner_id")
    placeable_missing, unplaceable_missing = split_missing_required_by_placeability(
        ats, bound_project_id,
    )

    if unplaceable_missing:
        result["ats_unplaceable_required"] = unplaceable_missing

    ats_blocking = bool(placeable_missing)

    if ats_blocking:
        # Fix 3: gate_pass is already required-concept-only (see
        # ats_preflight_v86.py), so this branch only fires for a genuine,
        # blocking (required) gap -- never for an optional/preferred one,
        # and never solely for a proven-unplaceable one.
        correction_reasons.append("ATS")
        details["ats"] = {
            "supported_but_missing": placeable_missing,
            "unsupported_required_terms": ats.get("unsupported_required_terms", []),
            "instruction": "Repair supported missing concepts only. Never add an unsupported requirement.",
        }
        if optional_missing:
            # Bundle optional concepts in as an opportunistic, explicitly
            # non-blocking ask riding along with an already-necessary round
            # -- never the reason a round happens, never required to succeed.
            details["ats"]["optional_concepts_opportunistic"] = {
                "terms": optional_missing,
                "instruction": (
                    "Optional only: add ONLY if truthful evidence already fits an "
                    "editable field without altering claims; never fabricate; this "
                    "list must never by itself block or extend a correction round."
                ),
            }

    bottom = qa.get("usable_bottom_whitespace_in")
    if qa.get("page_count") == 1 and bottom is not None and bottom < 0.25:
        density_report = job_dir / "density_guard.json"
        with Timer() as t:
            run_step(python_cmd(
                ROOT / "tools/resume/density_guard.py",
                "--qa", qa_report, "--payload", payload, "--output", density_report,
            ))
        timings["density"] = t.as_dict("density")
        density = load_json(density_report) if density_report.exists() else {}
        correction_reasons.append("DENSITY")
        details["density"] = {
            "density_guard": density,
            "instruction": "Shorten only allowed dynamic content enough to remove the estimated wrapped line count.",
        }

    if qa.get("page_count") != 1:
        correction_reasons.append("MULTIPAGE")
        details["multipage"] = {
            "minimal_page_2_spill": qa.get("minimal_page_2_spill", False),
            "page_2_nonempty_lines": qa.get("page_2_nonempty_lines", 0),
            "page_2_text_chars": qa.get("page_2_text_chars", 0),
            "instruction": "Use the approved bounded minimal-spill correction only if the deterministic condition is eligible.",
        }

    # --- action-verb diversity --------------------------------------------
    # STYLE/QUALITY check only -- never an ATS/evidence/company/project-
    # selection change (see action_verb_diversity.py's module docstring).
    # Counts each EXPERIENCE + PROJECT bullet's opening verb on the just-
    # rendered `markdown` (the same final resume_draft.md ats_preflight_v86
    # itself reads), then splits any verb repeated over the limit into
    # `fixable` (at least one occurrence sits in an editable bullet, so a
    # correction round has a real chance of helping) vs. `unfixable` (every
    # occurrence is baked into a protected canonical bullet -- no
    # correction round could ever change it, the same "don't spend a
    # bounded round on an impossible fix" reasoning
    # `split_missing_required_by_placeability` already applies to ATS).
    # Only `fixable` verbs are bundled into this round's correction_reasons/
    # details, exactly like an already-necessary ATS/DENSITY/MULTIPAGE round
    # -- with ONE difference: when action-verb diversity is the ONLY
    # finding, it is still allowed to spend one of the two permitted rounds
    # on its own (see the round-limit handling below).
    resume_payload_for_verbs = load_json(payload)
    editable_bullets = action_verb_diversity.editable_bullet_texts(resume_payload_for_verbs)
    verb_report = action_verb_diversity.analyze_action_verbs_from_file(markdown)
    repeated_verbs = verb_report["repeated_over_limit"]
    verb_classification = action_verb_diversity.classify_repeated_verbs(verb_report, editable_bullets)
    fixable_verbs = verb_classification["fixable"]
    unfixable_verbs = verb_classification["unfixable"]

    result["action_verb_diversity"] = {
        "counts": verb_report["counts"],
        "repeated_over_limit": repeated_verbs,
        "limit": verb_report["limit"],
    }
    if unfixable_verbs:
        # Honest, non-blocking diagnostic -- never requested from
        # correction (nothing editable to change), never silently dropped.
        result["action_verb_unfixable"] = {verb: e["total_count"] for verb, e in unfixable_verbs.items()}

    action_verb_correction_only = False
    if fixable_verbs:
        correction_reasons.append("ACTION_VERB_DIVERSITY")
        details["action_verb_diversity"] = {
            "limit": verb_report["limit"],
            "repeated_verbs": fixable_verbs,
            "instruction": (
                "STYLE ONLY, never a factual/evidence change: for each verb in repeated_verbs, "
                "'fixed_canonical_count' occurrences are protected/canonical and cannot be "
                "touched -- rephrase the opening of enough of that verb's 'editable_bullets' "
                "(a truthful synonym with equivalent meaning) so its TOTAL count "
                "(fixed_canonical_count + your remaining editable bullets using it) drops to at "
                "most 'limit'. Never touch a bullet not listed in editable_bullets, never change "
                "any metric, technology, employer, or claim, and never fabricate evidence to "
                "justify a different wording."
            ),
        }
        action_verb_correction_only = (correction_reasons == ["ACTION_VERB_DIVERSITY"])

    if correction_reasons:
        round_number = previous_round(job_dir) + 1
        if round_number > CORRECTION_ROUND_LIMIT:
            if not action_verb_correction_only:
                result["status"] = "CORRECTION_LIMIT_EXCEEDED"
                result["reason"] = "+".join(correction_reasons)
                return result
            # Action-verb diversity is a STYLE rule, never important enough
            # to fail an otherwise-valid, truthful, ATS/QA-passing resume
            # solely because the correction budget is now exhausted -- fall
            # through to the success path below, which surfaces this as a
            # non-blocking warning exactly like an optional ATS concept or
            # unplaceable-evidence gap already does.
        else:
            req = write_correction_request(job_dir, correction_reasons, details)
            result["status"] = "NEEDS_LLM_CORRECTION"
            result["correction_request"] = str(req.relative_to(ROOT)) if req.is_absolute() else str(req)
            return result

    # `ats_blocking` is False either because the frozen gate itself passed,
    # or because the ONLY required gap(s) are proven-unplaceable (never
    # fabricated, never silently waived -- see above); either way, nothing
    # further could be gained by requesting a correction, so this is the
    # effective ATS pass/fail for the local success gate.
    ats_pass_effective = not ats_blocking

    success = (
        ats_pass_effective
        and qa.get("status") == "PASS"
        and qa.get("page_count") == 1
        and qa.get("density_target_pass") is True
        and bottom is not None and 0.25 <= bottom <= 0.50
        and docx.exists() and pdf.exists()
    )

    if not success:
        result["status"] = "LOCAL_QUALITY_GATE_FAILED"
        result["ats_gate"] = ats_pass_effective
        result["qa"] = qa
        return result

    # --- deterministic cover-letter rendering ---
    cover_payload = job_dir / "cover_letter_payload.json"
    cover_docx = job_dir / "cover_letter.docx"
    cover_pdf = job_dir / "cover_letter.pdf"

    if not cover_payload.exists():
        result["status"] = "COVER_LETTER_PAYLOAD_MISSING"
        return result

    with Timer() as t:
        run_step(python_cmd(
            ROOT / "tools/resume/v8/render_cover_letter.py",
            "--payload", cover_payload, "--output-docx", cover_docx,
        ))
        run_step(python_cmd(
            ROOT / "tools/resume/word_to_pdf.py",
            "--input", cover_docx, "--output", cover_pdf,
        ))
    timings["cover_letter_render"] = t.as_dict("cover_letter_render")

    if not (cover_docx.exists() and cover_pdf.exists()):
        result["status"] = "COVER_LETTER_RENDER_FAILED"
        return result

    # A remaining OPTIONAL supported-but-missing concept is a non-blocking
    # ATS warning, never a terminal failure -- surface it honestly on an
    # otherwise-RESUME_READY result rather than silently dropping it. A
    # proven-unplaceable REQUIRED concept (`result["ats_unplaceable_
    # required"]`, set above) is the same kind of honest, non-blocking
    # warning -- distinct from the optional list, never silently dropped,
    # never re-requested from correction. A still-repeated opening action
    # verb, surviving the permitted correction process (see above), is the
    # same kind of honest, STYLE-only, non-blocking warning -- never a
    # terminal failure by itself.
    # --- consolidated ADVISORY resume-quality report -----------------------
    # Purely diagnostic (see resume_quality_report.py): it writes one
    # job-local `resume_quality_report.json` into this job's own
    # (git-ignored) `output/<job_key>/` directory and returns a small,
    # candidate-content-free summary for the job result. It cannot change
    # `result["status"]`, cannot add a correction round, and cannot cause a
    # model call -- it runs here, AFTER every gate decision above has
    # already been made, and its own generator swallows any internal error.
    #
    # The artifact it inspects at this point is the rendered `resume.pdf`
    # BEFORE `finalize_local.py` has had a chance to promote cleaned bytes
    # over it, so the report records `artifact.stage = "pre_finalization"`.
    # `run_batch.py` re-runs `resume_quality_report.refresh_after_
    # finalization` once finalization has happened, which re-reads the
    # actual shipped file and updates the stage/sha256 -- the report never
    # claims to have validated a finalized PDF it did not read.
    #
    # `verb_report` is passed in and reused verbatim: action_verb_diversity
    # still runs exactly ONCE per deterministic pass, above.
    #
    # Belt and braces: `generate` is itself written never to raise, and
    # this call site ALSO refuses to let anything out. `run_job_pipeline`
    # is contractually "never raises for an ordinary job failure", and an
    # advisory diagnostic is the last thing that should be allowed to
    # break that contract.
    try:
        quality_report = resume_quality_report.generate(
            job_dir, job_key, qa=qa, action_verb_report=result["action_verb_diversity"],
        )
        result["quality_report"] = resume_quality_report.console_summary(quality_report, job_dir)
    except Exception as exc:  # noqa: BLE001 -- advisory only, never fatal
        result["quality_report"] = {
            "path": None,
            "diagnostic_state": "INCOMPLETE",
            "error": f"{type(exc).__name__}: {exc}",
            "advisory_only": True,
        }

    if optional_missing or unplaceable_missing or repeated_verbs:
        result["status"] = "RESUME_READY_WITH_WARNINGS"
        if optional_missing:
            result["ats_warnings"] = optional_missing
        if repeated_verbs:
            result["action_verb_warning"] = repeated_verbs
    else:
        result["status"] = "RESUME_READY"
    result["ats_gate"] = True
    result["page_count"] = 1
    result["usable_bottom_whitespace_in"] = bottom
    result["qa_status"] = "PASS"
    return result


def enforce_correction_length_budget(
    resume_payload: dict, layout_path: Path = DEFAULT_LAYOUT_PATH,
) -> tuple[dict, list]:
    """Deterministic, same-round mechanical length enforcement.

    A correction round's authored text sometimes lands past the normal
    payload budget by only a few characters. A mechanical character-count
    overflow does not need another model call to fix -- it needs a
    deterministic trim, applied before the corrected payload is even
    written to disk, in the SAME round.

    Reuses the exact field budgets already defined in the candidate's own
    reference/resume_template_layout.json (the same layout
    tools/resume/payload_preflight.py reads), read-only; this module does
    not duplicate or modify those numbers. Only the OFFENDING field's
    trailing content is removed, at a safe boundary (never splitting an
    unmatched "**" markdown pair); nothing is added, reworded, or
    fabricated -- "do not alter claims merely to force length" is satisfied
    by construction (a trim is always a strict prefix of the original text).

    Returns (payload_copy, trimmed_field_names). `payload_copy` is always a
    deep copy; the input is never mutated.
    """
    layout = layout_lib.load_layout(layout_path)

    payload = json.loads(json.dumps(resume_payload))
    trimmed_fields: list = []

    def clean_length(text: str) -> int:
        return len(str(text).replace("**", "").strip())

    def _trim(text: str, limit: int) -> str:
        if clean_length(text) <= limit:
            return text

        def _valid(candidate: str) -> Optional[str]:
            if candidate.count("**") % 2 != 0:
                return None  # never leave an unmatched markdown bold marker
            stripped = candidate.rstrip()
            if clean_length(stripped) <= limit:
                return stripped.rstrip(" ,;:-–—")
            return None

        # Prefer trimming at a whole-word boundary (last space at or before
        # each candidate cut point) so the surviving text reads as a
        # truncated sentence, not a word sliced in half.
        for end in range(len(text), 0, -1):
            if end < len(text) and text[end] not in (" ", "\n", "\t"):
                continue
            result = _valid(text[:end])
            if result is not None:
                return result

        # Fallback: no whole-word cut fits the budget at all -- hard-cut at
        # the character level (still a strict prefix, still no fabrication).
        for end in range(len(text), 0, -1):
            result = _valid(text[:end])
            if result is not None:
                return result
        return text[:limit]

    alternates = payload.get("experience_alternate_bullets")
    if isinstance(alternates, dict):
        for block in layout_lib.tailorable_experience_blocks(layout):
            owner_id = block["owner_id"]
            bullets = alternates.get(owner_id)
            limit = block.get("max_chars_per_bullet")
            if isinstance(bullets, list) and limit:
                for i, bullet in enumerate(bullets):
                    if isinstance(bullet, str):
                        trimmed = _trim(bullet, limit)
                        if trimmed != bullet:
                            bullets[i] = trimmed
                            trimmed_fields.append(f"experience_alternate_bullets.{owner_id}[{i}]")

    slot = layout_lib.selected_project_block(layout)
    selected = payload.get("selected_project")
    if isinstance(selected, dict):
        title = selected.get("title")
        if isinstance(title, str) and slot.get("max_chars_title"):
            trimmed = _trim(title, slot["max_chars_title"])
            if trimmed != title:
                selected["title"] = trimmed
                trimmed_fields.append("selected_project.title")

        tech = selected.get("technology_line")
        if isinstance(tech, str) and slot.get("max_chars_technology_line"):
            trimmed = _trim(tech, slot["max_chars_technology_line"])
            if trimmed != tech:
                selected["technology_line"] = trimmed
                trimmed_fields.append("selected_project.technology_line")

        bullets = selected.get("bullets")
        if isinstance(bullets, list) and slot.get("max_chars_per_bullet"):
            for i, bullet in enumerate(bullets):
                if isinstance(bullet, str):
                    trimmed = _trim(bullet, slot["max_chars_per_bullet"])
                    if trimmed != bullet:
                        bullets[i] = trimmed
                        trimmed_fields.append(f"selected_project.bullets[{i}]")

    return payload, trimmed_fields


def apply_correction(
    job_dir: Path,
    correction_round: int,
    corrected_resume_payload: dict,
    correction_status: dict,
    bound_project_id: Optional[str] = None,
) -> dict:
    """Merge a correction-worker result back into the job's resume_payload,
    with a hard Python-level guard: a correction round can NEVER change
    which project resume_payload.selected_project describes. This is
    defense in depth on top of the fact that the correction worker's
    schema has no field for a different project identity in the first
    place -- see tools/resume/v86/correction_worker.py.
    """
    if bound_project_id is not None:
        new_owner_id = str(corrected_resume_payload.get("selected_project", {}).get("owner_id", ""))
        if new_owner_id.strip() and new_owner_id.strip() != bound_project_id.strip():
            status = {
                "status": "CORRECTION_FAILED",
                "skill_name": V86_SKILL_NAME,
                "skill_identity": V86_SKILL_IDENTITY,
                "correction_round": correction_round,
                "job": job_dir.name,
                "reasons": [
                    "REJECTED_UNAUTHORIZED_PROJECT_RESELECTION: "
                    f"bound={bound_project_id!r}, attempted={new_owner_id!r}"
                ],
            }
            write_json(job_dir / "v8_correction_status.json", status)
            return status

    write_json(job_dir / "resume_payload.json", corrected_resume_payload)
    status = {
        "status": correction_status.get("status", "CORRECTED"),
        "skill_name": V86_SKILL_NAME,
        "skill_identity": V86_SKILL_IDENTITY,
        "correction_round": correction_round,
        "job": job_dir.name,
        "reasons": correction_status.get("reasons", []),
    }
    write_json(job_dir / "v8_correction_status.json", status)
    return status
