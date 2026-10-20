from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]

SCANNED_EXTENSIONS = {
    ".py", ".json", ".md", ".txt",
    ".yaml", ".yml", ".toml", ".sh",
}

# Directories that are allowed (or expected) to hold real user data locally.
# They are git-ignored, and this suite must never assume anything about
# their contents -- a real user's private files must never be required
# (or forbidden) by a test that runs in the public repository.
PRIVATE_DIRS = {
    ".git",
    ".work",
    "master",
    "reference",
    "output",
    "tracking",
}

# Fictional placeholders it is safe (and expected) for example/config files
# to contain. Anything email/phone-shaped that is NOT one of these is
# treated as a possible real personal value and fails the scan.
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.invalid",
    "email.invalid",
}

ALLOWED_PHONE_PATTERNS = [
    re.compile(r"555"),
]


def _iter_scanned_files():
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue

        rel = path.relative_to(ROOT)

        if rel.parts and rel.parts[0] in PRIVATE_DIRS:
            continue

        if path.suffix.lower() not in SCANNED_EXTENSIONS:
            continue

        yield rel, path


class TestPublicRepository(unittest.TestCase):
    """Generic, candidate-agnostic safety checks for the public repository.

    Deliberately does NOT contain any denylist of the original candidate's
    real name/email/phone/employer/school -- storing those values here,
    even only to assert their absence, would put private information back
    into the repository. See docs/security-and-privacy.md.
    """

    def test_active_version_is_documented(self):
        path = ROOT / "config/active_resume_pipeline.json"
        self.assertTrue(path.exists())

        data = json.loads(path.read_text(encoding="utf-8"))

        self.assertTrue(
            str(data.get("active_version", "")).strip(),
            "config/active_resume_pipeline.json must declare active_version",
        )

    def test_critical_files_exist(self):
        files = [
            "tools/resume/v86/run_batch.py",
            "tools/resume/v86/semantic_worker.py",
            "tools/resume/v86/correction_worker.py",
            "tools/resume/v86/deterministic_pipeline.py",
            "tools/resume/v86/action_verb_diversity.py",
            "tools/resume/v86/ats_preflight_v86.py",
            "tools/resume/v86/finalize_local.py",
            "tools/resume/v86/llm_invoke.py",
            "tools/resume/v86/build_job_packet.py",
            "tools/resume/v86/build_shared_context.py",
            "tools/resume/v86/context_slicing.py",
            "tools/resume/v86/evidence_ownership.py",
            "tools/resume/v86/resume_quality_report.py",
            "tools/resume/v86/resume_draft_model.py",
            "tools/resume/v86/pdf_roundtrip.py",
            "tools/resume/v86/metrics_audit.py",
            "tools/resume/v86/bullet_length_audit.py",
            "tools/resume/v86/timeline_audit.py",
            "config/v86/resume_quality_diagnostics.json",
            "tools/resume/render_resume.py",
            "tools/resume/v8/render_cover_letter.py",
            "tools/ats/preflight.py",
            "config/resume_rules.md",
            "config/v86/ats_concept_bridges.json",
            "INSTRUCTIONS.md",
            "tools/bootstrap_candidate.py",
            "tools/validate_candidate_setup.py",
        ]

        missing = [rel for rel in files if not (ROOT / rel).exists()]

        self.assertEqual(missing, [], f"Missing files: {missing}")

    def test_no_absolute_home_paths(self):
        # The home-path prefix is assembled from fragments so that this guard
        # does not itself register as a hit when the repository is scanned for
        # absolute home paths.
        pattern = re.compile("/" + "Users" + r"/([A-Za-z0-9_.\-]+)")

        # Usernames that are obviously synthetic/fake placeholders, used
        # deliberately in fixtures/tests (for example "example" in a doc
        # snippet, or "fake-home" in an isolation test's simulated
        # environment) -- never a real local machine username.
        allowed_fake_usernames = {"example", "fake-home", "x"}

        hits = []

        for rel, path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8", errors="ignore")

            for match in pattern.finditer(text):
                if match.group(1) in allowed_fake_usernames:
                    continue
                hits.append(f"{rel}: {match.group(0)}")

        self.assertEqual(hits, [], "\n".join(hits))

    def test_no_unexpected_email_addresses(self):
        pattern = re.compile(
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
        )

        hits = []

        for rel, path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8", errors="ignore")

            for match in pattern.finditer(text):
                email = match.group(0)
                domain = email.rsplit("@", 1)[-1].lower()

                if domain in ALLOWED_EMAIL_DOMAINS:
                    continue

                if domain.endswith(".invalid"):
                    continue

                hits.append(f"{rel}: {email}")

        self.assertEqual(hits, [], "\n".join(hits))

    def test_no_unexpected_phone_numbers(self):
        # Deliberately narrow: requires phone-style formatting (parens
        # and/or a separator between every group) so bare 10-digit runs
        # (job IDs, LinkedIn posting IDs, checksums, PIDs, ...) are never
        # mistaken for a phone number.
        pattern = re.compile(
            r"\(\d{3}\)[\s.\-]?\d{3}[\s.\-]\d{4}\b"
            r"|\b\d{3}[\s.\-]\d{3}[\s.\-]\d{4}\b"
        )

        hits = []

        for rel, path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8", errors="ignore")

            for match in pattern.finditer(text):
                value = match.group(0)

                if any(p.search(value) for p in ALLOWED_PHONE_PATTERNS):
                    continue

                hits.append(f"{rel}: {value}")

        self.assertEqual(hits, [], "\n".join(hits))

    def test_no_key_shaped_strings(self):
        patterns = [
            re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
            re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
            re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
            re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
        ]

        # Fixtures/tests deliberately use obviously-fake credential strings
        # (see test_fake_credentials_are_unmistakably_fake) that must never
        # be flagged as if they were real key-shaped secrets.
        allowed_substrings = {
            "fake-anthropic-api-key-for-test-only",
            "fake-dotenv-secret-for-test-only",
        }

        hits = []

        for rel, path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8", errors="ignore")

            for pattern in patterns:
                for match in pattern.finditer(text):
                    value = match.group(0)
                    if any(allowed in text for allowed in allowed_substrings):
                        continue
                    hits.append(f"{rel}: {value}")

        self.assertEqual(hits, [], f"Key-shaped strings: {hits}")

    def test_no_unexplained_real_urls_in_fixtures(self):
        # Public test fixtures may reference repository URLs to exercise
        # the pipeline's project/link-handling code, but only ever under
        # obviously fictional owners -- never a real, resolvable GitHub
        # username/org that could point back to a real person's account.
        allowed_url_owners = {"example-user", "example-teammate", "example-org"}
        pattern = re.compile(r"https?://github\.com/([A-Za-z0-9_.\-]+)/")

        hits = []

        fixtures_root = ROOT / "tools/resume/v86/tests/fixtures"
        if fixtures_root.exists():
            for path in fixtures_root.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                for match in pattern.finditer(text):
                    owner = match.group(1)
                    if owner not in allowed_url_owners:
                        hits.append(f"{path.relative_to(ROOT)}: {match.group(0)}")

        self.assertEqual(hits, [], f"Unexplained real-looking URLs: {hits}")

    def test_no_generated_documents_tracked(self):
        hits = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".docx", ".pdf"}
            and (path.relative_to(ROOT)).parts[0] not in PRIVATE_DIRS
        ]

        self.assertEqual(
            hits, [], f"Generated documents must not be tracked: {hits}"
        )

    def test_all_python_files_parse(self):
        errors = []

        for path in ROOT.rglob("*.py"):
            rel = path.relative_to(ROOT)

            if rel.parts and rel.parts[0] in {".git", ".work", "output"}:
                continue

            try:
                source = path.read_text(encoding="utf-8")
                ast.parse(source, filename=str(rel))
            except Exception as exc:
                errors.append(f"{rel}: {exc}")

        self.assertEqual(errors, [], "\n".join(errors))

    def test_all_json_examples_parse(self):
        errors = []

        for path in (ROOT / "master" / "examples").rglob("*.json"):
            rel = path.relative_to(ROOT)

            try:
                json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                errors.append(f"{rel}: {exc}")

        self.assertEqual(errors, [], "\n".join(errors))

    def test_private_runtime_is_gitignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")

        expected = [
            ".env",
            "master/",
            "reference/",
            ".work/",
            "output/",
            "jobs/batch_inbox/",
            "jobs/batch_done/",
            "*.docx",
            "*.pdf",
            "tracking/*.xlsx",
        ]

        missing = [item for item in expected if item not in text]

        self.assertEqual(
            missing, [], f"Missing .gitignore rules: {missing}"
        )

    def test_examples_are_not_gitignored(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")

        for expected in ("!master/examples", "!reference/README.md"):
            self.assertIn(
                expected,
                text,
                f".gitignore must keep {expected} tracked",
            )

    def test_fake_credentials_are_unmistakably_fake(self):
        isolation = (
            ROOT / "tools/resume/v86/tests/test_v86_2_isolation_patch.py"
        ).read_text(encoding="utf-8")

        queue = (ROOT / "orchestrator/rolling_queue.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("fake-anthropic-api-key-for-test-only", isolation)
        self.assertIn("fake-dotenv-secret-for-test-only", queue)

    def test_no_date_coded_batch_ids(self):
        # Development-time batch directories are date/time-coded
        # (`batch-YYYYMMDD-HHMMSS`) and identify exactly when a private,
        # local run happened. Regression fixtures must use a fixed,
        # obviously-synthetic name (e.g. `batch-synthetic-regression`)
        # instead -- never a literal date-coded value, even a fake one,
        # since that shape itself is what a real batch ID looks like.
        pattern = re.compile(r"batch-\d{8}-\d{6}")

        hits = []
        for rel, path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in pattern.finditer(text):
                hits.append(f"{rel}: {match.group(0)}")

        self.assertEqual(hits, [], "\n".join(hits))

    def test_no_date_coded_freeze_snapshot_names(self):
        # Local frozen-baseline/candidate-freeze backup directories are
        # private and gitignored (see docs/security-and-privacy.md). Only
        # generic documentation about such directories (or a `*`-wildcard
        # pattern illustrating the naming convention) may be tracked --
        # never one concrete, date/time-coded snapshot name, which would
        # reveal exactly when a private local promotion/freeze happened.
        pattern = re.compile(r"(?:candidate-)?freeze-\d{8}-\d{6}")

        hits = []
        for rel, path in _iter_scanned_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in pattern.finditer(text):
                hits.append(f"{rel}: {match.group(0)}")

        self.assertEqual(hits, [], "\n".join(hits))

    def test_no_resume_quality_report_instances_are_tracked(self):
        # Each job's consolidated resume-quality report
        # (tools/resume/v86/resume_quality_report.py) is written ONLY into
        # that job's own git-ignored runtime directory, because its
        # findings quote the candidate's actual bullets, owners and
        # verified measurements. An instance appearing anywhere a public
        # scan can reach means candidate content has escaped the private
        # runtime area.
        hits = [
            str(path.relative_to(ROOT))
            for path in ROOT.rglob("resume_quality_report.json")
            if path.relative_to(ROOT).parts[0] not in PRIVATE_DIRS
        ]

        self.assertEqual(
            hits, [], f"resume-quality report instances must never be tracked: {hits}"
        )

    def test_resume_quality_diagnostics_config_is_candidate_agnostic(self):
        # The shared diagnostics config holds advisory thresholds only --
        # never a candidate's employer, metric, date or bullet.
        path = ROOT / "config/v86/resume_quality_diagnostics.json"
        data = json.loads(path.read_text(encoding="utf-8"))

        offenders = [
            key for key, value in data.items()
            if not key.startswith("_")
            and key != "schema_version"
            and not isinstance(value, (int, float))
        ]

        self.assertEqual(
            offenders, [],
            f"only numeric thresholds belong in this config: {offenders}",
        )

    def test_resume_layout_does_not_hardcode_a_candidate_location(self):
        # config/resume_layout.md is shared, candidate-agnostic formatting
        # config. It must describe the contact-row/location rule generically
        # (e.g. "candidate location") rather than hardcoding any specific
        # city as a rule -- a hardcoded city here previously leaked which
        # real city the original candidate lived in.
        text = (ROOT / "config/resume_layout.md").read_text(encoding="utf-8")

        # City names are assembled from fragments so this guard does not
        # itself register as a hit when the repository is scanned for
        # personal locations.
        hardcoded_cities = ("Bos" + "ton", "Hyder" + "abad", "Brook" + "line")

        for city in hardcoded_cities:
            self.assertNotIn(city, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
