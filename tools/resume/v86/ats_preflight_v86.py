#!/usr/bin/env python3
"""
V8.6.1 -- adapted copy of tools/ats/preflight.py (Fix 2 / Fix 3).

This is intentionally a NEW file, not a modification of the frozen
`tools/ats/preflight.py` (which stays byte-identical, and is still what
V8.5 uses). It imports and reuses that module's pure helper functions
(`normalize`, `phrase_present`, `load_evidence`, `load_aliases`,
`concept_forms`, `find_evidence`, `priority_value`, `percentage`)
unmodified, so there is exactly one implementation of the normalization/
matching primitives -- only the gate-decision and alias-merging logic
below is new.

Fix 2 (deterministic ATS semantic-evidence recognition):
  `--extra-aliases` (`config/v86/ats_concept_bridges.json`) is loaded on top
  of `--aliases` (the frozen `config/ats_aliases.json`, untouched) so a
  small set of explicitly reviewed concept bridges (e.g. "LLM evaluation"
  <-> "LLM-as-judge verification") can satisfy a JD concept via truthful
  canonical evidence/resume wording without requiring the JD's exact
  phrasing. This is NOT fuzzy matching: every bridge is a literal,
  reviewed phrase, checked with the exact same word-boundary
  `phrase_present` test as every other alias.

Fix 3 (optional/preferred ATS policy):
  The frozen script's `gate_pass` folds EVERY high-importance concept
  (importance >= 4, which includes any `CORE_RESPONSIBILITY` or
  priority>=high concept regardless of `required`) into one coverage
  percentage, so a single OPTIONAL (`required: false`) supported-but-
  missing concept can terminally block an otherwise-valid resume. This
  version's `gate_pass` instead requires:
    - no REQUIRED+supported concept missing from the resume (unchanged
      from the frozen script), AND
    - >=90% coverage among REQUIRED, high-importance, supported concepts
      only (`required_high_priority_concept_coverage_percent`).
  Optional supported-but-missing concepts are still fully reported (as
  `optional_supported_but_missing`) for the correction worker to
  opportunistically address, but can never by themselves fail the gate.
  The original all-concepts number is preserved as
  `high_priority_concept_coverage_percent` for backward-compatible
  reporting; it is informational only in this script and does not gate.

Fix 6 (V8.6.8 round 2 -- negated-evidence guard):
  Root cause this closes:
  the frozen `find_evidence`'s substring/word-boundary match has no notion
  of negation -- a master-evidence sentence that explicitly RULES OUT a
  technique ("...expanding-window validation (not random cross-validation)")
  still literally contains the phrase "cross-validation", so it was
  (wrongly) treated as truthful support (`supported_by_master_evidence:
  true`), producing a `SUPPORTED_BUT_MISSING` ATS gap that a correction
  round could only "fix" by fabricating a claim the candidate's own master
  evidence explicitly contradicts.
  `filter_negated_evidence_hits` removes an evidence hit for a concept ONLY
  when the matched phrase is immediately preceded (same clause -- never
  crossing a `.`/`!`/`?` sentence boundary, and only within a short,
  fixed-size word window) by an explicit, literal negation marker ("not",
  "no", "never", "without", or a "...n't" contraction). This is NOT fuzzy
  matching -- the phrase match itself is the exact same word-boundary
  `phrase_present` test as every other alias/bridge; only whether a hit
  counts as truthful SUPPORT is refined. A concept whose only hit(s) are
  negated is reclassified `UNSUPPORTED` (moves out of `supported_but_
  missing_required` into `unsupported_required_terms`, exactly like any
  other genuinely-unsupported required concept) -- never fabricated into
  the resume, and never requested from a correction round. Excluded hits
  are still reported, per-concept, as `negated_evidence_hits_excluded` so
  this is disclosed, never a silent drop.

Fix 7 (V8.6.9 -- exact vs semantic match disclosure, additive only):
  The frozen `resume_present` test answers "does ANY checked form occur in
  the resume", collapsing a literal match on the JD's own wording and a
  match that only succeeded via an alias/concept bridge into one
  indistinguishable boolean. That is the right GATE semantics and is not
  changed here, but it makes a report unreviewable: a reader cannot tell
  which coverage is literal and which rests on a bridge they might
  disagree with. `partition_forms` labels the frozen `concept_forms`
  expansion as (literal, bridged) -- it partitions the identical form
  list and never adds, drops or re-matches a form -- and `evaluate` now
  also emits `match_kind` ("exact"/"semantic"/"absent"), `matched_form`
  (the specific string that matched) and `bridged_forms_checked`. These
  are PURELY ADDITIVE: `resume_present` is computed as
  `literal_matched or bridged_matched`, which is precisely the previous
  `any(phrase_present(...))`, so `status`, `gate_pass`, every coverage
  percentage and every pre-existing key keep their exact prior meaning.
  The explainable, non-gating consumer of this is
  tools/resume/v86/jd_match_report.py, which imports these helpers rather
  than reimplementing them.
"""

from __future__ import annotations

import argparse
import json
import re
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ats"))
import preflight as frozen  # noqa: E402  (tools/ats/preflight.py, unmodified)

# Fix 6: literal, reviewed negation markers only -- never a broad "sentiment"
# heuristic. Checked against individual, punctuation-stripped words in a
# short window immediately before the matched phrase, within the same
# clause (never crossing a sentence boundary).
_NEGATION_TOKENS = {"not", "no", "never", "without"}
_NEGATION_WINDOW_WORDS = 4
_SENTENCE_BOUNDARY_RE = re.compile(r"[.!?]")


def _negation_window_has_marker(text_n: str, match_start: int) -> bool:
    preceding = text_n[:match_start]
    boundaries = [m.end() for m in _SENTENCE_BOUNDARY_RE.finditer(preceding)]
    clause = preceding[boundaries[-1] if boundaries else 0:]
    words = clause.split()[-_NEGATION_WINDOW_WORDS:]
    for word in words:
        cleaned = word.strip(string.punctuation)
        if cleaned in _NEGATION_TOKENS or cleaned.endswith("n't"):
            return True
    return False


def filter_negated_evidence_hits(hits: list, forms: list) -> tuple[list, list]:
    """Split `hits` (as returned by the frozen `find_evidence`) into
    (kept, excluded_as_negated). A hit is excluded only when at least one of
    `forms` matches inside it with an explicit negation marker immediately
    preceding the match in the same clause -- see module docstring Fix 6."""
    kept, negated = [], []
    for hit in hits:
        text_n = frozen.normalize(hit.get("text", ""))
        hit_is_negated = False
        for form in forms:
            form_n = frozen.normalize(form)
            if not form_n:
                continue
            pattern = r"(?<!\w)" + re.escape(form_n) + r"(?!\w)"
            for m in re.finditer(pattern, text_n):
                if _negation_window_has_marker(text_n, m.start()):
                    hit_is_negated = True
                    break
            if hit_is_negated:
                break
        (negated if hit_is_negated else kept).append(hit)
    return kept, negated


def literal_forms(concept: dict) -> list:
    """The job description's OWN wording for a concept.

    Only `term`/`exact`/`canonical` -- the fields the JD analysis itself
    produced. A resume match on one of these is literal and needs no
    reviewer judgement, unlike a match that travelled through an alias or
    a concept bridge.
    """
    forms, seen = [], set()
    for field in ("term", "exact", "canonical"):
        value = concept.get(field)
        if isinstance(value, str) and value.strip():
            normalized = frozen.normalize(value)
            if normalized and normalized not in seen:
                seen.add(normalized)
                forms.append(value.strip())
    return forms


def partition_forms(concept: dict, aliases: dict) -> tuple:
    """Label the frozen `concept_forms` expansion as (literal, bridged).

    `frozen.concept_forms` remains the single authority on WHICH forms are
    checked; this only says where each one came from, so a report can
    distinguish an exact match from one that relied on a reviewed bridge.
    Every form the gate checks appears in exactly one of the two lists --
    this partitions, it never adds or drops a form, so it cannot change
    what matches.
    """
    every_form = frozen.concept_forms(concept, aliases)
    literal = literal_forms(concept)
    literal_normalized = {frozen.normalize(form) for form in literal}
    bridged = [
        form for form in every_form
        if frozen.normalize(form) not in literal_normalized
    ]
    return literal, bridged


def first_present_form(text: str, forms: list):
    """The first of `forms` present in `text`, or None.

    Uses the frozen word-boundary `phrase_present`, i.e. the identical
    test the gate itself uses -- naming the form that matched is what
    makes a semantic match auditable.
    """
    for form in forms:
        if frozen.phrase_present(text, form):
            return form
    return None


def load_and_merge_aliases(alias_path, extra_alias_path) -> dict:
    aliases = frozen.load_aliases(Path(alias_path) if alias_path else None)
    if extra_alias_path:
        extra_path = Path(extra_alias_path)
        if extra_path.exists():
            extra = frozen.load_aliases(extra_path)
            for key, values in extra.items():
                existing = aliases.setdefault(key, [])
                seen = {v.casefold() for v in existing}
                for value in values:
                    if value.casefold() not in seen:
                        existing.append(value)
                        seen.add(value.casefold())
    return aliases


def evaluate(requirements: dict, resume_text: str, evidence_root: Path, aliases: dict) -> dict:
    concepts = requirements.get("concepts")
    if not isinstance(concepts, list):
        raise SystemExit("ERROR: jd_requirements.json must contain a concepts list.")

    evidence_records = frozen.load_evidence(evidence_root)

    results = []
    supported_present, supported_missing_required, supported_missing_optional, unsupported = [], [], [], []

    for concept in concepts:
        term = concept.get("term") or concept.get("exact") or concept.get("canonical") or ""
        required = bool(concept.get("required"))
        forms = frozen.concept_forms(concept, aliases)
        raw_evidence_hits = frozen.find_evidence(evidence_records, forms)
        evidence_hits, negated_hits = filter_negated_evidence_hits(raw_evidence_hits, forms)
        supported = bool(evidence_hits)

        # `resume_present` is unchanged: still "does ANY checked form occur".
        # The partition below only records WHICH form satisfied it, so the
        # report can distinguish a literal match from one that relied on a
        # reviewed alias/bridge. Gate behaviour is untouched by design --
        # `literal_matched or bridged_matched` is exactly the old `any(...)`.
        literal, bridged = partition_forms(concept, aliases)
        literal_matched = first_present_form(resume_text, literal)
        bridged_matched = (
            None if literal_matched else first_present_form(resume_text, bridged)
        )
        resume_present = bool(literal_matched or bridged_matched)

        if literal_matched:
            match_kind = "exact"
        elif bridged_matched:
            match_kind = "semantic"
        else:
            match_kind = "absent"

        if supported and resume_present:
            status = "SUPPORTED_AND_PRESENT"
            supported_present.append(term)
        elif supported and not resume_present:
            status = "SUPPORTED_BUT_MISSING"
            (supported_missing_required if required else supported_missing_optional).append(term)
        else:
            status = "UNSUPPORTED"
            unsupported.append(term)

        results.append({
            "term": term,
            "canonical": concept.get("canonical"),
            "category": concept.get("category"),
            "required": required,
            "priority": concept.get("priority"),
            "importance": frozen.priority_value(concept),
            "forms_checked": forms,
            "supported_by_master_evidence": supported,
            "resume_present": resume_present,
            # Additive reporting only (Fix 7). `status`/`gate_pass` and every
            # pre-existing key keep their exact previous meaning; these three
            # just expose HOW `resume_present` was satisfied.
            "match_kind": match_kind,
            "matched_form": literal_matched or bridged_matched,
            "bridged_forms_checked": bridged,
            "status": status,
            "evidence_hits": evidence_hits,
            "negated_evidence_hits_excluded": negated_hits,
        })

    supported_required = [r for r in results if r["required"] and r["supported_by_master_evidence"]]
    supported_required_present = [r for r in supported_required if r["resume_present"]]

    supported_high_all = [r for r in results if r["supported_by_master_evidence"] and r["importance"] >= 4]
    supported_high_all_present = [r for r in supported_high_all if r["resume_present"]]

    # Fix 3: the concepts that can actually GATE are required + high-importance.
    supported_high_required = [r for r in supported_high_all if r["required"]]
    supported_high_required_present = [r for r in supported_high_required if r["resume_present"]]

    supported_responsibilities = [
        r for r in results
        if r["supported_by_master_evidence"] and str(r["category"]).upper() == "CORE_RESPONSIBILITY"
    ]
    supported_responsibilities_present = [r for r in supported_responsibilities if r["resume_present"]]

    missing_required = [r["term"] for r in supported_required if not r["resume_present"]]
    unsupported_required_terms = [r["term"] for r in results if r["required"] and not r["supported_by_master_evidence"]]

    required_coverage = frozen.percentage(len(supported_required_present), len(supported_required))
    high_coverage_all = frozen.percentage(len(supported_high_all_present), len(supported_high_all))
    required_high_coverage = frozen.percentage(len(supported_high_required_present), len(supported_high_required))
    responsibility_coverage = frozen.percentage(len(supported_responsibilities_present), len(supported_responsibilities))

    gate_pass = (not missing_required) and required_high_coverage >= 90.0

    return {
        "job_key": requirements.get("job_key"),
        "gate_pass": gate_pass,
        "supported_required_skill_coverage_percent": required_coverage,
        "high_priority_concept_coverage_percent": high_coverage_all,
        "required_high_priority_concept_coverage_percent": required_high_coverage,
        "core_responsibility_coverage_percent": responsibility_coverage,
        "supported_and_present": supported_present,
        "supported_but_missing": supported_missing_required + supported_missing_optional,
        "supported_but_missing_required": supported_missing_required,
        "optional_supported_but_missing": supported_missing_optional,
        "unsupported": unsupported,
        "missing_supported_required_terms": missing_required,
        "unsupported_required_terms": unsupported_required_terms,
        "concepts": results,
    }


def main():
    parser = argparse.ArgumentParser(description="V8.6.1 deterministic ATS evidence/coverage validator.")
    parser.add_argument("--requirements", required=True)
    parser.add_argument("--resume", required=True)
    parser.add_argument("--evidence-root", default="master")
    parser.add_argument("--aliases", default=None)
    parser.add_argument("--extra-aliases", default=None)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    requirements_path = Path(args.requirements)
    resume_path = Path(args.resume)
    evidence_root = Path(args.evidence_root)
    output_path = Path(args.output)

    if not requirements_path.exists():
        raise SystemExit(f"ERROR: Missing requirements file: {requirements_path}")
    if not resume_path.exists():
        raise SystemExit(f"ERROR: Missing resume draft: {resume_path}")
    if not evidence_root.exists():
        raise SystemExit(f"ERROR: Missing evidence directory: {evidence_root}")

    requirements = json.loads(requirements_path.read_text(encoding="utf-8"))
    resume_text = resume_path.read_text(encoding="utf-8", errors="replace")
    aliases = load_and_merge_aliases(args.aliases, args.extra_aliases)

    report = evaluate(requirements, resume_text, evidence_root, aliases)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print("ATS PREFLIGHT (V8.6.1)")
    print("=" * 60)
    print("Required high-priority coverage:", f"{report['required_high_priority_concept_coverage_percent']:.1f}%")
    print("All-concept high-priority coverage (informational):", f"{report['high_priority_concept_coverage_percent']:.1f}%")
    print()
    if report["optional_supported_but_missing"]:
        print("OPTIONAL SUPPORTED BUT MISSING (non-blocking):")
        for item in report["optional_supported_but_missing"]:
            print(f"  - {item}")
    print()
    print("GATE:", "PASS" if report["gate_pass"] else "FAIL")
    print(f"Report: {output_path}")

    if not report["gate_pass"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
