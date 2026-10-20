#!/usr/bin/env python3
"""
V8.6 shared helpers.

This module is NEW V8.6 code. It does not modify, import from, or depend on
any behavior change in the frozen V8.5 files. It only *invokes* the frozen
V8.5 deterministic scripts as unmodified subprocesses (exactly like V8.5's
own tools/resume/v8/run_local_resume_pipeline.py already does), and provides
generic plumbing (timing, locking, atomic IO, subprocess wrapper) used by the
rest of tools/resume/v86/.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def now_ms() -> float:
    """Monotonic milliseconds, for duration math (not wall-clock display)."""
    return time.monotonic() * 1000.0


class Timer:
    """Records start/end wall-clock ISO timestamps and a duration in ms."""

    def __init__(self):
        self.start_iso: Optional[str] = None
        self.end_iso: Optional[str] = None
        self._t0: Optional[float] = None
        self.duration_ms: Optional[float] = None

    def __enter__(self):
        self.start_iso = now_iso()
        self._t0 = now_ms()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.end_iso = now_iso()
        self.duration_ms = round(now_ms() - self._t0, 1)
        return False

    def as_dict(self, prefix: str) -> dict:
        return {
            f"{prefix}_start": self.start_iso,
            f"{prefix}_end": self.end_iso,
            f"{prefix}_duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# JSON / filesystem helpers
# ---------------------------------------------------------------------------

def load_json(path) -> Any:
    return json.loads(Path(path).read_text())


def write_json(path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)  # atomic on same filesystem


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def job_key_for(job_file: Path) -> str:
    """Stable job key derived from the job posting filename (its stem)."""
    return job_file.stem


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "job"


@contextlib.contextmanager
def file_lock(lock_path: Path):
    """A small exclusive advisory lock, reusing the same fcntl.flock pattern
    the frozen V8.5 renderer already uses for the Word-conversion lock
    (tools/resume/render_resume.py / tools/resume/word_to_pdf.py). This is a
    NEW lock file for a NEW purpose (tracker writes); it does not touch or
    reuse the Word-conversion lock's file, but follows the same safe
    exclusive-lock pattern.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Subprocess wrapper for invoking frozen V8.5 deterministic scripts unmodified
# ---------------------------------------------------------------------------

class StepResult:
    def __init__(self, returncode: int, stdout: str, stderr: str, duration_ms: float):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_step(cmd: list, cwd: Path = ROOT) -> StepResult:
    """Run a deterministic (non-LLM) subprocess step, timed. Never raises on
    nonzero exit -- callers check .ok / .returncode themselves, matching the
    frozen V8.5 pipeline's own `check=False` pattern so one job's failure
    can be handled without crashing the batch."""
    cmd = [str(x) for x in cmd]
    t0 = now_ms()
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    dt = round(now_ms() - t0, 1)
    return StepResult(result.returncode, result.stdout, result.stderr, dt)


def _resolve_deterministic_python() -> str:
    """The frozen V8.5 deterministic scripts (and their exact CLI contract)
    require packages (PyMuPDF/`fitz`, python-docx, openpyxl) that are only
    installed for the system `python3` in this environment, NOT for
    `python3.13`. V8.5 has always been invoked as plain `python3 <script>`
    (see every command in the frozen SKILL.md files and
    config/active_resume_pipeline.json); V8.6 subprocess calls to those same
    scripts must use the SAME interpreter regardless of which interpreter
    launched the V8.6 orchestrator itself. See tools/resume/v86/README.md,
    "Python interpreter note" for the full explanation of why this
    deliberately does NOT use sys.executable.
    """
    import os
    import shutil

    override = os.environ.get("V86_DETERMINISTIC_PYTHON")
    if override:
        return override
    return shutil.which("python3") or "python3"


DETERMINISTIC_PYTHON = _resolve_deterministic_python()


def python_cmd(*parts) -> list:
    """Build a subprocess command line for a frozen/adapted deterministic
    V8.5/V8.6 script, using the interpreter that actually has the packages
    those scripts need (see _resolve_deterministic_python above)."""
    return [DETERMINISTIC_PYTHON, *[str(p) for p in parts]]


# ---------------------------------------------------------------------------
# Batch id
# ---------------------------------------------------------------------------

def new_batch_id() -> str:
    return "batch-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
