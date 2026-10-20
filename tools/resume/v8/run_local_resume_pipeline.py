#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def run(cmd, check=True):
    cmd = [str(x) for x in cmd]

    print("+", " ".join(cmd))

    result = subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
    )

    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): "
            + " ".join(cmd)
        )

    return result


def load(path: Path):
    return json.loads(path.read_text())


def write(path: Path, data):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        ) + "\n"
    )


def previous_round(root: Path) -> int:
    p = root / "v8_correction_status.json"

    if not p.exists():
        return 0

    try:
        return int(
            load(p).get(
                "correction_round",
                0,
            )
        )
    except Exception:
        return 0


def correction_request(
    root: Path,
    reasons,
    details,
):
    current_round = previous_round(root)
    requested_round = current_round + 1

    request = {
        "job": root.name,
        "correction_round":
            requested_round,
        "reasons": reasons,
        "details": details,
        "allowed_scope":
            "dynamic_resume_content_only",
    }

    path = root / "correction_request.json"
    write(path, request)

    return path


def process(job_file: Path):
    stem = job_file.stem
    out = ROOT / "output" / stem

    print()
    print("=" * 72)
    print(stem)
    print("=" * 72)

    result = {
        "job": stem,
        "job_file": str(job_file),
        "status": None,
        "correction_request": None,
    }

    strategy = out / "v8_strategy_status.json"
    payload = out / "resume_payload.json"
    requirements = out / "jd_requirements.json"

    required_strategy_files = [
        strategy,
        payload,
        requirements,
    ]

    missing = [
        str(p)
        for p in required_strategy_files
        if not p.exists()
    ]

    if missing:
        result["status"] = (
            "MISSING_STRATEGY_FILES"
        )
        result["missing"] = missing
        return result

    # ------------------------------------------
    # Strict V8 strategy contract validation
    # ------------------------------------------

    contract_report = (
        out / "v8_contract_validation.json"
    )

    run(
        [
            sys.executable,
            "tools/resume/v8/"
            "validate_strategy_contract.py",
            "--job-dir",
            out,
            "--report",
            contract_report,
        ],
        check=False,
    )

    if not contract_report.exists():
        result["status"] = (
            "STRATEGY_CONTRACT_FAILED"
        )
        return result

    contract = load(contract_report)

    if contract.get("status") != "PASS":
        result["status"] = (
            "STRATEGY_CONTRACT_FAILED"
        )
        result["contract_failures"] = (
            contract.get("failures", [])
        )
        return result

    strategy_data = load(strategy)

    if (
        strategy_data.get("status")
        != "STRATEGY_READY"
    ):
        result["status"] = (
            "STRATEGY_NOT_READY"
        )
        return result

    # ------------------------------------------
    # -1. Deterministic selectable-project binding
    # ------------------------------------------

    selection_report = (
        out / "project_selection.json"
    )

    selector_result = run(
        [
            sys.executable,
            "tools/resume/v8/"
            "select_project.py",
            "--requirements",
            requirements,
            "--output",
            selection_report,
        ],
        check=False,
    )

    if (
        selector_result.returncode != 0
        or not selection_report.exists()
    ):
        result["status"] = (
            "PROJECT_SELECTION_FAILED"
        )
        return result

    selection_data = load(
        selection_report
    )

    if selection_data.get("status") != "PASS":
        result["status"] = (
            "PROJECT_SELECTION_FAILED"
        )
        result["project_selection"] = (
            selection_data
        )
        return result

    selection_guard_report = (
        out /
        "v8_project_selection_validation.json"
    )

    guard_result = run(
        [
            sys.executable,
            "tools/resume/v8/"
            "validate_project_selection.py",
            "--job-dir",
            out,
            "--selection",
            selection_report,
            "--report",
            selection_guard_report,
        ],
        check=False,
    )

    if (
        guard_result.returncode != 0
        or not selection_guard_report.exists()
    ):
        result["status"] = (
            "PROJECT_SELECTION_MISMATCH"
        )

        if selection_guard_report.exists():
            guard_data = load(
                selection_guard_report
            )

            result[
                "project_selection_failures"
            ] = guard_data.get(
                "failures",
                [],
            )

        return result

    guard_data = load(
        selection_guard_report
    )

    if guard_data.get("status") != "PASS":
        result["status"] = (
            "PROJECT_SELECTION_MISMATCH"
        )
        result[
            "project_selection_failures"
        ] = guard_data.get(
            "failures",
            [],
        )
        return result

    # ------------------------------------------
    # 0. Normalize/validate structural payload
    # ------------------------------------------

    normalization_report = (
        out / "payload_normalization.json"
    )

    run(
        [
            sys.executable,
            "tools/resume/v8/"
            "normalize_resume_payload.py",
            "--payload",
            payload,
            "--report",
            normalization_report,
        ],
        check=False,
    )

    if not normalization_report.exists():
        result["status"] = (
            "PAYLOAD_NORMALIZATION_FAILED"
        )
        return result

    normalization = load(
        normalization_report
    )

    if normalization.get("status") != "PASS":
        result["status"] = (
            "PAYLOAD_NORMALIZATION_FAILED"
        )
        result["normalization"] = (
            normalization
        )
        return result

    # ------------------------------------------
    # 1. Normal payload-fit check
    # ------------------------------------------

    payload_report = (
        out / "payload_preflight.json"
    )

    run(
        [
            sys.executable,
            "tools/resume/payload_preflight.py",
            "--payload",
            payload,
            "--output",
            payload_report,
        ],
        check=False,
    )

    if not payload_report.exists():
        result["status"] = (
            "PAYLOAD_PREFLIGHT_FAILED"
        )
        return result

    payload_data = load(payload_report)

    if payload_data.get("status") != "PASS":

        round_number = (
            previous_round(out) + 1
        )

        if round_number > 2:
            result["status"] = (
                "CORRECTION_LIMIT_EXCEEDED"
            )
            result["reason"] = (
                "PAYLOAD_PREFLIGHT"
            )
            return result

        req = correction_request(
            out,
            ["PAYLOAD_PREFLIGHT"],
            {
                "payload_preflight":
                    payload_data,
                "instruction":
                    "Bring only dynamic fields "
                    "inside the normal payload "
                    "budgets while preserving "
                    "supported JD concepts and "
                    "approved metrics.",
            },
        )

        result["status"] = (
            "NEEDS_LLM_CORRECTION"
        )
        result["correction_request"] = (
            str(req.relative_to(ROOT))
        )

        return result

    # ------------------------------------------
    # 2. Render resume
    # ------------------------------------------

    markdown = out / "resume_draft.md"

    docx = out / "resume.docx"

    pdf = out / "resume.pdf"

    run(
        [
            sys.executable,
            "tools/resume/render_resume.py",
            "--payload",
            payload,
            "--markdown-output",
            markdown,
            "--output-docx",
            docx,
            "--output-pdf",
            pdf,
        ]
    )

    # ------------------------------------------
    # 3. ATS
    # ------------------------------------------

    ats_report = out / "ats_preflight.json"

    run(
        [
            sys.executable,
            "tools/ats/preflight.py",
            "--requirements",
            requirements,
            "--resume",
            markdown,
            "--evidence-root",
            "master/runtime/ats_evidence",
            "--aliases",
            "config/ats_aliases.json",
            "--output",
            ats_report,
        ],
        check=False,
    )

    if not ats_report.exists():
        result["status"] = "ATS_EXECUTION_FAILED"
        return result

    ats = load(ats_report)

    # ------------------------------------------
    # 4. QA
    # ------------------------------------------

    qa_report = out / "resume_qa.json"

    run(
        [
            sys.executable,
            "tools/resume/qa_resume.py",
            "--pdf",
            pdf,
            "--output",
            qa_report,
        ],
        check=False,
    )

    if not qa_report.exists():
        result["status"] = "QA_EXECUTION_FAILED"
        return result

    qa = load(qa_report)

    correction_reasons = []
    details = {}

    # ------------------------------------------
    # 5. ATS semantic repair request
    # ------------------------------------------

    if not ats.get("gate_pass"):

        supported_missing = (
            ats.get(
                "supported_but_missing",
                [],
            )
        )

        correction_reasons.append(
            "ATS"
        )

        details["ats"] = {
            "supported_but_missing":
                supported_missing,
            "unsupported_required_terms":
                ats.get(
                    "unsupported_required_terms",
                    [],
                ),
            "instruction":
                "Repair supported missing "
                "concepts only. Never add an "
                "unsupported requirement.",
        }

    # ------------------------------------------
    # 6. Dense one-page repair
    # ------------------------------------------

    bottom = qa.get(
        "usable_bottom_whitespace_in"
    )

    if (
        qa.get("page_count") == 1
        and
        bottom is not None
        and
        bottom < 0.25
    ):
        density_report = (
            out / "density_guard.json"
        )

        run(
            [
                sys.executable,
                "tools/resume/density_guard.py",
                "--qa",
                qa_report,
                "--payload",
                payload,
                "--output",
                density_report,
            ],
            check=False,
        )

        density = (
            load(density_report)
            if density_report.exists()
            else {}
        )

        correction_reasons.append(
            "DENSITY"
        )

        details["density"] = {
            "density_guard":
                density,
            "instruction":
                "Shorten only allowed dynamic "
                "content enough to remove the "
                "estimated wrapped line count.",
        }

    # ------------------------------------------
    # 7. Multipage bounded repair
    # ------------------------------------------

    if qa.get("page_count") != 1:

        correction_reasons.append(
            "MULTIPAGE"
        )

        details["multipage"] = {
            "minimal_page_2_spill":
                qa.get(
                    "minimal_page_2_spill",
                    False,
                ),
            "page_2_nonempty_lines":
                qa.get(
                    "page_2_nonempty_lines",
                    0,
                ),
            "page_2_text_chars":
                qa.get(
                    "page_2_text_chars",
                    0,
                ),
            "instruction":
                "Use the approved bounded "
                "minimal-spill correction only "
                "if the deterministic condition "
                "is eligible.",
        }

    # ------------------------------------------
    # 8. Correction required
    # ------------------------------------------

    if correction_reasons:

        round_number = (
            previous_round(out) + 1
        )

        # Bounded architecture:
        # round 1 may solve payload-fit;
        # round 2 may solve post-render ATS/density.
        if round_number > 2:
            result["status"] = (
                "CORRECTION_LIMIT_EXCEEDED"
            )
            return result

        req = correction_request(
            out,
            correction_reasons,
            details,
        )

        result["status"] = (
            "NEEDS_LLM_CORRECTION"
        )

        result["correction_request"] = (
            str(req.relative_to(ROOT))
        )

        return result

    # ------------------------------------------
    # 9. Final deterministic resume gate
    # ------------------------------------------

    success = (
        ats.get("gate_pass") is True
        and
        qa.get("status") == "PASS"
        and
        qa.get("page_count") == 1
        and
        qa.get(
            "density_target_pass"
        ) is True
        and
        bottom is not None
        and
        0.25 <= bottom <= 0.50
        and
        docx.exists()
        and
        pdf.exists()
    )

    if success:
        # ------------------------------------------
        # 10. Deterministic cover-letter rendering
        # ------------------------------------------

        cover_payload = (
            out / "cover_letter_payload.json"
        )

        cover_docx = out / "cover_letter.docx"

        cover_pdf = out / "cover_letter.pdf"

        if not cover_payload.exists():
            result["status"] = (
                "COVER_LETTER_PAYLOAD_MISSING"
            )
            return result

        run(
            [
                sys.executable,
                "tools/resume/v8/"
                "render_cover_letter.py",
                "--payload",
                cover_payload,
                "--output-docx",
                cover_docx,
            ]
        )

        run(
            [
                sys.executable,
                "tools/resume/word_to_pdf.py",
                "--input",
                cover_docx,
                "--output",
                cover_pdf,
            ]
        )

        if not (
            cover_docx.exists()
            and cover_pdf.exists()
        ):
            result["status"] = (
                "COVER_LETTER_RENDER_FAILED"
            )
            return result

        result["status"] = "RESUME_READY"
        result["ats_gate"] = True
        result["page_count"] = 1
        result[
            "usable_bottom_whitespace_in"
        ] = bottom
        result["qa_status"] = "PASS"

        return result

    result["status"] = (
        "LOCAL_QUALITY_GATE_FAILED"
    )

    result["ats_gate"] = (
        ats.get("gate_pass")
    )

    result["qa"] = qa

    return result


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--manifest",
        required=True,
    )

    args = parser.parse_args()

    # Runtime evidence must still match
    # authoritative master sources.
    run(
        [
            sys.executable,
            "tools/resume/v8/"
            "runtime_context_guard.py",
            "check",
        ]
    )

    # Build a clean, V8-only ATS evidence source.
    # This prevents instruction/rule prose in master/
    # from being mistaken for candidate evidence.
    run(
        [
            sys.executable,
            "tools/resume/v8/"
            "build_runtime_ats_evidence.py",
        ]
    )

    manifest = ROOT / args.manifest

    jobs = [
        Path(line.strip())
        for line in manifest.read_text().splitlines()
        if line.strip()
    ]

    results = [
        process(job)
        for job in jobs
    ]

    result_path = (
        ROOT /
        ".work/v8/"
        "local_pipeline_results.json"
    )

    write(result_path, results)

    print()
    print("=== V8 LOCAL RESUME PIPELINE ===")
    print()

    for item in results:
        print(
            f"{item['job']}: "
            f"{item['status']}"
        )

    ready = sum(
        item["status"] == "RESUME_READY"
        for item in results
    )

    corrections = sum(
        item["status"]
        == "NEEDS_LLM_CORRECTION"
        for item in results
    )

    failed = (
        len(results)
        - ready
        - corrections
    )

    print()
    print("Jobs:", len(results))
    print("RESUME_READY:", ready)
    print(
        "NEEDS_LLM_CORRECTION:",
        corrections,
    )
    print("FAILED:", failed)
    print()
    print(
        "Report:",
        result_path.relative_to(ROOT),
    )


if __name__ == "__main__":
    main()
