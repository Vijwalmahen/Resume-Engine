#!/usr/bin/env python3
"""
Template-fidelity audit: did rendering change anything but the words?

The renderer edits an existing `.docx` in place at fixed paragraph
indices. That makes the template -- not any code in this repository --
the source of truth for appearance, and it makes one question the whole
visual contract: after `render_resume.py` has run, is the document still
typographically the template it started from?

This module answers that by DIFFING THE RENDERED DOCUMENT AGAINST ITS OWN
TEMPLATE. Nothing is compared against a hardcoded "correct" font, size or
margin, because this repository does not get to decide what a user's
resume should look like. A template in Garamond 11pt is as valid as one
in Calibri 10.5pt; what is never valid is the renderer silently turning
one into the other.

Why a diff and not a style sheet
--------------------------------

An absolute contract ("body must be Calibri 10.5") would have to be
written down somewhere, and would then be wrong for every template that
disagrees with it -- including, quietly, a future revision of the user's
own. A differential check has no such opinion: it holds whatever the
template already says, so it keeps working when the design legitimately
changes, and it fails precisely when rendering is the thing that changed
it.

What is compared
----------------

  geometry    page size and all four margins, from the section
              properties. A margin change moves every line on the page.

  structure   paragraph count and, per paragraph, the style name
              ('List Bullet' vs 'Normal' is what makes a bullet a
              bullet).

  paragraph   the spacing and indentation that produce vertical rhythm:
  formatting  space_before, space_after, line_spacing, left_indent,
              first_line_indent, alignment, tab stops, and the presence
              of a bottom border (the thin rule under each section
              heading).

  typography  the set of (font family, point size) pairs used by the
              runs of each paragraph.

Bold and italic are deliberately NOT diffed per paragraph. The renderer
applies `**markdown**` emphasis inside tailored bullets by design, so a
bold span appearing where the template had none is intended behaviour,
not drift. Font family and size are diffed, because no legitimate
tailoring decision changes those.

Paragraphs whose text the renderer is contractually allowed to replace
are still fully checked for formatting -- that is the entire point. A
rewritten bullet that comes back in a different face or size is exactly
the defect this exists to catch.

Separately, `audit_pdf_appearance` inspects the rendered PDF for the
things that only exist after conversion: how many pages it really is,
which font families and sizes actually got embedded, and how much blank
space is left under the last line of PAGE ONE. On a document that has
spilled onto a second page that is not the end of the document, so the
figure is a first-page density signal rather than a statement about the
whole file -- `tools/resume/qa_resume.py` owns the authoritative
one-page / spill verdict. It reports measurements and compares
them only against thresholds its caller passes in; it has no opinion of
its own about what they should be, and it produces no score.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

SCHEMA_VERSION = "1.0"


# ---------------------------------------------------------------------------
# docx inspection
# ---------------------------------------------------------------------------

def _emu(value):
    return None if value is None else int(value)


def paragraph_fingerprint(paragraph) -> dict:
    """Everything about a paragraph except the characters in it."""
    pf = paragraph.paragraph_format
    ppr = paragraph._p.pPr

    has_bottom_border = False
    if ppr is not None:
        borders = ppr.find(f"{W_NS}pBdr")
        if borders is not None and borders.find(f"{W_NS}bottom") is not None:
            has_bottom_border = True

    typography = sorted({
        (run.font.name, _emu(run.font.size))
        for run in paragraph.runs
        if run.text.strip()
    }, key=lambda pair: (str(pair[0]), pair[1] or 0))

    return {
        "style": paragraph.style.name if paragraph.style is not None else None,
        "alignment": str(pf.alignment) if pf.alignment is not None else None,
        "space_before": _emu(pf.space_before),
        "space_after": _emu(pf.space_after),
        "line_spacing": pf.line_spacing,
        "left_indent": _emu(pf.left_indent),
        "right_indent": _emu(pf.right_indent),
        "first_line_indent": _emu(pf.first_line_indent),
        "tab_stops": [_emu(t.position) for t in pf.tab_stops],
        "bottom_border": has_bottom_border,
        "typography": [list(pair) for pair in typography],
    }


def section_fingerprint(document) -> dict:
    section = document.sections[0]
    return {
        attribute: _emu(getattr(section, attribute))
        for attribute in (
            "page_width", "page_height",
            "left_margin", "right_margin", "top_margin", "bottom_margin",
        )
    }


def document_fingerprint(path) -> dict:
    from docx import Document

    document = Document(str(path))
    return {
        "section": section_fingerprint(document),
        "paragraph_count": len(document.paragraphs),
        "paragraphs": [paragraph_fingerprint(p) for p in document.paragraphs],
    }


# ---------------------------------------------------------------------------
# the differential audit
# ---------------------------------------------------------------------------

def _finding(check, severity, subject, message, expected, actual) -> dict:
    return {
        "check": check,
        "severity": severity,
        "subject": subject,
        "message": message,
        "expected": expected,
        "actual": actual,
    }


# Compared per paragraph, strictly. Bold/italic are excluded on purpose --
# see the module docstring: markdown emphasis inside tailored bullets is
# intended. `typography` is excluded here too and handled separately by
# `_typography_findings`, because it needs the type-scale rule below
# rather than a strict per-paragraph equality.
_PARAGRAPH_KEYS = (
    "style", "alignment", "space_before", "space_after", "line_spacing",
    "left_indent", "right_indent", "first_line_indent", "tab_stops",
    "bottom_border",
)


def _type_scale(fingerprint: dict) -> tuple:
    """The families and point sizes the template actually uses anywhere.

    A design has a type scale: a small set of faces and sizes that recur
    across the document. Rendering may legitimately use any member of
    that scale in a slot where the template happened not to pre-declare
    it -- the clearest case being the selected-project header, where the
    template may ship a bare title but the contract says the technology
    descriptor is set in the same smaller italic size the other project
    headers already use.

    Introducing a face or a size that appears NOWHERE in the template is
    different in kind: that value came from the renderer, not the design.
    """
    families, sizes = set(), set()
    for paragraph in fingerprint["paragraphs"]:
        for name, size in paragraph["typography"]:
            if name is not None:
                families.add(name)
            if size is not None:
                sizes.add(size)
    return families, sizes


def _typography_findings(template_fingerprint: dict, rendered_fingerprint: dict) -> list:
    """Typography drift, graded by whether it stayed inside the type scale."""
    findings = []
    families, sizes = _type_scale(template_fingerprint)

    pairs = zip(template_fingerprint["paragraphs"], rendered_fingerprint["paragraphs"])
    for index, (expected_paragraph, actual_paragraph) in enumerate(pairs):
        expected = expected_paragraph["typography"]
        actual = actual_paragraph["typography"]
        if expected == actual:
            continue

        off_scale = [
            [name, size] for name, size in actual
            if (name is not None and name not in families)
            or (size is not None and size not in sizes)
        ]

        if off_scale:
            findings.append(_finding(
                "typography_scale", "high", f"paragraph[{index}].typography",
                f"rendering used a font face or size on paragraph {index} that "
                "appears nowhere in the template, so it came from the renderer "
                "rather than the design",
                {"template_families": sorted(families),
                 "template_sizes_emu": sorted(sizes)},
                off_scale,
            ))
            continue

        if not expected:
            continue

        # A slot may legitimately GAIN an in-scale run: the selected-project
        # header is one paragraph that renders as a bold title plus a
        # smaller italic technology descriptor, and a template often ships
        # it as a bare placeholder with only the title's formatting. So
        # additive, in-scale typography is accepted; what is reported is a
        # paragraph that LOST or REPLACED formatting the template declared,
        # which is the direction that actually degrades a design.
        kept = {tuple(pair) for pair in actual}
        dropped = [pair for pair in expected if tuple(pair) not in kept]
        if dropped:
            findings.append(_finding(
                "typography_slot", "medium", f"paragraph[{index}].typography",
                f"paragraph {index} no longer uses the face/size the template "
                "declared for it",
                expected, actual,
            ))

    return findings


def compare_documents(template_fingerprint: dict, rendered_fingerprint: dict) -> dict:
    """Diff a rendered document against the template it was rendered from."""
    findings = []

    template_section = template_fingerprint["section"]
    rendered_section = rendered_fingerprint["section"]
    for attribute, expected in template_section.items():
        actual = rendered_section.get(attribute)
        if actual != expected:
            findings.append(_finding(
                "geometry", "high", attribute,
                f"page geometry changed during rendering: {attribute}",
                expected, actual,
            ))

    expected_count = template_fingerprint["paragraph_count"]
    actual_count = rendered_fingerprint["paragraph_count"]
    if expected_count != actual_count:
        findings.append(_finding(
            "structure", "high", "paragraph_count",
            "rendering added or removed paragraphs, which shifts every "
            "layout index the template contract depends on",
            expected_count, actual_count,
        ))

    pairs = zip(template_fingerprint["paragraphs"], rendered_fingerprint["paragraphs"])
    for index, (expected_paragraph, actual_paragraph) in enumerate(pairs):
        for key in _PARAGRAPH_KEYS:
            expected = expected_paragraph.get(key)
            actual = actual_paragraph.get(key)
            if expected == actual:
                continue
            # `style` is high because it is structural, not cosmetic: it
            # is what makes a paragraph a bullet, and losing it changes
            # how the document is read and extracted, not merely how it
            # looks. Spacing and indentation shift the page; they do not
            # change what an element IS.
            severity = "high" if key == "style" else "medium"
            findings.append(_finding(
                "paragraph_formatting", severity, f"paragraph[{index}].{key}",
                f"rendering changed {key} on paragraph {index}",
                expected, actual,
            ))

    findings.extend(_typography_findings(template_fingerprint, rendered_fingerprint))

    return {
        "findings": findings,
        "paragraphs_compared": min(expected_count, actual_count),
    }


def audit_render_fidelity(template_path, rendered_path) -> dict:
    """Top-level DOCX audit: rendered document vs. its own template."""
    template_path, rendered_path = Path(template_path), Path(rendered_path)
    for path, label in ((template_path, "template"), (rendered_path, "rendered document")):
        if not path.exists():
            return {
                "status": "NOT_EVALUATED",
                "reason": f"missing {label}: {path}",
                "findings": [],
            }

    try:
        template_fingerprint = document_fingerprint(template_path)
        rendered_fingerprint = document_fingerprint(rendered_path)
    except Exception as exc:
        return {
            "status": "NOT_EVALUATED",
            "reason": f"could not read documents: {type(exc).__name__}: {exc}",
            "findings": [],
        }

    result = compare_documents(template_fingerprint, rendered_fingerprint)
    return {
        "status": "EVALUATED",
        "template": str(template_path),
        "rendered": str(rendered_path),
        "paragraphs_compared": result["paragraphs_compared"],
        "findings": result["findings"],
        "fidelity_preserved": not result["findings"],
    }


# ---------------------------------------------------------------------------
# rendered-PDF appearance
# ---------------------------------------------------------------------------

def audit_pdf_appearance(
    pdf_path,
    *,
    min_body_size_pt=None,
    min_any_size_pt=None,
    bottom_whitespace_in=None,
    page_bottom_margin_in=0.0,
    expected_families=None,
) -> dict:
    """Measure the rendered PDF's actual typography and page fill.

    Every threshold is supplied by the caller. Passing none makes this a
    pure measurement with no pass/fail opinion at all.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return {"status": "NOT_EVALUATED", "reason": f"missing PDF: {pdf_path}", "findings": []}

    try:
        import fitz
    except ImportError:
        return {"status": "NOT_EVALUATED", "reason": "PyMuPDF (fitz) unavailable", "findings": []}

    findings = []
    try:
        document = fitz.open(str(pdf_path))
    except Exception as exc:
        return {
            "status": "NOT_EVALUATED",
            "reason": f"could not open PDF: {type(exc).__name__}: {exc}",
            "findings": [],
        }

    try:
        page_count = document.page_count
        fonts: dict = {}
        sizes: dict = {}
        max_y = 0.0
        page_height = None

        for page_number in range(page_count):
            page = document[page_number]
            if page_number == 0:
                page_height = page.rect.height
            for block in page.get_text("dict").get("blocks", []):
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if not span.get("text", "").strip():
                            continue
                        font = span.get("font", "")
                        fonts[font] = fonts.get(font, 0) + len(span["text"])
                        size = round(float(span.get("size", 0)), 2)
                        sizes[size] = sizes.get(size, 0) + len(span["text"])
                        if page_number == 0 and span.get("bbox"):
                            max_y = max(max_y, span["bbox"][3])
    finally:
        document.close()

    # A PDF font name is usually "Family" or "Family-Bold"/"ABCDEF+Family".
    families = sorted({
        font.split("+")[-1].split("-")[0].split(",")[0]
        for font in fonts
    })
    observed_sizes = sorted(sizes)
    smallest = observed_sizes[0] if observed_sizes else None

    raw_bottom = None
    usable_bottom = None
    if page_height is not None and max_y:
        raw_bottom = round((page_height - max_y) / 72, 3)
        usable_bottom = round(max(0.0, raw_bottom - page_bottom_margin_in), 3)

    # The size that carries the document's body text: the one most
    # characters are set in. Comparing a body-size floor against the
    # GLOBAL minimum would be wrong, because a resume design legitimately
    # sets secondary text smaller than its body -- this repository's own
    # layout contract requires bullets at >= 10.5pt while specifying the
    # project technology descriptor at ~10pt. Flagging that descriptor as
    # an undersized bullet would be a false positive on a conforming
    # document, so the floor is applied to the body size and the global
    # minimum is reported as a measurement unless the caller supplies a
    # separate absolute floor.
    dominant_size = max(sizes, key=lambda size: sizes[size]) if sizes else None

    if min_body_size_pt is not None and dominant_size is not None:
        # Word renders a nominal 10.5pt as 10.56pt in the PDF (a ~1.008
        # scale), so an exact comparison would misreport a conforming
        # document. 0.1pt absorbs that rounding while staying far below
        # the smallest deliberate step in a resume type scale (0.5pt).
        if dominant_size < float(min_body_size_pt) - 0.1:
            findings.append(_finding(
                "pdf_typography", "high", "body_size_pt",
                "the document's body text is set smaller than the declared "
                "minimum body size",
                f">= {min_body_size_pt}", dominant_size,
            ))

    if min_any_size_pt is not None and smallest is not None:
        if smallest < float(min_any_size_pt) - 0.1:
            findings.append(_finding(
                "pdf_typography", "medium", "min_font_size_pt",
                "some rendered text is smaller than the declared absolute floor "
                "for any text on the page",
                f">= {min_any_size_pt}", smallest,
            ))

    if expected_families:
        unexpected = [f for f in families if f not in set(expected_families)]
        if unexpected:
            findings.append(_finding(
                "pdf_typography", "high", "font_families",
                "the rendered PDF embeds a font family the template did not declare",
                sorted(expected_families), families,
            ))

    if bottom_whitespace_in and usable_bottom is not None:
        low, high = bottom_whitespace_in
        if usable_bottom > high:
            findings.append(_finding(
                "pdf_density", "medium", "usable_bottom_whitespace_in",
                "the page is underfilled: more usable blank space under the last "
                "line than the layout contract allows",
                f"{low}-{high} in", usable_bottom,
            ))
        elif usable_bottom < low:
            findings.append(_finding(
                "pdf_density", "low", "usable_bottom_whitespace_in",
                "the page is tighter at the bottom than the layout contract expects",
                f"{low}-{high} in", usable_bottom,
            ))

    return {
        "status": "EVALUATED",
        "pdf": str(pdf_path),
        "page_count": page_count,
        "font_families": families,
        "fonts_by_char_count": dict(sorted(fonts.items(), key=lambda kv: -kv[1])),
        "font_sizes_pt": observed_sizes,
        "body_size_pt": dominant_size,
        "min_font_size_pt": smallest,
        "raw_bottom_whitespace_in": raw_bottom,
        "usable_bottom_whitespace_in": usable_bottom,
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Audit that rendering changed only the words, not the design."
    )
    parser.add_argument("--template", required=True)
    parser.add_argument("--rendered-docx", required=True)
    parser.add_argument("--pdf", default=None)
    parser.add_argument("--min-body-size-pt", type=float, default=None)
    parser.add_argument("--min-any-size-pt", type=float, default=None)
    parser.add_argument("--bottom-whitespace-min-in", type=float, default=None)
    parser.add_argument("--bottom-whitespace-max-in", type=float, default=None)
    parser.add_argument("--page-bottom-margin-in", type=float, default=0.0)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report = {
        "schema_version": SCHEMA_VERSION,
        "render_fidelity": audit_render_fidelity(args.template, args.rendered_docx),
    }

    if args.pdf:
        window = None
        if args.bottom_whitespace_min_in is not None and args.bottom_whitespace_max_in is not None:
            window = (args.bottom_whitespace_min_in, args.bottom_whitespace_max_in)
        report["pdf_appearance"] = audit_pdf_appearance(
            args.pdf,
            min_body_size_pt=args.min_body_size_pt,
            min_any_size_pt=args.min_any_size_pt,
            bottom_whitespace_in=window,
            page_bottom_margin_in=args.page_bottom_margin_in,
        )

    findings = list(report["render_fidelity"].get("findings", []))
    findings += list(report.get("pdf_appearance", {}).get("findings", []))
    report["finding_count"] = len(findings)
    report["advisory_only"] = True

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print()
    print("TEMPLATE STYLE AUDIT (advisory -- gates nothing)")
    print("=" * 60)
    fidelity = report["render_fidelity"]
    print(f"  render fidelity : {fidelity.get('status')} "
          f"preserved={fidelity.get('fidelity_preserved')}")
    if "pdf_appearance" in report:
        appearance = report["pdf_appearance"]
        print(f"  pdf families    : {appearance.get('font_families')}")
        print(f"  pdf sizes (pt)  : {appearance.get('font_sizes_pt')} "
              f"(body={appearance.get('body_size_pt')})")
        print(f"  bottom space in : raw={appearance.get('raw_bottom_whitespace_in')} "
              f"usable={appearance.get('usable_bottom_whitespace_in')}")
    for finding in findings:
        print(f"    [{finding['severity']}] {finding['subject']}: {finding['message']}")
        print(f"        expected={finding['expected']!r} actual={finding['actual']!r}")
    print(f"  Report: {output_path}")


if __name__ == "__main__":
    main()
