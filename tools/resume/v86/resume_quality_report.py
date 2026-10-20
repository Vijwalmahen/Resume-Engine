#!/usr/bin/env python3
"""
Feature E -- the one consolidated, ADVISORY resume-quality report.

Writes exactly one `resume_quality_report.json` per job, into that job's
own existing runtime working directory (`output/<job_key>/`, which
`.gitignore` already excludes). It never creates a new top-level output
convention and never writes candidate-specific findings anywhere else.

What it consolidates (one source per check -- nothing is computed twice):

  artifact_integrity  `pdf_roundtrip` (text layer, glyph corruption,
                      field-level content recall) + the EXISTING
                      `resume_qa.json` for the replacement-character
                      verdict and page/density facts
  reading_order       `pdf_roundtrip`
  metrics             `metrics_audit`
  content_quality     `bullet_length_audit`, plus the professional-summary
                      check which is `not_applicable` by design
  timeline            `timeline_audit`
  style               the action-verb report the pipeline ALREADY
                      computed, passed in and reused verbatim --
                      `action_verb_diversity` is never re-run here
  job_match           `jd_match_report` -- per-requirement job fit, kept
                      on two separate axes (how the requirement surfaces
                      in the resume, and whether master evidence can back
                      it). Folded in when the job directory has one
                      (`jd_match_report.json`); `not_evaluated` otherwise
  design              `template_style_audit` -- whether rendering changed
                      anything about the document except its words,
                      diffed against the job's own template
                      (`template_style_audit.json`)

Document integrity and job fit are deliberately kept as separate
categories of finding. "The PDF's text layer is broken" is an objective
defect in the artifact; "this job wants Kubernetes and the candidate has
never used it" is a fact about fit that no amount of rendering can fix.
Merging them would let a cosmetic keyword gap read like a corrupt file,
and would let a genuinely broken artifact hide behind good keyword
coverage.

Hard boundaries, all preserved:

  - Nothing in this report can fail a job, change a status, trigger a
    correction round, or cause a model/API call. Generation is wrapped so
    that even an unexpected internal error degrades to a recorded note.
  - It does not produce a numeric ATS score and never blends artifact
    readability, job fit and writing style into one number. The only
    overall value is a diagnostic state: PASS / REVIEW / INCOMPLETE.
  - It never claims to have validated a cleaned, finalized PDF unless it
    actually read that promoted file: `artifact.stage` records which
    artifact was inspected, and `artifact.sha256` makes the claim
    checkable. See `artifact_stage` below.

Diagnostic state precedence (documented, deterministic):

    REVIEW      any finding of severity `high` or `medium`
    INCOMPLETE  otherwise, any check that could not be evaluated
    REVIEW      otherwise, any finding of severity `low`
    PASS        otherwise (only `info` findings, everything evaluated)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bullet_length_audit  # noqa: E402
import metrics_audit  # noqa: E402
import metrics_registry_adapter  # noqa: E402
import pdf_roundtrip  # noqa: E402
import resume_draft_model  # noqa: E402
import timeline_audit  # noqa: E402
from common import ROOT, load_json, now_iso, sha256_file, write_json  # noqa: E402

SCHEMA_VERSION = "1.0"
REPORT_FILENAME = "resume_quality_report.json"

DEFAULT_CONFIG_PATH = ROOT / "config" / "v86" / "resume_quality_diagnostics.json"
DEFAULT_RUNTIME_CONTEXT = ROOT / "master" / "runtime" / "runtime_resume_context.json"
DEFAULT_METRICS_REGISTRY = ROOT / "master" / "metrics_registry.json"

# The reusable repository renders `resume.pdf`; a deployment that names its
# shipped resume differently passes its own name (or a glob) rather than
# having one patched in. See `resolve_resume_pdf`.
RESUME_PDF_NAME = "resume.pdf"
RESUME_DRAFT_NAME = "resume_draft.md"

_GLOB_CHARACTERS = ("*", "?", "[")

CONFIG_DEFAULTS = {
    "short_bullet_min_visible_chars": bullet_length_audit.DEFAULT_SHORT_BULLET_MIN_VISIBLE_CHARS,
    "partial_match_min_token_ratio": pdf_roundtrip.DEFAULT_PARTIAL_MIN_TOKEN_RATIO,
    "min_readable_text_chars": pdf_roundtrip.DEFAULT_MIN_READABLE_TEXT_CHARS,
    "timeline_gap_review_months": timeline_audit.DEFAULT_GAP_REVIEW_MONTHS,
}

SEVERITIES = ("high", "medium", "low", "info")

SUMMARY_NOT_APPLICABLE_MESSAGE = (
    "This resume format intentionally has no professional-summary section, so a "
    "summary-length rule does not apply to it. Nothing is added or restructured to "
    "satisfy an external scanner that expects one."
)


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

def load_config(path=None) -> dict:
    """Load the advisory thresholds, falling back to the built-in
    defaults for anything missing or non-numeric. A broken config file
    can never break a job -- it only costs the custom thresholds."""
    config = dict(CONFIG_DEFAULTS)
    config["_source"] = None

    resolved = Path(path) if path else DEFAULT_CONFIG_PATH
    if not resolved.exists():
        return config

    try:
        data = load_json(resolved)
    except Exception:
        return config

    if not isinstance(data, dict):
        return config

    config["_source"] = str(resolved.relative_to(ROOT)) if resolved.is_absolute() and str(resolved).startswith(str(ROOT)) else str(resolved)
    for key, default in CONFIG_DEFAULTS.items():
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            value = default
        config[key] = value
    return config


# ---------------------------------------------------------------------------
# findings helpers
# ---------------------------------------------------------------------------

def _finding(finding_id, category, severity, status, subject, message, evidence, recommendation) -> dict:
    return {
        "id": finding_id,
        "category": category,
        "severity": severity,
        "status": status,
        "subject": subject,
        "message": message,
        "evidence": evidence,
        "recommendation": recommendation,
    }


def _check(check_id, category, status, reason=None) -> dict:
    return {"id": check_id, "category": category, "status": status, "reason": reason}


def _excerpt(text: str, limit: int = 90) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# artifact provenance
# ---------------------------------------------------------------------------

def resolve_resume_pdf(job_dir: Path, name_or_pattern: Optional[str] = None) -> dict:
    """Identify which file in `job_dir` is THIS deployment's shipped resume.

    `name_or_pattern` is either an exact filename (`"resume.pdf"`, the
    reusable repository's own default) or a glob
    (`"*-resume.pdf"`) for a deployment whose renderer emits a
    personalised filename. Nothing is guessed from the directory
    contents: a caller that passes nothing gets the default name and
    nothing else.

    Returns `{"expected", "resolution", "name", "path", "candidates"}`
    where `resolution` is:

      `found`      exactly one artifact matched -- `name`/`path` are it
      `missing`    nothing matched; `path` still points at the expected
                   name so the report can say what it looked for
      `ambiguous`  a pattern matched more than one file. Picking one
                   would be a guess about which artifact ships, so the
                   report refuses to and says so.
    """
    expected = (name_or_pattern or RESUME_PDF_NAME).strip() or RESUME_PDF_NAME
    job_dir = Path(job_dir)

    if any(ch in expected for ch in _GLOB_CHARACTERS):
        matches = sorted(p for p in job_dir.glob(expected) if p.is_file())
        if len(matches) == 1:
            return {"expected": expected, "resolution": "found", "name": matches[0].name,
                    "path": matches[0], "candidates": [matches[0].name]}
        if not matches:
            return {"expected": expected, "resolution": "missing", "name": None,
                    "path": None, "candidates": []}
        return {"expected": expected, "resolution": "ambiguous", "name": None,
                "path": None, "candidates": [p.name for p in matches]}

    candidate = job_dir / expected
    if candidate.is_file():
        return {"expected": expected, "resolution": "found", "name": expected,
                "path": candidate, "candidates": [expected]}
    return {"expected": expected, "resolution": "missing", "name": None,
            "path": None, "candidates": []}


def artifact_stage(
    finalization_record: Optional[dict],
    finalization_attempted: bool = False,
    resume_pdf_name: Optional[str] = None,
) -> tuple:
    """Which artifact this report actually inspected.

    `pre_finalization`
        Finalization has not run for this job yet. The rendered PDF read
        here may still be replaced by the watermark/metadata finalizer
        (`finalize_local.py` promotes cleaned bytes over `resume.pdf` in
        place), so this report must NOT be read as having validated the
        shipped file.
    `finalized`
        `watermark_finalization.json` reports `resume.pdf` as PASS, which
        is exactly the case where the cleaned bytes were promoted over
        `resume.pdf` -- the file read here IS the final deliverable.
    `finalization_not_applied`
        Finalization ran but did not promote cleaned bytes for
        `resume.pdf` (service unavailable, or a check failed and the
        original was deliberately kept). The rendered PDF read here is
        therefore still the file that ships.

    `finalization_attempted` is what separates the first case from the
    last when no record exists at all: `finalize_local.finalize_job`
    writes no `watermark_finalization.json` when the cleaning service is
    unavailable, and in that situation the rendered PDF really is the
    shipped artifact -- saying it "may still be replaced" would be
    wrong. Only `run_batch.py`, which calls this strictly AFTER the
    finalizer has returned, passes True.
    """
    if not isinstance(finalization_record, dict) or not finalization_record:
        if finalization_attempted:
            return "finalization_not_applied", (
                "the finalization step ran but produced no watermark_finalization.json "
                "(the cleaning service was unavailable); the rendered PDF inspected here "
                "was not replaced and is what ships"
            )
        return "pre_finalization", (
            "no watermark_finalization.json for this job yet; the rendered PDF inspected "
            "here may still be replaced by the finalizer"
        )

    # Match the finalizer's own record by the SAME artifact name this
    # report resolved, so a deployment whose renderer emits a different
    # filename is still matched against its own finalization entry.
    tracked = resume_pdf_name or RESUME_PDF_NAME

    for artifact in finalization_record.get("artifacts") or []:
        if isinstance(artifact, dict) and artifact.get("file") == tracked:
            if artifact.get("status") == "PASS":
                return "finalized", (
                    f"finalize_local.py promoted the cleaned {tracked}, so the artifact "
                    "inspected here is the final deliverable"
                )
            return "finalization_not_applied", (
                f"finalization reported {tracked} as {artifact.get('status')!r}; the "
                "original rendered PDF was kept and is what ships"
            )

    return "finalization_not_applied", (
        f"finalization overall_status={finalization_record.get('overall_status')!r} and no "
        f"{tracked} artifact record was produced; the rendered PDF was not replaced"
    )


def _artifact_block(
    resolved: dict, finalization_record: Optional[dict], finalization_attempted: bool = False,
) -> dict:
    stage, reason = artifact_stage(
        finalization_record, finalization_attempted, resume_pdf_name=resolved.get("name"),
    )
    pdf_path = resolved.get("path")
    exists = bool(pdf_path and Path(pdf_path).exists())
    digest = None
    if exists:
        try:
            digest = sha256_file(pdf_path)
        except OSError:
            digest = None
    return {
        # `path` is the artifact actually inspected; `expected` is what the
        # caller asked for, which is what makes a `missing`/`ambiguous`
        # resolution legible instead of silently empty.
        "path": resolved.get("name") or resolved.get("expected"),
        "expected": resolved.get("expected"),
        "resolution": resolved.get("resolution"),
        "candidates": resolved.get("candidates") or [],
        "exists": exists,
        "sha256": digest,
        "stage": stage,
        "stage_reason": reason,
    }


# ---------------------------------------------------------------------------
# per-category evaluation
# ---------------------------------------------------------------------------

def _artifact_findings(roundtrip: dict, qa: Optional[dict], resolved: Optional[dict] = None) -> tuple:
    checks, findings = [], []
    resolved = resolved or {"expected": RESUME_PDF_NAME, "resolution": "found",
                            "name": RESUME_PDF_NAME, "candidates": []}
    # Every artifact-level finding names the file that was actually
    # inspected (or, when nothing was, the name that was looked for).
    subject = resolved.get("name") or resolved.get("expected") or RESUME_PDF_NAME

    resolution = resolved.get("resolution")
    if resolution == "ambiguous":
        checks.append(_check(
            "artifact_integrity.artifact_resolution", "artifact_integrity", "fail",
            f"{len(resolved.get('candidates') or [])} files match "
            f"{resolved.get('expected')!r}",
        ))
        findings.append(_finding(
            "artifact_integrity.artifact_resolution", "artifact_integrity", "high", "fail",
            resolved.get("expected"),
            "More than one file matches the expected resume-PDF name, so which artifact "
            "actually ships cannot be determined. Nothing was inspected rather than "
            "guessing at one of them.",
            {"expected": resolved.get("expected"), "candidates": resolved.get("candidates")},
            "Give the report an unambiguous resume-PDF name for this deployment, or "
            "remove the stale artifacts from the job directory.",
        ))
    elif resolution == "missing":
        checks.append(_check(
            "artifact_integrity.artifact_resolution", "artifact_integrity", "not_evaluated",
            f"no file matching {resolved.get('expected')!r} in the job directory",
        ))
    else:
        checks.append(_check(
            "artifact_integrity.artifact_resolution", "artifact_integrity", "pass",
            f"inspected {subject!r}",
        ))

    if roundtrip.get("status") != "EVALUATED":
        reason = roundtrip.get("reason") or "round-trip inputs unavailable"
        checks.append(_check("artifact_integrity.text_layer", "artifact_integrity", "not_evaluated", reason))
        checks.append(_check("artifact_integrity.glyph_corruption", "artifact_integrity", "not_evaluated", reason))
        checks.append(_check("artifact_integrity.content_recall", "artifact_integrity", "not_evaluated", reason))
        checks.append(_check("reading_order.sequence", "reading_order", "not_evaluated", reason))
    else:
        text_layer = roundtrip["text_layer"]
        unreadable = text_layer["effectively_unreadable"]
        checks.append(_check(
            "artifact_integrity.text_layer", "artifact_integrity",
            "fail" if unreadable else "pass",
            None if not unreadable else "extracted text layer is empty or below the readable minimum",
        ))
        if unreadable:
            findings.append(_finding(
                "artifact_integrity.text_layer", "artifact_integrity", "high", "fail",
                subject,
                "The PDF's extracted text layer is empty or effectively unreadable, so an "
                "ATS would not be able to read this resume.",
                {
                    "extracted_chars": text_layer["extracted_chars"],
                    "min_readable_chars": text_layer["min_readable_chars"],
                },
                "Re-render the resume and confirm the PDF export produces real text rather "
                "than an image-only or broken text layer.",
            ))

        glyphs = roundtrip["glyph_corruption"]
        structural_corruption = bool(glyphs["cid_glyph_markers"] or glyphs["control_characters"])
        checks.append(_check(
            "artifact_integrity.glyph_corruption", "artifact_integrity",
            "fail" if structural_corruption else "pass",
            None if not structural_corruption else "extraction produced unmapped-glyph or control characters",
        ))
        if structural_corruption:
            findings.append(_finding(
                "artifact_integrity.glyph_corruption", "artifact_integrity", "medium", "fail",
                subject,
                "Text extraction produced corrupted glyph output, which can garble the "
                "resume for a text-based parser.",
                {
                    "cid_glyph_markers": glyphs["cid_glyph_markers"],
                    "control_characters": glyphs["control_characters"],
                },
                "Check the embedded fonts in the export path; a font without a usable "
                "Unicode mapping extracts as (cid:NN) placeholders.",
            ))

        recall = roundtrip["recall_summary"]
        evaluated = recall["missing"] + recall["partial"]
        checks.append(_check(
            "artifact_integrity.content_recall", "artifact_integrity",
            "fail" if recall["missing"] else ("review" if recall["partial"] else "pass"),
            None if not evaluated else
            f"{recall['missing']} expected field(s) missing, {recall['partial']} partial",
        ))

        for row in roundtrip["fields"]:
            if row["status"] == "found":
                continue
            if row["status"] == "not_evaluated":
                continue
            substantive = row["substantive"]
            if row["status"] == "missing":
                severity = "high" if substantive else "medium"
                message = (
                    "An expected resume element was not found in the text extracted from "
                    "the PDF."
                )
                recommendation = (
                    "Confirm this element actually rendered; if it did, the export is "
                    "dropping it from the text layer."
                )
            else:
                severity = "medium" if substantive else "low"
                message = (
                    "An expected resume element was only partially recovered from the "
                    "text extracted from the PDF."
                )
                recommendation = (
                    "Review the rendered line: partial recovery usually means a styling "
                    "run or a special character broke the extracted text."
                )
            findings.append(_finding(
                f"artifact_integrity.content_recall.{row['status']}",
                "artifact_integrity", severity, row["status"], row["id"],
                message,
                {
                    "field_id": row["id"],
                    "kind": row["kind"],
                    "section": row["section"],
                    "match_ratio": row["match_ratio"],
                    "partial_min_ratio": row.get("partial_min_ratio"),
                    "expected_chars": row["expected_chars"],
                },
                recommendation,
            ))

        order = roundtrip["reading_order"]
        if order["status"] != "evaluated":
            checks.append(_check("reading_order.sequence", "reading_order", "not_evaluated", order["reason"]))
        else:
            inverted = order["inversions"] > 0
            checks.append(_check(
                "reading_order.sequence", "reading_order",
                "review" if inverted else "pass",
                None if not inverted else f"{order['inversions']} ordering inversion(s) measured",
            ))
            if inverted:
                findings.append(_finding(
                    "reading_order.sequence", "reading_order", "medium", "review",
                    subject,
                    "Expected resume elements appear in the extracted text in a different "
                    "order than the intended document order.",
                    {
                        "order_fidelity": order["order_fidelity"],
                        "inversions": order["inversions"],
                        "anchored_fields": order["anchored_fields"],
                        "misordered_elements": order["misordered_elements"][:10],
                    },
                    "Interpret this against the current template and this extraction only: "
                    "a column-based layout is not automatically ATS-incompatible, but a "
                    "field that extracts out of sequence is worth checking.",
                ))

    # Replacement characters: the existing QA report owns this verdict.
    qa_available = isinstance(qa, dict) and "replacement_character_present" in qa
    if qa_available:
        present = bool(qa.get("replacement_character_present"))
        source = "resume_qa.json"
    elif roundtrip.get("glyph_corruption"):
        present = bool(roundtrip["glyph_corruption"]["replacement_characters"])
        source = "pdf_roundtrip"
    else:
        present = None
        source = None

    if present is None:
        checks.append(_check(
            "artifact_integrity.replacement_characters", "artifact_integrity",
            "not_evaluated", "no QA report and no readable PDF text to inspect",
        ))
    else:
        checks.append(_check(
            "artifact_integrity.replacement_characters", "artifact_integrity",
            "fail" if present else "pass", f"source: {source}",
        ))
        if present:
            findings.append(_finding(
                "artifact_integrity.replacement_characters", "artifact_integrity",
                "high", "fail", subject,
                "The extracted text contains Unicode replacement characters, which means "
                "some glyphs did not survive the export.",
                {"source": source},
                "This is already a blocking QA failure in the existing gate; fix the "
                "offending character in the payload or the template font.",
            ))

    return checks, findings


def _density_check(
    qa: Optional[dict], density_guard: Optional[dict], resolved: Optional[dict] = None,
) -> tuple:
    # This is an `artifact_integrity` finding, so it names the artifact it
    # describes exactly the way `_artifact_findings` does -- the file that
    # was actually inspected, or the name that was looked for. `resolved`
    # is optional so the default stays the repository's own generic
    # artifact name and no deployment-specific filename is ever implied.
    resolved = resolved or {}
    subject = resolved.get("name") or resolved.get("expected") or RESUME_PDF_NAME

    if not isinstance(qa, dict) or not qa:
        return [_check("artifact_integrity.page_density", "artifact_integrity",
                       "not_evaluated", "no resume_qa.json available")], []

    density_pass = qa.get("density_target_pass")
    evidence = {
        "source": "resume_qa.json",
        "page_count": qa.get("page_count"),
        "usable_bottom_whitespace_in": qa.get("usable_bottom_whitespace_in"),
        "density_target_pass": density_pass,
        "qa_status": qa.get("status"),
    }
    if isinstance(density_guard, dict) and density_guard:
        evidence["density_guard_status"] = density_guard.get("status")

    if density_pass is None:
        return [_check("artifact_integrity.page_density", "artifact_integrity",
                       "not_evaluated", "QA recorded no one-page density measurement")], []

    status = "pass" if density_pass else "review"
    checks = [_check("artifact_integrity.page_density", "artifact_integrity", status,
                     "restated from the existing QA/density measurement; not recomputed")]
    findings = []
    if not density_pass:
        findings.append(_finding(
            "artifact_integrity.page_density", "artifact_integrity", "info", "review",
            subject,
            "The existing QA density measurement is outside the template's target band. "
            "This is a restatement of the existing check, not a new rule.",
            evidence,
            "No action from this report: the existing density gate and density_guard "
            "advisor already own this decision.",
        ))
    return checks, findings


def _metrics_findings(audit: dict) -> tuple:
    if audit.get("status") != "EVALUATED":
        return [_check("metrics.verified_measurement_recall", "metrics",
                       "not_evaluated", audit.get("reason"))], []

    summary = audit["summary"]
    status = "review" if summary["omitted"] else "pass"
    reason = (
        f"{summary['present']} present, {summary['omitted']} possibly omitted, "
        f"{summary['not_evaluated']} not evaluated, {summary['not_applicable']} not applicable"
    )
    checks = [_check("metrics.verified_measurement_recall", "metrics", status, reason)]

    findings = []
    for row in audit["results"]:
        if row["status"] != "omitted":
            continue
        findings.append(_finding(
            "metrics.verified_measurement_omitted", "metrics", "low", "omitted",
            row["owner_id"],
            "An approved source bullet for this entry carries a verified measurement that "
            "none of the entry's final tailored bullets communicates.",
            {
                "metric_id": row["metric_id"],
                "owner_id": row["owner_id"],
                "measurements": row["measurements"],
                "source_bullet_index": row["source_bullet_index"],
                "final_bullet_count": row["final_bullet_count"],
            },
            "Advisory only. If the measurement still applies to the tailored bullet, it may "
            "be restored EXACTLY as the candidate's verified records state it. Never "
            "restate it differently, never estimate, and never treat its absence as "
            "evidence that the bullet is untrue.",
        ))
    return checks, findings


def _bullet_length_findings(audit: dict) -> tuple:
    if audit.get("status") != "EVALUATED":
        return [_check("content_quality.short_bullets", "content_quality",
                       "not_evaluated", audit.get("reason"))], []

    short = audit["short_bullets"]
    checks = [_check(
        "content_quality.short_bullets", "content_quality",
        "review" if short else "pass",
        f"{len(short)} of {audit['bullets_measured']} bullets below "
        f"{audit['threshold']} visible characters",
    )]
    findings = []
    for row in short:
        findings.append(_finding(
            "content_quality.short_bullet", "content_quality", "low", "review", row["id"],
            f"This bullet is unusually short ({row['visible_chars']} visible characters, "
            f"advisory threshold {row['threshold']}).",
            {
                "field_id": row["id"],
                "section": row["section"],
                "visible_chars": row["visible_chars"],
                "threshold": row["threshold"],
                "threshold_note": "provisional heuristic, not an industry-standard cutoff",
            },
            "Advisory only, and non-blocking. A short bullet that already states a clear, "
            "verified result needs no change. If a verified detail from the candidate's own "
            "records was dropped during tailoring it may be restored; never pad a bullet to "
            "reach a character count, and never change what it claims.",
        ))
    return checks, findings


def _timeline_findings(audit: dict) -> tuple:
    status_map = {"EVALUATED": None, "NOT_EVALUATED": "not_evaluated", "UNDETERMINED": "undetermined"}
    if audit.get("status") != "EVALUATED":
        return [_check("timeline.experience_gaps", "timeline",
                       status_map.get(audit.get("status"), "not_evaluated"), audit.get("reason"))], []

    intervals = audit["intervals"]
    unexplained = [i for i in intervals if i["classification"] != "explained_by_education"]
    checks = [_check(
        "timeline.experience_gaps", "timeline",
        "review" if unexplained else "pass",
        f"{len(intervals)} interval(s) at or above the {audit['gap_review_months']}-month "
        "review threshold" if intervals else "no interval reached the review threshold",
    )]

    findings = []
    for interval in intervals:
        findings.append(_finding(
            "timeline.experience_interval", "timeline", "info", interval["classification"],
            f"{interval['from']}..{interval['to']}",
            f"A {interval['months']}-month interval between consecutive experience periods: "
            f"{interval['reason']}.",
            {
                "from": interval["from"],
                "to": interval["to"],
                "months": interval["months"],
                "between": interval["between"],
                "education_months_covered": interval["education_months_covered"],
                "review_threshold_months": audit["gap_review_months"],
            },
            "Informational only -- never a gate failure and never a reason to add, move or "
            "invent an entry. Use it to decide whether existing, already-documented "
            "information is worth surfacing.",
        ))
    return checks, findings


def _style_findings(action_verb_report: Optional[dict]) -> tuple:
    """Reuses the action-verb result the pipeline already computed. This
    function never calls `action_verb_diversity` -- running that checker a
    second time is exactly what the consolidation is meant to avoid."""
    if not isinstance(action_verb_report, dict) or not action_verb_report:
        return [_check("style.action_verb_diversity", "style", "not_evaluated",
                       "the pipeline did not supply an action-verb result to reuse")], []

    repeated = action_verb_report.get("repeated_over_limit") or {}
    checks = [_check(
        "style.action_verb_diversity", "style",
        "review" if repeated else "pass",
        "reused verbatim from the pipeline's own action_verb_diversity run; not recomputed",
    )]
    findings = []
    if repeated:
        findings.append(_finding(
            "style.action_verb_repetition", "style", "low", "review",
            "experience+projects bullets",
            "One or more opening action verbs are still repeated above the configured limit "
            "after the pipeline's own handling of this check.",
            {
                "repeated_over_limit": repeated,
                "limit": action_verb_report.get("limit"),
                "source": "action_verb_diversity (pipeline result, reused)",
            },
            "No action from this report: the existing pipeline already decides whether this "
            "is correctable and already surfaces it as a non-blocking warning.",
        ))
    return checks, findings


def _job_match_findings(match: Optional[dict]) -> tuple:
    """Turn the advisory JD->resume match report into report findings.

    Severity is derived from ONE input signal: the job description's own
    `required` flag. `category`, `priority` and `importance` are carried
    into each finding's payload so a reader can sort and filter on them,
    but they deliberately do not feed severity -- combining them would
    mean inventing relative weights this project has not justified.
    Nothing here is added up and no score is produced.
    """
    if not match:
        # `not_applicable`, deliberately NOT `not_evaluated`. A job
        # directory with no `jd_match_report.json` is a job for which
        # requirement matching was simply not run -- which is the normal
        # case for every pre-existing caller. `not_evaluated` means "this
        # was attempted and could not be completed" and degrades the
        # whole report to INCOMPLETE; using it here would make an absent
        # OPTIONAL input look like a failed check on every job.
        return [_check("job_match.requirement_coverage", "job_match",
                       "not_applicable",
                       "no jd_match_report for this job; requirement matching was "
                       "not run for this invocation")], []

    rows = match.get("requirements") or []
    summary = match.get("summary") or {}
    required = summary.get("required") or {}

    checks = [_check(
        "job_match.requirement_coverage", "job_match",
        "review" if (summary.get("ambiguous_requiring_review") or summary.get("opportunities")) else "pass",
        f"{len(rows)} requirements evaluated; "
        f"required exact={required.get('exact', 0)} semantic={required.get('semantic', 0)} "
        f"partial={required.get('partial', 0)} none={required.get('none', 0)} "
        f"ambiguous={required.get('ambiguous', 0)}; "
        f"{summary.get('opportunities', 0)} evidence-backed opportunities",
    )]

    findings = []
    for row in rows:
        term = row.get("term")
        is_required = row.get("required")
        base = {
            "term": term,
            "category": row.get("category"),
            "required": is_required,
            "priority": row.get("priority"),
            "importance": row.get("importance"),
            "match_class": row.get("match_class"),
            "match_basis": row.get("match_basis"),
            "matched_form": row.get("matched_form"),
            "evidence_support": row.get("evidence_support"),
            "evidence_hit_count": len(row.get("evidence_hits") or []),
        }

        if row.get("match_class") == "ambiguous":
            findings.append(_finding(
                "job_match.ambiguous_requirement", "job_match",
                "medium" if is_required else "low", "ambiguous", term,
                "This requirement's signals conflict and a human has to decide: "
                + str(row.get("ambiguity_reason")),
                base,
                "Human review required. Confirm against the candidate's own records "
                "before relying on this requirement either way. Do not resolve it by "
                "adding wording the evidence bank cannot corroborate.",
            ))
            continue

        if row.get("opportunity"):
            findings.append(_finding(
                "job_match.supported_but_not_surfaced", "job_match",
                "medium" if is_required else "low", "opportunity", term,
                "The candidate's verified evidence supports this requirement, but the "
                "resume does not surface it in terms a reader or scanner would match.",
                dict(base, evidence_excerpt=_excerpt(
                    (row.get("evidence_hits") or [{}])[0].get("text", "")
                )),
                "Candidate for an evidence-backed rewrite using the candidate's own "
                "verified wording. Any proposal must pass recommendation_guard.py "
                "before it is acted on; never introduce a measurement the cited "
                "evidence does not already state.",
            ))
            continue

        if row.get("match_class") == "none" and is_required:
            findings.append(_finding(
                "job_match.required_requirement_unmet", "job_match",
                "medium", "gap", term,
                "A required requirement with no supporting evidence and no presence in "
                "the resume. This is a genuine gap.",
                base,
                "Report the gap honestly. Do not add this to the resume: experience that "
                "exists only in the job description must never become candidate history.",
            ))
            continue

        if row.get("match_class") == "semantic":
            findings.append(_finding(
                "job_match.semantic_match_for_review", "job_match", "info", "semantic", term,
                "Matched through a reviewed terminology bridge rather than the job "
                f"description's own wording (matched {row.get('matched_form')!r}).",
                base,
                "No action required. Listed so a reviewer who disagrees with the bridge "
                "can see exactly which phrase satisfied the requirement.",
            ))

    return checks, findings


def _job_match_block(match: Optional[dict]) -> dict:
    """The compact job-fit summary carried in the consolidated report.

    Counts per bucket and nothing else. There is deliberately no overall
    percentage here: the per-requirement detail lives in the job's own
    `jd_match_report.json`, and collapsing it into one number would
    invent a weighting this project has not justified and cannot
    validate against any external checker's methodology.
    """
    if not match:
        return {"available": False, "reason": "no jd_match_report for this job"}
    summary = match.get("summary") or {}
    return {
        "available": True,
        "job_key": match.get("job_key"),
        "requirements_evaluated": match.get("requirements_evaluated"),
        "required": summary.get("required"),
        "preferred": summary.get("preferred"),
        "evidence_support": summary.get("evidence_support"),
        "opportunities": summary.get("opportunities"),
        "ambiguous_requiring_review": summary.get("ambiguous_requiring_review"),
        "composite_score": None,
        "composite_score_note": (
            "Intentionally absent. External ATS scores are produced by external "
            "services whose methodology this project does not model, imitate or "
            "predict."
        ),
    }


def _template_fidelity_block(audit: Optional[dict]) -> dict:
    if not audit:
        return {"available": False, "reason": "no template style audit for this job"}
    return {
        "available": True,
        "status": audit.get("status"),
        "template": audit.get("template"),
        "paragraphs_compared": audit.get("paragraphs_compared"),
        "fidelity_preserved": audit.get("fidelity_preserved"),
        "difference_count": len(audit.get("findings") or []),
    }


def _template_fidelity_findings(audit: Optional[dict]) -> tuple:
    """Did rendering change anything about the document but its words?"""
    if not audit:
        # See `_job_match_findings`: absent optional input is
        # `not_applicable`, not a failed check.
        return [_check("design.template_fidelity", "design", "not_applicable",
                       "no template style audit for this job; render-fidelity "
                       "checking was not run for this invocation")], []

    # Present but unusable IS a genuine `not_evaluated` -- something was
    # supplied and could not be read.
    if audit.get("status") != "EVALUATED":
        return [_check("design.template_fidelity", "design", "not_evaluated",
                       audit.get("reason"))], []

    raw = audit.get("findings") or []
    checks = [_check(
        "design.template_fidelity", "design",
        "pass" if audit.get("fidelity_preserved") else "review",
        f"{len(raw)} differences between the rendered document and its template",
    )]

    findings = []
    for item in raw:
        findings.append(_finding(
            f"design.{item.get('check')}", "design",
            item.get("severity", "medium"), "drift", item.get("subject"),
            item.get("message"),
            {"expected": item.get("expected"), "actual": item.get("actual")},
            "The template is the source of truth for appearance. Restore the template's "
            "value rather than adjusting the template to match the renderer, unless the "
            "design itself was deliberately changed.",
        ))
    return checks, findings


def _summary_section_check() -> tuple:
    checks = [_check(
        "content_quality.professional_summary", "content_quality", "not_applicable",
        SUMMARY_NOT_APPLICABLE_MESSAGE,
    )]
    findings = [_finding(
        "content_quality.professional_summary", "content_quality", "info", "not_applicable",
        "professional summary",
        SUMMARY_NOT_APPLICABLE_MESSAGE,
        {"section_present": False, "section_expected_by_this_format": False},
        "No action. Do not add a summary section or restructure the resume to satisfy an "
        "external scanner's expectation of one.",
    )]
    return checks, findings


# ---------------------------------------------------------------------------
# report assembly
# ---------------------------------------------------------------------------

def summarize(findings: list) -> dict:
    summary = {severity: 0 for severity in SEVERITIES}
    for finding in findings:
        if finding["severity"] in summary:
            summary[finding["severity"]] += 1
    summary["total"] = len(findings)
    return summary


def diagnostic_state(checks: list, summary: dict) -> str:
    if summary.get("high") or summary.get("medium"):
        return "REVIEW"
    if any(check["status"] == "not_evaluated" for check in checks):
        return "INCOMPLETE"
    if summary.get("low"):
        return "REVIEW"
    return "PASS"


def _maybe_load(path: Optional[Path]) -> Optional[dict]:
    if path is None:
        return None
    resolved = Path(path)
    if not resolved.exists():
        return None
    try:
        data = load_json(resolved)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def build_report(
    job_dir: Path,
    job_key: str,
    *,
    qa: Optional[dict] = None,
    action_verb_report: Optional[dict] = None,
    config: Optional[dict] = None,
    runtime_context_path: Optional[Path] = None,
    metrics_registry_path: Optional[Path] = None,
    finalization_record: Optional[dict] = None,
    finalization_attempted: bool = False,
    resume_pdf_name: Optional[str] = None,
    metrics_entity_content_map: Optional[dict] = None,
    jd_match: Optional[dict] = None,
    template_style_audit: Optional[dict] = None,
) -> dict:
    """Build (but do not write) one job's consolidated report.

    Everything is read from the job's own directory, except the two
    candidate-level schemas the metrics and timeline audits need --
    `runtime_resume_context.json` and `metrics_registry.json` -- which
    default to the same canonical paths `qa_resume.py` and
    `build_shared_context.py` already read, and are simply treated as
    unavailable (`not_evaluated`) when absent.
    """
    job_dir = Path(job_dir)
    config = config or load_config()

    resolved_pdf = resolve_resume_pdf(job_dir, resume_pdf_name)
    draft_path = job_dir / RESUME_DRAFT_NAME

    if qa is None:
        qa = _maybe_load(job_dir / "resume_qa.json")
    if finalization_record is None:
        finalization_record = _maybe_load(job_dir / "watermark_finalization.json")
    density_guard = _maybe_load(job_dir / "density_guard.json")
    resume_payload = _maybe_load(job_dir / "resume_payload.json") or {}

    # Both are produced by separate, independently runnable tools. When a
    # job directory has them they are folded in; when it does not, the
    # corresponding checks report `not_evaluated` rather than failing --
    # exactly like every other optional input here.
    if jd_match is None:
        jd_match = _maybe_load(job_dir / "jd_match_report.json")
    if template_style_audit is None:
        style_report = _maybe_load(job_dir / "template_style_audit.json")
        if style_report:
            template_style_audit = style_report.get("render_fidelity") or style_report

    runtime_context = _maybe_load(
        Path(runtime_context_path) if runtime_context_path else DEFAULT_RUNTIME_CONTEXT
    )
    metrics_registry = _maybe_load(
        Path(metrics_registry_path) if metrics_registry_path else DEFAULT_METRICS_REGISTRY
    )

    draft_model = resume_draft_model.parse_resume_markdown_file(draft_path)
    expected_fields = draft_model["fields"]

    roundtrip = pdf_roundtrip.run(
        resolved_pdf["path"],
        expected_fields,
        partial_min_ratio=config["partial_match_min_token_ratio"],
        min_readable_chars=int(config["min_readable_text_chars"]),
    )

    # Registry shape adaptation happens at ONE explicit boundary (see
    # metrics_registry_adapter). A canonical registry passes through
    # untouched; an entity-keyed one is adapted only as far as the
    # caller's explicit entity->content map allows, and anything it
    # cannot link is reported as not_evaluated rather than guessed at.
    canonical_registry, content_map, adaptation = metrics_registry_adapter.normalize_registry(
        metrics_registry, metrics_entity_content_map,
    )
    metrics_result = metrics_audit.audit_metrics(
        runtime_context or {}, canonical_registry, resume_payload, content_map,
    )
    metrics_result = metrics_registry_adapter.merge_into_audit(metrics_result, adaptation)
    bullet_result = bullet_length_audit.audit_bullet_lengths(
        resume_draft_model.substantive_bullets(draft_model),
        threshold=int(config["short_bullet_min_visible_chars"]),
    )
    timeline_result = timeline_audit.audit_timeline(
        runtime_context or {},
        gap_review_months=int(config["timeline_gap_review_months"]),
    )

    checks: list = []
    findings: list = []
    for block_checks, block_findings in (
        _artifact_findings(roundtrip, qa, resolved_pdf),
        _density_check(qa, density_guard, resolved_pdf),
        _metrics_findings(metrics_result),
        _bullet_length_findings(bullet_result),
        _timeline_findings(timeline_result),
        _style_findings(action_verb_report),
        _summary_section_check(),
        _job_match_findings(jd_match),
        _template_fidelity_findings(template_style_audit),
    ):
        checks.extend(block_checks)
        findings.extend(block_findings)

    severity_rank = {severity: index for index, severity in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (severity_rank.get(f["severity"], len(SEVERITIES)), f["id"], str(f["subject"])))

    summary = summarize(findings)

    notes = []
    if roundtrip.get("status") != "EVALUATED":
        notes.append(f"PDF round-trip not evaluated: {roundtrip.get('reason')}")
    if metrics_result.get("status") != "EVALUATED":
        notes.append(f"Metrics audit not evaluated: {metrics_result.get('reason')}")
    adaptation_note = metrics_result.get("registry_adaptation")
    if adaptation_note:
        notes.append(
            f"Metrics registry adapted from shape {adaptation_note.get('shape')!r}: "
            f"{adaptation_note.get('adapted')} of {adaptation_note.get('items_seen')} items linked "
            f"via {adaptation_note.get('map_entries')} explicit entity mapping(s)"
        )
    if timeline_result.get("status") != "EVALUATED":
        notes.append(f"Timeline audit {timeline_result.get('status').lower()}: {timeline_result.get('reason')}")
    elif timeline_result.get("reason"):
        notes.append(f"Timeline audit caveat: {timeline_result['reason']}")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "job": job_key,
        "artifact": _artifact_block(resolved_pdf, finalization_record, finalization_attempted),
        "expected_content_source": {
            "path": RESUME_DRAFT_NAME,
            "exists": draft_path.exists(),
            "fields_parsed": len(expected_fields),
            "substantive_bullets": len(resume_draft_model.substantive_bullets(draft_model)),
        },
        "thresholds": {key: config[key] for key in CONFIG_DEFAULTS},
        "thresholds_source": config.get("_source"),
        "job_match": _job_match_block(jd_match),
        "template_fidelity": _template_fidelity_block(template_style_audit),
        "checks": checks,
        "findings": findings,
        "summary_by_severity": summary,
        "diagnostic_state": diagnostic_state(checks, summary),
        "notes": notes,
        "reused_results": {
            "action_verb_diversity": action_verb_report,
            "qa_status": (qa or {}).get("status"),
            "density_guard_status": (density_guard or {}).get("status"),
        },
        "advisory_only": True,
        "advisory_note": (
            "Every finding here is diagnostic. This report never changes a job's status, "
            "never triggers a correction round, and never produces a numeric ATS score."
        ),
    }


def report_path(job_dir: Path) -> Path:
    return Path(job_dir) / REPORT_FILENAME


def generate(job_dir: Path, job_key: str, **kwargs) -> dict:
    """Build + write one job's report.

    NEVER raises. Both halves are guarded independently, because both can
    fail for reasons that have nothing to do with the job's real outcome:
    building can hit malformed input, and WRITING can hit a read-only
    directory, a full disk, or a permission problem. An advisory
    diagnostic that escaped here would propagate out of
    `run_job_pipeline` (documented never to raise for an ordinary job
    failure) and out of `run_batch.process_job`'s try/finally, where
    `Batch.run`'s `future.result()` would re-raise it and take down the
    whole batch. A failure to produce a diagnostic is recorded in the
    returned report instead.
    """
    job_dir = Path(job_dir)
    path = report_path(job_dir)
    try:
        report = build_report(job_dir, job_key, **kwargs)
    except Exception as exc:  # a diagnostic must never break a job
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "job": job_key,
            "artifact": {"path": kwargs.get("resume_pdf_name") or RESUME_PDF_NAME,
                         "expected": kwargs.get("resume_pdf_name") or RESUME_PDF_NAME,
                         "resolution": "not_evaluated", "candidates": [],
                         "exists": False, "sha256": None, "stage": "unknown",
                         "stage_reason": "report generation failed before the artifact was inspected"},
            "checks": [], "findings": [],
            "summary_by_severity": summarize([]),
            "diagnostic_state": "INCOMPLETE",
            "notes": [f"resume-quality report generation failed: {type(exc).__name__}: {exc}"],
            "advisory_only": True,
        }

    try:
        write_json(path, report)
        report["written_to"] = REPORT_FILENAME
    except Exception as exc:
        report["written_to"] = None
        report.setdefault("notes", []).append(
            f"resume-quality report could not be written: {type(exc).__name__}: {exc}"
        )
        report["diagnostic_state"] = "INCOMPLETE"
    return report


def refresh_after_finalization(job_dir: Path, job_key: str, **kwargs) -> dict:
    """Re-run the artifact-dependent checks against `resume.pdf` as it
    exists AFTER finalization, so `artifact.stage`/`artifact.sha256`
    describe the file that actually ships.

    The action-verb result is carried over from the pre-finalization
    report rather than recomputed -- `action_verb_diversity` still runs
    exactly once per job, in the deterministic pipeline.

    Reading the previous report back is not a cross-attempt state leak:
    `run_batch.py` only reaches this call after `run_job_pipeline` has
    returned a SUCCESS status in this same attempt, and that success path
    always rewrites the report first. If the file is somehow absent or
    unreadable, the style check simply reports `not_evaluated` rather
    than reusing anything stale.
    """
    job_dir = Path(job_dir)
    existing = _maybe_load(report_path(job_dir)) or {}
    carried = (existing.get("reused_results") or {}).get("action_verb_diversity")
    kwargs.setdefault("action_verb_report", carried)
    # This call happens strictly after the finalizer returned, so a
    # missing watermark_finalization.json means "finalization ran and
    # promoted nothing", not "finalization has not happened yet".
    kwargs.setdefault("finalization_attempted", True)
    return generate(job_dir, job_key, **kwargs)


def console_summary(report: dict, job_dir: Path) -> dict:
    """The small, candidate-content-free record the pipeline result and
    the batch console summary carry. Detailed findings stay in the
    job-local, git-ignored report file."""
    job_dir = Path(job_dir)
    try:
        relative = str(report_path(job_dir).relative_to(ROOT))
    except ValueError:
        relative = str(report_path(job_dir))
    return {
        "path": relative,
        "diagnostic_state": report.get("diagnostic_state"),
        "artifact_stage": (report.get("artifact") or {}).get("stage"),
        "summary_by_severity": report.get("summary_by_severity"),
        "advisory_only": True,
    }
