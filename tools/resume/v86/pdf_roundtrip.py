#!/usr/bin/env python3
"""
Feature A -- deterministic PDF round-trip integrity diagnostics.

Answers one question about ONE artifact: does the text an ATS would
extract from the resume PDF still contain, in the right order, the
content the pipeline intended to ship?

Inputs are both existing job-local generated artifacts -- the rendered
`resume.pdf` and the `resume_draft.md` that `tools/resume/render_resume.py`
wrote from the very same Word document in the very same render step. No
candidate master file is consulted and nothing is reconstructed from
unrelated sources.

Reuse, not re-implementation:
  - PDF text comes from `tools/resume/qa_resume.extract_pdf_text`, the one
    extraction primitive `resume_qa.json` itself is built from, so this
    module and QA can never disagree about what the text layer says.
  - Expected content comes from `resume_draft_model.parse_resume_markdown`,
    shared with the short-bullet advisory.
  - The replacement-character VERDICT stays owned by `qa_resume.py`; the
    consolidated report prefers `resume_qa.json`'s own boolean and only
    falls back to the count measured here when no QA report exists (see
    `resume_quality_report.py`). What this module adds on top are the
    corruption signals QA does not measure (PDF `(cid:NN)` glyph markers,
    stray control characters) and the field-level recall/order analysis.

Normalization (`normalize_text`) folds exactly the things that make an
ordinary rendered line look different from its markdown source -- Unicode
compatibility variants (NFKC), soft hyphens and zero-width marks, the
dash family, markdown emphasis markers, and all whitespace including the
line wraps a PDF inserts mid-sentence. It deliberately does NOT touch
`+ # . / % -` inside a token or any digit, so `C++`, `C#`, `.NET`,
`CI/CD`, `p95`, `99.9%`, `480ms`, `0.71`, and `Python 3.11` survive
intact and a missing number can never be normalized away.

Matching is exact-normalized-substring first. Only when that fails is a
single documented fallback used: ordered-token coverage via
`difflib.SequenceMatcher(autojunk=False).get_matching_blocks()` over the
normalized token lists, which is order-preserving by construction. The
ratio threshold is configuration, is reported alongside every partial
result, and is a DIAGNOSTIC -- not a claim about what any real ATS does.

Nothing here blocks a job, requests a correction, or calls a model.
"""

from __future__ import annotations

import difflib
import re
import sys
import unicodedata
from pathlib import Path
from typing import Optional, Union

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import qa_resume  # noqa: E402  (also supplies the shared `fitz` dependency)

DEFAULT_PARTIAL_MIN_TOKEN_RATIO = 0.6
DEFAULT_MIN_READABLE_TEXT_CHARS = 200

REPLACEMENT_CHARACTER = "�"

# PyMuPDF emits `(cid:NN)` when a glyph has no usable ToUnicode mapping --
# the classic "the PDF looks fine but extracts as garbage" signature.
_CID_MARKER = re.compile(r"\(cid:\d+\)")

# Characters that should never survive into an extracted resume line.
# Tab/newline/carriage-return are ordinary layout characters here and are
# excluded from the scan.
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_ZERO_WIDTH = dict.fromkeys(
    ord(c) for c in ("­", "​", "‌", "‍", "⁠", "﻿")
)

_DASH_VARIANTS = {
    ord("‐"): "-",  # hyphen
    ord("‑"): "-",  # non-breaking hyphen
    ord("‒"): "-",  # figure dash
    ord("–"): "-",  # en dash
    ord("—"): "-",  # em dash
    ord("―"): "-",  # horizontal bar
    ord("−"): "-",  # minus sign
}

_MARKDOWN_MARKERS = re.compile(r"\*\*|__|`")
_LEADING_BULLET = re.compile(r"^\s*[-•]\s+")
_HYPHEN_RUN = re.compile(r"-{2,}")
_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Fold presentation-only differences between intended content and
    extracted PDF text. Never removes a digit, a unit, or any of the
    in-token punctuation that distinguishes real technical strings."""
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text))
    folded = folded.translate(_ZERO_WIDTH)
    folded = folded.translate(_DASH_VARIANTS)
    folded = _MARKDOWN_MARKERS.sub("", folded)
    folded = _LEADING_BULLET.sub("", folded)
    # "--" in the markdown source and "—" in the rendered document are the
    # same visible separator; collapse every hyphen run to one hyphen on
    # BOTH sides so the comparison is symmetric.
    folded = _HYPHEN_RUN.sub("-", folded)
    # Collapsing all whitespace (including newlines) is what makes an
    # ordinary PDF line wrap stop looking like missing text.
    folded = _WHITESPACE.sub(" ", folded)
    return folded.strip().casefold()


def tokenize(normalized: str) -> list:
    """Whitespace tokens of already-normalized text. Tokens keep their
    internal punctuation, so `c++`, `ci/cd`, `.net`, `99.9%` and `480ms`
    are each one comparable token."""
    return normalized.split() if normalized else []


def ordered_token_coverage(expected_tokens: list, haystack_tokens: list) -> float:
    """Fraction of `expected_tokens` recovered from `haystack_tokens` in
    order. `difflib.SequenceMatcher`'s matching blocks are strictly
    increasing in both sequences, so token order is preserved by
    construction; `autojunk=False` keeps the result deterministic for
    long documents (the heuristic would otherwise start ignoring popular
    tokens once the haystack passes 200 items)."""
    if not expected_tokens:
        return 0.0
    matcher = difflib.SequenceMatcher(None, expected_tokens, haystack_tokens, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return round(min(matched / len(expected_tokens), 1.0), 4)


def measure_text_layer(pdf_text: str, min_readable_chars: int) -> dict:
    stripped = (pdf_text or "").strip()
    return {
        "extracted_chars": len(stripped),
        "min_readable_chars": min_readable_chars,
        "empty": not stripped,
        "effectively_unreadable": len(stripped) < min_readable_chars,
    }


def measure_glyph_corruption(pdf_text: str) -> dict:
    text = pdf_text or ""
    replacement = text.count(REPLACEMENT_CHARACTER)
    cid_markers = len(_CID_MARKER.findall(text))
    control = len(_CONTROL_CHARACTERS.findall(text))
    return {
        "replacement_characters": replacement,
        "cid_glyph_markers": cid_markers,
        "control_characters": control,
        "corrupt": bool(replacement or cid_markers or control),
    }


def check_fields(expected_fields: list, pdf_text: str, partial_min_ratio: float) -> list:
    """Field-level recall. Every expected field gets its own row with its
    own identifier -- never a single whole-document verdict."""
    haystack = normalize_text(pdf_text)
    haystack_tokens = tokenize(haystack)
    results = []

    for field in expected_fields:
        normalized = normalize_text(field.get("text", ""))
        row = {
            "id": field.get("id"),
            "kind": field.get("kind"),
            "section": field.get("section"),
            "substantive": bool(field.get("substantive")),
            "expected_chars": len(field.get("text", "") or ""),
            "status": "not_evaluated",
            "match_ratio": None,
            "observed_offset": None,
            "reason": None,
        }

        if not normalized:
            row["reason"] = "expected field text is empty after normalization"
            results.append(row)
            continue

        offset = haystack.find(normalized)
        if offset >= 0:
            row["status"] = "found"
            row["match_ratio"] = 1.0
            row["observed_offset"] = offset
            results.append(row)
            continue

        expected_tokens = tokenize(normalized)
        ratio = ordered_token_coverage(expected_tokens, haystack_tokens)
        row["match_ratio"] = ratio
        row["partial_min_ratio"] = partial_min_ratio

        if ratio >= partial_min_ratio:
            # Deliberately left UNANCHORED (`observed_offset is None`): a
            # partially recovered field has no single trustworthy position
            # in the extracted text, and guessing one (say, the first
            # occurrence of its first token, which may belong to an
            # entirely different line) would manufacture reading-order
            # inversions that are not real. Order fidelity is measured
            # from exactly-matched fields only.
            row["status"] = "partial"
        else:
            row["status"] = "missing"

        results.append(row)

    return results


def check_reading_order(field_results: list) -> dict:
    """Compare the document order of the fields we could anchor in the
    extracted text against the order they actually appear at.

    Only exactly-matched (`found`) fields are anchored -- those are the
    ones whose position in the extracted text is unambiguous. Reported as
    order fidelity plus the specific affected elements. A multi-column
    template is NOT assumed to be ATS-hostile: this only reports what THIS
    extraction of THIS artifact actually did.
    """
    anchored = [
        row for row in field_results
        if row.get("observed_offset") is not None and row.get("status") == "found"
    ]

    if len(anchored) < 2:
        return {
            "status": "not_evaluated",
            "reason": "fewer than two expected fields could be anchored in the extracted text",
            "anchored_fields": len(anchored),
            "inversions": 0,
            "order_fidelity": None,
            "misordered_elements": [],
        }

    total_pairs = 0
    inversions = 0
    for i in range(len(anchored)):
        for j in range(i + 1, len(anchored)):
            total_pairs += 1
            if anchored[i]["observed_offset"] > anchored[j]["observed_offset"]:
                inversions += 1

    misordered = []
    for i in range(len(anchored) - 1):
        first, second = anchored[i], anchored[i + 1]
        if first["observed_offset"] > second["observed_offset"]:
            misordered.append({
                "expected_before": first["id"],
                "expected_after": second["id"],
                "observed_offset_before": first["observed_offset"],
                "observed_offset_after": second["observed_offset"],
            })

    return {
        "status": "evaluated",
        "reason": None,
        "anchored_fields": len(anchored),
        "inversions": inversions,
        "order_fidelity": round(1.0 - (inversions / total_pairs), 4) if total_pairs else None,
        "misordered_elements": misordered,
    }


def analyze(
    pdf_text: str,
    expected_fields: list,
    partial_min_ratio: float = DEFAULT_PARTIAL_MIN_TOKEN_RATIO,
    min_readable_chars: int = DEFAULT_MIN_READABLE_TEXT_CHARS,
) -> dict:
    """Pure function over already-extracted text + already-parsed expected
    fields. No I/O -- directly unit-testable without producing a PDF."""
    text_layer = measure_text_layer(pdf_text, min_readable_chars)
    glyphs = measure_glyph_corruption(pdf_text)

    if text_layer["effectively_unreadable"]:
        # A broken/absent text layer must not cascade into dozens of false
        # "missing bullet" findings -- the honest answer is that recall
        # could not be evaluated at all.
        fields = [
            {
                "id": f.get("id"), "kind": f.get("kind"), "section": f.get("section"),
                "substantive": bool(f.get("substantive")),
                "expected_chars": len(f.get("text", "") or ""),
                "status": "not_evaluated", "match_ratio": None, "observed_offset": None,
                "reason": "pdf text layer is empty or effectively unreadable",
            }
            for f in expected_fields
        ]
        order = {
            "status": "not_evaluated",
            "reason": "pdf text layer is empty or effectively unreadable",
            "anchored_fields": 0, "inversions": 0,
            "order_fidelity": None, "misordered_elements": [],
        }
    else:
        fields = check_fields(expected_fields, pdf_text, partial_min_ratio)
        order = check_reading_order(fields)

    summary = {"found": 0, "partial": 0, "missing": 0, "not_evaluated": 0}
    substantive_summary = dict(summary)
    for row in fields:
        summary[row["status"]] = summary.get(row["status"], 0) + 1
        if row["substantive"]:
            substantive_summary[row["status"]] = substantive_summary.get(row["status"], 0) + 1

    return {
        "status": "EVALUATED",
        "reason": None,
        "text_layer": text_layer,
        "glyph_corruption": glyphs,
        "fields": fields,
        "recall_summary": summary,
        "substantive_recall_summary": substantive_summary,
        "reading_order": order,
        "partial_min_token_ratio": partial_min_ratio,
    }


def not_evaluated(reason: str) -> dict:
    """The documented unevaluated shape -- returned instead of raising
    whenever an input artifact is missing or unreadable."""
    return {
        "status": "NOT_EVALUATED",
        "reason": reason,
        "text_layer": None,
        "glyph_corruption": None,
        "fields": [],
        "recall_summary": {"found": 0, "partial": 0, "missing": 0, "not_evaluated": 0},
        "substantive_recall_summary": {"found": 0, "partial": 0, "missing": 0, "not_evaluated": 0},
        "reading_order": {
            "status": "not_evaluated", "reason": reason, "anchored_fields": 0,
            "inversions": 0, "order_fidelity": None, "misordered_elements": [],
        },
        "partial_min_token_ratio": None,
    }


def run(
    pdf_path: Union[str, Path, None],
    expected_fields: Optional[list],
    partial_min_ratio: float = DEFAULT_PARTIAL_MIN_TOKEN_RATIO,
    min_readable_chars: int = DEFAULT_MIN_READABLE_TEXT_CHARS,
) -> dict:
    """File-level entry point. Never raises: a missing PDF, an unreadable
    PDF, or a missing expected-content source each return the documented
    `NOT_EVALUATED` shape with a concise reason."""
    if pdf_path is None:
        return not_evaluated("no resume PDF path was provided")

    resolved = Path(pdf_path)
    if not resolved.exists():
        return not_evaluated(f"resume PDF not found: {resolved.name}")

    if not expected_fields:
        return not_evaluated(
            "no expected-content source available (resume_draft.md missing or empty)"
        )

    try:
        pdf_text, _pages = qa_resume.extract_pdf_text(str(resolved))
    except Exception as exc:  # malformed/locked PDF -- diagnose, never crash
        return not_evaluated(f"could not extract text from the resume PDF: {type(exc).__name__}")

    return analyze(
        pdf_text, expected_fields,
        partial_min_ratio=partial_min_ratio,
        min_readable_chars=min_readable_chars,
    )
