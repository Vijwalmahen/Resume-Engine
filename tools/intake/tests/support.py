#!/usr/bin/env python3
"""
Shared offline-test scaffolding for `tools/intake/tests/`.

Two things every suite here needs:

  1. **Network has to be impossible, not merely unused.** `block_network()`
     replaces `socket.socket`, `socket.create_connection` and
     `socket.getaddrinfo` with versions that raise, so a test that would have
     reached the network fails loudly instead of quietly succeeding on
     somebody's machine and quietly costing money on somebody else's.

  2. **Synthetic everything.** The fixtures below are invented job postings
     for invented companies at invented URLs, and the only credential that
     appears anywhere in these tests is the obviously-fake
     `fake-apify-token-for-test-only`.
"""

from __future__ import annotations

import socket
import ssl  # noqa: F401 -- imported BEFORE block_network() rebinds socket.socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
INTAKE_DIR = ROOT / "tools" / "intake"
sys.path.insert(0, str(INTAKE_DIR))
sys.path.insert(0, str(ROOT / "tools" / "resume" / "v86"))

FAKE_TOKEN = "fake-apify-token-for-test-only"


class NetworkAccessAttempted(AssertionError):
    """Raised if any code under test tries to open a socket."""


def block_network() -> dict:
    """Make network access impossible for this process. Returns the originals
    so a suite could restore them (none of them do).

    `socket.socket` is replaced by a SUBCLASS that raises on construction
    rather than by a plain function: `ssl.SSLSocket` subclasses whatever
    `socket.socket` is at import time, so a non-class stand-in breaks any
    stdlib module imported later (xml.sax.saxutils -> urllib -> http.client
    -> ssl, for instance)."""
    originals = {
        "socket": socket.socket,
        "create_connection": socket.create_connection,
        "getaddrinfo": socket.getaddrinfo,
    }

    def _blocked(*args, **kwargs):
        raise NetworkAccessAttempted(
            "network access is blocked in tools/intake/tests: no test may open a socket"
        )

    class _BlockedSocket(originals["socket"]):
        def __init__(self, *args, **kwargs):
            _blocked()

    socket.socket = _BlockedSocket
    socket.create_connection = _blocked
    socket.getaddrinfo = _blocked
    return originals


def synthetic_description(company: str = "Example AI Labs", chars: int = 900) -> str:
    """A job-description body long enough to clear the completeness floor,
    with no real employer, person, or posting behind it."""
    base = (
        f"{company} is hiring a Machine Learning Engineer Intern to build and evaluate "
        "production-oriented retrieval and inference systems. You will build Python "
        "services, design evaluation workflows, and work with structured and unstructured "
        "data alongside engineers moving prototypes toward production. Requirements include "
        "strong Python programming skills, familiarity with machine learning or natural "
        "language processing, and clear written communication. "
    )
    text = base
    while len(text) < chars:
        text += base
    return text[:chars]


class TempDirTestCase(unittest.TestCase):
    """Every filesystem artifact a suite creates lives in its own temp dir and
    is removed afterwards. Nothing here ever touches the repository's real
    `jobs/`, `output/` or `tracking/` contents."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="intake-test-")
        self.tmp = Path(self._tmp.name)
        self.inbox = self.tmp / "batch_inbox"
        self.done = self.tmp / "batch_done"
        self.output = self.tmp / "output"
        self.tracking = self.tmp / "tracking"
        for d in (self.inbox, self.done, self.output, self.tracking):
            d.mkdir(parents=True, exist_ok=True)
        self.tracker = self.tracking / "job_application_tracker.xlsx"

    def tearDown(self):
        self._tmp.cleanup()

    def make_tracker(self):
        import intake_tracker

        intake_tracker.create_workbook_with_applications(self.tracker)
        return self.tracker


def make_synthetic_batch_dir(batch_dir: Path) -> Path:
    """A V8.6 batch directory built from PUBLIC config plus invented candidate
    data, so `build_job_packet.build_packet` — the production packet builder —
    can be exercised for real without reading anybody's private runtime
    context.

    Mirrors exactly what `build_shared_context.build` writes: the shared
    candidate context, the three payload shapes, the project-selection policy,
    and the resume template layout.
    """
    import json

    batch_dir = Path(batch_dir)
    batch_dir.mkdir(parents=True, exist_ok=True)

    shared_context = {
        "schema_version": "synthetic-test",
        "candidate": {"name": "Example Candidate", "email": "candidate@example.com"},
        "education": [],
        "experience": [],
        "projects": [
            {"id": "project_selectable_1", "name": "Synthetic Retrieval Service",
             "project_status": "selectable"},
        ],
        "skills": {},
        "truth_constraints": [],
    }
    (batch_dir / "shared_candidate_context.json").write_text(
        json.dumps(shared_context, indent=2), encoding="utf-8")

    shapes = {
        name: json.loads((ROOT / "config" / "v8" / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("resume_payload_shape", "jd_requirements_shape", "cover_letter_payload_shape")
    }
    (batch_dir / "shapes.json").write_text(json.dumps(shapes, indent=2), encoding="utf-8")

    policy = (ROOT / "config" / "v8" / "project_selection_policy.json").read_text(encoding="utf-8")
    (batch_dir / "project_selection_policy.json").write_text(policy, encoding="utf-8")

    layout = (ROOT / "reference" / "resume_template_layout.example.json").read_text(encoding="utf-8")
    (batch_dir / "resume_template_layout.json").write_text(layout, encoding="utf-8")

    return batch_dir
