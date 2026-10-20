#!/usr/bin/env python3
"""
Decide whether a scraped posting is actually a posting.

A scrape can come back "successful" and still be useless: a login wall, a
captcha interstitial, a rate-limit page, an error page, or a short teaser
blurb where the full description should be. Each of those is a DIFFERENT
outcome and gets its own status, because the right next step differs (retry
later, change approach, give up on this source):

  COMPLETE                — passed every check; the only status intake writes
                            a `jobs/batch_inbox/*.md` file for.
  BLOCKED                 — a login/captcha/blocked/error page was returned
                            instead of the posting.
  INCOMPLETE_DESCRIPTION  — a real posting, but the description is shorter
                            than the configured minimum (a teaser).
  MALFORMED_RESULT        — the result could not be mapped, or is missing a
                            field the pipeline requires.
  SCRAPE_FAILED           — no result at all (transport error, timeout, actor
                            returned nothing for this URL).

The minimum description length is configurable and defaults to 400 characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

COMPLETE = "COMPLETE"
BLOCKED = "BLOCKED"
INCOMPLETE_DESCRIPTION = "INCOMPLETE_DESCRIPTION"
MALFORMED_RESULT = "MALFORMED_RESULT"
SCRAPE_FAILED = "SCRAPE_FAILED"

STATUSES = (COMPLETE, BLOCKED, INCOMPLETE_DESCRIPTION, MALFORMED_RESULT, SCRAPE_FAILED)

DEFAULT_MIN_DESCRIPTION_CHARS = 400

# Literal marker phrases, matched case-insensitively against the description.
# Deliberately phrases a real job description would not contain verbatim.
BLOCK_MARKERS = (
    "sign in to view this job",
    "sign in to see who",
    "log in to continue",
    "please log in to view",
    "you must be signed in",
    "join linkedin to see",
    "complete the captcha",
    "captcha",
    "are you a human",
    "please verify you are a human",
    "robot check",
    "unusual traffic from your computer",
    "access denied",
    "403 forbidden",
    "429 too many requests",
    "rate limit exceeded",
    "your request has been blocked",
    "enable javascript and cookies to continue",
    "page not found",
    "404 not found",
    "something went wrong",
    "internal server error",
)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class CompletenessResult:
    status: str
    description_length: int = 0
    reason: Optional[str] = None
    marker: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return self.status == COMPLETE


def normalized_length(description: Optional[str]) -> int:
    """Length after collapsing runs of whitespace — so a teaser padded with
    newlines cannot clear the threshold on whitespace alone."""
    if not description:
        return 0
    return len(_WHITESPACE_RE.sub(" ", description).strip())


def find_block_marker(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    lowered = text.lower()
    for marker in BLOCK_MARKERS:
        if marker in lowered:
            return marker
    return None


def assess(record, min_description_chars: int = DEFAULT_MIN_DESCRIPTION_CHARS) -> CompletenessResult:
    """Classify one extracted `JobRecord`. Order matters: a blocked page is
    reported as BLOCKED even though it is also (always) too short, because
    "we were blocked" and "this posting is a teaser" call for different
    responses."""
    if record is None:
        return CompletenessResult(status=SCRAPE_FAILED, reason="no result for this URL")

    description = getattr(record, "description", None)
    length = normalized_length(description)

    missing = [
        name for name in ("company", "title", "canonical_url")
        if not (getattr(record, name, None) or "").strip()
    ]
    if not (description or "").strip():
        missing.append("description")
    if missing:
        return CompletenessResult(
            status=MALFORMED_RESULT, description_length=length,
            reason="missing required field(s): " + ", ".join(missing),
        )

    marker = find_block_marker(description) or find_block_marker(record.title)
    if marker:
        return CompletenessResult(
            status=BLOCKED, description_length=length, marker=marker,
            reason=f"response looks like a login/captcha/blocked/error page (matched {marker!r})",
        )

    if length < int(min_description_chars):
        return CompletenessResult(
            status=INCOMPLETE_DESCRIPTION, description_length=length,
            reason=f"description is {length} chars, below the {int(min_description_chars)}-char minimum",
        )

    return CompletenessResult(status=COMPLETE, description_length=length)
