# `orchestrator/fixtures/`

Offline sample input for the rolling-queue orchestrator's self-test.

## Contents

| Path | What it holds |
| --- | --- |
| [`incoming/`](incoming/README.md) | Six standardized job Markdown files — one per ingestion source, one cross-source duplicate, and one deliberately malformed file. |

## Where this sits in the flow

Validation. `orchestrator/rolling_queue.py` resolves this directory as
`FIXTURES_DIR` and copies named files out of it during
`--self-test`, which runs entirely against temporary directories and a
temporary SQLite database and makes no network call.

```bash
python3 orchestrator/rolling_queue.py --self-test
```

## Private or git-ignored

Nothing here is git-ignored, and nothing here is real. The orchestrator's
own runtime state (`queue/`, `.work/`) is git-ignored; real postings go
to `jobs/batch_inbox/`, whose contents are git-ignored too.

## Related documentation

- [`../README.md`](../README.md) — the orchestrator, its universal job schema, and the offline self-test.
- [`incoming/README.md`](incoming/README.md) — what each sample file exercises.
