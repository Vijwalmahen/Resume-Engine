# `tools/resume/v86/tests/`

The V8.6 regression suites, one file per hardening pass, plus the flat
runner that executes them. No file here makes a real model call, touches
the network, or reads private candidate data.

## Runner and shared pieces

| File | What it is |
| --- | --- |
| `run_all_v86_tests.py` | The flat, non-recursive runner. Executes every suite exactly once as its own subprocess in a fixed, chronological `SUITE_ORDER`. It replaced an earlier arrangement in which each suite re-ran the older suites, so running the newest file transitively re-executed everything many times over. |
| `__init__.py` | Makes the directory a package. |
| [`fixtures/`](fixtures/README.md) | The checked-in synthetic artifacts the suites read. |

## Suites, in run order

| File | What it covers |
| --- | --- |
| `test_v86_offline.py` | Batch orchestration: concurrency limits, live refill, per-job isolation, and job movement between the inbox and done directories. All semantic calls go through a mock caller; real Word/PDF rendering is deliberately out of scope. |
| `test_v86_reliability_patch.py` | The reliability patch: de-duplication of near-identical job concepts, correction-round state, and the first ATS concept bridges. |
| `test_v86_2_isolation_patch.py` | State isolation. Where the real subprocess-building path needs end-to-end exercise, a deterministic fake `claude` executable is generated into a throwaway temp directory and records exactly what it was given. |
| `test_v86_3_usage_optimization.py` | The usage and cost pass: that the first semantic call receives only extraction-relevant information, and that isolation still holds. |
| `test_v86_4_call2_effort_medium.py` | A single-variable change to the second semantic call, with everything else held constant. |
| `test_v86_5_clear_winner_safeguard.py` | The deterministic clear-winner safeguard wired in immediately after the V8 project selector. |
| `test_v86_6_ats_bridge_and_unplaceable_evidence.py` | The "hybrid search" concept bridge and the unplaceable-evidence safety net: evidence that no editable owner can carry must be classified as unplaceable rather than driving an endless correction loop. |
| `test_v86_7_ats_bridge_reciprocal_rank_fusion.py` | The "reciprocal rank fusion" bridge, and that unrelated concepts are unchanged by adding it. |
| `test_v86_8_stabilization.py` | Cover-letter company identity (backfill from the posting, and a refusal to fabricate one when the posting has no parseable header), and the mechanical payload pre-trim. |
| `test_v86_8_round2_hardening.py` | Three defects found in a realistic ten-job retest, including stale cross-batch correction-round state. |
| `test_v86_8_round3_state_isolation.py` | Fresh-attempt isolation: a previous run's `project_selection.json` must never be silently reused as the current attempt's own decision. |
| `test_v86_8_round4_company_identity.py` | The deterministic company-identity source of truth: a non-empty but non-specific company value is still wrong. |
| `test_v86_8_round5_action_verb_diversity.py` | A deterministic style check — the same opening action verb should normally appear at most twice across the experience and project bullets. Never an evidence or selection gate. |
| `test_v86_9_resume_quality_report.py` | The evidence-aware resume-quality and ATS-readability diagnostics. Every candidate, employer, project, and metric is synthetic and written inline; the only repository data touched is the committed fictional examples and the threshold config. |
| `test_v86_9_acceptance_offline.py` | The independent acceptance layer for those diagnostics: real code paths end to end against real synthetic PDFs, with only the external cleaning service mocked. |
| `test_v86_9_batch_and_adapters.py` | The real threaded batch path, the configurable resume-artifact name, and the metrics-registry adaptation boundary. |
| `test_v86_10_jd_match_and_recommendations.py` | Job-description matching, evidence-gated recommendations, and render fidelity. |
| `test_v86_8_final_flat_runner.py` | A leaf suite that proves the flat contract itself: the runner executes every suite exactly once, and no suite file invokes another suite file. |

## Where this sits in the flow

Validation.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/resume/v86/tests/run_all_v86_tests.py
```

`tests/run_public_tests.py` runs three of these suites — round-2
hardening, round-3 state isolation, and the flat-runner contract — as
part of the portable entrypoint.

## Private or git-ignored

Nothing here is git-ignored and no private candidate file is required.
Generated artifacts, temporary job directories, and any fake `claude`
executable live in temporary directories or in `output/` and `.work/`,
all of which are git-ignored.

## Related documentation

- [`fixtures/README.md`](fixtures/README.md) — what each fixture reproduces.
- [`../README.md`](../README.md) — the batch pipeline's implementation reference.
- [`../../../../docs/testing.md`](../../../../docs/testing.md) — the testing strategy.
- [`../../../../tests/README.md`](../../../../tests/README.md) — the portable public entrypoint.
