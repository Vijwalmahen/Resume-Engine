# retrieval-engineering-intern synthetic fixture (V8.6.7 bridge regression)

Small, fully synthetic fixture built from the fictional "Candidate Example"
dataset (see `master/runtime/runtime_resume_context.json`). It exists only
to exercise `test_v86_7_ats_bridge_reciprocal_rank_fusion.py`'s
`TestJob09RealArtifactBridgeRegression` suite offline, with zero real
Claude/Apify calls.

## Why this exists

The directory name and the `retrieval-engineering-intern` job key are
historical labels carried over from the original scale-10 batch job whose
`gate_pass: false` / `supported_but_missing_required: ["Reciprocal rank
fusion"]` ATS gap the V8.6.7 "reciprocal rank fusion" concept bridge
(`config/v86/ats_concept_bridges.json`) was written to fix. This fixture
does **not** contain any of that original real batch data -- every field
below is invented or drawn from the fictional Candidate Example dataset.

The property under test: the candidate's own truthful project bullets use
"RRF" and "RRF fusion" verbatim (a real, common shorthand for "reciprocal
rank fusion" in hybrid-retrieval work), but the JD concept is phrased as the
full term "Reciprocal rank fusion". Before the bridge, that's a real
ATS-terminology mismatch (`SUPPORTED_BUT_MISSING`, gate fails); the bridge
(`"reciprocal rank fusion": ["RRF", "RRF fusion"]`) closes it deterministically
with zero model calls.

## What's here

- `jd_requirements.json` -- one required "Reciprocal rank fusion" concept
  plus a few optional filler concepts (Python/FastAPI/Redis) that are
  truthfully satisfied by the fictional dataset on both sides of the bridge
  change, so `test_other_concepts_unchanged` has something to hold constant.
- `resume_draft.md` -- a rendered-resume-shaped markdown file whose "Project
  Atlas" and "Project Beacon" bullets each use "RRF"/"RRF fusion" verbatim
  (never the literal phrase "Reciprocal rank fusion").
- `shared_ats_evidence/verified_resume_evidence.json` -- synthetic verified
  evidence in the real `find_evidence`-consumable shape
  (`experience`/`projects` lists with `technologies`/`canonical_bullets`),
  whose Project Atlas and Project Beacon entries both list "Reciprocal rank
  fusion" in `technologies` -- the literal evidence the bridge maps "RRF"/
  "RRF fusion" onto.
- `correction_request.json` -- a small synthetic correction request (reason:
  `DENSITY` only, never `ATS`) proving the bridge alone, not a correction
  round, is what resolves this concept.
