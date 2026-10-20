# `config/`

Shared, candidate-agnostic configuration: the rules, shapes, and
terminology tables the pipeline reads at run time, none of which contain
any candidate's identity.

## Files

| File | What it is | How the pipeline uses it |
| --- | --- | --- |
| `active_resume_pipeline.json` | Declares `active_version`, the production entrypoints, and the production defaults (`input_dir`, `done_dir`, `max_workers`, tracker path). | Read as the single pointer to which pipeline version is current; `tests/public/test_public_repo.py` asserts that `active_version` is set. |
| `ats_aliases.json` | Canonical term → accepted surface forms (for example `machine learning` → `ML`). | Loaded by `tools/ats/preflight.py` via `--aliases` so a requirement phrased one way still matches evidence phrased another. |
| `skill_aliases.json` | A larger canonical-name → equivalent-surface-form table, restricted by its own `_comment` to genuine terminology equivalence only. | Terminology equivalence data for skill matching; non-equivalent technologies are deliberately never listed as aliases of each other. |
| `application_tracker_schema.md` | The workbook contract: which sheets exist, the required column order, and which columns are human-owned. | The written specification the tracker writers follow when creating or updating the Excel workbook in `tracking/`. |
| `resume_layout.md` | Authoritative rendering rules — font, header, contact row, margins, and section formatting. | Formatting reference for the rendered resume. It is deliberately generic: `tests/public/test_public_repo.py` asserts it hardcodes no candidate city. |
| `resume_rules.md` | Source-precedence order for canonical content plus the truth constraints ("never invent …"). | The tailoring/truth rulebook that sits alongside the deterministic validators. |
| `v8/` | JSON *shape* files for the V8 contracts plus the project-selection policy. | See the table below. |
| `v86/` | V8.6-only configuration. Has its own [`README.md`](v86/README.md). | Advisory diagnostic thresholds and the deterministic ATS concept bridges. |

### `v8/`

| File | What it is | How the pipeline uses it |
| --- | --- | --- |
| `jd_requirements_shape.json` | Placeholder-valued shape for `jd_requirements.json`: `job_key` plus a `concepts[]` list of `term` / `category` / `required` / `priority`. | The documented target shape for job-requirement extraction, enforced downstream by `tools/resume/v8/validate_strategy_contract.py`. |
| `resume_payload_shape.json` | Shape for `resume_payload.json`: `experience_bullet_orders`, `experience_alternate_bullets`, `project_bullet_orders`, and `selected_project`. | The documented target shape for generated resume content, keyed by `owner_id`. |
| `cover_letter_payload_shape.json` | Shape for `cover_letter_payload.json`: company, role, salutation, opening, per-experience paragraphs, project paragraph, closing. | The documented target shape for generated cover-letter content. |
| `project_selection_policy.json` | Domain `concept_bridges` only. Its `_comment` records that the selectable-project pool is derived at run time from candidate data, never listed here. | Read by `tools/resume/v8/select_project.py` via `--policy`. `tests/public/test_generic_pipeline.py` asserts this file contains no `projects` key. |

## Where this sits in the flow

Setup and batch run. Nothing here is generated; every file is edited by
hand and read by the pipeline at run time.

## Private or git-ignored

Nothing in `config/` is git-ignored — it is all shared, candidate-agnostic
configuration and is committed in full. The private counterparts live
under `master/` and `reference/` instead.

## Related documentation

- [`../README.md`](../README.md) — what the repository contains.
- [`../docs/architecture.md`](../docs/architecture.md) — where configuration is read in the flow.
- [`../docs/deterministic-validation.md`](../docs/deterministic-validation.md) — the validators these shapes feed.
- [`../docs/candidate-data-guide.md`](../docs/candidate-data-guide.md) — the private schemas these shapes refer to.
- [`v86/README.md`](v86/README.md) — the V8.6-only configuration.
