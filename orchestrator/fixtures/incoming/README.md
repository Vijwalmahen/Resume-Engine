# `orchestrator/fixtures/incoming/`

Six invented job postings in the orchestrator's standardized
`queue/incoming/` format: flat YAML-style frontmatter followed by the
job description body.

## Files

| File | What it is | What it exercises |
| --- | --- | --- |
| `linkedin_sample.md` | `source: linkedin` with `source_job_id: 4499001122`, and an `application_url` on the employer's own site. | Job-key derivation from `source` + `source_job_id` (`linkedin-4499001122`), and ingestion into the queue. |
| `indeed_sample.md` | `source: indeed` with `source_job_id: abc123`, `application_url: null`, and a `closing_date`. | A non-numeric source job id and a posting with no direct application URL (`indeed-abc123`). |
| `handshake_sample.md` | `source: handshake` with `source_job_id: null` but `requisition_id: REQ-2026-0451`. | The second job-key rule — normalized company plus requisition id (`harbor-logic-req-2026-0451`) — when no source job id exists. |
| `greenhouse_sample.md` | `source: greenhouse` with `source_job_id: 7654321`. | A fourth distinct source, confirming the key is per-source (`greenhouse-7654321`) and never collides with the others. |
| `employer_site_duplicate_of_linkedin.md` | `source: company_website`, describing the same posting as `linkedin_sample.md` with the same `application_url`. | Cross-source de-duplication: it must resolve to the already-known LinkedIn job rather than create a second row. |
| `malformed_missing_role.md` | Frontmatter with no `role`, and a body that is only a few words long. | The rejection path — a file that fails schema validation and the minimum body-length check must land in `queue/rejected/` with a companion `.error.txt`, never be silently discarded. |

Every company, URL, salary, and date in these files is invented.

## Where this sits in the flow

Validation (and, by extension, intake — these are the shape of the files
intake produces). `orchestrator/rolling_queue.py --self-test` copies
specific files out of this directory by name into a sandbox and runs the
real ingestion path over them.

## Private or git-ignored

Nothing here is git-ignored. The live counterpart of this directory is
`queue/incoming/`, which is git-ignored along with the rest of `queue/`.
The self-test never reads or writes `queue/incoming/`, `jobs/inbox/`,
`output/`, or `tracking/` — it asserts that explicitly at the end of the
run.

## Related documentation

- [`../README.md`](../README.md) — this fixtures directory.
- [`../../README.md`](../../README.md) — the universal job schema, job-key rules, de-duplication order, and self-test coverage.
- [`../../universal_job.py`](../../universal_job.py) — the authoritative implementation of that schema.
