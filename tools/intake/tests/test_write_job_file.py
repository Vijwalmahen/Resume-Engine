#!/usr/bin/env python3
"""
Acceptance 4: the normalized Markdown file — UTF-8, atomic, stem == job key,
exactly one `Company:` header, and a verbatim description that the production
packet builder reads back unchanged.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import (ROOT, TempDirTestCase, block_network,  # noqa: E402
                     make_synthetic_batch_dir, synthetic_description)

block_network()

import dedup  # noqa: E402
import extractors  # noqa: E402
import normalize_cover_letter_identity as company_identity  # noqa: E402
import write_job_file  # noqa: E402
import build_job_packet  # noqa: E402
from common import job_key_for  # noqa: E402

JOB_KEY = "linkedin-4012345678"
CANONICAL = "https://www.linkedin.com/jobs/view/ml-intern-at-example-4012345678"


def make_record(**overrides):
    fields = dict(
        source="linkedin", source_job_id="4012345678", canonical_url=CANONICAL,
        description=synthetic_description(), company="Example AI Labs",
        title="Machine Learning Engineer Intern", location="Example City, ST",
        employment_type="Internship", workplace_type="Hybrid", date_posted="2026-10-01",
        compensation="$30/hr", application_url="https://careers.example-ai-labs.invalid/apply/1",
        scraped_at="2026-10-18T00:00:00+00:00",
    )
    fields.update(overrides)
    return extractors.JobRecord(**fields)


class TestWriteJobFile(TempDirTestCase):
    def test_writes_utf8_file_named_after_the_job_key(self):
        written = write_job_file.write(make_record(), JOB_KEY, self.inbox)
        self.assertEqual(written.path, self.inbox / f"{JOB_KEY}.md")
        self.assertEqual(written.path.stem, JOB_KEY)
        self.assertEqual(job_key_for(written.path), JOB_KEY)
        raw = written.path.read_bytes()
        self.assertEqual(raw.decode("utf-8"), written.path.read_text(encoding="utf-8"))

    def test_non_ascii_content_survives_as_utf8(self):
        description = synthetic_description() + " Café — Zürich — 日本語 — naïve — ✓"
        written = write_job_file.write(make_record(description=description), JOB_KEY, self.inbox)
        text = written.path.read_text(encoding="utf-8")
        self.assertIn("Café — Zürich — 日本語 — naïve — ✓", text)

    def test_exactly_one_company_header_line(self):
        written = write_job_file.write(make_record(), JOB_KEY, self.inbox)
        text = written.path.read_text(encoding="utf-8")
        matches = [line for line in text.splitlines()
                   if company_identity._COMPANY_HEADER_RE.match(line)]
        self.assertEqual(len(matches), 1, matches)
        self.assertEqual(matches[0], "Company: Example AI Labs")
        self.assertEqual(written.company_header_lines, 1)

    def test_the_pipeline_resolves_the_company_from_the_written_file(self):
        written = write_job_file.write(make_record(), JOB_KEY, self.inbox)
        text = written.path.read_text(encoding="utf-8")
        self.assertEqual(company_identity.extract_company_from_posting(text), "Example AI Labs")
        self.assertEqual(
            company_identity.company_check_status("Example AI Labs", text), "PASS"
        )

    def test_a_conflicting_company_line_in_the_body_is_refused_not_shipped(self):
        description = synthetic_description() + "\nCompany: Some Other Holdings Ltd\n"
        with self.assertRaises(write_job_file.JobFileError) as caught:
            write_job_file.write(make_record(description=description), JOB_KEY, self.inbox)
        self.assertIn("disagreeing", str(caught.exception))
        self.assertEqual(list(self.inbox.glob("*")), [], "nothing may be left behind on a refusal")

    def test_description_is_preserved_verbatim(self):
        description = synthetic_description(chars=2600)
        written = write_job_file.write(make_record(description=description), JOB_KEY, self.inbox)
        text = written.path.read_text(encoding="utf-8")
        self.assertIn(description.strip(), text)
        self.assertEqual(written.description_length, len(description))

    def test_metadata_and_canonical_url_are_present(self):
        text = write_job_file.write(make_record(), JOB_KEY, self.inbox).path.read_text(encoding="utf-8")
        for expected in (
            f"URL: {CANONICAL}",
            "Source: linkedin",
            "Job ID: 4012345678",
            f"Job Key: {JOB_KEY}",
            "Source Domain: www.linkedin.com",
            "Application URL: https://careers.example-ai-labs.invalid/apply/1",
            "Date Posted: 2026-10-01",
            "Compensation: $30/hr",
        ):
            self.assertIn(expected, text)

    def test_write_is_atomic_and_leaves_no_temp_files(self):
        write_job_file.write(make_record(), JOB_KEY, self.inbox)
        leftovers = [p.name for p in self.inbox.iterdir() if p.name != f"{JOB_KEY}.md"]
        self.assertEqual(leftovers, [])

    def test_existing_file_is_never_silently_overwritten(self):
        write_job_file.write(make_record(), JOB_KEY, self.inbox)
        before = (self.inbox / f"{JOB_KEY}.md").read_bytes()
        with self.assertRaises(write_job_file.JobFileError):
            write_job_file.write(make_record(description=synthetic_description(chars=1500)),
                                 JOB_KEY, self.inbox)
        self.assertEqual((self.inbox / f"{JOB_KEY}.md").read_bytes(), before)

    def test_a_mismatched_job_key_is_refused(self):
        with self.assertRaises(dedup.IdentityError):
            write_job_file.write(make_record(), "Not A Safe Key", self.inbox)


class TestProductionPacketBuilderReadsIt(TempDirTestCase):
    """The file intake writes is the file `run_batch.py` feeds to the semantic
    worker, so the real packet builder is exercised here on synthetic data."""

    def test_build_packet_reads_the_intake_file_unchanged(self):
        description = synthetic_description(chars=1800)
        written = write_job_file.write(make_record(description=description), JOB_KEY, self.inbox)
        batch_dir = make_synthetic_batch_dir(self.tmp / "batch")

        packet_path = build_job_packet.build_packet(batch_dir, written.path, JOB_KEY)
        import json
        packet = json.loads(packet_path.read_text(encoding="utf-8"))

        self.assertEqual(packet["job_key"], JOB_KEY)
        self.assertEqual(packet["job_posting_text"], written.path.read_text(encoding="utf-8"))
        self.assertIn(description.strip(), packet["job_posting_text"])
        self.assertEqual(
            company_identity.extract_company_from_posting(packet["job_posting_text"]),
            "Example AI Labs",
        )

    def test_the_backfill_the_pipeline_runs_finds_the_company(self):
        written = write_job_file.write(make_record(), JOB_KEY, self.inbox)
        posting = written.path.read_text(encoding="utf-8")
        payload, filled = company_identity.backfill_cover_letter_company(
            {"company": "your organization"}, posting,
        )
        self.assertEqual(filled, "Example AI Labs")
        self.assertEqual(payload["company"], "Example AI Labs")


if __name__ == "__main__":
    unittest.main(verbosity=2)
