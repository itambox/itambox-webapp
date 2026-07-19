from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "e2e.yml"
PLAYWRIGHT_CONFIG_PATH = (
    REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "playwright.config.ts"
)


def test_e2e_workflow_generates_masked_ephemeral_credentials_before_seeding():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "secrets.E2E_PASSWORD" not in workflow
    assert "Generate ephemeral E2E credentials" in workflow
    assert "secrets.token_urlsafe" in workflow
    assert 'echo "::add-mask::$E2E_PASSWORD"' in workflow
    assert 'echo "E2E_PASSWORD=$E2E_PASSWORD" >> "$GITHUB_ENV"' in workflow
    assert 'echo "DJANGO_SUPERUSER_PASSWORD=$E2E_PASSWORD" >> "$GITHUB_ENV"' in workflow
    assert workflow.index("Generate ephemeral E2E credentials") < workflow.index(
        "Seed test data (minimal)"
    )


def test_playwright_retains_failure_diagnostics_uploaded_by_workflow():
    config = PLAYWRIGHT_CONFIG_PATH.read_text(encoding="utf-8")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "screenshot: 'only-on-failure'" in config
    assert "video: 'retain-on-failure'" in config
    assert "itambox/tests/e2e/test-results/" in workflow
    assert "itambox/tests/e2e/playwright-report/" in workflow
