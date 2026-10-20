# Canonical Resume Entries (fictional example)

Copy this file to `master/canonical_resume_entries.md`. It is the
human-readable, approved-wording counterpart to the `canonical_bullets`
arrays already embedded in `master/runtime/runtime_resume_context.json` --
keep the two in sync by hand. This file exists so you (or an assisting
model) can review exact, final, protected wording in one place without
digging through JSON.

## Example Research Lab -- Machine Learning Research Intern (owner_id: experience_1)

1. Built a data pipeline that cleaned and merged 5 heterogeneous lab
   datasets into one training-ready corpus.
2. Trained a baseline classification model and improved its validation
   F1 score from 0.71 to 0.84 through feature engineering.
3. Wrote automated tests that reduced a recurring data-pipeline
   regression from happening roughly weekly to zero over 3 months.
4. Presented findings in a lab-wide seminar and incorporated feedback
   into a revised experiment design.

## Project Atlas (owner_id: project_1)

1. Designed a document-classification pipeline covering 6 document
   categories with a held-out accuracy of 91%.
2. Built a labeling and evaluation harness that cut manual QA time by
   half compared to the initial spreadsheet-based process.
3. Wrote unit and integration tests achieving 85% coverage of the
   pipeline's core modules.

## Project Beacon (owner_id: project_2)

1. Implemented a recommendation prototype using collaborative filtering
   over a public 100k-rating dataset.
2. Improved offline NDCG@10 from 0.41 to 0.53 through hyperparameter
   tuning and feature engineering.
3. Packaged the prototype as a reproducible CLI with a documented
   evaluation harness.

These bullets may only be REORDERED per job (via `experience_bullet_orders`
/ `project_bullet_orders` in `resume_payload.json`), never reworded,
shortened, or reworded to insert JD keywords.
