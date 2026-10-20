# `tools/resume/v86/tests/fixtures/`

Immutable, checked-in synthetic artifacts the V8.6 suites read instead of
the mutable `output/<job_key>/` directories a later batch run could
overwrite.

Every fixture is built from the fictional "Candidate Example" dataset.
Directory names and job keys are historical labels from the development
passes the fixture was written for; none of them contains data from a
real run.

## Contents

| Directory | Reproduces | Read by |
| --- | --- | --- |
| `shared_ats_evidence/` | A `verified_resume_evidence.json` evidence root in the shape `tools/ats/preflight.py`'s `find_evidence` consumes. | Most suites, as the shared evidence root for ATS evaluation. |
| [`benchmark01_company_placeholder/`](benchmark01_company_placeholder/README.md) | A cover-letter payload whose `company` is the placeholder `"your organization"` — contract-valid but not the employer the posting names. | `test_v86_8_stabilization.py`, `test_v86_8_round2_hardening.py`. |
| [`benchmark09_company_backfill/`](benchmark09_company_backfill/README.md) | A cover-letter payload with an empty `company`, plus two postings: one with a parseable company header and one deliberately without. | `test_v86_8_stabilization.py`. |
| [`live13_11_recommendation/`](live13_11_recommendation/README.md) | A full set of post-fix job artifacts for one job, used as a realistic starting point for pipeline runs. | `test_v86_6_…`, `test_v86_8_stabilization.py`, `test_v86_8_round5_…`, `test_v86_9_resume_quality_report.py`. |
| `scale10_02_budget_overflow/` | A job whose required "Agentic workflows" concept is only satisfied once the concept bridges are loaded. | `test_v86_reliability_patch.py`. |
| `scale10_03_llm_eval_bridge/` | A job whose "LLM evaluation" concept is supported by protected bullets phrased differently, proving the bridge recognizes evidence without rewriting canonical content. | `test_v86_reliability_patch.py`. |
| [`scale10_04_duplicate_opencv/`](scale10_04_duplicate_opencv/README.md) | A requirements file containing the same concept term twice, which failed contract validation before de-duplication was added. | `test_v86_reliability_patch.py`. |
| `v866_scale10_09_retrieval_engineering_intern/` | Candidate bullets using "RRF" against a job concept phrased as "Reciprocal rank fusion". Has its own `README.md`. | `test_v86_7_ats_bridge_reciprocal_rank_fusion.py`. |
| `v866_unplaceable_evidence/` | The pre-bridge "Hybrid search" scenario in which the only evidence hit resolves to a non-editable owner. Has its own `README.md`. | `test_v86_6_ats_bridge_and_unplaceable_evidence.py`. |

## Where this sits in the flow

Validation. Nothing in the production pipeline reads this directory.

## Private or git-ignored

Nothing here is git-ignored. `tests/public/test_public_repo.py` scans
this whole tree and fails if any `github.com` URL names an owner outside
the allowed fictional set.

## Related documentation

- [`../README.md`](../README.md) — the suites that read these fixtures.
- [`../../README.md`](../../README.md) — the batch pipeline, including the passes these fixtures were written for.
- [`../../../../../docs/testing.md`](../../../../../docs/testing.md) — why fixtures are preferred over live job output.
