import json
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "e2e.yml"
PLAYWRIGHT_CONFIG_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "playwright.config.ts"
PREFLIGHT_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "preflight-check.mjs"
E2E_PACKAGE_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "package.json"
SCIM_SPEC_PATH = REPOSITORY_ROOT / "itambox" / "tests" / "e2e" / "spec" / "apps" / "users" / "sso-scim.spec.ts"


def _folded_json_env_value(document, key):
    lines = document.splitlines()
    marker = f"{key}: >-"
    for index, line in enumerate(lines):
        if line.strip() != marker:
            continue
        key_indent = len(line) - len(line.lstrip())
        value_lines = []
        for value_line in lines[index + 1 :]:
            if value_line.strip() and len(value_line) - len(value_line.lstrip()) <= key_indent:
                break
            if value_line.strip():
                value_lines.append(value_line.strip())
        return json.loads("".join(value_lines))
    raise AssertionError(f"missing folded JSON environment value: {key}")


def load_tests(_loader, _tests, _pattern):
    suite = unittest.TestSuite()
    for name, test in globals().items():
        if name.startswith("test_") and callable(test):
            suite.addTest(unittest.FunctionTestCase(test))
    return suite


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


def test_e2e_workflow_pins_the_positive_oidc_provider_contract_before_django():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    service_config = _folded_json_env_value(workflow, "JSON_CONFIG")
    tenant_config = _folded_json_env_value(workflow, "ITAMBOX_TENANT_OIDC_CONFIGS")

    assert (
        "ghcr.io/navikt/mock-oauth2-server:6.0.0@sha256:"
        "b9fa251aefee22a97c32534d23a1c400f01dbd483ab263b013d89f6d60d96691"
    ) in workflow
    assert service_config == {
        "interactiveLogin": True,
        "httpServer": "NettyWrapper",
        "tokenProvider": {"keyProvider": {"algorithm": "RS256"}},
        "tokenCallbacks": [
            {
                "issuerId": "itambox-e2e",
                "tokenExpiry": 300,
                "requestMappings": [
                    {
                        "requestParam": "subject",
                        "match": "itambox-e2e-oidc-user",
                        "claims": {
                            "sub": "itambox-e2e-oidc-user",
                            "email": "e2e.oidc@itambox.local",
                            "given_name": "E2E",
                            "family_name": "OIDC",
                            "groups": ["e2e-oidc-admins"],
                            "aud": ["${clientId}"],
                            "azp": "${clientId}",
                        },
                    }
                ],
            }
        ],
    }
    assert tenant_config == {
        "helix-rnd": {
            "enabled": True,
            "display_name": "E2E OIDC",
            "OIDC_RP_CLIENT_ID": "itambox-e2e-client",
            "OIDC_RP_CLIENT_SECRET": "itambox-e2e-secret",
            "OIDC_OP_AUTHORIZATION_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/authorize",
            "OIDC_OP_TOKEN_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/token",
            "OIDC_OP_USER_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/userinfo",
            "OIDC_OP_ISSUER": "http://127.0.0.1:8081/itambox-e2e",
            "OIDC_OP_JWKS_ENDPOINT": "http://127.0.0.1:8081/itambox-e2e/jwks",
            "OIDC_RP_SIGN_ALGO": "RS256",
            "OIDC_RP_SCOPES": "openid email profile groups",
            "OIDC_USE_NONCE": True,
            "OIDC_CREATE_USER": True,
            "OIDC_GROUP_ROLE_MAPPING": {"e2e-oidc-admins": "Admin"},
        }
    }
    assert "E2E_OIDC_PROVIDER_URL: http://127.0.0.1:8081" in workflow
    assert "E2E_OIDC_SUBJECT: itambox-e2e-oidc-user" in workflow
    assert "E2E_OIDC_EMAIL: e2e.oidc@itambox.local" in workflow
    assert workflow.index("ITAMBOX_TENANT_OIDC_CONFIGS:") < workflow.index("Run Django system checks")
    assert workflow.index("Check OIDC mock provider readiness") < workflow.index("Run Django system checks")
    assert "deadline=$((SECONDS + 60))" in workflow
    assert "while (( SECONDS < deadline && attempt < 60 ))" in workflow
    assert "curl --fail --silent --show-error" in workflow
    assert "/isalive" in workflow
    assert "openid-configuration" in workflow
    assert "authorization_endpoint" in workflow
    assert "token_endpoint" in workflow
    assert "userinfo_endpoint" in workflow
    assert "jwks_uri" in workflow
    assert "for key, value in expected.items():" in workflow
    assert "JWKS contains an RSA signing key usable for RS256" in workflow
    assert 'key.get("kty") == "RSA"' in workflow
    assert 'key.get("use", "sig") == "sig"' in workflow
    assert 'key.get("alg", "RS256") == "RS256"' in workflow
    assert "settings.RATELIMIT_USE_X_FORWARDED_FOR = True" in workflow
    assert "settings.RATELIMIT_NUM_PROXIES = 1" in workflow
    assert "Role.all_objects.filter(" in workflow
    assert 'name="Administrator"' in workflow
    assert 'name="Admin"' in workflow
    assert "User._base_manager.filter(email=oidc_email)" in workflow
    assert "AssetHolder.all_objects.filter(tenant=tenant)" in workflow
    assert "Membership._base_manager.filter(tenant=tenant)" in workflow


def test_e2e_workflow_redacts_query_strings_from_django_request_logging():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "WSGIRequestHandler.log_message" in workflow
    assert 'runpy.run_path("manage.py", run_name="__main__")' in workflow
    assert "re.sub(r'([^\\s\"]+)\\?[^\\s\"]*', r'\\1', value)" in workflow


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


def test_positive_oidc_e2e_keeps_the_flow_explicit_and_fresh():
    spec = SCIM_SPEC_PATH.read_text(encoding="utf-8")
    preflight = PREFLIGHT_PATH.read_text(encoding="utf-8")
    positive = spec.split("test('13. Positive OIDC login", 1)[1]

    for variable in (
        "E2E_OIDC_PROVIDER_URL",
        "E2E_OIDC_SUBJECT",
        "E2E_OIDC_EMAIL",
        "ITAMBOX_TENANT_OIDC_CONFIGS",
    ):
        assert variable in preflight
    assert "JSON.parse(process.env.ITAMBOX_TENANT_OIDC_CONFIGS)" in preflight
    assert "(redacted)" in preflight
    assert "console.log(process.env.ITAMBOX_TENANT_OIDC_CONFIGS)" not in preflight
    assert "Sign in with E2E OIDC (OIDC)" in positive
    assert "browser.newContext({ baseURL });" in positive
    assert "test.setTimeout(" not in positive
    assert "attachBrowserErrorCollection(page, browserErrors)" in positive
    assert "assertNoUnexpectedBrowserErrors(browserErrors)" in positive
    assert "console.error(" not in positive
    assert "await oidcContext.clearCookies();" in positive
    assert "await page.goto('about:blank', { waitUntil: 'commit' });" in positive
    assert "await page.close();" in positive
    assert "storageState" not in positive
    assert "await page.setExtraHTTPHeaders({ 'X-Forwarded-For': '127.0.0.2' });" in positive
    assert "await page.setExtraHTTPHeaders({});" in positive
    assert 'input[name="username"]' in positive
    assert "getByRole('button', { name: 'Sign-in' })" in positive
    assert "expect(loginResponse.status()).toBe(200);" in positive
    assert "expect(initiationResponse.status()).toBe(302);" in positive
    assert "expect(providerResponse.status()).toBe(200);" in positive
    assert "expect(providerPostResponse.status()).toBe(302);" in positive
    assert "expect(callbackResponse.status()).toBe(302);" in positive
    assert "expect(dashboardResponse.status()).toBe(200);" in positive
    assert "expect(membershipResponse.status()).toBe(200);" in positive
    assert "expect(assetHolderResponse.status()).toBe(200);" in positive
    assert "callbackState !== initiationState" in positive
    assert "await expect(page).toHaveTitle('Dashboard - ITAMbox');" in positive
    assert "#dashboard-grid" in positive
    assert "workspace-switcher-name" in positive
    assert "Helix Biopharma AG" in positive
    assert "organization/memberships/?q=" in positive
    assert "organization/asset-holders/?q=" in positive
    assert "toHaveCount(1)" in positive
    assert "span.true" in positive
    assert "getByRole('link', { name: 'Admin', exact: true })" in positive
    assert "E2E" in positive
    assert "OIDC" in positive
    assert "finally" in positive
    assert "console.log" not in positive
    assert "soft-pass" not in positive.lower()


def test_owned_scope_workflow_topology_is_stable():
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "e2e.yml").read_text(encoding="utf-8")
    assert "detect-e2e-scope:" in workflow
    assert "e2e-selected:" in workflow
    assert "e2e-gate:" in workflow
    assert "if: always()" in workflow
    assert "node run-selected.mjs" in workflow
    assert "python scripts/certify_e2e_run.py" in workflow
    assert "python scripts/check_e2e_gate.py" in workflow
    assert "fetch-depth: 0" in workflow
    assert "workflow_call:" in workflow


def test_release_preparation_awaits_reusable_full_e2e():
    release = (REPOSITORY_ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "e2e-qualification:" in release
    assert "uses: ./.github/workflows/e2e.yml" in release
    assert "prepare-release:" in release
    assert "e2e-qualification" in release


def test_e2e_privileged_role_grants_are_reasoned_and_time_bounded():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    provision = workflow.split("Provision E2E principal and SCIM token", 1)[1].split("Start Django dev server", 1)[0]

    assert "from datetime import timedelta" in provision
    assert "from django.utils import timezone" in provision
    assert '"reason": "Disposable CI E2E role boundary."' in provision
    assert '"valid_until": timezone.now() + timedelta(hours=4)' in provision


def test_e2e_uses_an_isolated_settings_module_with_a_bounded_login_budget():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    settings_path = REPOSITORY_ROOT / "itambox" / "core" / "settings" / "e2e.py"

    assert "DJANGO_SETTINGS_MODULE: core.settings.e2e" in workflow
    assert settings_path.is_file()
    settings = settings_path.read_text(encoding="utf-8")
    assert "from .dev import *" in settings
    assert "RATELIMIT_LIMIT = 100" in settings
    assert "RATELIMIT_PERIOD = 60" in settings


def test_e2e_provisions_one_masked_tenant_bound_api_token():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    provision = workflow.split("Provision E2E principal and SCIM token", 1)[1].split("Start Django dev server", 1)[0]

    assert 'print(f"::add-mask::{plaintext}")' in provision
    assert 'env_file.write(f"E2E_SCIM_TOKEN={plaintext}\\n")' in provision
    assert 'env_file.write(f"E2E_API_TOKEN={plaintext}\\n")' in provision
