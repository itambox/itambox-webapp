"""Static contract tests for the shared Playwright foundation."""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
E2E_ROOT = REPO_ROOT / "itambox" / "tests" / "e2e"


class OwnedFoundationFilesTests(unittest.TestCase):
    def test_required_foundation_modules_exist(self):
        required = (
            "auth/admin.setup.ts",
            "auth/operator.setup.ts",
            "auth/viewer.setup.ts",
            "fixtures/test.ts",
            "fixtures/cleanup.ts",
            "fixtures/tenant.ts",
            "fixtures/factories/identity.ts",
            "helpers/api.ts",
            "helpers/errors.ts",
            "helpers/forms.ts",
            "helpers/htmx.ts",
            "helpers/jobs.ts",
            "helpers/names.ts",
            "run-selected.mjs",
        )
        for relative in required:
            with self.subTest(path=relative):
                self.assertTrue((E2E_ROOT / relative).is_file())

    def test_playwright_config_has_explicit_role_projects_and_no_shared_global_state(self):
        config = (E2E_ROOT / "playwright.config.ts").read_text(encoding="utf-8")
        for project in ("setup-admin", "setup-operator", "setup-viewer", "admin", "operator", "viewer", "anonymous"):
            with self.subTest(project=project):
                self.assertIn(f"name: '{project}'", config)
        self.assertIn("dependencies:", config)
        self.assertIn("setup-admin", config)
        self.assertIn("setup-operator", config)
        self.assertIn("setup-viewer", config)
        self.assertIn("setup-aggregate", config)
        self.assertNotIn("globalSetup", config)
        self.assertNotIn("storageState.json", config)
        self.assertIn("reporter", config)
        self.assertIn("json", config)
        self.assertIn("workers: 1", config)
        self.assertIn("fullyParallel: false", config)

    def test_automatic_fixture_contracts_are_fail_closed(self):
        fixture = (E2E_ROOT / "fixtures" / "test.ts").read_text(encoding="utf-8") + (
            E2E_ROOT / "fixtures" / "playwright-fixtures.ts"
        ).read_text(encoding="utf-8")
        cleanup = (E2E_ROOT / "fixtures" / "cleanup.ts").read_text(encoding="utf-8")
        tenant = (E2E_ROOT / "fixtures" / "tenant.ts").read_text(encoding="utf-8")
        errors = (E2E_ROOT / "helpers" / "errors.ts").read_text(encoding="utf-8")
        names = (E2E_ROOT / "helpers" / "names.ts").read_text(encoding="utf-8")

        self.assertIn("{ auto: true }", fixture)
        shell = (REPO_ROOT / "itambox" / "templates" / "global_includes" / "_topbar.html").read_text(encoding="utf-8")
        self.assertIn("data-testid", shell)
        self.assertIn("data-tenant-id", shell)
        self.assertIn("data-tenant-slug", shell)
        self.assertIn("assertSafeTarget", fixture)
        self.assertIn("activeTenant", fixture)
        self.assertIn("E2E_TENANT_SLUG", tenant)
        self.assertIn("console", errors)
        self.assertIn("pageerror", errors)
        self.assertIn("status() >= 500", errors)
        self.assertIn("reverse()", cleanup)
        self.assertIn("failures", cleanup)
        for token in ("project.name", "workerIndex", "retry", "runId"):
            with self.subTest(token=token):
                self.assertIn(token, names)

    def test_new_owned_specs_do_not_use_known_fail_open_shortcuts(self):
        owned = E2E_ROOT / "spec" / "apps"
        self.assertTrue(owned.is_dir())
        contents = "\n".join(path.read_text(encoding="utf-8") for path in owned.rglob("*.ts"))
        self.assertNotIn("waitForTimeout(", contents)
        self.assertNotIn("console.log(", contents)
        self.assertNotIn("test.skip(", contents)
        self.assertNotIn("test.fixme(", contents)


if __name__ == "__main__":
    unittest.main()
