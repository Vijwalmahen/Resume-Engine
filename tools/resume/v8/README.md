# `tools/resume/v8/`

The V8 per-job pipeline: the guards that decide a run may start, the
deterministic project selector, the cover-letter renderer, and the
contract validators the V8.6 batch pipeline still reuses.

## Files

| File | What it does | How the pipeline uses it |
| --- | --- | --- |
| `runtime_context_guard.py` | `write-manifest` records the SHA-256 of `master/runtime/runtime_resume_context.json` and of each authoritative `master/` source into a manifest; `check` recomputes them and prints `RUNTIME CONTEXT: VALID` or exits `1` with `RUNTIME CONTEXT: STALE`. | The first gate before any paid call. `scripts/run_batch_with_cleaning.sh` refuses to start a batch unless `check` passes. It only *detects* staleness — it never regenerates the runtime context. |
| `build_runtime_ats_evidence.py` | Rebuilds `master/runtime/ats_evidence/verified_resume_evidence.json` from the runtime context, deliberately excluding truth constraints, rules and instruction prose, contact metadata, and generic experience-level `technologies` inventories. | Produces a conservative, V8-only evidence source so rule prose in `master/` is never mistaken for candidate evidence during ATS matching. |
| `select_project.py` | Derives the selectable-project pool at run time from every `projects[]` entry whose `project_status` is `selectable`, ranks each by how many *incremental* required job concepts it truthfully supports, and binds the winner when coverage is non-zero. Takes `--requirements`, `--output`, `--runtime-context`, `--evidence-bank`, `--aliases`, `--policy`. | The deterministic answer to "which project fills the one swappable slot". No project name is hardcoded; `tests/public/test_generic_pipeline.py` exercises it against the fictional example dataset. |
| `validate_project_selection.py` | Cross-checks the selected project across three independent sources: `resume_payload.json`'s `selected_project`, `v8_strategy_status.json`'s `selected_project_bound`, and the selector's own `project_selection.json`. Compares `owner_id` / `runtime_name` and optional candidate-supplied `title_markers` only. | Catches a generated payload that drifted from the binding the selector actually made. |
| `validate_strategy_contract.py` | Validates `jd_requirements.json`, `resume_payload.json`, `cover_letter_payload.json`, and `v8_strategy_status.json` against the V8 schema, with bullet counts and permutation sizes read from the candidate's own layout file rather than hardcoded. | The contract gate between generation and rendering. |
| `normalize_resume_payload.py` | Converts any 1-based bullet-order array the model produced into the canonical 0-based form for every protected `owner_id` the layout declares, and checks the dynamic fields exist structurally. | A deterministic repair that runs before validation, so an off-by-one indexing convention never costs a model call. |
| `render_cover_letter.py` | Mirrors the resume renderer for the cover letter: paragraph indices, which paragraphs are static identity/signature blocks (verified unchanged after editing), and which experiences get a dedicated paragraph all come from `reference/cover_letter_template_layout.json`. | The cover-letter render stage. `tests/public/test_public_repo.py` lists this file as critical. |
| `run_local_resume_pipeline.py` | The per-job local runner. Takes `--manifest` (a file listing job paths), runs the runtime-context guard and the evidence rebuild once, then drives each job through the stages and writes `.work/v8/local_pipeline_results.json`. | The V8 single-machine path that predates the V8.6 batch runner. Its fixed, non-per-job output paths are why it is not safe to run two copies at once. |
| `collect_corrections.py` | Reads a job manifest plus `.work/v8/local_pipeline_results.json` and assembles a correction batch at `--output` (default `.work/v8/correction_batch.json`). | Gathers the jobs that need a bounded correction round into one request. |
| `validate_runtime_context.py` | A standalone check that `master/runtime/runtime_resume_context.json` exists and is valid JSON, failing loudly otherwise. | A quick structural check, separate from the hash-based staleness guard. |
| `extract_json_shape.py` | Reduces a concrete JSON document (`--input`) to a placeholder-valued shape (`--output`). | How the templates in `config/v8/` are produced and refreshed. |

## Where this sits in the flow

Setup (the guards), batch run (selection, normalization, rendering), and
validation (the two contract validators). The V8.6 batch pipeline in
[`../v86/`](../v86/README.md) invokes several of these as subprocesses
rather than reimplementing them.

## Private or git-ignored

Every default input and output path here is git-ignored:

- reads `master/runtime/runtime_resume_context.json`, the authoritative
  `master/` sources, `master/evidence_bank.json`, and
  `reference/*_layout.json`;
- writes `master/runtime/ats_evidence/`,
  `master/runtime/runtime_resume_context.manifest.json`, `.work/v8/`,
  and per-job files under `output/<job_key>/`.

Only `config/` inputs (`ats_aliases.json`,
`v8/project_selection_policy.json`) are committed.

## Related documentation

- [`../README.md`](../README.md) — the renderer and guards these modules call.
- [`../v86/README.md`](../v86/README.md) — the batch pipeline that reuses them.
- [`../../../config/v8/README.md`](../../../config/v8/README.md) — the contract shapes validated here.
- [`../../../docs/retrieval-and-grounding.md`](../../../docs/retrieval-and-grounding.md) — the project-selection contract.
- [`../../../docs/deterministic-validation.md`](../../../docs/deterministic-validation.md) — the validation layer.
- [`../../../docs/candidate-data-guide.md`](../../../docs/candidate-data-guide.md) — the candidate schemas.
