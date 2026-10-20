# `docs/`

The design documents: one page per subsystem, explaining why each layer
exists and what contract it enforces.

## Files

| File | What it covers |
| --- | --- |
| `GETTING_STARTED.md` | The numbered checklist from a fresh clone to a rendered resume, including watermark-cleaning setup. Start here. |
| `setup.md` | A condensed setup reference (virtual environment, CLI, validation) for readers who already know the repository. |
| `candidate-data-guide.md` | Schema reference for every file under `master/` and `reference/`, illustrated with the committed fictional example dataset. |
| `architecture.md` | How the batch pipeline separates semantic reasoning from deterministic enforcement, and the processing flow between them. |
| `retrieval-and-grounding.md` | How candidate information is treated as evidence: context building, requirement analysis, evidence retrieval, then generation. |
| `deterministic-validation.md` | The deterministic checks that run after generation and decide whether a job may render. |
| `concurrency-and-state.md` | The batch concurrency model and the rule that one job never inherits another job's state. |
| `failure-handling.md` | Failure as a first-class state: warnings, correction requests, and explicit failure instead of a fabricated fit. |
| `cost-and-performance.md` | Why the model is used only for semantic work and everything checkable is done deterministically. |
| `evolution-v86.md` | How the current pipeline arrived at its shape, pass by pass. |
| `benchmarks.md` | What the project's benchmarks evaluate: truthfulness, request efficiency, correction frequency, failure isolation, throughput. |
| `testing.md` | The testing strategy, including the AI-safety properties the suites exist to protect. |
| `security-and-privacy.md` | The public/private boundary and which runtime data never enters Git. |

## Where this sits in the flow

Reference material for every stage. `GETTING_STARTED.md` covers setup;
`architecture.md`, `retrieval-and-grounding.md`, and
`deterministic-validation.md` cover the batch run and its validation;
`benchmarks.md` and `testing.md` cover reporting and verification.

## Private or git-ignored

Nothing in `docs/` is git-ignored. These pages describe the private
directories (`master/`, `reference/`, `output/`, `.work/`, `tracking/`)
without reproducing their contents; the data itself is excluded from Git
by `.gitignore`.

## Related documentation

- [`../README.md`](../README.md) — the repository overview these pages expand on.
- [`../INSTRUCTIONS.md`](../INSTRUCTIONS.md) — the field-by-field candidate-data reference.
- [`../master/README.md`](../master/README.md) and [`../reference/README.md`](../reference/README.md) — the private directories described in `candidate-data-guide.md`.
