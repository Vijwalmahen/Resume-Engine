#!/usr/bin/env python3
"""
tracker_xlsx.py — stdlib-only reader/writer for
tracking/job_application_tracker.xlsx.

No openpyxl dependency: openpyxl is only installed under this machine's
/usr/bin/python3 (Apple's bundled 3.9.6), not under python3.13, and the
rest of the orchestrator deliberately stays stdlib-only (see
rolling_queue.py's .env loader) so it never depends on which interpreter
happens to run it. An .xlsx file is just a zip of a handful of small XML
parts, so this module builds/reads exactly those parts with `zipfile` and
`xml.etree.ElementTree`.

Design for safe read-modify-write:
  - Every column is classified ORCHESTRATOR-owned or HUMAN-owned (see
    ORCHESTRATOR_OWNED_HEADERS / HUMAN_OWNED_HEADERS below).
  - On every upsert, orchestrator-owned columns are always regenerated from
    the caller-supplied `fields` dict.
  - Human-owned columns are never touched for a row that already exists:
    the ORIGINAL cell XML Element is copied byte-for-byte (only its cell
    reference is corrected) into the rewritten sheet, so whatever the user
    typed into Excel — including cells Excel itself re-saved using a
    sharedStrings table — survives unchanged. A brand-new row gets a
    sensible default (see DEFAULTS) for any human-owned column not
    explicitly supplied.
  - xl/sharedStrings.xml, if present in the existing workbook, is copied
    through unmodified (never rewritten/renumbered) so passthrough cells
    that reference it by index stay valid. This module never needs to
    WRITE a new shared-string entry itself: every value it writes fresh
    uses inline strings (t="inlineStr"), which need no shared table at all.
  - The whole file is written to a temp path in the same directory and
    then atomically replaced with os.replace() (see upsert_tracker_row).
  - Every write is gated by assert_workbook_compatible(), which refuses any
    workbook whose schema this module does not own (a different sheet name,
    extra worksheets, or any header row other than HEADERS below) rather
    than rewriting it into this module's 35-column layout. The V8.6
    production tracker's 23-column `Applications` sheet is exactly such a
    workbook.
"""

from __future__ import annotations

import copy
import os
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from xml.sax.saxutils import escape as xml_escape

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
ET.register_namespace("", NS)


def qn(tag: str) -> str:
    return f"{{{NS}}}{tag}"


HEADERS = [
    "Job Key", "Source", "Source Website", "Source Job ID", "Requisition ID",
    "Company", "Role", "Location", "Work Arrangement", "Employment Type",
    "Job URL", "Application URL", "Company Careers URL",
    "Posted Date", "Closing Date", "Date Found",
    "Priority", "Pipeline Status",
    "Resume Path", "Cover Letter Path", "Job Description Path",
    "Resume Created At",
    "Application Status", "Date Applied",
    "Days Since Posted When Applied", "Days Since Applied",
    "Recruiter / Contact", "Referral",
    "Compensation", "Sponsorship / Work Authorization",
    "Interview Stage",
    "Next Action", "Next Action Date",
    "Last Updated", "Notes",
]

# Fields the user owns. The orchestrator MUST NEVER overwrite a non-empty
# value here once a row exists — see upsert_tracker_row(). In particular,
# "Application Status" only ever gets its NOT_APPLIED default at row
# creation; nothing here ever sets it to APPLIED.
HUMAN_OWNED_HEADERS = {
    "Application Status", "Date Applied",
    "Days Since Posted When Applied", "Days Since Applied",
    "Recruiter / Contact", "Referral",
    "Sponsorship / Work Authorization", "Interview Stage",
    "Next Action", "Next Action Date", "Notes",
}

# Everything else is orchestrator-owned and refreshed on every upsert call
# from whatever the caller passes in `fields`.
ORCHESTRATOR_OWNED_HEADERS = [h for h in HEADERS if h not in HUMAN_OWNED_HEADERS]

DEFAULTS = {
    "Application Status": "NOT_APPLIED",
    "Priority": "NORMAL",
}


def _today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def col_letter(n: int) -> str:
    """1-indexed column number -> Excel column letters (1 -> A, 27 -> AA)."""
    letters = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


# ---------------------------------------------------------------------------
# Reading an existing workbook
# ---------------------------------------------------------------------------

class ExistingWorkbook:
    def __init__(self):
        self.shared_strings: list[str] = []
        self.header_by_letter: dict[str, str] = {}
        self.header_row_cells: dict[str, ET.Element] = {}  # header name -> original <c> element
        self.row_order: list[str] = []            # job_key, in original row order
        self.row_cells: dict[str, dict[str, ET.Element]] = {}  # job_key -> {header: <c> element}
        self.has_shared_strings_part = False
        self.shared_strings_bytes: Optional[bytes] = None
        self.styles_bytes: Optional[bytes] = None


def _cell_column_letter(ref: str) -> str:
    return "".join(ch for ch in ref if ch.isalpha())


def _resolve_shared_string(si_elem: ET.Element) -> str:
    return "".join(t.text or "" for t in si_elem.iter(qn("t")))


def _cell_text(cell_elem: ET.Element, shared_strings: list[str]) -> str:
    t = cell_elem.get("t")
    if t == "inlineStr":
        is_elem = cell_elem.find(qn("is"))
        if is_elem is None:
            return ""
        return "".join(t_elem.text or "" for t_elem in is_elem.iter(qn("t")))
    v_elem = cell_elem.find(qn("v"))
    if v_elem is None or v_elem.text is None:
        return ""
    if t == "s":
        try:
            idx = int(v_elem.text)
        except ValueError:
            return ""
        return shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
    return v_elem.text


def read_existing_workbook(path: Path) -> ExistingWorkbook:
    wb = ExistingWorkbook()
    if not path.exists():
        return wb
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        if "xl/sharedStrings.xml" in names:
            wb.has_shared_strings_part = True
            wb.shared_strings_bytes = zf.read("xl/sharedStrings.xml")
            root = ET.fromstring(wb.shared_strings_bytes)
            wb.shared_strings = [_resolve_shared_string(si) for si in root.findall(qn("si"))]
        if "xl/styles.xml" in names:
            wb.styles_bytes = zf.read("xl/styles.xml")
        if "xl/worksheets/sheet1.xml" not in names:
            return wb
        sheet_root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        sheet_data = sheet_root.find(qn("sheetData"))
        if sheet_data is None:
            return wb
        rows = sheet_data.findall(qn("row"))
        if not rows:
            return wb

        header_row = rows[0]
        for c in header_row.findall(qn("c")):
            ref = c.get("r") or ""
            letter = _cell_column_letter(ref)
            if not letter:
                continue
            text = _cell_text(c, wb.shared_strings).strip()
            if text:
                wb.header_by_letter[letter] = text
                wb.header_row_cells[text] = c

        letter_by_header = {v: k for k, v in wb.header_by_letter.items()}
        job_key_letter = letter_by_header.get("Job Key")
        if job_key_letter is None:
            return wb

        for row in rows[1:]:
            cell_by_header: dict[str, ET.Element] = {}
            job_key = None
            for c in row.findall(qn("c")):
                ref = c.get("r") or ""
                letter = _cell_column_letter(ref)
                header = wb.header_by_letter.get(letter)
                if header is None:
                    continue
                cell_by_header[header] = c
                if letter == job_key_letter:
                    job_key = _cell_text(c, wb.shared_strings).strip() or None
            if job_key:
                wb.row_order.append(job_key)
                wb.row_cells[job_key] = cell_by_header
    return wb


# ---------------------------------------------------------------------------
# Header-compatibility guard
# ---------------------------------------------------------------------------

class TrackerSchemaError(Exception):
    """Raised instead of rewriting a workbook whose schema this module does
    not own — see assert_workbook_compatible()."""


def _worksheet_part_names(zf: zipfile.ZipFile) -> list[str]:
    return sorted(n for n in zf.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml"))


def _sheet_names(zf: zipfile.ZipFile) -> list[str]:
    if "xl/workbook.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/workbook.xml"))
    return [s.get("name") or "" for s in root.iter(qn("sheet"))]


def existing_header_row(path: Path) -> Optional[list[str]]:
    """The header texts of the first worksheet's first row, in column order,
    or None when the file does not exist / holds no rows at all. Trailing
    blank header cells are ignored (Excel pads rows out)."""
    if not path.exists():
        return None
    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            shared = [_resolve_shared_string(si) for si in root.findall(qn("si"))]
        if "xl/worksheets/sheet1.xml" not in names:
            return None
        sheet_root = ET.fromstring(zf.read("xl/worksheets/sheet1.xml"))
        sheet_data = sheet_root.find(qn("sheetData"))
        if sheet_data is None:
            return None
        rows = sheet_data.findall(qn("row"))
        if not rows:
            return None
        headers = [_cell_text(c, shared).strip() for c in rows[0].findall(qn("c"))]
    while headers and not headers[-1]:
        headers.pop()
    return headers


def assert_workbook_compatible(path: Path) -> None:
    """Refuse to touch a workbook this module's 35-column layout does not
    own.

    write_workbook() below rebuilds the whole package from scratch: one
    worksheet part, one `Jobs` sheet, this module's exact HEADERS, in this
    module's exact column order. Pointed at a workbook written to any other
    schema — in particular the V8.6 production tracker, whose 23-column
    `Applications` sheet (config/application_tracker_schema.md,
    tools/resume/v86/tracker_writer.py) is a completely different contract —
    that rewrite is destructive: every value lands under the wrong header
    and every other worksheet disappears.

    This module has no live call site today (rolling_queue.py is the only
    importer, and it is not running), so the guard is the difference between
    a dormant writer that is merely unused and one that silently destroys a
    tracker the first time somebody does point it at the wrong file.

    Accepted, and only these:
      - a path that does not exist yet (brand-new workbook), or an existing
        workbook whose first sheet has no rows at all;
      - a single-worksheet workbook whose first sheet is named `Jobs` and
        whose header row equals HEADERS exactly, in order.

    Anything else raises TrackerSchemaError and nothing is written.
    """
    if not path.exists():
        return

    with zipfile.ZipFile(path, "r") as zf:
        worksheets = _worksheet_part_names(zf)
        sheet_names = _sheet_names(zf)

    if len(worksheets) > 1:
        raise TrackerSchemaError(
            f"{path} holds {len(worksheets)} worksheets {sheet_names}; this writer rebuilds a "
            "single-worksheet workbook and would drop the others. Refusing to modify it."
        )

    if sheet_names and sheet_names[0] != "Jobs":
        raise TrackerSchemaError(
            f"{path}'s first sheet is {sheet_names[0]!r}, not 'Jobs' — this writer owns only its own "
            f"'Jobs' sheet (see HEADERS). Refusing to modify it. The V8.6 'Applications' tracker is "
            "written by tools/resume/v86/tracker_writer.py; intake rows by tools/intake/intake_tracker.py."
        )

    headers = existing_header_row(path)
    if headers is None:
        return  # existing but empty — nothing of anyone else's to destroy

    if headers != HEADERS:
        missing = [h for h in HEADERS if h not in headers]
        unexpected = [h for h in headers if h not in HEADERS]
        raise TrackerSchemaError(
            f"{path} has a {len(headers)}-column header row that does not match this writer's "
            f"{len(HEADERS)}-column schema. Refusing to modify it (a rewrite would put every value "
            f"under the wrong header). missing={missing} unexpected={unexpected}"
        )


# ---------------------------------------------------------------------------
# Writing a workbook
# ---------------------------------------------------------------------------

def _make_cell(ref: str, value: Optional[str], style: Optional[int] = None) -> ET.Element:
    c = ET.Element(qn("c"), {"r": ref})
    if style is not None:
        c.set("s", str(style))
    text = "" if value is None else str(value)
    if text == "":
        return c
    c.set("t", "inlineStr")
    is_elem = ET.SubElement(c, qn("is"))
    t_elem = ET.SubElement(is_elem, qn("t"))
    t_elem.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    # Strip XML-illegal control characters (everything except tab/lf/cr);
    # ElementTree escapes &/</> itself on serialization, so no manual
    # escaping is needed here beyond that filter.
    t_elem.text = "".join(ch for ch in text if ch in "\t\r\n" or ord(ch) >= 0x20)
    return c


def _passthrough_cell(original: ET.Element, ref: str) -> ET.Element:
    new = copy.deepcopy(original)
    new.set("r", ref)
    return new


def _build_sheet_xml(headers: list[str], data_rows: list[list[ET.Element]],
                      header_row_cells: Optional[dict] = None) -> bytes:
    n_cols = len(headers)
    last_row = len(data_rows) + 1
    last_col_letter = col_letter(n_cols)

    root = ET.Element(qn("worksheet"))
    ET.SubElement(root, qn("dimension"), {"ref": f"A1:{last_col_letter}{last_row}"})
    sheet_views = ET.SubElement(root, qn("sheetViews"))
    sheet_view = ET.SubElement(sheet_views, qn("sheetView"), {"workbookViewId": "0"})
    ET.SubElement(sheet_view, qn("pane"), {
        "ySplit": "1", "topLeftCell": "A2", "activePane": "bottomLeft", "state": "frozen",
    })
    cols = ET.SubElement(root, qn("cols"))
    for i, h in enumerate(headers, start=1):
        width = max(12, min(40, len(h) + 4))
        ET.SubElement(cols, qn("col"), {
            "min": str(i), "max": str(i), "width": str(width), "customWidth": "1",
        })

    sheet_data = ET.SubElement(root, qn("sheetData"))
    header_row_elem = ET.SubElement(sheet_data, qn("row"), {"r": "1"})
    for i, h in enumerate(headers, start=1):
        ref = f"{col_letter(i)}1"
        original = (header_row_cells or {}).get(h)
        # Passthrough the original header cell (preserves whatever style
        # index it already used, which is only ever valid against the
        # styles.xml that was in effect when it was written — see the
        # "always keep the existing styles.xml unmodified" rationale in
        # upsert_tracker_row). Only a brand-new workbook (no header cell
        # yet) gets a freshly built, bold (style=1) header cell using this
        # module's own minimal styles.xml.
        header_row_elem.append(
            _passthrough_cell(original, ref) if original is not None else _make_cell(ref, h, style=1)
        )

    for r_idx, row_cells in enumerate(data_rows, start=2):
        row_elem = ET.SubElement(sheet_data, qn("row"), {"r": str(r_idx)})
        for c_idx, cell in enumerate(row_cells, start=1):
            row_elem.append(cell)

    ET.SubElement(root, qn("autoFilter"), {"ref": f"A1:{last_col_letter}{last_row}"})
    return b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ET.tostring(root, encoding="UTF-8")


_STYLES_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="1"><fill><patternFill patternType="none"/></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="2">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>
</cellXfs>
</styleSheet>
'''

_WORKBOOK_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Jobs" sheetId="1" r:id="rId1"/></sheets>
</workbook>
'''

_ROOT_RELS_XML = b'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
'''


def _content_types_xml(has_shared_strings: bool) -> bytes:
    shared = (
        '<Override PartName="/xl/sharedStrings.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        if has_shared_strings else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{shared}"
        "</Types>"
    ).encode("utf-8")


def _workbook_rels_xml(has_shared_strings: bool) -> bytes:
    shared = (
        '<Relationship Id="rId3" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" '
        'Target="sharedStrings.xml"/>'
        if has_shared_strings else ""
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        f"{shared}"
        "</Relationships>"
    ).encode("utf-8")


def write_workbook(path: Path, headers: list[str], data_rows: list[list[ET.Element]],
                    shared_strings_bytes: Optional[bytes], styles_bytes: Optional[bytes] = None,
                    header_row_cells: Optional[dict] = None) -> None:
    """Atomic write: build the whole zip in a temp file in the same
    directory, then os.replace() it over the target path.

    `styles_bytes`, when given, is the ORIGINAL workbook's styles.xml,
    copied through unmodified — never regenerated — because passthrough
    data/header cells carry style-index (`s="N"`) references that are only
    valid against that exact stylesheet. Regenerating a fresh minimal
    styles.xml on every write would silently corrupt any such cell (Excel/
    openpyxl fail to reopen the file with an out-of-range style index).
    Only a brand-new workbook (styles_bytes is None) uses this module's own
    minimal built-in stylesheet.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_xml = _build_sheet_xml(headers, data_rows, header_row_cells=header_row_cells)
    has_shared = shared_strings_bytes is not None
    styles_xml = styles_bytes if styles_bytes is not None else _STYLES_XML

    fd, tmp_name = tempfile.mkstemp(prefix=".job_application_tracker-", suffix=".xlsx.tmp", dir=str(path.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", _content_types_xml(has_shared))
            zf.writestr("_rels/.rels", _ROOT_RELS_XML)
            zf.writestr("xl/workbook.xml", _WORKBOOK_XML)
            zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml(has_shared))
            zf.writestr("xl/styles.xml", styles_xml)
            zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)
            if has_shared:
                zf.writestr("xl/sharedStrings.xml", shared_strings_bytes)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def upsert_tracker_row(tracker_path: Path, job_key: str, fields: dict) -> None:
    """Read-modify-write the whole workbook to create-or-update exactly one
    row identified by job_key.

    `fields`: header_name -> value string for whichever orchestrator-owned
    columns should be (re)written on this call. Any header not present in
    `fields`:
      - on an EXISTING row: left exactly as it was (passthrough of the
        original cell, human-owned or not — this is what makes repeated
        calls safe to make with a partial `fields` dict, e.g. only
        "Pipeline Status" + "Last Updated" on a plain stage-transition
        sync).
      - on a BRAND NEW row: filled from DEFAULTS, or left blank.

    Never overwrites a human-owned column's existing non-empty value —
    callers must never put a human-owned header in `fields` for an
    already-existing row (there is no code path in this orchestrator that
    does; see rolling_queue.py's tracker sync call sites).
    """
    # Fail closed on any workbook this module's schema does not own, BEFORE
    # reading or rewriting a single byte of it (see assert_workbook_compatible).
    assert_workbook_compatible(tracker_path)

    existing = read_existing_workbook(tracker_path)
    is_new_row = job_key not in existing.row_cells

    if is_new_row:
        existing.row_order.append(job_key)
        existing.row_cells[job_key] = {}
        if "Date Found" not in fields:
            fields = {**fields, "Date Found": _today_iso()}

    data_rows: list[list[ET.Element]] = []
    for jk in existing.row_order:
        cells_for_row = existing.row_cells.get(jk, {})
        row_cells: list[ET.Element] = []
        for col_idx, header in enumerate(HEADERS, start=1):
            ref = col_letter(col_idx)  # row number appended in the fix-up pass below
            if jk == job_key and header in fields:
                # Caller explicitly wants this (orchestrator-owned) column
                # refreshed — always wins, even on an existing row.
                row_cells.append(_make_cell(ref, fields[header]))
            elif header in cells_for_row:
                # Preserve whatever was already in this cell verbatim
                # (this is what protects human-owned columns).
                row_cells.append(_passthrough_cell(cells_for_row[header], ref))
            elif jk == job_key and is_new_row:
                row_cells.append(_make_cell(ref, DEFAULTS.get(header, "")))
            else:
                row_cells.append(_make_cell(ref, ""))
        data_rows.append(row_cells)

    # Fix up cell references now that we know each row's actual row number
    # (row 1 is the header; data starts at row 2).
    for r_idx, row_cells in enumerate(data_rows, start=2):
        for cell in row_cells:
            letter = "".join(ch for ch in cell.get("r", "") if ch.isalpha())
            cell.set("r", f"{letter}{r_idx}")

    write_workbook(
        tracker_path, HEADERS, data_rows,
        shared_strings_bytes=existing.shared_strings_bytes,
        styles_bytes=existing.styles_bytes,
        header_row_cells=existing.header_row_cells,
    )


def read_tracker_rows(tracker_path: Path) -> list[dict]:
    """Read-only convenience helper (used by tests) — returns a list of
    {header: text} dicts, in row order."""
    existing = read_existing_workbook(tracker_path)
    shared = existing.shared_strings
    rows = []
    for jk in existing.row_order:
        row = {}
        for header, cell in existing.row_cells.get(jk, {}).items():
            row[header] = _cell_text(cell, shared)
        rows.append(row)
    return rows
