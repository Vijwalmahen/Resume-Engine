# V8.6 Resume Batch Pipeline

V8.6 is the production batch runner for tailoring resumes and cover letters
against a folder of job postings. It is fully generic: every fact it
tailors comes from the candidate's own `master/runtime/runtime_resume_context.json`
and every schema is keyed by opaque `owner_id` strings the candidate
assigns to their own experience/project entries -- nothing in this
directory hardcodes an employer name, a university, or a project title.

This document describes the pipeline as it exists today: the architecture,
the file/data flow, and the specific engineering properties (truthfulness,
determinism, bounded cost, concurrency safety) that make it safe to run
unattended over a batch of job postings. A short "how this evolved"
section at the end summarizes the hardening history without repeating a
blow-by-blow bugfix log.

Throughout this document, examples use the fictional example candidate
("Candidate Example" / Example Research Lab / Example Data Systems /
Project Atlas / Project Beacon / Project Meridian / Project Horizon /
Project Solstice) documented in `master/examples/`. Replace these with
your own candidate's data -- nothing about the pipeline changes.

## Architecture

```
BUILD SHARED BATCH CONTEXT        (once per batch)
        |
FAST PARALLEL SEMANTIC WORK       (2 short, schema-bounded Claude calls/job)
        |
DETERMINISTIC PROJECT SELECTION   (pure Python, between the two calls)
        |
JOB-LOCAL DETERMINISTIC PIPELINE  (render, ATS, QA, density, action-verb check)
        |
TINY BOUNDED CORRECTION           (1 Claude call/round, <=2 rounds)
        |
DETERMINISTIC FINALIZATION        (pure Python; watermark-removal handoff)
        |
SERIALIZED TRACKER WRITE          (small dedicated lock)
        |
DONE (job posting moved to --done-dir)
```

Each job pipelines independently: job A can be in deterministic
validation, finalization, or the tracker write while jobs B and C are
still doing semantic work. The only genuinely serialized resources are the
Word/PDF conversion lock (reused unchanged from the underlying renderer)
and the tracker's own small file lock -- there is no global pipeline lock.

## Quick start

Production-style run:

```
python3 tools/resume/v86/run_batch.py \
    --input-dir jobs/batch_inbox --done-dir jobs/batch_done --max-workers 10
```

Test mode (writes to `tracking/job_application_tracker_TEST.xlsx` instead
of the live tracker; everything else behaves identically):

```
python3 tools/resume/v86/run_batch.py \
    --input-dir jobs/batch_inbox --done-dir jobs/batch_done --max-workers 10 --test-mode
```

Full offline test suite (mocked semantic calls, zero real Claude calls,
zero cost, zero network):

```
python3 tools/resume/v86/tests/run_all_v86_tests.py
```

Read a batch's own results back out afterward:

```
python3 tools/resume/v86/benchmark_report.py --batch-id <batch-id>
```

### Python interpreter note

Run everything in this directory with the system `python3`, not
`python3.13`. The deterministic scripts this pipeline invokes as
subprocesses (`render_resume.py`, `qa_resume.py`, `word_to_pdf.py`, the
`tools/resume/v8/*.py` scripts, `finalize_local.py`) depend on `openpyxl`,
`PyMuPDF` (`fitz`), and `python-docx`, which in most environments are only
installed against `python3`. (`requests` is NOT a dependency -- nothing in the
tree imports it; `finalize_local.py` uses `urllib.request` from the standard
library.) `tools/resume/v86/common.py`
resolves the interpreter used for those subprocess calls independently of
whichever interpreter launched the orchestrator (`V86_DETERMINISTIC_PYTHON`
overrides auto-detection if you ever need to), but the orchestrator process
itself (which imports `openpyxl` in-process for the tracker) must still be
launched with `python3`.

## What each file does

| File | Role |
|---|---|
| `run_batch.py` | One-shot batch runner: discovery/claiming, the semantic + deterministic + correction loop per job, finalization, tracker write, move-to-done, batch metrics. |
| `build_shared_context.py` | Builds the batch-wide candidate context, shape files, project-selection policy, and template layout ONCE per batch; rebuilds the ATS evidence set once and copies it to a batch-local, read-only directory. |
| `build_job_packet.py` | Builds one self-contained `job_packet.json` per job -- everything a semantic worker needs, with no filesystem access of its own. |
| `context_slicing.py` | Pure functions that shrink `candidate_context` to exactly what each call type can truthfully use (see "Context slicing" below). |
| `llm_invoke.py` | The single choke point for every Claude CLI invocation: builds the sanitized, tool-free, session-isolated command line; provides `MockCaller` for offline tests. |
| `semantic_worker.py` | The two real model calls: JD-requirements extraction (call 1) and tailored resume/cover-letter authoring (call 2). Embeds both JSON Schemas. |
| `correction_worker.py` | The one bounded-correction model call, and the packet builder that scopes its editable fields/evidence. |
| `deterministic_pipeline.py` | The job-local, parallel-safe control flow: contract validation, project-selection binding, payload preflight, render, ATS, QA, density, action-verb diversity, and the correction-request/apply-correction machinery. |
| `evidence_ownership.py` | Resolves which experience/project entry owns an ATS evidence hit, and whether that owner is editable right now. |
| `clear_winner_safeguard.py` | Post-processes the deterministic project selector's own rankings to catch a dominant, undecided `MODEL_CHOICE` case. |
| `action_verb_diversity.py` | Counts opening action verbs across Experience + Project bullets on the rendered resume; classifies a repeated verb as fixable/unfixable. |
| `resume_quality_report.py` | Consolidates every advisory diagnostic into one job-local `resume_quality_report.json`; owns the threshold config and the `artifact.stage` provenance. Advisory only -- it can never change a status, spend a correction round, or make a model call. |
| `resume_draft_model.py` | Parses the rendered `resume_draft.md` into identified, ordered expected fields (shared by the round-trip check and the short-bullet advisory). |
| `pdf_roundtrip.py` | Field-level content recall and reading-order diagnostics for the resume PDF, on top of `qa_resume.extract_pdf_text`. |
| `metrics_audit.py` | Evidence-aware quantifiable-impact audit: reports a verified measurement that an approved source bullet carries but the corresponding final tailored bullet no longer communicates. |
| `metrics_registry_adapter.py` | The one explicit boundary for translating an alternative (entity-keyed) metrics-registry shape into the canonical one, linking entities to resume-content groups only through an operator-supplied map. |
| `bullet_length_audit.py` | Advisory "unusually short bullet" check. The authoritative maximum-length and density gates are untouched. |
| `timeline_audit.py` | Education-aware experience-timeline diagnostics over the candidate's own structured `dates` strings. |
| `normalize_jd_concepts.py` | Deterministically merges exact-duplicate JD concept terms before contract validation. |
| `normalize_cover_letter_identity.py` | Deterministically parses the job posting's own literal company name and makes it authoritative for `cover_letter_payload.company`. |
| `ats_preflight_v86.py` | Adapted ATS evaluator: adds concept bridges, optional/preferred gate policy, and a negated-evidence guard on top of the shared `tools/ats/preflight.py` primitives. |
| `validate_strategy_contract_v86.py` | Adapted structural contract check, stamped with this pipeline's own worker identity instead of a skill's. |
| `finalize_local.py` | Deterministic (no-Claude) watermark-removal handoff and promotion/rollback logic. |
| `tracker_writer.py` | Deterministic, lock-serialized tracker xlsx writer that only ever touches pipeline-owned columns. |
| `benchmark_report.py` | Offline, zero-model-call report over a batch's own on-disk artifacts (quality summary, correction-state breakdown, company-identity check, contamination scan). |
| `common.py` | Shared plumbing: timing, atomic JSON I/O, file locking, the deterministic-interpreter resolver, subprocess wrapper. |
| `tests/` | The offline regression suite; `tests/run_all_v86_tests.py` runs every suite once, in order, with no suite ever invoking another. |

This pipeline also invokes several already-existing, general-purpose
scripts as subprocesses rather than reimplementing them:
`tools/resume/v8/select_project.py`, `tools/resume/v8/validate_project_selection.py`,
`tools/resume/v8/normalize_resume_payload.py`, `tools/resume/v8/render_cover_letter.py`,
`tools/resume/v8/build_runtime_ats_evidence.py`, `tools/resume/v8/runtime_context_guard.py`,
`tools/resume/render_resume.py`, `tools/resume/payload_preflight.py`,
`tools/resume/qa_resume.py`, `tools/resume/density_guard.py`,
`tools/resume/word_to_pdf.py`, and `tools/ats/preflight.py` (via
`ats_preflight_v86.py`'s reuse of its pure helper functions). None of these
are modified by anything in `tools/resume/v86/`.

## The generic data model, in one paragraph

Every experience/project entry in `master/runtime/runtime_resume_context.json`
declares its own stable `id` and a `tailoring_mode` of `"protected"` (its
`canonical_bullets` are verbatim and may only be reordered) or
`"tailorable"` (its `alternate_bullets_for_dynamic_tailoring` may be
reworded per job). Project entries additionally declare `project_status`:
`"required"` projects are always rendered; `"selectable"` projects form the
candidate pool for the one dynamically-bound project slot. `resume_payload.json`
is keyed entirely by these `owner_id` strings
(`experience_bullet_orders`, `experience_alternate_bullets`,
`project_bullet_orders`, `selected_project`) -- see
`config/v8/resume_payload_shape.json` for the exact shape. No field is
ever named after a specific employer or project. Editability everywhere in
this pipeline is derived from `tailoring_mode` and from which project is
currently bound, never from a hardcoded name list.

## Job lifecycle, end to end

1. **Batch setup** (`build_shared_context.py`, once): validates the
   runtime context isn't stale, rebuilds the ATS evidence set, and writes
   `shared_candidate_context.json` / `shapes.json` /
   `project_selection_policy.json` / `resume_template_layout.json` into
   `.work/v86/<batch-id>/`. The watermark-removal service's health is
   checked exactly once per batch and passed to every job's finalizer call.
2. **Discovery**: `run_batch.py` scans `--input-dir` for `*.md` postings,
   claims unclaimed ones, and rescans continuously while any job is active
   -- a job that arrives mid-batch is picked up as soon as a slot frees up.
   This is a one-shot batch, not a persistent queue: it stops when nothing
   is active and a final rescan finds nothing new.
3. **Packet build** (`build_job_packet.py`): one `job_packet.json` per job
   containing the posting text, the shared candidate context, content
   budgets/truth rules read from `reference/resume_template_layout.json`,
   and the allowed selectable-project pool.
4. **Semantic call 1** (`semantic_worker.run_requirements_call`): JD text +
   a compact evidence view -> `jd_requirements.json` (atomic concepts,
   each with `category`/`required`/`priority`/`aliases`). Deterministically
   de-duplicated (`normalize_jd_concepts.py`) immediately afterward.
5. **Deterministic project selection** (`tools/resume/v8/select_project.py`,
   invoked by `deterministic_pipeline.ensure_project_selection`): ranks
   every `selectable` project by incremental REQUIRED-concept coverage and
   either binds one (`BINDING_REQUIRED_COVERAGE`) or hands the whole pool
   to call 2 (`MODEL_CHOICE`). The clear-winner safeguard runs immediately
   after (see below).
6. **Semantic call 2** (`semantic_worker.run_content_call`): `jd_requirements.json`
   + the project binding + candidate context -> `resume_payload.json`,
   `cover_letter_payload.json`, `strategy_status`. The prompt is explicit
   that the bound project may not be substituted and that protected
   canonical bullets may only be reordered.
7. **Cover-letter company backfill** (`normalize_cover_letter_identity.py`):
   the job posting's own literal `Company:`-style header is parsed and made
   authoritative for `cover_letter_payload.company`, deterministically.
8. **`write_strategy_files`**: writes `jd_requirements.json`,
   `resume_payload.json`, `cover_letter_payload.json`, and
   `v8_strategy_status.json` into `output/<job_key>/`.
9. **Deterministic pipeline** (`deterministic_pipeline.run_job_pipeline`):
   strategy-contract validation -> project-selection guard -> payload
   normalization -> payload preflight -> render (docx + pdf) -> ATS
   evaluation -> QA -> density check -> action-verb diversity check. Each
   step either passes, hard-fails the job, or requests a bounded
   correction round.
10. **Correction loop** (`correction_worker.py`, <= 2 rounds): a narrow
    model call repairs only what the deterministic gate flagged, within a
    fixed `editable_fields` list; `deterministic_pipeline.apply_correction`
    refuses at the Python level to let a correction change the bound
    project's identity.
11. **Finalization** (`finalize_local.py`): deterministic watermark-removal
    handoff for `resume.docx`/`resume.pdf`/`cover_letter.docx`/`cover_letter.pdf`,
    with a before/after truthfulness and page-count check before promoting
    a cleaned artifact over the original.
12. **Tracker write** (`tracker_writer.py`): one lock-serialized update to
    `tracking/job_application_tracker.xlsx`, touching only pipeline-owned
    columns.
13. **Done**: the source posting is atomically renamed into `--done-dir`.
    A job that fails at any stage is left in `--input-dir` for diagnosis --
    never moved, never silently retried.

## Engineering properties

### Evidence-grounded generation

Both semantic calls are given `candidate_context` drawn only from the
validated `master/runtime/runtime_resume_context.json`, plus an explicit
list of truth rules (`build_job_packet.truth_rules`): never fabricate a
skill/technology/experience/metric; a skill appearing in the JD is not
evidence the candidate has it; protected canonical bullets must be
preserved exactly (only their order may change); the bound project may
never be substituted. Every model call runs with `--tools ""` (see
"Isolation and permissions" below), so there is no path by which a worker
could read outside its own packet to find additional "evidence."

### Deterministic validation

Nothing about whether a resume ships is decided by a model. Every gate is
a pure Python/subprocess check against explicit rules:
`validate_strategy_contract_v86.py` checks structural shape (bullet-order
permutations, non-empty required fields, correct owner identity);
`payload_preflight.py` checks per-field character budgets read from
`reference/resume_template_layout.json`; `ats_preflight_v86.py` checks
truthful evidence coverage against the JD's own extracted concepts;
`qa_resume.py` and `density_guard.py` check page count and vertical
density of the rendered PDF. A model call can only ever produce a
candidate result; whether that result is acceptable is always decided
deterministically.

### Bounded correction (<= 2 rounds)

`CORRECTION_ROUND_LIMIT = 2` in `deterministic_pipeline.py`. When a
deterministic gate fails, `write_correction_request` records the exact
failure reasons/details and `correction_worker.run_correction_call` makes
one narrow, schema-bounded repair attempt against only the fields listed
in `editable_fields`. If the same or a different gate still fails after 2
rounds, the job terminates `CORRECTION_LIMIT_EXCEEDED` and is left in the
inbox -- it is never silently shipped with an unresolved gate. Two
mechanical optimizations avoid spending a correction round on arithmetic:

- `enforce_correction_length_budget` trims an over-budget field at a word
  boundary (never introducing or rewording content) both before the first
  correction request is written (a same-stage pre-trim on the semantic
  worker's own output) and again on a correction round's own output,
  before it is accepted.
- Action-verb-diversity findings are treated as STYLE-only: if it is the
  *only* remaining finding once the correction budget is exhausted, the
  job still finishes as `RESUME_READY_WITH_WARNINGS` rather than hard
  failing (see "Action-verb diversity" below).

### Evidence ownership and placeability

`evidence_ownership.py` answers one question: for a REQUIRED,
supported-but-missing ATS concept, does at least one of its evidence hits
belong to something a correction round could actually edit right now (a
`tailorable` experience entry, or the currently-bound selectable project)?
Ownership is resolved structurally from the evidence hit's own JSON path
and the entry's own `id`/`tailoring_mode` field -- never by string-matching
resume text, never by guessing. A concept is only ever classified
`"unplaceable"` when *every* hit's owner is positively resolved and none is
editable; any ambiguity is treated as `"unknown"` and handled exactly like
`"placeable"`, so a required concept is never silently waived on
incomplete information. This prevents the pipeline from burning a bounded
correction round asking a worker to fabricate evidence into a field that
could never truthfully hold it, and prevents a real gap from being
silently dropped -- `deterministic_pipeline.split_missing_required_by_placeability`
routes a genuinely unplaceable gap into a non-blocking
`ats_unplaceable_required` warning instead.

### Context slicing for cost control

`context_slicing.py` is pure, subset-only logic: each function returns the
*same* validated candidate data, narrowed to exactly what one call type can
truthfully use, never introducing or rewording a fact.

- `requirements_evidence_context` (call 1): call 1 only ever needs to
  decide whether an "A or B or C" JD requirement is truthfully supported --
  a technology/skill/degree name-match. It receives a flattened
  technology/skill list plus education and experience/project
  names+technologies; contact info, canonical bullet wording, and
  resume-formatting rules are dropped because they cannot change a
  truthful support decision.
- `content_call_candidate_context` (call 2): when the deterministic
  selector already bound one project (`BINDING_*`), every other
  selectable-pool project is dropped -- call 2 is contractually forbidden
  from describing anything else, so they would be pure duplication.
  `MODEL_CHOICE` is an explicit no-op: the full, unfiltered pool is sent,
  because call 2 must genuinely choose among it.
- `correction_evidence_context`: a correction round's fixed
  `editable_fields` can only ever touch a `tailorable` experience entry's
  alternate bullets or the already-bound project's fields, so only that
  evidence is sent -- sourcing correction content from protected canonical
  bullets or an unbound project candidate would misattribute evidence, not
  just waste tokens.

Layered on top of context slicing, each call type also runs at its own
`--effort` level (`llm_invoke.py` / `semantic_worker.py` /
`correction_worker.py`): JD extraction and correction rounds are narrow,
mechanical tasks (`"low"`); tailored authoring is the one call that needs
real reasoning about JD fit, truthful evidence selection, and prose
quality (`"medium"`).

### Concurrency and state isolation across a batch

`run_batch.py` uses a `threading.Semaphore(--max-workers)` to bound how
many *semantic* (Claude subprocess) calls may be in flight at once;
deterministic/finalization work is not bounded by it, since rendering,
ATS, and QA have no genuine shared-resource conflict across jobs (the only
serialized resources are the Word-conversion lock and the tracker lock,
both already fine-grained). `ConcurrencyTracker` records the observed peak
for each phase into `batch_metrics.json`.

Because `output/<job_key>/` is keyed only by job name (not by batch id), a
job that is reprocessed by a later, independent batch run could otherwise
silently inherit stale decision state left by an earlier run.
`deterministic_pipeline.reset_attempt_local_state` is the one centralized
initializer for every file this pipeline ever treats as "prior decision to
reuse" -- correction-round bookkeeping (`v8_correction_status.json`,
`correction_request*.json`, `density_guard.json`) and project-selection
bookkeeping (`project_selection.json`, `v8_project_selection_validation.json`).
It is called at the true start of a fresh attempt (`run_batch.py`'s
`process_job`, before project selection ever runs) with a full reset, and
again, narrower, inside `write_strategy_files` (which always runs *after*
this same attempt's own project selection has already legitimately
happened, so that half of the reset would otherwise destroy a decision
this very attempt just made). Every other on-disk file this pipeline reads
back is either input/immutable (the batch-local ATS evidence copy, the
original job posting) or this same run's own fresh output, read back only
within the same call that just wrote it -- there is no other stateful
cross-run read anywhere in the pipeline.

### Deterministic project selection and the clear-winner safeguard

`tools/resume/v8/select_project.py` ranks every `project_status ==
"selectable"` entry by how many *incremental* REQUIRED JD concepts it
truthfully supports (beyond what fixed/protected content already covers),
using only evidence records whose `entity` is declared in that project's
own `evidence_entities`. It binds deterministically
(`BINDING_REQUIRED_COVERAGE`) whenever the top candidate has a positive
incremental match count; otherwise it hands the full pool to call 2 as
`MODEL_CHOICE`. `clear_winner_safeguard.py` is a pure post-processing step
over that same output -- never a re-scoring of evidence -- that upgrades a
`MODEL_CHOICE` result to a binding when the top candidate clears
conservative floors (>= 3 matched concepts, score >= 30) *and* dominates
the runner-up by a real margin (>= 2 more matched concepts, and >= 2x
score when the runner-up scored above zero). Ambiguous or tied cases are
left as `MODEL_CHOICE`, unchanged. A correction round can never change
which project is bound: `deterministic_pipeline.apply_correction` rejects
any attempt at the Python level, regardless of what the correction worker
returned, with reason `REJECTED_UNAUTHORIZED_PROJECT_RESELECTION`.

### ATS concept bridges and the negation guard

`ats_preflight_v86.py` layers two refinements on top of the shared
`tools/ats/preflight.py` primitives, without modifying that frozen module:

- **Concept bridges** (`config/v86/ats_concept_bridges.json`): a small,
  explicitly reviewed set of phrase equivalences (e.g. `"hybrid search"` ->
  `["hybrid retrieval", "hybrid dense+sparse retrieval", ...]`,
  `"reciprocal rank fusion"` -> `["RRF", "RRF fusion"]`) so truthful,
  already-rendered wording satisfies a JD concept phrased differently.
  This is never fuzzy matching -- every bridge is a literal phrase checked
  with the same word-boundary test as any other alias.
- **Negated-evidence guard** (`filter_negated_evidence_hits`): a hit is
  excluded from counting as truthful support only when an explicit
  negation marker ("not", "no", "never", "without", or an "...n't"
  contraction) appears in a short, same-clause window immediately before
  the matched phrase -- so master evidence that explicitly *rules out* a
  technique is never treated as evidence *for* it. A concept whose only
  hits are negated is reclassified `UNSUPPORTED`, never fabricated into the
  resume and never requested from a correction round.

The gate itself (`gate_pass`) requires zero missing REQUIRED-and-supported
concepts, plus >= 90% coverage among REQUIRED, high-importance, supported
concepts. An OPTIONAL (`required: false`) supported-but-missing concept is
reported (`optional_supported_but_missing`) but can never by itself fail
the gate; it becomes a non-blocking `RESUME_READY_WITH_WARNINGS` note.

### Action-verb diversity

`action_verb_diversity.py` counts each bullet's first lexical word across
the rendered Experience + Project sections (both protected and dynamic
bullets, since both are equally visible on the shipped resume) using
exact, case-insensitive string comparison -- never a stemmer or synonym
match. A verb repeated more than the limit (default 2) is split into
`fixable` (at least one occurrence sits in an editable bullet) and
`unfixable` (every occurrence is baked into protected canonical content,
so no correction round could ever change it). Only fixable repetition is
ever requested from a correction round; unfixable repetition is reported
as a non-blocking diagnostic. This is a STYLE-only check -- it never
touches ATS logic, evidence ownership, or project selection -- and is the
one correction reason allowed to consume the pipeline's last available
correction round without turning an otherwise-truthful, ATS/QA-passing
resume into a hard failure.

### Finalization and the watermark-removal handoff

`finalize_local.py` is pure Python -- no Claude call. For each of
`resume.docx`, `resume.pdf`, `cover_letter.docx`, `cover_letter.pdf`, it
extracts substantive text before cleaning, preserves an original copy,
calls the already-running watermark-removal HTTP service's `/inspect` and
`/clean` endpoints -- two of the four (`GET /health`, `GET /capabilities`,
`POST /inspect`, `POST /clean`) that the upstream project's README lists
and that this finalizer calls, all of which a substitute service must
provide -- and only promotes the cleaned artifact over the
original when the extracted text is byte-for-byte identical (after
normalization) and, for the resume PDF specifically, the page count is
still exactly 1. On any mismatch, or if the service is unavailable, the
original file is left untouched and the artifact is marked
`FAILED`/`SERVICE_UNAVAILABLE` rather than silently shipping a possibly
different document. The service's health is checked exactly once per
batch by `run_batch.py` and passed to every job, so an unavailable service
degrades every job in the batch the same, predictable way instead of
retrying per artifact.

The options sent to `/clean` are fixed in `PREFERRED_CLEAN_OPTIONS`:
`detect_before` and `detect_after` on, `also_layer_a_text` on,
`keep_non_ai_metadata` on, `strip_all_metadata` off, and
`deep_images: "auto"`. Layer B is never requested and every record says
`"layer_b": "NOT_RUN"`. Originals land in
`<job>/.work/finalize_originals/`, cleaned candidates in
`<job>/.work/finalize_cleaned/`.

Two consequences are worth stating plainly. `FINALIZED` means the cleaned
bytes round-tripped with identical text and an unchanged page count -- not
that anything was found or removed; a file needing no cleaning passes the
same way. And when the health check fails, no
`watermark_finalization.json` is written at all, so its absence (not a
`SERVICE_UNAVAILABLE` record on disk) is what marks a skipped job.
`scripts/run_batch_with_cleaning.sh` exists to turn that silent skip into
a hard stop; see `docs/GETTING_STARTED.md` section 9, "Watermark
cleaning".

### Tracker integration

`tracker_writer.py` writes only to `tracking/job_application_tracker.xlsx`
(or `_TEST.xlsx` in `--test-mode`), serialized by one small,
dedicated file lock -- never the Word-conversion lock, and never a
batch-wide lock. It only ever populates pipeline-owned columns (Company,
Role, Resume Status, Cover Letter Status, Watermark Status, Final Resume,
Final Cover Letter). User-owned columns (Application Status, Applied Date,
Notes) are never overwritten if already present, and Application Status
is never automatically set to `APPLIED`.

### Isolation and permissions

Every semantic/correction call (`llm_invoke.build_claude_command`) runs
with:

```
claude -p --output-format json --model sonnet \
    --session-id <fresh uuid, one per call> \
    --system-prompt <narrow prompt> --json-schema <schema> \
    --tools "" --disable-slash-commands --safe-mode \
    --permission-prompts none --no-session-persistence \
    --effort <low|medium> <packet>
```

run with `stdin` closed and a sanitized child environment
(`sanitized_worker_env`, which strips only Claude Code's own
session-identity variables -- `CLAUDE_CODE_*`, `CLAUDECODE`, `CLAUDE_PID`,
etc. -- while passing every auth-relevant variable through untouched).
`--tools ""` means the worker has no Bash/Read/Write and cannot spawn
subagents, explore the repository, or re-invoke the deterministic project
selector; `--disable-slash-commands` makes every skill unreachable even by
name; a fresh `--session-id` on every call means no two calls (concurrent
workers, sequential calls for the same job, or anything from the parent
process) can ever share a session identity.

## Status taxonomy

Every job resolves to exactly one terminal status, recorded in
`batch_metrics.json` and in each job's own `local_pipeline_result.json`:

| Status | Meaning |
|---|---|
| `RESUME_READY` | Full success, no warnings. |
| `RESUME_READY_WITH_WARNINGS` | Success; one or more non-blocking notes (optional ATS concept, proven-unplaceable required concept, or a still-repeated action verb). |
| `SEMANTIC_CALL_FAILED` | A Claude CLI call errored or returned non-conforming output. |
| `STRATEGY_FAILED` | Call 2 itself determined the job cannot be represented truthfully. |
| `STRATEGY_CONTRACT_FAILED` | Structural contract validation failed (shape, permutations, empty required fields, wrong worker identity). |
| `PROJECT_SELECTION_FAILED` | The deterministic project selector could not run or did not report `PASS`. |
| `PROJECT_SELECTION_MISMATCH` | The payload's bound project doesn't match what the selector guard expects. |
| `PAYLOAD_NORMALIZATION_FAILED` / `PAYLOAD_PREFLIGHT_FAILED` | Structural payload normalization or the character-budget preflight failed outright (not merely over budget). |
| `NEEDS_LLM_CORRECTION` | A deterministic gate flagged a specific, bounded repair; a correction round will run. |
| `CORRECTION_CALL_FAILED` | The correction worker's own call errored. |
| `CORRECTION_LIMIT_EXCEEDED` | A real, correctable gap survived 2 correction rounds. |
| `RENDER_FAILED` / `ATS_EXECUTION_FAILED` / `QA_EXECUTION_FAILED` | The corresponding deterministic subprocess step failed to produce its report. |
| `LOCAL_QUALITY_GATE_FAILED` | Rendering succeeded but the combined ATS/QA/density success gate did not pass. |
| `COVER_LETTER_PAYLOAD_MISSING` / `COVER_LETTER_RENDER_FAILED` | The cover letter could not be rendered. |

A job that fails at any stage is left in `--input-dir`, never moved and
never silently retried -- rerunning the batch will pick it up again from a
fresh attempt (see "Concurrency and state isolation" above).

## Batch and job artifacts, at a glance

Batch-level, under `.work/v86/<batch-id>/`:
`shared_candidate_context.json`, `shapes.json`,
`project_selection_policy.json`, `resume_template_layout.json`,
`shared_ats_evidence/` (read-only for the whole batch),
`jobs/<job_key>/job_packet.json`, `jobs/<job_key>/local_pipeline_result.json`,
`batch_metrics.json` / `.txt`.

Job-level, under `output/<job_key>/`: `jd_requirements.json`,
`project_selection.json`, `v8_project_selection_validation.json`,
`resume_payload.json`, `cover_letter_payload.json`,
`v8_strategy_status.json`, `v8_contract_validation.json`,
`payload_normalization.json`, `payload_preflight.json`,
`resume_draft.md`, `resume.docx`, `resume.pdf`, `ats_preflight.json`,
`resume_qa.json`, `density_guard.json` (only when density was checked),
`cover_letter.docx`, `cover_letter.pdf`, `correction_request.json`
(plus a per-round `correction_request.round{N}.json` snapshot),
`v8_correction_status.json`, `watermark_finalization.json`,
`resume_quality_report.json`.

## Resume-quality diagnostics (advisory)

On the deterministic pipeline's success path, and again after
finalization, one `resume_quality_report.json` is written into the job's
own (git-ignored) `output/<job_key>/` directory. It is purely diagnostic:
it never changes a job's status, never requests a correction round, never
spends a model call, and never produces a numeric ATS score.

| Category | What it reports | Source |
|---|---|---|
| `artifact_integrity` | text-layer readability, glyph corruption, and per-field content recall (`found` / `partial` / `missing` / `not_evaluated`) between `resume_draft.md` and the PDF | `pdf_roundtrip.py`; the replacement-character verdict and the page/density facts are restated from the existing `resume_qa.json` |
| `reading_order` | order fidelity and the specific elements that extract out of sequence | `pdf_roundtrip.py` |
| `metrics` | a verified measurement an approved source bullet carries that its own final tailored bullet no longer communicates | `metrics_audit.py` |
| `content_quality` | unusually short substantive bullets; the professional-summary check, which is `not_applicable` because this format has no summary section | `bullet_length_audit.py` |
| `job_match` | per-requirement job fit on two axes (resume presence vs. evidence support), evidence-backed opportunities, and requirements needing human review | `jd_match_report.py` (folded in when the job has a `jd_match_report.json`) |
| `design` | whether rendering changed the document's geometry, paragraph formatting or typography relative to its own template | `template_style_audit.py` (folded in when the job has a `template_style_audit.json`) |
| `timeline` | intervals between consecutive experience periods, classified `explained_by_education` / `partially_explained` / `unexplained` / `undetermined` | `timeline_audit.py` |
| `style` | repeated opening action verbs, reused verbatim from the pipeline's own single `action_verb_diversity` run | reused, never recomputed |

Each finding carries `id`, `category`, `severity` (`high`/`medium`/`low`/
`info`), `status`, `subject`, `message`, `evidence` and `recommendation`.
The report's overall `diagnostic_state` is `PASS`, `REVIEW` or
`INCOMPLETE` (precedence is documented at the top of
`resume_quality_report.py`), and only that state plus the report's path
is surfaced in the job result and batch summary -- the candidate-specific
findings stay in the job-local, git-ignored file.

**Which file is the resume.** The reusable repository renders
`resume.pdf` and that stays the default. A deployment whose renderer
emits a different filename passes `resume_pdf_name=` -- either an exact
name or a glob -- to `generate` / `refresh_after_finalization`, and the
round-trip, the sha256, and the finalization-record match all follow it.
`artifact.resolution` records the outcome: `found`, `missing` (honest
`not_evaluated`, never a false pass), or `ambiguous` when a glob matches
more than one file, which is a high-severity finding because picking one
would be a guess about which artifact ships.

**Metrics registry shape.** `metrics_audit` consumes one canonical
shape. `metrics_registry_adapter` is the single, explicit boundary for
translating an entity-keyed registry (`{"items": [{entity, id, name,
description, value, unit, source, verified}]}`) into it. The adapter
links an entity to a resume-content group **only** through an
operator-supplied `metrics_entity_content_map`; a registry entity whose
name merely equals a project or employer name is never linked on that
basis. An unmapped entity produces no metric and is reported as
`not_evaluated` with a reason. The optional `final_bullets` path in a
mapping entry tells the audit where a non-canonical payload shape keeps
that group's shipped bullets.

The report also records `written_to` (the filename it landed on, or
`null` if the write itself failed -- an advisory diagnostic never raises
into the pipeline, so an unwritable job directory is reported rather than
thrown).

**Which artifact was checked.** `finalize_local.py` promotes cleaned
bytes over `resume.pdf` in place, so the deterministic pipeline only ever
sees the pre-finalization render. The report records this honestly in
`artifact.stage`: `pre_finalization` when written by the deterministic
pipeline, then `finalized` (or `finalization_not_applied`) once
`run_batch.py` re-runs the artifact checks against the promoted file.
`artifact.sha256` makes the claim checkable. The report never says it
validated a finalized PDF it did not read.

Thresholds live in `config/v86/resume_quality_diagnostics.json`. They are
documented heuristics, not industry ATS rules, and they do not touch the
authoritative gates: per-field maximum character budgets stay in
`reference/resume_template_layout.json` (enforced by
`tools/resume/payload_preflight.py`) and rendered-page density stays in
`tools/resume/qa_resume.py` / `tools/resume/density_guard.py`.

## Explainable job matching and the recommendation gate (V8.6.9)

Three advisory tools sit alongside the gate. None of them can fail a job,
change a status, trigger a correction round or spend a model call.

| Tool | Question it answers |
|---|---|
| `jd_match_report.py` | For each job requirement: how does it surface in the resume, and can master evidence back it? |
| `recommendation_guard.py` | Is this proposed rewrite something the candidate's own evidence can carry? |
| `template_style_audit.py` | Did rendering change anything about the document except its words? |

### Why matching is kept on two axes

`ats_preflight_v86.py` **gates**, with a deliberately coarse three-state
verdict. `jd_match_report.py` **explains**, and keeps apart the two
questions that keyword reports collapse:

- `match_class` -- `exact` (the JD's own wording), `semantic` (a reviewed
  alias/bridge, with the matching phrase always named so a reviewer can
  reject it), `partial` (content-word overlap only), `none`, `ambiguous`.
- `evidence_support` -- `supported`, `negated_only` (the candidate's own
  evidence rules it out), `unsupported`.

The pairs mean different things, and only one of them licenses a change:

| Combination | Meaning |
|---|---|
| supported + none/partial | **Opportunity** -- real experience the resume fails to surface. The only quadrant where "add it" is legitimate. |
| unsupported + exact/semantic | **Ambiguous** -- the resume asserts what evidence cannot corroborate. An integrity signal, not a win. |
| negated_only + anything | **Ambiguous** -- evidence contradicts the requirement. Never auto-filled. |
| unsupported + none | A genuine gap, reported honestly. Never closed by this pipeline. |

Both tools reuse the frozen matching primitives in `tools/ats/preflight.py`
and the Fix 7 `partition_forms` helper in `ats_preflight_v86.py`, so the
gate and the explanation can never disagree about whether a phrase is
present.

### The recommendation gate

Four rules, first failure decides, rejecting by default:

1. every measurement in the proposed wording must already appear in the
   cited evidence (`unsupported_metric`);
2. at least one cited evidence record must resolve (`no_resolvable_evidence`);
3. the requirement must not be contradicted (`requirement_contradicted`)
   or merely absent (`requirement_unsupported`);
4. an ambiguous requirement yields `needs_human_review`, deliberately
   checked *before* the unsupported rejection so "we cannot tell" is never
   reported as "we know it is false".

Rule 1 is what refuses an external checker's suggestion to claim a
performance improvement that exists in no evidence record. The rule is
general -- nothing recognises any particular number or phrase.

### No composite score, anywhere

None of these tools produces a score, a weighted total or a match
percentage, and `resume_quality_report.json`'s `job_match.composite_score`
is explicitly `null`. Requirement importance comes only from the job
analysis that already ran (`required`, `category`, `priority`); buckets
exist so a missing required qualification is not displayed next to a
missing nice-to-have, not so they can be combined. External ATS scores
belong to external services whose methodology this project does not
model, imitate or predict.

### Render fidelity

`template_style_audit.py` diffs a rendered `.docx` against **its own
template** rather than against any hardcoded idea of a correct design, so
a template in any font stays valid and only *rendering* can fail the
check. It compares page geometry, paragraph count, style names, spacing,
indents, tab stops and heading borders strictly; typography is graded
against the template's own type scale, so a slot may reuse a face/size the
design already contains but may never introduce one the renderer invented.

Relatedly, `render_resume.py` no longer hardcodes `Calibri` at fixed point
sizes when it rewrites a paragraph. It reads the typography the template
declares for that paragraph and reuses it, falling back to the documented
defaults only when the template declares nothing -- previously a
non-Calibri template had its tailored bullets silently re-typeset while
its protected (XML-copied) bullets kept the original face.

## Testing

```
python3 tools/resume/v86/tests/run_all_v86_tests.py
```

runs every `test_v86_*.py` suite under `tools/resume/v86/tests/` exactly
once, in the pipeline's own historical order, each as its own subprocess.
No suite invokes another suite. Every suite is pure offline unittest: a
`MockCaller` stands in for the real Claude CLI (zero real Claude calls,
zero cost), while the deterministic subprocess steps (render/QA/ATS/etc.)
are the real scripts running against scratch fixtures. Suites are grouped
by the property they cover -- orchestration/queue behavior, the isolation
patch's environment sanitization, context-slicing safety, the clear-winner
safeguard, ATS concept bridges and the negation guard, fresh-attempt state
isolation, company-identity backfill, and action-verb diversity. Any
individual suite file can also be run directly
(`python3 tools/resume/v86/tests/test_v86_offline.py -v`).

## Production configuration

`config/active_resume_pipeline.json` is the one source of truth for which
version is live: `active_version`, `status`, and
`production_entrypoints.batch_runner` /
`production_defaults.max_workers` (nested under those keys, not at the
top level -- `benchmark_report.read_active_config_summary` is the
canonical reader). It also records `legacy_orchestrator.status: "DORMANT"`
for an older, unrelated queue-based orchestrator that is not part of this
execution path and shares no imports with it.

## How this evolved

V8.6 replaced an earlier, fully sequential, single-skill-per-job design
with the two-call, deterministically-gated architecture described above.
Development proceeded through a series of small, targeted hardening
passes, each validated with a real (paid) benchmark batch before the next
was attempted:

- **Isolation**: an early live-arrival test revealed that a spawned
  `claude -p` subprocess could inherit the parent Claude Code process's
  own session-identity environment variables, causing a worker to see
  content from the wrong conversation. Fixed by sanitizing the child
  environment, forcing a fresh session id per call, and adding
  `--safe-mode` (see "Isolation and permissions").
- **Cost**: per-call-type `--effort`, plus the context-slicing functions
  described above, cut token usage for the two mechanical call types
  without touching the one call that needs deep reasoning.
- **Project-selection determinism**: the clear-winner safeguard closed a
  gap where a genuinely dominant selectable project could still be handed
  to `MODEL_CHOICE` for lack of an *incremental required-concept* match.
- **ATS truthfulness**: concept bridges closed gaps where truthful,
  already-rendered wording didn't literally match a JD's phrasing; the
  negation guard closed a gap where evidence that explicitly ruled out a
  technique was still counted as support for it; evidence-ownership
  classification stopped the pipeline from ever requesting a correction
  for a concept no editable field could truthfully satisfy.
- **State isolation**: because job output directories are keyed by job
  name rather than by batch, several rounds of hardening centralized every
  piece of "prior attempt" state (correction bookkeeping, project
  selection) behind one reset function, called at the true start of each
  fresh attempt, so a stale decision from an earlier, unrelated batch run
  could never leak into a new one.
- **Cover-letter identity**: a deterministic parser now makes the job
  posting's own literal company name authoritative, closing a class of
  silently-generic ("your organization") or silently-wrong cover letters
  that a purely schema-valid, non-empty string had let through.
- **Style**: the action-verb-diversity check was added last, as a
  non-blocking quality pass that never affects truthfulness, ATS, or
  project selection.

Each of these passes is reflected in the current code as the properties
described above -- this file no longer keeps a separate, dated log of the
individual fixes and per-batch benchmark numbers that produced them.
