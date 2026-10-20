"""
Candidate-agnostic pipeline tests.

These tests use ONLY the fictional example data already committed under
master/examples/ (never real/private candidate files, which may not even
exist on a fresh clone) to verify the pipeline's core invariants are
genuinely data-driven: evidence ownership, project selection, and
template rendering never depend on any specific candidate's identity.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "master" / "examples"


def run(cmd, **kwargs):
    return subprocess.run(
        [str(x) for x in cmd], cwd=ROOT, capture_output=True, text=True, **kwargs
    )


class TestGenericEvidenceOwnership(unittest.TestCase):
    """evidence_ownership.py must decide editability purely from
    `tailoring_mode` / bound-project-id, never from an employer/project
    name allowlist."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / "tools" / "resume" / "v86"))
        import evidence_ownership as eo
        self.eo = eo

        self.tmp = tempfile.TemporaryDirectory()
        self.evidence_path = Path(self.tmp.name) / "evidence.json"
        self.evidence_path.write_text(json.dumps({
            "experience": [
                {"id": "experience_1", "tailoring_mode": "protected", "canonical_bullets": ["x"]},
                {"id": "experience_2", "tailoring_mode": "tailorable", "alternate_bullets_for_dynamic_tailoring": ["y"]},
            ],
            "projects": [
                {"id": "project_1", "tailoring_mode": "protected", "canonical_bullets": ["z"]},
                {"id": "project_selectable_1", "tailoring_mode": "tailorable", "alternate_bullets_for_dynamic_tailoring": ["w"]},
            ],
        }))

    def tearDown(self):
        self.tmp.cleanup()

    def _hit(self, source):
        return {"file": str(self.evidence_path), "source": source}

    def test_protected_experience_is_never_editable(self):
        entry = self.eo.resolve_owner_entry(self._hit("$.experience[0].canonical_bullets[0]"))
        self.assertEqual(entry["owner_id"], "experience_1")
        self.assertFalse(self.eo.is_editable_owner(entry))

    def test_tailorable_experience_is_editable(self):
        entry = self.eo.resolve_owner_entry(self._hit("$.experience[1].alternate_bullets_for_dynamic_tailoring[0]"))
        self.assertEqual(entry["owner_id"], "experience_2")
        self.assertTrue(self.eo.is_editable_owner(entry))

    def test_only_the_bound_project_is_editable(self):
        unbound = self.eo.resolve_owner_entry(self._hit("$.projects[1].alternate_bullets_for_dynamic_tailoring[0]"))
        self.assertFalse(self.eo.is_editable_owner(unbound, bound_project_id="some_other_project"))
        self.assertTrue(self.eo.is_editable_owner(unbound, bound_project_id="project_selectable_1"))

    def test_required_project_is_never_editable_even_if_bound(self):
        required = self.eo.resolve_owner_entry(self._hit("$.projects[0].canonical_bullets[0]"))
        self.assertFalse(self.eo.is_editable_owner(required, bound_project_id="project_1"))

    def test_classify_missing_required_placeable_vs_unplaceable(self):
        placeable_hit = self._hit("$.experience[1].alternate_bullets_for_dynamic_tailoring[0]")
        unplaceable_hit = self._hit("$.experience[0].canonical_bullets[0]")
        self.assertEqual(self.eo.classify_missing_required([placeable_hit]), "placeable")
        self.assertEqual(self.eo.classify_missing_required([unplaceable_hit]), "unplaceable")
        self.assertEqual(self.eo.classify_missing_required([]), "unknown")


class TestGenericProjectSelection(unittest.TestCase):
    """The selectable-project pool must come entirely from
    project_status == "selectable" entries in candidate data -- never a
    hardcoded project-name list in config or code."""

    def test_selectable_pool_has_no_hardcoded_identity(self):
        # Generic property check only (see docs/security-and-privacy.md):
        # the selection policy config must never itself enumerate a
        # project-name allowlist -- the pool must come from candidate data.
        policy = json.loads((ROOT / "config" / "v8" / "project_selection_policy.json").read_text())
        self.assertNotIn("projects", policy, "project pool must not be listed in policy config")

    def test_selection_picks_the_project_with_incremental_required_evidence(self):
        runtime_context = json.loads((EXAMPLES / "runtime_resume_context.example.json").read_text())
        evidence_bank = json.loads((EXAMPLES / "evidence_bank.example.json").read_text())

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            runtime_path = tmp_path / "runtime_resume_context.json"
            evidence_path = tmp_path / "evidence_bank.json"
            requirements_path = tmp_path / "jd_requirements.json"
            output_path = tmp_path / "project_selection.json"

            runtime_path.write_text(json.dumps(runtime_context))
            evidence_path.write_text(json.dumps(evidence_bank))
            requirements_path.write_text(json.dumps({
                "job_key": "test-job",
                "concepts": [
                    {"term": "Python", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "critical"},
                    {"term": "Hybrid Search", "category": "REQUIRED_HARD_SKILL", "required": True, "priority": "high"},
                ],
            }))

            result = run([
                sys.executable, "tools/resume/v8/select_project.py",
                "--requirements", requirements_path,
                "--output", output_path,
                "--runtime-context", runtime_path,
                "--evidence-bank", evidence_path,
                "--aliases", ROOT / "config" / "ats_aliases.json",
                "--policy", ROOT / "config" / "v8" / "project_selection_policy.json",
            ])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            selection = json.loads(output_path.read_text())
            self.assertEqual(selection["selection_mode"], "BINDING_REQUIRED_COVERAGE")
            # Project Meridian is the only selectable project with
            # "hybrid search" evidence in the example evidence bank.
            self.assertEqual(selection["selected_project_key"], "project_selectable_1")


class TestGenericTemplateRendering(unittest.TestCase):
    """The resume renderer must apply a payload purely by owner_id, using
    only the layout config -- never a hardcoded paragraph-content
    assumption about a specific candidate."""

    def _build_template(self, layout: dict, path: Path) -> None:
        from docx import Document

        doc = Document()
        max_index = layout["min_paragraph_count"] - 1
        lines = ["" for _ in range(max_index + 1)]
        for name, anchor in layout["section_anchors"].items():
            lines[anchor["index"]] = anchor["text"]
        for block in layout["experience_blocks"] + layout["project_blocks"]:
            lines[block["header_index"]] = f"HEADER {block['owner_id']}"
            for i in block["bullet_indices"]:
                lines[i] = f"BULLET {block['owner_id']} {i}"
        for line in lines:
            doc.add_paragraph(line)
        doc.save(str(path))

    def test_render_resume_is_layout_driven(self):
        layout = json.loads((ROOT / "reference" / "resume_template_layout.example.json").read_text())

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            template_path = tmp_path / "template.docx"
            self._build_template(layout, template_path)

            payload = {
                "experience_bullet_orders": {"experience_1": [0, 1, 2, 3]},
                "experience_alternate_bullets": {"experience_2": ["Alt bullet one.", "Alt bullet two."]},
                "project_bullet_orders": {"project_1": [0, 1, 2], "project_2": [0, 1, 2]},
                "selected_project": {
                    "owner_id": "project_selectable_1",
                    "title": "Some Selected Project",
                    "technology_line": "Python, FastAPI",
                    "bullets": ["Selected bullet one.", "Selected bullet two."],
                },
            }
            payload_path = tmp_path / "resume_payload.json"
            payload_path.write_text(json.dumps(payload))

            markdown_path = tmp_path / "resume_draft.md"
            docx_path = tmp_path / "resume.docx"

            result = run([
                sys.executable, "tools/resume/render_resume.py",
                "--payload", payload_path,
                "--template", template_path,
                "--layout", ROOT / "reference" / "resume_template_layout.example.json",
                "--markdown-output", markdown_path,
                "--output-docx", docx_path,
            ])
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            text = markdown_path.read_text()
            self.assertIn("Alt bullet one.", text)
            self.assertIn("Selected bullet one.", text)
            self.assertIn("Some Selected Project", text)
            # Protected bullets are untouched pass-through content.
            self.assertIn("BULLET experience_1 11", text)


class TestGenericOutputArtifactNaming(unittest.TestCase):
    """Output artifact filenames must never embed a candidate's name."""

    def test_no_personalized_output_filenames(self):
        checked = [
            ROOT / "tools/resume/v8/run_local_resume_pipeline.py",
            ROOT / "tools/resume/v86/deterministic_pipeline.py",
            ROOT / "tools/resume/v86/finalize_local.py",
            ROOT / "tools/resume/v86/benchmark_report.py",
            ROOT / "tools/resume/v86/run_batch.py",
        ]
        for path in checked:
            text = path.read_text()
            self.assertRegex(
                text,
                r"resume\.(docx|pdf)",
                f"{path} should use the generic resume.docx/resume.pdf filename",
            )
            self.assertNotRegex(
                text,
                r"[A-Z][a-z]+_[A-Z][a-z]+_Resume",
                f"{path} must not embed a personal name in an output filename",
            )


class TestCandidateSetupTooling(unittest.TestCase):
    """Bootstrap/validate tooling must be safe (never silently overwrite)
    and must run without any network/model calls."""

    def test_bootstrap_is_idempotent(self):
        run([sys.executable, "tools/bootstrap_candidate.py"])
        second = run([sys.executable, "tools/bootstrap_candidate.py"])
        self.assertEqual(second.returncode, 0)
        self.assertNotIn("OVERWRITTEN", second.stdout)
        self.assertIn("SKIPPED", second.stdout)

    def test_validate_candidate_setup_never_crashes(self):
        result = run([sys.executable, "tools/validate_candidate_setup.py"])
        self.assertIn(result.returncode, (0, 1))
        self.assertIn("CANDIDATE SETUP VALIDATION", result.stdout)

    def test_example_files_all_parse(self):
        for path in EXAMPLES.glob("*.json"):
            json.loads(path.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
