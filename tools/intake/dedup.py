#!/usr/bin/env python3
"""
One posting, one identity, everywhere.

Intake refuses to create a second copy of a posting that is already known, in
any of the four places a posting can already exist:

  1. the `Intake` worksheet (a row for this Job Key already exists)
  2. `jobs/batch_inbox/` (a normalized file is waiting to be processed)
  3. `jobs/batch_done/` (the batch already consumed it)
  4. completed job directories under `output/` (artifacts already exist)

Identity rule
-------------
A posting's Job Key, its `jobs/batch_inbox/<stem>.md` filename, and the key
the V8.6 pipeline later writes into the tracker must all be the same string.
That is enforced by construction rather than convention:

  - the key is `<source>-<source_job_id>`, produced by
    `orchestrator/universal_job.compute_job_key` (see `url_classifier.py`);
  - the file is written at `<job_key>.md` (see `write_job_file.py`);
  - `tools/resume/v86/common.job_key_for` is literally the file stem, so the
    pipeline's tracker key equals the intake key with no translation step.

When no stable source job id can be established, intake FAILS CLOSED
(`IdentityError`) rather than hashing something plausible into a key: a
guessed key cannot be matched again later, which is precisely how duplicates
are created.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))
import universal_job  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import url_classifier  # noqa: E402

NEW = "NEW"
DUPLICATE_INTAKE = "DUPLICATE_INTAKE"
DUPLICATE_INBOX = "DUPLICATE_INBOX"
DUPLICATE_DONE = "DUPLICATE_DONE"
DUPLICATE_COMPLETED = "DUPLICATE_COMPLETED"

DUPLICATE_STATUSES = (DUPLICATE_INTAKE, DUPLICATE_INBOX, DUPLICATE_DONE, DUPLICATE_COMPLETED)

_URL_LINE_RE = re.compile(r"(?im)^URL:[ \t]*(\S+)[ \t]*$")
_JOB_KEY_LINE_RE = re.compile(r"(?im)^Job Key:[ \t]*(\S+)[ \t]*$")
_SAFE_STEM_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class IdentityError(Exception):
    """Raised when a posting cannot be given a stable, reusable identity."""


@dataclass
class DuplicateDecision:
    status: str
    matched_key: Optional[str] = None
    matched_where: Optional[str] = None
    matched_on: Optional[str] = None   # "job_key" | "canonical_url"

    @property
    def is_duplicate(self) -> bool:
        return self.status in DUPLICATE_STATUSES


@dataclass
class DedupIndex:
    key_locations: dict = field(default_factory=dict)   # job_key -> (status, where)
    url_keys: dict = field(default_factory=dict)        # canonical url -> job_key
    scanned: list = field(default_factory=list)

    def _record(self, job_key: Optional[str], status: str, where: str,
                canonical_url: Optional[str] = None) -> None:
        if job_key and job_key not in self.key_locations:
            self.key_locations[job_key] = (status, where)
        if canonical_url:
            canonical = universal_job.canonicalize_url(canonical_url)
            if canonical and canonical not in self.url_keys and job_key:
                self.url_keys[canonical] = job_key

    def check(self, job_key: str, canonical_url: Optional[str] = None) -> DuplicateDecision:
        if job_key in self.key_locations:
            status, where = self.key_locations[job_key]
            return DuplicateDecision(status=status, matched_key=job_key,
                                     matched_where=where, matched_on="job_key")
        canonical = universal_job.canonicalize_url(canonical_url) if canonical_url else None
        if canonical and canonical in self.url_keys:
            matched_key = self.url_keys[canonical]
            status, where = self.key_locations.get(matched_key, (DUPLICATE_INTAKE, "unknown"))
            return DuplicateDecision(status=status, matched_key=matched_key,
                                     matched_where=where, matched_on="canonical_url")
        return DuplicateDecision(status=NEW)

    def add_pending(self, job_key: str, canonical_url: Optional[str] = None) -> None:
        """Register a posting intake is about to write in THIS run, so two
        identical URLs in one input list cannot both be written."""
        self._record(job_key, DUPLICATE_INBOX, "this intake run", canonical_url)


def _scan_markdown_dir(index: DedupIndex, directory: Optional[Path], status: str) -> None:
    if not directory:
        return
    directory = Path(directory)
    index.scanned.append(str(directory))
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("*.md")):
        job_key = path.stem
        canonical = None
        try:
            # errors="replace": an unreadable/odd-encoding file still counts as
            # an existing posting by its stem; dedup must never crash on one.
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        key_match = _JOB_KEY_LINE_RE.search(text)
        if key_match:
            job_key = key_match.group(1).strip() or job_key
        url_match = _URL_LINE_RE.search(text)
        if url_match:
            canonical = url_match.group(1).strip()
        index._record(job_key, status, str(path), canonical)


def build_index(intake_rows=None, inbox_dir: Optional[Path] = None,
                done_dir: Optional[Path] = None, output_dir: Optional[Path] = None) -> DedupIndex:
    """Build the dedup index from every place a posting can already exist.

    `intake_rows` is the list of dicts `intake_tracker.read_intake_rows`
    returns (header -> value); nothing else about the workbook is needed here.
    """
    index = DedupIndex()

    for row in intake_rows or []:
        job_key = (row.get("Job Key") or "").strip()
        canonical = (row.get("Canonical URL") or "").strip() or None
        # Only a row that actually produced a normalized posting blocks a
        # later attempt. A row recording a BLOCKED, truncated, malformed or
        # failed scrape is a record of what happened, not a claim that the
        # posting is in hand -- re-running those URLs later is exactly how a
        # transient block gets recovered from, and the upsert updates the
        # existing row in place rather than adding a second one.
        wrote_a_file = bool((row.get("Normalized File") or "").strip())
        if not wrote_a_file:
            continue
        index._record(job_key, DUPLICATE_INTAKE, "Intake worksheet", canonical)

    _scan_markdown_dir(index, inbox_dir, DUPLICATE_INBOX)
    _scan_markdown_dir(index, done_dir, DUPLICATE_DONE)

    if output_dir:
        output_dir = Path(output_dir)
        index.scanned.append(str(output_dir))
        if output_dir.is_dir():
            for child in sorted(output_dir.iterdir()):
                if child.is_dir() and not child.name.startswith("."):
                    index._record(child.name, DUPLICATE_COMPLETED, str(child))

    return index


def require_stable_identity(classification) -> str:
    """Return the job key for a classification, or raise IdentityError.

    Fails closed on anything that is not a fully identified posting — this is
    the single gate every write path goes through."""
    if classification is None:
        raise IdentityError("no classification")
    if classification.status != url_classifier.SUPPORTED or not classification.job_key:
        raise IdentityError(
            f"no stable identity for {classification.raw_url!r} "
            f"({classification.status}: {classification.reason})"
        )
    return classification.job_key


def assert_filename_agreement(job_key: str, path) -> None:
    """The file stem IS the job key — the V8.6 pipeline derives its tracker
    key from the stem (`common.job_key_for`), so any drift here silently
    creates a second identity for the same posting."""
    stem = Path(path).stem
    if stem != job_key:
        raise IdentityError(
            f"filename stem {stem!r} does not match job key {job_key!r}; "
            "the pipeline would track this posting under a different key"
        )
    if not _SAFE_STEM_RE.match(stem):
        raise IdentityError(f"job key {stem!r} is not a safe filename stem")
