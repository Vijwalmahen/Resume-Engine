# `tools/resume/`

The resume and cover-letter side of the pipeline: the layout-driven
renderer and its guards here, the per-job V8 pipeline in `v8/`, and the
V8.6 batch pipeline in `v86/`.

Nothing in this directory knows any candidate's name, employer,
university, or project. Every identity is an opaque `owner_id` string
that must match an `id` in the candidate's own runtime context.

## Files in this directory

| File | What it does | How the pipeline uses it |
| --- | --- | --- |
| `template_layout.py` | The DOCX layout contract. Reads the candidate's `reference/resume_template_layout.json` and resolves which paragraph index is which section, which bullets are protected/reorderable, which are tailorable, and which single slot holds the selected project. | Imported by the renderer and the guards so paragraph geometry lives in one place. The renderer edits an existing document at fixed indices; it never lays one out from scratch. |
| `render_resume.py` | Opens the candidate's Word template and edits exactly the paragraphs the layout file names, substituting values from `resume_payload.json` by `owner_id`. Can emit Markdown (`--markdown-output`), DOCX (`--output-docx`), PDF (`--output-pdf`), or draft text only (`--draft-only`). | The render stage. `tests/public/test_generic_pipeline.py` builds a synthetic template from the example layout and asserts rendering is driven purely by the layout and payload. |
| `payload_preflight.py` | Checks the dynamic (`owner_id`-keyed) payload fields against the per-field character budgets declared in the layout file; `--emergency` selects the layout's emergency budgets. | Runs before rendering so an over-long field is caught deterministically instead of overflowing the page. Limits come from the layout file, never from a hardcoded table. |
| `density_guard.py` | Given a QA report showing the one-page resume is under- or over-filled, proposes which dynamic field to shorten — restricted to the `owner_id`s the layout marks tailorable or selected. | The deterministic repair advisor for page density. Protected canonical content is never a repair candidate. |
| `qa_resume.py` | PDF QA. Derives the required text (candidate name, section headings, protected employer/title values, required project names, static skills lines) entirely from the candidate's own runtime context and checks the rendered PDF against it. | The artifact-validation stage after rendering. |
| `word_to_pdf.py` | Converts a DOCX to PDF via `docx2pdf`, staging through a fixed working directory under the user's home and serializing concurrent conversions with an `fcntl` lock. | The DOCX→PDF step. The lock matters because the conversion drives a single local Word instance. |

## Subdirectories

| Directory | What it holds |
| --- | --- |
| [`v8/`](v8/README.md) | The per-job local pipeline, the runtime-context guard, deterministic project selection, the cover-letter renderer, and the V8 contract validators. |
| [`v86/`](v86/README.md) | The batch pipeline: the runner, semantic and correction workers, the deterministic pipeline, the finalizer, and the advisory diagnostics. |

## Where this sits in the flow

Batch run and validation. Generation happens in `v86/`; everything in
this directory turns an approved payload into artifacts and then checks
those artifacts.

## Private or git-ignored

These modules read two private, git-ignored inputs by default:

- `reference/resume_template.docx` and
  `reference/resume_template_layout.json` — the candidate's own template
  and its paragraph geometry (committed counterparts:
  `reference/resume_template_layout.example.json` and the fictional
  `reference/examples/resume_template.example.docx`).
- `master/runtime/runtime_resume_context.json` — the runtime candidate
  context.

Everything they write (`output/<job_key>/`) is git-ignored, and
`*.docx` / `*.pdf` are git-ignored repository-wide.

## Related documentation

- [`../../reference/README.md`](../../reference/README.md) — how to build a layout file for your own template.
- [`../../docs/candidate-data-guide.md`](../../docs/candidate-data-guide.md) — the schemas behind `owner_id` and `tailoring_mode`.
- [`../../docs/deterministic-validation.md`](../../docs/deterministic-validation.md) — where preflight, QA, and the density guard sit.
- [`../../config/resume_layout.md`](../../config/resume_layout.md) — the shared formatting rules.
