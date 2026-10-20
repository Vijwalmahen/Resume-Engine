# Testing Strategy

## Goal

Testing in this project is designed to verify both ordinary software behavior and AI-system safety properties.

The test strategy asks more than whether a function returns the expected value.

It also checks whether the pipeline:

- preserves candidate truth;
- isolates job-specific state;
- respects bounded correction behavior;
- rejects unsupported experience;
- keeps deterministic validation authoritative;
- avoids private-data leakage in the public repository; and
- maintains stable batch orchestration.

## Test entrypoints

Three runners, all offline. None of them makes a model call, and none
requires private candidate evidence, personalized document templates,
historical benchmark work directories, or local backup snapshots.

    PYTHONDONTWRITEBYTECODE=1 python3 -u tests/run_public_tests.py
    PYTHONDONTWRITEBYTECODE=1 python3 -u tools/resume/v86/tests/run_all_v86_tests.py
    PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/tests/run_all_tests.py

`tests/run_public_tests.py` is the portable entrypoint intended for the
cleaned public repository. It prints `PUBLIC TEST RESULT: PASS` or
`PUBLIC TEST RESULT: FAIL` and exits non-zero on failure.

`tools/resume/v86/tests/run_all_v86_tests.py` is the flat runner for every
V8.6.x suite. It runs each suite file exactly once, as its own `python3`
subprocess, in a fixed deterministic order, synchronously. No suite
invokes another suite — `test_v86_8_final_flat_runner.py` pins that
property.

`tools/intake/tests/run_all_tests.py` covers the offline intake package
and runs with socket access disabled.

## Current portable suites

The public runner exercises four groups:

1. repository safety checks;
2. V8.6.8 round-2 hardening tests;
3. V8.6.8 state-isolation tests; and
4. V8.6.8 flat-runner contract tests.

Some state-isolation tests skip when private replay fixtures are absent,
which is the expected condition in the public repository. Run the commands
above for the current counts rather than relying on a number recorded
here.

## Repository-safety tests

The public safety checks verify properties such as:

- the active pipeline version recorded in `config/active_resume_pipeline.json` is the expected one;
- critical source files exist;
- exact private phone/email/home-path values are absent;
- no hardcoded candidate city appears in the shared layout config;
- no `sk-*` shaped secret strings remain;
- no date-coded batch id or freeze-snapshot name appears in a tracked file;
- public Python files parse successfully;
- `.gitignore` contains important runtime/privacy exclusions; and
- test credentials are clearly synthetic.

The home-path and city guards assemble their own search strings from
fragments, so the guard itself never registers as a hit when the
repository is scanned for the same values.

These checks are intended to catch accidental publication problems before a repository is pushed.
## State-isolation tests

State-isolation tests focus on the risk that one job attempt can affect another.

They exercise behaviors such as:

- stale correction state;
- stale selection artifacts;
- attempt-local cleanup;
- independent retries; and
- job-local mutable state.

Some replay-oriented cases depend on private historical fixtures.

When those fixtures are not present in a clean public checkout, those tests are intentionally skipped rather than treated as failures.

## Resume-quality diagnostics tests

`tools/resume/v86/tests/test_v86_9_resume_quality_report.py` covers the
advisory diagnostics layer end to end, entirely offline, with wholly
synthetic candidate, employer, institution, project, date, bullet and
metric data written inline in the suite itself. It runs through
`tools/resume/v86/tests/run_all_v86_tests.py` like every other V8.6 suite
and makes no model, network or paid call.

It asserts, among other things, that:

- a bullet that is missing, partially extracted, garbled or reordered in
  the PDF is reported individually, by field identifier;
- normalization folds line wraps and markdown styling but never a number,
  a unit, or a technical string such as `C++`, `C#`, `.NET` or `CI/CD`;
- a missing PDF or missing expected-content source returns a documented
  unevaluated state instead of raising;
- a measurement is only called "possibly omitted" when the candidate's own
  records tie it to that specific entry, and an unrelated or unbound
  entry's metric is never attributed to the wrong bullet;
- the short-bullet advisory excludes headings, dates, skills and
  education lines, and never warns at exactly the configured threshold;
- the existing action-verb checker runs once per job and its result is
  reused verbatim;
- timeline intervals are classified against dated education periods, and
  year-only or ambiguous dates produce an uncertain result rather than a
  claim;
- the report's schema, finding structure and diagnostic-state precedence
  are stable, no numeric score is produced, and the file is written only
  into the job-local, git-ignored runtime directory; and
- the existing gate semantics, correction budget, success statuses and
  dependency set are unchanged.

## Batch and adapter hardening tests

`tools/resume/v86/tests/test_v86_9_batch_and_adapters.py` closes the two
gaps the compatibility audit identified, again entirely offline and with
wholly synthetic data.

It drives the **real** `Batch.run()` -- the real scan loop, thread pool,
`process_job` body, `future.result()` propagation point and metrics
assembly. Only three seams are substituted: the semantic caller
(`MockCaller`), the Word-dependent deterministic pipeline (the existing
`Batch.pipeline_runner` seam), and the per-batch shared-context build,
stubbed so the test reads synthetic candidate data instead of the
machine's private runtime context. Across three synthetic jobs it
asserts that each job keeps its genuine status and report metadata, that
`jobs_quality_review` aggregates correctly, that `batch_metrics.txt`
carries the per-job quality suffix and no candidate content, that a
`refresh_after_finalization` raising mid-batch cannot fail a job or stop
the batch, that the action-verb checker runs exactly once per job, and
that the success statuses, correction reasons, correction-round limit
and tracker invocation are untouched.

It also covers the configurable resume-artifact name (exact name, glob,
ambiguous glob, missing file, wrongly-configured name) and the metrics
registry-shape adaptation boundary (shape detection, explicit mapping,
unmapped entities, duplicate entity labels, ambiguous owners, unverified
items, dropped and differing values, value/unit boundaries, and explicit
payload paths).

## Job-matching and recommendation-gate tests

`tools/resume/v86/tests/test_v86_10_jd_match_and_recommendations.py`
covers the explainable JD-match report, the evidence-gated recommendation
guard and the template style audit, offline and with synthetic data.

It pins, among other things, that:

- `match_class` and `evidence_support` stay on separate axes, and the
  specific bridge form behind a `semantic` match is always recorded;
- a recommendation citing no resolvable evidence record is rejected;
- a measurement in proposed wording that does not already appear in the
  cited evidence is rejected as `unsupported_metric` -- the case that
  motivated the guard, pinned so it cannot silently regress;
- a contradicted requirement rejects outright, while conflicting signals
  return `needs_human_review` rather than a confident "unsupported";
- an unknown `requirement_term`, or a missing match report, is reported
  rather than passed over; and
- the style audit diffs a rendered document against its own template, so
  no house style is ever asserted.

## Intake tests

`tools/intake/tests/run_all_tests.py` covers the offline intake package:
URL classification and canonicalization, job-key derivation, duplicate
detection across the sheet and all three job directories, job-file
rendering and its `Company:` header contract, the Intake worksheet's
schema guard and idempotent upsert, and post-batch result sync. Every
test runs with socket access disabled, so a regression that reaches for
the network fails rather than silently succeeding.

## Historical regression tests

The repository also retains historical V8.x tests from development.

Not every historical test is portable.

Some expect artifacts such as:

- private runtime candidate context;
- historical backup/freeze directories;
- previous `.work/` benchmark outputs;
- personalized DOCX templates; or
- exact development-era intermediate files.

Those tests remain useful for engineering traceability, but they are not the supported clean-checkout contract.

The public entrypoint should therefore be used when validating the GitHub repository.

## Why tests are split this way

The production workspace contains richer local state than a public repository should expose.

Removing all tests that reference historical behavior would reduce traceability.

Publishing private fixtures would violate the privacy boundary.

The project therefore keeps two concepts separate:

- portable public validation; and
- historical development regression coverage.

## AI-specific invariants

Important regression properties include:

- unsupported evidence must not become supported through rewriting;
- a truthful poor-fit job may fail with `STRATEGY_FAILED`;
- correction rounds are bounded;
- stale state must not consume a fresh attempt's correction budget;
- project selection must remain job-specific;
- deterministic blockers must remain authoritative;
- successful semantic generation is not enough if rendering/finalization fails; and
- one failed job must not invalidate unrelated jobs in the same batch.

## Testing philosophy

The system is not tested only for maximum completion rate.

A test can pass when a job is rejected, provided the rejection is the truthful and expected behavior.

That matters because the central safety property is:

**The pipeline must never convert missing candidate evidence into invented candidate evidence simply to make a run succeed.**

## Summary

The testing strategy combines:

- portable repository checks;
- deterministic regression tests;
- state-isolation coverage;
- batch-runner contract tests;
- historical engineering tests; and
- explicit safety invariants for evidence-grounded generation.

This makes the test suite a validation layer for both software correctness and AI-system behavior.
