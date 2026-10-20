# `scripts/`

Shell wrappers for running the pipeline the way a real run should be run.

## Files

| File | What it does | How the pipeline uses it |
| --- | --- | --- |
| `run_batch_with_cleaning.sh` | Four gated steps: (1) `tools/resume/v8/runtime_context_guard.py check` must report a valid runtime context; (2) the watermark-cleaning service must be healthy, or the script starts a local one from `WATERMARKS_REMOVER_DIR` (default `tools/watermarks-remover`) on Python 3.10 or newer; (3) it runs `tools/resume/v86/run_batch.py` against `jobs/batch_inbox` → `jobs/batch_done`; (4) it checks every job folder created during the run for `watermark_finalization.json` with `"overall_status": "FINALIZED"`. | The recommended entrypoint for a real batch, because `run_batch.py` on its own silently skips finalization when the service is down. The wrapper stops the run instead, and exits `2` if any job was not finalized. |

Usage and environment:

```bash
bash scripts/run_batch_with_cleaning.sh [extra run_batch.py flags]
```

- `WATERMARKS_REMOVER_DIR` — local checkout of the cleaning service
  (default `tools/watermarks-remover`, which is git-ignored).
- `WATERMARKS_SERVICE_URL` — default `http://127.0.0.1:8765`. The script
  refuses to start a service for a non-local URL.

The script passes any extra arguments through to `run_batch.py` and
stops the service again only if it started it itself.

## Where this sits in the flow

Batch run. It wraps the batch runner and adds the pre-run and post-run
gates around it.

## Private or git-ignored

The script expects a local checkout of the external watermark-cleaning
service, which is **not vendored here** and whose default location
(`tools/watermarks-remover/`) is git-ignored. You supply it yourself and
check its own README and license. Everything the script reads or writes
at run time — `jobs/batch_inbox/`, `jobs/batch_done/`, `output/` — is
git-ignored as well.

## Related documentation

- [`../docs/GETTING_STARTED.md`](../docs/GETTING_STARTED.md) — the "Watermark cleaning" section covers service setup and the exact checks.
- [`../README.md`](../README.md) — "Finalization and watermark cleaning".
- [`../tools/resume/v86/README.md`](../tools/resume/v86/README.md) — the batch runner this script invokes.
