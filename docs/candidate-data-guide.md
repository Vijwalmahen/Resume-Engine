# Candidate Data Guide

Technical reference for the schemas behind every file under `master/` and
`reference/`. For a beginner-friendly, step-by-step walkthrough, read
[`INSTRUCTIONS.md`](../INSTRUCTIONS.md) first — this document assumes you
already know *where* things go and explains *why* the schema is shaped
this way.

All examples below use the fictional "Candidate Example" dataset
committed at `master/examples/`. Every real file you create is
git-ignored and never leaves your machine.

---

## 1. Design principle: data-driven, not identity-driven

Nothing in `tools/` or `.claude/skills/` contains a hardcoded employer,
university, or project name. Every decision the pipeline makes about
*what may be edited* is derived from two fields you set on your own data:

- `tailoring_mode`: `"protected"` (verbatim, order-only edits) or
  `"tailorable"` (truthful rewording allowed).
- `project_status` (projects only): `"required"` (always rendered) or
  `"selectable"` (a candidate in the pool for the ONE dynamically-bound
  project slot per resume).

Everything downstream — the deterministic project selector, evidence
ownership resolution, the correction worker's editable-fields list, the
renderer's paragraph-by-paragraph behavior — reads these two fields. There
is no allowlist of "this employer is special" anywhere in the codebase.

---

## 2. `master/runtime/runtime_resume_context.json`

This is the **one file the pipeline actually reads at run time**. Every
other `master/*` file is a human-readable or structurally-adjacent source
you maintain by hand and keep in sync with this one.

```json
{
  "schema_version": "1.0",
  "candidate": { "full_name": "...", "email": "...", "phone": "...", "location": "...", "linkedin": "...", "github": "...", "portfolio": "..." },
  "education": [ { "id": "...", "institution": "...", "degree": "...", "field": "...", "location": "...", "dates": "...", "gpa": "...", "coursework": ["..."] } ],
  "experience": [ /* see below */ ],
  "projects": [ /* see below */ ],
  "skills": { "static_resume_lines": {"Languages": "..."}, "extended_for_dynamic_evidence": {"category": ["term", "..."]} },
  "truth_constraints": ["..."]
}
```

`tools/resume/v8/validate_runtime_context.py` enforces the top-level keys
and a total size budget. `tools/validate_candidate_setup.py` enforces
everything else described in this document.

### Experience entries

```json
{
  "id": "experience_1",
  "employer": "Example Research Lab",
  "title": "Machine Learning Research Intern",
  "location": "Example City, USA (Remote)",
  "resume_display_location": "USA (Remote)",
  "dates": "Jan 2025 -- May 2025",
  "tailoring_mode": "protected",
  "canonical_bullets": ["...", "...", "...", "..."],
  "technologies": ["Python", "pandas"]
}
```

- `id` must be unique across `experience` and is the `owner_id` your
  resume/cover-letter layout files reference.
- If `tailoring_mode` is `"protected"`, `canonical_bullets` is required
  and rendered **exactly as written**; only its display order may be
  permuted via `resume_payload.json`'s `experience_bullet_orders`.
- If `tailoring_mode` is `"tailorable"`, use
  `alternate_bullets_for_dynamic_tailoring` instead of `canonical_bullets`
  — the model may reword these truthfully per job, filled into
  `resume_payload.json`'s `experience_alternate_bullets`.

### Project entries

```json
{
  "id": "project_selectable_1",
  "name": "Project Meridian",
  "description": "Hybrid-search retrieval-augmented question-answering service.",
  "project_status": "selectable",
  "tailoring_mode": "tailorable",
  "alternate_bullets_for_dynamic_tailoring": ["...", "..."],
  "technologies": ["Python", "FastAPI", "pgvector"],
  "evidence_entities": ["Project Meridian"],
  "title_markers": ["project meridian"]
}
```

- `project_status: "required"` always pairs with `tailoring_mode:
  "protected"` and `canonical_bullets` — these projects are always on the
  resume, in every job.
- `project_status: "selectable"` always pairs with `tailoring_mode:
  "tailorable"` and `alternate_bullets_for_dynamic_tailoring` — these
  projects form the pool `tools/resume/v8/select_project.py` scores per
  job, and exactly one is bound to the resume's single selectable-project
  slot.
- `evidence_entities` (optional, defaults to `[name]`): which
  `evidence_bank.json` `entity` values count as this project's evidence
  when scoring JD coverage.
- `title_markers` (optional, defaults to the normalized `name`): accepted
  paraphrases of this project's title that still count as "the same
  project" when `tools/resume/v8/validate_project_selection.py`
  cross-checks the model's authored title against the deterministic
  binding.

You need **at least one** `selectable` project — the selector has nothing
to choose from otherwise (`tools/validate_candidate_setup.py` enforces
this).

---

## 3. `master/evidence_bank.json`

```json
{
  "items": [
    {
      "entity": "Project Meridian",
      "category": "project",
      "verified": true,
      "source": "candidate_facts.md#project-meridian",
      "fact": "Built a retrieval-augmented question-answering service ...",
      "skills": ["information retrieval", "hybrid search"],
      "technologies": ["Python", "FastAPI", "pgvector"]
    }
  ]
}
```

Each item is one atomic, verified fact. `entity` must match a project's
`name` (or `evidence_entities` override). `category` must be `"project"`
or `"research"` — other categories are ignored by the selector by design
(it only scores project/research evidence, never resume-formatting notes
or eligibility facts). `verified: true` is required; unverified items are
never used for scoring.

---

## 4. `master/metrics_registry.json`

A flat list of verified, quotable metrics, each tied back to an
`owner_id` (an experience/project `id`). This exists so a human reviewer
can audit "every number that could appear in a generated document" in one
place, separate from prose. The model is never permitted to invent a
number not already present here or in a canonical/alternate bullet.

---

## 5. `master/eligibility_facts.json`

Free-form but complete answers to work-authorization, relocation, and
availability questions, so `ELIGIBILITY`-category JD concepts (see
`docs/retrieval-and-grounding.md`) are answered from your own verified
facts, never guessed by the model.

---

## 6. `master/rejections.json`

```json
{
  "rejected_claims": [
    { "claim": "Production deployment to paying customers", "reason": "...", "added": "2026-01-10" }
  ]
}
```

An explicit, growing denylist: claims that must never appear in generated
content, typically because a past generation attempt fabricated or
overstated something and you corrected it here so it can never recur.
This is conceptually similar to a regression test for truthfulness.

---

## 7. `master/candidate_facts.md` and `master/canonical_resume_entries.md`

Free-text, human-authored counterparts to the structured JSON above.
Nothing reads these automatically — they exist so you (or an assisting
model, under your supervision, when you ask for help drafting the JSON
above) always have a complete narrative source of truth to check every
generated bullet against.

---

## 8. Template layout files (`reference/*_layout.json`)

See [`reference/README.md`](../reference/README.md) for the full
walkthrough of finding your own paragraph indices. The key invariant:
every `owner_id` in a layout file must match an `id` in
`master/runtime/runtime_resume_context.json` **with the same
`tailoring_mode`** — `tools/validate_candidate_setup.py` checks this
cross-reference for you.

---

## 9. Grounding and truthfulness enforcement

See [`docs/retrieval-and-grounding.md`](retrieval-and-grounding.md) for
how JD requirements are matched against this data, and
[`docs/deterministic-validation.md`](deterministic-validation.md) for how
the deterministic pipeline enforces that only `tailorable`/`selectable`
content is ever edited, and that a `GAP` (unsupported requirement) is
never silently converted into fabricated evidence.
