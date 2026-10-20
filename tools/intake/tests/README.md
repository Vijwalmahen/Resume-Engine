# `tools/intake/tests/`

The offline acceptance suite for `tools/intake/`. Network access is made
impossible, not merely unused, and every workbook and posting is
synthetic.

## Files

| File | What it covers |
| --- | --- |
| `run_all_tests.py` | The flat, non-recursive runner. Executes each suite exactly once as its own subprocess in the fixed `SUITE_ORDER`, and fails with exit `2` if a `test_*.py` file on disk is not listed in that order. No suite invokes another suite. |
| `support.py` | Shared scaffolding: `block_network()` replaces `socket.socket`, `socket.create_connection`, and `socket.getaddrinfo` with versions that raise, so a test that would reach the network fails loudly; plus synthetic postings, temp-directory test cases, and the single obviously-fake credential string used throughout. |
| `__init__.py` | Makes the directory a package. |
| `test_url_and_identity.py` | Acceptance 1–2: URL classification and canonicalization, and the single identity shared by job key, filename, and tracker key. |
| `test_scrape_results.py` | Acceptance 3 and 10: complete, blocked, truncated, and malformed actor results; the explicit actor contract; and secret hygiene. |
| `test_write_job_file.py` | Acceptance 4: the normalized Markdown file — UTF-8, atomic, stem equal to the job key, exactly one `Company:` header, and a verbatim description the production packet builder reads back unchanged. |
| `test_dedup.py` | Acceptance 5: one posting recognized as the same posting whether it appears as an `Intake` row, an inbox file, a consumed `batch_done` file, or a completed `output/<job_key>/` directory — and whichever URL spelling it arrives as. |
| `test_intake_tracker.py` | Acceptance 7–8 plus the schema guards: the `Intake` worksheet never disturbs `Applications`, the legacy 35-column writer refuses an incompatible workbook, and both writers share one lock. |
| `test_run_intake.py` | Acceptance 6, 9, and 11: five synthetic postings whose outcomes are independent, a consistent status vocabulary, and a `--dry-run` that touches nothing. |
| `test_sync_results.py` | Acceptance 12: post-batch reconciliation updates `Intake` and nothing else. |
| `test_batch_job_setup_failed.py` | Acceptance 13–14: a malformed input file is classified `JOB_SETUP_FAILED` and isolated rather than taking the batch down, and the advisory diagnostics behave exactly as before. It drives the real batch code with only the two substitutions production already exposes as test seams. |

## Where this sits in the flow

Validation.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/tests/run_all_tests.py
```

## Private or git-ignored

Nothing here is git-ignored and nothing here requires a private file.
Every workbook lives in a temporary directory, and the real workbooks
under `tracking/` (which are git-ignored) are never opened. The only
credential string that appears anywhere is the obviously fake
`fake-apify-token-for-test-only`.

## Related documentation

- [`../README.md`](../README.md) — the intake package these suites exercise.
- [`../../../docs/testing.md`](../../../docs/testing.md) — the testing strategy.
- [`../../resume/v86/tests/README.md`](../../resume/v86/tests/README.md) — the batch pipeline's suites, which follow the same flat-runner contract.
