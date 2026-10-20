# Security and Privacy

## Security model

This project processes sensitive career and application data.

The public repository is therefore designed to expose the pipeline's engineering logic without exposing the private runtime data used by a real candidate.

The main boundary is:

**Public code can be inspectable. Private candidate data should not be part of the public Git history.**

## Private runtime data

The following categories are treated as local-only or private runtime state:

- candidate evidence and master facts;
- personalized resume and cover-letter templates;
- real job input queues;
- generated resumes and cover letters;
- tracking spreadsheets;
- temporary work directories;
- historical runtime artifacts; and
- local backup or freeze snapshots.

These are not required to understand the public source architecture.

## Git ignore boundary

The repository's `.gitignore` excludes key private and runtime paths.

The exclusions cover classes such as:

- `.env` and environment-variable files;
- `__pycache__/` and local Python caches;
- virtual environments;
- `.ds_store`;
- private `master/` and `reference/` content;
- .work/` generated runtime state;
- `output/` generated artifacts;
- tracking workbooks;
- real job queue inputs;
- queue state; and
- generated `docx` and `pdf` files.

The purpose is to make accidental publication difficult by default.

## Secret handling

Real authentication credentials must never be committed to the repository.

The public copy includes an `.env.example` file that documents the boundary without storing real credentials.

The LLM runtime uses the local Claude CLI environment for authentication.

The repository does not require a committed API key.

## Synthetic test credentials

Some tests need values that look like credentials to exercise environment or secret-handling logic.

The public repository uses obviously fake values for those cases.

This keeps tests realistic without creating ambiguity about whether a string could be a real secret.
## Identity sanitization

The GitHub copy is sanitized so that test fixtures and examples do not need the real candidate's private phone, email, home path, or personal repository URL.

Public fixtures use generic values such as:

- `example.com` email addresses;
- `github.com/example` style repository URLs;
- generic home paths; and
- fake test credentials.

The goal is not to hide the system's behavior.

The goal is to remove unnecessary personal data from the public development context.

## Private templates

The production rendering pipeline uses personalized DOCX templates.

Those templates are local runtime inputs and are not part of the public repository.

This prevents a public clone from inadvertently inheriting personal contact details or private formatting.

The one exception is `reference/examples/resume_template.example.docx`, a
wholly fictional example template committed so a new user can exercise the
renderer before designing their own. It carries placeholder contact
details only (`Candidate Example`, `candidate@example.com`,
`(555) 010-2000`, `Example City, ST`) and its document properties contain
no author or company name. It is the only tracked binary in the
repository. No example cover-letter template is shipped.

## Private candidate evidence

The evidence bank and candidate facts are the most sensitive information in the system.

They can contain detailed career history, metrics, eligibility facts, contact information, and other data used for resume generation.

The public repository exposes the retrieval, grounding, validation, and orchestration code, but not the real candidate evidence store.

## Pre-push safety checks

Before publication, the public copy should be checked for:

- exact private phone numbers;
- private email addresses;
- absolute personal home paths;
- real API keys or key-shaped secrets;
- private candidate records;
- real job queue contents;
- generated resumes or cover letters; and
- personalized DOCX/PDF templates.

The public test runner also includes repository-safety checks to catch some of these conditions automatically.

## Release strategy

The safest publication path is:

1. prepare a sanitized sibling copy;
2. run privacy and secret scans;
3. run the portable public tests;
4. review the final staged file list;
5. initialize Git only after the clean copy is approved;
6. create the remote repository as private first; and
7. only consider public visibility after remote verification.

This avoids trying to sanitize private data after it has already entered Git history.

## Security principle

The public repository should contain enough information to understand, test, and develop the system.

It should not contain enough information to reconstruct the private candidate workspace.

## Resume-quality reports

Each job's advisory diagnostics are written to
`output/<job_key>/resume_quality_report.json`. In this repository that
path is covered by the `output/` ignore rule, so the Git boundary above
applies to it unchanged. A **deployment that is not a Git repository has
no such protection**, and the ignore rule cannot be relied on there.

### What a report actually contains

Measured against a populated synthetic report, the following candidate
content is **absent by construction** -- the report stores identifiers
and measurements, never prose:

- the candidate's name, email, phone or location;
- any verbatim resume bullet text;
- employer, institution or project names (when the candidate's runtime
  entries carry `id` fields);
- registry descriptions or source citations.

The following candidate-derived content **is** present:

- **verified measurement values** (`45ms`, `70%`, `4hours`) for any
  metric reported as possibly omitted;
- **opaque owner identifiers** (`experience_1`, `project_selectable_a`)
  and registry metric ids;
- **field identifiers** (`projects.bullet.0`) and bullet character
  counts;
- **opening action verbs** that repeat above the limit;
- **employment-gap intervals** as year-month strings;
- the **job key**, which in a real deployment typically encodes the
  employer and role applied to;
- the **SHA-256** of the shipped resume PDF.

One case deserves explicit attention: `timeline_audit` labels an
interval using an entry's `id`, falling back to its `institution` or
`employer` when no `id` exists. On a runtime context whose entries have
no `id` field, a timeline finding therefore carries **employer and
institution names**. That fallback exists so the finding is legible
rather than anonymous; it is not a defect, but it does change what the
file holds.

Across many jobs the job keys alone amount to an application history.
That aggregate, rather than any single field, is the most sensitive
thing the reports hold.

### Recommended storage and access policy

This is a recommendation for a local, single-user application workflow.
It changes no setting in any deployment and claims no guarantee the code
enforces.

1. **Keep reports inside the existing private runtime area.** They are
   written next to the artifacts they describe and should not be copied,
   synced, or collected into a separate reporting directory.
2. **Treat the report as being exactly as sensitive as the resume PDF
   beside it.** Whatever protects `output/` -- disk encryption, an
   unsynced local path, file permissions -- is what protects the report.
   Nothing additional is implied.
3. **Do not rely on the Git ignore rule outside a Git repository.** In a
   non-Git deployment, the ignore boundary documented above simply does
   not exist; the directory's own location and permissions are the whole
   control.
4. **Never attach a report to an application, a bug report, or a shared
   log.** It is a private review aid, not a deliverable.
5. **Prune reports with their job directories.** They have no value once
   the application is closed out and should not outlive the artifacts
   they describe.
6. **If reports are ever shared for debugging, share the `checks` array
   and `summary_by_severity` only.** Those carry outcomes and reasons
   without measurement values, owner identifiers, or the job key.

## Summary

The security and privacy model is built around:

- separating source code from private runtime data;
- keeping real credentials out of Git;
- sanitizing public test fixtures;
- excluding personalized document templates;
- keeping candidate evidence local;
- running pre-push secret and identity checks; and
- starting the remote repository as private.
