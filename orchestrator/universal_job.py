#!/usr/bin/env python3
"""
universal_job.py — source-agnostic job schema, canonicalization, universal
job-key computation, and jobs/inbox Markdown rendering for the rolling-queue
ingestion pipeline (queue/incoming/*.md -> jobs/inbox/*.md).

This module is part of the OUTER orchestration layer, alongside
rolling_queue.py. It never edits any frozen V8.5 production file and has no
network dependency — everything here is pure text processing.

Any Claude conversation (external or interactive) that has already extracted
a job posting writes a standardized Markdown file with a flat YAML-style
frontmatter block (see FRONTMATTER_FIELDS) followed by the full job
description body, and drops it in queue/incoming/. This module is what turns
that file into a validated, deduplicated, canonical job record.

Deliberately NOT a general YAML parser — the frontmatter contract here is a
flat mapping of scalar `key: value` lines, which is all the universal job
schema needs (see docstring on parse_frontmatter).
"""

from __future__ import annotations

import hashlib
import re
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

# ---------------------------------------------------------------------------
# Universal job schema
# ---------------------------------------------------------------------------

FRONTMATTER_FIELDS = [
    "source", "source_domain", "source_job_id", "requisition_id",
    "source_url", "application_url", "company_careers_url",
    "company", "role", "location", "work_arrangement", "employment_type",
    "posted_date", "closing_date", "compensation",
    "scraped_at",
]

REQUIRED_FIELDS = ["company", "role", "source", "source_url"]

# A job posting body shorter than this is treated as missing/incomplete —
# mirrors the MIN_DESCRIPTION_CHARS threshold the old LinkedIn-only scraper
# used, kept intentionally low here since some source sites legitimately
# post short listings.
MIN_BODY_CHARS = 40

_NULLISH = {"", "null", "none", "~", "n/a", "na"}
_FRONTMATTER_DELIM = re.compile(r"^---\s*$")
_MD_LINK_RE = re.compile(r"^\[([^\]]*)\]\(([^)]*)\)$")

# Query-string params stripped during canonicalization because they carry no
# job identity (click tracking, referral tagging, ad attribution, ...).
_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_EXACT = {
    "trk", "trkinfo", "ref", "refid", "ref_id", "src", "gclid", "fbclid",
    "mc_cid", "mc_eid", "li_fat_id", "trackingid",
}


class SchemaError(Exception):
    """Raised with a list of human-readable validation problems — never
    raised for a single opaque reason, so the companion .error.txt written
    to queue/rejected/ can explain exactly what failed."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


# ---------------------------------------------------------------------------
# URL sanitization / canonicalization
# ---------------------------------------------------------------------------

def sanitize_url(raw) -> Optional[str]:
    """Unwrap a Markdown-formatted link ('[label](url)') down to the actual
    URL, and normalize any nullish sentinel value down to None. Idempotent."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text.lower() in _NULLISH:
        return None
    m = _MD_LINK_RE.match(text)
    if m:
        label, target = m.group(1).strip(), m.group(2).strip()
        text = target or label or text
    return text or None


def canonicalize_url(raw) -> Optional[str]:
    """Lowercase scheme/host, drop the fragment, strip known tracking query
    params, sort the remaining ones, and strip a trailing slash from the
    path — so the same job link reached two different ways still
    deduplicates. Falls back to the sanitized-but-uncanonicalized text for
    anything that isn't a real absolute URL (never raises)."""
    text = sanitize_url(raw)
    if not text:
        return None
    try:
        parsed = urlparse(text)
    except ValueError:
        return text
    if not parsed.scheme or not parsed.netloc:
        return text
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or ""
    kept = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAM_EXACT
        and not k.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    kept.sort()
    query = urlencode(kept)
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_key_component(text) -> str:
    """Lowercase, collapse anything non-alphanumeric to a single '-'. Used
    for every component that goes into a universal job key or a
    normalized-identity dedup composite."""
    text = ("" if text is None else str(text)).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return re.sub(r"-+", "-", text).strip("-") or "unknown"


def sha256_short(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a Markdown file into (frontmatter_dict, body).

    Only supports a flat mapping of scalar `key: value` lines inside a
    leading `---` / `---` delimited block — intentionally not a general
    YAML parser, since that's all the universal job schema needs. Raises
    SchemaError if the frontmatter block is missing, unterminated, or
    contains a line that isn't a `key: value` pair (blank lines and `#`
    comments inside the block are ignored).
    """
    lines = text.splitlines()
    if not lines or not _FRONTMATTER_DELIM.match(lines[0]):
        raise SchemaError(["missing YAML frontmatter (file must start with a '---' line)"])

    fm: dict = {}
    idx = 1
    closed = False
    while idx < len(lines):
        line = lines[idx]
        if _FRONTMATTER_DELIM.match(line):
            closed = True
            idx += 1
            break
        idx += 1
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise SchemaError([f"malformed frontmatter line (expected 'key: value'): {line!r}"])
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        fm[key] = None if value.lower() in _NULLISH else value
    if not closed:
        raise SchemaError(["frontmatter block was never closed with a second '---' line"])

    body = "\n".join(lines[idx:]).strip("\n")
    return fm, body


def validate_schema(fm: dict, body: str) -> list[str]:
    """Return a list of validation problems (empty list = valid). Never
    raises — callers decide what to do with a non-empty list."""
    problems = []
    for f in REQUIRED_FIELDS:
        v = fm.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            problems.append(f"missing required field: {f}")
    if fm.get("source_url") and canonicalize_url(fm.get("source_url")) is None:
        problems.append("source_url is present but not a usable URL")
    if not body or len(body.strip()) < MIN_BODY_CHARS:
        problems.append(
            f"job description body is missing or too short (<{MIN_BODY_CHARS} chars after stripping)"
        )
    return problems


# ---------------------------------------------------------------------------
# Universal job key
# ---------------------------------------------------------------------------

def compute_job_key(fm: dict, canonical_source_url: Optional[str]) -> str:
    """Deterministic universal job key. Priority order:
    1. normalize(source) + "-" + source_job_id, when source_job_id exists.
    2. normalize(company) + "-" + normalize(requisition_id), when
       requisition_id exists (and source_job_id does not).
    3. "job-" + short SHA256 of canonical source_url + company + role.
    """
    source_job_id = (fm.get("source_job_id") or "").strip() or None
    requisition_id = (fm.get("requisition_id") or "").strip() or None
    if source_job_id:
        return f"{normalize_key_component(fm.get('source'))}-{normalize_key_component(source_job_id)}"
    if requisition_id:
        return f"{normalize_key_component(fm.get('company'))}-{normalize_key_component(requisition_id)}"
    basis = f"{canonical_source_url or ''}|{fm.get('company') or ''}|{fm.get('role') or ''}"
    return f"job-{sha256_short(basis)}"


# ---------------------------------------------------------------------------
# Deduplication composites
# ---------------------------------------------------------------------------

def dedup_composites(fm: dict) -> dict:
    """Return the composite dedup key strings this job should be checked
    against, in priority order (see rolling_queue.find_duplicate_job). Any
    entry whose inputs are incomplete is simply omitted (None)."""
    source = fm.get("source")
    source_job_id = fm.get("source_job_id")
    company = fm.get("company")
    requisition_id = fm.get("requisition_id")
    role = fm.get("role")
    location = fm.get("location")

    source_job_composite = (
        f"{normalize_key_component(source)}|{normalize_key_component(source_job_id)}"
        if source and source_job_id else None
    )
    company_req_composite = (
        f"{normalize_key_component(company)}|{normalize_key_component(requisition_id)}"
        if company and requisition_id else None
    )
    company_role_location_composite = (
        f"{normalize_key_component(company)}|{normalize_key_component(role)}|{normalize_key_component(location)}"
        if company and role else None
    )
    return {
        "source_job_composite": source_job_composite,
        "company_req_composite": company_req_composite,
        "company_role_location_composite": company_role_location_composite,
    }


# ---------------------------------------------------------------------------
# jobs/inbox Markdown rendering — the standardized file the V8.5 worker reads
# ---------------------------------------------------------------------------

def _field(value, default="Not specified in posting"):
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def render_inbox_markdown(
    fm: dict, body: str, job_key: str,
    canonical_source_url: Optional[str], canonical_application_url: Optional[str],
) -> str:
    """Render the standardized jobs/inbox/*.md file. Deliberately keeps the
    same '# Job Posting' / '## Metadata' / '## Job Description' shape and
    the same core field labels (Company/Role/Location/Job ID/Source/URL/
    Employment Type) that the existing (LinkedIn-only) inbox files already
    used, extended with the new universal fields — so the frozen V8.5
    worker skills, which read this file with ordinary language
    understanding rather than a rigid parser, see a strict superset of what
    they have always seen."""
    lines = [
        "# Job Posting",
        "",
        "## Metadata",
        "",
        f"Company: {_field(fm.get('company'))}",
        f"Role: {_field(fm.get('role'))}",
        f"Location: {_field(fm.get('location'))}",
        f"Work Arrangement: {_field(fm.get('work_arrangement'))}",
        f"Employment Type: {_field(fm.get('employment_type'))}",
        f"Job ID: {_field(fm.get('source_job_id'), '')}",
        f"Requisition ID: {_field(fm.get('requisition_id'), '')}",
        f"Source: {_field(fm.get('source'))}",
        f"Source Domain: {_field(fm.get('source_domain'))}",
        f"URL: {_field(canonical_source_url or fm.get('source_url'))}",
        f"Application URL: {_field(canonical_application_url or fm.get('application_url'))}",
        f"Company Careers URL: {_field(fm.get('company_careers_url'))}",
        f"Date Posted: {_field(fm.get('posted_date'))}",
        f"Closing Date: {_field(fm.get('closing_date'))}",
        f"Compensation: {_field(fm.get('compensation'))}",
        f"Scraped At: {_field(fm.get('scraped_at'))}",
        f"Job Key: {job_key}",
        "",
        "## Job Description",
        "",
        body.strip(),
        "",
    ]
    return "\n".join(lines)
