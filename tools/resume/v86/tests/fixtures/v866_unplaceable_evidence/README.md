# V8.6.6 unplaceable-evidence fixture

Immutable, hand-built reconstruction of the ORIGINAL (pre-"hybrid search"-
bridge) `live13-11-recommendation` ATS scenario that
`tools/resume/v86/evidence_ownership.py` /
`deterministic_pipeline.split_missing_required_by_placeability` were written
to fix (see README.md "V8.6.6 ATS/renderability reliability patch").

## Why this exists

`test_v86_6_ats_bridge_and_unplaceable_evidence.py` originally had two tests
that read the real, mutable `output/live13-11-recommendation/
ats_preflight.json` to get the "Hybrid search" concept's pre-fix
`evidence_hits`. That file is a shared, job-key-scoped artifact that gets
overwritten every time ANY batch re-runs that job_key -- including with the
V8.6.6 bridge already active, at which point "Hybrid search" is
`SUPPORTED_AND_PRESENT` (not `SUPPORTED_BUT_MISSING`) and
`missing_supported_required_terms` no longer contains it at all. A later,
unrelated batch run did exactly that, which made
`test_split_matches_classify_missing_required_per_term`
non-deterministic/failing depending on whichever batch last touched that
job_key -- fixture drift, not a bridge regression.

## What's here

- `evidence.json` -- a minimal, self-contained master-evidence fixture
  (fictional "Candidate Example" dataset) with exactly two protected
  projects (Project Atlas, Project Beacon), matching the identity fields
  (`name`) that `evidence_ownership.resolve_owner` reads. Neither is the
  tailorable experience entry nor any selectable-project candidate, so both
  are non-editable owners.
- `ats_preflight_hybrid_search_unplaceable.json` -- a minimal
  `ats_preflight.json`-shaped document (only the fields
  `split_missing_required_by_placeability`/`classify_missing_required`
  actually read: `missing_supported_required_terms`, and one `concepts[]`
  entry for "Hybrid search" with a single `evidence_hits[]` entry). This
  reproduces the documented pre-fix scenario: the concept's ONLY evidence
  hit resolves to the protected "Project Beacon" project's
  `alternate_facts_for_dynamic_tailoring` bucket -- never a tailorable
  experience entry, never the bound selectable project -- so it must
  classify as `unplaceable`. The `evidence_hits[0].file` field is the
  literal placeholder string `"{EVIDENCE_FILE}"`, substituted at test time
  (see `_load_v866_unplaceable_fixture()` in the test file) with this
  directory's own `evidence.json` absolute path -- keeping the fixture
  itself host-independent and diff-friendly.

This is a deliberate, narrow reconstruction of the documented scenario, not
a byte-for-byte copy of any real batch artifact.
