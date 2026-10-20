# Candidate Facts (fictional example)

Copy this file to `master/candidate_facts.md` and replace every section with
your own verified information. This file is the free-text source of truth
you write for yourself (and, optionally, hand to Claude when asking it to
help you draft `master/runtime/runtime_resume_context.json`). Nothing here
is read directly by the deterministic pipeline -- it exists so a human (or
an assisting model, under your supervision) always has the full, narrative
version of your facts to check every generated bullet against.

## Identity

- Full name: Candidate Example
- Email: candidate@example.com
- Phone: (555) 010-2000
- Location: Example City, ST
- LinkedIn: linkedin.com/in/candidate-example
- GitHub: github.com/example-user

## Education

### Example University -- M.S. Data Engineering
- Example City, ST. Aug 2024 -- May 2026. GPA 3.80/4.00.
- Relevant coursework: Distributed Data Systems, Data Pipeline
  Architecture, Database Internals, Applied Machine Learning.

### Example State College -- B.S. Applied Mathematics
- Example City. Aug 2019 -- May 2023. GPA 3.60/4.00.
- Relevant coursework: Linear Algebra, Probability & Statistics,
  Numerical Methods, Optimization.

## Experience

### Example Research Lab -- Machine Learning Research Intern (protected)
- Example City, USA (Remote). Jan 2025 -- May 2025.
- This entry is PROTECTED: its bullets are canonical and must be rendered
  verbatim (only their display order may change) on every resume.
- Fact 1: Built a data pipeline that cleaned and merged 5 heterogeneous
  lab datasets into one training-ready corpus.
- Fact 2: Trained a baseline classification model and improved its
  validation F1 score from 0.71 to 0.84 through feature engineering.
- Fact 3: Wrote automated tests that reduced a recurring data-pipeline
  regression from happening roughly weekly to zero over 3 months.
- Fact 4: Presented findings in a lab-wide seminar and incorporated
  feedback into a revised experiment design.

### Example Data Systems -- AI Engineer Intern (tailorable)
- Example City (Remote). Jun 2025 -- Aug 2025.
- This entry is TAILORABLE: the two bullets on the resume may be
  re-authored per job (within these truthful facts), never fabricated.
- Fact 1: Built an internal tool that automated a manual data-labeling
  step, cutting labeling turnaround time from 2 days to 4 hours.
- Fact 2: Integrated a vector database into an internal search prototype
  and added evaluation scripts the team still uses.
- Fact 3: Contributed to code review and documentation for a shared
  internal Python library used by 2 other teams.

## Projects

### Project Atlas (required, protected)
- Personal project. Not deployed to real users.
- Canonical bullets (verbatim, order may vary):
  1. Designed a document-classification pipeline covering 6 document
     categories with a held-out accuracy of 91%.
  2. Built a labeling and evaluation harness that cut manual QA time by
     half compared to the initial spreadsheet-based process.
  3. Wrote unit and integration tests achieving 85% coverage of the
     pipeline's core modules.

### Project Beacon (required, protected)
- Personal project. Not deployed to real users.
- Canonical bullets (verbatim, order may vary):
  1. Implemented a recommendation prototype using collaborative
     filtering over a public 100k-rating dataset.
  2. Improved offline NDCG@10 from 0.41 to 0.53 through hyperparameter
     tuning and feature engineering.
  3. Packaged the prototype as a reproducible CLI with a documented
     evaluation harness.

### Project Meridian (selectable, tailorable)
- Personal project. Not deployed to real users.
- Fact 1: Built a retrieval-augmented question-answering service over a
  50k-document corpus using hybrid (BM25 + dense) search.
- Fact 2: Reduced p95 query latency from 480ms to 190ms by adding an
  approximate nearest-neighbor index.

### Project Horizon (selectable, tailorable)
- Personal project. Not deployed to real users.
- Fact 1: Built a time-series demand-forecasting model for a simulated
  regional logistics network, reducing forecast error (MAPE) from 19% to
  11% versus a naive seasonal baseline.
- Fact 2: Packaged the forecasting model behind a weekly batch-scoring
  script that regenerated demand forecasts across 12 simulated
  distribution regions.

### Project Solstice (selectable, tailorable)
- Personal project. Not deployed to real users.
- Fact 1: Built an ETL data-quality validation framework that
  automatically checked schema drift and null-rate anomalies across 8
  simulated data sources.
- Fact 2: Reduced manual data-quality review time by 70% by
  auto-flagging anomalous batches before they reached a downstream
  warehouse table.

## Skills

- Languages: Python, C++, Java, SQL
- Frameworks: PyTorch, FastAPI, React
- Tools: Docker, Git, PostgreSQL
- Also has exposure to (not primary): AWS, GCP

## Truth constraints

- Never claim a production deployment to paying/real users for any
  project listed above.
- Never state a specific user/customer count for any project.
- Never claim a leadership/management role; all work was individual or
  with at most one peer collaborator.
