#!/usr/bin/env python3
"""
Normalize one raw actor result into a documented, source-agnostic record.

The mapping from an actor's own result field names onto this record is always
EXPLICIT (`ResultFieldMap`). There is no default mapping and no heuristic
"try a few likely names" fallback: the historical actor is unidentified (see
`apify_client_shim.py`), and a guessed field name is how a teaser blurb ends
up shipped as if it were the full job description.

The record below is the one shape the rest of intake knows about:

  company            employer name, as stated by the posting
  title              role title, as stated by the posting
  location           location string, as stated
  source             intake source id (linkedin/greenhouse/... )
  source_job_id      stable id read off the URL by `url_classifier`
  canonical_url      canonical posting URL
  application_url    direct apply URL, ONLY when the actor supplied one
  employment_type    e.g. Full-time / Internship, as stated
  workplace_type     e.g. Remote / Hybrid / On-site, as stated
  date_posted        as stated
  compensation       as stated, else None — never estimated
  description        the COMPLETE job-description body, verbatim

Absent fields stay absent (None). Nothing is inferred from another field,
nothing is summarized, and the description is never trimmed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional
from urllib.parse import urlparse


class ExtractionError(Exception):
    """Raised when a raw result cannot be mapped at all (not a dict, or the
    mapped description field is present but not text). Callers map this to the
    MALFORMED_RESULT intake status."""


@dataclass
class ResultFieldMap:
    """Actor result field name for each record field. `description` is the
    only one that is mandatory to declare, because an unmapped description is
    indistinguishable from a blocked/teaser scrape and must never be guessed.
    """

    description: str
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    application_url: Optional[str] = None
    employment_type: Optional[str] = None
    workplace_type: Optional[str] = None
    date_posted: Optional[str] = None
    compensation: Optional[str] = None

    def declared(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v}


@dataclass
class JobRecord:
    source: str
    source_job_id: str
    canonical_url: str
    description: str
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    application_url: Optional[str] = None
    employment_type: Optional[str] = None
    workplace_type: Optional[str] = None
    date_posted: Optional[str] = None
    compensation: Optional[str] = None
    scraped_at: Optional[str] = None
    unmapped_fields: list = field(default_factory=list)

    @property
    def description_length(self) -> int:
        return len(self.description or "")

    def as_dict(self) -> dict:
        return asdict(self)

    def frontmatter(self) -> dict:
        """The universal-job frontmatter mapping (see
        orchestrator/universal_job.py's FRONTMATTER_FIELDS) this record
        corresponds to. Used by `write_job_file.py`."""
        return {
            "source": self.source,
            "source_domain": urlparse(self.canonical_url or "").netloc or None,
            "source_job_id": self.source_job_id,
            "requisition_id": None,
            "source_url": self.canonical_url,
            "application_url": self.application_url,
            "company_careers_url": None,
            "company": self.company,
            "role": self.title,
            "location": self.location,
            "work_arrangement": self.workplace_type,
            "employment_type": self.employment_type,
            "posted_date": self.date_posted,
            "closing_date": None,
            "compensation": self.compensation,
            "scraped_at": self.scraped_at,
        }


def _text(raw: dict, actor_field: Optional[str]) -> Optional[str]:
    if not actor_field:
        return None
    value = raw.get(actor_field)
    if value is None:
        return None
    if isinstance(value, (dict, list, tuple, set)):
        # A structured value where a string was declared means the mapping is
        # wrong; say so rather than stringifying a dict into the tracker.
        raise ExtractionError(
            f"actor field {actor_field!r} holds {type(value).__name__}, not text"
        )
    text = str(value).strip()
    return text or None


def extract(raw, field_map: ResultFieldMap, *, source: str, source_job_id: str,
            canonical_url: str, scraped_at: Optional[str] = None) -> JobRecord:
    """Map one raw actor result onto a `JobRecord`. Raises ExtractionError if
    the result is not a usable mapping."""
    if not isinstance(raw, dict):
        raise ExtractionError(f"actor result is {type(raw).__name__}, not a mapping")
    if not field_map.description:
        raise ExtractionError("ResultFieldMap.description is required; it is never guessed")

    description = _text(raw, field_map.description) or ""
    declared = set(field_map.declared().values())
    unmapped = sorted(k for k in raw.keys() if k not in declared)

    return JobRecord(
        source=source,
        source_job_id=source_job_id,
        canonical_url=canonical_url,
        description=description,
        company=_text(raw, field_map.company),
        title=_text(raw, field_map.title),
        location=_text(raw, field_map.location),
        application_url=_text(raw, field_map.application_url),
        employment_type=_text(raw, field_map.employment_type),
        workplace_type=_text(raw, field_map.workplace_type),
        date_posted=_text(raw, field_map.date_posted),
        compensation=_text(raw, field_map.compensation),
        scraped_at=scraped_at,
        unmapped_fields=unmapped,
    )
