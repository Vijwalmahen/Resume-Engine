#!/usr/bin/env python3
"""
Write one normalized posting to `jobs/batch_inbox/<job_key>.md`.

The file this produces is the V8.6 batch pipeline's actual input: `run_batch.py`
globs `*.md`, derives the job key from the stem, and hands the whole file text
to `build_job_packet.build_packet` -> `job_posting_text`. Two downstream
contracts therefore have to hold exactly, and both are verified here before
anything is written:

  1. **The `Company:` heading.** `normalize_cover_letter_identity`
     deterministically reads the employer name off an anchored `Company:`
     header line, and treats a posting with two DISAGREEING header lines as
     having no parseable company at all (its ambiguity guard). The rendered
     metadata block contributes exactly one such line; if the scraped
     description body happens to contain another one that disagrees, this
     module refuses to write the file rather than shipping a posting whose
     employer name silently stops resolving.

  2. **The description is verbatim.** The complete scraped body is written
     through unchanged — never summarized, paraphrased, trimmed to a budget,
     or re-wrapped — and that is asserted against the rendered text.

Rendering itself is delegated to `orchestrator/universal_job.render_inbox_markdown`,
the existing standardized renderer, so intake files look exactly like every
other normalized posting in this workspace.

Writes are atomic: a temp file in the destination directory, fsync-free
`os.replace` onto the final name, so a reader never sees a half-written file
and a crash can never leave a truncated posting for the batch to pick up.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "orchestrator"))
import universal_job  # noqa: E402

sys.path.insert(0, str(ROOT / "tools" / "resume" / "v86"))
import normalize_cover_letter_identity as company_identity  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dedup  # noqa: E402

# Deliberately the pipeline's OWN header regex, not a second copy of it: the
# whole point of the check below is to agree with what
# `normalize_cover_letter_identity` will do to this file later.
COMPANY_HEADER_RE = company_identity._COMPANY_HEADER_RE


class JobFileError(Exception):
    """Raised instead of writing a file the pipeline would misread."""


@dataclass
class WrittenJobFile:
    path: Path
    job_key: str
    bytes_written: int
    description_length: int
    company_header_lines: int
    company_resolved: Optional[str]


def render(record, job_key: str) -> str:
    """Render the normalized Markdown for one extracted `JobRecord`."""
    frontmatter = record.frontmatter()
    canonical_source_url = frontmatter.get("source_url")
    canonical_application_url = (
        universal_job.canonicalize_url(frontmatter.get("application_url"))
        if frontmatter.get("application_url") else None
    )
    return universal_job.render_inbox_markdown(
        frontmatter, record.description, job_key,
        canonical_source_url, canonical_application_url,
    )


def company_header_lines(text: str) -> list:
    return [m.group(1).strip().strip("*").strip().strip("\"'").strip()
            for m in COMPANY_HEADER_RE.finditer(text)]


def validate(text: str, record, job_key: str) -> WrittenJobFile:
    """Every check that must pass before the bytes hit the disk. Returns the
    (path-less) summary the writer fills in."""
    try:
        encoded = text.encode("utf-8")
    except UnicodeEncodeError as exc:  # lone surrogates from a bad scrape
        raise JobFileError(f"rendered posting is not encodable as UTF-8: {exc}") from exc

    headers = company_header_lines(text)
    if not headers:
        raise JobFileError("rendered posting has no 'Company:' header line")
    resolved = company_identity.extract_company_from_posting(text)
    if resolved is None:
        raise JobFileError(
            f"rendered posting has {len(headers)} disagreeing 'Company:' header lines "
            f"({sorted(set(headers))}); the pipeline would resolve no company at all"
        )
    expected = (record.company or "").strip()
    if expected and resolved != expected:
        raise JobFileError(
            f"rendered posting resolves company {resolved!r}, not the scraped {expected!r}"
        )

    body = (record.description or "").strip()
    if body and body not in text:
        raise JobFileError("the scraped job description was not preserved verbatim")

    return WrittenJobFile(
        path=Path(), job_key=job_key, bytes_written=len(encoded),
        description_length=len(record.description or ""),
        company_header_lines=len(headers), company_resolved=resolved,
    )


def job_file_path(inbox_dir, job_key: str) -> Path:
    return Path(inbox_dir) / f"{job_key}.md"


def write(record, job_key: str, inbox_dir, overwrite: bool = False) -> WrittenJobFile:
    """Validate, then atomically write `<inbox_dir>/<job_key>.md`."""
    inbox_dir = Path(inbox_dir)
    path = job_file_path(inbox_dir, job_key)
    dedup.assert_filename_agreement(job_key, path)

    text = render(record, job_key)
    summary = validate(text, record, job_key)

    if path.exists() and not overwrite:
        raise JobFileError(f"{path} already exists; intake never silently overwrites a posting")

    inbox_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{job_key}-", suffix=".md.tmp", dir=str(inbox_dir))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()

    summary.path = path
    return summary
