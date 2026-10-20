# `live13_11_recommendation` fixture

A complete, immutable set of per-job artifacts for one synthetic job,
built from the fictional "Candidate Example" dataset. It exists so
suites have a realistic, *post-fix* starting point for a pipeline run
without reading the shared, mutable `output/<job_key>/` directory, which
any later batch run targeting the same job key would silently overwrite.

The `live13-11-recommendation` job key is a historical label from the
development pass this fixture was written for. None of the content below
comes from a real run.

## Files

| File | What it holds |
| --- | --- |
| `jd_requirements.json` | The extracted job concepts, including required `Semantic recommendation systems` and `Hybrid search`. |
| `resume_payload.json` | The generated resume payload, keyed by `owner_id`. |
| `cover_letter_payload.json` | The generated cover-letter payload. |
| `resume_draft.md` | The rendered resume as Markdown, using only fictional identity values. |
| `v8_strategy_status.json` | The worker's status record: `STRATEGY_READY`, `selected_project_bound: project_selectable_1`, zero semantic corrections. |
| `project_selection.json` | The deterministic selector's own binding (`BINDING_REQUIRED_COVERAGE`) and the allowed project keys. |
| `payload_preflight.json` | A passing character-budget preflight report with per-field measurements and limits. |
| `payload_normalization.json` | A passing bullet-order normalization report with no conversions. |
| `v8_contract_validation.json` | A passing contract-validation report. |
| `v8_project_selection_validation.json` | A passing project-selection cross-validation report. |
| `ats_preflight.json` | The **post-fix** ATS report for this job: `gate_pass: true`, with `Hybrid search` already satisfied. |
| `shared_ats_evidence/verified_resume_evidence.json` | The evidence root the ATS evaluator reads for this fixture. |

## How the suites use it

- `test_v86_6_ats_bridge_and_unplaceable_evidence.py` evaluates the ATS
  report from `jd_requirements.json` plus `resume_draft.md` against this
  directory's evidence root, and copies the artifacts into a temporary
  job directory to run the real deterministic pipeline.
- `test_v86_8_stabilization.py` builds the same job directory with one
  field deliberately pushed over the character budget, to exercise the
  mechanical pre-trim.
- `test_v86_8_round5_action_verb_diversity.py` reads `resume_draft.md`
  and `resume_payload.json` for the action-verb diversity check.
- `test_v86_9_resume_quality_report.py` uses it as realistic input for
  the advisory diagnostics.

Because `ats_preflight.json` here is the already-fixed snapshot, the
*pre-fix* "Hybrid search" scenario lives in its own fixture,
[`../v866_unplaceable_evidence/`](../v866_unplaceable_evidence/README.md),
which that suite loads separately.

## Related documentation

- [`../README.md`](../README.md) — the other fixtures.
- [`../../README.md`](../../README.md) — the suites that read this one.
- [`../../../../../../docs/deterministic-validation.md`](../../../../../../docs/deterministic-validation.md) — what each of these reports gates.
