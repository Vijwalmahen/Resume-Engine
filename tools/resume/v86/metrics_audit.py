#!/usr/bin/env python3
"""
Feature B -- evidence-aware quantifiable-impact audit (ADVISORY ONLY).

The question this answers is deliberately narrow: for a measurement the
candidate's own records already VERIFY and already attach to a specific
resume entry, does that entry's final, tailored text still communicate
it? Nothing else. In particular this module never asks "does this bullet
have a number in it", never proposes a number, and never treats a missing
number as evidence that a claim is false.

Trusted inputs, all of them existing schemas (see
master/examples/metrics_registry.example.json and
master/examples/runtime_resume_context.example.json):

  metrics_registry.json  {"metrics": [{"id", "owner_id", "statement",
                           "verified", "source"}, ...]}
  runtime_resume_context.json
                         experience[] / projects[] entries carrying their
                         own `id`, `tailoring_mode`, `project_status`,
                         `canonical_bullets` and/or
                         `alternate_bullets_for_dynamic_tailoring`
  resume_payload.json    this job's actual tailored output --
                         `experience_alternate_bullets[owner_id]` and
                         `selected_project` (whose `owner_id` is the bound
                         runtime-context project id, exactly as
                         `deterministic_pipeline`/`evidence_ownership`
                         already use it)

The source-to-bullet relationship must be PROVEN before anything is
reported, in this order:

  1. the metric must be `verified` and name an `owner_id`;
  2. that `owner_id` must resolve to exactly ONE runtime-context entry
     (zero matches, or two entries sharing an id, is ambiguity ->
     `not_evaluated`, never a guess);
  3. the metric's statement must contain at least one measurement token
     that survives the date/version/identifier filter -- if the
     "measurement" is really a year, a semantic version, or a bare
     technical identifier, there is nothing to audit;
  4. at least one APPROVED SOURCE bullet belonging to that same owner
     must itself carry that measurement -- this is the link that makes
     "the tailored bullet dropped it" a meaningful statement. A metric
     that no approved bullet for its owner expresses is `not_evaluated`,
     not an omission;
  5. the owner must actually be rendered on THIS job's resume. A
     selectable project that was not bound for this job is
     `not_applicable` -- its metric was never dropped, it simply is not
     on this resume.

Only when all five hold, and none of that owner's final bullets carries
any of the measurement tokens, is an advisory omission reported -- scoped
to that owner, so an unrelated measurement can never be attributed to the
wrong bullet.

Advisory in this version: no correction round, no rewrite, no model call.
"""

from __future__ import annotations

import re
import unicodedata

_MONTHS = (
    "jan", "feb", "mar", "apr", "may", "jun",
    "jul", "aug", "sep", "oct", "nov", "dec",
)

# A measurement candidate: a number, optionally with a unit or a percent
# sign attached. Deliberately generous at the capture step -- the
# filtering below is what decides whether it is an IMPACT measurement.
_NUMBER_WITH_UNIT = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|ms|s\b|x\b|k\b|m\b|b\b|gb|mb|kb|tb|hours?|hrs?|days?|weeks?|months?|minutes?|mins?|seconds?|qps|rps)?",
    re.IGNORECASE,
)

# Things that look numeric but are never an impact metric.
_SEMVER = re.compile(r"\bv?\d+\.\d+\.\d+\b", re.IGNORECASE)
_VERSIONED_IDENTIFIER = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:\.net|c\+\+|c#|python|java|node|cuda|php|ruby|rust|postgres(?:ql)?|mysql|"
    r"spark|hadoop|tensorflow|pytorch|ubuntu|windows|macos|ios|android)"
    r"\s*v?\d+(?:\.\d+)*",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(
    r"\b(?:" + "|".join(_MONTHS) + r")[a-z]*\.?\s+\d{4}\b", re.IGNORECASE
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

# A number welded directly onto a leading word fragment is an identifier,
# not a measurement: `p95`, `f1`, `ndcg@10`, `x86`, `bm25`, `gpt4`.
_LETTER_PREFIXED_IDENTIFIER = re.compile(r"\b[a-z]+@?\d+(?:\.\d+)?\b", re.IGNORECASE)


def _strip_non_metric_spans(text: str) -> str:
    """Blank out spans whose digits are dates / software versions /
    versioned technical identifiers before measurement extraction, so
    those are never mistaken for impact metrics."""
    cleaned = _VERSIONED_IDENTIFIER.sub(" ", text)
    cleaned = _SEMVER.sub(" ", cleaned)
    cleaned = _MONTH_YEAR.sub(" ", cleaned)
    cleaned = _YEAR.sub(" ", cleaned)
    cleaned = _LETTER_PREFIXED_IDENTIFIER.sub(" ", cleaned)
    return cleaned


def normalize_measurement_text(text: str) -> str:
    """Normalize for measurement comparison only: NFKC, casefold, drop
    thousands separators, and close the gap between a number and its unit
    so `480 ms` and `480ms` compare equal. Digits and units are never
    removed."""
    folded = unicodedata.normalize("NFKC", str(text or "")).casefold()
    folded = re.sub(r"(?<=\d),(?=\d{3}\b)", "", folded)
    folded = re.sub(r"(?<=\d)\s+(?=%|ms\b|s\b|x\b|k\b|m\b|b\b|gb\b|mb\b|kb\b|tb\b)", "", folded)
    return re.sub(r"\s+", " ", folded).strip()


def extract_measurements(statement: str) -> list:
    """Ordered, de-duplicated measurement tokens found in a statement
    (e.g. `["480ms", "190ms"]`, `["70%"]`). Empty when the statement
    carries no quantifiable impact measurement at all -- in which case
    nothing is ever reported for it and no number is invented."""
    normalized = normalize_measurement_text(statement)
    scanned = _strip_non_metric_spans(normalized)

    found = []
    for match in _NUMBER_WITH_UNIT.finditer(scanned):
        value = match.group("value").replace(",", "")
        unit = (match.group("unit") or "").strip()
        token = f"{value}{unit}"
        if token not in found:
            found.append(token)
    return found


_TOKEN_SPLIT = re.compile(r"^(?P<value>\d[\d.]*)(?P<unit>.*)$")


def _measurement_pattern(token: str):
    """Build the search pattern for one extracted measurement token.

    `extract_measurements` joins a value and its unit into a single
    comparable token (`"45ms"`, `"4hours"`, `"70%"`), but the text being
    searched may legitimately separate them (`"45 ms"`, `"4 hours"`), so
    the gap must be optional HERE rather than relying on the normalizer
    to close it -- the normalizer only closes the gap for the short
    symbol units, which silently made every word-unit measurement
    (hours/minutes/days/weeks/months/seconds) impossible to find.

    The boundaries stay strict in both directions: `(?<![\\w.])` keeps
    `45ms` from matching inside `245ms`, and `(?![\\w])` keeps `18000`
    from matching inside `180000`.
    """
    match = _TOKEN_SPLIT.match(token)
    if not match:
        body = re.escape(token)
    else:
        unit = match.group("unit")
        body = re.escape(match.group("value"))
        if unit:
            body += r"\s*" + re.escape(unit)
    return re.compile(r"(?<![\w.])" + body + r"(?![\w])")


def measurement_present(measurements: list, texts: list) -> list:
    """Which of `measurements` any of `texts` still communicates."""
    haystacks = [normalize_measurement_text(t) for t in texts if isinstance(t, str)]
    present = []
    for token in measurements:
        pattern = _measurement_pattern(token)
        if any(pattern.search(h) for h in haystacks):
            present.append(token)
    return present


def _source_bullets(entry: dict) -> list:
    bullets = []
    for key in ("canonical_bullets", "alternate_bullets_for_dynamic_tailoring"):
        for bullet in entry.get(key) or []:
            if isinstance(bullet, str) and bullet.strip():
                bullets.append(bullet)
    return bullets


def _resolve_owner(runtime_context: dict, owner_id: str):
    """Exactly-one-match resolution across experience[] and projects[].
    Returns `(entry, container)` or `(None, reason)`."""
    matches = []
    for container in ("experience", "projects"):
        for entry in runtime_context.get(container) or []:
            if isinstance(entry, dict) and str(entry.get("id", "")).strip() == owner_id:
                matches.append((entry, container))
    if not matches:
        return None, "owner_id does not resolve to any experience or project entry"
    if len(matches) > 1:
        return None, "owner_id resolves to more than one entry (ambiguous ownership)"
    return matches[0], None


def _bullets_at_path(resume_payload: dict, path: list):
    """Walk an explicit payload path (list of dict keys / list indices)
    to the bullets a deployment's own payload shape keeps them at.

    Used ONLY when the caller supplied that path explicitly via
    `content_map` (see metrics_registry_adapter). Nothing is searched
    for or guessed: a path that does not resolve to a non-empty list of
    strings is an error the caller is told about, not something this
    module works around."""
    node = resume_payload
    for step in path:
        if isinstance(node, dict):
            if step not in node:
                return None
            node = node[step]
        elif isinstance(node, list):
            try:
                node = node[int(step)]
            except (ValueError, TypeError, IndexError):
                return None
        else:
            return None
    if not isinstance(node, list):
        return None
    bullets = [b for b in node if isinstance(b, str) and b.strip()]
    return bullets or None


def _final_bullets_for_owner(
    entry: dict, container: str, resume_payload: dict, content_map: dict = None,
):
    """The bullets this job's resume actually renders for `entry`.

    Returns `(bullets, status, reason)` where `status` is one of
    `"ok"`, `"not_applicable"` (the entry is not on this resume) or
    `"not_evaluated"` (the mapping could not be established).

    An explicit `content_map[owner_id]["final_bullets"]` path takes
    precedence over the schema-driven resolution below -- that is the
    supported way for a deployment whose payload key names differ to
    say, unambiguously, where its shipped bullets live.
    """
    owner_id = str(entry.get("id", "")).strip()

    explicit = (content_map or {}).get(owner_id) or {}
    path = explicit.get("final_bullets")
    if path:
        bullets = _bullets_at_path(resume_payload, path)
        if bullets is None:
            return None, "not_evaluated", (
                "the explicit final_bullets path supplied for this owner does not resolve "
                "to a non-empty list of bullet strings in this job's payload"
            )
        return bullets, "ok", None

    if container == "experience":
        if entry.get("tailoring_mode") == "tailorable":
            alternates = resume_payload.get("experience_alternate_bullets") or {}
            bullets = alternates.get(owner_id)
            if not isinstance(bullets, list) or not bullets:
                return None, "not_evaluated", (
                    "this job's resume_payload has no experience_alternate_bullets "
                    "entry for the owning experience"
                )
            return [b for b in bullets if isinstance(b, str)], "ok", None
        # A protected experience renders its own canonical bullets
        # verbatim (only their order may change), so those ARE the final
        # bullets -- nothing in the tailoring path can drop a measurement
        # from them.
        canonical = [b for b in (entry.get("canonical_bullets") or []) if isinstance(b, str)]
        if not canonical:
            return None, "not_evaluated", "protected experience has no canonical bullets recorded"
        return canonical, "ok", None

    selected = resume_payload.get("selected_project") or {}
    if str(selected.get("owner_id", "")).strip() == owner_id:
        bullets = [b for b in (selected.get("bullets") or []) if isinstance(b, str)]
        if not bullets:
            return None, "not_evaluated", "selected_project carries no bullets in this job's payload"
        return bullets, "ok", None

    if entry.get("project_status") == "required":
        canonical = [b for b in (entry.get("canonical_bullets") or []) if isinstance(b, str)]
        if not canonical:
            return None, "not_evaluated", "required project has no canonical bullets recorded"
        return canonical, "ok", None

    return None, "not_applicable", (
        "this selectable project was not bound for this job, so it is not on this resume"
    )


def audit_metrics(
    runtime_context: dict,
    metrics_registry: dict,
    resume_payload: dict,
    content_map: dict = None,
) -> dict:
    """Evaluate every registry metric against this job's tailored output.

    Returns::

        {
          "status": "EVALUATED" | "NOT_EVALUATED",
          "reason": <str or None>,
          "metrics_considered": <int>,
          "results": [ {"metric_id", "owner_id", "status", "reason",
                        "measurements", "measurements_present",
                        "source_bullet_index", "final_bullet_count"} ],
          "summary": {"omitted": n, "present": n,
                      "not_evaluated": n, "not_applicable": n},
        }

    `status == "omitted"` is the only row that becomes an advisory
    finding.
    """
    runtime_context = runtime_context or {}
    resume_payload = resume_payload or {}
    registry_metrics = (metrics_registry or {}).get("metrics")

    if not isinstance(registry_metrics, list):
        return {
            "status": "NOT_EVALUATED",
            "reason": "no metrics registry available (or it declares no `metrics` list)",
            "metrics_considered": 0, "results": [],
            "summary": {"omitted": 0, "present": 0, "not_evaluated": 0, "not_applicable": 0},
        }

    if not (runtime_context.get("experience") or runtime_context.get("projects")):
        return {
            "status": "NOT_EVALUATED",
            "reason": "no structured candidate context available to resolve metric ownership",
            "metrics_considered": len(registry_metrics), "results": [],
            "summary": {"omitted": 0, "present": 0, "not_evaluated": 0, "not_applicable": 0},
        }

    results = []
    for metric in registry_metrics:
        if not isinstance(metric, dict):
            continue
        metric_id = str(metric.get("id") or "").strip() or "<unnamed metric>"
        owner_id = str(metric.get("owner_id") or "").strip()
        row = {
            "metric_id": metric_id,
            "owner_id": owner_id or None,
            "status": "not_evaluated",
            "reason": None,
            "measurements": [],
            "measurements_present": [],
            "source_bullet_index": None,
            "final_bullet_count": 0,
        }

        if metric.get("verified") is not True:
            row["reason"] = "metric is not marked verified in the registry"
            results.append(row)
            continue

        if not owner_id:
            row["reason"] = "metric declares no owner_id, so no bullet can be associated with it"
            results.append(row)
            continue

        resolved, problem = _resolve_owner(runtime_context, owner_id)
        if resolved is None:
            row["reason"] = problem
            results.append(row)
            continue
        entry, container = resolved

        # A registry entry may carry its measurement as structured
        # tokens already (see metrics_registry_adapter, which builds
        # them from the registry's OWN value/unit fields using
        # `extract_measurements`). Those are authoritative: re-parsing
        # a prose description instead could widen the metric to
        # unrelated numbers that happen to appear in it.
        precomputed = metric.get("measurements")
        if isinstance(precomputed, list) and precomputed:
            measurements = [str(t) for t in precomputed if str(t).strip()]
        else:
            measurements = extract_measurements(metric.get("statement", ""))
        row["measurements"] = measurements
        if not measurements:
            row["reason"] = (
                "the registry entry carries no quantifiable impact measurement "
                "(dates, versions and technical identifiers are not metrics)"
            )
            results.append(row)
            continue

        source_bullets = _source_bullets(entry)
        source_index = None
        for index, bullet in enumerate(source_bullets):
            if measurement_present(measurements, [bullet]):
                source_index = index
                break
        if source_index is None:
            row["reason"] = (
                "no approved source bullet for this owner communicates the measurement, "
                "so no source-to-bullet relationship could be established"
            )
            results.append(row)
            continue
        row["source_bullet_index"] = source_index

        final_bullets, status, reason = _final_bullets_for_owner(
            entry, container, resume_payload, content_map,
        )
        if status != "ok":
            row["status"] = status
            row["reason"] = reason
            results.append(row)
            continue

        row["final_bullet_count"] = len(final_bullets)
        present = measurement_present(measurements, final_bullets)
        row["measurements_present"] = present
        if present:
            row["status"] = "present"
        else:
            row["status"] = "omitted"
            row["reason"] = (
                "an approved source bullet for this entry carries this verified measurement, "
                "but none of the entry's final tailored bullets communicates it"
            )
        results.append(row)

    summary = {"omitted": 0, "present": 0, "not_evaluated": 0, "not_applicable": 0}
    for row in results:
        summary[row["status"]] = summary.get(row["status"], 0) + 1

    return {
        "status": "EVALUATED",
        "reason": None,
        "metrics_considered": len(registry_metrics),
        "results": results,
        "summary": summary,
    }
