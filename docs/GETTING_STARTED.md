# Getting Started

A numbered checklist from a fresh clone to a finished resume. Work through it
in order — later steps assume the earlier ones.

For the field-by-field reference behind step 2, see
[`INSTRUCTIONS.md`](../INSTRUCTIONS.md) and
[`candidate-data-guide.md`](candidate-data-guide.md). For what the pipeline
does and why, see the [README](../README.md).

Every command below is run from the repository root.

---

## 1. Prerequisites

- [ ] **Python 3**, with the packages in `requirements.txt`:

      ```bash
      python3 -m venv .venv
      source .venv/bin/activate
      python3 -m pip install -r requirements.txt
      ```

      That installs `python-docx`, `docx2pdf`, `PyMuPDF`, and `openpyxl` —
      the complete third-party list. `requests` is **not** needed and is not
      used anywhere in the tree; the finalization client uses the standard
      library's `urllib.request`.

- [ ] **Use the system `python3`**, not a version-suffixed interpreter such as
      `python3.13`. The pipeline launches deterministic steps as subprocesses,
      and those depend on the four packages above being importable from
      `python3`. Set `V86_DETERMINISTIC_PYTHON` only if you need to override
      which interpreter those subprocesses use.

- [ ] **Nothing else is required to run a batch.** The local finalization
      service at `--base-url` (default `http://127.0.0.1:8765`) is optional:
      the batch health-checks it once and, if it is not running, skips
      finalization entirely and still completes the job, recording the
      watermark status as `NOT STARTED`. Step 9 explains how to run it and
      why a real run should.

- [ ] **Microsoft Word**, for steps 8 onward. PDF conversion goes through
      `docx2pdf`, which drives Word. Steps 1–7 do not need it.

- [ ] **The Claude CLI**, installed and authenticated, for the real run in
      step 10 only. Steps 1–8 make no model calls and cost nothing.
      Credentials come from your local CLI environment — never commit them.

Nothing in steps 1–8 touches the network or spends money.

---

## 2. Create your candidate data

```bash
python3 tools/bootstrap_candidate.py
```

This copies the committed fictional examples into their real, git-ignored
locations. It never overwrites an existing file unless you pass `--overwrite`,
and it is safe to run repeatedly — it reports which files already exist and
skips them. You end up with a complete, valid, entirely **fictional** candidate
setup, which you then edit in place.

It creates these files under `master/` from `master/examples/`:

| File | What it holds |
| --- | --- |
| `master/runtime/runtime_resume_context.json` | **The one file the pipeline actually reads at run time.** Identity, education, experience, projects, and skills. Each experience and project entry carries a stable `id`, a `tailoring_mode` (`protected` = fixed bullets rendered verbatim, `tailorable` = truthful wording the model may re-author), and — for projects — a `project_status` (`required` = always rendered, `selectable` = a candidate for the single dynamically-chosen project slot). You need at least one `selectable` project. |
| `master/rejections.json` | Explicit denylist of claims that must never appear, however convenient a job description makes them. |
| `master/evidence_bank.json` | Structured, atomic, verified facts. Used to score which *selectable* project truthfully supports a given job. `entity` must match a project's `name`. |
| `master/metrics_registry.json` | Every verified measurement you are willing to have quoted, tied to the experience/project `id` it belongs to. The model may never introduce a metric that is not here or already embedded in an approved bullet. |
| `master/candidate_profile.json` | Standalone identity block: name, email, phone, location, LinkedIn, GitHub, portfolio. |
| `master/eligibility_facts.json` | Work authorization, relocation, availability — so eligibility questions are answered truthfully rather than guessed. |
| `master/candidate_facts.md` | Free-text narrative of everything above, in your own words. Nothing reads it automatically; it exists so you always have the full version to check generated bullets against. |
| `master/canonical_resume_entries.md` | Human-readable copy of your approved protected bullet wording. Keep it in sync with `runtime_resume_context.json` by hand. |

It also copies `reference/resume_template_layout.example.json` and
`reference/cover_letter_template_layout.example.json` to their real
(non-`.example`) names.

### Precedence

When two sources disagree, the higher one wins:

```text
rejections.json            (strongest — an explicit refusal)
  > evidence_bank.json
  > metrics_registry.json
  > candidate_profile.json
  > eligibility_facts.json
  > candidate_facts.md     (weakest — narrative only)
```

A convenient lower-level summary can never override a more explicit rejection
or a verified evidence decision. Set your identity in **both**
`candidate_profile.json` and `runtime_resume_context.json`'s `candidate` block;
they are deliberately separate, and the runtime context is the one read during
a run.

- [ ] Replaced every fictional value with your own verified information.
- [ ] Every `id` is unique across the `experience` and `projects` arrays.
- [ ] At least one project has `"project_status": "selectable"`.

See [`master/README.md`](../master/README.md) for the per-file reference.

---

## 3. Create your document templates

The renderer opens an **existing** `.docx` and edits specific paragraphs by
0-based index. It does not lay out a resume from scratch. Which paragraph is
which lives entirely in a layout JSON file, not in any Python file.

- [ ] **Resume template.** Design your resume in Word, with every section and
      bullet already present as placeholder text, and save it as
      `reference/resume_template.docx`.

      To try the pipeline before designing your own, copy the committed
      fictional one — it is already a matching pair with the example layout:

      ```bash
      cp reference/examples/resume_template.example.docx \
         reference/resume_template.docx
      ```

- [ ] **Resume layout JSON.** `reference/resume_template_layout.json` already
      exists from step 2. Edit its indices to match your document. List your
      template's paragraphs with:

      ```bash
      python3 -c "
      from docx import Document
      doc = Document('reference/resume_template.docx')
      for i, p in enumerate(doc.paragraphs):
          print(i, repr(p.text))
      "
      ```

      Match each index against the layout's shape: `min_paragraph_count`,
      `name_index`, `contact_index`, `section_anchors` (the literal heading
      text at each heading paragraph), `education_blocks`, `experience_blocks`
      (each with an `owner_id` matching an `id` in your
      `runtime_resume_context.json`, a `tailoring_mode`, a `header_index`, and
      `bullet_indices`), `project_blocks` (the same shape, plus **exactly one**
      block with `"tailoring_mode": "selected"` — the dynamically-bound slot),
      and `skills_indices`.

      Character budgets (`max_chars_per_bullet`, `max_chars_title`,
      `max_chars_technology_line`, and their `_emergency` variants) are
      declared per block here. `tools/resume/payload_preflight.py` and
      `tools/resume/density_guard.py` read them from this file, not from any
      hardcoded table — set them from your own column widths and font size.

- [ ] **Cover-letter template.** Save yours as
      `reference/cover_letter_template.docx`. **The repository ships no
      example for this one**, so step 4 will report it missing until you add
      it. Its layout file, `reference/cover_letter_template_layout.json`, is
      simpler: `salutation_index`, `opening_index`,
      `experience_paragraph_blocks` (one `{owner_id, index}` per experience the
      letter should address), `project_paragraph_index`, `closing_index`, and
      `static_indices` — the paragraphs the renderer verifies are unchanged
      after every render, as a safety check.

If you later edit a `.docx` in a way that adds or removes a paragraph, you must
update its layout indices. Rendering fails loudly on a mismatch rather than
silently writing to the wrong paragraph.

See [`reference/README.md`](../reference/README.md) for the full contract.

### Where each line on the resume comes from

> This surprises people, so it is worth being explicit. The renderer
> (`template_layout.apply_payload`) edits **only** tailored content. Everything
> else on the page is whatever your `.docx` already said.
>
> | Line on the finished resume | Where its text comes from |
> | --- | --- |
> | Your name and the contact row | **The template `.docx`** — never rewritten |
> | Education blocks (school, degree, GPA, coursework) | **The template `.docx`** — never rewritten |
> | Section headings (`EDUCATION`, `SKILLS`, …) | **The template `.docx`** — never rewritten |
> | Skills lines | **The template `.docx`** — never rewritten |
> | Protected experience / project bullets | Text from **the template `.docx`**; only their **order** is chosen per job |
> | Tailorable experience bullets | Written per job from `master/runtime/runtime_resume_context.json` |
> | The one selected project (header, technology line, bullets) | Written per job from `master/runtime/runtime_resume_context.json` |
>
> So your identity, education and skills live in the `.docx`, which is exactly
> why `reference/` is git-ignored — those details are baked into the document
> itself. `master/canonical_resume_entries.md` is a **reference copy** of your
> approved protected wording for you to read and check against; nothing in the
> pipeline reads it at run time.

> **⚠️ A Skills line that wraps can fail the density check.** The renderer
> never touches your Skills lines, but every line they occupy counts against
> the page. `tools/resume/qa_resume.py` computes
> `usable_bottom_whitespace_in` and passes only when it lands between **0.25
> and 0.50 inches** (`density_guard.py`: `MIN_BOTTOM = 0.25`,
> `MAX_BOTTOM = 0.50`). If a long Skills line wraps onto a second line and
> drives the value below 0.25, the pipeline raises a `DENSITY` correction
> whose instruction is to *"shorten only allowed dynamic content"* — meaning
> it will shorten your tailored bullets, because it cannot shorten the Skills
> line it is not allowed to edit. Repeat that and the job can end at
> `CORRECTION_LIMIT_EXCEEDED`. Keep each Skills line to one rendered line in
> the template itself.

---

## 4. Validate the setup

```bash
python3 tools/validate_candidate_setup.py
```

Checks that every required private file exists and parses, that the shapes
match what the pipeline expects, that your layout `owner_id`s line up with your
candidate data, and that your templates exist and have at least
`min_paragraph_count` paragraphs. **No network, API, or model calls.**

Exit code `0` means every hard check passed — warnings may still be printed.
Exit code `1` means at least one hard check failed.

- [ ] Exit code is `0`. Fix everything it reports before going further.

---

## 5. Write the runtime-context manifest

```bash
python3 tools/resume/v8/runtime_context_guard.py write-manifest
```

`runtime_resume_context.json` is maintained by hand alongside the narrative and
evidence files, so it can drift out of sync with them. The guard records a
SHA-256 of each source file in
`master/runtime/runtime_resume_context.manifest.json`.

It watches these six sources:

```text
master/canonical_resume_entries.md
master/candidate_facts.md
master/candidate_profile.json
master/evidence_bank.json
master/metrics_registry.json
master/eligibility_facts.json
```

Then verify:

```bash
python3 tools/resume/v8/runtime_context_guard.py check
```

- [ ] Prints `RUNTIME CONTEXT: VALID`.

The guard only **detects** staleness — it never regenerates
`runtime_resume_context.json` for you. See step 12.

---

## 6. Run the offline test suites

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tests/run_public_tests.py
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/resume/v86/tests/run_all_v86_tests.py
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/intake/tests/run_all_tests.py
```

All three are offline and make no model calls.

**What to expect — not all of these pass, and that is by design:**

| Runner | On a bare clone (no `master/`, no template) | After steps 2–3 |
| --- | --- | --- |
| `tests/run_public_tests.py` | **PASS** | PASS |
| `tools/intake/tests/run_all_tests.py` | **PASS** (115 tests) | PASS |
| `tools/resume/v86/tests/run_all_v86_tests.py` | **Fails by design** — 9 failures, 24 errors of 507 tests | 3 failures remain |

The V8.6 runner exercises the real render and validation path, so without your
private inputs it cannot pass: the failures are `RUNTIME_CONTEXT_STALE` (no
`master/runtime/runtime_resume_context.json` or manifest) and `RENDER_FAILED`
(no `reference/resume_template.docx`). They are missing-input errors, not
defects.

Once you have completed steps 2–3, **3 findings still remain if you used the
shipped example template**: density/multipage results caused by that template's
geometry differing from the one those fixtures were calibrated against. With
your own template they depend on your own layout and budgets. If you only want
a signal that your checkout is sound, `run_public_tests.py` and the intake
suite are the two that should be green unconditionally.

---

## 7. Add job descriptions

Drop one Markdown file per posting into `jobs/batch_inbox/`. **The filename
stem is the job key** — it names the output directory and the tracker row — so
use something stable and readable, e.g.
`jobs/batch_inbox/example-co-ml-engineer.md`.

The pipeline reads the whole file as the posting text, and deterministically
reads the employer name off an **anchored `Company:` header line**. Three
literal forms are recognized:

```text
Company: Example Health Co
**Company:** Example Health Co
company: "Example Health Co"        (YAML frontmatter)
```

A line such as `## Company` or `# Company: X` is **not** one of them — the
regex anchors on `Company:` at the start of the line, allowing only optional
`**` bold markers. If two `Company:` lines disagree, the pipeline resolves no
company at all rather than guessing, so keep exactly one.

The canonical shape, which `tools/intake/` generates and
[`examples/sample_job.md`](../examples/sample_job.md) demonstrates, is:

```markdown
# Job Posting

## Metadata

Company: Example AI Labs
Role: Machine Learning Engineer Intern
Location: Example City, ST
Employment Type: Internship

## Job Description

<the full posting text, verbatim>
```

Paste the description verbatim. Do not summarize or trim it — requirement
extraction reads what you give it.

- [ ] At least one `*.md` file in `jobs/batch_inbox/`, each with exactly one
      `Company:` header line.

Optionally, let the intake tools normalize postings for you. Offline, this
makes no network call, writes no file, and changes no workbook:

```bash
python3 -u tools/intake/run_intake.py --dry-run --urls-file <urls.txt>
```

---

## 8. Offline dry run

Before spending anything, run the real batch path with the model calls mocked:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tools/resume/v86/run_batch.py \
  --input-dir jobs/batch_inbox \
  --done-dir jobs/batch_done \
  --max-workers 2 \
  --mock-semantic \
  --test-mode
```

- `--mock-semantic` replaces the model caller with a deterministic mock. No
  model call, no network, no cost.
- `--test-mode` writes `tracking/job_application_tracker_TEST.xlsx` instead of
  your live tracker. Everything else behaves identically.

This exercises rendering, validation, the ATS gate, finalization, and the
tracker write with your real templates and real candidate data — so it catches
layout-index mistakes and budget violations before a paid run. The mocked
content itself is placeholder text, so judge the *structure*, not the prose.

- [ ] The run completes and each job reports a terminal status.

A job that fails stays in `--input-dir`; it is never moved and never silently
retried. Rerunning picks it up as a fresh attempt.

---
## 9. Watermark cleaning (recommended before real runs)

Finalization is the last stage of every job, after the ATS gate and before the
tracker write. It is deterministic Python
(`tools/resume/v86/finalize_local.py`) — no model call — and it hands the
rendered files to a local watermark-cleaning HTTP service.

### What the finalizer actually does

The batch health-checks the service **exactly once per batch**, in
`run_batch.py`'s setup, with a `GET /health` against `--base-url` (default
`http://127.0.0.1:8765`). The result is passed to every job's finalizer, so one
answer governs the whole batch and no job re-checks it per file.

Then, for each of `resume.docx`, `resume.pdf`, `cover_letter.docx`, and
`cover_letter.pdf` in the job's output directory — missing files are recorded
as `SKIPPED_MISSING` and nothing else — it:

1. extracts the file's text and, for PDFs, its page count;
2. **saves the original** to `output/<job_key>/.work/finalize_originals/`
   before anything can be overwritten;
3. sends the file to the service's `/inspect`, then to `/clean` with exactly
   these options:

   ```json
   {
     "detect_before": true,
     "detect_after": true,
     "also_layer_a_text": true,
     "keep_non_ai_metadata": true,
     "strip_all_metadata": false,
     "deep_images": "auto"
   }
   ```

   The service's more invasive "Layer B" is never requested, and the record
   written for every job says `"layer_b": "NOT_RUN"`;
4. writes the returned bytes to `.work/finalize_cleaned/` and re-extracts their
   text;
5. **requires that the text is identical** before and after cleaning
   (Unicode-NFKC-normalized, blank lines and leading/trailing spaces ignored),
   and, for `resume.pdf` specifically, **that the resume is exactly one page
   both before and after**;
6. **replaces the original file only if those checks pass.** On any failure —
   the text changed, the page count changed, `/clean` errored or returned
   `ok: false` — the original file is left exactly as rendered and the artifact
   is marked `FAILED`.

Each job gets an `output/<job_key>/watermark_finalization.json` recording the
per-artifact outcome and an `overall_status`: `FINALIZED` when every present
artifact passed, `PARTIAL` when some passed, `FAILED` otherwise. That status
becomes the tracker's `Watermark Status`.

### Without a running service

Nothing breaks and nothing warns loudly. The health check fails, the finalizer
returns `SERVICE_UNAVAILABLE` without reading or writing a single artifact, the
job still completes normally, and the tracker records `Watermark Status` as
`NOT STARTED`. No `watermark_finalization.json` is written at all. **A batch
run therefore looks successful whether or not any cleaning happened** — which
is the reason for the wrapper script below.

### Getting the service

The cleaning service is a separate open-source project,
[`watermarks-remover`](https://github.com/guillaumemeyer/watermarks-remover).
It is not vendored here and no code from it is copied into this repository.
**Read its README and its license yourself** before you use it; this repository
makes no claim about either.

Clone it into `tools/watermarks-remover/`, which is git-ignored, and start it:

```bash
python3 service/scripts/server.py --host 127.0.0.1 --port 8765
```

It needs Python 3.10 or newer and no extra packages. Its own README lists
`GET /health`, `GET /capabilities`, `POST /inspect`, and `POST /clean` — the
four endpoints this finalizer calls. **A substitute service must provide all
four.** If any of them is missing, the affected artifacts are reported as
`FAILED` and `scripts/run_batch_with_cleaning.sh` exits `2`; the files
themselves are still left untouched, so nothing is damaged, but nothing is
cleaned either. **Check the upstream README and license yourself** — this
repository makes no claim about either. Set `WATERMARKS_REMOVER_DIR` if you
keep the checkout somewhere else.

### Use the wrapper for real runs

Instead of calling `run_batch.py` directly (step 10), run:

```bash
bash scripts/run_batch_with_cleaning.sh
```

Any extra flags are passed straight through to `run_batch.py`, so
`bash scripts/run_batch_with_cleaning.sh --test-mode` works. The script does
four things:

1. **Checks the runtime context.** Runs `runtime_context_guard.py check` and
   stops unless it prints `RUNTIME CONTEXT: VALID`, so a batch never generates
   from evidence you have already revised.
2. **Makes sure the service is healthy.** If `GET /health` already answers, it
   uses the running service. Otherwise — and only for a loopback URL — it looks
   for `service/scripts/server.py` in `WATERMARKS_REMOVER_DIR`, picks the
   newest Python 3.10+ it can find, starts the service, and waits up to 20
   seconds for it to become healthy. It stops the service again on exit, but
   only if it was the one that started it. If the service cannot be found or
   does not come up, **the batch never runs** — no paid model calls are made.
3. **Runs the batch** with `--input-dir jobs/batch_inbox --done-dir
   jobs/batch_done --max-workers 2`.
4. **Verifies that every job was finalized.** For each job folder created
   during the run it checks that `watermark_finalization.json` exists and says
   `FINALIZED`, prints a per-job verdict, and exits `2` if any job was not
   finalized. That exit code is the signal not to send those files.

Set `WATERMARKS_SERVICE_URL` to point at a service on another port; a non-local
URL is checked but never started.

### What cleaning does and does not do

Be honest with yourself about the guarantees here:

- **`FINALIZED` does not mean anything was removed.** The finalizer promotes a
  file whenever the text and page count survived the round trip. A file that
  needed no cleaning at all passes identically to one that was scrubbed.
- **PDF metadata is not stripped unless `exiftool` is installed**, and **C2PA
  provenance is only partially inspected unless `c2patool` is installed.**
  Neither is a Python dependency of this repository, and neither is installed
  by `requirements.txt`.
- **Cleaning never changes wording.** It cannot — identical extracted text is a
  precondition for promotion. It is not a rewrite, a paraphrase, or an
  "AI-detector bypass", and it makes no claim about how any detector will
  classify the result.
- **The checks are text and page count only.** Formatting, hyperlinks, and
  visual fidelity are not compared; read the promoted PDF yourself (step 11).

---

## 10. Real run

```bash
python3 -u tools/resume/v86/run_batch.py \
  --input-dir jobs/batch_inbox \
  --done-dir jobs/batch_done \
  --max-workers 2
```

**This makes real, paid model calls.** The normal path is two requests per job
— requirement extraction, then evidence-constrained generation — plus up to two
bounded correction rounds only when deterministic validation finds a specific,
correctable gap.

`--max-workers` bounds concurrent semantic calls. The code's default is `10`,
but start at `2`: higher is not automatically better, because it balances
throughput against model pressure, local rendering contention, and debugging
clarity — and on a first real run you want to be able to read what happened.
Raise it once a batch has worked end to end.

Successful jobs move to `jobs/batch_done/`. The batch prints its metrics as
JSON when it finishes.

---

## 11. Read the outputs

Everything below is git-ignored.

- [ ] **The resume** — `output/<job_key>/resume.pdf`, with
      `output/<job_key>/cover_letter.pdf` beside it. Open and read them. This
      is the deliverable; nothing downstream replaces your own judgement.

- [ ] **The ATS gate** — `output/<job_key>/ats_preflight.json`. The
      authoritative gate decision. Each job concept is
      `SUPPORTED_AND_PRESENT`, `SUPPORTED_BUT_MISSING`, or `UNSUPPORTED`. The
      gate passes when no required-and-supported concept is missing from the
      resume and coverage among required, high-importance, supported concepts
      is at least 90%. Concepts listed under `optional_supported_but_missing`
      are reported but never fail the gate on their own.

- [ ] **The quality report** —
      `output/<job_key>/resume_quality_report.json`. **Advisory only**: it
      cannot fail a job, change a status, trigger a correction, or spend a
      model call. Read `diagnostic_state` (`PASS` / `REVIEW` / `INCOMPLETE` —
      a state, not a score) and then the individual findings, each with a
      `severity` of `high` / `medium` / `low` / `info`. Categories cover
      artifact integrity, reading order, metrics, content quality, job match,
      design fidelity, timeline, and action-verb style.

      Check `artifact.stage` to see which file was actually inspected:
      `pre_finalization` when written by the deterministic pipeline, then
      `finalized` (or `finalization_not_applied`) once the artifact checks
      re-run against the promoted file. `artifact.sha256` makes that claim
      checkable.

- [ ] **The tracker** — `tracking/job_application_tracker.xlsx`, or
      `..._TEST.xlsx` under `--test-mode`. The pipeline owns the `Company`,
      `Role`, `Resume Status`, `Cover Letter Status`, `Watermark Status`,
      `Final Resume`, and `Final Cover Letter` columns. `Application Status`,
      `Applied Date`, and `Notes` are yours and are never overwritten.

- [ ] **Batch metrics** — `.work/v86/<batch-id>/batch_metrics.json` and
      `.txt`. Read a finished batch back out with:

      ```bash
      python3 tools/resume/v86/benchmark_report.py --batch-id <batch-id>
      ```

Also in each job directory: `jd_requirements.json`, `project_selection.json`,
`resume_payload.json`, `payload_preflight.json`, `resume_draft.md`,
`resume_qa.json`, and — when generated — `jd_match_report.json` and
`template_style_audit.json`.

---

## 12. After any edit to `master/`

```bash
python3 tools/resume/v8/runtime_context_guard.py write-manifest
```

Any time you change a file under `master/` — a new bullet, a corrected metric,
an added rejection — update `runtime_resume_context.json` to match by hand,
then re-run `write-manifest` so the recorded hashes reflect the new state.
Re-run `check` to confirm, and re-run
`python3 tools/validate_candidate_setup.py` if you changed a shape rather than
just a value.

The guard detects drift; it does not fix it. A stale manifest means the
pipeline may be generating from facts you have already revised.

---

## 13. What must never be committed

Verify with `git status` before your first commit. These are git-ignored, and
that is deliberate:

| Path | Why |
| --- | --- |
| `master/*` (except `README.md`, `examples/`) | Your real evidence, metrics, and identity. |
| `reference/*` (except `README.md`, `*.example.json`, `examples/resume_template.example.docx`) | Templates have your name and contact details baked into the document. |
| `jobs/batch_inbox/*`, `jobs/batch_done/*` (except `.gitkeep`) | Which jobs you are applying to. |
| `output/` | Every generated resume, cover letter, payload, and report. |
| `tracking/*.xlsx`, `*.xls`, `*.lock` | Your application history. |
| `.work/` | Runtime state, shared context, and batch artifacts. |
| `.env` | Credentials. Use `.env.example` as the committed template. |
| `*.docx`, `*.pdf` | Generated documents, anywhere in the tree. |

`tests/public/test_public_repo.py` enforces parts of this boundary as
executable checks, including that no absolute home path and no hardcoded
candidate city appears in a scanned file. It is not a substitute for reading
`git status` yourself.

See [Security and Privacy](security-and-privacy.md) for the full boundary.
