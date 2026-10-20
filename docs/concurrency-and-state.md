# Concurrency and State Isolation

## Why this matters

The batch pipeline can process multiple jobs concurrently, but concurrency is only safe when each job keeps its own state.

V8.6.8 therefore treats per-job and per-attempt artifacts as isolated runtime state rather than shared mutable truth.

The core invariant is:

**One job must never inherit another job's selections, correction state, or intermediate artifacts.**

## Batch concurrency model

The batch runner accepts a worker limit through:

    --max-workers

Each worker processes an independent job attempt.

Shared candidate context can be prepared once for the batch, but job-specific analysis, generation, validation, correction, rendering, and status remain isolated.

Conceptually:

    shared candidate context
              |
      +-------+-------+
      |       |       |
      v       v       v
    Job A   Job B   Job C
      |       |       |
    isolated isolated isolated
    state    state    state

This gives the pipeline parallel throughput without making semantic decisions global.

## Shared vs isolated state

The system deliberately separates reusable read-only context from attempt-local mutable state.

### Shared batch context

Examples include:

- validated candidate facts;
- canonical evidence sources;
- reusable configuration;
- static validation rules; and
- batch-level metadata.

### Job-local state

Examples include:

- job requirements;
- project selection;
- generated payloads;
- correction packets;
- correction counters;
- rendering intermediates;
- validation results; and
- final status.

Only the first category should be reused across jobs.

## Live queue refill

The worker pool does not need to wait for an entire wave of jobs to finish before accepting more work.

As workers complete, capacity can be reused for remaining jobs.

This improves wall-clock performance compared with strictly sequential execution while still respecting the configured concurrency limit.

## Attempt-local reset

Retries are treated as fresh attempts.

Before a new attempt begins, state that belongs only to the prior attempt must be cleared or recomputed.

This prevents stale artifacts from creating false failures or false successes.

Examples include:

- old correction counters;
- previous correction packets;
- stale project selections;
- intermediate payloads; and
- temporary validation outputs.
## Project-selection isolation

Project selection is job-specific.

A project chosen for one role must not silently determine the project selected for another role simply because an old artifact is present.

The pipeline therefore validates or recomputes selection state for the current job.

This prevents a structurally valid but semantically stale selection from contaminating a new application.

## Correction-state isolation

Correction rounds are also attempt-local.

A fresh job or retry must not inherit a previous attempt's correction budget.

Without this rule, a valid new attempt could incorrectly fail immediately because an old counter makes the system believe the correction limit has already been exhausted.

V8.6.8 includes regression coverage for this class of bug.

## Failure containment

A batch contains independent job outcomes.

One job failing must not automatically invalidate unrelated successful jobs.

This enables:

- partial batch completion;
- per-job failure reporting;
- independent retries;
- clearer debugging; and
- better use of concurrent workers.

A truthful `STRATEGY_FAILED` result for one poor-fit job can coexist with successful completion of other jobs in the same batch.

## Queue safety

Jobs begin in:

    jobs/batch_inbox/

A successfully completed job can move to:

    jobs/batch_done/

A failed or unresolved job should remain available for inspection or retry rather than being falsely marked complete.

Queue movement is therefore part of the success contract, not merely file housekeeping.

## Observability

Batch metrics expose per-job state so concurrency does not hide what happened.

Useful fields include:

- job;
- status;
- correction rounds;
- deterministic runs; and
- batch-level success/failure counts.

This makes it possible to distinguish:

- semantic failure;
- deterministic blocker;
- correction usage;
- artifact failure; and
- truthful bad-fit rejection.

## Why concurrency is bounded

Higher concurrency is not always better.

The worker limit exists to balance:

- throughput;
- model/API pressure;
- local CPU and memory use;
- document-rendering contention;
- debugging clarity; and
- predictable resource usage.

The goal is controlled parallelism, not maximum fan-out.

## Design summary

V8.6.8 treats concurrency as an orchestration problem and state isolation as a correctness requirement.

The system shares only reusable read-only context while keeping job-specific mutable state isolated.

That lets the pipeline process multiple applications in parallel without allowing one job's artifacts, corrections, or selections to silently influence another.
