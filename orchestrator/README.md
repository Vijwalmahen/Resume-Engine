# Rolling-Queue Orchestrator

An outer orchestration layer, **outside** the frozen V8.5 production resume
pipeline (`config/active_resume_pipeline.json`). It never edits any frozen
production file. It drives each job through the existing production
pipeline via independent `claude -p` processes, releasing each job the
moment it's ready instead of waiting for a whole batch.

**Status: implemented and offline-tested only. No real job has been
processed end-to-end with this yet.** See "One-job test" at the bottom
before running it for real.

## Source-agnostic ingestion architecture (current)

This orchestrator **no longer scrapes anything itself**. There is no Apify
dependency, no `APIFY_TOKEN`, and no network call anywhere in the ingestion
path. Scraping/extraction is now an external or interactive Claude
conversation's job: given ANY job URL (LinkedIn, Indeed, Handshake,
Greenhouse, Workday, Lever, Ashby, Wellfound, Built In, Dice, a direct
employer careers page, ...), that conversation extracts the exact posting
and writes one standardized Markdown file into `queue/incoming/`.

```
ANY JOB URL
  -> external/interactive Claude extracts the exact job
  -> standardized Markdown file (frontmatter + full JD body)
  -> queue/incoming/
  -> rolling_queue.py's IncomingWatcher detects the file
  -> validate frontmatter schema
  -> canonicalize URLs, compute the universal job key
  -> deduplicate against every already-known job
  -> copy into jobs/inbox/ (standardized layout) + SQLite row (status NEW)
  -> Claude Code worker (unchanged frozen V8.5 pipeline)
  -> finalization -> READY_TO_APPLY
  -> tracking/job_application_tracker.xlsx updated
```

Malformed files are never silently discarded — they're moved to
`queue/rejected/` with a companion `<name>.error.txt` explaining exactly
what failed validation.

### Universal job schema

See `universal_job.py` for the authoritative implementation. A file in
`queue/incoming/` is Markdown with a flat YAML-style frontmatter block:

```markdown
---
source: greenhouse
source_domain: boards.greenhouse.io
source_job_id: 7654321
requisition_id: null
source_url: https://boards.greenhouse.io/acmecloud/jobs/7654321
application_url: https://boards.greenhouse.io/acmecloud/jobs/7654321#app
company_careers_url: https://acmecloud.com/careers
company: AcmeCloud
role: Applied AI Engineer Co-op
location: New York, NY
work_arrangement: Hybrid
employment_type: Co-op
posted_date: 2026-09-05
closing_date: null
compensation: null
scraped_at: 2026-09-11T10:15:00Z
---

<the complete job description>
```

Required fields: `company`, `role`, `source`, `source_url`. Everything else
may be `null`. The body must contain the actual job description (a short
minimum-length check catches an empty/truncated body). See
`orchestrator/fixtures/incoming/` for real sample files covering LinkedIn,
Indeed, Handshake, and Greenhouse, plus one intentionally malformed sample.

### Universal job key

Computed by `universal_job.compute_job_key()`, in priority order:

1. `normalize(source) + "-" + source_job_id`, when `source_job_id` exists
   (e.g. `linkedin-9900001234`, `indeed-abc123`, `greenhouse-123456`).
2. `normalize(company) + "-" + normalize(requisition_id)`, when
   `requisition_id` exists instead (e.g. `example-corp-ai-engineer-req123`).
3. A stable short SHA256 of the canonical `source_url` + company + role,
   e.g. `job-a82f31c7`, when neither identifier is available.

Job keys are deterministic — ingesting the same file twice, or two files
describing the same job with the same identifying fields, always produces
the same key.

### Deduplication

`find_duplicate_job()` checks, in priority order: (1) exact job key match,
(2) canonical `application_url`, (3) canonical `source_url`, (4)
`source + source_job_id` composite, (5) `company + requisition_id`
composite, (6) normalized `company + role + location` composite. The same
job appearing on both LinkedIn and the employer's own careers site is
never processed twice; `enrich_duplicate_job()` prefers storing the direct
employer `application_url` once one becomes available and keeps a
`known_source_urls` JSON list of every source URL seen for that job.

### Schema migration

`migrate_schema()` (called from `init_db()`) adds every new
source-agnostic column to an existing `jobs` table via
`ALTER TABLE ... ADD COLUMN` — it never drops or rebuilds the table. Any
row from the previous LinkedIn-only flow (identified by `linkedin_job_id`
being set while `source` is still `NULL`) is backfilled with
`source='linkedin'`, `source_domain='linkedin.com'`, `source_job_id`, and
`source_url` so it fits the universal schema without losing any data. This
has already been run once against the real
`.work/rolling_queue/queue.db` — the pre-existing Example Robotics test row
(`li-9900001234`) survived with its `status`/`attempts`/`last_error`
untouched and `source`/`source_job_id`/`source_url` backfilled.

### Tracker workbook

`tracking/job_application_tracker.xlsx` — a **separate** workbook from the
pre-existing `tracking/application_tracker.xlsx`, which this orchestrator
never reads or writes. Maintained by `tracker_xlsx.py`, a **stdlib-only**
reader/writer (no openpyxl/PyYAML dependency — see that module's docstring
for why, and how it safely round-trips a workbook a human has since edited
in real Excel, including cells Excel re-saved with a shared-strings table).

Every column is either orchestrator-owned (refreshed on every sync: Job
Key, Source, Company, Role, pipeline/resume/cover-letter fields, Last
Updated, ...) or human-owned (`Application Status`, `Date Applied`,
`Recruiter / Contact`, `Referral`, `Interview Stage`, `Next Action(-
Date)`, `Notes`, ...). **`sync_tracker_for_job()` never includes a
human-owned header in the fields it writes**, so an automated sync can
never overwrite them. `Application Status` defaults to `NOT_APPLIED` at row
creation and is never set to anything else by any code in this
orchestrator — reaching `READY_TO_APPLY` only means the resume/cover letter
are ready; the user changes `Application Status` to `APPLIED` themselves
after actually submitting. Writes are atomic (temp file + `os.replace()`).

### Status machine

```
NEW ------------------------> GENERATING -> VALIDATING
QUEUED --------------------/                    |         |
                                                 v         v
                                          NEEDS_CORRECTION  FINALIZING -> READY_TO_APPLY
                                                 |
                                                 v
                                          CORRECTING -> VALIDATING (loop, bounded)
```

`NEW` and `QUEUED` both go straight to `GENERATING` — ingestion is the new
"scraping": by the time a job reaches `NEW` it already has a complete
`jobs/inbox/*.md` file, so there is no separate scraping stage to enter.
`SCRAPING`/`SCRAPED` remain defined only as legacy/history values (a
pre-migration row could theoretically be stuck there); `recover_after_
restart()` still safely drains any such row back to `NEW`, but no code path
in this file ever assigns those statuses to a job going forward.

Any active stage can end in `FAILED`. `FAILED` only leaves that state via
an explicit manual requeue (`transition(..., QUEUED)`); the orchestrator
never auto-retries a failed job on its own. `READY_TO_APPLY` is terminal —
it is never re-entered by any code path, including crash recovery.

## Layout

```
orchestrator/
    rolling_queue.py    # main daemon: incoming watcher + worker pool + CLI + self-test
    universal_job.py    # source-agnostic schema, canonicalization, job key, dedup, inbox rendering
    tracker_xlsx.py      # stdlib-only tracking/job_application_tracker.xlsx reader/writer
    jobadd.py            # CLI: manually ingest one queue/incoming-style Markdown file
    worker_prompt.txt    # prompt template fed to each `claude -p` stage call
    fixtures/incoming/   # offline sample Markdown files (LinkedIn/Indeed/Handshake/Greenhouse/malformed)
    README.md            # this file

queue/
    incoming/             # drop standardized job Markdown files here; watched continuously
    rejected/              # malformed files land here, each with a companion .error.txt
    job_links.txt          # legacy LinkedIn-only-flow file; no longer read by anything

.work/rolling_queue/      # all runtime state — NOT part of the frozen pipeline
    queue.db               # SQLite: jobs, status_history, claude_metrics, watcher_state
    logs/
    workers/
    ready/                 # <job_key>.json written the instant a job is READY_TO_APPLY
    raw/                   # raw snapshot of the original queue/incoming/ file per job
    frozen_v85_checksums_before.json / _after*.json
    concurrency_audit.json
```

## Preferred interpreter

Plain `python3` on this machine resolves to Apple's Command Line Tools
Python 3.9.6 (`/usr/bin/python3`). Also installed, already on `PATH`:

| interpreter | path | `sqlite3.threadsafety` |
|---|---|---|
| `python3.9` (Apple CLT, what plain `python3` resolves to) | `/usr/bin/python3` | 1 |
| `python3.12` (Homebrew) | `/opt/homebrew/bin/python3.12` | 3 |
| `python3.13` | `~/.local/bin/python3.13` | 3 |

**Recommended: `python3.13`.** It's the newest available, it's already
first on `PATH`, and — like `python3.12` — its bundled SQLite reports
`threadsafety=3` (DB-API "connections and cursors may be shared across
threads"), vs. `1` for Apple's bundled Python 3.9.6. This module never
relies on that (see "SQLite thread-safety design" below — no connection is
ever shared across threads regardless of interpreter), but running under
the interpreter with the weaker guarantee is one less variable.
`rolling_queue.py`, `universal_job.py`, and `tracker_xlsx.py` are all
deliberately **stdlib-only** (no PyYAML, no openpyxl — see
`tracker_xlsx.py`'s docstring for why), so unlike the old design there is
no dependency that only exists under one particular interpreter; any of
`python3`/`python3.12`/`python3.13` will run this correctly.

## Two ways to enqueue a job

Drop a standardized Markdown file into `queue/incoming/` while the
orchestrator is running — it's watched continuously (kqueue on macOS, ~0.5s
polling fallback elsewhere) — or run it through synchronously without the
daemon:

```bash
python3.13 orchestrator/jobadd.py path/to/job.md
python3.13 orchestrator/jobadd.py --urgent path/to/job.md
```

`jobadd.py` copies the file into `queue/incoming/` and runs the exact same
validate/canonicalize/dedup/ingest sequence the watcher does (never touches
or deletes the file you pointed it at) — both paths write to the same
SQLite queue and dedupe identically, so it doesn't matter which one you
use.

## Running it

```bash
python3.13 orchestrator/rolling_queue.py --workers 4   # 1-5, default 4
```

A `.work/rolling_queue/rolling_queue.pid` lock file prevents a second
instance from starting against the same `queue.db`.

## Locking strategy (see `.work/rolling_queue/concurrency_audit.json`)

Before enabling `--workers > 1`, every V8.5 stage was audited for shared
writable resources. Summary of what actually required action:

- `tools/resume/v8/run_local_resume_pipeline.py` unconditionally rebuilds
  the shared `master/runtime/ats_evidence/` directory on every run and
  writes a **hardcoded** (non-per-job) results file at
  `.work/v8/local_pipeline_results.json`. Two concurrent invocations race.
- `tools/resume/v8/collect_corrections.py` defaults to the same fixed
  paths.
- `tracking/application_tracker.xlsx` has **no lock or atomic-write
  mechanism anywhere in the codebase** — it's a full read-modify-save of an
  xlsx (zip) file. Two concurrent writers silently lose one side's update.
  (`tracking/job_application_tracker.xlsx`, the new workbook this
  orchestrator owns, does have atomic write/replace semantics — see
  `tracker_xlsx.py` — but is still only ever touched from inside
  `CLAUDE_STAGE_LOCK`-serialized code paths, so there is still never more
  than one writer at a time.)
- `~/Documents/ResumeRenderer/.word-conversion.lock` is **already**
  self-serialized via `fcntl.flock` in both `render_resume.py` and
  `word_to_pdf.py` — no extra lock needed there, but it's a hard bottleneck
  (drives one real MS Word instance) regardless of `--workers`.
- The watermark service at `http://127.0.0.1:8765` is a stateless,
  `ThreadingHTTPServer`-based singleton — safe for concurrent *requests*,
  but must never have a second *instance* started.

None of those files can be safely instrumented from outside without editing
frozen production code, which is forbidden. So the orchestrator's single
lock, `CLAUDE_STAGE_LOCK` (a process-wide `threading.Lock` in
`rolling_queue.py`), is held for the **entire duration of every `claude -p`
subprocess call** for the `GENERATING`, `CORRECTING`, and `FINALIZING`
stages, across every worker thread.

Practical effect: `queue/incoming/` ingestion (validation, dedup, copy into
`jobs/inbox/`) runs with full `--workers` concurrency (it has no shared
Claude/Word/tracker state at all — it's pure local file/SQLite work). The
actual Claude-driven pipeline work runs one job at a time no matter how
many workers are configured — `--workers` mainly parallelizes
ingestion/backlog-draining so jobs are already `QUEUED` and ready the
instant the previous job's generation finishes, rather than adding real
generation-stage parallelism. This is a deliberate, conservative trade:
correctness over throughput, because the shared resources above have no
technical lock we're allowed to add.

Frozen V8.5 files were **not** modified to add any of this. Layer B
(statistical text watermarking) remains disabled, unchanged from existing
policy.

## SQLite thread-safety design (V1)

Deliberately conservative — correctness over DB-level concurrency:

- **No `sqlite3.Connection` or `Cursor` is ever shared across threads.**
  Every DB operation (`transition`, `claim_next_job`, `fail_job`,
  `recover_after_restart`, `insert_new_job`, `find_duplicate_job`,
  `fetch_status_rows_snapshot`, `init_db`, ...) opens its own connection
  via `_open_conn()`, does its work, and closes it before returning — see
  `_with_conn()`. `check_same_thread=True` (the actual default) is used on
  purpose, not left off by oversight: if a future change ever did try to
  reuse a connection across threads, Python would raise immediately instead
  of silently allowing it.
- **A process-wide `threading.RLock` (`SQLITE_LOCK`)** additionally
  serializes every one of those operations, so at most one thread is ever
  inside SQLite at all in this process. Reentrant, so a function that owns
  a connection can safely call another locked helper on the same thread.
- Worker threads (`worker_loop`) and the file watcher (`IncomingWatcher`)
  hold **no** connection of their own across loop iterations — they call
  the above per-operation functions each pass, nothing more.
- Transactions are kept short (one logical operation per connection),
  WAL mode is enabled, and `busy_timeout` is set to 30s on every
  connection.

## SQLite concurrency stress test

```bash
python3.13 orchestrator/rolling_queue.py --stress-test                  # 5 repetitions, 6 threads x 8 jobs (defaults)
python3.13 orchestrator/rolling_queue.py --stress-test --repetitions 10 --threads 8 --jobs-per-thread 12
```

Each repetition runs entirely against a fresh temp SQLite DB under the
system tempdir (never `.work/rolling_queue/queue.db`) and never calls
Claude or makes a network call. Phase 1: N threads each insert their own
unique synthetic jobs concurrently (insert contention, exercised through
the same connection-per-operation + `SQLITE_LOCK` path real ingestion
uses — not through the full validate/dedup/tracker pipeline, which has its
own dedicated self-test coverage instead). Phase 2: N threads race
`claim_next_job()` against the shared pool and drive whatever they claim
through to `READY_TO_APPLY` via `transition()` (the same compare-and-swap
claim path real workers use). Each repetition is launched as its own
**subprocess** (`--stress-test-worker`) specifically so a genuine SIGSEGV in
SQLite is caught as a subprocess killed by a signal (reported as `CRASHED`)
instead of taking down the whole test run. After each repetition, the run
asserts: no uncaught exception in any thread, no duplicate/lost `job_key`s,
and every inserted job reached `READY_TO_APPLY`. A smaller, in-process
version of this same test (`sqlite_concurrency_stress_smoke`) is also part
of `--self-test`.

## Recovery after restart

On startup, `recover_after_restart()`:
- Leaves `READY_TO_APPLY` jobs untouched — they never rerun.
- Leaves `QUEUED` jobs as-is — they're simply picked up again.
- Resets any job found in an active in-flight status (`GENERATING`,
  `VALIDATING`, `CORRECTING`, `FINALIZING`, or the legacy-only `SCRAPING`)
  back to its nearest safe re-entry point (`NEW`/`QUEUED`), clearing
  `worker_id`/`claimed_at`, since a fresh process start means no worker
  thread is actually still running that job.

## Per-job Claude metrics

Every `claude -p ... --output-format json` call's `total_cost_usd`,
`duration_ms`, `usage`, `modelUsage`, `session_id`, and success/failure are
recorded in the `claude_metrics` table, one row per stage attempt.

## Offline self-test

```bash
python3.13 orchestrator/rolling_queue.py --self-test
```

Runs entirely against a temp SQLite DB and temp directories under the
system tempdir — it never touches `jobs/inbox/`, `output/`, `tracking/`,
`queue/incoming/`, `queue/rejected/`, or any frozen file, and asserts this
explicitly at the end (directory-listing and checksum diff, including the
real `tracking/job_application_tracker.xlsx` if one already exists). It
does **not** call Claude and makes no network call anywhere. It covers:
SQLite init and schema migration (including a synthetic pre-migration
LinkedIn-only row that must survive with its data intact), universal
schema parsing/validation, deterministic job-key computation, ingesting all
four `fixtures/incoming/` sample sources, cross-source deduplication
(preferring the direct employer application URL and preserving every known
source URL), a malformed file landing in `queue/rejected/` with a
companion `.error.txt`, tracker row creation with `Application Status`
defaulting to `NOT_APPLIED`, tracker syncs never touching human-owned
columns, `READY_TO_APPLY` populating `Resume Path`/`Cover Letter Path`/
`Resume Created At` without ever touching `Application Status`, an explicit
assertion that no network call happens during ingestion, state-machine
transitions (including rejecting an illegal transition), crash recovery,
ready-marker generation, `IncomingWatcher` detection latency (kqueue path
on macOS, or the polling fallback), a SQLite concurrency stress smoke
check, read-only `--status`, `.env` auto-loading without ever logging
secret values, and mocked Claude worker success/failure persistence.

## `--status`

```bash
python3.13 orchestrator/rolling_queue.py --status
```

Prints a read-only table (`job_key`, `source`, `company`, `role`, `status`,
`attempts`, `worker_id`, `last_error`, `created_at`, `updated_at`). Opens
`queue.db` read-only (SQLite URI `mode=ro` when supported), never creates
the DB if absent, never runs schema migration, and never starts workers or
mutates state.

## One-job test (do this only when you're ready to process a real job)

```bash
python3.13 orchestrator/jobadd.py path/to/a/real/job.md
python3.13 orchestrator/rolling_queue.py --workers 1
```

Watch for the `READY TO APPLY` banner, or inspect
`.work/rolling_queue/ready/<job_key>.json` once it appears. Use
`--workers 1` for the first real run so any pipeline-integration issue is
easy to attribute to this one job.

## Migrating from the LinkedIn-only flow

This patch changed the ingestion architecture but not the frozen V8.5
pipeline or the worker contract (`jobs/inbox/*.md` is still the contract —
see `worker_prompt.txt`). What changed and why:

- `queue/job_links.txt` and the Apify-based `JobLinksWatcher`/
  `do_scrape_stage` are gone. Nothing in this orchestrator makes a network
  call or requires `APIFY_TOKEN` anymore. `queue/job_links.txt` itself was
  left in place untouched (historical evidence), just no longer read.
- The `jobs` table gained the universal source-agnostic columns (see
  "Schema migration" above) via a non-destructive `ALTER TABLE` migration.
  The pre-existing Example Robotics test row (`li-9900001234`) was preserved
  and backfilled, not recreated.
- `add_job()`/`extract_linkedin_job_id()`/`normalize_linkedin_url()`/
  `make_job_key()` (LinkedIn-URL-specific) were replaced by
  `universal_job.py` + `process_incoming_file()`/`find_duplicate_job()`.
  `jobadd.py` now ingests a Markdown file instead of a raw URL.
- `NEW`/`QUEUED` now transition straight to `GENERATING` (see "Status
  machine" above) since ingestion already has the complete job posting —
  there is no separate scraping stage left to enter.
- `tracking/job_application_tracker.xlsx` is new; the pre-existing
  `tracking/application_tracker.xlsx` is untouched by this orchestrator.

Frozen V8.5 integrity was re-verified after this change: all 78 files
listed in `.work/rolling_queue/frozen_v85_checksums_before.json` still
match — see
`.work/rolling_queue/frozen_v85_checksums_after_source_agnostic_migration.json`
(0 mismatches, 0 missing).
