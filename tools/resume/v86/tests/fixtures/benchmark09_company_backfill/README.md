# `benchmark09_company_backfill` fixture

An immutable, synthetic set of per-job artifacts in which the
cover-letter payload's `company` is empty, plus the two postings needed
to show that backfilling it is deterministic and never invents a name.

## Files

| File | What it holds |
| --- | --- |
| `job_posting.md` | A fictional "Retrieval Engineer" posting with a `**Company:** Searchlight Technologies` header. |
| `job_posting_no_company_header.md` | The same role with no structured company metadata header anywhere — deliberately, so there is nothing to parse. |
| `cover_letter_payload.json` | The cover-letter payload for that role, with `"company": ""`. |
| `jd_requirements.json` | The extracted job requirements for the job. |
| `resume_payload.json` | The generated resume payload for the job. |
| `v8_strategy_status.json` | The worker's own status record, including the `selected_project_bound` value. |

## What it proves

`test_v86_8_stabilization.py` copies the four JSON artifacts into a
temporary job directory and runs
`tools/resume/v86/validate_strategy_contract_v86.py` over them three
times:

1. **Unchanged** — validation fails with exactly
   `cover_letter_payload.company empty`, reproducing the original
   failure.
2. **After backfill from `job_posting.md`** — the company resolves to
   `Searchlight Technologies` and validation passes.
3. **After backfill from `job_posting_no_company_header.md`** — backfill
   is a no-op, and the unweakened validator fails exactly as before.

The third case is the point: the fix adds a deterministic parse, not a
guess. When there is no header to read, the job fails rather than
shipping a fabricated employer name.

Every company, location, and requirement here is invented.

## Related documentation

- [`../README.md`](../README.md) — the other fixtures.
- [`../../README.md`](../../README.md) — the suites that read this one.
- [`../../../../../../config/v8/README.md`](../../../../../../config/v8/README.md) — the contract shapes being validated.
