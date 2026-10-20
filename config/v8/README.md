# `config/v8/`

The V8 contract shapes and the deterministic project-selection policy.

## Files

| File | What it is | How the pipeline uses it |
| --- | --- | --- |
| `jd_requirements_shape.json` | Placeholder-valued template for `jd_requirements.json`: a `job_key` string and a `concepts[]` list whose entries carry `term`, `category`, `required`, and `priority`. | Documents the exact shape job-requirement extraction must produce, which `tools/resume/v8/validate_strategy_contract.py` then checks for a job. |
| `resume_payload_shape.json` | Placeholder-valued template for `resume_payload.json`: `experience_bullet_orders`, `experience_alternate_bullets`, `project_bullet_orders`, and a `selected_project` block. | Documents the generated-resume contract. Every key is an opaque `owner_id`, so no candidate identity appears in the shape. |
| `cover_letter_payload_shape.json` | Placeholder-valued template for `cover_letter_payload.json`: `company`, `role`, `salutation`, `opening`, `experience_paragraphs`, `project_paragraph`, `closing`. | Documents the generated-cover-letter contract used by `tools/resume/v8/render_cover_letter.py`. |
| `project_selection_policy.json` | A `concept_bridges` table mapping a job-description concept to truthful alternative phrasings. | Passed to `tools/resume/v8/select_project.py` as `--policy` so the selector can tell whether a project's evidence supports a concept phrased differently from the candidate's own wording. |

`tools/resume/v8/extract_json_shape.py` produces files in this
placeholder style from a concrete JSON document.

## Where this sits in the flow

Batch run and validation — these shapes describe the per-job artifacts
produced between generation and deterministic validation.

## Private or git-ignored

Nothing here is git-ignored. The selectable-project **pool** is
deliberately absent: `project_selection_policy.json`'s own `_comment`
states it is derived at run time from the private
`master/runtime/runtime_resume_context.json` (any project entry whose
`project_status` is `selectable`), and
`tests/public/test_generic_pipeline.py` asserts the policy file contains
no `projects` key.

## Related documentation

- [`../README.md`](../README.md) — the rest of the shared configuration.
- [`../../docs/deterministic-validation.md`](../../docs/deterministic-validation.md) — the validation layer these contracts feed.
- [`../../docs/retrieval-and-grounding.md`](../../docs/retrieval-and-grounding.md) — the project-selection contract.
- [`../../docs/candidate-data-guide.md`](../../docs/candidate-data-guide.md) — the candidate schemas referenced by the shapes.
