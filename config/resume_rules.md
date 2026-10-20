# Resume Rules — Lean v7.5

This file contains supporting truth and tailoring rules only.

It must NOT introduce additional reasoning loops beyond the active
resume-tailor skill.

# Source Precedence

For protected (canonical) experience/project entries, coursework, Skills, and
approved resume display values:

1. master/canonical_resume_entries.md
2. master/metrics_registry.json
3. master/evidence_bank.json
4. master/candidate_profile.json
5. master/candidate_facts.md

If these sources conflict for canonical content, the higher source wins.

The JD may influence relevance and terminology, but never candidate truth.

# Truth

Never invent:

- employment;
- job titles;
- dates;
- education;
- coursework;
- technologies;
- responsibilities;
- deployments;
- users;
- metrics;
- leadership;
- publications;
- certifications.

Unsupported requirements remain unsupported.

# Metrics

Use only verified metrics.

For protected (canonical) experience and project entries, canonical metrics
are defined by master/canonical_resume_entries.md and mirrored in
metrics_registry.json.

Do not use superseded metrics from old drafts.

Do not metric-stack merely to make bullets look impressive.

# JD Terminology

Use exact JD terminology when:

- the underlying concept is supported;
- the phrase fits naturally;
- it does not change the factual meaning.

Do not keyword-stuff.

Do not use arbitrary keyword quotas.

# Resume Structure

Use exactly:

Candidate Name
Contact Information

EDUCATION
PROFESSIONAL EXPERIENCE
PROJECTS
SKILLS

No Summary.

No target title or professional headline beneath the candidate name.

# Static Content

A protected experience entry (tailoring_mode=protected) renders ALL of its
canonical bullets verbatim (only their display order may change). A
tailorable experience entry (tailoring_mode=tailorable) uses exactly 2
substantive alternate bullets.

Every project with project_status=required is mandatory and renders ALL of
its canonical bullets verbatim. Exactly one project with
project_status=selectable is chosen dynamically per job and uses exactly 2
substantive bullets.

NOTE (current architecture limitation, not a universal candidate rule):
this pipeline's static resume layout assumes exactly one protected
experience entry, exactly one tailorable experience entry, and exactly two
required projects -- bullet counts above (e.g. "4 canonical bullets" for
the protected experience, "3 canonical bullets" for each required project)
reflect whatever `master/runtime/runtime_resume_context.json` actually
defines for those entries, not a fixed number baked into this file. A
candidate profile with a different number of protected/required entries
would need this file (and config/resume_layout.md's density targets)
revisited -- this is a documented limitation of the current reusable
architecture, not something the pipeline derives automatically today.

Skills:
exactly the three approved static lines.

Coursework:
always include the approved university coursework lines.

# Project Selection

Every project_status=required project is mandatory.

The selected project_status=selectable project is dynamic and should be
the strongest approved selectable project for the target JD.

Normally use exactly 3 projects (this candidate profile's current 2
required + 1 selectable) -- see the NOTE above.

# Bullet Quality

Prefer:

Action + Technical Method + Scope + Result

or:

What was built + how it was built + meaningful outcome/scale.

Use strong ownership language only when supported.

Avoid generic bullets.

# Seniority

Match internship/new-grad seniority accurately.

Do not exaggerate scope.

Do not weaken genuine implementation ownership.

# ATS

Use deterministic tools/ats/preflight.py.

Do not create model-generated ATS scores unless specifically requested.

Do not perform separate semantic-ATS, recruiter-score, market-analysis, or
role-family-analysis loops.

Maximum ATS-driven text correction:

ONE.

# Formatting

config/resume_layout.md is authoritative.

reference/resume_template.docx (with reference/resume_template_layout.json)
is the approved visual baseline.

Do not redesign the resume.

# Rendering

Target:

ONE resume render.

Normal maximum:

TWO resume renders.

A second render is only for a real artifact/layout failure.

One emergency third render is permitted only when deterministic resume QA
reports:

minimal_page_2_spill = true

after render #2.

The emergency third render must use one minimal targeted correction and must
not change canonical content or approved visual design.

If render #3 still exceeds one page, stop with RENDER_FAILED.

No subjective polishing loops.

# Density

Target bottom usable whitespace:

approximately 0.25–0.50 inch.

Do not solve density using:

- larger fonts;
- larger margins;
- filler;
- generic soft skills;
- invented content.

# Final Rule

Strongest truthful representation of the candidate.

No fabrication.

No repeated analysis loops.


# Automatic One-Page Density Gate

Approved usable bottom whitespace is 0.25-0.50 inches.

A one-page resume with usable bottom whitespace below 0.25 inches is too
dense and must not be finalized.

If deterministic QA reports a one-page dense condition:

1. run tools/resume/density_guard.py;
2. perform one automatic correction using only dynamic tailorable-experience /
   selected-project content;
3. rerun ATS when wording changes;
4. rerender exactly once;
5. rerun deterministic QA.

The worker must not ask the user for permission for this correction.

If the second render still has usable bottom whitespace below 0.25 inches,
stop with DENSITY_FAILED.

PASS_WITH_DENSITY_WARNING is not sufficient for READY_FOR_SERIAL_FINALIZE.

Canonical experience, project, coursework, Skills, margins, fonts, and
section geometry must never be altered to repair density.
