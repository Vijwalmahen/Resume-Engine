# `master/` — private candidate evidence

**Everything in this directory except `README.md` and `examples/` is your
real, private information and is excluded from Git** (see `.gitignore`).
Nothing you put here is ever committed, even accidentally.

**New here? Start with [`INSTRUCTIONS.md`](../INSTRUCTIONS.md)** at the
repository root — it walks through every field in every file below with a
small example. This file is a quick reference, not the full walkthrough.

## Getting started

```bash
python3 tools/bootstrap_candidate.py
```

copies the fictional example files below into their real locations (never
overwriting anything that already exists). Then edit each real file with
your own verified information, and run:

```bash
python3 tools/validate_candidate_setup.py
```

before running anything that spends money on model calls.

## Files

| Real file (private, git-ignored) | Example (public, committed) | Purpose |
| --- | --- | --- |
| `candidate_facts.md` | `examples/candidate_facts.example.md` | Free-text source of truth: identity, education, experience, projects, skills, truth constraints, written in your own words. Nothing reads this file automatically — it exists so you (or an assisting model, under your supervision) always have the full narrative to check every generated bullet against. |
| `candidate_profile.json` | `examples/candidate_profile.example.json` | Structured identity block (name, email, phone, location, LinkedIn, GitHub, portfolio). |
| `canonical_resume_entries.md` | `examples/canonical_resume_entries.example.md` | Human-readable copy of your approved, protected bullet wording — keep in sync with the `canonical_bullets` arrays in `runtime/runtime_resume_context.json` by hand. |
| `evidence_bank.json` | `examples/evidence_bank.example.json` | Structured, atomic, verified facts used by `tools/resume/v8/select_project.py` to decide which **selectable** project truthfully supports a given job's requirements. |
| `metrics_registry.json` | `examples/metrics_registry.example.json` | Every verified metric you're willing to have quoted, tied back to the experience/project `id` it belongs to. The model must never invent a metric that isn't here (or already embedded in a canonical/alternate bullet). |
| `eligibility_facts.json` | `examples/eligibility_facts.example.json` | Work authorization, relocation, and availability facts, so ELIGIBILITY-category JD requirements are answered truthfully instead of guessed. |
| `rejections.json` | `examples/rejections.example.json` | An explicit denylist of claims that must never appear in generated content (things you know are false, unsupported, or previously had to be corrected out). |
| `runtime/runtime_resume_context.json` | `examples/runtime_resume_context.example.json` | **The one file the pipeline actually reads at run time.** Structured education/experience/projects/skills, each experience and project entry carrying a stable `id`, a `tailoring_mode` (`protected` or `tailorable`), and — for projects — a `project_status` (`required` or `selectable`). Keep this in sync with the files above by hand; `tools/resume/v8/runtime_context_guard.py` only detects staleness, it does not regenerate this file for you. |

## Protected vs. tailorable, required vs. selectable

- An **experience** or **project** entry with `"tailoring_mode": "protected"`
  ships with fixed `canonical_bullets` that are rendered **verbatim** on
  every resume — only their display order may change.
- An entry with `"tailoring_mode": "tailorable"` ships
  `alternate_bullets_for_dynamic_tailoring` instead — truthful wording that
  may be re-authored per job, but never fabricated beyond what you wrote.
- A **project** additionally has `"project_status": "required"` (always
  rendered) or `"project_status": "selectable"` (a candidate in the pool
  the deterministic selector chooses ONE of, per job, for the resume's
  single dynamically-bound project slot).

See [`docs/candidate-data-guide.md`](../docs/candidate-data-guide.md) for
the full schema reference, and [`INSTRUCTIONS.md`](../INSTRUCTIONS.md) for
a beginner-friendly walkthrough.
