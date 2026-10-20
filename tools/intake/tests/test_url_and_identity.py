#!/usr/bin/env python3
"""
Acceptance 1 + 2: URL classification/canonicalization, and the single
identity shared by job key, filename and tracker key.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import block_network  # noqa: E402

block_network()

import dedup  # noqa: E402
import url_classifier as uc  # noqa: E402
import universal_job  # noqa: E402
from common import job_key_for  # noqa: E402


class TestSupportedPatterns(unittest.TestCase):
    CASES = [
        ("https://www.linkedin.com/jobs/view/ml-engineer-at-example-4012345678/?trk=public_jobs",
         uc.SOURCE_LINKEDIN, "linkedin-4012345678"),
        ("https://www.linkedin.com/jobs/view/4012345678",
         uc.SOURCE_LINKEDIN, "linkedin-4012345678"),
        ("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4012345678&utm_source=share",
         uc.SOURCE_LINKEDIN, "linkedin-4012345678"),
        ("https://boards.greenhouse.io/examplecorp/jobs/7654321",
         uc.SOURCE_GREENHOUSE, "greenhouse-examplecorp-7654321"),
        ("https://job-boards.greenhouse.io/examplecorp/jobs/7654321?gh_src=abc",
         uc.SOURCE_GREENHOUSE, "greenhouse-examplecorp-7654321"),
        ("https://careers.examplecorp.com/openings?gh_jid=5512345",
         uc.SOURCE_GREENHOUSE, "greenhouse-examplecorp-5512345"),
        ("https://jobs.lever.co/examplecorp/1b2c3d4e-5f60-4711-8899-aabbccddeeff",
         uc.SOURCE_LEVER, "lever-examplecorp-1b2c3d4e-5f60-4711-8899-aabbccddeeff"),
        ("https://jobs.lever.co/examplecorp/1b2c3d4e-5f60-4711-8899-aabbccddeeff/apply",
         uc.SOURCE_LEVER, "lever-examplecorp-1b2c3d4e-5f60-4711-8899-aabbccddeeff"),
        ("https://jobs.ashbyhq.com/examplecorp/9f8e7d6c-5b4a-4321-9876-0a1b2c3d4e5f",
         uc.SOURCE_ASHBY, "ashby-examplecorp-9f8e7d6c-5b4a-4321-9876-0a1b2c3d4e5f"),
        ("https://examplecorp.wd5.myworkdayjobs.com/en-US/External/job/Example-City/ML-Engineer_R-12345",
         uc.SOURCE_WORKDAY, "workday-examplecorp-r-12345"),
        ("https://www.indeed.com/viewjob?jk=abc1234567890def&from=serp",
         uc.SOURCE_INDEED, "indeed-abc1234567890def"),
    ]

    def test_every_supported_pattern_classifies_and_keys(self):
        for url, source, job_key in self.CASES:
            with self.subTest(url=url):
                result = uc.classify(url)
                self.assertEqual(result.status, uc.SUPPORTED, result.reason)
                self.assertEqual(result.source, source)
                self.assertEqual(result.job_key, job_key)

    def test_canonicalization_strips_tracking_and_fragments(self):
        noisy = ("https://WWW.LinkedIn.com/jobs/view/ml-engineer-at-example-4012345678/"
                 "?utm_source=newsletter&trk=public&refId=zz#applynow")
        clean = "https://www.linkedin.com/jobs/view/ml-engineer-at-example-4012345678"
        self.assertEqual(uc.classify(noisy).canonical_url, clean)

    def test_two_spellings_of_one_posting_share_a_key(self):
        a = uc.classify("https://www.linkedin.com/jobs/view/ml-engineer-at-example-4012345678/?trk=x")
        b = uc.classify("https://www.linkedin.com/jobs/search/?currentJobId=4012345678")
        self.assertEqual(a.job_key, b.job_key)

    def test_canonicalization_is_the_existing_shared_helper(self):
        url = "https://boards.greenhouse.io/examplecorp/jobs/7654321?utm_medium=email"
        self.assertEqual(uc.classify(url).canonical_url, universal_job.canonicalize_url(url))


class TestIncompleteAndUnsupported(unittest.TestCase):
    def test_known_host_without_a_job_id_is_incomplete(self):
        for url in (
            "https://www.linkedin.com/jobs/view/",
            "https://www.indeed.com/jobs?q=machine+learning",
            "https://boards.greenhouse.io/examplecorp",
            "https://examplecorp.wd5.myworkdayjobs.com/en-US/External",
        ):
            with self.subTest(url=url):
                result = uc.classify(url)
                self.assertEqual(result.status, uc.INCOMPLETE)
                self.assertIsNone(result.job_key)
                self.assertTrue(result.reason)

    def test_direct_employer_page_is_recognized_but_incomplete(self):
        result = uc.classify("https://careers.examplecorp.com/openings/ml-engineer")
        self.assertEqual(result.status, uc.INCOMPLETE)
        self.assertEqual(result.source, uc.SOURCE_EMPLOYER)
        self.assertEqual(result.canonical_url, "https://careers.examplecorp.com/openings/ml-engineer")
        self.assertIn("fails closed", result.reason)

    def test_unknown_or_malformed_input_is_unsupported(self):
        for url in ("", "   ", "not a url", "ftp://example.com/job/1", "javascript:alert(1)", None):
            with self.subTest(url=url):
                self.assertEqual(uc.classify(url).status, uc.UNSUPPORTED)

    def test_classification_never_opens_a_socket(self):
        # block_network() is installed at import time; a classification that
        # reached the network would raise here instead of returning.
        self.assertEqual(
            uc.classify("https://www.linkedin.com/jobs/view/4012345678").status, uc.SUPPORTED
        )


class TestIdentityAgreement(unittest.TestCase):
    def test_job_key_filename_and_pipeline_key_are_one_string(self):
        result = uc.classify("https://boards.greenhouse.io/examplecorp/jobs/7654321")
        job_key = dedup.require_stable_identity(result)
        path = Path("/tmp/batch_inbox") / f"{job_key}.md"
        dedup.assert_filename_agreement(job_key, path)
        # tools/resume/v86/common.job_key_for IS the stem -- no translation.
        self.assertEqual(job_key_for(path), job_key)

    def test_identity_fails_closed_without_a_stable_id(self):
        result = uc.classify("https://careers.examplecorp.com/openings/ml-engineer")
        with self.assertRaises(dedup.IdentityError):
            dedup.require_stable_identity(result)

    def test_mismatched_filename_is_refused(self):
        with self.assertRaises(dedup.IdentityError):
            dedup.assert_filename_agreement("linkedin-4012345678", Path("/tmp/linkedin-9999.md"))

    def test_keys_are_filesystem_safe(self):
        for url, _source, job_key in TestSupportedPatterns.CASES:
            with self.subTest(url=url):
                self.assertRegex(job_key, r"^[a-z0-9][a-z0-9-]*$")
                self.assertNotIn("/", job_key)
                self.assertNotIn("..", job_key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
