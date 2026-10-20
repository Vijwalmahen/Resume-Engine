#!/usr/bin/env python3
"""
V8.6.8 -- deterministic cover-letter `company` backfill.

The general engineering problem: semantic call 2
(`semantic_worker.run_content_call`) is handed the entire job posting text
(`job_packet.json` -> `job_posting_text`), which almost always already
states the company name verbatim in a structured header line
(`**Company:** X` for synthetic postings, or `Company: X` under
`## Metadata` for real `linkedin-job-scraper` postings). Even so, the model
can leave `cover_letter_payload.company` empty, fill it with a generic
filler phrase ("your organization" / "the hiring team" / ...), or write a
plausible-sounding but wrong value (a team name, a paraphrase, a role
description) even when the posting's own header disagrees. A required,
deterministically-knowable literal fact should never depend on the model
getting it right, and an empty/incorrect value can hard-fail the whole job
downstream in `validate_strategy_contract_v86.py` (a `STRATEGY_CONTRACT_FAILED`
is terminal -- there is no bounded correction round for it; see
`run_batch.py`, `deterministic_pipeline.run_job_pipeline`).

This is exactly the class of gap Section "Brittle boundaries" calls out:
a deterministically-known literal (not a semantic judgment call) that has no
deterministic recovery path. The fix is NOT another model call, a looser
contract, or fuzzy matching -- it is a narrow, explicit parse of the same
structured header line the job posting already contains.

Scope, deliberately narrow:
  - Only ever reads `job_posting_text` (the same text call 2 was already
    given -- no new fact, no external source).
  - Recognizes a small number of literal header forms already used verbatim
    by real job postings (see `jobs/inbox/*.md` for the
    `linkedin-job-scraper`-produced `Company: X` form, `jobs/batch_inbox/
    *.md` for the synthetic `**Company:** X` form, and `scale10-*`/`test16-
    *`/`live13-*`-style YAML frontmatter `company: "X"`). If none of these
    forms are found, nothing is filled and the contract validator still
    correctly fails the job -- this module never guesses or fabricates a
    company name.

Called from two places, exactly mirroring `normalize_jd_concepts.py`'s
established dual-call-site pattern:
  1. `run_batch.py`, immediately after semantic call 2 returns, BEFORE
     `detp.write_strategy_files` ever writes `cover_letter_payload.json` --
     the primary, always-exercised call site for a normal batch run.
  2. Defensively, at the top of `deterministic_pipeline.run_job_pipeline`,
     for any caller that writes `cover_letter_payload.json` directly
     (offline tests, a future correction path) without going through
     `run_batch.py`'s call site. Idempotent: if `company` already exactly
     equals the posting's own parsed literal company (the common case,
     since call site 1 already ran), this is a pure no-op.

--------------------------------------------------------------------------
Canonical company source-of-truth policy
--------------------------------------------------------------------------
Some generated cover letters may contain a non-empty but incorrect company
value even when the posting includes an authoritative structured company
header. The deterministic normalization layer therefore treats an
unambiguous structured header as the source of truth: whenever a literal
company name can be reliably parsed from the job posting via
`extract_company_from_posting`, that parsed value is the SOLE authority for
`cover_letter_payload.company`, unconditionally -- not merely a fallback
for empty/placeholder values. A non-empty, non-placeholder value the model
wrote is not exempt: if it differs at all from the posting's own parsed
literal, it is replaced. Only when NO literal company name can be reliably
parsed (see the ambiguity guard below) does this module fall back to
preserving whatever the model wrote -- it still never fabricates.

`_GENERIC_PLACEHOLDER_COMPANY_NAMES`/`is_recognized_placeholder_or_empty`
are kept as a read-only diagnostic convenience (some callers still want to
ask "was this specific value a known placeholder phrase?") -- they do not
gate the backfill decision itself.

**Ambiguity guard:** `extract_company_from_posting` scans for EVERY line
matching the header pattern, not just the first. A posting can carry two
literal `Company:` header lines with two genuinely different values (for
example, a parent/subsidiary pair such as "Example Financial Group" vs.
"Example Financial Group Holdings"). Silently picking whichever line the
regex happened to match first would itself be a guess -- so when 2+ header
lines resolve to genuinely different values, this module treats the
posting as having NO reliably-parseable literal company (returns `None`,
identical to the no-header case) rather than arbitrarily choosing one. When
every matching line agrees (the overwhelming common case), the single
agreed value is returned exactly as before.

`benchmark_report.py`'s `company_check` (see that module) is the
production-parity verification counterpart: it reports `PASS` only when the
FINAL on-disk `cover_letter_payload.company` exactly equals the posting's
own parsed literal company, `FAIL` on any other non-empty mismatch, and an
explicit `NO_LITERAL_SOURCE` (never a fabricated PASS/FAIL) when no literal
company is reliably parseable -- reusing this same module's
`extract_company_from_posting`, one source of truth.

--------------------------------------------------------------------------
A second, related defect this module also closes
--------------------------------------------------------------------------
`cover_letter_payload.company` can be non-empty and still not be the real
employer name: call 2 (`semantic_worker.run_content_call`) is never given
`job_posting_text` or any extracted company field at all (see
`packet_for_requirements`/`run_content_call` in `semantic_worker.py`), so it
has no way to know the real name, and can paper over the gap with a
plausible-sounding generic filler phrase instead of an empty string --
which is non-empty and therefore silently passes
`validate_strategy_contract_v86.py`'s truthiness check. That is a real
quality defect (a non-personalized cover letter shipped as if it were
tailored). `_GENERIC_PLACEHOLDER_COMPANY_NAMES` below is the small,
explicit, literal set of filler phrases this module recognizes and treats
as needing a real value, never a fuzzy/partial match.
"""

from __future__ import annotations

import re
from typing import Optional

# Matches a line whose entire content is a "Company:" header, in either of
# the two literal forms already used verbatim by real job postings in this
# repo:
#   **Company:** Searchlight Technologies      (synthetic benchmark postings)
#   Company: Example Health Co                 (linkedin-job-scraper postings)
# Anchored to a full line (MULTILINE + start/end-of-line) so this can never
# match an unrelated mid-sentence occurrence of the word "company".
_COMPANY_HEADER_RE = re.compile(r"(?im)^\*{0,2}Company:\*{0,2}[ \t]*(.+?)[ \t]*$")

# Exact, literal filler phrases Call 2 output can use to stand in for a
# real company name -- never fabricated evidence,
# never a guess: these are generic enough that no real employer name could
# collide with one, so replacing them with a deterministically-parsed real
# name can only improve truthfulness/personalization, never remove a
# genuine fact. Casefold-exact match only -- no substring/fuzzy matching.
_GENERIC_PLACEHOLDER_COMPANY_NAMES = {
    "your organization", "your organisation",
    "your company", "the company", "this company",
    "your team", "the organization", "the organisation",
    "your firm", "the firm",
    "the hiring organization", "the hiring organisation",
    "hiring team", "the hiring team",
    # "Hiring Company" plus its generic equivalents -- "hiring
    # organization"/"hiring organisation" (bare, without a leading "the")
    # are also covered even though "the hiring organization" already was.
    # Still an exact, literal, casefold-only set -- never a substring/fuzzy
    # match, so a genuine employer name such as "A Company Called Hiring
    # Company" is untouched (see test_v86_8_round2_hardening.py).
    "hiring company", "the hiring company",
    "hiring organization", "hiring organisation",
    "employer", "the employer",
}


def _needs_backfill(existing: str) -> bool:
    return not existing or existing.casefold() in _GENERIC_PLACEHOLDER_COMPANY_NAMES


def is_recognized_placeholder_or_empty(company: object) -> bool:
    """Public, read-only counterpart of `_needs_backfill` for diagnostic
    tooling (`benchmark_report.py`) that needs to ask "is this FINAL company
    value still empty or a known generic placeholder?" without invoking the
    backfill/rewrite path itself. Reuses the exact same literal
    `_GENERIC_PLACEHOLDER_COMPANY_NAMES` set -- one source of truth; no
    second hardcoded placeholder list anywhere in this pipeline."""
    return _needs_backfill(str(company or "").strip())


def extract_company_from_posting(posting_text: str) -> Optional[str]:
    """Deterministically read a literal `Company:` header line out of the
    raw job posting text, or None if no such header line exists (or if the
    posting contains 2+ such header lines that disagree -- see below). Never
    fuzzy, never inferred from prose -- an explicit, anchored line match
    only.

    The same anchored regex (case-insensitive) also happens to match a
    THIRD literal posting format: synthetic `scale10-*`/`test16-*`/
    `live13-*` postings' YAML frontmatter (`company: "Example Corp"`,
    lowercase key, quoted value). Surrounding `"`/`'` quote characters are
    stripped the same way surrounding `*` markdown-bold markers already
    are, so the returned string is always the bare company name, never
    `"Example Corp"` with literal quote marks.

    Ambiguity guard: every matching header line in the posting is checked,
    not just the first. A posting can carry two literal `Company:` lines
    with two DIFFERENT values (e.g. a parent/subsidiary pair) -- silently
    returning whichever the regex matches first would itself be a guess.
    When 2+ header lines resolve to genuinely different values, this
    returns `None` (no reliably-parseable literal company), never an
    arbitrary pick. When every matching line agrees (the overwhelming
    common case), that single agreed value is returned exactly as before."""
    if not posting_text:
        return None
    values = []
    for match in _COMPANY_HEADER_RE.finditer(posting_text):
        company = match.group(1).strip()
        company = company.strip("*").strip().strip("\"'").strip()
        if company:
            values.append(company)
    if not values:
        return None
    if len(set(values)) > 1:
        return None
    return values[0]


def backfill_cover_letter_company(
    cover_letter_payload: dict, posting_text: str,
) -> tuple[dict, Optional[str]]:
    """Returns (possibly-new cover_letter_payload, filled_company_or_None).

    Canonical source-of-truth policy: whenever a literal company name can be
    reliably parsed from `posting_text` (see `extract_company_from_posting`
    -- including the ambiguity guard), that parsed value is the SOLE
    authority for `company`, UNCONDITIONALLY -- not merely a fallback for an
    empty/placeholder value. A non-empty, non-placeholder value the model
    wrote (e.g. a paraphrased team name) is not exempt from this: if it
    differs at all from the posting's own parsed literal, it is replaced.
    This closes the entire class of wrong-but-nonempty company names, not
    just an enumerable placeholder subset.

    Only when no literal company can be reliably parsed (no header line, or
    2+ disagreeing header lines -- the ambiguity guard) does this fall back
    to preserving whatever `company` already holds -- still never a
    fabrication.

    If the parsed company already exactly equals the existing value (the
    common case once this fix is live and has already run once), the EXACT
    SAME object is returned unchanged (no rewrite, no copy) and the second
    return value is None -- callers can use `bool(filled_company)` to
    decide whether a rewrite/log entry is needed, exactly like
    `normalize_jd_concepts.normalize_jd_requirements`'s `merge_notes`
    convention."""
    if not isinstance(cover_letter_payload, dict):
        return cover_letter_payload, None
    existing = str(cover_letter_payload.get("company", "") or "").strip()
    company = extract_company_from_posting(posting_text)
    if not company or company == existing:
        return cover_letter_payload, None
    out = dict(cover_letter_payload)
    out["company"] = company
    return out, company


def company_check_status(cover_letter_company: object, posting_text: str) -> str:
    """The one canonical PASS/FAIL/NO_LITERAL_SOURCE verdict comparing a
    FINAL `cover_letter_payload.company` value against the posting's own
    parsed literal company -- reused by `benchmark_report.py` (and any
    other diagnostic tooling) so a reported verdict can never drift out of
    sync with what `backfill_cover_letter_company` itself would have done.

    - `"NO_LITERAL_SOURCE"`: no literal company is reliably parseable from
      `posting_text` (no header line, or 2+ disagreeing header lines) --
      reported explicitly rather than inventing a PASS or a FAIL.
    - `"PASS"`: the final value exactly equals the posting's own parsed
      literal company.
    - `"FAIL"`: a literal company IS parseable and the final value does not
      exactly equal it (empty, a placeholder, or any other mismatch)."""
    parsed = extract_company_from_posting(posting_text)
    if not parsed:
        return "NO_LITERAL_SOURCE"
    final = str(cover_letter_company or "").strip()
    return "PASS" if final == parsed else "FAIL"
