# Failure Handling and Recovery

## Design principle

Failure is treated as a first-class system state.

The pipeline is not optimized to force every job into a successful resume. If the available candidate evidence cannot support a requirement, the correct result can be a warning, a correction request, or an explicit failure.

That is intentional.

**A truthful failure is better than a fabricated success.**

---

## Failure model

At a high level, every job moves through deterministic validation after semantic generation.

    Generated candidate payload
              |
              v
       Deterministic checks
              |
        +-----+-----+
        |           |
        v           v
      PASS       BLOCKER
        |           |
        |      Can it be fixed
        |      deterministically?
        |        /       \
        |      yes        no
        |      |           |
        |      v           v
        |   local fix   semantic correction
        |                  |
        |             correction succeeds?
        |                /       \
        |              yes        no
        |              |           |
        v              v           v
    RESUME_READY   RESUME_READY   explicit failure

The system separates:

- acceptable output;
- deterministic/mechanical defects;
- semantically correctable defects;
- unsupported candidate gaps; and
- terminal pipeline failures.

---

## Truthful bad-fit rejection

One of the most important failure behaviors is refusing to manufacture qualifications.

If a job requires experience that is not supported by candidate evidence, the pipeline does not silently rewrite the candidate to make the requirement appear satisfied.

A genuine unsupported requirement remains unsupported.

For sufficiently poor-fit jobs, the strategy layer can terminate with an explicit bad-fit result such as:

    STRATEGY_FAILED

This is considered correct behavior, not a pipeline crash.

The validated V8.6.8 stress run intentionally included a poor-fit senior/CUDA-style role. That job failed while the other supported jobs completed successfully.

The purpose of this behavior is to preserve factual integrity even when doing so lowers the apparent success rate.

---

## Supported-but-missing vs unsupported

The pipeline distinguishes two very different failure cases.

### Supported but missing

The candidate has valid evidence for the concept, but the generated resume failed to express it.

This can be eligible for correction.

### Unsupported

The underlying candidate evidence does not support the requirement.

This must remain a gap.

No correction worker is allowed to convert an unsupported qualification into candidate history.

This distinction prevents ATS optimization from becoming hallucination.

---

## Deterministic repair before model repair

A model call is not the first response to every validation failure.

When a defect can be fixed reliably in Python, the pipeline prefers a deterministic repair.

Examples include classes of problems such as:

- payload normalization;
- mechanical formatting issues;
- known placeholder cleanup;
- bounded text trimming;
- state cleanup;
- action-verb composition checks; and
- other contract-level validation.

This reduces:

- cost;
- latency;
- model variance; and
- unnecessary regeneration.

Only failures that genuinely require semantic reasoning should reach the correction worker.

---

## Bounded semantic correction

When deterministic validation finds a semantically correctable blocker, the pipeline can invoke a correction worker.

The correction worker is deliberately constrained.

It is not given unrestricted authority to rebuild the candidate profile.

Instead, correction operates over an explicit correction packet and a bounded set of editable fields.

This provides two protections:

1. the correction request remains focused on the actual blocker;
2. unrelated, already-valid content is less likely to drift.

Correction rounds are also bounded.

If the system exceeds its permitted correction behavior, it fails explicitly instead of entering an uncontrolled regeneration loop.

---

## Correction-limit handling

The pipeline tracks correction state explicitly.

A correction-limit failure must mean that the current attempt actually consumed the permitted correction budget.

V8.6.8 includes regression coverage for a historical bug shape where stale correction-round state could make a fresh attempt look as though it had already exhausted its correction budget.

The hardening work ensures that attempt-local correction state is reset correctly.

This prevents errors such as:

- a new job inheriting an old correction counter;
- a repeated job starting from the wrong correction round; or
- a valid new attempt immediately reporting correction exhaustion.

---

## Attempt-local state reset

Before a new attempt, attempt-local artifacts are cleared or recomputed.

State that belongs to one run must not silently leak into another.

Examples of attempt-local state include correction artifacts and selection-related intermediate files.

This is especially important when:

- the same job is retried;
- multiple jobs share similar identifiers;
- batches run concurrently; or
- a previous run failed midway through the pipeline.

The state-isolation tests specifically exercise these cases.

---

## Project-selection isolation

Project selection is job-specific.

A project chosen for Job A must not silently determine the project used for Job B.

The pipeline therefore treats selection state as attempt-sensitive and recomputes it when required.

This avoids a subtle class of cross-job contamination where a valid selection artifact from an earlier attempt could appear structurally valid but be semantically wrong for the current job.

---

## Company identity handling

Company identity is validated rather than blindly trusted.

The system contains logic for recognized placeholder values and can backfill a company name when the posting provides a reliable literal source.

The repair is deliberately narrow.

If the posting does not provide a parseable company identity, the system does not fabricate one.

This gives the system three useful behaviors:

- preserve genuine company names;
- replace known placeholders only when grounded; and
- leave unresolved identity unresolved when evidence is insufficient.

---

## Negated evidence

A keyword appearing in candidate evidence does not automatically mean the candidate possesses that skill or qualification.

The ATS layer includes negation-aware handling so that evidence such as:

    "No production Kubernetes experience"

must not be interpreted as positive Kubernetes experience.

Negation is evaluated locally rather than allowed to leak across unrelated sentences.

This prevents naive keyword matching from creating false support.

---

## Rendering failures

Semantic success is not enough.

The pipeline still has to create usable application artifacts.

Rendering and finalization are therefore part of the success contract.

If document creation or artifact validation fails, the job should not be reported as a successful completed application package.

The deterministic pipeline and finalization stages keep artifact-level failure separate from semantic generation.

This prevents a successful model response from masking a broken deliverable.

---

## Queue behavior

The job queue distinguishes completed work from unsuccessful work.

A successfully processed job can be moved from:

    jobs/batch_inbox/

to:

    jobs/batch_done/

A failed job should not be falsely treated as completed merely because processing began.

This preserves a recoverable workflow: unresolved jobs remain visible for inspection or retry.

---

## Batch-level resilience

A batch contains independent job attempts.

One job failing should not automatically invalidate unrelated successful jobs.

The batch runner therefore records per-job status and metrics.

This provides:

- partial batch completion;
- isolated failure reporting;
- independent retries; and
- visibility into correction behavior.

The validated 10-job stress run demonstrated this behavior: nine supported jobs completed while one intentionally unsupported job failed truthfully.

---

## Failure observability

The V8.6 workflow writes structured artifacts and batch metrics that make failure states inspectable.

Useful operational signals include:

- per-job status;
- correction rounds;
- deterministic validation runs;
- request counts;
- batch-level success/failure totals; and
- explicit strategy or validation failure states.

This makes failures diagnosable rather than reducing every unsuccessful run to a generic model error.

---

## Failure taxonomy

A useful way to reason about the system is:

| Failure class | Example | Preferred response |
| --- | --- | --- |
| Truth/evidence gap | Candidate lacks required experience | Preserve gap or reject |
| Supported-but-missing evidence | Valid skill omitted from draft | Bounded correction |
| Mechanical payload defect | Formatting/size/shape issue | Deterministic repair |
| Placeholder identity | Company placeholder with reliable source | Deterministic backfill |
| Unresolvable identity | No reliable company source | Do not fabricate |
| Stale attempt state | Old correction/selection artifact | Reset/recompute |
| Correction exhausted | Semantic blocker remains after limit | Explicit failure |
| Rendering/artifact failure | DOCX/PDF cannot be finalized | Do not mark ready |
| Poor-fit job | Required background unsupported | `STRATEGY_FAILED` |

---

## Why failure handling matters in an LLM system

A naive LLM workflow often treats regeneration as the universal recovery strategy:

    bad output -> ask the model again

That approach has several problems:

- repeated model cost;
- unpredictable latency;
- output drift;
- no guarantee that the underlying evidence problem is solvable;
- accidental fabrication under pressure to "fix" a resume; and
- difficult debugging.

V8.6.8 instead asks:

1. Is this actually wrong?
2. Is the required evidence available?
3. Can Python repair it deterministically?
4. Does it truly require semantic correction?
5. Has the correction budget already been used?
6. If it cannot be fixed truthfully, should the job fail?

This makes recovery policy explicit and testable.

---

## Safety property

The most important failure invariant is:

**The system must never convert missing evidence into invented evidence simply to make the pipeline succeed.**

The pipeline is therefore allowed to produce fewer successful resumes than a fully unconstrained generator.

That is a deliberate tradeoff.

The goal is not maximum completion rate.

The goal is maximum useful automation subject to factual correctness.

---

## Summary

Failure handling in V8.6.8 is built around:

- truthful bad-fit rejection;
- supported-vs-unsupported evidence distinction;
- deterministic repair before model repair;
- bounded semantic correction;
- correction-budget enforcement;
- attempt-local state reset;
- project-selection isolation;
- grounded company identity repair;
- negation-aware evidence checks;
- artifact-level success requirements;
- queue-safe failure behavior; and
- per-job batch observability.

The result is a workflow that can fail explicitly, locally, and recoverably instead of hiding errors behind repeated generation.
