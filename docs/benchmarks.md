# Benchmarks and Evaluation

## Purpose

The project uses benchmarks to validate more than just resume quality.

The evaluation focuses on:

- truthfulness;
- model-request efficiency;
- correction frequency;
- failure isolation;
- deterministic validation;
- batch throughput;
- state isolation; and
- final artifact readiness.

This page distinguishes between **measured** results and **derived or estimated** claims.

## 10-job stress run (synthetic illustrative example)

The example below is synthetic and is included only to demonstrate how benchmark results can be reported. It does not represent any specific historical run.

A stress run processing a mix of supported and intentionally poor-fit jobs might report:

| Metric | Illustrative value |
| --- | ---: |
| Jobs attempted | 10 |
| Successful jobs | 8 |
| Expected truthful bad-fit rejection | 1 |
| Model requests | 19 |
| Jobs entering correction | 4 |
| Approximate model cost | $0.94 |

The failed job is intentionally poorly matched to the candidate's evidence.

It fails because the pipeline preserves the gap instead of fabricating unsupported experience.

Therefore, a sub-100% success rate should not be interpreted as a weakness in the system. In this case, the one failure is part of the test contract.

## What the stress run validated

The run exercised:

- multiple jobs in one batch;
- concurrent semantic work;
- per-job state isolation;
- deterministic validation;
- correction loops;
- bad-fit rejection;
- rendering and finalization; and
- batch-level metric collection.
## Smoke run (synthetic illustrative example)

A one-job smoke run completed on the normal no-correction path might report:

| Metric | Illustrative value |
| --- | ---: |
| Final status | `RESUME_READY` |
| Model requests | 2 |
| Correction rounds | 0 |
| Approximate model cost | $0.09 |

This run validated the intended two-request normal path:

    requirements analysis
           |
           v
    content generation
           |
           v
    deterministic validation
           |
           v
    RESUME_READY

No semantic correction request was required.

## Measured vs estimated

Only directly observed run results are labeled as benchmarks.

For example (synthetic illustrative figures), if a previous 10-job workload used 19 model requests and four of those jobs required an additional correction call, then removing those four correction calls would imply a theoretical 15-request path.

That is a **derived counterfactual**, not a measured rerun, and should not be presented as an observed benchmark.

## Why request count matters

Model request count is a useful proxy for both cost and latency.

The pipeline therefore tries to keep the normal path to two semantic requests and reserve a third request only for blockers that really require semantic correction.

Deterministic repairs are preferred when a problem can be fixed safely without another model call.

## Why we keep bad-fit jobs in evaluation

An evaluation set containing only easy, supported jobs would not test the most important safety property of the system:

**The pipeline must refuse to invent missing candidate history even when that means the job cannot be completed.**

Therefore, a truthful failure is a positive test outcome when the input is intentionally unsupported.

## Benchmark limitations

The current benchmarks are project-specific and run against the validated local candidate context.

They are not a claim of universal performance across all models, machines, resume templates, or job distributions.

Cost and latency can vary with:

- model provider;
- model version;
- prompt size;
- output size;
- reasoning effort;
- network conditions;
- concurrency; and
- local machine performance.

## Summary

The benchmark philosophy is to measure the whale system, not just the LLM.

The most important metrics are the ones that show whether the pipeline is:

- truthful;
- efficient;
- recoverable;
- deterministically validated; and
- capable of producing usable final artifacts.
