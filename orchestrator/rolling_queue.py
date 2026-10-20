#!/usr/bin/env python3
"""
rolling_queue.py — outer rolling-queue orchestrator for the job-application
workspace.

This module lives OUTSIDE the frozen V8.5 production resume pipeline
(config/active_resume_pipeline.json). It never edits any frozen production
file. It drives the pipeline by shelling out to independent `claude -p`
processes, one per pipeline stage per job.

SOURCE-AGNOSTIC INGESTION (current architecture): this orchestrator no
longer scrapes anything itself and has no Apify dependency. An external or
interactive Claude conversation extracts a job posting from ANY source
(LinkedIn, Indeed, Handshake, Greenhouse, a direct employer careers page,
...) and writes a standardized Markdown file (see universal_job.py for the
schema) into queue/incoming/. This module watches that directory, validates
and deduplicates each file, and copies the standardized result into
jobs/inbox/ for the unchanged V8.5 worker flow. See orchestrator/README.md
"Source-agnostic ingestion architecture" for the full flow diagram.

Runtime state (SQLite DB, logs, per-worker scratch, ready markers) lives
under .work/rolling_queue/ and is NOT part of the frozen production
snapshot.

Run modes:
    python3.13 orchestrator/rolling_queue.py              further real
        (not to be run for real yet — see README.md)      processing
    python3.13 orchestrator/rolling_queue.py --workers N
    python3.13 orchestrator/rolling_queue.py --status     read-only queue
                                                           table, never
                                                           starts workers
    python3.13 orchestrator/rolling_queue.py --self-test  offline only,
                                                           no network/Claude
    python3.13 orchestrator/rolling_queue.py --stress-test  SQLite
                                                           concurrency stress
                                                           test, temp DB only

Prefer `python3.13` (see README.md "Preferred interpreter") — `python3` on
this machine resolves to Apple's bundled Python 3.9.6, whose sqlite3 module
reports threadsafety=1 rather than the 3 that python3.12/python3.13 report.
Any of them work correctness-wise with this module's connection-per-
operation design, but python3.13 is the recommended one. This module (and
its siblings universal_job.py / tracker_xlsx.py) is deliberately stdlib-only
— no PyYAML, no openpyxl — precisely so it runs identically regardless of
which interpreter is used; see tracker_xlsx.py's docstring for why.

SQLite thread-safety design (V1 — see README.md for the full audit): no
sqlite3.Connection or Cursor is ever shared across threads. Every DB
operation opens its own connection via _open_conn()/_with_conn(), uses it,
and closes it before returning, and a process-wide threading.RLock
(SQLITE_LOCK) additionally serializes all of them. This trades away
DB-level concurrency for correctness, which is the right trade here: every
operation is short, and the actual throughput bottleneck is the serialized
`claude -p` stage calls (CLAUDE_STAGE_LOCK), not SQLite.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import os
import re
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import universal_job  # noqa: E402
import tracker_xlsx  # noqa: E402

# ---------------------------------------------------------------------------
# Paths / constants
# ---------------------------------------------------------------------------

ORCH_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = ORCH_DIR.parent

RUNTIME_DIR = WORKSPACE_ROOT / ".work" / "rolling_queue"
DB_PATH = RUNTIME_DIR / "queue.db"
LOG_DIR = RUNTIME_DIR / "logs"
WORKERS_DIR = RUNTIME_DIR / "workers"
READY_DIR = RUNTIME_DIR / "ready"
RAW_DIR = RUNTIME_DIR / "raw"
TRACKER_LOCK_PATH = RUNTIME_DIR / "tracker.lock"
PID_FILE = RUNTIME_DIR / "rolling_queue.pid"

QUEUE_INCOMING_DIR = WORKSPACE_ROOT / "queue" / "incoming"
QUEUE_REJECTED_DIR = WORKSPACE_ROOT / "queue" / "rejected"
JOBS_INBOX = WORKSPACE_ROOT / "jobs" / "inbox"
ACTIVE_PIPELINE_CONFIG = WORKSPACE_ROOT / "config" / "active_resume_pipeline.json"
WORKER_PROMPT_TEMPLATE = ORCH_DIR / "worker_prompt.txt"
TRACKER_XLSX_PATH = WORKSPACE_ROOT / "tracking" / "job_application_tracker.xlsx"
# Never touched by this orchestrator — a separate, pre-existing workbook.
LEGACY_TRACKER_XLSX_PATH = WORKSPACE_ROOT / "tracking" / "application_tracker.xlsx"

# ---------------------------------------------------------------------------
# Workspace-root .env auto-load (stdlib only; never prints/logs values)
# ---------------------------------------------------------------------------
#
# Must run before anything below reads os.environ (WATERMARKS_SERVICE_URL,
# ...), which is why this sits directly after the path constants and before
# those are used.

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_dotenv(path: Path) -> int:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    A key already present in the real environment always wins — the file
    never overwrites it. Returns the number of variables actually loaded.
    Never prints or logs the values it loads, only (optionally) a count.
    """
    if not path.is_file():
        return 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    loaded = 0
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not _ENV_KEY_RE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key in os.environ:
            continue  # real environment always wins over the .env file
        os.environ[key] = value
        loaded += 1
    return loaded


_dotenv_loaded_count = _load_dotenv(WORKSPACE_ROOT / ".env")
if _dotenv_loaded_count:
    print(
        f"[rolling_queue] loaded {_dotenv_loaded_count} variable(s) from "
        f"{WORKSPACE_ROOT / '.env'} (values not logged)",
        file=sys.stderr,
    )

CLAUDE_TIMEOUT_SECONDS = 60 * 20  # 20 minutes per stage invocation
MAX_CORRECTION_ROUNDS = 3
STALE_CLAIM_SECONDS = 60 * 30  # only relevant if a live process ever crashes mid-claim

WATERMARK_SERVICE_URL = os.environ.get("WATERMARKS_SERVICE_URL", "http://127.0.0.1:8765")

# ---------------------------------------------------------------------------
# Status machine
# ---------------------------------------------------------------------------

NEW = "NEW"
# SCRAPING / SCRAPED are retained only as legacy/history values: no code
# path in this source-agnostic architecture ever assigns them to a job
# anymore (ingestion already has the full job description, so there is
# nothing left to scrape). They remain valid ALLOWED_TRANSITIONS targets
# for exactly one purpose — recover_after_restart() safely draining any row
# a pre-migration run of this orchestrator left mid-scrape.
SCRAPING = "SCRAPING"
SCRAPED = "SCRAPED"
QUEUED = "QUEUED"
GENERATING = "GENERATING"
VALIDATING = "VALIDATING"
NEEDS_CORRECTION = "NEEDS_CORRECTION"
CORRECTING = "CORRECTING"
FINALIZING = "FINALIZING"
READY_TO_APPLY = "READY_TO_APPLY"
FAILED = "FAILED"

ALL_STATUSES = [
    NEW, SCRAPING, SCRAPED, QUEUED, GENERATING, VALIDATING,
    NEEDS_CORRECTION, CORRECTING, FINALIZING, READY_TO_APPLY, FAILED,
]

# Legal forward transitions. FAILED is reachable from every active status.
# NEW/QUEUED both go straight to GENERATING — ingestion is the new
# "scraping": by the time a job reaches NEW it already has a complete
# jobs/inbox/*.md file, so there is no separate SCRAPING stage to enter.
ALLOWED_TRANSITIONS = {
    NEW: {GENERATING},
    SCRAPING: {SCRAPED, FAILED},   # legacy-only, see comment above
    SCRAPED: {QUEUED},             # legacy-only
    QUEUED: {GENERATING},
    GENERATING: {VALIDATING, FAILED},
    VALIDATING: {NEEDS_CORRECTION, FINALIZING, FAILED},
    NEEDS_CORRECTION: {CORRECTING},
    CORRECTING: {VALIDATING, FAILED},
    FINALIZING: {READY_TO_APPLY, FAILED},
    READY_TO_APPLY: set(),   # terminal — must never rerun
    FAILED: {QUEUED},        # only via explicit manual/administrative requeue
}

# Statuses a worker is allowed to pick up from the queue.
CLAIMABLE_STATUSES = (NEW, QUEUED, NEEDS_CORRECTION)

# Statuses that mean "a worker was actively doing something with this job".
# On restart, with no worker threads alive, anything sitting in one of these
# is an abandoned in-flight job and must be safely requeued (never silently
# left stuck, never resurrected into READY_TO_APPLY).
IN_FLIGHT_RECOVERY_MAP = {
    SCRAPING: NEW,       # legacy-only re-entry point
    GENERATING: QUEUED,
    VALIDATING: QUEUED,
    CORRECTING: QUEUED,
    FINALIZING: QUEUED,
}

PRINT_LOCK = threading.Lock()

# Global lock serializing every `claude -p` stage invocation across all
# worker threads in this process. See concurrency_audit.json /
# ../orchestrator/README.md "Locking strategy" for the evidence-based
# rationale: the frozen V8.5 pipeline stages read-modify-write
# tracking/application_tracker.xlsx as a single whole-file operation, share
# one watermark HTTP service instance, and share Word/PDF conversion
# scratch paths. None of that lives in the new orchestrator, so it cannot be
# instrumented at finer granularity than "one claude -p pipeline stage at a
# time".
CLAUDE_STAGE_LOCK = threading.Lock()

# Process-wide lock serializing every SQLite operation in this process. See
# "SQLite thread-safety design" in README.md for the full rationale. V1
# design, deliberately conservative: a single sqlite3.Connection is NEVER
# shared across threads (each DB operation opens its own connection, uses
# it, and closes it before returning — see _with_conn()), and this RLock
# additionally ensures at most one thread is ever inside SQLite at all in
# this process, so reads and writes from different worker threads can never
# truly overlap at the C level. This trades away DB-level concurrency (every
# operation is short, so the cost is small) for correctness. Reentrant so a
# function that owns a connection may safely call another locked helper
# without deadlocking itself on the same thread.
SQLITE_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    with PRINT_LOCK:
        print(f"[{now_iso()}] {msg}", flush=True)


def slugify(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or "unknown"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# SQLite layer
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_key           TEXT PRIMARY KEY,

    -- Universal source-agnostic identity/metadata (see universal_job.py).
    source            TEXT,
    source_domain     TEXT,
    source_job_id     TEXT,
    requisition_id    TEXT,
    source_url        TEXT,
    application_url   TEXT,
    company_careers_url TEXT,
    company           TEXT,
    role              TEXT,
    location          TEXT,
    work_arrangement  TEXT,
    employment_type   TEXT,
    posted_date       TEXT,
    closing_date      TEXT,
    compensation      TEXT,
    scraped_at        TEXT,
    known_source_urls TEXT,             -- JSON array of every source_url seen for this job

    -- Dedup composites (see universal_job.dedup_composites) — precomputed
    -- at ingestion time so duplicate lookups are plain indexed equality.
    dedup_source_job_composite TEXT,
    dedup_company_req_composite TEXT,
    dedup_company_role_location_composite TEXT,

    status            TEXT NOT NULL,
    priority          INTEGER NOT NULL DEFAULT 0,
    inbox_path        TEXT,
    raw_snapshot_path TEXT,
    resume_path       TEXT,
    cover_letter_path TEXT,
    attempts          INTEGER NOT NULL DEFAULT 0,
    correction_rounds INTEGER NOT NULL DEFAULT 0,
    last_error        TEXT,
    worker_id         TEXT,
    claimed_at        TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,

    -- Legacy LinkedIn-only-flow columns. No longer written by any code
    -- path in this file; kept only so migrate_schema() can backfill an
    -- already-existing row (e.g. the Example Robotics test row) without
    -- losing data.
    -- See migrate_schema() / README.md "Schema migration".
    linkedin_job_id   TEXT,
    raw_url           TEXT,
    normalized_url    TEXT
);

CREATE TABLE IF NOT EXISTS status_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key     TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    ts          TEXT NOT NULL,
    detail      TEXT
);

CREATE TABLE IF NOT EXISTS claude_metrics (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key         TEXT NOT NULL,
    stage           TEXT NOT NULL,
    attempt         INTEGER,
    success         INTEGER NOT NULL,
    total_cost_usd  REAL,
    duration_ms     INTEGER,
    usage_json      TEXT,
    model_usage_json TEXT,
    session_id      TEXT,
    error           TEXT,
    ts              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watcher_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Columns a pre-migration `jobs` table (LinkedIn-only flow) won't have yet.
# migrate_schema() adds any of these that are missing via ALTER TABLE ...
# ADD COLUMN — never a destructive rebuild. A fresh install's CREATE TABLE
# above already includes all of them, so this is a no-op there.
_NEW_COLUMNS = {
    "source": "TEXT", "source_domain": "TEXT", "source_job_id": "TEXT", "requisition_id": "TEXT",
    "source_url": "TEXT", "application_url": "TEXT", "company_careers_url": "TEXT",
    "location": "TEXT", "work_arrangement": "TEXT", "employment_type": "TEXT",
    "posted_date": "TEXT", "closing_date": "TEXT", "compensation": "TEXT", "scraped_at": "TEXT",
    "known_source_urls": "TEXT",
    "dedup_source_job_composite": "TEXT",
    "dedup_company_req_composite": "TEXT",
    "dedup_company_role_location_composite": "TEXT",
}


def _open_conn(db_path: Path) -> sqlite3.Connection:
    """Open ONE fresh connection for ONE short operation on the calling
    thread. check_same_thread=True (the default) is intentional, not an
    oversight: it makes Python itself raise loudly if this connection is
    ever touched from a different thread than the one that opened it,
    instead of silently allowing the unsafe cross-thread access that this
    whole module is designed to rule out. Callers must close it (see
    _with_conn)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _with_conn(db_path: Path, fn):
    """Run fn(conn) against a single connection that is opened, used, and
    closed by this one call, on this one thread, before returning — this is
    the ONLY way any code in this module talks to SQLite. SQLITE_LOCK
    additionally serializes these calls process-wide. See "SQLite
    thread-safety design" in README.md."""
    with SQLITE_LOCK:
        conn = _open_conn(db_path)
        try:
            return fn(conn)
        finally:
            conn.close()


def migrate_schema(db_path: Path = DB_PATH) -> None:
    """Non-destructive schema migration for the source-agnostic universal
    job schema: adds any missing new columns via ALTER TABLE ... ADD COLUMN
    (never drops or rebuilds the table), then backfills them for any
    pre-existing LinkedIn-only-flow row (identified by linkedin_job_id
    being set but source still NULL) so it fits the universal schema
    without losing any data — see README.md "Schema migration"."""
    def op(conn):
        existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
        for col, sql_type in _NEW_COLUMNS.items():
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {sql_type}")
        conn.commit()

        legacy_rows = conn.execute(
            "SELECT job_key, linkedin_job_id, raw_url, normalized_url FROM jobs "
            "WHERE source IS NULL AND linkedin_job_id IS NOT NULL"
        ).fetchall()
        for row in legacy_rows:
            source_job_id = row["linkedin_job_id"]
            source_url = row["normalized_url"] or row["raw_url"]
            composite = f"linkedin|{source_job_id}"
            conn.execute(
                "UPDATE jobs SET source = 'linkedin', source_domain = 'linkedin.com', "
                "source_job_id = ?, source_url = ?, dedup_source_job_composite = ? "
                "WHERE job_key = ?",
                (source_job_id, source_url, composite, row["job_key"]),
            )
        if legacy_rows:
            conn.commit()
            log(f"migrate_schema: backfilled {len(legacy_rows)} legacy LinkedIn-flow row(s) "
                f"with universal source fields")
    _with_conn(db_path, op)


def init_db(db_path: Path = DB_PATH) -> None:
    def op(conn):
        conn.executescript(_SCHEMA)
        conn.commit()
    _with_conn(db_path, op)
    migrate_schema(db_path)


def record_transition(conn, job_key: str, from_status: Optional[str], to_status: str, detail: str = "") -> None:
    conn.execute(
        "INSERT INTO status_history (job_key, from_status, to_status, ts, detail) VALUES (?, ?, ?, ?, ?)",
        (job_key, from_status, to_status, now_iso(), detail),
    )


def transition(job_key: str, to_status: str, detail: str = "", db_path: Path = DB_PATH,
               allow_from: Optional[set] = None) -> None:
    """Validate and apply a state transition. Raises ValueError on an
    illegal transition instead of silently allowing it. One connection,
    opened and closed inside this one call (see _with_conn)."""
    def op(conn):
        row = conn.execute("SELECT status FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        if row is None:
            raise ValueError(f"unknown job_key {job_key!r}")
        current = row["status"]
        legal = ALLOWED_TRANSITIONS.get(current, set())
        if to_status not in legal:
            raise ValueError(f"illegal transition for {job_key}: {current} -> {to_status}")
        if allow_from is not None and current not in allow_from:
            raise ValueError(f"transition {current} -> {to_status} not permitted from caller context")
        conn.execute(
            "UPDATE jobs SET status = ?, updated_at = ? WHERE job_key = ?",
            (to_status, now_iso(), job_key),
        )
        record_transition(conn, job_key, current, to_status, detail)
        conn.commit()
    _with_conn(db_path, op)


def claim_next_job(worker_id: str, db_path: Path = DB_PATH) -> Optional[dict]:
    """Atomically claim the highest-priority claimable job for this worker.
    One connection, opened and closed inside this one call, so the
    select-then-conditional-update compare-and-swap below can never overlap
    with another thread's connection at the C level."""
    def op(conn):
        row = conn.execute(
            f"""SELECT job_key, status FROM jobs
                WHERE status IN ({",".join("?" for _ in CLAIMABLE_STATUSES)})
                ORDER BY priority DESC, created_at ASC
                LIMIT 1""",
            CLAIMABLE_STATUSES,
        ).fetchone()
        if row is None:
            return None
        job_key, status = row["job_key"], row["status"]
        # Compare-and-swap: only claim if status hasn't changed under us.
        # NEW and QUEUED both go straight to GENERATING — see the
        # ALLOWED_TRANSITIONS comment above on why there is no separate
        # scraping stage to claim into anymore.
        next_status = CORRECTING if status == NEEDS_CORRECTION else GENERATING
        cur = conn.execute(
            "UPDATE jobs SET status = ?, worker_id = ?, claimed_at = ?, updated_at = ? "
            "WHERE job_key = ? AND status = ?",
            (next_status, worker_id, now_iso(), now_iso(), job_key, status),
        )
        if cur.rowcount == 0:
            conn.commit()
            return None  # another thread beat us to it
        record_transition(conn, job_key, status, next_status, detail=f"claimed by {worker_id}")
        conn.commit()
        claimed = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        return dict(claimed) if claimed is not None else None
    return _with_conn(db_path, op)


def fail_job(job_key: str, reason: str, db_path: Path = DB_PATH) -> None:
    def op(conn):
        row = conn.execute("SELECT status FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        current = row["status"] if row else None
        conn.execute(
            "UPDATE jobs SET status = ?, last_error = ?, worker_id = NULL, claimed_at = NULL, "
            "attempts = attempts + 1, updated_at = ? WHERE job_key = ?",
            (FAILED, reason, now_iso(), job_key),
        )
        record_transition(conn, job_key, current, FAILED, detail=reason)
        conn.commit()
    _with_conn(db_path, op)
    log(f"job {job_key} FAILED: {reason}")


def recover_after_restart(db_path: Path = DB_PATH) -> list[str]:
    """Safely requeue abandoned in-flight jobs after a process restart.

    READY_TO_APPLY jobs are never touched (must not rerun). QUEUED jobs are
    left as-is (they will simply be picked up again). Anything left in an
    active in-flight status has no live worker behind it anymore (this is a
    fresh process start) and is requeued to the nearest safe re-entry point.
    One connection for the whole recovery pass (startup-only, single-threaded).
    """
    def op(conn):
        recovered = []
        for in_flight_status, target_status in IN_FLIGHT_RECOVERY_MAP.items():
            rows = conn.execute("SELECT job_key FROM jobs WHERE status = ?", (in_flight_status,)).fetchall()
            for row in rows:
                job_key = row["job_key"]
                conn.execute(
                    "UPDATE jobs SET status = ?, worker_id = NULL, claimed_at = NULL, updated_at = ? "
                    "WHERE job_key = ?",
                    (target_status, now_iso(), job_key),
                )
                record_transition(conn, job_key, in_flight_status, target_status, detail="recovered_after_restart")
                recovered.append(job_key)
        conn.commit()
        return recovered
    return _with_conn(db_path, op)


def _fetch_job(job_key: str, db_path: Path = DB_PATH) -> Optional[dict]:
    def op(conn):
        row = conn.execute("SELECT * FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        return dict(row) if row is not None else None
    return _with_conn(db_path, op)


# ---------------------------------------------------------------------------
# Universal ingestion: queue/incoming/*.md -> jobs/inbox/*.md + SQLite row
# ---------------------------------------------------------------------------

def find_duplicate_job(job_key: str, canonical_application_url: Optional[str],
                        canonical_source_url: Optional[str], composites: dict,
                        db_path: Path = DB_PATH) -> Optional[str]:
    """Look up an existing job matching this new one, in the priority order
    from the universal dedup contract:
      1. exact job_key match
      2. canonical application_url
      3. canonical source_url
      4. source + source_job_id composite
      5. company + requisition_id composite
      6. normalized company + role + location composite
    Returns the existing job_key, or None if this is genuinely new."""
    def op(conn):
        row = conn.execute("SELECT job_key FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        if row:
            return row["job_key"]
        if canonical_application_url:
            row = conn.execute(
                "SELECT job_key FROM jobs WHERE application_url = ?", (canonical_application_url,)
            ).fetchone()
            if row:
                return row["job_key"]
        if canonical_source_url:
            row = conn.execute(
                "SELECT job_key FROM jobs WHERE source_url = ?", (canonical_source_url,)
            ).fetchone()
            if row:
                return row["job_key"]
        for column, key in (
            ("dedup_source_job_composite", "source_job_composite"),
            ("dedup_company_req_composite", "company_req_composite"),
            ("dedup_company_role_location_composite", "company_role_location_composite"),
        ):
            value = composites.get(key)
            if value:
                row = conn.execute(f"SELECT job_key FROM jobs WHERE {column} = ?", (value,)).fetchone()
                if row:
                    return row["job_key"]
        return None
    return _with_conn(db_path, op)


def enrich_duplicate_job(existing_job_key: str, canonical_application_url: Optional[str],
                          canonical_source_url: Optional[str], db_path: Path = DB_PATH) -> None:
    """When the same real-world job arrives again under a different
    identity (e.g. seen on LinkedIn, then again on the employer's own
    careers site), never create a second row. Instead prefer storing the
    direct employer application URL when one becomes available, and keep a
    record of every known source URL — 'preserve all known source URLs
    when practical' per the dedup contract."""
    def op(conn):
        row = conn.execute(
            "SELECT application_url, known_source_urls FROM jobs WHERE job_key = ?", (existing_job_key,)
        ).fetchone()
        if row is None:
            return
        known = json.loads(row["known_source_urls"]) if row["known_source_urls"] else []
        changed = False
        if canonical_source_url and canonical_source_url not in known:
            known.append(canonical_source_url)
            changed = True
        new_app_url = row["application_url"]
        if canonical_application_url and not row["application_url"]:
            new_app_url = canonical_application_url
            changed = True
        if changed:
            conn.execute(
                "UPDATE jobs SET application_url = ?, known_source_urls = ?, updated_at = ? WHERE job_key = ?",
                (new_app_url, json.dumps(known), now_iso(), existing_job_key),
            )
            conn.commit()
    _with_conn(db_path, op)


def insert_new_job(fm: dict, job_key: str, canonical_source_url: Optional[str],
                    canonical_application_url: Optional[str], inbox_path: Path,
                    raw_snapshot_path: Optional[Path], composites: dict,
                    urgent: bool = False, db_path: Path = DB_PATH) -> None:
    def op(conn):
        ts = now_iso()
        conn.execute(
            """INSERT INTO jobs (
                job_key, source, source_domain, source_job_id, requisition_id,
                source_url, application_url, company_careers_url,
                company, role, location, work_arrangement, employment_type,
                posted_date, closing_date, compensation, scraped_at,
                known_source_urls, dedup_source_job_composite, dedup_company_req_composite,
                dedup_company_role_location_composite,
                status, priority, inbox_path, raw_snapshot_path,
                attempts, correction_rounds, created_at, updated_at
            ) VALUES (?,?,?,?,?, ?,?,?, ?,?,?,?,?, ?,?,?,?, ?,?,?,?, ?,?,?,?, 0,0,?,?)""",
            (
                job_key, fm.get("source"), fm.get("source_domain"), fm.get("source_job_id"),
                fm.get("requisition_id"),
                canonical_source_url, canonical_application_url, fm.get("company_careers_url"),
                fm.get("company"), fm.get("role"), fm.get("location"), fm.get("work_arrangement"),
                fm.get("employment_type"),
                fm.get("posted_date"), fm.get("closing_date"), fm.get("compensation"), fm.get("scraped_at"),
                json.dumps([canonical_source_url] if canonical_source_url else []),
                composites.get("source_job_composite"), composites.get("company_req_composite"),
                composites.get("company_role_location_composite"),
                NEW, 1 if urgent else 0, str(inbox_path),
                str(raw_snapshot_path) if raw_snapshot_path else None,
                ts, ts,
            ),
        )
        record_transition(conn, job_key, None, NEW,
                          detail=f"ingested from queue/incoming (source={fm.get('source')})")
        conn.commit()
    _with_conn(db_path, op)


def _tracker_fields_from_job(job: dict) -> dict:
    return {
        "Job Key": job["job_key"],
        "Source": job.get("source") or "",
        "Source Website": job.get("source_domain") or "",
        "Source Job ID": job.get("source_job_id") or "",
        "Requisition ID": job.get("requisition_id") or "",
        "Company": job.get("company") or "",
        "Role": job.get("role") or "",
        "Location": job.get("location") or "",
        "Work Arrangement": job.get("work_arrangement") or "",
        "Employment Type": job.get("employment_type") or "",
        "Job URL": job.get("source_url") or "",
        "Application URL": job.get("application_url") or "",
        "Company Careers URL": job.get("company_careers_url") or "",
        "Posted Date": job.get("posted_date") or "",
        "Closing Date": job.get("closing_date") or "",
        "Priority": "URGENT" if job.get("priority") else "NORMAL",
        "Pipeline Status": job.get("status") or "",
        "Resume Path": job.get("resume_path") or "",
        "Cover Letter Path": job.get("cover_letter_path") or "",
        "Job Description Path": job.get("inbox_path") or "",
        "Compensation": job.get("compensation") or "",
        "Last Updated": now_iso(),
    }


def sync_tracker_for_job(job_key: str, db_path: Path = DB_PATH,
                          tracker_path: Path = TRACKER_XLSX_PATH,
                          extra_fields: Optional[dict] = None) -> None:
    """Read the current DB row for job_key and (re)sync its tracker row.
    Only ever writes ORCHESTRATOR-owned columns (see
    tracker_xlsx.HUMAN_OWNED_HEADERS) — Application Status/Date Applied/
    Recruiter/Referral/Interview Stage/Next Action/Notes are never included
    here, so they can never be overwritten by an automated sync.

    A tracker-write failure must never lose the underlying job, resume, or
    cover-letter artifacts — caught and logged here, never raised, matching
    config/application_tracker_schema.md's "Atomic Update Rule".
    """
    job = _fetch_job(job_key, db_path=db_path)
    if job is None:
        return
    fields = _tracker_fields_from_job(job)
    if extra_fields:
        fields.update(extra_fields)
    try:
        tracker_xlsx.upsert_tracker_row(tracker_path, job_key, fields)
    except Exception as exc:  # noqa: BLE001 — tracker failures must not lose pipeline state
        log(f"job {job_key}: TRACKER_UPDATE_FAILED: {exc!r}")


def _write_rejected(path: Path, problems: list[str],
                     rejected_dir: Path = QUEUE_REJECTED_DIR) -> tuple[Path, Path]:
    rejected_dir.mkdir(parents=True, exist_ok=True)
    dest = rejected_dir / path.name
    if dest.exists():
        dest = rejected_dir / f"{path.stem}-{sha256_hex(path.name.encode())[:8]}{path.suffix}"
    shutil.move(str(path), str(dest))
    error_path = dest.with_name(dest.name + ".error.txt")
    error_path.write_text(
        "This file was rejected by the rolling-queue ingestion pipeline.\n\n"
        "Problems found:\n" + "\n".join(f"- {p}" for p in problems) + "\n",
        encoding="utf-8",
    )
    return dest, error_path


def process_incoming_file(path: Path, db_path: Path = DB_PATH,
                           inbox_dir: Path = JOBS_INBOX, raw_dir: Path = RAW_DIR,
                           rejected_dir: Path = QUEUE_REJECTED_DIR,
                           tracker_path: Path = TRACKER_XLSX_PATH) -> dict:
    """Validate, canonicalize, deduplicate, and (if new) ingest one
    queue/incoming/*.md file. Always consumes the file (moves it to
    jobs/inbox/ + queue/incoming/'s raw snapshot on success, to
    queue/rejected/ on failure, or deletes it once its data is folded into
    an existing job on a duplicate) — queue/incoming/ is a self-draining
    drop zone, never an append-only log. Never silently discards a
    malformed file: every rejection gets a companion .error.txt explaining
    why. Never makes a network call."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        problems = [f"could not read file: {exc}"]
        dest, _ = _write_rejected(path, problems, rejected_dir=rejected_dir)
        log(f"ingest: REJECTED {path.name} -> {dest.name} ({'; '.join(problems)})")
        return {"result": "REJECTED", "path": str(dest), "problems": problems}

    try:
        fm, body = universal_job.parse_frontmatter(text)
    except universal_job.SchemaError as exc:
        dest, _ = _write_rejected(path, exc.problems, rejected_dir=rejected_dir)
        log(f"ingest: REJECTED {path.name} -> {dest.name} ({'; '.join(exc.problems)})")
        return {"result": "REJECTED", "path": str(dest), "problems": exc.problems}

    problems = universal_job.validate_schema(fm, body)
    if problems:
        dest, _ = _write_rejected(path, problems, rejected_dir=rejected_dir)
        log(f"ingest: REJECTED {path.name} -> {dest.name} ({'; '.join(problems)})")
        return {"result": "REJECTED", "path": str(dest), "problems": problems}

    canonical_source_url = universal_job.canonicalize_url(fm.get("source_url"))
    canonical_application_url = universal_job.canonicalize_url(fm.get("application_url"))
    job_key = universal_job.compute_job_key(fm, canonical_source_url)
    composites = universal_job.dedup_composites(fm)

    duplicate_of = find_duplicate_job(
        job_key, canonical_application_url, canonical_source_url, composites, db_path=db_path
    )
    if duplicate_of:
        enrich_duplicate_job(duplicate_of, canonical_application_url, canonical_source_url, db_path=db_path)
        path.unlink(missing_ok=True)
        log(f"ingest: DUPLICATE {path.name} -> existing job {duplicate_of}")
        return {"result": "DUPLICATE", "job_key": duplicate_of}

    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_snapshot_path = raw_dir / f"{job_key}.md"
    raw_snapshot_path.write_text(text, encoding="utf-8")

    inbox_dir.mkdir(parents=True, exist_ok=True)
    inbox_filename = f"{slugify(fm.get('company'))}-{slugify(fm.get('role'))}-{job_key}.md"
    inbox_path = inbox_dir / inbox_filename
    inbox_markdown = universal_job.render_inbox_markdown(
        fm, body, job_key, canonical_source_url, canonical_application_url
    )
    inbox_path.write_text(inbox_markdown, encoding="utf-8")

    insert_new_job(
        fm, job_key, canonical_source_url, canonical_application_url,
        inbox_path, raw_snapshot_path, composites, db_path=db_path,
    )
    sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)

    path.unlink(missing_ok=True)
    log(f"ingest: NEW {path.name} -> job {job_key} ({fm.get('company')} / {fm.get('role')})")
    return {"result": "INGESTED", "job_key": job_key}


def drain_incoming_directory(incoming_dir: Path = QUEUE_INCOMING_DIR, db_path: Path = DB_PATH,
                              inbox_dir: Path = JOBS_INBOX, raw_dir: Path = RAW_DIR,
                              rejected_dir: Path = QUEUE_REJECTED_DIR,
                              tracker_path: Path = TRACKER_XLSX_PATH) -> list[dict]:
    """Process every *.md file currently sitting in incoming_dir, in
    filename order (deterministic). Files are expected to be written whole
    (the producing Claude conversation writes the complete standardized
    Markdown file in one shot before it ever appears in queue/incoming/),
    so there is no partial-write staleness gate here — an earlier version
    of this function had one, and it had a latent bug: on a directory
    watched purely via kqueue events (no periodic polling), a file skipped
    once for being "too young" was never guaranteed to be revisited, since
    nothing about a later os.utime/read touches the directory entry itself
    and so never re-fires a NOTE_WRITE event. Simpler and correct: process
    on sight."""
    if not incoming_dir.exists():
        return []
    results = []
    for path in sorted(incoming_dir.glob("*.md")):
        if not path.exists():
            continue
        results.append(process_incoming_file(
            path, db_path=db_path, inbox_dir=inbox_dir, raw_dir=raw_dir,
            rejected_dir=rejected_dir, tracker_path=tracker_path,
        ))
    return results


class IncomingWatcher:
    """Watches queue/incoming/ for new *.md files.

    Uses macOS kqueue (EVFILT_VNODE / NOTE_WRITE on the directory itself,
    which fires when entries are added/removed) for low-latency detection
    when available; otherwise falls back to ~0.5s polling. Holds no
    sqlite3.Connection of its own — every DB access goes through
    drain_incoming_directory() -> process_incoming_file(), each of which
    opens/closes its own connection per call (see "SQLite thread-safety
    design" in README.md).
    """

    POLL_INTERVAL_SECONDS = 0.5

    def __init__(self, incoming_dir: Path, stop_event: threading.Event, db_path: Path = DB_PATH,
                 inbox_dir: Path = JOBS_INBOX, raw_dir: Path = RAW_DIR,
                 rejected_dir: Path = QUEUE_REJECTED_DIR, tracker_path: Path = TRACKER_XLSX_PATH):
        self.incoming_dir = incoming_dir
        self.stop_event = stop_event
        self.db_path = db_path
        self.inbox_dir = inbox_dir
        self.raw_dir = raw_dir
        self.rejected_dir = rejected_dir
        self.tracker_path = tracker_path
        self.kqueue_supported = hasattr(select, "kqueue") and hasattr(select, "kevent")

    def _drain(self) -> None:
        drain_incoming_directory(
            self.incoming_dir, db_path=self.db_path, inbox_dir=self.inbox_dir,
            raw_dir=self.raw_dir, rejected_dir=self.rejected_dir, tracker_path=self.tracker_path,
        )

    def run(self) -> None:
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self._drain()  # catch up on anything already present
        if self.kqueue_supported:
            self._run_kqueue()
        else:
            self._run_polling()

    def _run_polling(self) -> None:
        last_listing = None
        while not self.stop_event.is_set():
            try:
                listing = tuple(sorted(p.name for p in self.incoming_dir.glob("*.md")))
            except FileNotFoundError:
                listing = ()
            if listing != last_listing:
                last_listing = listing
                self._drain()
            self.stop_event.wait(self.POLL_INTERVAL_SECONDS)

    def _run_kqueue(self) -> None:
        kq = select.kqueue()
        fd = os.open(str(self.incoming_dir), os.O_RDONLY)
        fall_back_to_polling = False
        try:
            ev = select.kevent(
                fd,
                filter=select.KQ_FILTER_VNODE,
                flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                fflags=select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME,
            )
            kq.control([ev], 0)
            while not self.stop_event.is_set():
                try:
                    events = kq.control(None, 1, 1.0)
                except OSError:
                    events = []
                if events:
                    self._drain()
                    try:
                        replaced = os.stat(str(self.incoming_dir)).st_ino != os.fstat(fd).st_ino
                    except FileNotFoundError:
                        replaced = True
                    if replaced:
                        fall_back_to_polling = True
                        break
        finally:
            with contextlib.suppress(Exception):
                os.close(fd)
            with contextlib.suppress(Exception):
                kq.close()
        if fall_back_to_polling:
            self._run_polling()


# ---------------------------------------------------------------------------
# Claude CLI worker-process stage invocation
# ---------------------------------------------------------------------------

_ORCHESTRATOR_RESULT_RE = re.compile(r"ORCHESTRATOR_RESULT:\s*(\{.*\})\s*$", re.DOTALL)


def render_worker_prompt(stage: str, job: dict, attempt: int, correction_feedback: str = "") -> str:
    template = WORKER_PROMPT_TEMPLATE.read_text(encoding="utf-8")
    pipeline_cfg = {}
    if ACTIVE_PIPELINE_CONFIG.exists():
        pipeline_cfg = json.loads(ACTIVE_PIPELINE_CONFIG.read_text(encoding="utf-8"))
    entrypoints = pipeline_cfg.get("production_entrypoints", {})
    replacements = {
        "{{JOB_KEY}}": job["job_key"],
        "{{SOURCE}}": job.get("source") or "",
        "{{SOURCE_JOB_ID}}": job.get("source_job_id") or "",
        "{{STAGE}}": stage,
        "{{ATTEMPT}}": str(attempt),
        "{{MAX_ATTEMPTS}}": str(MAX_CORRECTION_ROUNDS),
        "{{INBOX_PATH}}": job["inbox_path"] or "",
        "{{ACTIVE_VERSION}}": pipeline_cfg.get("active_version", "UNKNOWN"),
        "{{STRATEGY_SKILL}}": entrypoints.get("strategy_skill", "jobapp-resume-batch"),
        "{{CORRECTION_SKILL}}": entrypoints.get("correction_skill", "jobapp-resume-correct"),
        "{{LOCAL_PIPELINE}}": entrypoints.get("local_pipeline", ""),
        "{{CORRECTION_COLLECTOR}}": entrypoints.get("correction_collector", ""),
        "{{WATERMARK_URL}}": WATERMARK_SERVICE_URL,
        "{{CORRECTION_FEEDBACK}}": correction_feedback or "(none)",
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    return template


# ---------------------------------------------------------------------------
# Headless `claude -p` permission configuration
# ---------------------------------------------------------------------------
#
# Investigated against the actually-installed CLI (`claude --help`, `claude
# doctor` -> native 2.1.268) rather than guessed. A headless `-p` run has no
# TTY and nobody to answer an interactive permission prompt, so the default
# (manual) permission mode either hangs or auto-denies the first time the
# worker needs to run a project-local Python script — this is exactly what
# produced GENERATION_FAILED: PYTHON_EXECUTION_PERMISSION_DENIED on the first
# real run.
#
# Chosen fix: `--permission-mode dontAsk` plus a bounded `--allowedTools`
# list. `dontAsk` never prompts — anything not covered by an allow rule is
# denied automatically instead of hanging — so it is strictly safer than
# `bypassPermissions` / `--dangerously-skip-permissions` while still letting
# the existing trusted V8.5 workflow run unattended:
#   - Read/Write/Edit: draft/final resume & cover-letter files under
#     output/<job>/.
#   - Skill: the pipeline's own already-vetted, frozen skills
#     (jobapp-resume-batch, jobapp-resume-correct, finalize-resume,
#     remove-ai-marks) — these are the documented production entrypoints,
#     not arbitrary code.
#   - Bash(python3 tools/*): the project-local Python entrypoints the frozen
#     pipeline actually shells out to (tools/resume/**, tools/ats/**,
#     tools/resume/v8/**) — see concurrency_audit.json for the exact list.
#   - Bash(curl http://127.0.0.1:8765/*): the one local watermark-service
#     health-check URL named in worker_prompt.txt.
# Everything else (including destructive shell commands, network access
# beyond that one local port, or touching files outside this workspace) is
# silently denied rather than skipped-with-a-prompt-that-never-comes.
# --dangerously-skip-permissions is deliberately NOT used: this allowlist is
# sufficient for the documented workflow, so the least-privileged supported
# option applies.
CLAUDE_PERMISSION_MODE = "dontAsk"
CLAUDE_ALLOWED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Skill",
    "Bash(python3 tools/*)",
    "Bash(curl http://127.0.0.1:8765/*)",
]


def build_claude_command(prompt: str) -> list[str]:
    return [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--permission-mode", CLAUDE_PERMISSION_MODE,
        "--allowedTools", *CLAUDE_ALLOWED_TOOLS,
    ]


# ---------------------------------------------------------------------------
# Per-job worker result persistence (.work/rolling_queue/workers/<JOB_KEY>/)
# ---------------------------------------------------------------------------

def persist_worker_artifacts(
    job_key: str, stage: str, attempt: int, result: dict,
    started_at: str, finished_at: str,
) -> None:
    """Write claude_result.json / worker_status.json / worker.log for this
    job. Called for EVERY claude -p invocation attempt, success or failure —
    including subprocess-launch failures and timeouts — so these directories
    are never silently left empty after a real run."""
    job_dir = WORKERS_DIR / job_key
    job_dir.mkdir(parents=True, exist_ok=True)

    (job_dir / "claude_result.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8",
    )

    status = {
        "job_key": job_key,
        "stage": stage,
        "attempt": attempt,
        "started_at": started_at,
        "finished_at": finished_at,
        "success": bool(result.get("success")),
        "exit_code": result.get("returncode"),
        "failure_reason": None if result.get("success") else result.get("reason"),
        "claude_session_id": result.get("session_id"),
        "total_cost_usd": result.get("total_cost_usd"),
        "duration_ms": result.get("duration_ms"),
    }
    (job_dir / "worker_status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8",
    )

    with open(job_dir / "worker.log", "a", encoding="utf-8") as f:
        f.write(f"=== {finished_at} stage={stage} attempt={attempt} "
                f"success={status['success']} exit_code={status['exit_code']} ===\n")
        f.write("--- stdout ---\n")
        f.write((result.get("raw_stdout") or "") + "\n")
        f.write("--- stderr ---\n")
        f.write((result.get("raw_stderr") or "") + "\n")


def call_claude_worker(job: dict, stage: str, attempt: int, correction_feedback: str = "") -> dict:
    """Invoke one independent `claude -p ... --output-format json` process
    for a single pipeline stage of a single job. Every such invocation is
    serialized across all workers by CLAUDE_STAGE_LOCK — see the module
    docstring on CLAUDE_STAGE_LOCK for the shared-resource rationale
    (tracker.xlsx, watermark service, Word/PDF conversion scratch paths).

    Always persists claude_result.json / worker_status.json / worker.log
    under .work/rolling_queue/workers/<job_key>/ before returning, even on a
    subprocess launch failure, timeout, or unparseable output.
    """
    prompt = render_worker_prompt(stage, job, attempt, correction_feedback)
    job_key = job["job_key"]
    started_at = now_iso()
    started = time.time()
    proc = None
    launch_error = None  # (reason, stdout_or_none, stderr_or_none)
    with CLAUDE_STAGE_LOCK:
        log(f"job {job_key}: acquired pipeline lock for stage {stage} (attempt {attempt})")
        try:
            proc = subprocess.run(
                build_claude_command(prompt),
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                timeout=CLAUDE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            launch_error = ("CLAUDE_CLI_TIMEOUT", exc.stdout, exc.stderr)
        except OSError as exc:
            launch_error = (f"CLAUDE_CLI_LAUNCH_FAILED: {exc}", None, None)
        finally:
            log(f"job {job_key}: released pipeline lock for stage {stage}")
    duration_ms = int((time.time() - started) * 1000)
    finished_at = now_iso()

    result = {
        "success": False,
        "status": "FAIL",
        "reason": None,
        "resume_path": None,
        "cover_letter_path": None,
        "total_cost_usd": None,
        "duration_ms": duration_ms,
        "usage": None,
        "model_usage": None,
        "session_id": None,
        "raw_stdout": None,
        "raw_stderr": None,
        "returncode": None,
    }

    def finish() -> dict:
        persist_worker_artifacts(job_key, stage, attempt, result, started_at, finished_at)
        return result

    if launch_error is not None:
        reason, out, err = launch_error
        result["reason"] = reason
        result["raw_stdout"] = out or ""
        result["raw_stderr"] = err or ""
        return finish()

    result["raw_stdout"] = proc.stdout
    result["raw_stderr"] = proc.stderr
    result["returncode"] = proc.returncode

    if proc.returncode != 0:
        result["reason"] = f"CLAUDE_CLI_NONZERO_EXIT_{proc.returncode}"
        return finish()

    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        result["reason"] = "CLAUDE_CLI_UNPARSEABLE_JSON_OUTPUT"
        return finish()

    result["total_cost_usd"] = payload.get("total_cost_usd")
    result["usage"] = payload.get("usage")
    result["model_usage"] = payload.get("modelUsage")
    result["session_id"] = payload.get("session_id")
    result["duration_ms"] = payload.get("duration_ms", duration_ms)

    if payload.get("is_error"):
        result["reason"] = "CLAUDE_CLI_REPORTED_ERROR"
        return finish()

    text = payload.get("result") or ""
    m = _ORCHESTRATOR_RESULT_RE.search(text)
    if not m:
        result["reason"] = "MISSING_ORCHESTRATOR_RESULT_MARKER"
        return finish()
    try:
        marker = json.loads(m.group(1))
    except json.JSONDecodeError:
        result["reason"] = "UNPARSEABLE_ORCHESTRATOR_RESULT_MARKER"
        return finish()

    result["status"] = marker.get("status", "FAIL")
    result["reason"] = marker.get("reason")
    result["resume_path"] = marker.get("resume_path")
    result["cover_letter_path"] = marker.get("cover_letter_path")
    result["success"] = result["status"] == "PASS"
    return finish()


def persist_claude_metrics(job_key: str, stage: str, attempt: int, result: dict, db_path: Path = DB_PATH) -> None:
    def op(conn):
        conn.execute(
            """INSERT INTO claude_metrics
               (job_key, stage, attempt, success, total_cost_usd, duration_ms,
                usage_json, model_usage_json, session_id, error, ts)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_key, stage, attempt, 1 if result["success"] else 0,
                result.get("total_cost_usd"), result.get("duration_ms"),
                json.dumps(result.get("usage")) if result.get("usage") is not None else None,
                json.dumps(result.get("model_usage")) if result.get("model_usage") is not None else None,
                result.get("session_id"), None if result["success"] else result.get("reason"),
                now_iso(),
            ),
        )
        conn.commit()
    _with_conn(db_path, op)


# ---------------------------------------------------------------------------
# Generation / correction / finalization stages
# ---------------------------------------------------------------------------

def do_generate_stage(job: dict, worker_id: str, db_path: Path = DB_PATH,
                       tracker_path: Path = TRACKER_XLSX_PATH) -> None:
    job_key = job["job_key"]
    sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)  # Pipeline Status = GENERATING
    result = call_claude_worker(job, "GENERATING", attempt=1)
    persist_claude_metrics(job_key, "GENERATING", 1, result, db_path=db_path)
    if not result["success"]:
        fail_job(job_key, f"GENERATION_FAILED: {result.get('reason')}", db_path=db_path)
        sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)  # Pipeline Status = FAILED
        return
    transition(job_key, VALIDATING, detail="generation reported PASS, entering validation", db_path=db_path)
    _resolve_validation(job_key, worker_id, result, correction_feedback="", db_path=db_path, tracker_path=tracker_path)


def _bump_correction_round(job_key: str, rounds: int, db_path: Path = DB_PATH) -> None:
    def op(conn):
        conn.execute("UPDATE jobs SET correction_rounds = ?, updated_at = ? WHERE job_key = ?",
                     (rounds, now_iso(), job_key))
        conn.commit()
    _with_conn(db_path, op)


def do_correct_stage(job: dict, worker_id: str, db_path: Path = DB_PATH,
                      tracker_path: Path = TRACKER_XLSX_PATH) -> None:
    job_key = job["job_key"]
    rounds = job["correction_rounds"] + 1
    if rounds > MAX_CORRECTION_ROUNDS:
        fail_job(job_key, "MAX_CORRECTIONS_EXCEEDED", db_path=db_path)
        sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)
        return
    _bump_correction_round(job_key, rounds, db_path=db_path)
    sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)  # Pipeline Status = CORRECTING
    result = call_claude_worker(job, "CORRECTING", attempt=rounds, correction_feedback=job["last_error"] or "")
    persist_claude_metrics(job_key, "CORRECTING", rounds, result, db_path=db_path)
    if not result["success"]:
        fail_job(job_key, f"CORRECTION_FAILED: {result.get('reason')}", db_path=db_path)
        sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)
        return
    transition(job_key, VALIDATING, detail=f"correction round {rounds} reported PASS", db_path=db_path)
    _resolve_validation(job_key, worker_id, result, correction_feedback="", db_path=db_path, tracker_path=tracker_path)


def _update_resume_paths(job_key: str, resume_path, cover_letter_path, db_path: Path = DB_PATH) -> None:
    def op(conn):
        conn.execute(
            "UPDATE jobs SET resume_path = ?, cover_letter_path = ?, updated_at = ? WHERE job_key = ?",
            (resume_path, cover_letter_path, now_iso(), job_key),
        )
        conn.commit()
    _with_conn(db_path, op)


def _resolve_validation(job_key: str, worker_id: str, generation_result: dict, correction_feedback: str,
                         db_path: Path = DB_PATH, tracker_path: Path = TRACKER_XLSX_PATH) -> None:
    """The claude worker performs validation as part of its own stage and
    reports PASS/FAIL via the ORCHESTRATOR_RESULT marker; VALIDATING is a
    real (logged) state but resolves synchronously here rather than sitting
    idle in the queue."""
    if generation_result["resume_path"] or generation_result["cover_letter_path"]:
        _update_resume_paths(job_key, generation_result.get("resume_path"),
                              generation_result.get("cover_letter_path"), db_path=db_path)
    transition(job_key, FINALIZING, detail="validation passed", db_path=db_path)
    do_finalize_stage(job_key, worker_id, db_path=db_path, tracker_path=tracker_path)


def do_finalize_stage(job_key: str, worker_id: str, db_path: Path = DB_PATH,
                       tracker_path: Path = TRACKER_XLSX_PATH) -> None:
    job = _fetch_job(job_key, db_path=db_path)
    sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)  # Pipeline Status = FINALIZING
    result = call_claude_worker(job, "FINALIZING", attempt=1)
    persist_claude_metrics(job_key, "FINALIZING", 1, result, db_path=db_path)
    if not result["success"]:
        fail_job(job_key, f"FINALIZE_FAILED: {result.get('reason')}", db_path=db_path)
        sync_tracker_for_job(job_key, db_path=db_path, tracker_path=tracker_path)
        return
    if result.get("resume_path") or result.get("cover_letter_path"):
        _update_resume_paths(
            job_key,
            result.get("resume_path") or job["resume_path"],
            result.get("cover_letter_path") or job["cover_letter_path"],
            db_path=db_path,
        )
    transition(job_key, READY_TO_APPLY, detail="finalize reported PASS", db_path=db_path)
    sync_tracker_for_job(
        job_key, db_path=db_path, tracker_path=tracker_path,
        extra_fields={"Resume Created At": now_iso()},
    )
    release_ready(job_key, db_path=db_path)


def release_ready(job_key: str, db_path: Path = DB_PATH) -> None:
    job = _fetch_job(job_key, db_path=db_path)
    READY_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "job_key": job["job_key"],
        "source": job.get("source"),
        "source_job_id": job.get("source_job_id"),
        "company": job["company"],
        "role": job["role"],
        "inbox_path": job["inbox_path"],
        "resume_path": job["resume_path"],
        "cover_letter_path": job["cover_letter_path"],
        "ready_at": now_iso(),
    }
    (READY_DIR / f"{job_key}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with PRINT_LOCK:
        print("=" * 50, flush=True)
        print("READY TO APPLY", flush=True)
        print(f"Company: {job['company']}", flush=True)
        print(f"Role: {job['role']}", flush=True)
        print(f"Job: {job.get('application_url') or job.get('source_url')}", flush=True)
        print(f"Resume: {job['resume_path']}", flush=True)
        print(f"Cover Letter: {job['cover_letter_path']}", flush=True)
        print("=" * 50, flush=True)


# ---------------------------------------------------------------------------
# Read-only status view (--status)
# ---------------------------------------------------------------------------

STATUS_COLUMNS = [
    "job_key", "source", "company", "role", "status", "attempts",
    "worker_id", "last_error", "created_at", "updated_at",
]


def fetch_status_rows(conn) -> list[dict]:
    """Pure read: SELECT only, never mutates the jobs table. Takes an
    already-open connection whose lifecycle the caller owns — print_status()
    uses a dedicated read-only URI connection; fetch_status_rows_snapshot()
    below uses a normal short-lived one via _with_conn."""
    rows = conn.execute(
        f"SELECT {', '.join(STATUS_COLUMNS)} FROM jobs ORDER BY updated_at DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_status_rows_snapshot(db_path: Path = DB_PATH) -> list[dict]:
    """Same read as fetch_status_rows(), but owns its own short-lived
    connection — for internal callers (self-test, stress test) that don't
    already have one open."""
    return _with_conn(db_path, fetch_status_rows)


def format_status_table(rows: list[dict]) -> str:
    if not rows:
        return "No jobs in queue."
    col_width_cap = 32
    widths = {
        c: min(col_width_cap, max(len(c), max((len(str(r.get(c) or "")) for r in rows), default=0)))
        for c in STATUS_COLUMNS
    }

    def fmt_row(values) -> str:
        return " | ".join(str(v if v is not None else "")[:widths[c]].ljust(widths[c])
                           for c, v in zip(STATUS_COLUMNS, values))

    lines = [fmt_row(STATUS_COLUMNS)]
    lines.append("-+-".join("-" * widths[c] for c in STATUS_COLUMNS))
    for r in rows:
        lines.append(fmt_row(r.get(c) for c in STATUS_COLUMNS))
    return "\n".join(lines)


def print_status() -> None:
    """Read-only: prints a status table and returns. Never starts workers,
    never touches worker_id/claimed_at, never creates queue.db if it doesn't
    already exist."""
    if not DB_PATH.exists():
        print("No queue database found yet (nothing has been added to the rolling queue).")
        return
    try:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        # e.g. no WAL/shm sidecar files yet on some platforms — a plain
        # connection still only ever executes SELECT below, so it remains
        # read-only in effect even though not OS-enforced.
        conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = fetch_status_rows(conn)
    finally:
        conn.close()
    print(format_status_table(rows))


# ---------------------------------------------------------------------------
# Worker pool
# ---------------------------------------------------------------------------

def worker_loop(worker_id: str, stop_event: threading.Event, db_path: Path = DB_PATH,
                 tracker_path: Path = TRACKER_XLSX_PATH) -> None:
    """Holds no sqlite3.Connection of its own across iterations — every call
    below (claim_next_job, do_*_stage, fail_job) opens and closes its own
    short-lived connection. See "SQLite thread-safety design" in README.md."""
    while not stop_event.is_set():
        job = claim_next_job(worker_id, db_path=db_path)
        if job is None:
            stop_event.wait(0.5)
            continue
        try:
            status = job["status"]
            if status == GENERATING:
                do_generate_stage(job, worker_id, db_path=db_path, tracker_path=tracker_path)
            elif status == CORRECTING:
                do_correct_stage(job, worker_id, db_path=db_path, tracker_path=tracker_path)
            else:  # pragma: no cover — defensive, should not happen
                fail_job(job["job_key"], f"UNEXPECTED_CLAIMED_STATUS_{status}", db_path=db_path)
                sync_tracker_for_job(job["job_key"], db_path=db_path, tracker_path=tracker_path)
        except Exception as exc:  # noqa: BLE001 — one broken job must not kill the worker
            fail_job(job["job_key"], f"UNHANDLED_EXCEPTION: {exc!r}", db_path=db_path)
            sync_tracker_for_job(job["job_key"], db_path=db_path, tracker_path=tracker_path)


def run_orchestrator(num_workers: int) -> None:
    if not (1 <= num_workers <= 5):
        raise SystemExit("--workers must be between 1 and 5")
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    WORKERS_DIR.mkdir(parents=True, exist_ok=True)
    READY_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    QUEUE_REJECTED_DIR.mkdir(parents=True, exist_ok=True)

    if PID_FILE.exists():
        raise SystemExit(
            f"{PID_FILE} already exists — refusing to start a second orchestrator instance "
            f"against the same queue.db. Remove it only if you are certain no other instance is running."
        )
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")

    init_db(DB_PATH)
    recovered = recover_after_restart(DB_PATH)
    if recovered:
        log(f"recovered {len(recovered)} abandoned in-flight job(s): {recovered}")

    stop_event = threading.Event()
    threads = []
    try:
        watcher = IncomingWatcher(QUEUE_INCOMING_DIR, stop_event, db_path=DB_PATH)
        watcher_thread = threading.Thread(target=watcher.run, name="watcher", daemon=True)
        watcher_thread.start()
        threads.append(watcher_thread)

        for i in range(num_workers):
            t = threading.Thread(target=worker_loop, args=(f"worker-{i+1}", stop_event), name=f"worker-{i+1}", daemon=True)
            t.start()
            threads.append(t)

        log(f"rolling_queue orchestrator running with {num_workers} worker(s). Ctrl-C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        log("shutdown requested")
    finally:
        stop_event.set()
        for t in threads:
            t.join(timeout=5)
        with contextlib.suppress(FileNotFoundError):
            PID_FILE.unlink()


# ---------------------------------------------------------------------------
# SQLite concurrency stress test — temp DB only, no Claude, no network,
# never touches jobs/inbox, output, tracker, or V8.5.
#
# Two layers, both against a fresh temp DB every time:
#   - _run_stress_repetition() runs ONE repetition in-process: N threads
#     each insert their own unique jobs (add_job contention), then N threads
#     race to claim_next_job()/transition() the shared pool through to
#     READY_TO_APPLY (CAS-claim contention) — exactly the concurrent
#     add/read/state-update pattern requested, exercised through the real
#     per-operation-connection + SQLITE_LOCK code path.
#   - run_stress_test() drives 5+ repetitions via `--stress-test`, each one
#     launched as its own subprocess (`--stress-test-worker`) so that a
#     genuine SIGSEGV in one repetition is caught as a nonzero/negative
#     subprocess return code instead of taking down the whole run — a real
#     crash can't be caught as a Python exception, so isolating it in a
#     subprocess is the only reliable way to detect it as PASS/FAIL.
# ---------------------------------------------------------------------------

def _stress_add_job(fake_id: str, db_path: Path) -> str:
    """Minimal, source-agnostic job insertion for the stress test only —
    does not go through the full ingestion/dedup/tracker pipeline (that has
    its own dedicated self-test coverage); this exists purely to hammer
    add/claim/transition concurrency against a real jobs-table shape."""
    job_key = f"stress-{fake_id}"
    def op(conn):
        existing = conn.execute("SELECT job_key FROM jobs WHERE job_key = ?", (job_key,)).fetchone()
        if existing:
            return
        ts = now_iso()
        conn.execute(
            "INSERT INTO jobs (job_key, source, company, role, status, priority, "
            "attempts, correction_rounds, created_at, updated_at) "
            "VALUES (?, 'stress', 'StressCo', 'Stress Role', ?, 0, 0, 0, ?, ?)",
            (job_key, NEW, ts, ts),
        )
        record_transition(conn, job_key, None, NEW, detail="stress test insert")
        conn.commit()
    _with_conn(db_path, op)
    return job_key


def _stress_claim_loop(worker_id: str, db_path: Path, stats: dict, stats_lock: threading.Lock,
                        idle_limit: int = 30) -> None:
    idle = 0
    while idle < idle_limit:
        job = claim_next_job(worker_id, db_path=db_path)
        if job is None:
            idle += 1
            time.sleep(0.005)
            continue
        idle = 0
        key, status = job["job_key"], job["status"]
        try:
            if status in (GENERATING, CORRECTING):
                transition(key, VALIDATING, db_path=db_path)
                transition(key, FINALIZING, db_path=db_path)
                transition(key, READY_TO_APPLY, db_path=db_path)
                with stats_lock:
                    stats["completed"].append(key)
        except Exception as exc:  # noqa: BLE001 — record, don't crash the thread silently
            with stats_lock:
                stats["errors"].append(f"{worker_id}: {type(exc).__name__}: {exc}")


def _run_stress_repetition(n_threads: int = 6, jobs_per_thread: int = 8) -> dict:
    """Run ONE full concurrency stress repetition against a fresh temp DB
    (never DB_PATH). Returns a JSON-serializable summary dict."""
    tmpdir = Path(tempfile.mkdtemp(prefix="rolling_queue_stress_"))
    db_path = tmpdir / "stress_queue.db"
    errors: list = []
    errors_lock = threading.Lock()
    try:
        init_db(db_path)

        def inserter(idx: int) -> None:
            for i in range(jobs_per_thread):
                fake_id = f"{idx}-{i}"
                try:
                    _stress_add_job(fake_id, db_path)
                except Exception as exc:  # noqa: BLE001
                    with errors_lock:
                        errors.append(f"inserter-{idx}: {type(exc).__name__}: {exc}")

        insert_threads = [threading.Thread(target=inserter, args=(i,), name=f"stress-insert-{i}")
                          for i in range(n_threads)]
        for t in insert_threads:
            t.start()
        for t in insert_threads:
            t.join(timeout=60)

        expected_total = n_threads * jobs_per_thread
        stats = {"completed": [], "errors": errors}
        stats_lock = threading.Lock()
        claim_threads = [
            threading.Thread(target=_stress_claim_loop, args=(f"stress-worker-{i}", db_path, stats, stats_lock),
                              name=f"stress-claim-{i}")
            for i in range(n_threads)
        ]
        for t in claim_threads:
            t.start()
        for t in claim_threads:
            t.join(timeout=60)

        rows = fetch_status_rows_snapshot(db_path)
        all_job_keys = [r["job_key"] for r in rows]
        duplicate_keys = len(all_job_keys) != len(set(all_job_keys))
        ready_count = sum(1 for r in rows if r["status"] == READY_TO_APPLY)
        non_terminal = [r for r in rows if r["status"] not in (READY_TO_APPLY, FAILED)]

        ok = (
            not errors
            and not duplicate_keys
            and len(rows) == expected_total
            and ready_count == expected_total
            and not non_terminal
        )
        return {
            "ok": ok,
            "errors": errors,
            "expected_total": expected_total,
            "actual_total": len(rows),
            "duplicate_keys": duplicate_keys,
            "ready_count": ready_count,
            "non_terminal_count": len(non_terminal),
        }
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _stress_test_worker_cli(n_threads: int, jobs_per_thread: int) -> int:
    summary = _run_stress_repetition(n_threads=n_threads, jobs_per_thread=jobs_per_thread)
    print(json.dumps(summary))
    return 0 if summary["ok"] else 1


def run_stress_test(repetitions: int = 5, n_threads: int = 6, jobs_per_thread: int = 8) -> int:
    print(
        f"SQLite concurrency stress test: {repetitions} repetition(s), "
        f"{n_threads} threads x {jobs_per_thread} jobs each, temp DB only "
        f"(never {DB_PATH})."
    )
    all_ok = True
    for rep in range(1, repetitions + 1):
        cmd = [
            sys.executable, str(Path(__file__).resolve()),
            "--stress-test-worker",
            "--threads", str(n_threads),
            "--jobs-per-thread", str(jobs_per_thread),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired:
            all_ok = False
            print(f"[rep {rep}] FAIL: worker subprocess timed out")
            continue
        if proc.returncode < 0:
            all_ok = False
            print(f"[rep {rep}] CRASHED — worker subprocess killed by signal {-proc.returncode} "
                  f"(this is exactly the SIGSEGV-class failure this test exists to catch)")
            if proc.stderr:
                print(proc.stderr[-4000:])
            continue
        summary = None
        for line in reversed(proc.stdout.strip().splitlines()) if proc.stdout.strip() else []:
            try:
                summary = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if summary is None:
            all_ok = False
            print(f"[rep {rep}] FAIL (exit {proc.returncode}): worker produced no parseable summary")
            print("stdout:", proc.stdout)
            print("stderr:", proc.stderr)
            continue
        ok = proc.returncode == 0 and summary.get("ok")
        all_ok = all_ok and ok
        print(f"[rep {rep}] {'PASS' if ok else 'FAIL'}: {json.dumps(summary)}")
    print("ALL STRESS REPETITIONS PASSED" if all_ok else "SOME STRESS REPETITIONS FAILED")
    return 0 if all_ok else 1


# ---------------------------------------------------------------------------
# Offline self-test — no network, no Claude, no writes to real workspace state
# ---------------------------------------------------------------------------

FIXTURES_DIR = ORCH_DIR / "fixtures" / "incoming"
_real_tracker_hash_before: Optional[str] = None


def _self_test() -> int:
    results = []

    def check(name, fn):
        try:
            fn()
            results.append((name, True, ""))
        except AssertionError as e:
            results.append((name, False, str(e)))
        except Exception as e:  # noqa: BLE001
            results.append((name, False, f"{type(e).__name__}: {e}"))

    # Safety net: prove this test never touches real production state.
    watched_real_dirs = [
        JOBS_INBOX, WORKSPACE_ROOT / "output", WORKSPACE_ROOT / "tracking",
        QUEUE_INCOMING_DIR, QUEUE_REJECTED_DIR,
    ]
    before_snapshot = {}
    for d in watched_real_dirs:
        before_snapshot[str(d)] = sorted(p.name for p in d.rglob("*")) if d.exists() else None
    frozen_snapshot_dir = WORKSPACE_ROOT / "config" / "active_resume_pipeline.json"
    before_frozen_hash = sha256_hex(frozen_snapshot_dir.read_bytes()) if frozen_snapshot_dir.exists() else None

    tmpdir = Path(tempfile.mkdtemp(prefix="rolling_queue_selftest_"))
    sandbox_db = tmpdir / "queue.db"
    sandbox_ready = tmpdir / "ready"
    sandbox_incoming = tmpdir / "incoming"
    sandbox_inbox = tmpdir / "inbox"
    sandbox_raw = tmpdir / "raw"
    sandbox_rejected = tmpdir / "rejected"
    sandbox_tracker = tmpdir / "job_application_tracker.xlsx"
    init_db(sandbox_db)

    def _copy_fixture(name: str, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / name
        shutil.copy(FIXTURES_DIR / name, dest)
        # Backdate mtime so drain_incoming_directory()'s stability check
        # doesn't skip a file that was "just written" a moment ago.
        old = time.time() - 5
        os.utime(dest, (old, old))
        return dest

    def t_sqlite_init():
        def op(conn):
            return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tables = _with_conn(sandbox_db, op)
        assert {"jobs", "status_history", "claude_metrics", "watcher_state"} <= tables
        cols = _with_conn(sandbox_db, lambda c: {r["name"] for r in c.execute("PRAGMA table_info(jobs)")})
        assert "source" in cols and "application_url" in cols and "dedup_source_job_composite" in cols

    def t_schema_migration_preserves_legacy_row():
        legacy_db = tmpdir / "legacy_queue.db"
        _OLD_SCHEMA = """
        CREATE TABLE jobs (
            job_key TEXT PRIMARY KEY, linkedin_job_id TEXT, raw_url TEXT, normalized_url TEXT,
            status TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0, company TEXT, role TEXT,
            inbox_path TEXT, raw_snapshot_path TEXT, resume_path TEXT, cover_letter_path TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, correction_rounds INTEGER NOT NULL DEFAULT 0,
            last_error TEXT, worker_id TEXT, claimed_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        """
        def make_legacy(conn):
            conn.executescript(_OLD_SCHEMA)
            ts = now_iso()
            conn.execute(
                "INSERT INTO jobs (job_key, linkedin_job_id, raw_url, normalized_url, status, priority, "
                "company, role, attempts, correction_rounds, created_at, updated_at) "
                "VALUES ('li-9900001234', '9900001234', 'https://www.linkedin.com/jobs/view/9900001234/', "
                "'https://www.linkedin.com/jobs/view/9900001234/', 'FAILED', 0, 'Example Robotics', 'Robotics SDE Intern', "
                "1, 0, ?, ?)", (ts, ts),
            )
            conn.commit()
        _with_conn(legacy_db, make_legacy)
        init_db(legacy_db)  # runs migrate_schema()
        row = _with_conn(legacy_db, lambda c: dict(c.execute("SELECT * FROM jobs WHERE job_key='li-9900001234'").fetchone()))
        assert row["company"] == "Example Robotics", "pre-existing row lost data during migration"
        assert row["status"] == "FAILED"
        assert row["source"] == "linkedin", f"legacy row was not backfilled with source: {row}"
        assert row["source_job_id"] == "9900001234"
        count = _with_conn(legacy_db, lambda c: c.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"])
        assert count == 1, "migration must not duplicate or drop the pre-existing row"

    def t_universal_schema_parsing():
        text = (FIXTURES_DIR / "linkedin_sample.md").read_text(encoding="utf-8")
        fm, body = universal_job.parse_frontmatter(text)
        assert fm["company"] == "Example Corp"
        assert fm["source"] == "linkedin"
        assert len(body) > universal_job.MIN_BODY_CHARS
        problems = universal_job.validate_schema(fm, body)
        assert problems == [], f"valid fixture reported problems: {problems}"

    def t_job_key_determinism():
        text = (FIXTURES_DIR / "linkedin_sample.md").read_text(encoding="utf-8")
        fm, body = universal_job.parse_frontmatter(text)
        canon = universal_job.canonicalize_url(fm.get("source_url"))
        key1 = universal_job.compute_job_key(fm, canon)
        key2 = universal_job.compute_job_key(fm, canon)
        assert key1 == key2 == "linkedin-4499001122", key1

        gh_text = (FIXTURES_DIR / "greenhouse_sample.md").read_text(encoding="utf-8")
        gh_fm, _ = universal_job.parse_frontmatter(gh_text)
        gh_key = universal_job.compute_job_key(gh_fm, universal_job.canonicalize_url(gh_fm.get("source_url")))
        assert gh_key == "greenhouse-7654321", gh_key
        assert gh_key != key1

        hs_text = (FIXTURES_DIR / "handshake_sample.md").read_text(encoding="utf-8")
        hs_fm, _ = universal_job.parse_frontmatter(hs_text)
        hs_key = universal_job.compute_job_key(hs_fm, universal_job.canonicalize_url(hs_fm.get("source_url")))
        assert hs_key == "harbor-logic-req-2026-0451", hs_key

    def t_four_source_fixtures_ingest():
        for name in ("linkedin_sample.md", "indeed_sample.md", "handshake_sample.md", "greenhouse_sample.md"):
            path = _copy_fixture(name, sandbox_incoming)
            result = process_incoming_file(
                path, db_path=sandbox_db, inbox_dir=sandbox_inbox, raw_dir=sandbox_raw,
                rejected_dir=sandbox_rejected, tracker_path=sandbox_tracker,
            )
            assert result["result"] == "INGESTED", f"{name}: {result}"
            assert not path.exists(), f"{name}: source file should be drained from queue/incoming"
        job_keys = {r["job_key"] for r in fetch_status_rows_snapshot(sandbox_db)}
        assert {"linkedin-4499001122", "indeed-abc123", "harbor-logic-req-2026-0451",
                "greenhouse-7654321"} <= job_keys, job_keys
        inbox_files = list(sandbox_inbox.glob("*.md"))
        assert len(inbox_files) == 4, f"expected 4 standardized inbox files, found {len(inbox_files)}"

    def t_dedup_across_sources():
        path = _copy_fixture("employer_site_duplicate_of_linkedin.md", sandbox_incoming)
        before = fetch_status_rows_snapshot(sandbox_db)
        result = process_incoming_file(
            path, db_path=sandbox_db, inbox_dir=sandbox_inbox, raw_dir=sandbox_raw,
            rejected_dir=sandbox_rejected, tracker_path=sandbox_tracker,
        )
        assert result["result"] == "DUPLICATE", result
        assert result["job_key"] == "linkedin-4499001122", result
        assert not path.exists(), "duplicate source file must still be drained from queue/incoming"
        after = fetch_status_rows_snapshot(sandbox_db)
        assert len(after) == len(before), "a duplicate must never create a second row"

        # Re-ingesting the exact same LinkedIn fixture again must also be a
        # no-op duplicate (exact job_key match), not a second row or crash.
        path2 = _copy_fixture("linkedin_sample.md", sandbox_incoming)
        result2 = process_incoming_file(
            path2, db_path=sandbox_db, inbox_dir=sandbox_inbox, raw_dir=sandbox_raw,
            rejected_dir=sandbox_rejected, tracker_path=sandbox_tracker,
        )
        assert result2["result"] == "DUPLICATE", result2
        after2 = fetch_status_rows_snapshot(sandbox_db)
        assert len(after2) == len(before), "re-ingesting the identical job must not create a new row"

        row = _fetch_job("linkedin-4499001122", db_path=sandbox_db)
        assert row["application_url"] == "https://www.example-corp.com/careers/apply/4499001122", (
            "direct employer application URL must be preserved/preferred"
        )
        known = json.loads(row["known_source_urls"] or "[]")
        assert any("linkedin.com" in u for u in known), known
        assert any("example-corp.com" in u for u in known), known

    def t_malformed_goes_to_rejected():
        path = _copy_fixture("malformed_missing_role.md", sandbox_incoming)
        result = process_incoming_file(
            path, db_path=sandbox_db, inbox_dir=sandbox_inbox, raw_dir=sandbox_raw,
            rejected_dir=sandbox_rejected, tracker_path=sandbox_tracker,
        )
        assert result["result"] == "REJECTED", result
        assert not path.exists()
        rejected_path = Path(result["path"])
        assert rejected_path.exists(), "malformed file must be preserved under queue/rejected, never discarded"
        error_path = rejected_path.with_name(rejected_path.name + ".error.txt")
        assert error_path.exists(), "a companion .error.txt must explain the rejection"
        error_text = error_path.read_text()
        assert "role" in error_text.lower()

    def t_tracker_rows_and_defaults():
        rows = tracker_xlsx.read_tracker_rows(sandbox_tracker)
        by_key = {r["Job Key"]: r for r in rows}
        assert "linkedin-4499001122" in by_key
        row = by_key["linkedin-4499001122"]
        assert row["Application Status"] == "NOT_APPLIED", (
            "Application Status must default to NOT_APPLIED and never be auto-set to APPLIED"
        )
        assert row["Pipeline Status"] == "NEW"
        assert row["Company"] == "Example Corp"
        assert row["Source"] == "linkedin"

    def t_tracker_preserves_user_fields():
        # Simulate the user (or a future human-facing tool) directly setting
        # these human-owned fields — exactly what config/
        # application_tracker_schema.md calls "Human-Owned Fields".
        tracker_xlsx.upsert_tracker_row(sandbox_tracker, "indeed-abc123", {
            "Application Status": "APPLIED", "Notes": "Referred by a friend",
        })
        before = {r["Job Key"]: r for r in tracker_xlsx.read_tracker_rows(sandbox_tracker)}["indeed-abc123"]
        assert before["Application Status"] == "APPLIED"
        assert before["Notes"] == "Referred by a friend"

        # A normal orchestrator pipeline-stage sync (e.g. GENERATING) never
        # includes human-owned headers in its `fields` dict (see
        # sync_tracker_for_job / _tracker_fields_from_job) — it must leave
        # both untouched.
        sync_tracker_for_job("indeed-abc123", db_path=sandbox_db, tracker_path=sandbox_tracker)
        after = {r["Job Key"]: r for r in tracker_xlsx.read_tracker_rows(sandbox_tracker)}["indeed-abc123"]
        assert after["Application Status"] == "APPLIED", "orchestrator sync must never touch Application Status"
        assert after["Notes"] == "Referred by a friend", "orchestrator sync must never touch Notes"
        assert not any(h in _tracker_fields_from_job(_fetch_job("indeed-abc123", db_path=sandbox_db))
                       for h in tracker_xlsx.HUMAN_OWNED_HEADERS), (
            "sync_tracker_for_job's field builder must never include a human-owned header"
        )

    def t_ready_to_apply_populates_tracker_paths():
        job_key = "linkedin-4499001122"
        _update_resume_paths(job_key, "output/x/resume.pdf", "output/x/cover_letter.pdf", db_path=sandbox_db)
        sync_tracker_for_job(job_key, db_path=sandbox_db, tracker_path=sandbox_tracker,
                              extra_fields={"Pipeline Status": READY_TO_APPLY, "Resume Created At": now_iso()})
        row = {r["Job Key"]: r for r in tracker_xlsx.read_tracker_rows(sandbox_tracker)}[job_key]
        assert row["Resume Path"] == "output/x/resume.pdf"
        assert row["Cover Letter Path"] == "output/x/cover_letter.pdf"
        assert row["Pipeline Status"] == READY_TO_APPLY
        assert row["Resume Created At"], "Resume Created At must be populated at READY_TO_APPLY"
        assert row["Application Status"] == "NOT_APPLIED", (
            "reaching READY_TO_APPLY must never automatically mark a job APPLIED"
        )

    def t_no_network_call_during_ingestion():
        import urllib.request
        called = {"hit": False}
        orig_urlopen = urllib.request.urlopen

        def guard(*args, **kwargs):
            called["hit"] = True
            raise AssertionError("ingestion must never make a network call")

        urllib.request.urlopen = guard
        try:
            path = _copy_fixture("linkedin_sample.md", sandbox_incoming)
            # Already ingested above under the same job_key -> exercises the
            # DUPLICATE path too, still with zero network calls either way.
            process_incoming_file(
                path, db_path=sandbox_db, inbox_dir=sandbox_inbox, raw_dir=sandbox_raw,
                rejected_dir=sandbox_rejected, tracker_path=sandbox_tracker,
            )
        finally:
            urllib.request.urlopen = orig_urlopen
        assert called["hit"] is False, "a network call was made during ingestion"
        assert not hasattr(sys.modules[__name__], "apify_fetch_by_job_id"), (
            "Apify scraping code must be removed from the source-agnostic orchestrator"
        )

    def t_state_transitions():
        def op(conn):
            ts = now_iso()
            conn.execute(
                "INSERT INTO jobs (job_key, source, company, role, status, priority, attempts, "
                "correction_rounds, created_at, updated_at) VALUES (?, 'test', 'T', 'R', ?, 0, 0, 0, ?, ?)",
                ("st-test-1", NEW, ts, ts),
            )
            conn.commit()
        _with_conn(sandbox_db, op)
        key = "st-test-1"
        transition(key, GENERATING, db_path=sandbox_db)
        transition(key, VALIDATING, db_path=sandbox_db)
        transition(key, FINALIZING, db_path=sandbox_db)
        transition(key, READY_TO_APPLY, db_path=sandbox_db)
        raised = False
        try:
            transition(key, GENERATING, db_path=sandbox_db)  # illegal: terminal state
        except ValueError:
            raised = True
        assert raised, "expected illegal transition out of READY_TO_APPLY to raise"

    def t_crash_recovery():
        def insert(job_key, status):
            def op(conn):
                ts = now_iso()
                conn.execute(
                    "INSERT INTO jobs (job_key, source, company, role, status, priority, attempts, "
                    "correction_rounds, created_at, updated_at) VALUES (?, 'test', 'T', 'R', ?, 0, 0, 0, ?, ?)",
                    (job_key, status, ts, ts),
                )
                conn.commit()
            _with_conn(sandbox_db, op)

        insert("cr-active", NEW)
        transition("cr-active", GENERATING, db_path=sandbox_db)

        insert("cr-ready", NEW)
        for st in (GENERATING, VALIDATING, FINALIZING, READY_TO_APPLY):
            transition("cr-ready", st, db_path=sandbox_db)

        recovered = recover_after_restart(sandbox_db)
        assert "cr-active" in recovered
        row_active = _fetch_job("cr-active", db_path=sandbox_db)
        assert row_active["status"] == QUEUED, row_active["status"]
        assert row_active["worker_id"] is None

        row_ready = _fetch_job("cr-ready", db_path=sandbox_db)
        assert row_ready["status"] == READY_TO_APPLY, "READY_TO_APPLY must never be requeued"

    def t_ready_marker():
        global READY_DIR  # noqa: PLW0603 — test-local redirection into sandbox
        saved = globals()["READY_DIR"]
        globals()["READY_DIR"] = sandbox_ready
        try:
            def op(conn):
                ts = now_iso()
                conn.execute(
                    "INSERT INTO jobs (job_key, source, company, role, resume_path, cover_letter_path, "
                    "status, priority, attempts, correction_rounds, created_at, updated_at) "
                    "VALUES ('rm-test', 'test', 'Acme', 'ML Intern', 'r.pdf', 'c.pdf', ?, 0, 0, 0, ?, ?)",
                    (NEW, ts, ts),
                )
                conn.commit()
            _with_conn(sandbox_db, op)
            for st in (GENERATING, VALIDATING, FINALIZING, READY_TO_APPLY):
                transition("rm-test", st, db_path=sandbox_db)
            release_ready("rm-test", db_path=sandbox_db)
            marker_path = sandbox_ready / "rm-test.json"
            assert marker_path.exists()
            data = json.loads(marker_path.read_text())
            assert data["company"] == "Acme"
            assert data["resume_path"] == "r.pdf"
        finally:
            globals()["READY_DIR"] = saved

    def t_incoming_watcher_latency():
        watch_db_path = tmpdir / "watch.db"
        init_db(watch_db_path)
        watch_incoming = tmpdir / "watch_incoming"
        watch_inbox = tmpdir / "watch_inbox"
        watch_raw = tmpdir / "watch_raw"
        watch_rejected = tmpdir / "watch_rejected"
        watch_tracker = tmpdir / "watch_tracker.xlsx"
        stop = threading.Event()
        watcher = IncomingWatcher(
            watch_incoming, stop, db_path=watch_db_path, inbox_dir=watch_inbox,
            raw_dir=watch_raw, rejected_dir=watch_rejected, tracker_path=watch_tracker,
        )
        thread = threading.Thread(target=watcher.run, daemon=True)
        thread.start()
        time.sleep(0.2)  # let the watcher establish itself
        start = time.time()
        watch_incoming.mkdir(parents=True, exist_ok=True)
        dropped = watch_incoming / "linkedin_sample.md"
        shutil.copy(FIXTURES_DIR / "linkedin_sample.md", dropped)
        old = time.time() - 5
        os.utime(dropped, (old, old))
        deadline = start + 6.0
        seen = False
        latency = None
        while time.time() < deadline:
            row = _with_conn(
                watch_db_path,
                lambda c: c.execute("SELECT 1 FROM jobs WHERE job_key = ?", ("linkedin-4499001122",)).fetchone(),
            )
            if row:
                seen = True
                latency = time.time() - start
                break
            time.sleep(0.05)
        stop.set()
        thread.join(timeout=2)
        assert seen, "IncomingWatcher did not detect the dropped file within 6s"
        results.append((
            f"incoming_watcher_latency ({'kqueue' if watcher.kqueue_supported else 'polling'})",
            True,
            f"{latency*1000:.0f}ms",
        ))

    def t_dotenv_loading():
        env_dir = tmpdir / "dotenv_test"
        env_dir.mkdir(parents=True, exist_ok=True)
        env_path = env_dir / ".env"
        secret_value = "fake-dotenv-secret-for-test-only"
        env_path.write_text(
            "# comment line\n"
            f"DOTENV_TEST_SECRET={secret_value}\n"
            "export DOTENV_TEST_PLAIN=hello world\n"
            "DOTENV_TEST_PRESET=overridden\n",
            encoding="utf-8",
        )
        for k in ("DOTENV_TEST_SECRET", "DOTENV_TEST_PLAIN", "DOTENV_TEST_PRESET"):
            os.environ.pop(k, None)
        os.environ["DOTENV_TEST_PRESET"] = "real-env-wins"

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            loaded = _load_dotenv(env_path)
        captured = buf.getvalue()

        try:
            assert loaded == 2, f"expected 2 new vars loaded, got {loaded}"
            assert os.environ.get("DOTENV_TEST_SECRET") == secret_value
            assert os.environ.get("DOTENV_TEST_PLAIN") == "hello world"
            assert os.environ.get("DOTENV_TEST_PRESET") == "real-env-wins", \
                "real environment variable must not be overridden by .env"
            assert secret_value not in captured, "secret value must never be printed/logged"
        finally:
            for k in ("DOTENV_TEST_SECRET", "DOTENV_TEST_PLAIN", "DOTENV_TEST_PRESET"):
                os.environ.pop(k, None)

    def t_status_readonly():
        before = fetch_status_rows_snapshot(sandbox_db)
        assert len(before) > 0, "expected jobs from earlier self-test steps"
        table = format_status_table(before)
        for col in STATUS_COLUMNS:
            assert col in table
        after = fetch_status_rows_snapshot(sandbox_db)
        assert before == after, "reading status must never mutate queue state"

    def t_sqlite_concurrency_stress():
        """Quick in-process smoke check that the per-operation-connection +
        SQLITE_LOCK design holds up under real concurrent threads hammering
        add/claim/transition against one temp DB. This runs in-process (a
        crash here fails the self-test loudly, which is the point). The
        full 5-repetition, subprocess-isolated version that specifically
        detects a SIGSEGV without taking down the caller is
        `python3 orchestrator/rolling_queue.py --stress-test`."""
        summary = _run_stress_repetition(n_threads=4, jobs_per_thread=6)
        assert not summary["errors"], f"stress test threads raised: {summary['errors']}"
        assert not summary["duplicate_keys"], "stress test produced duplicate job_keys"
        assert summary["actual_total"] == summary["expected_total"], (
            f"expected {summary['expected_total']} jobs, found {summary['actual_total']} "
            "(lost or duplicated primary keys)"
        )
        assert summary["ready_count"] == summary["expected_total"], "not every job reached READY_TO_APPLY"
        assert summary["non_terminal_count"] == 0, "some job(s) left in a non-terminal state"

    def t_worker_persistence_mocked():
        saved_workers_dir = globals()["WORKERS_DIR"]
        sandbox_workers = tmpdir / "workers"
        globals()["WORKERS_DIR"] = sandbox_workers
        orig_run = subprocess.run
        fake_job = {"job_key": "wp-0000000001", "source": "test", "source_job_id": "0000000001", "inbox_path": ""}

        class _FakeCompletedProcess:
            def __init__(self, returncode, stdout, stderr):
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        def fake_run_success(cmd, **kwargs):
            payload = {
                "total_cost_usd": 0.05,
                "usage": {"input_tokens": 100},
                "modelUsage": {},
                "session_id": "sandbox-session-1",
                "duration_ms": 1234,
                "is_error": False,
                "result": (
                    "work done\n"
                    'ORCHESTRATOR_RESULT: {"status": "PASS", '
                    '"resume_path": "output/x/resume.pdf", '
                    '"cover_letter_path": null, "reason": null}'
                ),
            }
            return _FakeCompletedProcess(0, json.dumps(payload), "")

        def fake_run_timeout(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=1, output="partial stdout", stderr="partial stderr")

        try:
            subprocess.run = fake_run_success
            result_ok = call_claude_worker(fake_job, "GENERATING", attempt=1)
            assert result_ok["success"] is True
            assert result_ok["session_id"] == "sandbox-session-1"
            job_dir = sandbox_workers / fake_job["job_key"]
            assert (job_dir / "claude_result.json").exists()
            assert (job_dir / "worker_status.json").exists()
            assert (job_dir / "worker.log").exists()
            status_ok = json.loads((job_dir / "worker_status.json").read_text())
            assert status_ok["success"] is True
            assert status_ok["claude_session_id"] == "sandbox-session-1"
            assert status_ok["job_key"] == fake_job["job_key"]

            subprocess.run = fake_run_timeout
            result_fail = call_claude_worker(fake_job, "GENERATING", attempt=2)
            assert result_fail["success"] is False
            status_fail = json.loads((job_dir / "worker_status.json").read_text())
            assert status_fail["success"] is False
            assert status_fail["failure_reason"]
            log_text = (job_dir / "worker.log").read_text()
            assert "attempt=1" in log_text and "attempt=2" in log_text, \
                "worker.log should retain history across attempts"
        finally:
            subprocess.run = orig_run
            globals()["WORKERS_DIR"] = saved_workers_dir

    check("sqlite_initialization", t_sqlite_init)
    check("schema_migration_preserves_legacy_row", t_schema_migration_preserves_legacy_row)
    check("universal_schema_parsing", t_universal_schema_parsing)
    check("job_key_determinism", t_job_key_determinism)
    check("four_source_fixtures_ingest", t_four_source_fixtures_ingest)
    check("dedup_across_sources", t_dedup_across_sources)
    check("malformed_goes_to_rejected", t_malformed_goes_to_rejected)
    check("tracker_rows_and_defaults", t_tracker_rows_and_defaults)
    check("tracker_preserves_user_fields", t_tracker_preserves_user_fields)
    check("ready_to_apply_populates_tracker_paths", t_ready_to_apply_populates_tracker_paths)
    check("no_network_call_during_ingestion", t_no_network_call_during_ingestion)
    check("state_transitions", t_state_transitions)
    check("crash_recovery", t_crash_recovery)
    check("ready_marker_generation", t_ready_marker)
    check("incoming_watcher_latency", t_incoming_watcher_latency)
    check("dotenv_loading", t_dotenv_loading)
    check("status_readonly", t_status_readonly)
    check("worker_persistence_mocked_success_and_failure", t_worker_persistence_mocked)
    check("sqlite_concurrency_stress_smoke", t_sqlite_concurrency_stress)

    # Final safety-net assertion: no real production state was touched.
    def t_no_production_side_effects():
        for d in watched_real_dirs:
            after = sorted(p.name for p in d.rglob("*")) if d.exists() else None
            assert after == before_snapshot[str(d)], f"{d} changed during self-test!"
        if frozen_snapshot_dir.exists():
            after_hash = sha256_hex(frozen_snapshot_dir.read_bytes())
            assert after_hash == before_frozen_hash, "config/active_resume_pipeline.json changed during self-test!"
        assert not TRACKER_XLSX_PATH.exists() or sha256_hex(TRACKER_XLSX_PATH.read_bytes()) == _real_tracker_hash_before, \
            "the real tracking/job_application_tracker.xlsx changed during self-test!"

    check("no_production_side_effects", t_no_production_side_effects)

    shutil.rmtree(tmpdir, ignore_errors=True)

    print()
    print("SELF-TEST RESULTS")
    print("-" * 50)
    all_passed = True
    for name, ok, detail in results:
        status = "PASS" if ok else "FAIL"
        if not ok:
            all_passed = False
        line = f"[{status}] {name}"
        if detail:
            line += f" — {detail}"
        print(line)
    print("-" * 50)
    print("ALL TESTS PASSED" if all_passed else "SOME TESTS FAILED")
    return 0 if all_passed else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Outer rolling-queue orchestrator (V8.5 pipeline stays frozen).")
    parser.add_argument("--workers", type=int, default=4, help="worker count, 1-5 (default 4)")
    parser.add_argument("--self-test", action="store_true", help="run the offline self-test and exit; no network/Claude calls")
    parser.add_argument("--status", action="store_true",
                         help="print a read-only queue status table and exit; never starts workers or modifies queue state")
    parser.add_argument("--stress-test", action="store_true",
                         help="run the SQLite concurrency stress test (multiple repetitions, temp DB only) and exit")
    parser.add_argument("--stress-test-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--repetitions", type=int, default=5, help="--stress-test: number of repetitions (default 5)")
    parser.add_argument("--threads", type=int, default=6, help="--stress-test: threads per repetition (default 6)")
    parser.add_argument("--jobs-per-thread", type=int, default=8,
                         help="--stress-test: jobs inserted per thread per repetition (default 8)")
    args = parser.parse_args()

    if args.stress_test_worker:
        sys.exit(_stress_test_worker_cli(args.threads, args.jobs_per_thread))

    if args.stress_test:
        sys.exit(run_stress_test(args.repetitions, args.threads, args.jobs_per_thread))

    if args.self_test:
        global _real_tracker_hash_before
        _real_tracker_hash_before = (
            sha256_hex(TRACKER_XLSX_PATH.read_bytes()) if TRACKER_XLSX_PATH.exists() else None
        )
        sys.exit(_self_test())

    if args.status:
        print_status()
        return

    print(
        "This orchestrator is implemented but has not been authorized to process a real "
        "job yet. Drop a standardized Markdown file into queue/incoming/ once you are ready "
        "— see orchestrator/README.md.",
        file=sys.stderr,
    )
    run_orchestrator(args.workers)


if __name__ == "__main__":
    main()
