#!/usr/bin/env python3
"""
Intake CLI: URLs in, normalized postings and Intake rows out.

    # classify + dedup only; no network, no writes of any kind
    python3 tools/intake/run_intake.py --dry-run --urls-file urls.txt

    # a real run needs an explicit actor contract, ceiling and timeout
    python3 tools/intake/run_intake.py \
        --urls-file urls.txt --actor-config config/intake/actor.json \
        --max-cost-usd 0.50 --timeout-seconds 120

What this never does
--------------------
  - It never submits an application. Nothing in this package posts anything
    anywhere; the only output is a Markdown file and a spreadsheet row.
  - `--dry-run` performs no network call and makes no persistent change: no
    workbook is opened for writing, no file is created, no lock is taken. It
    reports the classification, the duplicate decision, and what it *would*
    do.
  - A live run refuses to start until the actor id, the actor input mapping,
    the result field mapping, `APIFY_TOKEN`, source support, a cost ceiling
    and a timeout are ALL explicitly configured (see `apify_client_shim.py`).
    None of those has a default here, and the actor is still unidentified, so
    in this version a live run always stops with a clear configuration error.

Every per-URL outcome is independent: one blocked, failed, malformed or
duplicate posting never changes what happens to the others, and each gets its
own Intake row.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools" / "resume" / "v86"))

from common import now_iso  # noqa: E402

import apify_client_shim as shim  # noqa: E402
import completeness  # noqa: E402
import dedup  # noqa: E402
import extractors  # noqa: E402
import intake_tracker  # noqa: E402
import url_classifier  # noqa: E402
import write_job_file  # noqa: E402

# Stated as code so it can be asserted by a test, not just promised in prose.
SUBMITS_APPLICATIONS = False

DEFAULT_TRACKER = ROOT / "tracking" / "job_application_tracker.xlsx"
DEFAULT_INBOX = ROOT / "jobs" / "batch_inbox"
DEFAULT_DONE = ROOT / "jobs" / "batch_done"
DEFAULT_OUTPUT = ROOT / "output"

# Intake Result vocabulary
WRITTEN = "WRITTEN"
REJECTED = "REJECTED"
DUPLICATE = "DUPLICATE"
DRY_RUN = "DRY_RUN"

# Scrape Status vocabulary
NOT_ATTEMPTED = "NOT_ATTEMPTED"
SCRAPED = "SCRAPED"
SCRAPE_FAILED = "SCRAPE_FAILED"
SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


@dataclass
class UrlOutcome:
    """One URL's independent result. Carries no job-description text."""

    raw_url: str
    status: str                      # the Intake Result
    job_key: Optional[str] = None
    source: Optional[str] = None
    url_status: Optional[str] = None
    scrape_status: str = NOT_ATTEMPTED
    completeness_status: Optional[str] = None
    duplicate_decision: str = dedup.NEW
    description_length: int = 0
    normalized_file: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    intended_action: Optional[str] = None

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


@dataclass
class IntakeReport:
    dry_run: bool
    outcomes: list = field(default_factory=list)
    tracker: Optional[dict] = None
    charged_usd: float = 0.0

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "urls_inspected": len(self.outcomes),
            "written": sum(1 for o in self.outcomes if o.status == WRITTEN),
            "duplicates": sum(1 for o in self.outcomes if o.status == DUPLICATE),
            "rejected": sum(1 for o in self.outcomes if o.status == REJECTED),
            "charged_usd": round(self.charged_usd, 6),
            "tracker": self.tracker,
            "outcomes": [o.as_dict() for o in self.outcomes],
        }


def load_actor_config(path):
    """Read an explicit actor contract from JSON. Every field is the operator's
    to supply — this function invents nothing."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    config = shim.ActorConfig(
        actor_id=data.get("actor_id"),
        input_mapping=data.get("input_mapping") or {},
        static_input=data.get("static_input") or {},
        max_cost_usd=data.get("max_cost_usd"),
        timeout_seconds=data.get("timeout_seconds"),
    )
    mapping = data.get("result_mapping") or {}
    if not mapping.get("description"):
        raise shim.ConfigurationError(
            "actor config is missing result_mapping.description — the actor's own field name "
            "for the FULL job description. It is never guessed."
        )
    field_map = extractors.ResultFieldMap(**mapping)
    return config, field_map


def _row_for(outcome: UrlOutcome, record, classification, timestamp: str) -> dict:
    row = {
        "Job Key": outcome.job_key,
        "Source": outcome.source or "",
        "Source Job ID": (classification.source_job_id or "") if classification else "",
        "Canonical URL": (classification.canonical_url or "") if classification else outcome.raw_url,
        "URL Status": outcome.url_status or "",
        "Scrape Status": outcome.scrape_status,
        "Completeness Status": outcome.completeness_status or "",
        "Description Length": outcome.description_length,
        "Duplicate Decision": outcome.duplicate_decision,
        "Normalized File": outcome.normalized_file or "",
        "Intake Result": outcome.status,
        "Error Code": outcome.error_code or "",
        "Error Detail": outcome.error_detail or "",
        "First Seen At": timestamp,
        "Last Intake At": timestamp,
    }
    if record is not None:
        row.update({
            "Application URL": record.application_url or "",
            "Company": record.company or "",
            "Role": record.title or "",
            "Location": record.location or "",
            "Workplace Type": record.workplace_type or "",
            "Employment Type": record.employment_type or "",
            "Date Posted": record.date_posted or "",
            "Compensation": record.compensation or "",
        })
    return row


def _identity_row_key(classification, raw_url: str) -> Optional[str]:
    """Unidentifiable URLs still deserve an Intake row (every inspected
    posting gets one), but they must not borrow a posting-shaped key. They get
    a clearly-marked `unidentified-` key derived from the canonical URL."""
    if classification and classification.job_key:
        return classification.job_key
    import universal_job  # already on sys.path via url_classifier

    basis = (classification.canonical_url if classification else None) or raw_url
    return f"unidentified-{universal_job.sha256_short(basis)}"


def run(urls, *, tracker_path=DEFAULT_TRACKER, inbox_dir=DEFAULT_INBOX,
        done_dir=DEFAULT_DONE, output_dir=DEFAULT_OUTPUT,
        transport=None, actor_config=None, field_map=None,
        dry_run: bool = False, min_description_chars: int = completeness.DEFAULT_MIN_DESCRIPTION_CHARS,
        max_cost_usd: Optional[float] = None, timeout_seconds: Optional[int] = None,
        env: Optional[dict] = None) -> IntakeReport:
    """Classify, dedup, (optionally) scrape, normalize and record every URL."""
    urls = [u for u in (s.strip() for s in urls) if u]
    report = IntakeReport(dry_run=dry_run)
    timestamp = now_iso()

    existing_rows = intake_tracker.read_intake_rows(tracker_path) if Path(tracker_path).exists() else []
    index = dedup.build_index(intake_rows=existing_rows, inbox_dir=inbox_dir,
                              done_dir=done_dir, output_dir=output_dir)

    client = None
    if not dry_run:
        if transport is None:
            # No transport was injected: the only remaining option is the live
            # one, which refuses until the actor contract is configured.
            transport = shim.LiveTransport()
        client = shim.ApifyClientShim(transport, actor_config or shim.ActorConfig())

    rows, outcomes = [], []
    for raw_url in urls:
        classification = url_classifier.classify(raw_url)
        outcome = UrlOutcome(
            raw_url=raw_url, status=REJECTED, source=classification.source,
            url_status=classification.status,
        )
        record = None

        try:
            job_key = dedup.require_stable_identity(classification)
        except dedup.IdentityError as exc:
            outcome.error_code = f"URL_{classification.status}"
            outcome.error_detail = str(exc)
            outcome.intended_action = "skip: no stable identity"
            outcome.job_key = _identity_row_key(classification, raw_url)
            outcomes.append(outcome)
            rows.append(_row_for(outcome, None, classification, timestamp))
            continue

        outcome.job_key = job_key
        decision = index.check(job_key, classification.canonical_url)
        outcome.duplicate_decision = decision.status

        if decision.is_duplicate:
            outcome.status = DUPLICATE
            outcome.scrape_status = SKIPPED_DUPLICATE
            outcome.error_code = decision.status
            outcome.error_detail = (
                f"already known as {decision.matched_key} in {decision.matched_where} "
                f"(matched on {decision.matched_on})"
            )
            outcome.intended_action = "skip: duplicate"
            outcomes.append(outcome)
            rows.append(_row_for(outcome, None, classification, timestamp))
            continue

        if dry_run:
            outcome.status = DRY_RUN
            outcome.intended_action = (
                f"would scrape {classification.canonical_url} and write "
                f"{Path(inbox_dir).name}/{job_key}.md"
            )
            outcomes.append(outcome)
            index.add_pending(job_key, classification.canonical_url)
            continue

        if field_map is None:
            raise shim.ConfigurationError(
                "a result field mapping is required before any scrape can be interpreted "
                "(extractors.ResultFieldMap); it is never guessed"
            )

        attempt = client.run_urls(
            [classification.canonical_url], sources=[classification.source], env=env,
            max_cost_usd=max_cost_usd, timeout_seconds=timeout_seconds,
        )
        report.charged_usd += attempt.charged_usd
        raw = attempt.results_by_url.get(classification.canonical_url)

        if raw is None:
            outcome.scrape_status = SCRAPE_FAILED
            outcome.completeness_status = completeness.SCRAPE_FAILED
            outcome.error_code = completeness.SCRAPE_FAILED
            outcome.error_detail = attempt.failed_urls.get(classification.canonical_url, "no result")
            outcomes.append(outcome)
            rows.append(_row_for(outcome, None, classification, timestamp))
            continue

        outcome.scrape_status = SCRAPED
        try:
            record = extractors.extract(
                raw, field_map, source=classification.source,
                source_job_id=classification.source_job_id,
                canonical_url=classification.canonical_url, scraped_at=timestamp,
            )
        except extractors.ExtractionError as exc:
            outcome.completeness_status = completeness.MALFORMED_RESULT
            outcome.error_code = completeness.MALFORMED_RESULT
            outcome.error_detail = str(exc)
            outcomes.append(outcome)
            rows.append(_row_for(outcome, None, classification, timestamp))
            continue

        verdict = completeness.assess(record, min_description_chars)
        outcome.completeness_status = verdict.status
        outcome.description_length = verdict.description_length

        if not verdict.accepted:
            outcome.error_code = verdict.status
            outcome.error_detail = verdict.reason
            outcomes.append(outcome)
            rows.append(_row_for(outcome, record, classification, timestamp))
            continue

        try:
            written = write_job_file.write(record, job_key, inbox_dir)
        except (write_job_file.JobFileError, dedup.IdentityError, OSError) as exc:
            outcome.error_code = "NORMALIZATION_FAILED"
            outcome.error_detail = f"{type(exc).__name__}: {exc}"
            outcomes.append(outcome)
            rows.append(_row_for(outcome, record, classification, timestamp))
            continue

        try:
            relative = str(written.path.relative_to(ROOT))
        except ValueError:
            relative = str(written.path)

        outcome.status = WRITTEN
        outcome.normalized_file = relative
        outcome.intended_action = "queued for the V8.6 batch"
        index.add_pending(job_key, classification.canonical_url)
        outcomes.append(outcome)
        rows.append(_row_for(outcome, record, classification, timestamp))

    report.outcomes = outcomes
    if rows and not dry_run:
        report.tracker = intake_tracker.upsert_rows(tracker_path, rows)
    return report


def _read_urls(args) -> list:
    urls = list(args.urls or [])
    if args.urls_file:
        text = Path(args.urls_file).read_text(encoding="utf-8")
        urls.extend(
            line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    return urls


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline-first job intake (never submits an application).")
    parser.add_argument("urls", nargs="*", help="job URLs")
    parser.add_argument("--urls-file", help="file with one job URL per line ('#' comments allowed)")
    parser.add_argument("--dry-run", action="store_true",
                        help="classify and dedup only: no network call, no file or workbook change")
    parser.add_argument("--max-cost-usd", type=float, help="explicit ceiling for this run")
    parser.add_argument("--timeout-seconds", type=int, help="explicit per-request/run timeout")
    parser.add_argument("--actor-config", help="JSON file with the actor id, input mapping and result mapping")
    parser.add_argument("--min-description-chars", type=int,
                        default=completeness.DEFAULT_MIN_DESCRIPTION_CHARS)
    parser.add_argument("--tracker", default=str(DEFAULT_TRACKER))
    parser.add_argument("--inbox-dir", default=str(DEFAULT_INBOX))
    parser.add_argument("--done-dir", default=str(DEFAULT_DONE))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args(argv)

    urls = _read_urls(args)
    if not urls:
        parser.error("no URLs given (pass them as arguments or via --urls-file)")

    actor_config, field_map = (None, None)
    if args.actor_config:
        actor_config, field_map = load_actor_config(args.actor_config)

    try:
        report = run(
            urls, tracker_path=Path(args.tracker), inbox_dir=Path(args.inbox_dir),
            done_dir=Path(args.done_dir), output_dir=Path(args.output_dir),
            actor_config=actor_config, field_map=field_map, dry_run=args.dry_run,
            min_description_chars=args.min_description_chars,
            max_cost_usd=args.max_cost_usd, timeout_seconds=args.timeout_seconds,
        )
    except (shim.ConfigurationError, shim.LiveExecutionDisabled, intake_tracker.IntakeSchemaError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
