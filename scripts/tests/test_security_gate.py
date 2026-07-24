import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from scripts.security_gate import (
    SecurityGateError,
    evaluate_gitleaks,
    evaluate_trivy,
    load_suppressions,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def suppression(**overrides):
    today = date.today()
    value = {
        "id": "SEC-001",
        "tool": "trivy",
        "finding": "CVE-2026-0001",
        "reason": "Upstream fix is not available; affected code is unreachable.",
        "owner": "@itambox/security",
        "scope": {
            "target": "uv.lock",
            "package": "example",
            "version": "1.0.0",
        },
        "review_on": (today + timedelta(days=14)).isoformat(),
        "expires_on": (today + timedelta(days=30)).isoformat(),
        "references": ["https://github.com/itambox/itambox-webapp/issues/20"],
    }
    value.update(overrides)
    return value


class SuppressionPolicyTests(unittest.TestCase):
    def write_manifest(self, root, entries):
        path = Path(root) / "suppressions.json"
        path.write_text(json.dumps({"version": 1, "suppressions": entries}), encoding="utf-8")
        return path

    def test_empty_manifest_is_valid(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(load_suppressions(self.write_manifest(root, [])), [])

    def test_required_governance_fields_are_enforced(self):
        for field in ("reason", "owner", "scope", "review_on", "expires_on"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                entry = suppression()
                del entry[field]
                with self.assertRaisesRegex(SecurityGateError, field):
                    load_suppressions(self.write_manifest(root, [entry]))

    def test_expired_overlong_and_duplicate_suppressions_are_rejected(self):
        today = date.today()
        cases = [
            ([suppression(
                review_on=(today - timedelta(days=2)).isoformat(),
                expires_on=(today - timedelta(days=1)).isoformat(),
            )], "expired"),
            ([suppression(expires_on=(today + timedelta(days=91)).isoformat())], "90 days"),
            ([suppression(review_on=(today - timedelta(days=1)).isoformat())], "review is overdue"),
            ([suppression(), suppression()], "duplicate"),
        ]
        for entries, message in cases:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as root:
                with self.assertRaisesRegex(SecurityGateError, message):
                    load_suppressions(self.write_manifest(root, entries))


class FindingPolicyTests(unittest.TestCase):
    def test_trivy_blocks_unsuppressed_high_and_emits_sarif(self):
        report = {
            "SchemaVersion": 2,
            "Results": [{
                "Target": "uv.lock",
                "Vulnerabilities": [{
                    "VulnerabilityID": "CVE-2026-0001",
                    "PkgName": "example",
                    "InstalledVersion": "1.0.0",
                    "Severity": "HIGH",
                }],
            }],
        }
        with tempfile.TemporaryDirectory() as root:
            sarif = Path(root) / "results.sarif"
            result = evaluate_trivy([report], [], sarif)
            self.assertFalse(result.passed)
            self.assertEqual(result.blocking, 1)
            self.assertEqual(json.loads(sarif.read_text())["version"], "2.1.0")

    def test_exact_trivy_suppression_and_low_findings_do_not_block(self):
        report = {
            "SchemaVersion": 2,
            "Results": [{
                "Target": "uv.lock",
                "Vulnerabilities": [
                    {"VulnerabilityID": "CVE-2026-0001", "PkgName": "example", "InstalledVersion": "1.0.0", "Severity": "HIGH"},
                    {"VulnerabilityID": "CVE-2026-0002", "PkgName": "other", "InstalledVersion": "2.0.0", "Severity": "LOW"},
                ],
            }],
        }
        with tempfile.TemporaryDirectory() as root:
            manifest = Path(root) / "suppressions.json"
            manifest.write_text(json.dumps({"version": 1, "suppressions": [suppression()]}), encoding="utf-8")
            result = evaluate_trivy([report], load_suppressions(manifest), Path(root) / "results.sarif")
            self.assertTrue(result.passed)
            self.assertEqual(result.suppressed, 1)

    def test_trivy_rejects_malformed_reports_and_missing_expected_targets(self):
        with tempfile.TemporaryDirectory() as root:
            sarif = Path(root) / "results.sarif"
            with self.assertRaisesRegex(SecurityGateError, "invalid Trivy report"):
                evaluate_trivy([{}], [], sarif)
            report = {
                "SchemaVersion": 2,
                "Results": [{"Target": "uv.lock", "Vulnerabilities": None}],
            }
            with self.assertRaisesRegex(SecurityGateError, "missing expected Trivy targets"):
                evaluate_trivy(
                    [report],
                    [],
                    sarif,
                    expected_targets={"uv.lock", "itambox/package-lock.json"},
                )
            report["Results"][0]["Vulnerabilities"] = [{
                "VulnerabilityID": "CVE-2026-0001",
                "PkgName": "example",
                "InstalledVersion": "1.0.0",
                "Severity": "UNRECOGNIZED",
            }]
            with self.assertRaisesRegex(SecurityGateError, "invalid Trivy severity"):
                evaluate_trivy([report], [], sarif)
            report["Results"] = [
                {"Target": "uv.lock", "Vulnerabilities": None},
                {"Target": "unexpected.lock", "Vulnerabilities": None},
            ]
            with self.assertRaisesRegex(SecurityGateError, "unexpected Trivy targets"):
                evaluate_trivy([report], [], sarif, expected_targets={"uv.lock"})

    def test_gitleaks_requires_exact_fingerprint_path_and_rule_suppression(self):
        finding = {"Fingerprint": "abc:src/app.py:rule", "File": "src/app.py", "RuleID": "rule"}
        self.assertFalse(evaluate_gitleaks([finding], []).passed)
        entry = suppression(
            tool="gitleaks",
            finding="rule",
            scope={"fingerprint": "abc:src/app.py:rule", "path": "src/app.py", "rule": "rule"},
        )
        with tempfile.TemporaryDirectory() as root:
            manifest = Path(root) / "suppressions.json"
            manifest.write_text(json.dumps({"version": 1, "suppressions": [entry]}), encoding="utf-8")
            self.assertTrue(evaluate_gitleaks([finding], load_suppressions(manifest)).passed)


class SecurityAutomationContractTests(unittest.TestCase):
    def test_security_workflow_covers_canonical_inputs_without_leaking_reports(self):
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "security.yml").read_text(encoding="utf-8")
        for source in ("uv.lock", "itambox/package-lock.json", "itambox/tests/e2e/package-lock.json"):
            self.assertIn(source, workflow)
        self.assertIn("pull_request:", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("--redact", workflow)
        self.assertIn("$RUNNER_TEMP/gitleaks.json", workflow)
        self.assertNotIn("actions/upload-artifact", workflow)
        self.assertNotIn("cat $RUNNER_TEMP", workflow)
        self.assertNotIn("tee ", workflow)
        self.assertIn("security-events: write", workflow)
        self.assertIn("github.actor != 'dependabot[bot]'", workflow)
        self.assertIn("uv lock --check", workflow)
        self.assertIn("--include-dev-deps", workflow)
        self.assertEqual(workflow.count("--expect-target"), 3)

    def test_release_rehearsal_scans_the_same_image_before_draft_creation(self):
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
        self.assertGreaterEqual(workflow.count("--ignore-unfixed"), 2)
        for image in ("itambox:release-rehearsal", 'itambox:${RELEASE_VERSION}'):
            build = workflow.index(image)
            scan = workflow.index('security-tools/trivy" image', build)
            self.assertGreater(workflow.index(image, scan), scan)
        self.assertLess(
            workflow.index('security-tools/trivy" image', workflow.index("prepare-release:")),
            workflow.index("gh release create"),
        )

    def test_installer_pins_tool_versions_and_literal_checksums(self):
        installer = (REPOSITORY_ROOT / "scripts" / "install_security_tools.sh").read_text(encoding="utf-8")
        self.assertIn("TRIVY_VERSION=0.72.0", installer)
        self.assertIn("GITLEAKS_VERSION=8.30.1", installer)
        self.assertIn("bbb64b9695866ce4a7a8f5c9592002c5961cab378577fa3f8a040df362b9b2ea", installer)
        self.assertIn("551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb", installer)


if __name__ == "__main__":
    unittest.main()
