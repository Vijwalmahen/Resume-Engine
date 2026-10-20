# `tools/intake/` — offline job intake and Excel tracking (V1)

The missing front door between a job URL and the V8.6 batch pipeline.

```
URL -> classify -> dedup -> (scrape) -> extract -> completeness -> jobs/batch_inbox/<job_key>.md
                                                               -> Intake worksheet row
                       ... V8.6 run_batch.py ...
                       -> sync_results.py -> Intake row updated with the outcome
```

This version is **offline by construction**. Live scraping is gated behind an
actor contract that does not exist yet, nothing here submits an application,
and every test runs with socket access disabled.

## Modules

| Module | Responsibility |
| --- | --- |
| `url_classifier.py` | Recognize and canonicalize LinkedIn, Greenhouse, Lever, Ashby, Workday, Indeed and direct employer URLs; derive a stable `<source>-<source_job_id>` job key. Returns `SUPPORTED` / `INCOMPLETE` / `UNSUPPORTED`. Never makes a network request. |
| `apify_client_shim.py` | Injectable Apify client: `ActorConfig` (actor id, input mapping, cost ceiling, timeout), `FakeTransport` for offline runs, `LiveTransport` which refuses while live execution is disabled. `APIFY_TOKEN` is only read in the live path and never stored, printed or serialized. |
| `extractors.py` | Map one raw actor result onto a documented, source-agnostic `JobRecord` using an explicit `ResultFieldMap`. No guessed field names, no inferred values, no truncation. |
| `completeness.py` | `COMPLETE` / `BLOCKED` / `INCOMPLETE_DESCRIPTION` / `MALFORMED_RESULT` / `SCRAPE_FAILED`, with a configurable minimum description length (default 400 chars). |
| `dedup.py` | One identity across the Intake sheet, `jobs/batch_inbox/`, `jobs/batch_done/` and completed `output/<job_key>/` directories. Fails closed when no stable id exists. |
| `write_job_file.py` | Atomic, UTF-8, `jobs/batch_inbox/<job_key>.md` with exactly one `Company:` header and a verbatim description. |
| `intake_tracker.py` | The 32-column `Intake` worksheet beside the untouched 23-column `Applications` sheet; shared lock, atomic save, idempotent upsert by Job Key, `Notes` never overwritten. |
| `run_intake.py` | The CLI: `--urls-file`, `--dry-run`, `--max-cost-usd`, `--timeout-seconds`, `--actor-config`, injectable transport. |
| `sync_results.py` | Post-batch reconciliation from `batch_metrics.json` + job-local quality reports into the sync-owned Intake columns only. |

## Identity

`<source>-<source_job_id>` is the job key, the Markdown filename stem, and —
because `tools/resume/v86/common.job_key_for` is literally the file stem — the
key the pipeline writes into the tracker. One string, no translation step. If a
URL yields no stable id the posting is refused, never hashed into a guess.

## The Intake worksheet (32 columns)

```
Job Key | Source | Source Job ID | Canonical URL | Application URL | Company | Role |
Location | Workplace Type | Employment Type | Date Posted | Compensation |
URL Status | Scrape Status | Completeness Status | Description Length |
Duplicate Decision | Normalized File | Intake Result | Pipeline Status | Batch ID |
Final Resume | Final Cover Letter | Quality Status | Quality Artifact Stage |
Quality Report | Error Code | Error Detail | First Seen At | Last Intake At |
Last Sync At | Notes
```

One row per **inspected** posting, including unsuccessful scrapes. `Notes` is
the user's and is never written after row creation. The `Applications` sheet's
headers, order and rows are never modified by anything in this package.

A workbook whose `Applications` or `Intake` headers are not exactly these
produces an `IntakeSchemaError` and is left byte-for-byte unchanged.

## Dry run

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/run_intake.py \
    --dry-run --urls-file /tmp/urls.txt \
    --tracker /tmp/synthetic/tracking/job_application_tracker.xlsx \
    --inbox-dir /tmp/synthetic/batch_inbox \
    --done-dir /tmp/synthetic/batch_done \
    --output-dir /tmp/synthetic/output
```

Prints a JSON report with each URL's classification, duplicate decision and
intended action. No network call, no file, no workbook change, no lock.

## What is still missing for a live run

The historical Apify actor is **unidentified**, so none of the following exists
yet and none of it is guessed here:

1. the actor id;
2. the actor's input field name for target URLs (`ActorConfig.input_mapping["urls"]`)
   and any required static inputs;
3. the actor's result field names (`extractors.ResultFieldMap`), above all the
   one holding the **full** job description rather than a teaser;
4. which sources the actor actually supports
   (`apify_client_shim.SUPPORTED_SOURCES_PLACEHOLDER`, currently empty);
5. a cost ceiling and a timeout;
6. `APIFY_TOKEN` in the environment;
7. flipping `apify_client_shim.LIVE_EXECUTION_ENABLED` and writing the real
   transport against that documented contract.

Until all of those are in place, a live run stops with a `ConfigurationError`
naming every gap, or a `LiveExecutionDisabled`.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/tests/run_all_tests.py
```

Eight suites, all offline: URL classification and identity, actor
contract/extraction/completeness and secret hygiene, the Markdown writer (via
the real `build_job_packet`), deduplication, the Excel writers (including the
legacy 35-column writer's refusal and lock interoperation), the CLI and
dry-run purity, `sync_results`, and `JOB_SETUP_FAILED` isolation in a real
`run_batch.Batch`.
