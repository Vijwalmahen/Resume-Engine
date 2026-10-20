#!/usr/bin/env python3
"""
Acceptance 6 + 9 + 11: five synthetic postings whose outcomes are independent,
a consistent status vocabulary, and a `--dry-run` that touches nothing.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from support import (FAKE_TOKEN, TempDirTestCase, block_network,  # noqa: E402
                     synthetic_description)

block_network()

import apify_client_shim as shim  # noqa: E402
import completeness  # noqa: E402
import dedup  # noqa: E402
import extractors  # noqa: E402
import intake_tracker  # noqa: E402
import run_intake  # noqa: E402
import deterministic_pipeline as detp  # noqa: E402

FIELD_MAP = extractors.ResultFieldMap(
    description="descriptionText", company="companyName", title="positionName",
    location="jobLocation", employment_type="employmentKind",
)

ACTOR = shim.ActorConfig(actor_id="synthetic/offline-fixture-actor",
                         input_mapping={"urls": "startUrls"},
                         max_cost_usd=0.25, timeout_seconds=60)

# Five synthetic postings, one per intended outcome.
GOOD = "https://www.linkedin.com/jobs/view/ml-intern-at-example-4012345678/"
BLOCKED = "https://boards.greenhouse.io/examplecorp/jobs/7654321"
TEASER = "https://jobs.lever.co/examplecorp/1b2c3d4e-5f60-4711-8899-aabbccddeeff"
MALFORMED = "https://jobs.ashbyhq.com/examplecorp/9f8e7d6c-5b4a-4321-9876-0a1b2c3d4e5f"
FAILED = "https://www.indeed.com/viewjob?jk=abc1234567890def"
UNSUPPORTED = "https://careers.examplecorp.com/openings/ml-engineer"

ALL_URLS = [GOOD, BLOCKED, TEASER, MALFORMED, FAILED, UNSUPPORTED]


def canonical(url):
    import url_classifier

    return url_classifier.classify(url).canonical_url


def posting(company="Example AI Labs", description=None, **extra):
    raw = {
        "companyName": company, "positionName": "Machine Learning Engineer Intern",
        "jobLocation": "Example City, ST", "employmentKind": "Internship",
        "descriptionText": description if description is not None else synthetic_description(company),
    }
    raw.update(extra)
    return raw


def transport():
    return shim.FakeTransport({
        canonical(GOOD): posting(),
        canonical(BLOCKED): posting(description="Sign in to view this job. " + synthetic_description()),
        canonical(TEASER): posting(description="We are hiring. Apply now."),
        canonical(MALFORMED): posting(descriptionText={"html": "<p>oops</p>"}),
        # FAILED has no entry at all -> the actor returned nothing for it.
    })


class IntakeRunTestCase(TempDirTestCase):
    def run_intake(self, urls, **kw):
        kwargs = dict(
            tracker_path=self.tracker, inbox_dir=self.inbox, done_dir=self.done,
            output_dir=self.output, transport=transport(), actor_config=ACTOR,
            field_map=FIELD_MAP,
        )
        kwargs.update(kw)
        return run_intake.run(urls, **kwargs)

    def outcomes_by_url(self, report):
        return {o.raw_url: o for o in report.outcomes}


class TestPartialFailuresAreIndependent(IntakeRunTestCase):
    def setUp(self):
        super().setUp()
        self.make_tracker()

    def test_each_posting_keeps_its_own_status(self):
        report = self.run_intake(ALL_URLS)
        outcomes = self.outcomes_by_url(report)

        self.assertEqual(outcomes[GOOD].status, run_intake.WRITTEN)
        self.assertEqual(outcomes[GOOD].completeness_status, completeness.COMPLETE)

        self.assertEqual(outcomes[BLOCKED].status, run_intake.REJECTED)
        self.assertEqual(outcomes[BLOCKED].completeness_status, completeness.BLOCKED)

        self.assertEqual(outcomes[TEASER].completeness_status, completeness.INCOMPLETE_DESCRIPTION)
        self.assertEqual(outcomes[MALFORMED].completeness_status, completeness.MALFORMED_RESULT)
        self.assertEqual(outcomes[FAILED].completeness_status, completeness.SCRAPE_FAILED)
        self.assertEqual(outcomes[FAILED].scrape_status, run_intake.SCRAPE_FAILED)

        self.assertEqual(outcomes[UNSUPPORTED].status, run_intake.REJECTED)
        self.assertEqual(outcomes[UNSUPPORTED].scrape_status, run_intake.NOT_ATTEMPTED)

    def test_only_the_complete_posting_becomes_a_file(self):
        self.run_intake(ALL_URLS)
        self.assertEqual([p.name for p in sorted(self.inbox.iterdir())],
                         ["linkedin-4012345678.md"])

    def test_every_inspected_url_gets_an_intake_row(self):
        self.run_intake(ALL_URLS)
        rows = {r["Job Key"]: r for r in intake_tracker.read_intake_rows(self.tracker)}
        self.assertEqual(len(rows), len(ALL_URLS))
        self.assertEqual(rows["linkedin-4012345678"]["Intake Result"], run_intake.WRITTEN)
        self.assertEqual(rows["greenhouse-examplecorp-7654321"]["Error Code"], completeness.BLOCKED)
        self.assertEqual(rows["indeed-abc1234567890def"]["Error Code"], completeness.SCRAPE_FAILED)
        unidentified = [k for k in rows if k.startswith("unidentified-")]
        self.assertEqual(len(unidentified), 1)
        self.assertEqual(rows[unidentified[0]]["Error Code"], "URL_INCOMPLETE")

    def test_description_length_is_recorded_but_not_the_description(self):
        self.run_intake(ALL_URLS)
        rows = {r["Job Key"]: r for r in intake_tracker.read_intake_rows(self.tracker)}
        good = rows["linkedin-4012345678"]
        self.assertEqual(int(good["Description Length"]), len(synthetic_description()))
        for value in good.values():
            self.assertNotIn("production-oriented retrieval", value)

    def test_a_rerun_writes_no_duplicates(self):
        self.run_intake(ALL_URLS)
        report = self.run_intake(ALL_URLS)
        outcomes = self.outcomes_by_url(report)
        self.assertEqual(outcomes[GOOD].status, run_intake.DUPLICATE)
        self.assertIn(outcomes[GOOD].duplicate_decision,
                      (dedup.DUPLICATE_INTAKE, dedup.DUPLICATE_INBOX))
        self.assertEqual([p.name for p in sorted(self.inbox.iterdir())],
                         ["linkedin-4012345678.md"])
        rows = intake_tracker.read_intake_rows(self.tracker)
        self.assertEqual(len(rows), len(ALL_URLS))

    def test_an_unsuccessful_row_does_not_block_a_later_retry(self):
        self.run_intake(ALL_URLS)
        healthy = shim.FakeTransport({canonical(BLOCKED): posting(company="Example Corp")})
        report = self.run_intake([BLOCKED], transport=healthy)
        self.assertEqual(self.outcomes_by_url(report)[BLOCKED].status, run_intake.WRITTEN)
        self.assertTrue((self.inbox / "greenhouse-examplecorp-7654321.md").exists())


class TestStatusVocabulary(unittest.TestCase):
    def test_completeness_statuses_are_the_documented_five(self):
        self.assertEqual(
            set(completeness.STATUSES),
            {"COMPLETE", "BLOCKED", "INCOMPLETE_DESCRIPTION", "MALFORMED_RESULT", "SCRAPE_FAILED"},
        )

    def test_intake_result_vocabulary_is_closed(self):
        self.assertEqual(
            {run_intake.WRITTEN, run_intake.REJECTED, run_intake.DUPLICATE, run_intake.DRY_RUN},
            {"WRITTEN", "REJECTED", "DUPLICATE", "DRY_RUN"},
        )

    def test_duplicate_vocabulary_covers_every_place_a_posting_can_exist(self):
        self.assertEqual(
            set(dedup.DUPLICATE_STATUSES),
            {"DUPLICATE_INTAKE", "DUPLICATE_INBOX", "DUPLICATE_DONE", "DUPLICATE_COMPLETED"},
        )

    def test_pipeline_success_statuses_come_from_the_pipeline_itself(self):
        import sync_results

        self.assertEqual(sync_results.SUCCESS_STATUSES, detp.SUCCESS_STATUSES)
        self.assertIn("RESUME_READY", sync_results.SUCCESS_STATUSES)

    def test_quality_states_match_the_report_generator(self):
        import resume_quality_report
        import sync_results

        self.assertEqual(resume_quality_report.diagnostic_state([], {"total": 0}), "PASS")
        self.assertEqual(
            resume_quality_report.diagnostic_state([], {"high": 1, "total": 1}), "REVIEW")
        self.assertEqual(
            resume_quality_report.diagnostic_state(
                [{"status": "not_evaluated"}], {"total": 0}), "INCOMPLETE")
        self.assertEqual(set(sync_results.QUALITY_STATES), {"PASS", "REVIEW", "INCOMPLETE"})

    def test_intake_never_submits_an_application(self):
        self.assertFalse(run_intake.SUBMITS_APPLICATIONS)
        for name in ("run_intake.py", "write_job_file.py", "intake_tracker.py",
                     "apify_client_shim.py", "sync_results.py"):
            source = (Path(__file__).resolve().parents[1] / name).read_text(encoding="utf-8")
            self.assertNotIn("urllib.request", source)
            self.assertNotIn("import requests", source)


class TestDryRunPurity(IntakeRunTestCase):
    def snapshot(self):
        files = {}
        for base in (self.inbox, self.done, self.output, self.tracking):
            for path in sorted(base.rglob("*")):
                files[str(path.relative_to(self.tmp))] = (
                    path.read_bytes() if path.is_file() else "<dir>"
                )
        return files

    def test_dry_run_changes_nothing_on_disk(self):
        self.make_tracker()
        before = self.snapshot()
        report = self.run_intake(ALL_URLS, dry_run=True)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(report.dry_run)
        self.assertIsNone(report.tracker)
        self.assertEqual(intake_tracker.read_intake_rows(self.tracker), [])

    def test_dry_run_without_a_tracker_creates_no_workbook(self):
        report = self.run_intake(ALL_URLS, dry_run=True)
        self.assertFalse(self.tracker.exists())
        self.assertEqual(len(report.outcomes), len(ALL_URLS))

    def test_dry_run_makes_no_network_call_and_no_actor_run(self):
        self.make_tracker()
        fake = transport()
        self.run_intake(ALL_URLS, dry_run=True, transport=fake)
        self.assertEqual(fake.calls, [], "a dry run must not run the actor at all")
        self.assertEqual(self.run_intake(ALL_URLS, dry_run=True).charged_usd, 0.0)

    def test_dry_run_reports_classification_duplicate_and_intent(self):
        self.make_tracker()
        outcomes = self.outcomes_by_url(self.run_intake(ALL_URLS, dry_run=True))
        good = outcomes[GOOD]
        self.assertEqual(good.status, run_intake.DRY_RUN)
        self.assertEqual(good.url_status, "SUPPORTED")
        self.assertEqual(good.duplicate_decision, dedup.NEW)
        self.assertIn("would scrape", good.intended_action)
        self.assertIn("linkedin-4012345678.md", good.intended_action)
        self.assertEqual(outcomes[UNSUPPORTED].status, run_intake.REJECTED)

    def test_dry_run_sees_existing_duplicates(self):
        self.make_tracker()
        self.run_intake([GOOD])
        outcomes = self.outcomes_by_url(self.run_intake(ALL_URLS, dry_run=True))
        self.assertEqual(outcomes[GOOD].status, run_intake.DUPLICATE)

    def test_cli_dry_run_is_pure_and_prints_json(self):
        import contextlib
        import io

        self.make_tracker()
        urls_file = self.tmp / "urls.txt"
        urls_file.write_text("# synthetic\n" + "\n".join(ALL_URLS) + "\n", encoding="utf-8")
        before = self.snapshot()

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = run_intake.main([
                "--dry-run", "--urls-file", str(urls_file),
                "--tracker", str(self.tracker), "--inbox-dir", str(self.inbox),
                "--done-dir", str(self.done), "--output-dir", str(self.output),
            ])

        self.assertEqual(code, 0)
        self.assertEqual(self.snapshot(), before)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["urls_inspected"], len(ALL_URLS))
        self.assertEqual(payload["charged_usd"], 0.0)


class TestLiveRunRefusal(IntakeRunTestCase):
    def test_a_live_run_refuses_without_a_configured_actor(self):
        self.make_tracker()
        with self.assertRaises(shim.ConfigurationError) as caught:
            run_intake.run([GOOD], tracker_path=self.tracker, inbox_dir=self.inbox,
                           done_dir=self.done, output_dir=self.output,
                           field_map=FIELD_MAP, max_cost_usd=1.0, timeout_seconds=30,
                           env={})
        message = str(caught.exception)
        self.assertIn("actor_id", message)
        self.assertIn("APIFY_TOKEN", message)
        self.assertEqual(list(self.inbox.iterdir()), [])

    def test_a_live_run_refuses_even_with_a_token_and_full_config(self):
        self.make_tracker()
        with self.assertRaises((shim.ConfigurationError, shim.LiveExecutionDisabled)):
            run_intake.run([GOOD], tracker_path=self.tracker, inbox_dir=self.inbox,
                           done_dir=self.done, output_dir=self.output,
                           actor_config=ACTOR, field_map=FIELD_MAP,
                           env={shim.TOKEN_ENV_VAR: FAKE_TOKEN})

    def test_a_scrape_without_a_result_mapping_is_refused(self):
        self.make_tracker()
        with self.assertRaises(shim.ConfigurationError):
            self.run_intake([GOOD], field_map=None)

    def test_actor_config_file_requires_a_description_mapping(self):
        path = self.tmp / "actor.json"
        path.write_text(json.dumps({
            "actor_id": "synthetic/offline-fixture-actor",
            "input_mapping": {"urls": "startUrls"},
            "max_cost_usd": 0.25, "timeout_seconds": 60,
            "result_mapping": {"company": "companyName"},
        }), encoding="utf-8")
        with self.assertRaises(shim.ConfigurationError):
            run_intake.load_actor_config(path)

    def test_a_complete_actor_config_file_loads(self):
        path = self.tmp / "actor.json"
        path.write_text(json.dumps({
            "actor_id": "synthetic/offline-fixture-actor",
            "input_mapping": {"urls": "startUrls"},
            "static_input": {"maxItems": 1},
            "max_cost_usd": 0.25, "timeout_seconds": 60,
            "result_mapping": {"description": "descriptionText", "company": "companyName"},
        }), encoding="utf-8")
        config, field_map = run_intake.load_actor_config(path)
        self.assertEqual(config.actor_id, "synthetic/offline-fixture-actor")
        self.assertEqual(field_map.description, "descriptionText")
        self.assertEqual(config.missing_settings(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
