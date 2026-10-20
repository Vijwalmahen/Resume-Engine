#!/usr/bin/env python3
"""
Deterministic selection of the ONE dynamically-swappable project slot.

The candidate pool is never hardcoded here: it is derived, at runtime,
from every entry in master/runtime/runtime_resume_context.json's
`projects` list whose `project_status` is `"selectable"` (as opposed to
`"required"`, which is always rendered and never chosen between). This
file only ever reads project identity (name/id/evidence markers) from
that candidate-supplied data plus config/v8/project_selection_policy.json's
domain concept bridges -- it has no knowledge of any specific candidate's
project names.

Ranks each selectable project by how many INCREMENTAL (not already
guaranteed by fixed/protected content) REQUIRED JD concepts it truthfully
supports, then binds the model to the winner whenever that coverage is
non-zero. See docs/retrieval-and-grounding.md for the full selection
contract this implements.
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]

DEFAULT_RUNTIME = ROOT / "master" / "runtime" / "runtime_resume_context.json"
DEFAULT_EVIDENCE = ROOT / "master" / "evidence_bank.json"
DEFAULT_ALIASES = ROOT / "config" / "ats_aliases.json"
DEFAULT_POLICY = ROOT / "config" / "v8" / "project_selection_policy.json"


QUESTIONABLE_MARKERS = (
    "needs confirmation",
    "to be confirmed",
    "whether this exists",
    "whether the final implementation",
    "planned architecture",
    "not necessarily implemented",
    "prior exposure",
)

QUESTIONABLE_SOURCES = (
    "master_resume.pdf",
)


PRIORITY_WEIGHT_REQUIRED = {
    "critical": 100,
    "high": 70,
    "medium": 40,
    "low": 20,
}

PRIORITY_WEIGHT_OPTIONAL = {
    "critical": 15,
    "high": 12,
    "medium": 7,
    "low": 3,
}


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value)).lower()

    for ch in ("–", "—", "−", "‐"):
        text = text.replace(ch, "-")

    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def phrase_present(text: str, phrase: str) -> bool:
    text_n = normalize(text)
    phrase_n = normalize(phrase)

    if not phrase_n:
        return False

    pattern = r"(?<!\w)" + re.escape(phrase_n) + r"(?!\w)"

    return bool(re.search(pattern, text_n))


def load_aliases(path: Path) -> dict:
    if not path.exists():
        return {}

    raw = json.loads(path.read_text())
    result = {}

    for key, values in raw.items():
        if not isinstance(values, list):
            continue

        result[normalize(key)] = [str(v) for v in values if str(v).strip()]

    return result


def concept_forms(
    concept: dict,
    aliases: dict,
    bridges: dict,
    include_bridges: bool = True,
) -> list:
    forms = []

    term = concept.get("term")
    if isinstance(term, str) and term.strip():
        forms.append(term.strip())

    for alias in concept.get("aliases", []) or []:
        if isinstance(alias, str) and alias.strip():
            forms.append(alias.strip())

    expanded = list(forms)

    for form in forms:
        expanded.extend(aliases.get(normalize(form), []))

    # Controlled project-evidence bridges are ONLY for deciding which
    # project can truthfully support a JD concept. They are not
    # ATS-visible aliases.
    if include_bridges:
        for form in list(expanded):
            expanded.extend(bridges.get(normalize(form), []))

    seen = set()
    unique = []

    for form in expanded:
        key = normalize(form)
        if key and key not in seen:
            seen.add(key)
            unique.append(form)

    return unique


def safe_evidence_record(item: dict) -> bool:
    if item.get("verified") is not True:
        return False

    if item.get("category") not in {"project", "research"}:
        return False

    source = str(item.get("source", "")).lower()
    fact = str(item.get("fact", "")).lower()

    if any(marker in source for marker in QUESTIONABLE_SOURCES):
        return False

    if any(marker in fact for marker in QUESTIONABLE_MARKERS):
        return False

    return True


def record_text(item: dict) -> str:
    pieces = [str(item.get("fact", ""))]
    pieces.extend(str(x) for x in item.get("skills", []) or [])
    pieces.extend(str(x) for x in item.get("technologies", []) or [])
    return "\n".join(pieces)


def runtime_project_text(project: dict) -> str:
    pieces = [str(project.get("name", ""))]
    pieces.extend(str(x) for x in project.get("technologies", []) or [])
    pieces.extend(str(x) for x in project.get("canonical_bullets", []) or [])
    pieces.extend(
        str(x) for x in project.get("alternate_bullets_for_dynamic_tailoring", []) or []
    )
    return "\n".join(pieces)


def fixed_baseline_text(runtime: dict) -> str:
    """Build only text that is guaranteed to appear in every rendered
    resume before any dynamic (tailorable/selected) content is chosen.

    Deliberately conservative:
    - fixed education
    - every protected experience entry's canonical content
    - static resume skills
    - every required (protected) project's canonical content
    - NO tailorable-experience wording
    - NO selectable-project wording
    """

    pieces = []

    for item in runtime.get("education", []):
        if not isinstance(item, dict):
            continue

        for key in ("institution", "degree", "location", "dates", "gpa"):
            value = item.get(key)
            if isinstance(value, str):
                pieces.append(value)

        for value in item.get("coursework", []) or []:
            pieces.append(str(value))

    for item in runtime.get("experience", []):
        if not isinstance(item, dict):
            continue

        canonical = item.get("canonical_bullets")
        if not canonical:
            # Excludes tailorable-experience content.
            continue

        for key in ("employer", "title", "resume_display_location", "location", "dates"):
            value = item.get(key)
            if isinstance(value, str):
                pieces.append(value)

        pieces.extend(str(x) for x in canonical)

    static_skills = (runtime.get("skills", {}) or {}).get("static_resume_lines", {})

    if isinstance(static_skills, dict):
        for label, value in static_skills.items():
            pieces.append(str(label))
            pieces.append(str(value))
    elif isinstance(static_skills, list):
        pieces.extend(str(x) for x in static_skills)

    for project in runtime.get("projects", []):
        if not isinstance(project, dict):
            continue

        canonical = project.get("canonical_bullets")
        if not canonical:
            # Selectable projects have no canonical_bullets in this
            # runtime representation.
            continue

        name = project.get("name")
        if isinstance(name, str):
            pieces.append(name)

        pieces.extend(str(x) for x in canonical)

    return "\n".join(pieces)


def priority_weight(concept: dict) -> int:
    priority = str(concept.get("priority", "low")).lower()

    if concept.get("required") is True:
        return PRIORITY_WEIGHT_REQUIRED.get(priority, 20)

    if str(concept.get("category", "")).upper() == "CORE_RESPONSIBILITY":
        return max(10, PRIORITY_WEIGHT_OPTIONAL.get(priority, 3))

    return PRIORITY_WEIGHT_OPTIONAL.get(priority, 3)


def selectable_project_pool(runtime: dict) -> list:
    """Every candidate-supplied project with project_status == "selectable",
    in the order the candidate listed them (used as the deterministic tie
    break unless a project explicitly overrides tie_break_order)."""
    pool = []

    for index, project in enumerate(runtime.get("projects", [])):
        if not isinstance(project, dict):
            continue
        if project.get("project_status") != "selectable":
            continue

        pool.append({
            "key": project.get("id") or project.get("name"),
            "runtime_name": project.get("name"),
            "evidence_entities": project.get("evidence_entities") or [project.get("name")],
            "title_markers": project.get("title_markers") or [normalize(project.get("name", ""))],
            "tie_break_order": project.get("tie_break_order", index + 1),
        })

    return pool


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--requirements", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--runtime-context", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--evidence-bank", default=str(DEFAULT_EVIDENCE))
    parser.add_argument("--aliases", default=str(DEFAULT_ALIASES))
    parser.add_argument("--policy", default=str(DEFAULT_POLICY))

    args = parser.parse_args()

    requirements = json.loads(Path(args.requirements).read_text())
    runtime = json.loads(Path(args.runtime_context).read_text())
    evidence = json.loads(Path(args.evidence_bank).read_text())
    policy = json.loads(Path(args.policy).read_text())
    aliases = load_aliases(Path(args.aliases))

    bridges = {
        normalize(k): list(v) for k, v in policy.get("concept_bridges", {}).items()
    }

    runtime_by_name = {
        project.get("name"): project
        for project in runtime.get("projects", [])
        if project.get("name")
    }

    safe_records = [
        item
        for item in evidence.get("items", [])
        if isinstance(item, dict) and safe_evidence_record(item)
    ]

    concepts = requirements.get("concepts", [])
    baseline_text = fixed_baseline_text(runtime)
    baseline_covered = {}

    for concept_index, concept in enumerate(concepts):
        # Fixed content counts as already covered only when an actual
        # ATS-visible term/alias is present. Semantic project-selection
        # bridges do not count.
        forms = concept_forms(concept, aliases, bridges, include_bridges=False)

        matched_form = next(
            (form for form in forms if phrase_present(baseline_text, form)), None
        )

        if matched_form is not None:
            baseline_covered[concept_index] = {
                "term": concept.get("term"),
                "matched_form": matched_form,
                "required": bool(concept.get("required")),
                "priority": concept.get("priority"),
            }

    rankings = []

    for project_policy in selectable_project_pool(runtime):
        runtime_name = project_policy["runtime_name"]
        runtime_project = runtime_by_name.get(runtime_name)

        if runtime_project is None:
            raise SystemExit("Runtime project missing: " + str(runtime_name))

        entities = {normalize(x) for x in project_policy.get("evidence_entities", [])}

        project_records = [
            item
            for item in safe_records
            if normalize(item.get("entity", "")) in entities
        ]

        text_parts = [runtime_project_text(runtime_project)]
        text_parts.extend(record_text(item) for item in project_records)
        project_text = "\n".join(text_parts)

        score = 0
        required_score = 0
        required_matches = []
        critical_high_required = []
        optional_matches = []

        for concept_index, concept in enumerate(concepts):
            # Project selection should score only incremental coverage.
            # Do not reward a project for concepts already guaranteed by
            # fixed resume content.
            if concept_index in baseline_covered:
                continue

            forms = concept_forms(concept, aliases, bridges)

            matched_form = next(
                (form for form in forms if phrase_present(project_text, form)), None
            )

            if matched_form is None:
                continue

            weight = priority_weight(concept)
            score += weight

            match = {
                "term": concept.get("term"),
                "matched_form": matched_form,
                "required": bool(concept.get("required")),
                "priority": concept.get("priority"),
                "weight": weight,
            }

            if concept.get("required") is True:
                required_score += weight
                required_matches.append(match)

                if str(concept.get("priority", "")).lower() in {"critical", "high"}:
                    critical_high_required.append(match)
            else:
                optional_matches.append(match)

        rankings.append({
            "project_key": project_policy["key"],
            "runtime_name": runtime_name,
            "score": score,
            "required_score": required_score,
            "required_match_count": len(required_matches),
            "critical_high_required_match_count": len(critical_high_required),
            "total_match_count": len(required_matches) + len(optional_matches),
            "required_matches": required_matches,
            "optional_matches": optional_matches,
            "safe_evidence_record_count": len(project_records),
            "tie_break_order": project_policy["tie_break_order"],
        })

    if not rankings:
        raise SystemExit(
            "No selectable projects found. At least one project entry in "
            "master/runtime/runtime_resume_context.json must have "
            "project_status == \"selectable\"."
        )

    # Ranking contract:
    # 1. number of incremental supported REQUIRED concepts
    # 2. critical/high REQUIRED coverage
    # 3. weighted relevance / responsibility coverage
    # 4. remaining matched concepts
    # 5. deterministic tie break
    rankings.sort(
        key=lambda row: (
            -row["required_match_count"],
            -row["critical_high_required_match_count"],
            -row["required_score"],
            -row["score"],
            -row["total_match_count"],
            row["tie_break_order"],
        )
    )

    winner = rankings[0]

    # Bind the model only when selection is necessary to expose at least
    # one incremental REQUIRED concept.
    #
    # If no candidate project covers an unresolved required concept,
    # Python should not force an arbitrary tie-break project. In that
    # case the semantic worker may choose among the selectable projects
    # using semantic relevance, while the deterministic ranking remains a
    # recommendation.
    binding_required = winner["required_match_count"] > 0

    selection_mode = "BINDING_REQUIRED_COVERAGE" if binding_required else "MODEL_CHOICE"

    report = {
        "status": "PASS",
        "selection_policy": "HYBRID_REQUIRED_COVERAGE",
        "selection_mode": selection_mode,
        "job_key": requirements.get("job_key"),

        # These fields are populated only when the selection must be binding.
        "selected_project_key": winner["project_key"] if binding_required else None,
        "selected_runtime_name": winner["runtime_name"] if binding_required else None,

        # Recommendation is always available, including MODEL_CHOICE jobs.
        "recommended_project_key": winner["project_key"],
        "recommended_runtime_name": winner["runtime_name"],

        "allowed_project_keys": [row["project_key"] for row in rankings],
        "allowed_runtime_names": [row["runtime_name"] for row in rankings],

        "selected_required_score": winner["required_score"],
        "selected_required_matches": winner["required_matches"],
        "fixed_baseline_covered": list(baseline_covered.values()),
        "fixed_baseline_required_covered": [
            x for x in baseline_covered.values() if x["required"]
        ],
        "rankings": rankings,
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    print("SELECTION MODE:", selection_mode)

    if binding_required:
        print("SELECTED:", winner["project_key"], "=>", winner["runtime_name"])
    else:
        print("RECOMMENDED:", winner["project_key"], "=>", winner["runtime_name"])

    print(
        "Fixed required concepts already covered:",
        ", ".join(str(x["term"]) for x in baseline_covered.values() if x["required"]) or "none",
    )

    print("Required score:", winner["required_score"])

    print(
        "Required matches:",
        ", ".join(str(x["term"]) for x in winner["required_matches"]) or "none",
    )

    print("\nRANKING:")

    for i, row in enumerate(rankings, start=1):
        print(
            f"{i}. {row['project_key']} "
            f"required={row['required_score']} "
            f"total={row['score']} "
            f"required_matches={row['required_match_count']}"
        )


if __name__ == "__main__":
    main()
