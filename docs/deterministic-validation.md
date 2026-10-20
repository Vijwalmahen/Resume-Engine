# Deterministic Validation

## Why this layer exists

The LLM is responsible for semantic interpretation and evidence-grounded writing. It is not the final authority on whether a generated resume is valid.

V8.6.8 places deterministic Python checks after generation so that important contracts are enforced consistently across jobs and runs.

The design principle is:

**Use probabilistic models for semantic work; use deterministic code for enforceable rules.**

## Validation flow

    semantic generation
           |
           v
    candidate payload
           |
           v
    deterministic checks
           |
      +----+----+
      |         |
     pass     blocker
      |         |
      v         v
    render   local repair or
             bounded correction

This separation prevents a fluent model response from being treated as correct merely because it looks plausible.

## Main validation responsibilities

The deterministic layer coordinates checks for:

- strategy validity;
- evidence ownership;
- project selection;
- ATS concept coverage;
- payload structure and length;
- company identity;
- action-verb composition;
- correction state;
- attempt isolation;
- rendering readiness; and
- final artifact integrity.

## Evidence ownership

Generated claims must remain attributable to approved candidate evidence.

The validator is designed to reject transformations that cross the boundary from rephrasing into fabrication.

A qualification that is not supported by the evidence store must remain unsupported.
## ATS validation

ATS optimization is constrained by factual support.

The pipeline distinguishes between:

- **supported and present** — the candidate has evidence and the resume expresses it;
- **supported but missing** — the candidate has evidence but the draft omitted it;
- **unsupported** — the evidence store does not support the qualification.

Only the second category is a normal candidate for semantic correction.

This prevents keyword coverage from overriding truthfulness.

## Project-selection validation

Project selection is job-specific.

The selected project must satisfy the current job strategy rather than inherit a structurally valid artifact from a prior attempt.

Selection artifacts are therefore validated and recomputed when required.

## Company identity validation

Company names are not trusted blindly.

Known placeholders may be repaired only when a reliable literal company source exists in the job posting or job packet.

If the company identity cannot be grounded, the validator must not invent one.

## Payload constraints

Generated content is checked against structural and size expectations before final rendering.

Mechanical defects should be fixed deterministically where possible rather than consuming another model request.

Examples include normalization, bounded trimming, and contract-level shape corrections.

## Action-verb composition

The validator checks composition-level action-verb repetition rather than relying only on prompt instructions.

This is important because individually strong bullets can still produce a repetitive resume when viewed as a whole.

The later V8.6.8 hardening added composition-aware behavior so this class of issue can be caught before finalization.
## Advisory resume-quality diagnostics

Separate from every gate above, the pipeline also writes one consolidated,
**advisory** `resume_quality_report.json` per job into that job's own
git-ignored runtime directory.

It reports, with per-field identifiers rather than a single whole-document
verdict:

- whether the text a parser extracts from the resume PDF still contains
  every intended bullet, heading and line, and in the intended order;
- a verified measurement that an approved source bullet carries but the
  corresponding final tailored bullet no longer communicates;
- unusually short substantive bullets;
- intervals between consecutive experience periods, classified against
  the candidate's own dated education periods; and
- the already-computed action-verb and QA/density results, restated, not
  recomputed.

Three properties keep it separate from validation proper:

1. **Nothing here can fail a job.** No finding changes a status, spends a
   correction round, or triggers a model call. Gate semantics, the
   two-round correction budget, and the success-status rules are exactly
   what they were.
2. **Nothing here is invented.** A measurement is only reported as
   possibly omitted when the candidate's own trusted records already tie
   it to that specific entry; anything else is `not_evaluated` with a
   reason rather than a guess. No number, outcome or employment period is
   ever proposed.
3. **It names the artifact it actually read.** Finalization replaces
   `resume.pdf` in place, so the report records `artifact.stage`
   (`pre_finalization`, `finalized`, or `finalization_not_applied`) and
   the file's `sha256`. It never claims to have validated a finalized PDF
   it did not inspect.

There is no numeric score: the report's only overall value is a
diagnostic state of `PASS`, `REVIEW` or `INCOMPLETE`.

## Correction gating

A correction request is created only when a blocker actually requires semantic repair.

The model is not asked to regenerate the entire resume for every defect.

The correction worker receives a bounded correction packet describing the actual failing condition and the editable scope.

This reduces:

- unnecessary model calls;
- latency;
- unrelated content drift; and
- accidental changes to already-valid evidence.

## State and attempt validation

Attempt-local state must belong to the current job attempt.

Old correction counters, project-selection artifacts, or intermediate files must not silently influence a fresh run.

The V8.6.8 regression suite includes state-isolation coverage for this behavior.

## Rendering as part of correctness

Semantic validity alone is insufficient.

A job is not considered ready unless the document pipeline can produce and finalize the required artifacts.

Rendering and finalization failures therefore remain real failures rather than being hidden behind a successful model response.

## Why deterministic validation matters

A naive LLM workflow often follows:

    generate -> trust output -> save

V8.6.8 instead follows:

    retrieve evidence
        -> generate
        -> validate
        -> repair only if necessary
        -> render
        -> validate artifacts
        -> finalize

That makes the system easier to test, cheaper to operate, and safer to use.

## Summary

The deterministic layer is the enforcement boundary of the pipeline.

The LLM can interpret and write, but Python decides whether the result satisfies the system's factual, structural, ATS, state, and artifact contracts.
