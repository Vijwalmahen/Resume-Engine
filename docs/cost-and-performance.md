# Cost and Performance Engineering

## Goal

The pipeline is designed to minimize unnecessary model work without weakening truthfulness or validation.

The core strategy is simple:

**Use the LLM only for semantic work. Use deterministic Python for everything that can be checked or repaired reliably without another model call.**

This improves three things at the same time:

- cost;
- latency; and
- reproducibility.

---

## Normal request path

For a successful job that does not need semantic correction, the V8.6.8 workflow uses two model requests:

1. **requirements analysis** — interpret the job description and extract the relevant concepts;
2. **content generation** — generate evidence-constrained resume content for that job.

A third model request is only used when deterministic validation finds a blocker that genuinely requires semantic correction.

Conceptually:

    Job
      |
      v
    Call 1: requirements analysis
      |
      v
    Call 2: evidence-constrained generation
      |
      v
    Deterministic validation
      |
      +--> PASS --------------------------> render/finalize
      |
      +--> mechanically fixable ---------> local Python repair
      |
      +--> semantic blocker -------------> bounded correction call
                                                |
                                                v
                                        deterministic re-check

The correction path is therefore conditional rather than automatic.

---

## Why this is cheaper than naive regeneration

A naive LLM workflow often responds to any problem by regenerating the entire resume.

This pipeline does not.

Instead, deterministic code decides what kind of problem occurred.

Examples of issues that do not need a fresh model call include:

- stale attempt-local state;
- project-selection state that must be recomputed;
- payload-length checks;
- company placeholder repair when the job posting provides a literal company name;
- action-verb composition checks;
- unsupported concept detection;
- output-shape validation;
- tracker-state handling;
- file movement and finalization logic.

Only problems that require new semantic wording are eligible for a correction call.

---

## Shared context reuse

Candidate evidence is expensive to repeatedly rebuild and send.

V8.6.8 creates shared candidate context once per batch and reuses it across jobs.

This avoids repeating identical preparation work for every job.

Conceptually:

    Candidate evidence
          |
          v
    Shared batch context
       /      |      \
      /       |       \
    Job A    Job B    Job C
      |       |        |
      v       v        v
    job-specific context slicing

The batch-level context is stable, while each job receives only the subset needed for its own semantic work.

---

## Context slicing

The pipeline does not treat "more context" as automatically better.

It deliberately limits the semantic payload sent to the model.

The context-slicing layer selects the job-relevant subset of:

- candidate evidence;
- project evidence;
- supported metrics;
- job concepts;
- ATS bridges;
- protected content;
- evidence ownership information; and
- correction-specific context.

Benefits:

- fewer input tokens;
- lower request cost;
- lower latency;
- less irrelevant information competing for attention;
- lower risk of cross-project contamination; and
- easier deterministic validation.

This is especially important in batch execution, where small per-job savings multiply across many jobs.

---

## Two different reasoning workloads

The system separates two semantic tasks that have different complexity.

### Requirements analysis

The first call determines what the job actually asks for.

Its job is extraction and classification, not creative writing.

### Resume content generation

The second call performs the more difficult composition task:

- choose supported evidence;
- emphasize job-relevant experience;
- satisfy formatting constraints;
- respect protected content;
- preserve factual ownership; and
- produce application-ready wording.

Keeping these responsibilities separate makes the prompts smaller and the outputs easier to validate.

---

## Bounded correction

Corrections are intentionally limited.

The system does not enter an open-ended agent loop that keeps calling the model until something appears acceptable.

A correction request:

- is triggered by deterministic evidence;
- receives a constrained correction packet;
- is limited to editable fields;
- cannot silently invent candidate history;
- cannot arbitrarily replace protected experience; and
- is followed by another deterministic validation pass.

If the system cannot truthfully repair the job within the allowed correction budget, it fails explicitly instead of continuing to spend tokens.

---

## Parallel job execution

The batch runner supports multiple concurrent workers.

Example production invocation:

    python3 -u tools/resume/v86/run_batch.py \
      --input-dir jobs/batch_inbox \
      --done-dir jobs/batch_done \
      --max-workers 10

Parallelism reduces total wall-clock time when multiple independent jobs are queued.

The important constraint is that concurrency is combined with strict state isolation.

Each job receives its own attempt-local state so one worker cannot silently inherit:

- correction rounds;
- project selection;
- density state;
- strategy artifacts; or
- other mutable job-local files

from another worker.

---

## Live queue refill

The orchestration layer is designed around a worker pool rather than processing the entire queue serially.

As workers finish, available capacity can be used for remaining work instead of waiting for the slowest job in a fixed group to complete.

This improves throughput for mixed workloads where some jobs require correction and others pass on the normal two-call path.

---

## Deterministic preflight as a cost control

Deterministic validation is not only a correctness layer. It is also a cost-control layer.

Every issue caught locally is one issue that does not require another model request.

Examples include:

- schema validation;
- ATS evidence checks;
- evidence ownership checks;
- company-name validation;
- action-verb composition;
- project-selection validation;
- correction-state validation;
- payload constraints;
- negation filtering; and
- output artifact checks.

The system therefore spends model tokens on semantic judgment, not mechanical bookkeeping.

---

## Action-verb optimization

A concrete example of cost optimization came from action-verb validation.

Protected experience already contributes fixed opening verbs to the final resume.

If dynamic content independently chooses the same verbs too often, the final composed resume can violate the diversity rule even when the generated dynamic section looks acceptable in isolation.

V8.6.8 made the generation prompt composition-aware:

- the model is told which opening verbs are already consumed by protected content;
- dynamic bullets are instructed not to reuse exhausted verbs; and
- deterministic validation still verifies the final composition.

This pushes a predictable constraint earlier into generation and reduces avoidable correction calls.

---

## Benchmark snapshot (synthetic illustrative example)

The example below is synthetic and is included only to demonstrate how benchmark results can be reported. It does not represent any specific historical run.

A 10-job stress run in a local workspace might report:

| Metric | Illustrative value |
| --- | ---: |
| Jobs attempted | 10 |
| Successful jobs | 8 |
| Expected truthful bad-fit rejection | 1 |
| Model requests | 19 |
| Jobs entering correction | 4 |
| Approximate model cost | $0.94 |

The failed job is an intentionally poor fit and is expected to fail rather than fabricate unsupported experience.

That distinction matters: a lower "success rate" can be the correct outcome when the alternative is hallucination.

---

## Smoke-run snapshot (synthetic illustrative example)

A one-job smoke test completed on the normal path might report:

| Metric | Illustrative value |
| --- | ---: |
| Final status | RESUME_READY |
| Model requests | 2 |
| Correction rounds | 0 |
| Approximate model cost | $0.09 |

This illustrates that a composition-aware optimization can avoid an unnecessary correction on a given workload.

---

## Measured vs estimated claims

The repository distinguishes measured results from hypothetical savings.

For example (synthetic illustrative figures), if a future change would have prevented four correction calls in a 19-request benchmark, it would be reasonable to estimate a 15-request counterfactual.

But unless that benchmark is actually rerun, it should be described as an **estimate**, not as a measured result.

The documentation follows that rule.

---

## Failure economics

Failure handling is part of cost engineering.

The pipeline stops spending when continued generation would not be justified.

Examples:

### Unsupported job

If a job requires evidence the candidate does not have, the system can return a strategy failure instead of repeatedly asking the model to "try harder."

### Correction limit reached

If the correction budget is exhausted, the job fails explicitly.

### Mechanical failure

If the failure is deterministic and locally repairable, the system uses Python rather than another model request.

### Rendering failure

A rendering or artifact-validation failure does not trigger arbitrary semantic regeneration unless the underlying problem actually requires semantic changes.

---

## Why not use one giant model call?

A single huge prompt could theoretically contain:

- the entire candidate history;
- all projects;
- all rules;
- all ATS concepts;
- all formatting constraints;
- the job description;
- correction policy; and
- rendering instructions.

The system intentionally avoids that design.

Breaking the workflow into structured stages provides:

- smaller semantic contexts;
- clearer contracts;
- easier failure attribution;
- reusable deterministic logic;
- cheaper retries;
- safer corrections;
- better testability; and
- better observability.

---

## Cost optimization hierarchy

The system effectively follows this decision order:

    Can Python determine the answer?
        |
       yes --> do it locally
        |
       no
        v
    Does the model need all candidate context?
        |
       no --> retrieve/slice only the relevant evidence
        |
       yes
        v
    Make semantic call
        |
        v
    Validate locally
        |
        +--> pass --> stop spending
        |
        +--> local fix possible --> repair locally
        |
        +--> semantic correction required --> one bounded correction path

This is the central cost-control philosophy of the pipeline.

---

## Performance tradeoffs

The system deliberately does not optimize only for minimum latency.

Priority order is:

1. factual accuracy;
2. evidence ownership;
3. deterministic correctness;
4. bounded cost;
5. latency.

A slightly slower truthful failure is preferable to a fast fabricated application.

Likewise, an extra deterministic validation pass is cheap compared with sending an incorrect resume or paying for repeated full-model regeneration.

---

## Summary

V8.6.8 reduces cost and latency through architecture rather than through aggressive quality reduction.

The main mechanisms are:

- shared batch context;
- structured evidence retrieval;
- job-specific context slicing;
- separate requirements and generation calls;
- two-call normal execution path;
- conditional correction only when needed;
- deterministic validation and repair;
- composition-aware prompting;
- bounded retry behavior;
- concurrent workers;
- strict job-state isolation; and
- explicit truthful failure for unsupported jobs.

The result is a pipeline where model usage is reserved for the parts of resume tailoring that actually require semantic reasoning.
