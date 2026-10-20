#!/usr/bin/env python3
"""
Feature D -- education-aware experience-timeline diagnostics
(INFORMATIONAL ONLY).

Reads the candidate's own structured `dates` strings from
master/runtime/runtime_resume_context.json -- the same
`"Jan 2025 -- May 2025"` shape every other consumer of that schema
already sees (see master/examples/runtime_resume_context.example.json) --
and reports uncovered intervals BETWEEN consecutive experience periods.

Hard rules this module is built around:

  - A gap is never a failure. The configured threshold (6 months by
    default) is a REVIEW threshold, nothing more. This check cannot fail
    the ATS/evidence gate, cannot request a correction, and cannot spend
    a model call.
  - Month precision or nothing. When every endpoint involved is month
    precise the arithmetic is month-aware and exact. A year-only,
    partial, or unparseable date can never be stretched into a claim
    about a six-month interval -- it makes the result `undetermined`.
  - Concurrent roles are not gaps. Overlapping and adjacent experience
    periods are merged before any interval is measured.
  - Education explains, it never erases. An interval fully covered by a
    dated education period is `explained_by_education`; partially
    covered is `partially_explained`; uncovered is `unexplained` and is
    reported for review only. If an education entry's own dates cannot
    be used (year-only, unparseable), an otherwise-uncovered interval is
    `undetermined` rather than `unexplained` -- the data cannot establish
    whether it is explained, and saying "unexplained" would assert more
    than is known.
  - Nothing is ever invented. This module never proposes an employment
    entry, never adjusts a date, and never suggests adding anything the
    candidate's records do not already contain.
"""

from __future__ import annotations

import re

DEFAULT_GAP_REVIEW_MONTHS = 6

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

ONGOING_WORDS = {"present", "current", "currently", "now", "ongoing", "today", "date"}

# The separators this schema's `dates` strings actually use ("--" in the
# runtime context, an en/em dash or a spaced hyphen elsewhere). A bare
# unspaced hyphen is deliberately NOT a separator: "Jan 2025-May 2025"
# is handled, but "Mid-2025" must not be split into two endpoints.
_RANGE_SPLIT = re.compile(r"\s*(?:--+|–|—|\bto\b|(?<=\d)\s*-\s*(?=[A-Za-z0-9])|\s-\s)\s*", re.IGNORECASE)

_MONTH_YEAR = re.compile(r"^([A-Za-z]+)\.?\s+(\d{4})$")
_YEAR_ONLY = re.compile(r"^(\d{4})$")
_NUMERIC_MONTH_YEAR = re.compile(r"^(\d{1,2})[/\-](\d{4})$")


def _month_index(year: int, month: int) -> int:
    """Whole months since year 0 -- the unit every interval calculation
    here uses, so differences are exact at month precision."""
    return year * 12 + (month - 1)


def _format_month(index: int) -> str:
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def parse_endpoint(text: str) -> dict:
    """Parse one side of a date range.

    Returns `{"precision": "month"|"year"|"ongoing"|"unknown",
    "month_index": int|None, "raw": str}`. `"year"` and `"unknown"` both
    mean the endpoint cannot support month-accurate interval math.
    """
    raw = str(text or "").strip().strip(",.")
    lowered = raw.casefold()

    if not raw:
        return {"precision": "unknown", "month_index": None, "raw": raw}

    if lowered in ONGOING_WORDS:
        return {"precision": "ongoing", "month_index": None, "raw": raw}

    match = _MONTH_YEAR.match(raw)
    if match:
        month = _MONTHS.get(match.group(1).casefold())
        if month:
            return {
                "precision": "month",
                "month_index": _month_index(int(match.group(2)), month),
                "raw": raw,
            }
        return {"precision": "unknown", "month_index": None, "raw": raw}

    match = _NUMERIC_MONTH_YEAR.match(raw)
    if match and 1 <= int(match.group(1)) <= 12:
        return {
            "precision": "month",
            "month_index": _month_index(int(match.group(2)), int(match.group(1))),
            "raw": raw,
        }

    match = _YEAR_ONLY.match(raw)
    if match:
        return {"precision": "year", "month_index": None, "raw": raw}

    return {"precision": "unknown", "month_index": None, "raw": raw}


def parse_date_range(text: str) -> dict:
    """Parse a whole `dates` string into
    `{"start": <endpoint>, "end": <endpoint>, "usable": bool, "raw": str}`.

    `usable` is True only when the start is month precise and the end is
    either month precise or explicitly ongoing -- the only two shapes that
    support an exact month-aware calculation.
    """
    raw = str(text or "").strip()
    parts = [p for p in _RANGE_SPLIT.split(raw) if p.strip()]

    if len(parts) == 1:
        single = parse_endpoint(parts[0])
        return {"start": single, "end": single, "usable": False, "raw": raw}
    if len(parts) != 2:
        unknown = {"precision": "unknown", "month_index": None, "raw": raw}
        return {"start": unknown, "end": unknown, "usable": False, "raw": raw}

    start = parse_endpoint(parts[0])
    end = parse_endpoint(parts[1])
    usable = start["precision"] == "month" and end["precision"] in ("month", "ongoing")
    if usable and end["precision"] == "month" and end["month_index"] < start["month_index"]:
        usable = False
    return {"start": start, "end": end, "usable": usable, "raw": raw}


def _intervals(entries: list, horizon: int) -> tuple:
    """Build closed month intervals for a list of dated entries.

    Returns `(intervals, unusable)` where each interval is
    `{"id", "start", "end", "ongoing"}` with inclusive month indices, and
    `unusable` lists the entries whose dates could not support exact
    month math.
    """
    intervals, unusable = [], []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("id") or entry.get("institution") or entry.get("employer") or "").strip()
        parsed = parse_date_range(entry.get("dates", ""))
        if not parsed["usable"]:
            unusable.append({
                "id": entry_id or "<unidentified entry>",
                "dates": parsed["raw"],
                "start_precision": parsed["start"]["precision"],
                "end_precision": parsed["end"]["precision"],
            })
            continue
        start = parsed["start"]["month_index"]
        ongoing = parsed["end"]["precision"] == "ongoing"
        end = horizon if ongoing else parsed["end"]["month_index"]
        intervals.append({
            "id": entry_id or "<unidentified entry>",
            "start": start,
            "end": max(end, start),
            "ongoing": ongoing,
        })
    intervals.sort(key=lambda i: (i["start"], i["end"]))
    return intervals, unusable


def _merge(intervals: list) -> list:
    """Merge overlapping AND month-adjacent intervals, so concurrent roles
    and back-to-back roles never look like a gap."""
    merged = []
    for interval in intervals:
        if merged and interval["start"] <= merged[-1]["end"] + 1:
            merged[-1] = {
                "start": merged[-1]["start"],
                "end": max(merged[-1]["end"], interval["end"]),
                "ids": merged[-1]["ids"] + [interval["id"]],
                "ongoing": merged[-1]["ongoing"] or interval["ongoing"],
            }
        else:
            merged.append({
                "start": interval["start"], "end": interval["end"],
                "ids": [interval["id"]], "ongoing": interval["ongoing"],
            })
    return merged


def _covered_months(interval_start: int, interval_end: int, covers: list) -> int:
    months = set(range(interval_start, interval_end + 1))
    covered = set()
    for cover in covers:
        covered |= months & set(range(cover["start"], cover["end"] + 1))
    return len(covered)


def audit_timeline(
    runtime_context: dict,
    gap_review_months: int = DEFAULT_GAP_REVIEW_MONTHS,
) -> dict:
    """Classify every uncovered interval between consecutive experience
    periods.

    Returns::

        {
          "status": "EVALUATED" | "NOT_EVALUATED" | "UNDETERMINED",
          "reason": <str or None>,
          "gap_review_months": <int>,
          "experience_periods": <int>,
          "education_periods": <int>,
          "undated_entries": {"experience": [...], "education": [...]},
          "intervals": [ {"from", "to", "months", "classification",
                          "reason", "between", "education_months_covered"} ],
        }

    `classification` is one of `explained_by_education`,
    `partially_explained`, `unexplained`, or `undetermined` (the last
    meaning the available dates cannot establish whether the interval is
    explained -- never a claim that it is not).
    """
    runtime_context = runtime_context or {}
    experience = runtime_context.get("experience") or []
    education = runtime_context.get("education") or []
    gap_review_months = int(gap_review_months)

    if not experience:
        return {
            "status": "NOT_EVALUATED",
            "reason": "no experience entries available in the candidate's structured context",
            "gap_review_months": gap_review_months,
            "experience_periods": 0, "education_periods": 0,
            "undated_entries": {"experience": [], "education": []},
            "intervals": [],
        }

    # An ongoing ("Present") period runs to the latest month any other
    # dated period reaches, which is all that is needed to decide whether
    # anything sits AFTER it -- nothing is assumed about "today".
    horizon = 0
    for entry in list(experience) + list(education):
        if isinstance(entry, dict):
            parsed = parse_date_range(entry.get("dates", ""))
            for side in ("start", "end"):
                if parsed[side]["month_index"] is not None:
                    horizon = max(horizon, parsed[side]["month_index"])

    exp_intervals, exp_unusable = _intervals(experience, horizon)
    edu_intervals, edu_unusable = _intervals(education, horizon)

    result = {
        "status": "EVALUATED",
        "reason": None,
        "gap_review_months": gap_review_months,
        "experience_periods": len(exp_intervals),
        "education_periods": len(edu_intervals),
        "undated_entries": {"experience": exp_unusable, "education": edu_unusable},
        "intervals": [],
    }

    if exp_unusable:
        # A year-only or unparseable experience date cannot establish that
        # any interval really is (or is not) six months long. Say so
        # instead of pretending otherwise.
        result["status"] = "UNDETERMINED"
        result["reason"] = (
            "one or more experience entries have year-only, incomplete or unparseable "
            "dates, which cannot support a month-accurate interval calculation"
        )
        return result

    if len(exp_intervals) < 2:
        result["status"] = "NOT_EVALUATED"
        result["reason"] = (
            "fewer than two month-precise experience periods, so there is no interval "
            "between consecutive experience entries to measure"
        )
        return result

    merged_experience = _merge(exp_intervals)
    merged_education = _merge(edu_intervals)

    for previous, following in zip(merged_experience, merged_experience[1:]):
        gap_start = previous["end"] + 1
        gap_end = following["start"] - 1
        months = gap_end - gap_start + 1
        if months < gap_review_months:
            continue

        covered = _covered_months(gap_start, gap_end, merged_education)
        if covered >= months:
            classification = "explained_by_education"
            reason = "a dated education period covers the whole interval"
        elif covered > 0:
            classification = "partially_explained"
            reason = (
                f"a dated education period covers {covered} of the interval's {months} months"
            )
        elif edu_unusable:
            # Nothing month-precise covers this interval, but at least one
            # education entry's dates could not be used at all -- so
            # whether it is covered is genuinely UNKNOWN. Calling that
            # "unexplained" would assert something the data does not
            # support; "cannot establish an explanation" is the honest
            # answer.
            classification = "undetermined"
            reason = (
                "no usable dated education period covers this interval, and "
                f"{len(edu_unusable)} education entr"
                f"{'y has' if len(edu_unusable) == 1 else 'ies have'} dates that cannot be "
                "used for coverage, so whether it is explained cannot be established"
            )
        else:
            classification = "unexplained"
            reason = (
                "no dated education period (or other dated entry in the candidate's "
                "structured context) covers this interval"
            )

        result["intervals"].append({
            "from": _format_month(gap_start),
            "to": _format_month(gap_end),
            "months": months,
            "classification": classification,
            "reason": reason,
            "between": [previous["ids"][-1], following["ids"][0]],
            "education_months_covered": covered,
        })

    if edu_unusable and result["intervals"]:
        result["reason"] = (
            "some education entries have dates that cannot be used for coverage; any "
            "interval they might have covered is classified 'undetermined' rather than "
            "'unexplained'"
        )

    return result
