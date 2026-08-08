from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "e2e.yml"
PLAYWRIGHT_CONFIG_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "playwright.config.ts"
PREFLIGHT_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "preflight-check.mjs"
E2E_PACKAGE_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "package.json"
SCIM_SPEC_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "spec" / "07-sso-scim.spec.ts"


def test_e2e_workflow_generates_masked_ephemeral_credentials_before_seeding():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "secrets.E2E_PASSWORD" not in workflow
    assert "Generate ephemeral E2E credentials" in workflow
    assert "secrets.token_urlsafe" in workflow
    assert 'echo "::add-mask::$E2E_PASSWORD"' in workflow
    assert 'echo "E2E_PASSWORD=$E2E_PASSWORD" >> "$GITHUB_ENV"' in workflow
    assert 'echo "DJANGO_SUPERUSER_PASSWORD=$E2E_PASSWORD" >> "$GITHUB_ENV"' in workflow
    assert workflow.index("Generate ephemeral E2E credentials") < workflow.index("Seed full E2E fixture data")


def test_playwright_retains_failure_diagnostics_uploaded_by_workflow():
    config = PLAYWRIGHT_CONFIG_PATH.read_text(encoding="utf-8")
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "screenshot: 'only-on-failure'" in config
    assert "video: 'retain-on-failure'" in config
    assert "itambox/tests/e2e/test-results/" in workflow
    assert "itambox/tests/e2e/playwright-report/" in workflow


def test_playwright_local_web_server_resolves_from_django_project_directory():
    config = PLAYWRIGHT_CONFIG_PATH.read_text(encoding="utf-8")
    django_project = REPOSITORY_ROOT / "itambox"

    assert (django_project / "manage.py").is_file()
    assert "cwd: '../..'" in config
    assert "'..\\\\.venv\\\\Scripts\\\\python.exe manage.py runserver 8000'" in config
    assert "'../.venv/bin/python manage.py runserver 8000'" in config


def test_preflight_parses_marked_superuser_count_despite_noisy_django_output():
    preflight = PREFLIGHT_PATH.read_text(encoding="utf-8")
    package = E2E_PACKAGE_PATH.read_text(encoding="utf-8")

    assert "import { parseSuperuserCount } from './preflight-output.mjs';" in preflight
    assert "__E2E_SUPERUSER_COUNT__=" in preflight
    assert "const count = parseSuperuserCount(userResult);" in preflight
    assert "node --test preflight-output.test.mjs" in package


def test_scim_negative_paths_match_authentication_and_url_routing_contracts():
    spec = SCIM_SPEC_PATH.read_text(encoding="utf-8")

    assert "Tenant-scoped bearer auth rejects another tenant and anonymous unknown tenants without disclosure" in spec
    assert "Tenant SCIM malformed User resource IDs return a typed 404" in spec
    assert "not-a-resource-id" in spec
    assert "expect(body.detail.toLowerCase()).not.toContain('not found');" in spec
    assert 'data: "{invalid json payload"' not in spec
    assert "/api/v1/" not in spec
    assert "/api/organization/asset-holders/" not in spec
    assert "/api/users/config/" not in spec


def test_e2e_workflow_provisions_full_demo_and_masked_scim_credentials():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "E2E_TENANT_SLUG: helix-rnd" in workflow
    assert "is_provider=False" in workflow
    assert "deleted_at__isnull=True" in workflow
    assert "uv run --locked --no-sync python manage.py seed_data --force" in workflow
    assert "uv run --locked --no-sync python manage.py runserver" in workflow
    assert "seed_data --production" not in workflow
    assert "Token.objects.create(" in workflow
    assert "::add-mask::" in workflow
    assert "E2E_SCIM_TOKEN=" in workflow
    assert workflow.index("Seed full E2E fixture data") < workflow.index("Provision E2E principal and SCIM token")


def test_scim_e2e_uses_bearer_auth_and_preserves_tenant_anti_harvesting():
    preflight = PREFLIGHT_PATH.read_text(encoding="utf-8")
    spec = SCIM_SPEC_PATH.read_text(encoding="utf-8")

    assert "E2E_SCIM_TOKEN" in preflight
    assert "E2E_TENANT_SLUG" in preflight
    assert "E2E_ISOLATION_TENANT_SLUG" in preflight
    assert "E2E_TENANT_GROUP_NAME" in preflight
    assert "Authorization: `Bearer ${scimToken}`" in spec
    assert "Tenant SCIM Groups remain read-only and expose tenant-owned data only" in spec
    assert "expectScimError(create, 403);" in spec
    assert "expectScimError(foreignTenant, 401);" in spec
    assert "expect(body.detail.toLowerCase()).not.toContain('e2e-missing-tenant');" in spec
    assert "expect(response.status()).toBeDefined();" not in spec
    assert "if (response.status() === 302)" not in spec
    assert "OIDC login initiation rejects an unknown tenant" in spec
    assert "OIDC callback without initiation fails closed at the login boundary" in spec
    assert "OIDC provider errors terminate an existing authenticated UI session" in spec
    assert "expect(callback.headers()['location']).toBe('/');" in spec
    assert "storageState: { cookies: [], origins: [] }" in spec
    assert "const authenticatedContext = await browser.newContext" in spec
    assert "expect(beforeLogout.status()).toBe(200);" in spec
    assert "Tenant SCIM User create persists and is readable through list and detail APIs" in spec
    assert "Tenant SCIM duplicate username returns a typed 409 uniqueness error" in spec
    assert "await expectScimError(patch, 403);" in spec
    assert "playwright.request.newContext()" not in spec
    assert "/api/v1/" not in spec
    assert "/api/organization/asset-holders/" not in spec
    assert "/api/users/config/" not in spec
    assert "mockcode" not in spec
    assert "mockstate" not in spec
    assert "combo_code" not in spec
