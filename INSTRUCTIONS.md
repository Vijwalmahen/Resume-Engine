# Instructions for New Users

**If you've just cloned this repository, start with
[`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md)** — it is the ordered
checklist from first setup to a finished resume, with the exact commands.

This document is the companion **field reference**: what every candidate
data file means and what to put in it. The two overlap deliberately — the
checklist tells you *when* to edit these files, this one tells you *what*
goes in them.

If you want the deeper engineering explanation of *why* the system is
built this way, see [`README.md`](README.md) and the documents under
[`docs/`](docs/).

---

## What this is

A tool that generates truthful, job-tailored resumes and cover letters
from **your own verified facts** — never from invented experience. You
provide your real education, jobs, projects, skills, and metrics once;
the pipeline tailors *how* that truthful material is presented for each
job description, and refuses (rather than fabricates) when a job's
requirements genuinely aren't supported by your background.

Everything about your identity lives in files you create locally under
`master/` and `reference/`. None of it is committed to Git. The
repository ships only fictional example data.

---

## Step 0: Environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Install and authenticate the [Claude CLI](https://docs.claude.com/claude-code)
locally if you plan to run live (paid) model calls later. You do **not**
need it to complete the setup steps below.

---

## Step 1: Bootstrap your candidate files

```bash
python3 tools/bootstrap_candidate.py
```

This copies the fictional example files from `master/examples/` into
their real, private locations (e.g. `master/candidate_profile.json`,
`master/runtime/runtime_resume_context.json`). It **never overwrites**
anything that already exists, and it's safe to run again at any time —
it will just report which files already exist and skip them.

You now have a complete, valid, but entirely fictional candidate setup.
The rest of these steps are about replacing the fictional values with
your own.

---

## Adding Your Information

Work through these files in this order. Each one is private and
git-ignored — edit the **real** file (not the `.example` version, which
stays in the repo as a template for future users).

### 1. Identity — `master/candidate_profile.json` and `master/runtime/runtime_resume_context.json`'s `candidate` block

```json
{
  "full_name": "Your Name",
  "email": "you@example.com",
  "phone": "(555) 555-5555",
  "location": "City, State",
  "linkedin": "linkedin.com/in/you",
  "github": "github.com/you",
  "portfolio": "yoursite.dev"
}
```

Set this in **both** files (they're kept separate on purpose:
`candidate_profile.json` is a simple standalone identity block;
`runtime_resume_context.json.candidate` is the one the pipeline actually
reads at run time).

### 2. Education — `runtime_resume_context.json`'s `education` array

Add one entry per degree:

```json
{
  "id": "education_1",
  "institution": "Your University",
  "degree": "M.S.",
  "field": "Your Field",
  "location": "City, State",
  "dates": "Aug 2024 -- May 2026",
  "gpa": "3.8/4.0",
  "coursework": ["Course One", "Course Two"]
}
```

`gpa` and `coursework` are optional — omit them if you'd rather not
include them.

### 3. Professional experience / internships — `runtime_resume_context.json`'s `experience` array

For each job, decide: should its bullets be **fixed forever** (protected)
or **reworded per job application** (tailorable)? Most people have one or
two most-recent/most-relevant roles they want tailorable, and older roles
they're happy to keep fixed.

**Protected** (verbatim bullets, order may be shuffled):

```json
{
  "id": "experience_1",
  "employer": "Your Employer",
  "title": "Your Title",
  "location": "City, State",
  "resume_display_location": "City, State",
  "dates": "Jan 2025 -- May 2025",
  "tailoring_mode": "protected",
  "canonical_bullets": [
    "Achieved X, measured by Y.",
    "Built Z, resulting in W."
  ],
  "technologies": ["Python", "SQL"]
}
```

**Tailorable** (truthful facts the model may reword per job, never
fabricate beyond):

```json
{
  "id": "experience_2",
  "employer": "Your Other Employer",
  "title": "Your Title",
  "tailoring_mode": "tailorable",
  "alternate_bullets_for_dynamic_tailoring": [
    "Fact-based bullet one.",
    "Fact-based bullet two."
  ],
  "technologies": ["..."]
}
```

Every `id` must be unique across the whole `experience` array.

### 4. Projects — `runtime_resume_context.json`'s `projects` array

Same protected/tailorable choice as experience, PLUS a `project_status`:

- `"required"` (always pairs with `tailoring_mode: "protected"`) — this
  project appears on **every** resume, unconditionally.
- `"selectable"` (always pairs with `tailoring_mode: "tailorable"`) —
  this project is a candidate for the **one** dynamically-chosen project
  slot; the pipeline picks whichever selectable project best supports
  each specific job, automatically.

You need **at least one** `"selectable"` project — see `master/examples/`
for a full worked example with two required and three selectable
projects.

### 5. Skills — `runtime_resume_context.json`'s `skills` block

```json
{
  "static_resume_lines": {
    "Languages": "Python, C++, Java, SQL",
    "Frameworks": "PyTorch, FastAPI, React"
  },
  "extended_for_dynamic_evidence": {
    "cloud": ["AWS", "GCP"],
    "research_interests_not_implemented": ["things you're interested in but haven't actually built"]
  }
}
```

`static_resume_lines` renders verbatim on every resume. Anything under
`research_interests_not_implemented` is explicitly excluded from ever
being treated as demonstrated experience — use it for genuine interests
you don't want claimed as skills.

### 6. Verified metrics — `master/metrics_registry.json`

List every number you're willing to have quoted, tied to the
experience/project `id` it belongs to. The model is never allowed to
invent a metric that isn't here or already embedded in a bullet.

### 7. Truth constraints — `runtime_resume_context.json`'s `truth_constraints` array

Plain-language statements of things that must never be overstated or
misattributed, e.g. `"Never claim a leadership/management role."`

### 8. Rejected / unsupported claims — `master/rejections.json`

An explicit denylist of specific claims that must never appear, typically
because a past draft got something wrong and you're locking that fix in.

### 9. Work authorization / eligibility — `master/eligibility_facts.json`

Work authorization, relocation, and availability, so those questions are
answered truthfully instead of guessed.

### 10. Evidence bank — `master/evidence_bank.json`

Structured, atomic facts (one entry per fact) used to score which
*selectable* project truthfully supports a given job's requirements.
`entity` must match a project's `name`. See
[`docs/candidate-data-guide.md`](docs/candidate-data-guide.md) for the
full field reference.

### 11. Free-text sources — `master/candidate_facts.md` and `master/canonical_resume_entries.md`

Human-readable narrative versions of everything above — useful as your
own reference and if you ask an assistant to help you draft the JSON
files, but not read automatically by the pipeline.

---

## Step 2: Add your document templates

The renderer edits an **existing** Word document at fixed paragraph
positions — it does not create a resume from scratch.

1. Design your resume in Word (or export one you like), save it as
   `reference/resume_template.docx`.
2. Copy `reference/resume_template_layout.example.json` to
   `reference/resume_template_layout.json` and edit the paragraph indices
   to match your document (full instructions in
   [`reference/README.md`](reference/README.md)).
3. Do the same for `reference/cover_letter_template.docx` and
   `reference/cover_letter_template_layout.json`.

---

## Step 3: Validate before spending any money

```bash
python3 tools/validate_candidate_setup.py
```

This checks every file above exists, parses, has the right shape, that
your layout files' `owner_id`s match your candidate data, and that your
templates exist — **with zero network or model calls**. Fix everything it
reports before continuing.

Note: the repository ships a fictional example resume template
(`reference/examples/resume_template.example.docx`) but **no** example
cover-letter template, so this will report a missing cover-letter template
until you add your own.

---

## Step 3b: Record the runtime-context manifest

```bash
python3 tools/resume/v8/runtime_context_guard.py write-manifest
python3 tools/resume/v8/runtime_context_guard.py check
```

`runtime_resume_context.json` is kept in sync with the narrative and
evidence files **by hand**, so it can drift. The guard records a SHA-256 of
each source file in
`master/runtime/runtime_resume_context.manifest.json`, and `check` prints
`RUNTIME CONTEXT: VALID` when nothing has changed since.

Re-run `write-manifest` after **every** edit to anything under `master/`.
The guard only detects staleness — it never regenerates
`runtime_resume_context.json` for you.

---

## Step 4: Run the portable/offline tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -u tests/run_public_tests.py
```

This exercises the deterministic pipeline logic without making any paid
model calls. It should pass regardless of whether you've added real
candidate data yet.

---

## Step 5: Add a job description

Create a Markdown file under `jobs/batch_inbox/`, e.g.
`jobs/batch_inbox/example-company-ml-engineer.md`. The filename stem is
the job key — it names the output directory and the tracker row.

```markdown
# Job Posting

## Metadata

Company: Example Company
Role: Machine Learning Engineer
Location: Example City, ST

## Job Description

<paste the job description text here, verbatim>
```

The `Company:` line matters: the pipeline reads the employer name off an
**anchored `Company:` header line**, and only three literal forms match —
`Company: X`, `**Company:** X`, or a YAML `company: "X"`. A `## Company`
section heading or a `# Company: X` title line is **not** matched. Keep
exactly one such line; two that disagree make the pipeline resolve no
company at all rather than guess.

See [`examples/sample_job.md`](examples/sample_job.md) for a full
fictional example.

---

## Step 6: Run the batch pipeline

```bash
python3 -u tools/resume/v86/run_batch.py \
  --input-dir jobs/batch_inbox \
  --done-dir jobs/batch_done \
  --max-workers 10
```

This makes real, paid Claude API calls. Each job gets exactly 2 model
requests in the normal case (job-requirement extraction, then content
generation), plus up to 2 bounded correction rounds only if deterministic
validation finds something to fix.

To rehearse the whole path first without spending anything, add
`--mock-semantic --test-mode`: the model caller is replaced with a
deterministic mock, and the tracker write goes to
`tracking/job_application_tracker_TEST.xlsx` instead of the live workbook.

The final stage hands each rendered file to a local watermark-cleaning
service. If that service is not running, the step is skipped silently and
the job still reports success with `Watermark Status = NOT STARTED`. For
real runs prefer `bash scripts/run_batch_with_cleaning.sh`, which refuses
to start the batch unless the service is healthy and then checks that
every job was finalized. See `docs/GETTING_STARTED.md` section 9,
"Watermark cleaning".

---

## Where things end up

- Generated resumes/cover letters: `output/<job-key>/resume.pdf`,
  `output/<job-key>/cover_letter.pdf` (plus `.docx` and intermediate
  JSON/markdown artifacts for debugging).
- The ATS gate decision: `output/<job-key>/ats_preflight.json`.
- The advisory quality report:
  `output/<job-key>/resume_quality_report.json` — `diagnostic_state` is
  `PASS`, `REVIEW`, or `INCOMPLETE`. It is a diagnostic state, not a
  score, and it cannot fail a job or trigger a correction.
- Completed job postings move from `jobs/batch_inbox/` to
  `jobs/batch_done/`.
- Tracking spreadsheet: `tracking/job_application_tracker.xlsx`
  (git-ignored). The pipeline owns the `Company`, `Role`, `Resume
  Status`, `Cover Letter Status`, `Watermark Status`, `Final Resume`, and
  `Final Cover Letter` columns; `Application Status`, `Applied Date`, and
  `Notes` are yours and are never overwritten.
- Batch metrics: `.work/v86/<batch-id>/batch_metrics.json`.

---

## Which directories are private (never committed)

`master/` (except `README.md` and `examples/`), `reference/` (except
`README.md`, `*.example.json`, and
`examples/resume_template.example.docx`), `.work/`, `output/`,
`jobs/batch_inbox/*`, `jobs/batch_done/*`, `tracking/*.xlsx`, `.env`, and
any generated `.docx`/`.pdf`. See [`.gitignore`](.gitignore) for the exact
rules — verify with `git status` before your first commit.

---

## Candidate Data Checklist

- [ ] Contact information added (`candidate_profile.json` +
      `runtime_resume_context.json.candidate`)
- [ ] Education added
- [ ] Experience added (with a `tailoring_mode` on every entry)
- [ ] Projects added (with `project_status` + `tailoring_mode`, at least
      one `selectable`)
- [ ] Skills added
- [ ] Verified metrics reviewed (`metrics_registry.json`)
- [ ] Truth constraints reviewed
- [ ] Rejected claims reviewed (`rejections.json`)
- [ ] Resume template added (`reference/resume_template.docx` +
      `resume_template_layout.json`)
- [ ] Cover-letter template added (`reference/cover_letter_template.docx`
      + `cover_letter_template_layout.json`)
- [ ] `python3 tools/validate_candidate_setup.py` passes
- [ ] `python3 tools/resume/v8/runtime_context_guard.py check` prints
      `RUNTIME CONTEXT: VALID`
- [ ] `python3 tests/run_public_tests.py` passes
- [ ] Job descriptions added under `jobs/batch_inbox/`, each with exactly
      one `Company:` header line

---

## Next steps / deeper reading

- [`docs/GETTING_STARTED.md`](docs/GETTING_STARTED.md) — the ordered
  setup-to-resume checklist, including the offline dry run and how to
  read each output file.
- [`docs/candidate-data-guide.md`](docs/candidate-data-guide.md) — full
  schema reference for every file above.
- [`docs/architecture.md`](docs/architecture.md) — how the pieces fit
  together.
- [`docs/retrieval-and-grounding.md`](docs/retrieval-and-grounding.md) —
  how job requirements are matched against your evidence.
- [`docs/deterministic-validation.md`](docs/deterministic-validation.md)
  — how truthfulness and structure are enforced after generation.
- [`docs/security-and-privacy.md`](docs/security-and-privacy.md) — the
  public/private boundary in more depth.
