#!/usr/bin/env python3
"""
jobadd.py — manually ingest one standardized job-posting Markdown file into
the rolling queue, without waiting for the daemon's queue/incoming/ watcher.

Usage:
    python3.13 orchestrator/jobadd.py path/to/job.md
    python3.13 orchestrator/jobadd.py --urgent path/to/job.md

This is the source-agnostic replacement for the old LinkedIn-URL-only
jobadd.py: there is no more Apify scraping step to enqueue a job for, so
"adding a job" now means handing this script (or queue/incoming/, which the
running orchestrator watches automatically) a file that already matches the
universal job schema in universal_job.py — the same file format an external
or interactive Claude conversation would drop into queue/incoming/.

This performs the exact same validate -> canonicalize -> dedup -> ingest
sequence rolling_queue.py's IncomingWatcher runs on every file it detects
(see process_incoming_file()) — it does not scrape, does not call Claude,
and does not require the orchestrator daemon to be running. --urgent bumps
priority on an existing (or brand-new) row so a worker claims it first.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rolling_queue import (  # noqa: E402
    DB_PATH, JOBS_INBOX, QUEUE_INCOMING_DIR, QUEUE_REJECTED_DIR, RAW_DIR, TRACKER_XLSX_PATH,
    init_db, process_incoming_file,
)
from rolling_queue import _with_conn, now_iso  # noqa: E402

# process_incoming_file()/init_db() each open, use, and close their own
# short-lived SQLite connection internally (see "SQLite thread-safety
# design" in rolling_queue.py / README.md) — jobadd.py is a separate process
# from rolling_queue.py, so it never holds a connection open across calls.


def _mark_urgent(job_key: str) -> None:
    def op(conn):
        conn.execute(
            "UPDATE jobs SET priority = 1, updated_at = ? WHERE job_key = ? AND status != 'READY_TO_APPLY'",
            (now_iso(), job_key),
        )
        conn.commit()
    _with_conn(DB_PATH, op)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manually ingest one standardized job-posting Markdown file into the rolling queue."
    )
    parser.add_argument("path", help="path to a queue/incoming-style Markdown file (see universal_job.py schema)")
    parser.add_argument("--urgent", action="store_true", help="process ahead of normal-priority jobs")
    args = parser.parse_args()

    source_path = Path(args.path).resolve()
    if not source_path.is_file():
        print(f"ERROR: no such file: {source_path}", file=sys.stderr)
        raise SystemExit(1)

    # process_incoming_file() always consumes its input (moves it to
    # jobs/inbox/ or queue/rejected/, or deletes it on a confirmed
    # duplicate) — exactly right for the watched queue/incoming/ directory,
    # but the user's own file passed on the command line here must never be
    # silently deleted. Ingest a copy dropped into queue/incoming/ instead,
    # so the original the user pointed at is left untouched either way.
    QUEUE_INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    working_copy = QUEUE_INCOMING_DIR / source_path.name
    shutil.copy2(source_path, working_copy)

    init_db(DB_PATH)
    result = process_incoming_file(
        working_copy, db_path=DB_PATH, inbox_dir=JOBS_INBOX, raw_dir=RAW_DIR,
        rejected_dir=QUEUE_REJECTED_DIR, tracker_path=TRACKER_XLSX_PATH,
    )

    outcome = result["result"]
    if outcome == "INGESTED":
        job_key = result["job_key"]
        if args.urgent:
            _mark_urgent(job_key)
        print(f"INGESTED job_key={job_key} priority={'urgent' if args.urgent else 'normal'}")
    elif outcome == "DUPLICATE":
        job_key = result["job_key"]
        if args.urgent:
            _mark_urgent(job_key)
        print(f"ALREADY_QUEUED job_key={job_key}" + (" (priority bumped to urgent)" if args.urgent else ""))
    else:  # REJECTED
        print(f"REJECTED: {path.name} -> {result['path']}", file=sys.stderr)
        for problem in result.get("problems", []):
            print(f"  - {problem}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
