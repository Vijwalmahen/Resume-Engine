# `tools/ats/`

The deterministic matching primitives: one module that decides whether a
job requirement is supported by candidate evidence and present in the
rendered resume.

## Files

| File | What it does | How the pipeline uses it |
| --- | --- | --- |
| `preflight.py` | A CLI and a library. `normalize()` applies NFKC folding, lowercasing, dash unification, intra-word hyphen splitting, and slash/whitespace collapsing; `phrase_present()` does a word-boundary check over normalized text; `flatten_json()` / `read_evidence_file()` / `load_evidence()` walk an evidence root (`.json`, `.md`, `.txt`) into addressable `{file, source, text}` hits; `load_aliases()` and `concept_forms()` expand a concept into every accepted surface form; `find_evidence()` locates the hits that support a concept; and `main()` writes a coverage report. | Run as `--requirements … --resume … --evidence-root … [--aliases …] --output …`, it is the ATS gate's engine. `tools/resume/v86/ats_preflight_v86.py` builds on these same primitives and extends the alias set with `config/v86/ats_concept_bridges.json`. `tests/public/test_public_repo.py` lists this file as critical. |

The normalization and `phrase_present` behaviour here is the single
definition of "matches" in the pipeline. Alias tables change; the
matching rule does not.

## Where this sits in the flow

Validation. It runs after generation and rendering, deciding whether
required concepts are both *supported* by evidence and *present* in the
resume draft.

## Private or git-ignored

Nothing here is git-ignored. At run time `--evidence-root` points at
candidate evidence (default `master`), which is git-ignored, and
`--output` is written into a job's `output/<job_key>/` directory, which
is git-ignored too. The module itself holds no candidate data and no
alias table — aliases come from `config/`.

## Related documentation

- [`../../config/README.md`](../../config/README.md) — `ats_aliases.json`, the alias table this reads.
- [`../../config/v86/README.md`](../../config/v86/README.md) — the additional concept bridges layered on top.
- [`../../docs/deterministic-validation.md`](../../docs/deterministic-validation.md) — where the ATS gate sits.
- [`../../docs/retrieval-and-grounding.md`](../../docs/retrieval-and-grounding.md) — how evidence is assembled before matching.
