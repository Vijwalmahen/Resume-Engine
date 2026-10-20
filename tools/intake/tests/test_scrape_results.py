#!/usr/bin/env python3
"""
Acceptance 3 + 10: complete / blocked / truncated / malformed actor results,
the explicit actor contract, and secret hygiene.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import FAKE_TOKEN, block_network, synthetic_description  # noqa: E402

block_network()

import apify_client_shim as shim  # noqa: E402
import completeness  # noqa: E402
import extractors  # noqa: E402

FIELD_MAP = extractors.ResultFieldMap(
    description="descriptionText", company="companyName", title="positionName",
    location="jobLocation", employment_type="employmentKind", workplace_type="workplaceKind",
    date_posted="postedAt", compensation="salaryText", application_url="applyUrl",
)

CANONICAL = "https://www.linkedin.com/jobs/view/ml-intern-at-example-4012345678"


def actor_config(**kw):
    defaults = dict(actor_id="synthetic/offline-fixture-actor",
                    input_mapping={"urls": "startUrls"}, max_cost_usd=0.25, timeout_seconds=60)
    defaults.update(kw)
    return shim.ActorConfig(**defaults)


def complete_result(**overrides):
    raw = {
        "companyName": "Example AI Labs",
        "positionName": "Machine Learning Engineer Intern",
        "jobLocation": "Example City, ST",
        "employmentKind": "Internship",
        "workplaceKind": "Hybrid",
        "postedAt": "2026-10-01",
        "salaryText": "$30/hr",
        "applyUrl": "https://careers.example-ai-labs.invalid/apply/4012345678",
        "descriptionText": synthetic_description(),
        "scrapedFrom": "synthetic fixture",
    }
    raw.update(overrides)
    return raw


def extract(raw):
    return extractors.extract(raw, FIELD_MAP, source="linkedin",
                              source_job_id="4012345678", canonical_url=CANONICAL)


class TestExtraction(unittest.TestCase):
    def test_complete_result_maps_every_declared_field(self):
        record = extract(complete_result())
        self.assertEqual(record.company, "Example AI Labs")
        self.assertEqual(record.title, "Machine Learning Engineer Intern")
        self.assertEqual(record.location, "Example City, ST")
        self.assertEqual(record.employment_type, "Internship")
        self.assertEqual(record.workplace_type, "Hybrid")
        self.assertEqual(record.date_posted, "2026-10-01")
        self.assertEqual(record.compensation, "$30/hr")
        self.assertTrue(record.application_url.endswith("/apply/4012345678"))
        self.assertEqual(record.canonical_url, CANONICAL)
        self.assertEqual(record.description_length, len(synthetic_description()))

    def test_undeclared_actor_fields_are_reported_not_guessed(self):
        record = extract(complete_result())
        self.assertIn("scrapedFrom", record.unmapped_fields)

    def test_absent_fields_stay_absent(self):
        raw = complete_result()
        del raw["salaryText"]
        del raw["applyUrl"]
        record = extract(raw)
        self.assertIsNone(record.compensation)
        self.assertIsNone(record.application_url)

    def test_a_non_mapping_result_is_malformed(self):
        for raw in (None, [], "a string", 42):
            with self.subTest(raw=raw):
                with self.assertRaises(extractors.ExtractionError):
                    extract(raw)

    def test_a_structured_value_where_text_was_declared_is_malformed(self):
        with self.assertRaises(extractors.ExtractionError):
            extract(complete_result(descriptionText={"html": "<p>...</p>"}))

    def test_description_field_must_be_declared_explicitly(self):
        with self.assertRaises(TypeError):
            extractors.ResultFieldMap()  # description has no default


class TestCompleteness(unittest.TestCase):
    def test_complete_posting_is_accepted(self):
        verdict = completeness.assess(extract(complete_result()))
        self.assertEqual(verdict.status, completeness.COMPLETE)
        self.assertTrue(verdict.accepted)

    def test_teaser_is_incomplete_description(self):
        raw = complete_result(descriptionText="We are hiring an intern. Apply now.")
        verdict = completeness.assess(extract(raw))
        self.assertEqual(verdict.status, completeness.INCOMPLETE_DESCRIPTION)
        self.assertFalse(verdict.accepted)
        self.assertIn("400-char minimum", verdict.reason)

    def test_minimum_length_is_configurable_and_defaults_to_400(self):
        self.assertEqual(completeness.DEFAULT_MIN_DESCRIPTION_CHARS, 400)
        raw = complete_result(descriptionText=synthetic_description(chars=420))
        record = extract(raw)
        self.assertEqual(completeness.assess(record).status, completeness.COMPLETE)
        self.assertEqual(
            completeness.assess(record, min_description_chars=1000).status,
            completeness.INCOMPLETE_DESCRIPTION,
        )

    def test_whitespace_padding_cannot_clear_the_floor(self):
        padded = "Short teaser." + ("\n" * 2000)
        verdict = completeness.assess(extract(complete_result(descriptionText=padded)))
        self.assertEqual(verdict.status, completeness.INCOMPLETE_DESCRIPTION)

    def test_login_captcha_blocked_and_error_pages_are_blocked(self):
        pages = {
            "login": "Sign in to view this job. " + synthetic_description(),
            "captcha": "Please complete the captcha to continue. " + synthetic_description(),
            "blocked": "Access denied. Your request has been blocked. " + synthetic_description(),
            "ratelimit": "429 Too Many Requests - rate limit exceeded. " + synthetic_description(),
            "error": "404 Not Found - page not found. " + synthetic_description(),
        }
        for name, text in pages.items():
            with self.subTest(page=name):
                verdict = completeness.assess(extract(complete_result(descriptionText=text)))
                self.assertEqual(verdict.status, completeness.BLOCKED)
                self.assertTrue(verdict.marker)

    def test_missing_required_fields_are_malformed(self):
        raw = complete_result()
        del raw["companyName"]
        verdict = completeness.assess(extract(raw))
        self.assertEqual(verdict.status, completeness.MALFORMED_RESULT)
        self.assertIn("company", verdict.reason)

    def test_no_result_at_all_is_scrape_failed(self):
        self.assertEqual(completeness.assess(None).status, completeness.SCRAPE_FAILED)

    def test_every_status_is_distinct(self):
        self.assertEqual(len(set(completeness.STATUSES)), 5)


class TestActorContractAndSecrets(unittest.TestCase):
    def test_no_actor_id_or_field_name_is_shipped(self):
        empty = shim.ActorConfig()
        self.assertIsNone(empty.actor_id)
        self.assertEqual(empty.input_mapping, {})
        self.assertEqual(empty.static_input, {})
        self.assertIsNone(empty.max_cost_usd)
        self.assertIsNone(empty.timeout_seconds)
        self.assertEqual(shim.SUPPORTED_SOURCES_PLACEHOLDER, set())

    def test_incomplete_config_fails_closed_naming_every_gap(self):
        with self.assertRaises(shim.ConfigurationError) as caught:
            shim.validate_live_config(shim.ActorConfig(), ["linkedin"], env={})
        message = str(caught.exception)
        for expected in ("actor_id", "input_mapping['urls']", "max_cost_usd",
                         "timeout_seconds", "APIFY_TOKEN", "linkedin"):
            self.assertIn(expected, message)

    def test_a_fully_configured_live_run_still_refuses_to_execute(self):
        env = {shim.TOKEN_ENV_VAR: FAKE_TOKEN}
        with self.assertRaises(shim.ConfigurationError):
            # source support is part of the missing contract, so even a
            # complete ActorConfig plus a token is not enough.
            shim.validate_live_config(actor_config(), ["linkedin"], env=env)
        with self.assertRaises(shim.LiveExecutionDisabled):
            shim.LiveTransport().run("synthetic/actor", {"startUrls": []}, 30)
        self.assertFalse(shim.LIVE_EXECUTION_ENABLED)

    def test_build_input_uses_the_declared_field_name_only(self):
        payload = actor_config(static_input={"maxItems": 1}).build_input([CANONICAL])
        self.assertEqual(payload, {"maxItems": 1, "startUrls": [CANONICAL]})

    def test_cost_ceiling_is_enforced_before_any_run(self):
        transport = shim.FakeTransport({CANONICAL: complete_result()})
        client = shim.ApifyClientShim(transport, actor_config(max_cost_usd=0.01),
                                      cost_estimator=lambda urls: 5.0)
        with self.assertRaises(shim.ConfigurationError):
            client.run_urls([CANONICAL])
        self.assertEqual(transport.calls, [], "no run may start once the ceiling is exceeded")

    def test_timeout_is_passed_through_explicitly(self):
        transport = shim.FakeTransport({CANONICAL: complete_result()})
        client = shim.ApifyClientShim(transport, actor_config())
        client.run_urls([CANONICAL], timeout_seconds=17)
        self.assertEqual(transport.calls[0]["timeout_seconds"], 17)

    def test_transport_failure_and_empty_result_are_reported_per_url(self):
        other = "https://www.indeed.com/viewjob?jk=abc1234567890def"
        transport = shim.FakeTransport({CANONICAL: complete_result()})
        client = shim.ApifyClientShim(transport, actor_config())
        outcome = client.run_urls([CANONICAL, other])
        self.assertIn(CANONICAL, outcome.results_by_url)
        self.assertIn(other, outcome.failed_urls)

        failing = shim.FakeTransport({CANONICAL: RuntimeError("actor timed out")})
        outcome = shim.ApifyClientShim(failing, actor_config()).run_urls([CANONICAL])
        self.assertIn(CANONICAL, outcome.failed_urls)
        self.assertEqual(outcome.results_by_url, {})

    def test_token_value_never_appears_in_a_repr_or_an_attribute(self):
        env = {shim.TOKEN_ENV_VAR: FAKE_TOKEN}
        self.assertTrue(shim.token_present(env))
        self.assertFalse(shim.token_present({}))

        config = actor_config()
        client = shim.ApifyClientShim(shim.FakeTransport(), config)
        for text in (repr(config), str(config), repr(client), str(client)):
            self.assertNotIn(FAKE_TOKEN, text)
        for holder in (config, client, client.transport):
            for value in vars(holder).values():
                self.assertNotIn(FAKE_TOKEN, str(value))

    def test_no_token_is_read_unless_a_live_run_is_attempted(self):
        import os

        self.assertNotIn(shim.TOKEN_ENV_VAR, os.environ,
                         "the offline suite must never need a real token in the environment")

    def test_token_is_not_written_into_the_source(self):
        source = (Path(__file__).resolve().parents[1] / "apify_client_shim.py").read_text(encoding="utf-8")
        self.assertNotIn(FAKE_TOKEN, source)
        self.assertIn("never printed", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
