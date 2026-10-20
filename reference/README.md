# `reference/` — your document templates

**Everything in this directory except `README.md` and the two
`*.example.json` layout files is your private document template and is
excluded from Git.** Templates commonly contain your name, address, and
contact details baked into the document itself, so they are never
committed.

## What goes here

| Real file (private, git-ignored) | Purpose |
| --- | --- |
| `resume_template.docx` | Your approved, final-formatted resume, as a Word document, with the sections/bullets you want the pipeline to edit already in place with placeholder text. |
| `resume_template_layout.json` | Describes, in JSON, exactly which paragraph (by 0-based index) is which section/bullet in `resume_template.docx`, and which ones are protected/tailorable/selected. Copy `resume_template_layout.example.json` and edit the indices to match your own document. |
| `cover_letter_template.docx` | Your approved cover-letter template. |
| `cover_letter_template_layout.json` | Same idea as above, for the cover letter. Copy `cover_letter_template_layout.example.json` and edit it. |

## How the layout contract works

The renderer (`tools/resume/render_resume.py`) does not lay out a resume
from scratch — it opens your existing `resume_template.docx` and edits
specific paragraphs in place, by index. Which paragraph is which is
entirely described by `resume_template_layout.json`, not hardcoded in any
Python file. This means:

- The renderer works with **any** resume layout, as long as you describe
  its geometry accurately in the layout file.
- If you edit your `.docx` (add/remove a line, insert a new section), you
  must update `resume_template_layout.json`'s paragraph indices to match,
  or rendering will fail loudly (never silently write to the wrong
  paragraph).

### Finding your own paragraph indices

The simplest way to find the indices for your own template:

```bash
python3 -c "
from docx import Document
doc = Document('reference/resume_template.docx')
for i, p in enumerate(doc.paragraphs):
    print(i, repr(p.text))
"
```

Match each printed index against `resume_template_layout.example.json`'s
shape: a `name_index`, a `contact_index`, `section_anchors` (the literal
heading text at each heading paragraph), `education_blocks`,
`experience_blocks` (each with an `owner_id` that must match an `id` in
your `master/runtime/runtime_resume_context.json`, a `tailoring_mode`, a
`header_index`, and `bullet_indices`), `project_blocks` (same shape, plus
exactly one block with `"tailoring_mode": "selected"` — the one
dynamically-bound project slot), and `skills_indices`.

Character budgets (`max_chars_per_bullet`, `max_chars_title`,
`max_chars_technology_line`, and their `_emergency` variants) are also
declared per-block in the layout file — set them based on your own
template's column widths and font size; `tools/resume/payload_preflight.py`
and `tools/resume/density_guard.py` read them from here, not from a
hardcoded table.

### Cover letter layout

`cover_letter_template_layout.json` is simpler: `salutation_index`,
`opening_index`, `experience_paragraph_blocks` (one `{owner_id, index}`
per experience the letter should address — typically all of them),
`project_paragraph_index`, `closing_index`, and `static_indices` (the
paragraphs — usually your name/address/signature block — that the
renderer verifies are byte-for-byte unchanged after every render, as a
safety check).

## Validating your setup

```bash
python3 tools/validate_candidate_setup.py
```

checks that both `.docx` templates exist, both layout files exist and
parse, and that every `owner_id` referenced in a layout file matches an
`id` with the expected `tailoring_mode` in
`master/runtime/runtime_resume_context.json` — all before any model call
is made.

See [`INSTRUCTIONS.md`](../INSTRUCTIONS.md) and
[`docs/candidate-data-guide.md`](../docs/candidate-data-guide.md) for more.
