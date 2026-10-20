#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any


SUPPORTED_EVIDENCE_EXTENSIONS = {".json", ".md", ".txt"}


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()

    for ch in ("–", "—", "−", "-", "‐"):
        text = text.replace(ch, "-")

    # Treat ordinary hyphenated terms like spaced equivalents.
    text = re.sub(r"(?<=\w)-(?=\w)", " ", text)

    # Normalize slash spacing.
    text = re.sub(r"\s*/\s*", "/", text)

    text = re.sub(r"\s+", " ", text)

    return text.strip()


def phrase_present(text: str, phrase: str) -> bool:
    text_n = normalize(text)
    phrase_n = normalize(phrase)

    if not phrase_n:
        return False

    pattern = r"(?<!\w)" + re.escape(phrase_n) + r"(?!\w)"

    return re.search(
        pattern,
        text_n,
        flags=re.IGNORECASE
    ) is not None


def flatten_json(value: Any, source: str = "$") -> list[dict]:
    records = []

    if isinstance(value, dict):
        for key, child in value.items():
            records.extend(
                flatten_json(
                    child,
                    f"{source}.{key}"
                )
            )

    elif isinstance(value, list):
        for i, child in enumerate(value):
            records.extend(
                flatten_json(
                    child,
                    f"{source}[{i}]"
                )
            )

    elif isinstance(value, (str, int, float, bool)):
        text = str(value).strip()

        if text:
            records.append({
                "source": source,
                "text": text
            })

    return records


def read_evidence_file(path: Path) -> list[dict]:
    suffix = path.suffix.lower()

    if suffix == ".json":
        try:
            data = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )
        except Exception as exc:
            print(
                f"WARNING: Could not parse {path}: {exc}",
                file=sys.stderr
            )
            return []

        rows = flatten_json(data)

        for row in rows:
            row["file"] = str(path)

        return rows

    if suffix in {".md", ".txt"}:
        rows = []

        content = path.read_text(
            encoding="utf-8",
            errors="replace"
        )

        for line_no, line in enumerate(
            content.splitlines(),
            start=1
        ):
            text = line.strip()

            if text:
                rows.append({
                    "file": str(path),
                    "source": f"line:{line_no}",
                    "text": text
                })

        return rows

    return []


def load_evidence(root: Path) -> list[dict]:
    records = []

    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and path.suffix.lower()
            in SUPPORTED_EVIDENCE_EXTENSIONS
        ):
            records.extend(
                read_evidence_file(path)
            )

    return records


def load_aliases(path: Path | None) -> dict[str, list[str]]:
    if not path or not path.exists():
        return {}

    raw = json.loads(
        path.read_text(
            encoding="utf-8"
        )
    )

    result = {}

    for key, values in raw.items():
        result[normalize(key)] = [
            str(v)
            for v in values
            if str(v).strip()
        ]

    return result


def concept_forms(
    concept: dict,
    aliases: dict[str, list[str]]
) -> list[str]:

    forms = []

    for field in (
        "term",
        "exact",
        "canonical"
    ):
        value = concept.get(field)

        if isinstance(value, str) and value.strip():
            forms.append(value.strip())

    for alias in concept.get("aliases", []) or []:
        if isinstance(alias, str) and alias.strip():
            forms.append(alias.strip())

    lookup_keys = {
        normalize(form)
        for form in forms
    }

    for key in lookup_keys:
        forms.extend(
            aliases.get(key, [])
        )

    seen = set()
    unique = []

    for form in forms:
        normalized = normalize(form)

        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(form)

    return unique


def find_evidence(
    records: list[dict],
    forms: list[str],
    max_hits: int = 5
) -> list[dict]:

    hits = []

    for record in records:
        if any(
            phrase_present(
                record["text"],
                form
            )
            for form in forms
        ):
            hits.append({
                "file": record["file"],
                "source": record["source"],
                "text": record["text"][:350]
            })

            if len(hits) >= max_hits:
                break

    return hits


def priority_value(concept: dict) -> int:
    category = str(
        concept.get("category", "")
    ).upper()

    priority = str(
        concept.get("priority", "")
    ).lower()

    if (
        concept.get("required")
        and category == "REQUIRED_HARD_SKILL"
    ):
        return 5

    if category == "CORE_RESPONSIBILITY":
        return 4

    if priority in {
        "critical",
        "very_high",
        "very high"
    }:
        return 5

    if priority == "high":
        return 4

    if category == "PREFERRED_HARD_SKILL":
        return 3

    if category == "SOFT_SKILL":
        return 2

    return 1


def percentage(num: int, den: int) -> float:
    if den == 0:
        return 100.0

    return round(
        (num / den) * 100,
        1
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Deterministic ATS evidence and "
            "resume-draft coverage validator."
        )
    )

    parser.add_argument(
        "--requirements",
        required=True
    )

    parser.add_argument(
        "--resume",
        required=True
    )

    parser.add_argument(
        "--evidence-root",
        default="master"
    )

    parser.add_argument(
        "--aliases",
        default=None
    )

    parser.add_argument(
        "--output",
        required=True
    )

    args = parser.parse_args()

    requirements_path = Path(
        args.requirements
    )

    resume_path = Path(
        args.resume
    )

    evidence_root = Path(
        args.evidence_root
    )

    alias_path = (
        Path(args.aliases)
        if args.aliases
        else None
    )

    output_path = Path(
        args.output
    )

    if not requirements_path.exists():
        raise SystemExit(
            f"ERROR: Missing requirements file: "
            f"{requirements_path}"
        )

    if not resume_path.exists():
        raise SystemExit(
            f"ERROR: Missing resume draft: "
            f"{resume_path}"
        )

    if not evidence_root.exists():
        raise SystemExit(
            f"ERROR: Missing evidence directory: "
            f"{evidence_root}"
        )

    requirements = json.loads(
        requirements_path.read_text(
            encoding="utf-8"
        )
    )

    concepts = requirements.get(
        "concepts"
    )

    if not isinstance(concepts, list):
        raise SystemExit(
            "ERROR: jd_requirements.json "
            "must contain a concepts list."
        )

    resume_text = resume_path.read_text(
        encoding="utf-8",
        errors="replace"
    )

    evidence_records = load_evidence(
        evidence_root
    )

    aliases = load_aliases(
        alias_path
    )

    results = []

    supported_present = []
    supported_missing = []
    unsupported = []

    for concept in concepts:

        term = (
            concept.get("term")
            or concept.get("exact")
            or concept.get("canonical")
            or ""
        )

        forms = concept_forms(
            concept,
            aliases
        )

        evidence_hits = find_evidence(
            evidence_records,
            forms
        )

        supported = bool(
            evidence_hits
        )

        resume_present = any(
            phrase_present(
                resume_text,
                form
            )
            for form in forms
        )

        if supported and resume_present:
            status = "SUPPORTED_AND_PRESENT"
            supported_present.append(term)

        elif supported and not resume_present:
            status = "SUPPORTED_BUT_MISSING"
            supported_missing.append(term)

        else:
            status = "UNSUPPORTED"
            unsupported.append(term)

        results.append({
            "term": term,
            "canonical":
                concept.get("canonical"),

            "category":
                concept.get("category"),

            "required":
                bool(concept.get("required")),

            "priority":
                concept.get("priority"),

            "importance":
                priority_value(concept),

            "forms_checked":
                forms,

            "supported_by_master_evidence":
                supported,

            "resume_present":
                resume_present,

            "status":
                status,

            "evidence_hits":
                evidence_hits
        })

    supported_required = [
        r
        for r in results
        if (
            r["required"]
            and r[
                "supported_by_master_evidence"
            ]
        )
    ]

    supported_required_present = [
        r
        for r in supported_required
        if r["resume_present"]
    ]

    supported_high = [
        r
        for r in results
        if (
            r[
                "supported_by_master_evidence"
            ]
            and r["importance"] >= 4
        )
    ]

    supported_high_present = [
        r
        for r in supported_high
        if r["resume_present"]
    ]

    supported_responsibilities = [
        r
        for r in results
        if (
            r[
                "supported_by_master_evidence"
            ]
            and str(
                r["category"]
            ).upper()
            == "CORE_RESPONSIBILITY"
        )
    ]

    supported_responsibilities_present = [
        r
        for r in supported_responsibilities
        if r["resume_present"]
    ]

    missing_required = [
        r["term"]
        for r in supported_required
        if not r["resume_present"]
    ]

    unsupported_required = [
        r["term"]
        for r in results
        if (
            r["required"]
            and not r[
                "supported_by_master_evidence"
            ]
        )
    ]

    required_coverage = percentage(
        len(
            supported_required_present
        ),
        len(
            supported_required
        )
    )

    high_coverage = percentage(
        len(
            supported_high_present
        ),
        len(
            supported_high
        )
    )

    responsibility_coverage = percentage(
        len(
            supported_responsibilities_present
        ),
        len(
            supported_responsibilities
        )
    )

    gate_pass = (
        not missing_required
        and high_coverage >= 90.0
    )

    report = {
        "job_key":
            requirements.get("job_key"),

        "gate_pass":
            gate_pass,

        "supported_required_skill_coverage_percent":
            required_coverage,

        "high_priority_concept_coverage_percent":
            high_coverage,

        "core_responsibility_coverage_percent":
            responsibility_coverage,

        "supported_and_present":
            supported_present,

        "supported_but_missing":
            supported_missing,

        "unsupported":
            unsupported,

        "missing_supported_required_terms":
            missing_required,

        "unsupported_required_terms":
            unsupported_required,

        "concepts":
            results
    }

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    print()
    print("ATS PREFLIGHT")
    print("=" * 60)

    print(
        "Supported required coverage:",
        f"{required_coverage:.1f}%"
    )

    print(
        "High-priority coverage:",
        f"{high_coverage:.1f}%"
    )

    print(
        "Responsibility coverage:",
        f"{responsibility_coverage:.1f}%"
    )

    print()

    if supported_missing:
        print("SUPPORTED BUT MISSING:")

        for item in supported_missing:
            print(f"  - {item}")
    else:
        print(
            "SUPPORTED BUT MISSING: none"
        )

    print()

    if unsupported_required:
        print("UNSUPPORTED REQUIRED:")

        for item in unsupported_required:
            print(f"  - {item}")
    else:
        print(
            "UNSUPPORTED REQUIRED: none"
        )

    print()

    print(
        "GATE:",
        "PASS"
        if gate_pass
        else "FAIL"
    )

    print(
        f"Report: {output_path}"
    )

    if not gate_pass:
        sys.exit(2)


if __name__ == "__main__":
    main()
