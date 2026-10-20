# Job Posting

## Metadata

Company: Example AI Labs
Role: Machine Learning Engineer Intern
Location: Example City, ST
Work Arrangement: Hybrid
Employment Type: Internship

## Job Description

Example AI Labs is looking for a Machine Learning Engineer Intern to help build and evaluate production-oriented AI systems.

### Responsibilities

- Build and evaluate machine-learning and retrieval pipelines.
- Develop Python services for model inference and experimentation.
- Design evaluation workflows for model quality and reliability.
- Work with structured and unstructured data.
- Collaborate with engineers to move prototypes toward production.

### Preferred qualifications

- Strong Python programming skills.
- Experience with machine learning or NLP.
- Familiarity with retrieval-augmented generation.
- Experience with APIs and backend services.
- Familiarity with Docker and cloud deployment.
- Clear written and verbal communication.

## Notes

This is fictional data intended only to demonstrate the expected Markdown job
input.

The `Company:` line in the `## Metadata` block above is load-bearing:
`tools/resume/v86/normalize_cover_letter_identity.py` reads the employer name
off an anchored `Company:` header line. A `## Company` section heading or a
`# Company: X` title line is **not** matched. Keep exactly one such line — two
that disagree cause the pipeline to resolve no company at all rather than guess.
