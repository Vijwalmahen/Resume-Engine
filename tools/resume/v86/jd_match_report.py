#!/usr/bin/env python3
"""
Explainable job-description -> resume requirement matching (ADVISORY).

This is the diagnostic counterpart to `ats_preflight_v86.py`. It answers a
different question and must not be confused with it:

  ats_preflight_v86.py  GATES. It decides whether a generated resume is
                        allowed to proceed, using a deliberately coarse
                        three-state classification
                        (SUPPORTED_AND_PRESENT / SUPPORTED_BUT_MISSING /
                        UNSUPPORTED) and a documented coverage rule.

  jd_match_report.py    EXPLAINS. It produces a per-requirement, reviewable
                        account of HOW each job requirement is (or is not)
                        reflected in the resume, so a human can see which
                        matches are literal, which rely on a reviewed
                        terminology bridge, which are only partial, and
                        which need judgement. It gates nothing.

Both read the SAME `jd_requirements.json` and reuse the SAME matching
primitives from the frozen `tools/ats/preflight.py` (`normalize`,
`phrase_present`, `concept_forms`, `find_evidence`, `priority_value`) plus
`ats_preflight_v86.filter_negated_evidence_hits`. There is deliberately no
second implementation of phrase matching, alias expansion or negation
handling in this file -- if the two reports ever disagreed about whether a
phrase is present, that would be a bug, not a feature.

Two orthogonal axes, reported separately
----------------------------------------

Collapsing "is it in the resume?" and "can the candidate's evidence back
it?" into one verdict is what makes keyword reports misleading. They are
kept apart here:

  match_class      RESUME side -- how the requirement surfaces in the
                   resume text that will actually be read by a human or an
                   ATS:

                     exact     a literal form of the requirement (the JD's
                               own `term`/`exact`/`canonical` wording)
                               appears verbatim.
                     semantic  no literal form appears, but a reviewed
                               alias/bridge form does. The specific form
                               that matched is always recorded, so a
                               semantic match can be audited and rejected
                               by a human who disagrees with the bridge.
                     partial   no complete form appears, but enough of the
                               requirement's content words do (see
                               `token_coverage`) that the resume is
                               plausibly gesturing at it. Advisory signal
                               only.
                     none      nothing matched.
                     ambiguous the signals conflict and a human must
                               decide. See `ambiguity_reason`.

  evidence_support MASTER-EVIDENCE side -- whether the candidate's own
                   verified evidence can back the requirement at all:

                     supported     at least one non-negated evidence hit.
                     negated_only   every hit was excluded by the negation
                                   guard, i.e. the candidate's own evidence
                                   explicitly rules the thing out.
                     unsupported   no hits.

The pair is what makes a finding actionable, and the combinations mean
genuinely different things:

  supported + none/partial   -> OPPORTUNITY. Real, verified experience that
                                the resume is simply not surfacing. This is
                                the only quadrant where "add it to the
                                resume" is a legitimate recommendation.
  unsupported + exact/semantic -> AMBIGUOUS. The resume says something the
                                master evidence cannot corroborate. Flagged
                                for review rather than silently trusted;
                                this is an integrity signal, not a win.
  negated_only + anything    -> AMBIGUOUS. The candidate's evidence
                                explicitly contradicts the requirement.
                                Never an opportunity, never auto-filled.
  unsupported + none         -> a genuine gap. Reported honestly. Nothing
                                in this module ever proposes closing it.

What this module deliberately does NOT do
-----------------------------------------

  - It computes no composite score, no weighted total and no "match
    percentage" out of 100. External checkers (ToolAdda, Jobscan) produce
    scores with methodologies this project does not know and must not
    guess at or imitate. Requirement counts per bucket are reported as
    plain counts, because a count is a fact; a weighted score would be an
    invention.
  - It applies no weights of its own. Requirement importance comes
    entirely from the JD analysis that already ran (`required`,
    `category`, `priority`), surfaced through the frozen
    `priority_value`. Buckets exist so that a missing REQUIRED concept is
    not displayed next to a missing nice-to-have as though they were
    equivalent -- not so that they can be multiplied together.
  - It changes no job status, triggers no correction round and causes no
    model call.
  - A keyword being present is reported as a keyword being present. It is
    never reported as proficiency.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ats"))

import preflight as frozen  # noqa: E402  (tools/ats/preflight.py, unmodified)

import ats_preflight_v86  # noqa: E402

SCHEMA_VERSION = "1.0"
REPORT_FILENAME = "jd_match_report.json"

# A requirement with no complete form in the resume is called `partial`
# when at least this fraction of its content words appear as whole words.
# A provisional, documented heuristic for drawing a human's attention --
# never a correctness rule, never a gate, and never a score component.
DEFAULT_PARTIAL_MIN_TOKEN_COVERAGE = 0.6

# Content-word filter for token coverage only. Dropping these stops a
# requirement like "experience with Kubernetes" from reading as 66%
# covered just because "experience" and "with" occur somewhere.
_STOPWORDS = frozenset(
    """
    a an and or the of in on for to with without using use used via by at as
    from into over under about across is are be been being has have had do
    does did will would can could should may might must not no nor but if
    then than that this these those it its their our your his her they them
    we you i he she who whom which what when where why how all any both each
    few more most other some such only own same so too very s t just dont
    experience experienced working work works knowledge familiarity strong
    excellent good solid proven demonstrated ability skills skill plus
    preferred required desirable nice
    """.split()
)

MATCH_CLASSES = ("exact", "semantic", "partial", "none", "ambiguous")
EVIDENCE_STATES = ("supported", "negated_only", "unsupported")


# ---------------------------------------------------------------------------
# form partitioning
# ---------------------------------------------------------------------------
#
# Imported, not reimplemented. `ats_preflight_v86` owns the literal/bridged
# partition (its Fix 7) because the GATE emits the same labels; if this file
# kept its own copy, the explainable report and the gate could drift into
# disagreeing about whether a match was exact or semantic.

literal_forms = ats_preflight_v86.literal_forms
partition_forms = ats_preflight_v86.partition_forms
first_present_form = ats_preflight_v86.first_present_form


# ---------------------------------------------------------------------------
# partial-match heuristic
# ---------------------------------------------------------------------------

def content_tokens(phrase: str) -> list:
    """Normalized content words of a requirement, stopwords removed."""
    return [
        token for token in frozen.normalize(phrase).split()
        if token and token not in _STOPWORDS
    ]


def token_coverage(phrase: str, text: str) -> dict:
    """Fraction of a requirement's content words present in `text`.

    Deliberately unordered and position-blind: this exists only to notice
    that a resume talks about the pieces of a requirement without using
    the requirement's exact phrase. It is reported alongside the tokens it
    found and missed so a reader can judge it, and it never upgrades a
    requirement past `partial`.
    """
    tokens = content_tokens(phrase)
    if not tokens:
        return {"coverage": 0.0, "tokens": [], "present": [], "missing": []}
    present = [token for token in tokens if frozen.phrase_present(text, token)]
    missing = [token for token in tokens if token not in present]
    return {
        "coverage": round(len(present) / len(tokens), 3),
        "tokens": tokens,
        "present": present,
        "missing": missing,
    }


# ---------------------------------------------------------------------------
# per-requirement classification
# ---------------------------------------------------------------------------

def classify_requirement(
    concept: dict,
    resume_text: str,
    evidence_records: list,
    aliases: dict,
    partial_min_coverage: float = DEFAULT_PARTIAL_MIN_TOKEN_COVERAGE,
) -> dict:
    """Classify one JD requirement on both axes. Pure; no IO."""
    term = (
        concept.get("term")
        or concept.get("exact")
        or concept.get("canonical")
        or ""
    )
    literal, bridged = partition_forms(concept, aliases)
    every_form = literal + bridged

    # --- evidence axis (master evidence, negation-filtered) --------------
    raw_hits = frozen.find_evidence(evidence_records, every_form)
    kept_hits, negated_hits = ats_preflight_v86.filter_negated_evidence_hits(
        raw_hits, every_form
    )
    if kept_hits:
        evidence_support = "supported"
    elif negated_hits:
        evidence_support = "negated_only"
    else:
        evidence_support = "unsupported"

    # --- resume axis ------------------------------------------------------
    exact_form = first_present_form(resume_text, literal)
    bridge_form = None if exact_form else first_present_form(resume_text, bridged)

    coverage = token_coverage(term, resume_text)

    if exact_form:
        match_class = "exact"
        matched_form = exact_form
        match_basis = "literal_jd_wording"
    elif bridge_form:
        match_class = "semantic"
        matched_form = bridge_form
        match_basis = "reviewed_alias_or_bridge"
    elif coverage["coverage"] >= partial_min_coverage:
        match_class = "partial"
        matched_form = None
        match_basis = "content_word_overlap_only"
    else:
        match_class = "none"
        matched_form = None
        match_basis = None

    # --- reconciliation: where the two axes conflict ---------------------
    ambiguity_reason = None
    if evidence_support == "negated_only":
        # The candidate's own evidence explicitly rules this out. Whatever
        # the resume says, a human has to look.
        ambiguity_reason = (
            "master evidence mentions this requirement only in a negated form, so it "
            "cannot be treated as supported; a human must confirm whether the "
            "candidate has this experience at all"
        )
    elif evidence_support == "unsupported" and match_class in ("exact", "semantic"):
        # The resume asserts something master evidence cannot corroborate.
        # This is an integrity signal, deliberately NOT reported as a win.
        ambiguity_reason = (
            "the resume surfaces this requirement but no master evidence record "
            "corroborates it; confirm the claim is backed by a verified source "
            "before relying on it"
        )

    if ambiguity_reason:
        resume_match_class = match_class
        match_class = "ambiguous"
    else:
        resume_match_class = match_class

    opportunity = (
        evidence_support == "supported"
        and resume_match_class in ("none", "partial")
        and ambiguity_reason is None
    )

    return {
        "term": term,
        "canonical": concept.get("canonical"),
        "category": concept.get("category"),
        "required": bool(concept.get("required")),
        "priority": concept.get("priority"),
        "importance": frozen.priority_value(concept),
        "match_class": match_class,
        "resume_match_class": resume_match_class,
        "match_basis": match_basis,
        "matched_form": matched_form,
        "literal_forms_checked": literal,
        "bridged_forms_checked": bridged,
        "token_coverage": coverage,
        "evidence_support": evidence_support,
        "evidence_hits": kept_hits,
        "negated_evidence_hits_excluded": negated_hits,
        "opportunity": opportunity,
        "ambiguity_reason": ambiguity_reason,
        "proficiency_claim": None,
        "proficiency_note": (
            "Presence of a term in the resume is evidence that the term appears, "
            "not evidence of proficiency. Absence of a term is evidence the term "
            "is absent, not evidence the candidate lacks the skill."
        ),
    }


# ---------------------------------------------------------------------------
# aggregation
# ---------------------------------------------------------------------------

def _bucket(rows: list) -> dict:
    """Plain counts per match class. Counts only -- never a score."""
    counts = {name: 0 for name in MATCH_CLASSES}
    for row in rows:
        counts[row["match_class"]] = counts.get(row["match_class"], 0) + 1
    counts["total"] = len(rows)
    return counts


def summarize(rows: list) -> dict:
    """Group requirements the way a reviewer needs to read them.

    Required and preferred are never merged, because a missing required
    qualification and a missing nice-to-have are not the same finding.
    No bucket is combined into an overall number.
    """
    required = [row for row in rows if row["required"]]
    preferred = [row for row in rows if not row["required"]]
    return {
        "all": _bucket(rows),
        "required": _bucket(required),
        "preferred": _bucket(preferred),
        "by_category": {
            category: _bucket([r for r in rows if r.get("category") == category])
            for category in sorted(
                {str(r.get("category")) for r in rows if r.get("category")}
            )
        },
        "evidence_support": {
            state: sum(1 for r in rows if r["evidence_support"] == state)
            for state in EVIDENCE_STATES
        },
        "opportunities": sum(1 for r in rows if r["opportunity"]),
        "ambiguous_requiring_review": sum(
            1 for r in rows if r["match_class"] == "ambiguous"
        ),
        "scoring_note": (
            "These are counts of requirements per bucket, not a score. This project "
            "does not compute a composite match percentage and does not model any "
            "external checker's methodology."
        ),
    }


def build_report(
    requirements: dict,
    resume_text: str,
    evidence_root: Path,
    aliases: dict,
    partial_min_coverage: float = DEFAULT_PARTIAL_MIN_TOKEN_COVERAGE,
) -> dict:
    concepts = requirements.get("concepts")
    if not isinstance(concepts, list):
        raise ValueError("jd_requirements must contain a concepts list.")

    evidence_records = frozen.load_evidence(Path(evidence_root))
    rows = [
        classify_requirement(
            concept, resume_text, evidence_records, aliases, partial_min_coverage
        )
        for concept in concepts
    ]

    # Most important first, then the ones a human must act on.
    order = {name: index for index, name in enumerate(
        ("ambiguous", "none", "partial", "semantic", "exact")
    )}
    rows.sort(key=lambda r: (
        -r["importance"],
        not r["required"],
        order.get(r["match_class"], len(order)),
        str(r["term"]).lower(),
    ))

    return {
        "schema_version": SCHEMA_VERSION,
        "job_key": requirements.get("job_key"),
        "requirements_evaluated": len(rows),
        "thresholds": {"partial_min_token_coverage": partial_min_coverage},
        "summary": summarize(rows),
        "requirements": rows,
        "advisory_only": True,
        "advisory_note": (
            "Diagnostic only. This report never changes a job's status, never "
            "triggers a correction round, never causes a model call and never "
            "produces a composite ATS score. Gating remains entirely with "
            "ats_preflight_v86.py."
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Explainable, advisory JD->resume requirement matching."
    )
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--resume", required=True)
    parser.add_argument("--evidence-root", default="master")
    parser.add_argument("--aliases", default=None)
    parser.add_argument("--extra-aliases", default=None)
    parser.add_argument(
        "--partial-min-token-coverage",
        type=float,
        default=DEFAULT_PARTIAL_MIN_TOKEN_COVERAGE,
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    requirements_path = Path(args.requirements)
    resume_path = Path(args.resume)
    evidence_root = Path(args.evidence_root)

    for path, label in (
        (requirements_path, "requirements file"),
        (resume_path, "resume draft"),
        (evidence_root, "evidence directory"),
    ):
        if not path.exists():
            raise SystemExit(f"ERROR: Missing {label}: {path}")

    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    resume_text = resume_path.read_text(encoding="utf-8", errors="replace")
    aliases = ats_preflight_v86.load_and_merge_aliases(
        args.aliases, args.extra_aliases
    )

    report = build_report(
        requirements,
        resume_text,
        evidence_root,
        aliases,
        args.partial_min_token_coverage,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    summary = report["summary"]
    print()
    print("JD MATCH REPORT (advisory -- gates nothing)")
    print("=" * 60)
    for label in ("required", "preferred"):
        bucket = summary[label]
        print(
            f"  {label:9} n={bucket['total']:<3} "
            f"exact={bucket['exact']} semantic={bucket['semantic']} "
            f"partial={bucket['partial']} none={bucket['none']} "
            f"ambiguous={bucket['ambiguous']}"
        )
    print(f"  evidence-backed opportunities : {summary['opportunities']}")
    print(f"  requiring human review        : {summary['ambiguous_requiring_review']}")
    print(f"  Report: {output_path}")


if __name__ == "__main__":
    main()
