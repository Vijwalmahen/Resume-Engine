#!/usr/bin/env python3
"""
V8.6 -- deterministic (no-Claude) resume/cover-letter finalizer.

Replaces the Claude-agent-driven `finalize-resume` skill invocation for
NORMAL finalization. This script talks directly to the already-running
watermark-removal HTTP service (the same one `.claude/skills/remove-ai-marks`
documents: GET /health, GET /capabilities, POST /inspect, POST /clean) and
performs the same required safety checks the skill's SKILL.md mandates:

  - health-check the service (the ONE-per-batch check is done by the
    caller, tools/resume/v86/run_batch.py, and passed in via
    --health-status so this script never re-checks it per job/file)
  - Layer B stays disabled (never sent)
  - originals are preserved before any overwrite
  - substantive text must be identical before/after cleaning
  - resume must remain exactly one page after cleaning
  - promotion only happens after all checks pass; on any failure the
    original is kept and the artifact-level status is FAILED/PARTIAL
  - never launches Docker, another watermark service, or retries a given
    artifact more than once

Run with the deterministic-python interpreter (fitz / python-docx are
needed here, exactly like tools/resume/qa_resume.py and
tools/resume/render_resume.py already require) -- see
tools/resume/v86/common.py DETERMINISTIC_PYTHON.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ARTIFACT_NAMES = [
    "resume.docx",
    "resume.pdf",
    "cover_letter.docx",
    "cover_letter.pdf",
]

PREFERRED_CLEAN_OPTIONS = {
    "detect_before": True,
    "detect_after": True,
    "also_layer_a_text": True,
    "keep_non_ai_metadata": True,
    "strip_all_metadata": False,
    "deep_images": "auto",
}


def _http_json(url: str, payload: dict = None, timeout: float = 30.0) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def check_health(base_url: str) -> bool:
    try:
        result = _http_json(f"{base_url}/health", timeout=5.0)
        return bool(result.get("ok"))
    except Exception:
        return False


def get_capabilities(base_url: str) -> dict:
    try:
        return _http_json(f"{base_url}/capabilities", timeout=5.0)
    except Exception:
        return {}


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        import fitz
        doc = fitz.open(str(path))
        try:
            return "\n".join(page.get_text() for page in doc)
        finally:
            doc.close()
    if suffix == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    raise ValueError(f"Unsupported artifact type: {path}")


def page_count(pdf_path: Path) -> int:
    import fitz
    doc = fitz.open(str(pdf_path))
    try:
        return doc.page_count
    finally:
        doc.close()


def normalize_text(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKC", text)
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def finalize_one_artifact(path: Path, base_url: str, capabilities: dict, work_dir: Path) -> dict:
    record = {"file": str(path.name), "status": None}

    if not path.exists():
        record["status"] = "SKIPPED_MISSING"
        return record

    before_text = normalize_text(extract_text(path))
    before_pages = page_count(path) if path.suffix.lower() == ".pdf" else None
    record["before_pages"] = before_pages

    originals_dir = work_dir / "finalize_originals"
    originals_dir.mkdir(parents=True, exist_ok=True)
    original_copy = originals_dir / (path.stem + ".original" + path.suffix)
    if not original_copy.exists():
        shutil.copy2(path, original_copy)

    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode()

    try:
        inspect_before = _http_json(f"{base_url}/inspect", {"file": b64, "name": path.name})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        record["status"] = "SERVICE_UNAVAILABLE"
        record["error"] = str(exc)
        return record

    options = dict(PREFERRED_CLEAN_OPTIONS)
    try:
        clean_result = _http_json(f"{base_url}/clean", {"file": b64, "name": path.name, "options": options})
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        record["status"] = "FAILED"
        record["error"] = f"/clean call failed: {exc}"
        record["inspect_before"] = inspect_before
        return record

    if not clean_result.get("ok"):
        record["status"] = "FAILED"
        record["error"] = "clean reported ok=false"
        record["inspect_before"] = inspect_before
        record["clean_report"] = clean_result.get("report")
        return record

    cleaned_b64 = clean_result.get("cleaned")
    if not cleaned_b64:
        record["status"] = "FAILED"
        record["error"] = "clean response missing cleaned bytes"
        return record

    cleaned_bytes = base64.b64decode(cleaned_b64)
    cleaned_dir = work_dir / "finalize_cleaned"
    cleaned_dir.mkdir(parents=True, exist_ok=True)
    cleaned_path = cleaned_dir / (path.stem + ".cleaned" + path.suffix)
    cleaned_path.write_bytes(cleaned_bytes)

    try:
        after_text = normalize_text(extract_text(cleaned_path))
    except Exception as exc:
        record["status"] = "FAILED"
        record["error"] = f"could not extract cleaned text: {exc}"
        return record

    text_identical = after_text == before_text
    record["text_identical"] = text_identical

    after_pages = None
    if path.suffix.lower() == ".pdf":
        try:
            after_pages = page_count(cleaned_path)
        except Exception:
            after_pages = None
        record["after_pages"] = after_pages

    page_count_ok = True
    is_resume = path.stem == "resume"
    if is_resume and path.suffix.lower() == ".pdf":
        page_count_ok = (before_pages == 1 and after_pages == 1)

    try:
        inspect_after = _http_json(f"{base_url}/inspect", {"file": cleaned_b64, "name": path.name})
    except Exception:
        inspect_after = None

    record["inspect_before"] = inspect_before
    record["inspect_after"] = inspect_after
    record["clean_report"] = clean_result.get("report")

    if text_identical and page_count_ok:
        shutil.copy2(cleaned_path, path)  # promote
        record["status"] = "PASS"
    else:
        record["status"] = "FAILED"
        record["reason"] = (
            "text_changed" if not text_identical else "page_count_changed"
        )
        # original path was never overwritten -- original preserved.

    return record


def finalize_job(job_dir: Path, base_url: str, health_status: str) -> dict:
    if health_status != "healthy":
        return {
            "job": job_dir.name,
            "overall_status": "SERVICE_UNAVAILABLE",
            "artifacts": [],
            "layer_b": "NOT_RUN",
        }

    capabilities = get_capabilities(base_url)
    work_dir = job_dir / ".work"
    work_dir.mkdir(parents=True, exist_ok=True)

    artifacts = []
    for name in ARTIFACT_NAMES:
        path = job_dir / name
        artifacts.append(finalize_one_artifact(path, base_url, capabilities, work_dir))

    statuses = {a["status"] for a in artifacts if a["status"] != "SKIPPED_MISSING"}
    if not statuses:
        overall = "FAILED"
    elif statuses == {"PASS"}:
        overall = "FINALIZED"
    elif "PASS" in statuses:
        overall = "PARTIAL"
    else:
        overall = "FAILED"

    record = {
        "job": job_dir.name,
        "overall_status": overall,
        "artifacts": artifacts,
        "layer_b": "NOT_RUN",
    }

    (job_dir / "watermark_finalization.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False) + "\n"
    )
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--health-status", required=True, choices=["healthy", "unavailable"],
        help="Pre-computed once per batch by run_batch.py -- see module docstring.",
    )
    args = parser.parse_args()

    record = finalize_job(Path(args.job_dir), args.base_url, args.health_status)
    print(json.dumps(record, indent=2, ensure_ascii=False))
    if record["overall_status"] not in ("FINALIZED",):
        sys.exit(0 if record["overall_status"] in ("PARTIAL", "SERVICE_UNAVAILABLE") else 1)


if __name__ == "__main__":
    main()
