# `benchmark01_company_placeholder` fixture

An immutable, synthetic reconstruction of a cover-letter payload whose
`company` field is non-empty — and therefore contract-valid — but is the
placeholder `"your organization"` rather than the employer the posting
actually names.

## Files

| File | What it holds |
| --- | --- |
| `job_posting.md` | A fictional "Machine Learning Engineer I" posting with a `**Company:** Northstar Commerce` header and the usual location/type/salary metadata. |
| `cover_letter_payload.json` | A cover-letter payload for that role whose `company` is the literal string `"your organization"`. |

## What it proves

`test_v86_8_stabilization.py`'s `TestBenchmark01PlaceholderRegression`
asserts the payload still ships the placeholder (so the fixture has not
drifted), then runs
`normalize_cover_letter_identity.backfill_cover_letter_company()` over it
and the posting and asserts the result is `Northstar Commerce`.

This is the wider defect the company-identity fix closes: a contract
validator that only checks for a non-empty `company` cannot catch a
generic placeholder. `test_v86_8_round2_hardening.py` reads the same
posting as realistic input.

Every company, salary, and location here is invented.

## Related documentation

- [`../README.md`](../README.md) — the other fixtures.
- [`../../README.md`](../../README.md) — the suites that read this one.
