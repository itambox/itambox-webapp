import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from scripts.release_policy import (
    ReleasePolicyError,
    extract_release_notes,
    main,
    parse_version,
    validate_repository,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class VersionPolicyTests(unittest.TestCase):
    def test_dotted_prereleases_parse_and_sort_in_promotion_order(self):
        values = [
            "1.0.0-alpha.2",
            "1.0.0",
            "1.0.0-rc.1",
            "1.0.0-beta.1",
            "1.0.0-alpha.1",
            "1.0.0-alpha.10",
        ]

        parsed = [parse_version(value) for value in values]

        self.assertEqual(
            [item.semver for item in sorted(parsed)],
            [
                "1.0.0-alpha.1",
                "1.0.0-alpha.2",
                "1.0.0-alpha.10",
                "1.0.0-beta.1",
                "1.0.0-rc.1",
                "1.0.0",
            ],
        )
        self.assertEqual(parse_version("1.0.0-alpha.1").pep440, "1.0.0a1")
        self.assertEqual(parse_version("1.0.0-beta.2").pep440, "1.0.0b2")
        self.assertEqual(parse_version("1.0.0-rc.3").pep440, "1.0.0rc3")
        self.assertEqual(parse_version("1.0.0").pep440, "1.0.0")

    def test_non_dotted_or_non_numeric_prereleases_are_rejected(self):
        invalid = [
            "1.0.0-alpha1",
            "1.0.0-alpha",
            "1.0.0-alpha.x",
            "1.0.0-preview.1",
            "v1.0.0-alpha.1",
            "1.0",
        ]

        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ReleasePolicyError):
                parse_version(value)


class RepositoryPolicyTests(unittest.TestCase):
    def test_checked_in_release_metadata_is_consistent(self):
        version = validate_repository(
            REPOSITORY_ROOT, expected_version="1.0.0-alpha.1"
        )

        self.assertEqual(version.semver, "1.0.0-alpha.1")
        self.assertEqual(version.pep440, "1.0.0a1")

    def _write_fixture(
        self,
        root: Path,
        *,
        project_version: str = "1.0.0-alpha.1",
        source_version: str = "1.0.0-alpha.1",
        locked_version: str = "1.0.0a1",
        changelog_version: str = "1.0.0-alpha.1",
        readme_version: str = "1.0.0-alpha.1",
    ) -> None:
        (root / "itambox" / "itambox").mkdir(parents=True)
        (root / "pyproject.toml").write_text(
            f'[project]\nname = "itambox"\nversion = {project_version!r}\n',
            encoding="utf-8",
        )
        (root / "itambox" / "itambox" / "release.py").write_text(
            f"VERSION = {source_version!r}\n", encoding="utf-8"
        )
        (root / "uv.lock").write_text(
            "[[package]]\n"
            'name = "itambox"\n'
            f"version = {locked_version!r}\n"
            'source = { virtual = "." }\n',
            encoding="utf-8",
        )
        (root / "CHANGELOG.md").write_text(
            "# Changelog\n\n"
            "## [Unreleased]\n\n"
            f"## [{changelog_version}] - 2026-07-24\n\n"
            "### Added\n\n- First alpha release.\n\n"
            f"[{changelog_version}]: https://github.com/itambox/itambox-webapp/releases/tag/v{changelog_version}\n",
            encoding="utf-8",
        )
        (root / "README.md").write_text(
            f"This repository is pre-release. `{readme_version}` is current version metadata.\n",
            encoding="utf-8",
        )

    def test_repository_metadata_maps_to_one_release_identity(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_fixture(root)

            result = validate_repository(root, expected_version="1.0.0-alpha.1")

            self.assertEqual(result.semver, "1.0.0-alpha.1")
            self.assertEqual(result.pep440, "1.0.0a1")
            self.assertEqual(
                extract_release_notes(root / "CHANGELOG.md", result.semver),
                "### Added\n\n- First alpha release.",
            )

    def test_metadata_drift_is_rejected_with_the_source_named(self):
        cases = [
            ("project_version", "1.0.0-alpha.2", "pyproject.toml"),
            ("source_version", "1.0.0-alpha.2", "release.py"),
            ("locked_version", "1.0.0a2", "uv.lock"),
            ("changelog_version", "1.0.0-alpha.2", "CHANGELOG.md"),
            ("readme_version", "1.0.0-alpha.2", "README.md"),
        ]
        for field, value, expected_source in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self._write_fixture(root, **{field: value})

                with self.assertRaisesRegex(ReleasePolicyError, expected_source):
                    validate_repository(root, expected_version="1.0.0-alpha.1")

    def test_cli_verifies_metadata_and_prints_release_notes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_fixture(root)
            output = io.StringIO()

            with redirect_stdout(output):
                status = main(
                    [
                        "verify",
                        "--root",
                        str(root),
                        "--version",
                        "1.0.0-alpha.1",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertIn("release metadata valid: 1.0.0-alpha.1", output.getvalue())

            output = io.StringIO()
            with redirect_stdout(output):
                status = main(
                    [
                        "notes",
                        "--root",
                        str(root),
                        "--version",
                        "1.0.0-alpha.1",
                    ]
                )
            self.assertEqual(status, 0)
            self.assertEqual(output.getvalue().strip(), "### Added\n\n- First alpha release.")

    def test_missing_or_empty_release_notes_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "CHANGELOG.md"
            path.write_text(
                "# Changelog\n\n## [1.0.0-alpha.1] - 2026-07-24\n\n",
                encoding="utf-8",
            )

            with self.assertRaises(ReleasePolicyError):
                extract_release_notes(path, "1.0.0-alpha.1")


class ReleaseAutomationContractTests(unittest.TestCase):
    def test_runtime_image_declares_required_oci_identity_labels(self):
        dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")

        for build_arg in ("ITAMBOX_VERSION", "ITAMBOX_REVISION", "ITAMBOX_SOURCE"):
            self.assertIn(f"ARG {build_arg}", dockerfile)
        for label in (
            "org.opencontainers.image.version",
            "org.opencontainers.image.revision",
            "org.opencontainers.image.source",
        ):
            self.assertIn(label, dockerfile)

    def test_public_guidance_uses_dotted_prerelease_examples(self):
        security_policy = (REPOSITORY_ROOT / "SECURITY.md").read_text(encoding="utf-8")
        bug_template = (
            REPOSITORY_ROOT / ".github" / "ISSUE_TEMPLATE" / "bug_report.md"
        ).read_text(encoding="utf-8")

        self.assertNotIn("1.0.0-alpha1", security_policy)
        self.assertNotIn("1.0.0-alpha1", bug_template)
        self.assertIn("1.0.0-alpha.1", bug_template)

    def test_release_workflow_separates_pr_rehearsal_from_draft_preparation(self):
        workflow = (
            REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("pull_request:", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn('- "README.md"', workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("github.event_name == 'pull_request'", workflow)
        self.assertIn("validate-dispatch-ref:", workflow)
        self.assertIn('test "$GITHUB_REF" = "refs/heads/main"', workflow)
        self.assertIn("github.ref == 'refs/heads/main'", workflow)
        self.assertIn("commits/${GITHUB_SHA}/pulls", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("pull-requests: read", workflow)
        self.assertIn("gh release create", workflow)
        self.assertIn("--draft", workflow)
        self.assertIn("--prerelease", workflow)
        self.assertIn("docker image inspect", workflow)
        self.assertNotIn("push: true", workflow)
        self.assertNotIn("self-hosted", workflow)


if __name__ == "__main__":
    unittest.main()
