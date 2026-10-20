# Evolution of V8.6

## Why this page exists

The current V8.6.8 pipeline is the result of repeated hardening against failures that appear only when an LLM-powered workflow is used as a real system rather than a single prompt.

The major engineering shift was from:

    generate a tailored resume

to:

    retrieve evidence
        -> analyze the job
        -> generate constrained content
        -> validate deterministically
        -> correct only when necessary
        -> render
        -> validate artifacts
        -> finalize and track
The result is less a resume writer and more a constrained AI application pipeline.

## Shift 1: from prompting to evidence grounding

An early failure mode for resume automation is to ask an LLM to "be truthful" and then trust the result.

That is not strong enough.

V8.6 moved candidate history into controlled evidence sources and built a job-specific grounding context before generation.

The key rule became:

**Same experience, different emphasis.**

The model can reframe and reprioritize evidence, but it should not manufacture new candidate history.

## Shift 2: structured retrieval instead of unbounded context

The pipeline uses structured candidate evidence and validated runtime context rather than simply passing everything to the model.

This introduced:

- a shared candidate context;
- job-specific requirement analysis;
- evidence ownership;
- context slicing; and
- a clearer truth hierarchy.

This is a structured form of retrieval-augmented generation, but without depending on a vector database for the core resume workflow.

## Shift 3: LLM output became a proposal, not the final authority

As the pipeline grew, more rules were moved out of prompts and into deterministic validation.

This includes checks for classes of problems such as:

- unsupported claims;
- ATS concept coverage;
- project selection;
- company identity;
- payload structure;
- correction state;
- rendering readiness; and
- action-verb composition.

The LLM could still be used for what it does best: semantic interpretation and writing.

Deterministic code became the enforcement boundary.

## Shift 4: correction became bounded

A naive recovery strategy yields:

    bad output
        -> regenerate everything

That is expensive, slow, and can cause unrelated content to drift.

V8.6 moved to a more conservative model:

1. deterministic validation finds the blocker;
2. mechanical defects are fixed locally when possible;
3. only semantic blockers reach the correction worker;
4. correction rounds are bounded; and
5. the pipeline fails explicitly if the blocker cannot be repaired truthfully.

Bounded correction reduced model cost, latency, and unrelated regeneration.
## Shift 5: state isolation became a correctness property

Once multiple jobs and retries exist, stale state becomes a serious risk.

Historical hardening addressed cases where:

- a previous correction counter could affect a fresh attempt;
- a stale project selection could be structurally valid but wrong for the current job;
- intermediate artifacts could outlive the attempt that created them; and
- a retry could begin from the wrong logical state.

V8.6.8 hardening treats correction, selection, and other mutable artifacts as attempt-local state.

This led to a simple but important invariant:

**One job's state must never silently influence another job.**

## Shift 6: concurrency became controlled orchestration

Batch processing was added to improve throughput, but the goal was not unbounded fan-out.

The worker model was designed around:

- a configurable worker limit;
- shared read-only candidate context;
- job-local mutable state;
- live queue refill;
- per-job status; and
- failure containment.

This made it possible for one job to fail without destroying the results of the rest of the batch.

## Shift 7: failure became a valid outcome

An important maturity shift was stopping the system from treating every failure as something that must be repaired into success.

For a genuinely poor-fit job, the correct result can be:

    STRATEGY_FAILED

The validated 10-job stress run included an intentionally unsupported senior/CUD--style role.

That job failed truthfully while the other nine jobs completed.

That behavior validated the core safety property: the system should not invent missing experience just to improve its apparent success rate.

## Shift 8: cost and latency optimization moved into architecture

The normal path was reduced to two semantic requests:

1. requirements analysis; and
2. content generation.

A additional correction request is conditional rather than default.

Other efficiency mechanisms include:

- shared candidate context;
- job-specific context slicing;
- deterministic repairs;
- bounded correction;
- concurrent workers; and
- live queue refill.

This made cost and latency outcomes of architecture rather than merely prompt shortening.

## Shift 9: composition-level validation

One late hardening issue involved action-verb repetition.

Individual bullets could be reasonable while the full resume still had poor composition-level diversity.

V8.6.8 added composition-aware behavior so that this issue is caught earlier.

After this hardening, the final smoke run completed with:

- `RESUME_READY`;
- 2 model requests;
- 0 correction rounds; and
- no final action-verb over-limit blocker.

## Final V8.6.8 properties

The final system emerged with these key properties:

- evidence-grounded generation;
- structured retrieval;
- deterministic enforcement;
- bounded correction;
- truthful bad-fit rejection;
- attempt-local state;
- concurrent batch processing;
- queue-safe failure behavior;
- privacy-aware public packaging; and
- portable regression tests.

## Engineering lesson
The main lesson from V8.6 is that reliable LLM systems are built by moving important decisions out of unconstrained generation.

The model should be used where semantic reasoning is valuable.

Deterministic software should own truth constraints, validation, retry policy, state isolation, and final success criteria.

## Summary

V8.6.8 is not the result of one prompt.

It is the result of systematically turning production failures into explicit, testable engineering contracts.
