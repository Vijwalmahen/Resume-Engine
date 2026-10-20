# Setup

**New user?** [`GETTING_STARTED.md`](GETTING_STARTED.md) is the ordered
setup-to-resume checklist, and [`INSTRUCTIONS.md`](../INSTRUCTIONS.md) at
the repository root is the candidate-data field reference. This page is a
condensed reference for people who already know the repo.

## 1. Create a Python environment

    python3 -m venv .venv
    source .venv/bin/activate
    python3 -m pip install -r requirements.txt

## 2. Claude CLI

The semantic worker uses the Claude CLI for live model calls.

Install and authenticate the Claude CLI using your own local environment.

Do not commit authentication credentials.

## 3. Bootstrap and edit your candidate data

    python3 tools/bootstrap_candidate.py

copies fictional example files into their real, private locations under
`master/` and `reference/` (never overwriting existing files). Edit them
with your own verified information — see
[`docs/candidate-data-guide.md`](candidate-data-guide.md) for the schema.

## 4. Document templates

Real resume and cover-letter rendering uses local DOCX templates under
`reference/`, plus a JSON layout file describing their paragraph geometry.
See [`reference/README.md`](../reference/README.md).

## 5. Validate before spending money

    python3 tools/validate_candidate_setup.py

Checks every required file exists, parses, and has the right shape, with
zero network/model calls.

## 6. Record the runtime-context manifest

    python3 tools/resume/v8/runtime_context_guard.py write-manifest
    python3 tools/resume/v8/runtime_context_guard.py check

`check` prints `RUNTIME CONTEXT: VALID`. Re-run `write-manifest` after
every edit to anything under `master/` — the guard detects staleness, it
does not regenerate `runtime_resume_context.json`.

## 7. Run the offline test suites

    PYTHONDONTWRITEBYTECODE=1 python3 -u tests/run_public_tests.py
    PYTHONDONTWRITEBYTECODE=1 python3 -u tools/resume/v86/tests/run_all_v86_tests.py
    PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/tests/run_all_tests.py

The portable suite (`run_public_tests.py`) does not require private
candidate data, personalized templates, or previous benchmark work
directories. None of the three makes a model call.

## 8. Add job descriptions

Place Markdown job descriptions in:

    jobs/batch_inbox/

Each file needs exactly one anchored `Company:` header line — `Company: X`,
`**Company:** X`, or a YAML `company: "X"`. A `## Company` heading is not
matched. See [`examples/sample_job.md`](../examples/sample_job.md).

## 9. Dry run, then run the batch pipeline

Rehearse the whole path offline first — no model call, no cost, and the
tracker write goes to `tracking/job_application_tracker_TEST.xlsx`:

    PYTHONDONTWRITEBYTECODE=1 python3 -u tools/resume/v86/run_batch.py \
      --input-dir jobs/batch_inbox \
      --done-dir jobs/batch_done \
      --max-workers 2 --mock-semantic --test-mode

Then the real run:

    python3 -u tools/resume/v86/run_batch.py \
      --input-dir jobs/batch_inbox \
      --done-dir jobs/batch_done \
      --max-workers 10

For a normal production run, do not use `--test-mode` or `--mock-semantic`.

## Local-only directories

These directories contain private or runtime state and should not be committed:

- `master/` (except `README.md` and `examples/`)
- `reference/` (except `README.md`, `*.example.json`, and
  `examples/resume_template.example.docx`)
- `.work/`
- `output/`
- `tracking/`
- real job queue contents (`jobs/batch_inbox/*`, `jobs/batch_done/*`)
