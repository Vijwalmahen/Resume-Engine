#!/usr/bin/env python3
"""
Acceptance 5: one posting is recognized as the same posting whether it is
already an Intake row, an inbox file, a consumed `batch_done` file, or a
completed `output/<job_key>/` directory — and whichever URL spelling it
arrives as.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import TempDirTestCase, block_network, synthetic_description  # noqa: E402

block_network()

import dedup  # noqa: E402
import extractors  # noqa: E402
import url_classifier as uc  # noqa: E402
import write_job_file  # noqa: E402

JOB_KEY = "linkedin-4012345678"
CANONICAL = "https://www.linkedin.com/jobs/view/ml-intern-at-example-4012345678"
SAME_POSTING_URLS = [
    "https://www.linkedin.com/jobs/view/ml-intern-at-example-4012345678/",
    "https://www.linkedin.com/jobs/view/ml-intern-at-example-4012345678?trk=public_jobs&utm_source=x",
    "https://www.linkedin.com/jobs/search/?currentJobId=4012345678",
]


def record(job_key=JOB_KEY, canonical=CANONICAL):
    return extractors.JobRecord(
        source="linkedin", source_job_id=job_key.split("-")[-1], canonical_url=canonical,
        description=synthetic_description(), company="Example AI Labs", title="ML Intern",
    )


class TestDedupSources(TempDirTestCase):
    def test_an_intake_row_blocks_a_second_write(self):
        rows = [{"Job Key": JOB_KEY, "Canonical URL": CANONICAL,
                 "Normalized File": f"jobs/batch_inbox/{JOB_KEY}.md"}]
        index = dedup.build_index(intake_rows=rows, inbox_dir=self.inbox,
                                  done_dir=self.done, output_dir=self.output)
        decision = index.check(JOB_KEY, CANONICAL)
        self.assertTrue(decision.is_duplicate)
        self.assertEqual(decision.status, dedup.DUPLICATE_INTAKE)
        self.assertEqual(decision.matched_where, "Intake worksheet")

    def test_an_inbox_file_blocks_a_second_write(self):
        write_job_file.write(record(), JOB_KEY, self.inbox)
        index = dedup.build_index(inbox_dir=self.inbox, done_dir=self.done, output_dir=self.output)
        decision = index.check(JOB_KEY, CANONICAL)
        self.assertEqual(decision.status, dedup.DUPLICATE_INBOX)

    def test_a_consumed_batch_done_file_blocks_a_second_write(self):
        write_job_file.write(record(), JOB_KEY, self.done)
        index = dedup.build_index(inbox_dir=self.inbox, done_dir=self.done, output_dir=self.output)
        decision = index.check(JOB_KEY, CANONICAL)
        self.assertEqual(decision.status, dedup.DUPLICATE_DONE)

    def test_a_completed_output_directory_blocks_a_second_write(self):
        (self.output / JOB_KEY).mkdir(parents=True)
        index = dedup.build_index(inbox_dir=self.inbox, done_dir=self.done, output_dir=self.output)
        decision = index.check(JOB_KEY, CANONICAL)
        self.assertEqual(decision.status, dedup.DUPLICATE_COMPLETED)

    def test_a_row_without_a_normalized_file_does_not_block_a_retry(self):
        # A BLOCKED/failed scrape is recorded, not treated as "already have
        # it" -- see dedup.build_index.
        rows = [{"Job Key": JOB_KEY, "Canonical URL": CANONICAL, "Intake Result": "REJECTED",
                 "Error Code": "BLOCKED", "Normalized File": ""}]
        index = dedup.build_index(intake_rows=rows, inbox_dir=self.inbox,
                                  done_dir=self.done, output_dir=self.output)
        self.assertEqual(index.check(JOB_KEY, CANONICAL).status, dedup.NEW)

    def test_an_unrelated_posting_is_new(self):
        write_job_file.write(record(), JOB_KEY, self.inbox)
        index = dedup.build_index(inbox_dir=self.inbox, done_dir=self.done, output_dir=self.output)
        other = uc.classify("https://boards.greenhouse.io/examplecorp/jobs/7654321")
        self.assertEqual(index.check(other.job_key, other.canonical_url).status, dedup.NEW)


class TestDedupByUrlSpelling(TempDirTestCase):
    def test_every_spelling_of_one_posting_matches(self):
        write_job_file.write(record(), JOB_KEY, self.inbox)
        index = dedup.build_index(inbox_dir=self.inbox, done_dir=self.done, output_dir=self.output)
        for url in SAME_POSTING_URLS:
            with self.subTest(url=url):
                classification = uc.classify(url)
                self.assertEqual(classification.job_key, JOB_KEY)
                self.assertTrue(index.check(classification.job_key, classification.canonical_url).is_duplicate)

    def test_a_url_match_works_even_when_the_key_differs(self):
        # A row whose key came from somewhere else still matches on the
        # canonical URL, so one posting cannot be written twice under two
        # different names.
        rows = [{"Job Key": "legacy-import-1", "Canonical URL": CANONICAL,
                 "Normalized File": "jobs/batch_inbox/legacy-import-1.md"}]
        index = dedup.build_index(intake_rows=rows, inbox_dir=self.inbox)
        decision = index.check(JOB_KEY, CANONICAL)
        self.assertTrue(decision.is_duplicate)
        self.assertEqual(decision.matched_on, "canonical_url")
        self.assertEqual(decision.matched_key, "legacy-import-1")

    def test_two_identical_urls_in_one_run_write_once(self):
        index = dedup.build_index(inbox_dir=self.inbox)
        first = uc.classify(SAME_POSTING_URLS[0])
        self.assertFalse(index.check(first.job_key, first.canonical_url).is_duplicate)
        index.add_pending(first.job_key, first.canonical_url)
        second = uc.classify(SAME_POSTING_URLS[1])
        self.assertTrue(index.check(second.job_key, second.canonical_url).is_duplicate)


class TestIndexRobustness(TempDirTestCase):
    def test_an_unreadable_inbox_file_still_counts_as_existing(self):
        (self.inbox / f"{JOB_KEY}.md").write_bytes(b"\xff\xfe not valid utf-8 \x00")
        index = dedup.build_index(inbox_dir=self.inbox)
        self.assertTrue(index.check(JOB_KEY).is_duplicate)

    def test_a_missing_directory_is_not_an_error(self):
        index = dedup.build_index(inbox_dir=self.tmp / "nope", done_dir=self.tmp / "also-nope",
                                  output_dir=self.tmp / "still-nope")
        self.assertEqual(index.check(JOB_KEY).status, dedup.NEW)

    def test_the_job_key_line_wins_over_the_filename(self):
        written = write_job_file.write(record(), JOB_KEY, self.inbox)
        written.path.rename(self.inbox / "renamed-by-hand.md")
        index = dedup.build_index(inbox_dir=self.inbox)
        self.assertTrue(index.check(JOB_KEY, CANONICAL).is_duplicate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
