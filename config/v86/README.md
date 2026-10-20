# v86-only config

Files here are specific to the v86 pipeline. Nothing here modifies
`config/ats_aliases.json` (the v8 alias set, unchanged and reused as-is).

## `resume_quality_diagnostics.json`

Thresholds for the ADVISORY resume-quality diagnostics
(`tools/resume/v86/resume_quality_report.py` and the per-feature modules
it consolidates). Every value is a documented heuristic used to produce
non-blocking findings in each job's own git-ignored
`output/<job_key>/resume_quality_report.json` -- none of them is an
industry-standard ATS rule, and none participates in the pipeline's
pass/fail gate, the correction budget, or any model call.

| Key | Meaning |
|---|---|
| `short_bullet_min_visible_chars` | An EXPERIENCE/PROJECTS bullet whose visible length (markdown styling and bullet marker stripped) is STRICTLY below this gets a low-severity advisory. A bullet exactly at the threshold is never warned about. Provisional default: 60. |
| `partial_match_min_token_ratio` | PDF round-trip only. Ordered-token coverage at or above this reports a field as `partial`; below it, `missing`. |
| `min_readable_text_chars` | PDF round-trip only. Below this, the text layer is treated as effectively unreadable and field recall becomes `not_evaluated` rather than a cascade of false `missing` findings. |
| `timeline_gap_review_months` | Informational review threshold for an uncovered interval between consecutive experience periods. Never a gate failure. |

A missing, malformed, or wrongly-typed value silently falls back to the
built-in default -- a broken config can cost the custom threshold, never
a job.

Authoritative limits deliberately do NOT live here: per-field maximum
character budgets stay in `reference/resume_template_layout.json`
(enforced by `tools/resume/payload_preflight.py`), and rendered-page
density stays in `tools/resume/qa_resume.py` /
`tools/resume/density_guard.py`.

## `ats_concept_bridges.json`

Deterministic ATS semantic-evidence recognition. A small, explicitly
reviewed table of concept-term bridges, loaded ON TOP of
`config/ats_aliases.json` by `tools/resume/v86/ats_preflight_v86.py`
(`--extra-aliases`). Each key is a JD concept term/alias (normalized the
same case-insensitive/hyphen-folding way `tools/ats/preflight.py` already
normalizes everything); each value is a list of truthful evidence phrasings
that a candidate's own canonical/master content or rendered resume might
use, which truthfully satisfy that concept even when the literal JD wording
is absent. This is NOT fuzzy/similarity matching -- every entry is a
specific, reviewed literal alternative phrase, matched with the exact same
word-boundary `phrase_present` check as every other alias. This file ships
with a small, generic starter set; add your own bridges as you notice your
own evidence phrasing differs from common JD wording.
