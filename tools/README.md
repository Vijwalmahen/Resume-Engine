# `tools/`

Every executable the pipeline runs: candidate setup, the frozen matching
primitives, job intake, and the resume/cover-letter pipelines.

## Files in this directory

| File | What it does | How the pipeline uses it |
| --- | --- | --- |
| `bootstrap_candidate.py` | Copies the fictional example files from `master/examples/` into their real, private, git-ignored locations under `master/` (and the layout examples under `reference/`), so a new user starts from a complete, valid dataset. Never overwrites an existing file unless `--overwrite` is passed, and prints a warning per file when it does. Safe to run repeatedly. | Step one of setup. `tests/public/test_generic_pipeline.py` asserts it is idempotent and never silently overwrites. |
| `validate_candidate_setup.py` | Checks that every required private candidate file exists, parses, and has the shape the deterministic pipeline expects, including that each `owner_id` in a layout file matches an `id` with the expected `tailoring_mode` in the runtime context. Makes no network, API, or model call. Exit `0` means every hard check passed; exit `1` means at least one failed. | Run before spending anything on model calls. `tests/public/test_generic_pipeline.py` asserts it never crashes and always prints its banner. |

## Subdirectories

| Directory | What it holds |
| --- | --- |
| [`ats/`](ats/README.md) | The frozen deterministic matching primitives — normalization, alias expansion, and requirement-versus-evidence coverage. |
| [`intake/`](intake/README.md) | Offline job intake: URL classification, de-duplication, normalized job files, and the `Intake` worksheet. |
| [`resume/`](resume/README.md) | Rendering and QA for the resume and cover letter, plus the V8 per-job pipeline (`v8/`) and the V8.6 batch pipeline (`v86/`). |

## Where this sits in the flow

All of it. `bootstrap_candidate.py` and `validate_candidate_setup.py` are
setup; `intake/` is intake; `resume/v86/` is the batch run; `ats/` and
`resume/`'s validators are validation; `resume/v86/benchmark_report.py`
and the advisory diagnostics are reporting.

## Private or git-ignored

- `tools/watermarks-remover/` is git-ignored. It is the default location
  for a local checkout of the external watermark-cleaning service, which
  is **not vendored here** — you supply it yourself and check its own
  README and license.
- Both setup scripts write into `master/` and `reference/`, which are
  git-ignored apart from their `README.md` files and the committed
  examples.
- `__pycache__/` and `*.py[cod]` are git-ignored throughout.

## Related documentation

- [`../README.md`](../README.md) — what each tool is for, in context.
- [`../docs/GETTING_STARTED.md`](../docs/GETTING_STARTED.md) — the ordered setup checklist that runs these scripts.
- [`../INSTRUCTIONS.md`](../INSTRUCTIONS.md) and [`../docs/candidate-data-guide.md`](../docs/candidate-data-guide.md) — the candidate data these scripts create and validate.
- [`../master/README.md`](../master/README.md) and [`../reference/README.md`](../reference/README.md) — the private directories they write into.
