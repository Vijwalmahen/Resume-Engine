# Application Tracker Contract

## Purpose

Maintain one persistent Excel workbook containing every job discovered by the
job-application pipeline and the current state of each application.

Workbook:

tracking/application_tracker.xlsx

Never create a separate tracker for each job.

If the workbook does not exist, create it.

If it already exists, update it in place without deleting or recreating
existing rows.

---

# Workbook Sheets

## Applications

Contains ONLY jobs that passed linkedin-job-scraper hard acceptance rules.

One job = one row.

Required columns, in this order:

1. Job Key
2. Company
3. Role
4. Location
5. Workplace Type
6. Employment Type
7. Date Posted
8. Date Scraped
9. Job Link
10. Required Skills
11. Preferred Skills
12. Experience Requirement
13. Education Requirement
14. Key Requirements
15. Salary
16. Resume Status
17. Cover Letter Status
18. Watermark Status
19. Final Resume
20. Final Cover Letter
21. Application Status
22. Applied Date
23. Notes

---

## Scrape Log

Record all materially valid raw job results inspected by the scraper,
including jobs rejected by hard filtering.

Required columns:

1. Job Key
2. Company
3. Role
4. Location
5. Employment Type
6. Workplace Type
7. Date Posted
8. Job Link
9. Scraped At
10. Acceptance Result
11. Rejection Reason

Acceptance Result values:

ACCEPTED
REJECTED
INCOMPLETE
ERROR

---

# Stable Job Identity

Every job must have a stable Job Key.

Preferred:

LinkedIn Job ID

Fallback:

stable hash based on normalized company + role + canonical URL.

Never create multiple Applications rows for the same Job Key.

When the same job is encountered again:

update the existing row rather than append a duplicate.

---

# Job Link

Job Link is one of the most important tracker fields.

Prefer the direct application URL when the scraper provides a reliable
employer/application URL.

Otherwise use the canonical LinkedIn job URL.

Never fabricate a job URL.

The Job Link cell must be a clickable Excel hyperlink when XLSX tooling
supports hyperlinks.

The visible cell text may be:

Apply

or the URL itself.

The underlying hyperlink must point to the real job URL.

---

# Requirement Extraction

When a job is accepted, populate requirement fields from the actual JD.

Required Skills:
comma-separated technical skills explicitly required by the employer.

Preferred Skills:
comma-separated preferred/nice-to-have skills.

Experience Requirement:
actual experience/seniority language from the JD.

Education Requirement:
actual education language from the JD.

Key Requirements:
short factual summary of the most important requirements.

Do not invent requirements.

Do not copy the entire JD into a spreadsheet cell.

The complete JD remains in jobs/inbox/.

---

# Status Lifecycle

When the scraper accepts a job:

Resume Status = NOT STARTED
Cover Letter Status = NOT STARTED
Watermark Status = NOT STARTED
Application Status = NOT APPLIED

When resume-tailor completes a resume:

Resume Status = COMPLETE

When resume-tailor completes a cover letter:

Cover Letter Status = COMPLETE

If cover letter generation was explicitly disabled:

Cover Letter Status = NOT REQUESTED

When finalize-resume successfully completes:

Watermark Status = FINALIZED

If finalization is incomplete:

Watermark Status = PARTIAL

If finalization fails:

Watermark Status = FAILED

Application Status remains:

NOT APPLIED

until the user manually changes it or explicitly instructs the system to
record an application.

Never mark a job APPLIED merely because application materials were generated.

---

# Final Artifact Paths

After successful finalization populate:

Final Resume
Final Cover Letter

with paths to the employer-facing final files.

Prefer paths relative to project root, for example:

output/acme-ml-intern/resume.pdf

Do not point these fields to temporary .work/ files.

---

# Workbook Safety

The tracker is persistent state.

Never:

- erase existing application history
- overwrite user-entered Applied Date
- overwrite user Notes
- reset Application Status
- recreate the workbook from scratch when it already exists
- create duplicate rows for the same Job Key

Automated stages may update only fields they own.

---

# Human-Owned Fields

These fields belong primarily to the user:

Application Status
Applied Date
Notes

Do not overwrite an existing non-empty value unless the user explicitly asks.

---

# Formatting

Keep the workbook professional and easy to scan.

Requirements:

- freeze the header row
- enable filtering
- bold headers
- wrap long requirement cells
- sensible column widths
- dates stored/formatted as dates when possible
- Job Link must be clickable
- preserve existing workbook formatting when updating

Suggested Application Status values:

NOT APPLIED
APPLIED
INTERVIEW
REJECTED
OFFER
WITHDRAWN

Suggested Watermark Status values:

NOT STARTED
FINALIZED
PARTIAL
FAILED

---

# Atomic Update Rule

Save the workbook after each successfully processed job.

Failure on one job must not destroy tracker updates for other jobs.

If tracker writing fails:

do not discard a valid scraped job, resume, cover letter, or finalized
application.

Report TRACKER_UPDATE_FAILED and preserve the application artifacts.
