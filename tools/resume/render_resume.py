#!/usr/bin/env python3
"""
Generic, layout-driven resume renderer.

This script never encodes any candidate's identity, employer, university,
or project name. It edits a user-supplied Word template
(reference/resume_template.docx) at the exact paragraph indices declared
in a user-supplied layout file (reference/resume_template_layout.json;
see reference/resume_template_layout.example.json and
docs/candidate-data-guide.md for the contract). Every dynamic value comes
from the candidate's own resume_payload.json, keyed by the same
`owner_id` strings the layout uses to identify each block.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from docx import Document
from docx.shared import Pt
from docx.oxml.ns import qn

sys.path.insert(0, str(Path(__file__).resolve().parent))
import template_layout as layout_lib  # noqa: E402


ROOT = Path(__file__).resolve().parents[2]

DEFAULT_TEMPLATE = ROOT / "reference" / "resume_template.docx"
DEFAULT_LAYOUT = ROOT / "reference" / "resume_template_layout.json"


# Used ONLY when the template paragraph being rewritten declares no
# typography of its own (e.g. it inherits everything from a style). These
# match this repository's documented default layout (config/resume_layout.md)
# and are a last resort -- the template, not this constant, is the source of
# truth for appearance whenever it says anything at all.
FALLBACK_FONT_NAME = "Calibri"
FALLBACK_BODY_SIZE_PT = 10.5
FALLBACK_DESCRIPTOR_SIZE_PT = 10.0


def _style_typography(style, _depth: int = 0) -> tuple:
    """(font name, size) a paragraph style declares, following
    `base_style` inheritance. Returns (None, None) when the chain
    declares neither."""
    name = size = None
    while style is not None and _depth < 10:
        font = getattr(style, "font", None)
        if font is not None:
            if name is None:
                name = font.name
            if size is None:
                size = font.size
        if name is not None and size is not None:
            break
        style = getattr(style, "base_style", None)
        _depth += 1
    return name, size


def paragraph_typography(paragraph) -> dict:
    """The run-level typography the TEMPLATE already declares here.

    `layout_lib.clear_content` keeps `pPr`, so paragraph-level design
    (style, indents, spacing, borders, tab stops) always survives a
    rewrite. Run-level formatting does not: the runs are deleted and new
    ones appended. Previously the replacements were hardcoded to Calibri
    at a fixed point size, which silently re-typeset every dynamically
    written paragraph -- fine for a Calibri template, but on any other
    template the tailored bullets would render in a different face and
    size from the protected ones that are reordered as copied XML. That
    is precisely the "wrong font / inconsistent sizing" class of defect.

    So the template is read first. `body` describes the paragraph's main
    text and `descriptor` its secondary italic span (the project
    technology line), each falling back to the documented default only
    when the template declares nothing.
    """
    body_name = body_size = None
    descriptor_name = descriptor_size = None

    for run in paragraph.runs:
        if not run.text.strip():
            continue
        if run.italic and descriptor_name is None and descriptor_size is None:
            descriptor_name, descriptor_size = run.font.name, run.font.size
        elif body_name is None and body_size is None:
            body_name, body_size = run.font.name, run.font.size

    # A paragraph whose only run is italic still defines the body face.
    if body_name is None and body_size is None:
        body_name, body_size = descriptor_name, descriptor_size

    # Most real .docx templates declare typography on the STYLE, not on
    # each run, in which case every `run.font.*` read above is None.
    # Resolving only run-level formatting would therefore fall through to
    # the Calibri fallback for exactly the templates that are built the
    # normal way -- reintroducing the imposed-font defect this function
    # exists to prevent. So the paragraph's style chain is consulted
    # next, walking `base_style` because a style commonly inherits its
    # face from `Normal` rather than restating it.
    style_name, style_size = _style_typography(paragraph.style)
    body_name = body_name or style_name
    body_size = body_size or style_size
    descriptor_name = descriptor_name or style_name

    return {
        "body": {
            "name": body_name or FALLBACK_FONT_NAME,
            "size": body_size or Pt(FALLBACK_BODY_SIZE_PT),
        },
        "descriptor": {
            "name": descriptor_name or body_name or FALLBACK_FONT_NAME,
            "size": descriptor_size or Pt(FALLBACK_DESCRIPTOR_SIZE_PT),
        },
    }


def set_font(run, typography, bold=False, italic=False):
    """Apply one resolved typography dict (see `paragraph_typography`)."""
    name = typography["name"]
    run.font.name = name
    run.font.size = typography["size"]
    run.bold = bold
    run.italic = italic

    if run._element.rPr is not None:
        fonts = run._element.rPr.rFonts
        if fonts is not None:
            fonts.set(qn("w:ascii"), name)
            fonts.set(qn("w:hAnsi"), name)
            fonts.set(qn("w:eastAsia"), name)


def markdown_runs(paragraph, text):
    typography = paragraph_typography(paragraph)["body"]

    layout_lib.clear_content(paragraph)

    parts = re.split(r"(\*\*.+?\*\*)", text)

    for part in parts:
        if not part:
            continue

        if part.startswith("**") and part.endswith("**"):
            run = paragraph.add_run(part[2:-2])
            set_font(run, typography, bold=True)
        else:
            run = paragraph.add_run(part)
            set_font(run, typography)


def set_project_header(paragraph, title, tech):
    typography = paragraph_typography(paragraph)

    layout_lib.clear_content(paragraph)

    r = paragraph.add_run(title)
    set_font(r, typography["body"], bold=True)

    r = paragraph.add_run("  —  " + tech)
    set_font(r, typography["descriptor"], italic=True)


def markdown_from_document(doc, layout: dict) -> str:
    bullet_indexes = layout_lib.all_bullet_indexes(layout)
    limit = layout.get("min_paragraph_count", len(doc.paragraphs))

    section_break_after = {
        anchor["index"] for anchor in (layout.get("section_anchors") or {}).values()
    }
    section_break_after.add(layout.get("contact_index"))

    for block in layout.get("experience_blocks", []) + layout.get("project_blocks", []):
        if block.get("bullet_indices"):
            section_break_after.add(block["bullet_indices"][-1])

    lines = []

    for i, paragraph in enumerate(doc.paragraphs[:limit]):
        text = paragraph.text.strip()

        if not text:
            lines.append("")
            continue

        if i in bullet_indexes:
            lines.append("- " + text)
        else:
            lines.append(text)

        if i in section_break_after:
            lines.append("")

    return "\n".join(lines).strip() + "\n"


def convert_to_pdf(docx_path, pdf_path):
    try:
        from docx2pdf import convert
    except ImportError:
        raise RuntimeError(
            "docx2pdf is unavailable in the renderer environment."
        )

    import fcntl

    docx_path = Path(docx_path).resolve()
    pdf_path = Path(pdf_path).resolve()

    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    # Microsoft Word always sees the SAME paths.
    # This avoids repeated macOS file-access prompts.
    staging_root = Path.home() / "Documents" / "ResumeRenderer"
    staging_root.mkdir(parents=True, exist_ok=True)

    staged_docx = staging_root / "resume-input.docx"
    staged_pdf = staging_root / "resume-input.pdf"
    lock_path = staging_root / ".word-conversion.lock"

    # Word conversion is serialized to support concurrent job processing.
    with lock_path.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)

        try:
            shutil.copy2(docx_path, staged_docx)
            staged_pdf.unlink(missing_ok=True)

            try:
                convert(str(staged_docx), str(staged_pdf), keep_active=True)
            except TypeError:
                convert(str(staged_docx), str(staged_pdf))

            if not staged_pdf.exists():
                raise RuntimeError(
                    "Microsoft Word did not produce the expected PDF."
                )

            shutil.copy2(staged_pdf, pdf_path)

        finally:
            # KEEP resume-input.docx in place: Word has been granted
            # permission to this exact path, and future jobs overwrite the
            # contents without changing the authorized pathname. The
            # generated PDF can be removed safely.
            staged_pdf.unlink(missing_ok=True)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--payload", required=True)
    parser.add_argument("--template", default=str(DEFAULT_TEMPLATE))
    parser.add_argument("--layout", default=str(DEFAULT_LAYOUT))

    parser.add_argument("--markdown-output")
    parser.add_argument("--output-docx")
    parser.add_argument("--output-pdf")
    parser.add_argument("--draft-only", action="store_true")

    args = parser.parse_args()

    payload_path = Path(args.payload)
    template = Path(args.template)
    layout_path = Path(args.layout)

    payload = json.loads(payload_path.read_text())

    if not layout_path.exists():
        raise SystemExit(
            f"Resume template layout not found: {layout_path}\n"
            "Copy reference/resume_template_layout.example.json to "
            f"{layout_path} and adjust it to match your own template."
        )

    layout = layout_lib.load_layout(layout_path)

    layout_lib.validate_payload(payload, layout)

    if not template.exists():
        raise SystemExit(f"Resume template not found: {template}")

    doc = Document(template)

    layout_lib.validate_template(doc, layout)
    layout_lib.apply_payload(doc, payload, layout, markdown_runs, set_project_header)

    if args.markdown_output:
        md = markdown_from_document(doc, layout)

        path = Path(args.markdown_output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md)

    if args.draft_only:
        return

    if not args.output_docx:
        raise SystemExit("--output-docx is required unless --draft-only is used.")

    output_docx = Path(args.output_docx)
    output_docx.parent.mkdir(parents=True, exist_ok=True)

    doc.save(output_docx)

    if args.output_pdf:
        convert_to_pdf(output_docx, Path(args.output_pdf))


if __name__ == "__main__":
    main()
