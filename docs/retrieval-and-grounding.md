# Retrieval and Grounding

## Why this layer exists

A resume-tailoring system becomes unreliable when the language model is allowed to freely infer, embellish, or invent candidate history.

V8.6.8 treats candidate information as evidence.

The model is not asked to create a new candidate profile from scratch. Instead, the pipeline first builds a controlled candidate context, identifies the requirements of the current job, retrieves the most relevant supported evidence, and only then asks the model to produce job-specific content.

The design goal is:

**Same experience, different emphasis.**

A real qualification gap remains a gap.

---

## Is this RAG?

This project uses a structured form of retrieval-augmented generation.

It does **not** rely on a vector database for the core resume workflow.

Instead, retrieval is performed over structured candidate evidence and validated runtime context. The system selects job-relevant facts and provides those facts to the generation stage as grounding context.

Conceptually:

    Job description
          |
          v
    Requirement extraction
          |
          v
    Candidate evidence sources
          |
          v
    Evidence filtering / context slicing
          |
          v
    Job-specific packet
          |
          v
    LLM generation
          |
          v
    Deterministic evidence validation

This is still retrieval-augmented generation in the architectural sense: generation is augmented with retrieved evidence from an external truth store.

The important distinction is that retrieval is structured and rule-aware rather than embedding-first.

---

## Why structured retrieval instead of vector-only retrieval

Candidate evidence is relatively small, high-value, and sensitive to factual mistakes.

For this use case, exact structure is useful because the system needs to know more than whether two pieces of text are semantically similar.

It also needs to know:

- whether a fact is allowed;
- whether a metric is verified;
- whether an item belongs to a specific project or employer;
- whether evidence is current or rejected;
- whether a qualification is merely related or actually supported;
- whether the evidence can be used in the requested resume section; and
- whether a gap must remain explicitly unsupported.

A pure nearest-neighbor retrieval system would not enforce those rules by itself.

---

## Truth hierarchy

The pipeline uses a candidate truth hierarchy rather than treating every file as equally authoritative.

The validated hierarchy is:

1. `rejections.json`
2. `evidence_bank.json`
3. `metrics_registry.json`
4. `candidate_profile.json`
5. `eligibility_facts.json`
6. `candidate_facts.md`

Higher-priority sources constrain lower-priority sources.

This prevents a convenient lower-level summary from overriding a more explicit rejection or verified evidence decision.

---

## Shared candidate context

At the beginning of a batch, V8.6.8 builds a shared candidate context.

This avoids rebuilding the same candidate representation independently for every job.

The shared context contains the stable evidence that multiple jobs may need.

Each job then receives a narrower packet derived from that shared context.

Benefits include:

- less repeated preprocessing;
- smaller repeated prompts;
- lower model-token usage;
- consistent candidate truth across jobs; and
- easier state isolation between jobs.

---

## Job-specific context slicing

The full candidate record is not blindly sent to every model call.

The pipeline performs context slicing so that a job receives the evidence that is relevant to its requirements.

This improves both efficiency and reliability.

Smaller, focused context helps reduce:

- irrelevant evidence competing for attention;
- accidental use of unrelated project details;
- prompt size;
- token cost; and
- opportunities for unsupported synthesis.

The generation model therefore works from a curated evidence surface rather than the entire private candidate archive.

---

## Job packet

Each job is represented by a per-job packet.

The packet acts as the boundary between shared candidate truth and job-specific generation.

Conceptually, it combines:

- the job description;
- normalized job requirements;
- relevant candidate evidence;
- allowed project or experience context;
- pipeline rules; and
- attempt-local state.

This makes each job independently reproducible and prevents one job's local state from silently becoming another job's context.

---

## Evidence ownership

Evidence is not treated as a free-floating bag of keywords.

The pipeline tracks ownership so that a fact associated with one project, role, or experience cannot be silently reassigned to another.

For example, a technology used in one project cannot automatically be presented as professional experience at an unrelated employer.

This is important for ATS tailoring because semantic similarity alone is not sufficient proof of experience.

---

## Supported, missing, and unsupported concepts

The ATS/evidence layer distinguishes between different evidence states.

A useful conceptual model is:

### Supported and present

The qualification is supported by candidate evidence and is already represented appropriately in the resume.

### Supported but missing

The candidate genuinely has the evidence, but the current draft does not surface it strongly enough.

This can be a valid correction target.

### Unsupported

The available candidate evidence does not substantiate the qualification.

This remains a gap.

The pipeline must not turn an unsupported requirement into supported experience simply to increase keyword coverage.

---

## Negation handling

Requirement matching also needs to understand when a term appears only in a negative context.

For example, a source saying that the candidate has **not** used a technology should not be treated as positive evidence merely because the technology name appears in the text.

V8.6.8 includes regression coverage for negated evidence so that keyword presence alone does not create false support.

---

## Retrieval vs generation responsibilities

The architecture intentionally separates the responsibilities of retrieval and generation.

### Retrieval / deterministic layer

Responsible for:

- deciding what evidence exists;
- deciding what evidence is allowed;
- deciding what belongs to which experience;
- classifying evidence support;
- filtering context;
- enforcing rejected facts; and
- identifying truthful gaps.

### LLM layer

Responsible for:

- interpreting the job description;
- choosing emphasis within allowed evidence;
- producing concise job-specific wording; and
- improving presentation without changing the underlying facts.

The model is therefore a semantic writer and reasoner, not the system of record.

---

## Retrieval and the correction loop

The same grounding rules continue to apply during correction.

A correction request does not grant permission to introduce new evidence.

If deterministic validation finds that a supported concept is missing, the correction worker may surface the verified evidence more effectively.

If the concept is unsupported, the correction worker must not manufacture it.

---

## Why this matters for resume generation

Traditional prompt-only resume tailoring often optimizes for superficial keyword overlap.

That creates several failure modes:

- fabricated skills;
- inflated project scope;
- metrics appearing in the wrong experience;
- technologies moved between projects;
- inconsistent claims between jobs; and
- stronger ATS wording at the expense of factual accuracy.

This pipeline reverses that priority.

**Accuracy is the hard constraint. ATS optimization happens inside that constraint.**

---

## Privacy boundary

The actual candidate evidence store is private runtime data.

It lives outside the public Git history under local-only candidate directories.

The public repository contains the retrieval and validation logic, but not the real evidence used to generate personal application materials.

This keeps the architecture inspectable without publishing private career records.

---

## Summary

The retrieval system is deliberately conservative.

It is not designed to retrieve the largest possible amount of semantically related text.

It is designed to retrieve the smallest useful set of **truthful, owned, permitted, job-relevant evidence** needed for generation.

That makes grounding a safety and correctness mechanism, not merely a prompt-enrichment technique.
