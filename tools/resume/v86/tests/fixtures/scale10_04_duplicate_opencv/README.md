# `scale10_04_duplicate_opencv` fixture

An immutable, synthetic set of per-job artifacts whose
`jd_requirements.json` lists the same concept term twice, which the
contract validator rejects as a duplicate.

## Files

| File | What it holds |
| --- | --- |
| `jd_requirements.json` | Extracted requirements for a fictional computer-vision role, containing two `OpenCV` concepts that differ only in their flags. |
| `resume_payload.json` | The generated resume payload for the job. |
| `cover_letter_payload.json` | The generated cover-letter payload for the job. |
| `v8_strategy_status.json` | The worker's own status record, including the `selected_project_bound` value. |

## What it proves

`test_v86_reliability_patch.py` first asserts the fixture really does
contain two `OpenCV` concepts, then runs
`tools/resume/v86/validate_strategy_contract_v86.py` over the unmodified
artifacts and asserts it fails with a `duplicate concept term` message —
the original failure mode. It then applies
`normalize_jd_concepts.normalize_jd_requirements()`, checks that the two
entries merged into one and that the `required: true` side won, and runs
the validator again, which now passes.

A second test repeats the sequence through
`deterministic_pipeline.run_job_pipeline`'s own defensive
normalize-then-validate path, stopping before the Word-dependent render
step, to prove the normalization is applied at both call sites rather
than only the primary one. The validator itself is never weakened —
normalization happens upstream of it.

Every company, requirement, and bullet here is invented.

## Related documentation

- [`../README.md`](../README.md) — the other fixtures.
- [`../../README.md`](../../README.md) — the suites that read this one.
