#!/usr/bin/env python3
"""
Evidence gate for proposed resume improvements.

A requirement-matching report (see `jd_match_report.py`) can show that a
resume fails to surface experience the candidate genuinely has. Acting on
that is legitimate. Acting on it carelessly is how a resume acquires
achievements its owner never earned -- which is the single failure mode
this whole pipeline exists to prevent.

Every proposed rewrite therefore passes through here before a human is
ever shown it as actionable. The guard's bias is explicit and
deliberate: it rejects by default and only accepts what the candidate's
own verified evidence can carry.

The motivating real-world case
------------------------------

An external ATS checker, reviewing a resume it knew nothing about,
recommended adding wording to the effect of "increased output by 30%".
No such measurement exists anywhere in the candidate's evidence -- the
checker produced a plausible-sounding number because resumes usually
have numbers there. A tool that passed that recommendation through to a
human as "recommended" would be laundering a fabrication through an
authoritative-looking report.

Rule 1 below rejects it, and `tests/test_v86_10_jd_match_and_
recommendations.py` pins that behaviour so it cannot silently regress.
The rejection does not depend on recognising the number 30, the word
"output", or anything else about that specific case: it falls out of the
general rule that a measurement in proposed wording must already exist
in the evidence being cited.

The rules
---------

Checked in order; the first failure decides the verdict.

  1. EVIDENCE RESOLUTION (`no_resolvable_evidence`)
     A recommendation must cite at least one evidence record that
     actually exists in the corpus. An uncited recommendation is a
     suggestion about a stranger. A free-text citation also has to be
     specific enough to identify a claim -- without a floor, citing
     `"the"` would resolve against nearly every record. Checked first,
     because rule 2 searches the records this rule resolves.

  2. MEASUREMENT GROUNDING (`unsupported_metric`)
     Every measurement in the proposed wording -- percentages,
     latencies, counts, durations, multipliers -- must already appear in
     the cited evidence. Extraction and comparison build on
     `metrics_audit`, so a number the metrics audit would accept is
     available to a recommendation. Genuine dates and version
     identifiers are not measurements ("Jan 2025", "YOLOv7" never trip
     this), but see `proposal_measurements`: a four-digit COUNT such as
     "2000 concurrent users" is recovered here, because the shared
     extractor's year filter would otherwise make a whole numeric range
     unauditable in proposals.

  3. REQUIREMENT GROUNDING
     Checked most-decisive first:

       `requirement_contradicted`  the candidate's own evidence
           explicitly rules the requirement out. This settles the
           question, so it rejects outright.
       `ambiguous_requirement`     the signals conflict -- typically the
           resume asserts something no evidence record corroborates.
           Nobody knows yet, so the honest verdict is
           `needs_human_review`, NOT a confident "unsupported". Reached
           BEFORE the unsupported rejection so that "we cannot tell" is
           never reported as "we know it is false".
       `requirement_unsupported`   plain, unambiguous absence: no
           evidence, no conflicting signal. This is the precise shape of
           "experience that exists only in the job description", and it
           rejects.

  4. CLAIM-TERM GROUNDING (`ungrounded_claim_terms`)
     Every distinctive word in the proposed wording must appear
     somewhere in the evidence corpus. This is the catch-all for a
     fabricated technology, certification or responsibility that carries
     no number and so clears rule 2 untouched. It is deliberately the
     LAST rejection rule, because it is the least specific diagnosis
     available and the requirement rules say more.

  5. UNKNOWN REQUIREMENT (`unknown_requirement` / `no_requirement_context`)
     If the proposal's `requirement_term` is not in the match report --
     or no match report was supplied at all -- rule 3 could not run, and
     that is reported as `needs_human_review` rather than passed over.
     Letting an unknown requirement fall through to `accepted` silently
     disabled the rule that refuses job-description-only experience.

Only a recommendation that clears all five is `accepted`, and even then
it is a *proposal for a human*, never an instruction the pipeline
executes. Nothing in this module edits a resume, and nothing downstream
treats `accepted` as authorisation to write.

What a verdict is not
---------------------

`accepted` means every measurement and every distinctive term in the
wording is traceable to the evidence corpus, and the requirement it
addresses is evidence-supported.

It is explicitly NOT a semantic entailment check. The guard does not
verify that the proposed sentence as a whole asserts what the cited
record asserts, and two known gaps follow from that: a number verified
for one metric can be re-attached to a different metric in the same
record, and a sentence built entirely from in-corpus words can still
combine them into a claim no record makes. Both are reported as
`accepted`, and both are why acceptance is a proposal for a human rather
than an authorisation.

It also does not mean the wording is good, that it will improve any
external score, or that the candidate is proficient at the thing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "ats"))

import preflight as frozen  # noqa: E402  (tools/ats/preflight.py, unmodified)

import metrics_audit  # noqa: E402

SCHEMA_VERSION = "1.0"
REPORT_FILENAME = "recommendation_review.json"

VERDICTS = ("accepted", "rejected", "needs_human_review")

REJECTION_REASONS = (
    "unsupported_metric",
    "no_resolvable_evidence",
    "requirement_unsupported",
    "requirement_contradicted",
)


# Ordinary resume/English connective and framing vocabulary. A token in
# this set carries no claim on its own, so it is never something the
# evidence has to corroborate. Everything NOT in here is treated as a
# potential claim term -- the bias is deliberately toward asking.
_STOPWORDS = frozenset("""
a an and or the of in on for to with without using use used uses via by at as
from into over under about across is are was were be been being has have had
do does did will would can could should may might must not no nor but if then
than that this these those it its their our your they them we you i he she who
whom which what when where why how all any both each few more most other some
such only own same so too very s t just
built build building designed design developed develop created create added add
implemented implement improved improve increased increase reduced reduce cut
delivered deliver led lead ran run shipped ship wrote write worked work made make
set up supported support enabled enable provided provide helped help achieved
achieve applied apply integrated integrate automated automate optimized optimize
scaled scale served serve maintained maintain owned own drove drive
new existing current prior per across within while during including include
team teams project projects system systems service services pipeline pipelines
tool tools platform platforms product production feature features users user
time times day days week weeks month months year years end entire full
since until before after ongoing present current date dates
jan january feb february mar march apr april may jun june jul july aug august
sep sept september oct october nov november dec december
""".split())


def _term_grounded(term: str, corpus_text: str, aliases: dict) -> bool:
    """Is this claim term traceable to the candidate's evidence?

    Directly, if the corpus uses the word. Otherwise through a REVIEWED
    alias/bridge: `config/v86/ats_concept_bridges.json` and
    `config/ats_aliases.json` are where this project records that two
    strings name the same thing, each entry human-reviewed. Consulting
    them here keeps the guard consistent with the matcher, which already
    treats those forms as interchangeable.

    Without this, making a true relationship explicit would be refused:
    proposing "MongoDB (NoSQL)" for a candidate whose evidence says
    MongoDB would fail purely because the literal string "NoSQL" is
    absent. Rewording through a reviewed equivalence is not a new claim.

    It stays strict for genuinely novel terminology: a term with no
    bridge, or whose bridge targets are themselves absent from the
    corpus, is still ungrounded. Nothing is inferred -- the equivalence
    must already be written down and reviewed.
    """
    if frozen.phrase_present(corpus_text, term):
        return True
    for form in aliases.get(frozen.normalize(term), []) or []:
        if frozen.phrase_present(corpus_text, form):
            return True
    return False


def _content_tokens(text: str) -> list:
    """Distinctive words of a phrase: normalized, stopwords and bare
    numbers removed."""
    tokens = []
    for token in frozen.normalize(text).replace("/", " ").split():
        token = token.strip(".,;:()[]{}'\"!?")
        if not token or len(token) < 3:
            continue
        if token in _STOPWORDS:
            continue
        if any(character.isdigit() for character in token):
            continue
        tokens.append(token)
    return tokens


# A four-digit number in 1900-2099 is stripped by `metrics_audit` before
# measurement extraction, because in a resume bullet it is nearly always
# a date. That is right for auditing the candidate's OWN bullets, but in
# a PROPOSAL it opens a hole: "scaled to 2000 concurrent users" carries
# no extractable measurement at all, so Rule 1 never examines it. This
# recovers those numbers for proposals only -- never for evidence -- and
# only when they are not sitting in date-shaped context.
_YEARLIKE = re.compile(r"(?<![\w.])((?:19|20)\d{2})(?![\w.])")
_MONTH_WORD = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\b",
    re.IGNORECASE,
)


def _date_shaped(text: str, start: int, end: int) -> bool:
    """Is this four-digit number being used as a calendar year?

    The discriminator that matters is what FOLLOWS it. A year used as a
    count is followed by the thing being counted -- "2000 concurrent
    users", "1995 dollars", "2048 documents" -- whereas a year used as a
    date is followed by punctuation, the end of the sentence, or
    ordinary connective words. Leading prepositions are not a reliable
    signal on their own ("by 1995 dollars" and "by 1995" differ only in
    what comes after), so they are not used as one.

    Ties break toward "this is a measurement", because a false
    measurement costs a false rejection that a human then reviews, while
    a missed one lets an invented figure through.
    """
    before = text[max(0, start - 24):start]
    after = text[end:end + 24]

    if _MONTH_WORD.search(before) or _MONTH_WORD.search(after):
        return True
    # an explicit year-to-year range: "2019 - 2023", "2019 to 2023"
    if re.match(r"\s*(?:--+|–|—|-|\bto\b)\s*(?:19|20)\d{2}", after, re.IGNORECASE):
        return True
    if re.search(r"(?:19|20)\d{2}\s*(?:--+|–|—|-|\bto\b)\s*$", before, re.IGNORECASE):
        return True
    # an explicit temporal preposition immediately before
    if re.search(r"\b(?:since|until|from|during|through)\s*$", before, re.IGNORECASE):
        return True

    # Followed by a content word -> it is counting something.
    next_word = re.match(r"\s*([A-Za-z][\w-]*)", after)
    if next_word and next_word.group(1).casefold() not in _STOPWORDS:
        return False

    return True


def proposal_measurements(text: str) -> list:
    """Measurements a PROPOSAL must ground.

    `metrics_audit.extract_measurements` plus the year-range numbers it
    deliberately filters out, when those are not date-shaped. Strictly a
    superset of the shared extractor, so anything the metrics audit would
    demand grounding for is still demanded here.
    """
    found = list(metrics_audit.extract_measurements(text))
    for match in _YEARLIKE.finditer(text):
        if _date_shaped(text, match.start(), match.end()):
            continue
        token = match.group(1)
        if token not in found:
            found.append(token)
    return found


# ---------------------------------------------------------------------------
# evidence corpus
# ---------------------------------------------------------------------------

def load_evidence_corpus(evidence_root) -> list:
    """Every evidence record, via the frozen loader.

    Reusing `frozen.load_evidence` means a recommendation is checked
    against exactly the corpus the ATS gate and the match report read --
    not a second, possibly divergent view of what the candidate has done.
    """
    return frozen.load_evidence(Path(evidence_root))


# A free-text citation must be specific enough to identify a claim. One
# common word ("the") occurs in almost every record, so without a floor
# Rule 2 is satisfiable by any string at all. An exact source/file id is
# exempt because it is already unambiguous.
MIN_CITATION_CONTENT_TOKENS = 4


def resolve_citations(citations: list, corpus: list) -> dict:
    """Match a recommendation's cited evidence against the corpus.

    A citation resolves if it names an evidence record's source path or
    file exactly, or if a sufficiently specific phrase occurs verbatim in
    a record. The phrase form exists because the most natural thing to
    cite is the sentence itself -- but it carries a specificity floor, or
    a citation of `"the"` would resolve against nearly every record and
    reduce Rule 2 to a formality.
    """
    resolved, unresolved = [], []
    for citation in citations:
        if not isinstance(citation, str) or not citation.strip():
            continue
        needle = citation.strip()

        hit = None
        for record in corpus:
            if needle == record.get("source") or needle == record.get("file"):
                hit = record
                break

        if hit is None:
            tokens = _content_tokens(needle)
            if len(tokens) < MIN_CITATION_CONTENT_TOKENS:
                unresolved.append({
                    "citation": needle,
                    "reason": (
                        f"too unspecific to identify a claim: {len(tokens)} content "
                        f"word(s), minimum {MIN_CITATION_CONTENT_TOKENS}"
                    ),
                })
                continue
            for record in corpus:
                if frozen.phrase_present(record.get("text", ""), needle):
                    hit = record
                    break

        if hit is None:
            unresolved.append({
                "citation": needle,
                "reason": "no evidence record contains this text",
            })
            continue

        resolved.append({
            "citation": needle,
            "file": hit.get("file"),
            "source": hit.get("source"),
            # The FULL record text. This is the haystack Rule 1 grounds
            # measurements against, so truncating it here would turn a
            # figure late in a long record into a false rejection.
            "text": hit.get("text", ""),
            "excerpt": hit.get("text", "")[:350],
        })

    return {"resolved": resolved, "unresolved": unresolved}


# ---------------------------------------------------------------------------
# the guard
# ---------------------------------------------------------------------------

def review_recommendation(
    recommendation: dict,
    corpus: list,
    requirement_index: dict,
    *,
    requirement_context_available: bool = True,
    aliases: dict = None,
) -> dict:
    """Apply the rules to one proposed recommendation. Pure; no IO."""
    aliases = aliases or {}
    rec_id = recommendation.get("id")
    proposed_text = str(recommendation.get("proposed_text") or "")
    requirement_term = recommendation.get("requirement_term")
    citations = recommendation.get("evidence_refs") or []

    citation_result = resolve_citations(citations, corpus)
    resolved = citation_result["resolved"]

    # Text a measurement is allowed to come from: the cited records.
    # Deliberately NOT the whole corpus -- a number that appears
    # somewhere unrelated in the candidate's history does not license
    # attaching it to this claim.
    cited_texts = [hit.get("text", "") for hit in resolved]

    measurements = proposal_measurements(proposed_text)
    grounded = metrics_audit.measurement_present(measurements, cited_texts)
    ungrounded = [m for m in measurements if m not in grounded]

    # Claim terms: distinctive words the proposal asserts. A term that
    # appears NOWHERE in the candidate's evidence corpus is a new claim,
    # not a rewording of an existing one -- this is what catches a
    # fabricated technology or responsibility that carries no number and
    # so sails past the measurement rule.
    corpus_text = "\n".join(record.get("text", "") for record in corpus)
    claim_terms = _content_tokens(proposed_text)
    ungrounded_terms = [
        term for term in claim_terms
        if not _term_grounded(term, corpus_text, aliases)
    ]

    requirement = requirement_index.get(requirement_term) or {}
    requirement_support = requirement.get("evidence_support")
    requirement_ambiguous = requirement.get("match_class") == "ambiguous"

    verdict = "accepted"
    reason = None
    detail = None

    # --- rule 1: evidence resolution --------------------------------------
    # Checked FIRST because rule 2 grounds measurements against the
    # resolved citations: with none resolved, every measurement would be
    # reported ungrounded and the reason given would describe a symptom
    # rather than the actual problem.
    if not resolved:
        verdict = "rejected"
        reason = "no_resolvable_evidence"
        detail = (
            "no cited evidence record could be resolved in the candidate's "
            "evidence corpus"
        )

    # --- rule 2: measurement grounding ------------------------------------
    elif ungrounded:
        verdict = "rejected"
        reason = "unsupported_metric"
        detail = (
            "proposed wording introduces measurement(s) "
            + ", ".join(repr(m) for m in ungrounded)
            + " that do not appear in the cited evidence"
        )

    # --- rule 3: requirement grounding ------------------------------------
    # Ordered most-decisive first. Contradiction settles the question, so
    # it rejects outright. Ambiguity does NOT settle it -- "the resume
    # claims this but the evidence bank is silent" means nobody knows yet,
    # and the honest verdict is to ask a human rather than to assert
    # unsupported. Only plain, unambiguous absence rejects as unsupported.
    elif requirement_support == "negated_only":
        verdict = "rejected"
        reason = "requirement_contradicted"
        detail = (
            "the candidate's own master evidence explicitly rules out this "
            "requirement"
        )
    elif requirement_ambiguous:
        verdict = "needs_human_review"
        reason = "ambiguous_requirement"
        detail = requirement.get("ambiguity_reason") or (
            "the requirement's match signals conflict"
        )
    elif requirement_support == "unsupported":
        verdict = "rejected"
        reason = "requirement_unsupported"
        detail = (
            "no master evidence supports this requirement; it exists only in the "
            "job description"
        )

    # --- rule 4: claim-term grounding (catch-all) ------------------------
    # Deliberately LAST of the rejection rules. It catches fabricated
    # prose that carries no number, but it is the least specific
    # diagnosis available, so the requirement rules above get first
    # refusal: "this requirement exists only in the job description"
    # tells a reader far more than "these words are not in your
    # evidence".
    elif ungrounded_terms:
        verdict = "rejected"
        reason = "ungrounded_claim_terms"
        detail = (
            "proposed wording introduces term(s) "
            + ", ".join(repr(t) for t in ungrounded_terms[:8])
            + " that appear nowhere in the candidate's evidence corpus"
        )

    # --- rule 5: the requirement could not be looked up at all -----------
    # After the claim-term rule on purpose: if a proposal BOTH cites an
    # unknown requirement and invents terminology, the outright
    # rejection is the more useful answer than deferring to a human.
    # Previously this fell straight through to `accepted`, because an
    # unknown term made every `requirement_support` comparison above
    # compare against None. That silently skipped the entire requirement
    # rule for any proposal whose `requirement_term` did not match an
    # entry in the match report EXACTLY -- a paraphrase, a casing
    # difference or a missing --match-report was enough. Rule 3 is the
    # rule that refuses JD-only experience, so losing it quietly was the
    # most dangerous failure mode in this module. An unknown requirement
    # is now an explicit unknown.
    elif not requirement:
        verdict = "needs_human_review"
        reason = (
            "no_requirement_context" if not requirement_context_available
            else "unknown_requirement"
        )
        detail = (
            "no requirement-matching context was supplied, so this proposal's "
            "requirement could not be checked for evidence support"
            if not requirement_context_available else
            f"requirement {requirement_term!r} does not appear in the match "
            "report, so its evidence support is unknown"
        )

    return {
        "id": rec_id,
        "requirement_term": requirement_term,
        "requirement_known": bool(requirement),
        "requirement_evidence_support": requirement_support,
        "requirement_match_class": requirement.get("match_class"),
        "original_evidence": recommendation.get("original_evidence"),
        "proposed_text": proposed_text,
        "rationale": recommendation.get("rationale"),
        "verdict": verdict,
        "reason": reason,
        "detail": detail,
        "measurements_in_proposal": measurements,
        "measurements_grounded": grounded,
        "measurements_ungrounded": ungrounded,
        "claim_terms": claim_terms,
        "claim_terms_ungrounded": ungrounded_terms,
        "citations_resolved": resolved,
        "citations_unresolved": citation_result["unresolved"],
        "confidence": _confidence(verdict, requirement, measurements, grounded),
        "human_review_required": verdict != "accepted",
    }


def _confidence(verdict: str, requirement: dict, measurements: list, grounded: list) -> str:
    """A coarse, explainable label -- never a number.

    A numeric confidence here would imply a calibration this project has
    not measured and cannot justify, so the label is derived only from
    facts already established: the verdict, whether the requirement is
    literally matched, and whether every number is grounded.
    """
    if verdict == "rejected":
        return "rejected_no_confidence"
    if verdict == "needs_human_review":
        return "unresolved_pending_review"
    if measurements and len(grounded) == len(measurements):
        return "high_evidence_backed_with_grounded_metrics"
    if requirement.get("match_class") in ("exact", "semantic"):
        return "high_evidence_backed"
    return "moderate_evidence_backed_no_metrics"


def review_all(
    recommendations: list,
    corpus: list,
    match_report: dict = None,
    aliases: dict = None,
) -> dict:
    """Review a batch of recommendations against evidence + match report."""
    requirement_index = {}
    for row in (match_report or {}).get("requirements", []) or []:
        if row.get("term"):
            requirement_index[row["term"]] = row

    # Without a match report there is no requirement context at all, so
    # Rule 3 cannot run for ANY recommendation in the batch. That must be
    # visible in the output rather than silently turning the gate into
    # "has a citation and no new numbers".
    requirement_context_available = bool(requirement_index)

    reviewed = [
        review_recommendation(
            rec, corpus, requirement_index,
            requirement_context_available=requirement_context_available,
            aliases=aliases,
        )
        for rec in recommendations
    ]

    counts = {verdict: 0 for verdict in VERDICTS}
    for item in reviewed:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1

    rejection_counts = {}
    for item in reviewed:
        if item["verdict"] == "rejected" and item["reason"]:
            rejection_counts[item["reason"]] = rejection_counts.get(item["reason"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "job_key": (match_report or {}).get("job_key"),
        "recommendations_reviewed": len(reviewed),
        "requirement_context_available": requirement_context_available,
        "verdicts": counts,
        "rejections_by_reason": rejection_counts,
        "recommendations": reviewed,
        "advisory_only": True,
        "advisory_note": (
            "An 'accepted' verdict means every measurement and every distinctive "
            "term in the proposed wording is traceable to the candidate's evidence "
            "corpus, and the requirement it addresses is evidence-supported. It is "
            "NOT a semantic entailment check: the guard does not verify that the "
            "sentence as a whole means what the cited record means. It is a "
            "proposal for a human to approve, never an instruction this pipeline "
            "executes, and never a claim of proficiency."
            + ("" if requirement_context_available else
               " NO REQUIREMENT CONTEXT WAS SUPPLIED for this run, so the "
               "requirement-grounding rule could not run and nothing was accepted "
               "on its basis.")
        ),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Evidence gate for proposed resume improvements."
    )
    parser.add_argument("--recommendations", required=True)
    parser.add_argument("--evidence-root", default="master")
    parser.add_argument("--aliases", default=None)
    parser.add_argument("--extra-aliases", default=None)
    # Required: without it the requirement-grounding rule cannot run for
    # any recommendation in the batch. Pass the empty string to run the
    # measurement/citation rules alone, which downgrades every otherwise-
    # acceptable proposal to needs_human_review rather than accepting it.
    parser.add_argument("--match-report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    recommendations_path = Path(args.recommendations)
    evidence_root = Path(args.evidence_root)

    for path, label in (
        (recommendations_path, "recommendations file"),
        (evidence_root, "evidence directory"),
    ):
        if not path.exists():
            raise SystemExit(f"ERROR: Missing {label}: {path}")

    payload = json.loads(recommendations_path.read_text(encoding="utf-8"))
    recommendations = (
        payload if isinstance(payload, list) else payload.get("recommendations", [])
    )

    match_report = None
    if args.match_report and Path(args.match_report).exists():
        match_report = json.loads(
            Path(args.match_report).read_text(encoding="utf-8")
        )

    corpus = load_evidence_corpus(evidence_root)
    import ats_preflight_v86
    aliases = ats_preflight_v86.load_and_merge_aliases(
        args.aliases, args.extra_aliases
    )
    report = review_all(recommendations, corpus, match_report, aliases)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print()
    print("RECOMMENDATION REVIEW (advisory -- gates nothing)")
    print("=" * 60)
    for verdict in VERDICTS:
        print(f"  {verdict:20} {report['verdicts'].get(verdict, 0)}")
    if not report["requirement_context_available"]:
        print("  WARNING: no requirement context -- requirement grounding did not run")
    for reason, count in sorted(report["rejections_by_reason"].items()):
        print(f"    rejected/{reason}: {count}")
    print(f"  Report: {output_path}")


if __name__ == "__main__":
    main()
