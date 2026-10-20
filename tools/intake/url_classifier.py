#!/usr/bin/env python3
"""
Recognize and canonicalize a job URL, offline.

This module answers exactly one question: *which posting is this URL, and can
we name it stably?* It never opens a network connection — classification is
pure string work on the URL itself, so it is safe to run over a large list of
pasted links before deciding what (if anything) is worth scraping.

Canonicalization and key generation are NOT reimplemented here. Both are
delegated to the existing, already-tested outer-orchestrator helpers in
`orchestrator/universal_job.py`:

  - `canonicalize_url`  — lowercase scheme/host, drop fragment, strip known
    tracking params, sort the rest, strip a trailing slash.
  - `compute_job_key`   — `<source>-<source_job_id>` when a source job id
    exists, which is the form this package always requires (see `dedup.py`).

So a URL, its job key, its `jobs/batch_inbox/<stem>.md` filename, and the
tracker key the V8.6 pipeline later writes (`common.job_key_for`, which is
just the file stem) all name the same posting by construction.

Statuses
--------
`SUPPORTED`    — a known source AND a stable source job id were both read off
                 the URL. This is the only status intake will scrape or write
                 a file for.
`INCOMPLETE`   — the host is recognized (or at least a plausible employer
                 careers page) but no stable job id could be established. Fails
                 closed: never guessed, never hashed into a fake id.
`UNSUPPORTED`  — not a usable absolute http(s) URL at all.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))
import universal_job  # noqa: E402

SUPPORTED = "SUPPORTED"
INCOMPLETE = "INCOMPLETE"
UNSUPPORTED = "UNSUPPORTED"

SOURCE_LINKEDIN = "linkedin"
SOURCE_GREENHOUSE = "greenhouse"
SOURCE_LEVER = "lever"
SOURCE_ASHBY = "ashby"
SOURCE_WORKDAY = "workday"
SOURCE_INDEED = "indeed"
SOURCE_EMPLOYER = "employer"

KNOWN_SOURCES = (
    SOURCE_LINKEDIN, SOURCE_GREENHOUSE, SOURCE_LEVER,
    SOURCE_ASHBY, SOURCE_WORKDAY, SOURCE_INDEED, SOURCE_EMPLOYER,
)

# Sources intake is allowed to scrape. `employer` is deliberately absent: a
# direct employer page is recognized and canonicalized, but it carries no
# stable id this module can read off the URL, so it always fails closed.
SCRAPEABLE_SOURCES = (
    SOURCE_LINKEDIN, SOURCE_GREENHOUSE, SOURCE_LEVER,
    SOURCE_ASHBY, SOURCE_WORKDAY, SOURCE_INDEED,
)

_TRAILING_DIGITS_RE = re.compile(r"(\d{4,})$")
_UUIDISH_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F-]{20,}$")
_WORKDAY_HOST_RE = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$")
_WORKDAY_REQ_RE = re.compile(r"_(?P<req>[A-Za-z0-9][A-Za-z0-9-]*)$")
_INDEED_JK_RE = re.compile(r"^[0-9a-f]{8,}$")
_SAFE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Classification:
    """What intake knows about one URL before anything is scraped."""

    raw_url: str
    status: str
    source: Optional[str] = None
    source_job_id: Optional[str] = None
    job_key: Optional[str] = None
    canonical_url: Optional[str] = None
    host: Optional[str] = None
    reason: Optional[str] = None
    notes: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == SUPPORTED

    def as_dict(self) -> dict:
        return {
            "raw_url": self.raw_url,
            "status": self.status,
            "source": self.source,
            "source_job_id": self.source_job_id,
            "job_key": self.job_key,
            "canonical_url": self.canonical_url,
            "host": self.host,
            "reason": self.reason,
        }


def _path_segments(path: str) -> list:
    return [seg for seg in path.split("/") if seg]


def _query(parsed) -> dict:
    return {k.lower(): v for k, v in parse_qsl(parsed.query, keep_blank_values=False)}


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _linkedin_job_id(parsed, query: dict) -> Optional[str]:
    # /jobs/view/<slug>-<id>, /jobs/view/<id>, and the collections/search
    # pages that carry the id as ?currentJobId=<id>.
    for key in ("currentjobid", "jobid"):
        value = (query.get(key) or "").strip()
        if value.isdigit():
            return value
    segments = _path_segments(parsed.path)
    if "view" in segments:
        tail = segments[segments.index("view") + 1:]
        if tail:
            candidate = tail[0]
            if candidate.isdigit():
                return candidate
            match = _TRAILING_DIGITS_RE.search(candidate)
            if match:
                return match.group(1)
    return None


def _greenhouse_id(host: str, parsed, query: dict):
    # Greenhouse-hosted board: boards.greenhouse.io/<board>/jobs/<id>
    segments = _path_segments(parsed.path)
    if "jobs" in segments:
        idx = segments.index("jobs")
        board = segments[idx - 1] if idx >= 1 else None
        tail = segments[idx + 1:]
        if board and tail and tail[0].isdigit():
            return f"{board}-{tail[0]}", None
    # Greenhouse embedded on the employer's own domain: ?gh_jid=<id>
    gh_jid = (query.get("gh_jid") or "").strip()
    if gh_jid.isdigit():
        # Use the employer's own domain label (careers.examplecorp.com ->
        # examplecorp) rather than the leading `careers`/`jobs`/`www` label,
        # so the board component of the id names the employer.
        labels = [label for label in host.split(".") if label]
        board = labels[-2] if len(labels) >= 2 else host
        return f"{board}-{gh_jid}", None
    return None, "greenhouse URL carries no numeric job id"


def _two_segment_id(parsed, label: str):
    """jobs.lever.co/<company>/<id> and jobs.ashbyhq.com/<company>/<id>."""
    segments = [s for s in _path_segments(parsed.path) if s.lower() not in ("apply", "application")]
    if len(segments) >= 2:
        company, posting = segments[0], segments[1]
        if _UUIDISH_RE.match(posting) or len(posting) >= 8:
            return f"{company}-{posting}", None
    return None, f"{label} URL does not have a <company>/<posting-id> path"


def _workday_id(host_match, parsed):
    segments = _path_segments(parsed.path)
    if "job" in segments:
        tail = segments[segments.index("job") + 1:]
        if tail:
            match = _WORKDAY_REQ_RE.search(tail[-1])
            if match:
                return f"{host_match.group('tenant')}-{match.group('req')}", None
    return None, "Workday URL carries no trailing _<requisition-id> segment"


def _indeed_id(query):
    jk = (query.get("jk") or "").strip().lower()
    if jk and _INDEED_JK_RE.match(jk):
        return jk, None
    return None, "Indeed URL carries no ?jk=<job key> parameter"


def classify(raw_url: str) -> Classification:
    """Classify one URL. Pure string work — never a network request."""
    text = universal_job.sanitize_url(raw_url)
    if not text:
        return Classification(raw_url=str(raw_url or ""), status=UNSUPPORTED,
                              reason="empty or nullish URL")

    parsed = urlparse(text)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return Classification(raw_url=text, status=UNSUPPORTED,
                              reason="not an absolute http(s) URL")

    canonical = universal_job.canonicalize_url(text)
    host = urlparse(canonical).netloc
    # Host-only comparisons must ignore the port and any (never expected here)
    # userinfo part.
    bare_host = host.split("@")[-1].split(":")[0]
    query = _query(urlparse(canonical))

    source, source_job_id, reason = None, None, None
    workday_match = _WORKDAY_HOST_RE.match(bare_host)

    if _host_matches(bare_host, "linkedin.com"):
        source = SOURCE_LINKEDIN
        source_job_id = _linkedin_job_id(urlparse(canonical), query)
        reason = None if source_job_id else "LinkedIn URL carries no numeric job id"
    elif _host_matches(bare_host, "greenhouse.io") or query.get("gh_jid"):
        source = SOURCE_GREENHOUSE
        source_job_id, reason = _greenhouse_id(bare_host, urlparse(canonical), query)
    elif _host_matches(bare_host, "lever.co"):
        source = SOURCE_LEVER
        source_job_id, reason = _two_segment_id(urlparse(canonical), "Lever")
    elif _host_matches(bare_host, "ashbyhq.com"):
        source = SOURCE_ASHBY
        source_job_id, reason = _two_segment_id(urlparse(canonical), "Ashby")
    elif workday_match:
        source = SOURCE_WORKDAY
        source_job_id, reason = _workday_id(workday_match, urlparse(canonical))
    elif _host_matches(bare_host, "indeed.com"):
        source = SOURCE_INDEED
        source_job_id, reason = _indeed_id(query)
    else:
        source = SOURCE_EMPLOYER
        reason = (
            "direct employer page: recognized and canonicalized, but no stable job id can be "
            "read off the URL, so intake fails closed rather than inventing one"
        )

    if not source_job_id:
        return Classification(raw_url=text, status=INCOMPLETE, source=source,
                              canonical_url=canonical, host=bare_host, reason=reason)

    job_key = universal_job.compute_job_key(
        {"source": source, "source_job_id": source_job_id}, canonical,
    )
    if not _SAFE_KEY_RE.match(job_key):
        # compute_job_key normalizes every component, so this is a guard
        # against future changes, not an expected branch.
        return Classification(raw_url=text, status=INCOMPLETE, source=source,
                              canonical_url=canonical, host=bare_host,
                              reason=f"derived job key is not filesystem-safe: {job_key!r}")

    return Classification(
        raw_url=text, status=SUPPORTED, source=source,
        source_job_id=universal_job.normalize_key_component(source_job_id),
        job_key=job_key, canonical_url=canonical, host=bare_host,
    )


def classify_all(urls) -> list:
    return [classify(u) for u in urls]
