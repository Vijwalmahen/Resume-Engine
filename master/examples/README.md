# `master/examples/`

The fictional "Candidate Example" dataset: a complete, valid starting
point that is committed in full, unlike the real candidate files beside
it.

Every value here is invented. The identity block uses
`candidate@example.com`, a `555`-prefixed phone number, and
`Example City, ST`; project and employer names are placeholders such as
"Project Meridian" and "Example Research Lab".

## Files

| File | Copied to (private, git-ignored) | What it holds |
| --- | --- | --- |
| `candidate_facts.example.md` | `master/candidate_facts.md` | Free-text source of truth: identity, education, experience, projects, skills, truth constraints. Nothing reads it automatically — it exists so a human always has the full narrative to check generated bullets against. |
| `candidate_profile.example.json` | `master/candidate_profile.json` | Structured identity block: name, email, phone, location, LinkedIn, GitHub, portfolio. |
| `canonical_resume_entries.example.md` | `master/canonical_resume_entries.md` | Human-readable copy of the approved, protected bullet wording, kept in sync with the `canonical_bullets` arrays in the runtime context by hand. |
| `evidence_bank.example.json` | `master/evidence_bank.json` | Atomic verified facts, each with an `entity` matching a project's name in the runtime context. Read by `tools/resume/v8/select_project.py` to score which selectable project truthfully supports a job. |
| `metrics_registry.example.json` | `master/metrics_registry.json` | Every metric that may be quoted, tied to the experience or project `owner_id` it belongs to. A metric not here (and not already inside a canonical or alternate bullet) may never appear. |
| `eligibility_facts.example.json` | `master/eligibility_facts.json` | Work authorization, relocation, and availability, so `ELIGIBILITY`-category requirements are answered from fact rather than guessed. |
| `rejections.example.json` | `master/rejections.json` | An explicit denylist of claims that must never appear in generated content, each with the reason it was rejected. |
| `runtime_resume_context.example.json` | `master/runtime/runtime_resume_context.json` | The one file the pipeline actually reads at run time: structured education, experience, projects, and skills, each entry carrying a stable `id`, a `tailoring_mode`, and — for projects — a `project_status`. |

Each file opens with a `_comment` (or an introductory paragraph) naming
its real destination and pointing at `INSTRUCTIONS.md` and
`docs/candidate-data-guide.md`.

## Where this sits in the flow

Setup. `python3 tools/bootstrap_candidate.py` copies each file to the
destination in the table above, never overwriting anything that already
exists, and `python3 tools/validate_candidate_setup.py` then checks the
result.

## Private or git-ignored

This directory is the exception inside `master/`: `.gitignore` excludes
`master/*` but re-includes `master/README.md`, `master/examples`, and
`master/examples/**`, so these example files are committed while every
real file is not. `tests/public/test_public_repo.py` asserts those
negations are present in `.gitignore` and that every JSON file here
parses.

## Related documentation

- [`../README.md`](../README.md) — the private directory these examples seed.
- [`../../INSTRUCTIONS.md`](../../INSTRUCTIONS.md) — the field-by-field walkthrough.
- [`../../docs/candidate-data-guide.md`](../../docs/candidate-data-guide.md) — the schema reference, written against this dataset.
- [`../../docs/GETTING_STARTED.md`](../../docs/GETTING_STARTED.md) — where bootstrapping fits in setup.
