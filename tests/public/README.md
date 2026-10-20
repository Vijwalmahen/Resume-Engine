# `tests/public/`

The portable test package: safety and pipeline-invariant checks that run
on a fresh clone, with no private candidate data.

## Files

| File | What it checks | How the pipeline uses it |
| --- | --- | --- |
| `test_public_repo.py` | Repository-safety properties: `config/active_resume_pipeline.json` declares an `active_version`; a list of critical pipeline files exists; no absolute home path, unexpected email domain, phone-shaped value, or key-shaped string appears in any scanned file; fixture GitHub URLs use only fictional owners; no `.docx`/`.pdf` is tracked outside the private directories; every Python file parses; every `master/examples/*.json` parses; `.gitignore` carries the expected private-runtime rules and the example-keeping negations; the deliberately fake credential strings are unmistakably fake; no date-coded batch ID or freeze-snapshot name is tracked; no `resume_quality_report.json` instance is tracked; the diagnostics config holds only numeric thresholds; and `config/resume_layout.md` hardcodes no candidate city. | Run first by `../run_public_tests.py`. It is the executable half of the privacy boundary described in `docs/security-and-privacy.md`. |
| `test_generic_pipeline.py` | That the pipeline is genuinely data-driven, using only the committed fictional examples: evidence editability is decided by `tailoring_mode` and bound project id rather than a name allowlist; the selectable-project pool comes from candidate data, not config; `tools/resume/render_resume.py` applies a payload purely by `owner_id` against a layout file; output filenames embed no personal name; and `tools/bootstrap_candidate.py` / `tools/validate_candidate_setup.py` are idempotent, non-destructive, and make no model call. | Run last by `../run_public_tests.py`, as `tests.public.test_generic_pipeline`. |

The suite deliberately contains **no denylist** of any real name, email,
phone, employer, or school — storing those values here, even to assert
their absence, would put private information back into the repository.
It checks shapes and properties instead.

## Where this sits in the flow

Validation. Both files are offline and make no model call.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tests/run_public_tests.py
```

## Private or git-ignored

`test_public_repo.py` skips `.git`, `.work`, `master`, `reference`,
`output`, and `tracking` when scanning, because those directories are
allowed to hold real local data and are git-ignored. Nothing in this
directory requires any private file to exist.

Running the suite executes `tools/bootstrap_candidate.py`, which creates
the private candidate files under `master/` (and leaves existing ones
untouched). Those paths are git-ignored.

## Related documentation

- [`../README.md`](../README.md) — how to run the tests.
- [`../../docs/testing.md`](../../docs/testing.md) — the testing strategy.
- [`../../docs/security-and-privacy.md`](../../docs/security-and-privacy.md) — the boundary these checks enforce.
