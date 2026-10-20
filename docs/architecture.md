# Architecture

## Overview

The V8.6.8 pipeline separates semantic reasoning from deterministic enforcement.

The LLM interprets a job description and produces evidence-constrained dynamic content. Python code then checks whether that output satisfies the pipeline's contracts before rendering or finalization.

This reduces the amount of behavior that depends on unconstrained model judgment.

## Processing flow

Job description
-> batch runner
-> shared candidate context
-> JD requirements analysis
-> evidence-constrained content generation
-> deterministic validation
-> bounded correction when required
-> DOCX/PDF rendering
-> finalization
-> application tracking

## Main components

### Batch orchestration

`tools/resume/v86/run_batch.py`

Responsibilities include:

- scanning Markdown job descriptions;
- creating per-batch work directories;
- building shared candidate context;
- coordinating concurrent workers;
- isolating attempt-local state;
- invoking the semantic and deterministic phases;
- moving successfully processed jobs to the done queue; and
- updating batch metrics.

### Semantic worker

`tools/resume/v86/semantic_worker.py`

The semantic phase performs job-description interpretation and evidence-based tailoring.

Its output is constrained by candidate evidence and pipeline rules. Unsupported qualifications are not supposed to be invented.

### Deterministic pipeline

`tools/resume/v86/deterministic_pipeline.py`

This stage coordinates local checks such as:

- strategy validation;
- selected-project binding;
- ATS preflight;
- evidence ownership;
- payload limits;
- company identity;
- action-verb diversity;
- rendering; and
- correction requests.

### Correction worker

`tools/resume/v86/correction_worker.py`

Corrections are bounded. The worker receives an explicitly constrained correction packet rather than unrestricted authority to rewrite candidate history.

### ATS checks

`tools/resume/v86/ats_preflight_v86.py`

The ATS layer distinguishes between supported evidence, missing-but-supported concepts, and unsupported concepts.

A real evidence gap must remain a gap.

### Rendering

Resume and cover-letter rendering happens after the semantic payload passes deterministic checks.

Personalized DOCX templates are local-only inputs under `reference/` and are intentionally excluded from Git.

### Finalization

`tools/resume/v86/finalize_local.py`

The finalization stage performs artifact-level checks and finishing steps before tracker updates and completion.

It hands each rendered artifact to a local watermark-cleaning HTTP service,
health-checked once per batch, and promotes a cleaned file only when the
extracted text is identical and the resume PDF is still exactly one page.
When the service is unavailable the stage is skipped and the job still
completes. See `docs/GETTING_STARTED.md` section 9, "Watermark cleaning".

### Advisory diagnostics

`tools/resume/v86/resume_quality_report.py`, `jd_match_report.py`,
`recommendation_guard.py`, `template_style_audit.py`

A reporting layer runs alongside the gate, on the deterministic success
path and again after finalization. It is architecturally separate from
enforcement: nothing in it can fail a job, change a status, trigger a
correction round, or spend a model call.

The separation is deliberate. The ATS preflight **gates** using a coarse
three-state classification; the JD-match report **explains**, per
requirement, on two independent axes (how the requirement surfaces in the
resume, and whether master evidence can back it). Both read the same
`jd_requirements.json` and reuse the same matching primitives from the
frozen `tools/ats/preflight.py`, so they cannot disagree about whether a
phrase is present.

The recommendation guard sits in front of any proposed rewrite a human
would be shown, rejecting by default and accepting only wording whose
measurements and distinctive terms already exist in the evidence corpus.
The template style audit diffs a rendered document against its own
template rather than against a house style.

The consolidated `resume_quality_report.json` folds these together into one
job-local, git-ignored file whose overall value is a diagnostic state
(`PASS` / `REVIEW` / `INCOMPLETE`) and never a numeric score.

## State isolation

Attempt-local artifacts are reset between runs so that one job or previous attempt cannot silently influence another.

This behavior is covered by the portable state-isolation regression tests.

## Private runtime inputs

The public repository intentionally excludes:

- candidate evidence under `master/`;
- personalized templates under `reference/`;
- generated work under `.work/`;
- generated output under `output/`;
- application history under `tracking/`; and
- real job queue contents.

These are runtime inputs or private state, not public source code.

## Public test contract

The supported portable test entrypoint is:

    python3 tests/run_public_tests.py

Historical V8.x tests remain in the tree for engineering traceability, but some require private development snapshots or runtime artifacts that are not part of a clean public checkout.
