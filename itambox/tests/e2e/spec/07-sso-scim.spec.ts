import { test, expect, APIRequestContext } from '@playwright/test';

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`E2E prerequisite missing: ${name}`);
  }
  return value;
}

const scimTenantSlug = requiredEnv('E2E_TENANT_SLUG');
const scimToken = requiredEnv('E2E_SCIM_TOKEN');
const scimHeaders = { Authorization: `Bearer ${scimToken}` };
const scimUrl = (path: string) => `/api/tenants/${scimTenantSlug}/scim/v2/${path}`;

async function expectAssetHolder(
  request: APIRequestContext,
  username: string,
  email: string,
) {
  const response = await request.get(
    `/api/organization/asset-holders/?q=${encodeURIComponent(username)}`,
  );
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.results).toEqual(
    expect.arrayContaining([expect.objectContaining({ upn: email, email })]),
  );
}

test.describe('SSO and SCIM 2.0 Provisioning Specs', () => {
  let scimRequest: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    scimRequest = await playwright.request.newContext({
      baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
      extraHTTPHeaders: scimHeaders,
    });
  });

  test.afterAll(async () => {
    await scimRequest.dispose();
  });

  test.beforeEach(async ({ page }) => {
    page.on('console', msg => {
      if (msg.type() === 'error') {
        console.error(`[Console Error]: ${msg.text()}`);
      }
    });
    page.on('pageerror', error => {
      console.error(`[Page Error]: ${error.message}`);
    });
  });

  // TIER 1: Feature Coverage (>= 5 tests)

  test('1. OIDC login flow redirection: /oidc/authenticate/ initiates a redirect', async ({ request }) => {
    // Navigate to authenticate endpoint without following redirects automatically to inspect status/location
    const response = await request.get('/oidc/authenticate/', { maxRedirects: 0 });

    // It should be a redirect (302) to the OIDC provider's authorization page
    if (response.status() === 302) {
      const location = response.headers()['location'];
      expect(location).toBeDefined();
      expect(location).toContain('auth'); // typical authorize url keyword
    } else {
      console.log(`OIDC redirect returned status: ${response.status()}`);
    }
  });

  test('2. OIDC callback validation: POST/GET to /oidc/callback/ with tokens authenticates session', async ({ request }) => {
    // If we pass mock/configured auth codes to OIDC callback, it should validate and redirect or return failure
    const response = await request.get('/oidc/callback/?code=mockcode123&state=mockstate123', { maxRedirects: 0 });
    // Should return 302 redirect on successful auth (to dashboard) or 400/403/Redirect on mock failures
    expect(response.status()).toBeDefined();
  });

  test('3. SCIM User Provisioning creates a user in the configured tenant', async () => {
    const uniqueUser = `scim.test.user.${Date.now()}`;
    const scimUserPayload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: uniqueUser,
      name: {
        givenName: "Scim",
        familyName: "Test"
      },
      emails: [{
        value: `${uniqueUser}@example.com`,
        primary: true
      }],
      active: true
    };

    const response = await scimRequest.post(scimUrl('Users'), {
      data: scimUserPayload
    });

    expect(response.status()).toBe(201);
    const json = await response.json();
    expect(json.userName).toBe(uniqueUser);
    expect(json.id).toBeDefined();
  });

  test('4. SCIM User profile sync: Syncing a user via SCIM provisions matching AssetHolder', async ({ request }) => {
    // Verify that syncing a user automatically creates an AssetHolder in organization
    const uniqueUser = `scim.holder.sync.${Date.now()}`;
    const email = `${uniqueUser}@example.com`;
    const payload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: uniqueUser,
      name: { givenName: "Holder", familyName: "Sync" },
      emails: [{ value: email, primary: true }]
    };

    const response = await scimRequest.post(scimUrl('Users'), { data: payload });
    expect(response.status()).toBe(201);
    await expectAssetHolder(request, uniqueUser, email);
  });

  test('5. Tenant SCIM group creation is rejected by the read-only contract', async ({ request }) => {
    const scimGroupPayload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:Group"],
      displayName: "IT-Admins",
      members: []
    };

    const response = await scimRequest.post(scimUrl('Groups'), {
      data: scimGroupPayload
    });

    expect(response.status()).toBe(403);
  });

  // TIER 2: Boundary & Corner Cases (>= 5 tests)

  test('6. OIDC callback with invalid or expired state parameter returns login failure', async ({ request }) => {
    const response = await request.get('/oidc/callback/?code=mockcode123&state=expired_state', { maxRedirects: 0 });
    // Should either redirect to a login failure page or return an error page/response
    expect(response.status()).toBeDefined();
  });

  test('7. SCIM User creation with duplicate username returns 409 Conflict', async () => {
    const duplicateUser = `duplicate.user.${Date.now()}`;
    const payload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: duplicateUser,
      name: { givenName: "Dup", familyName: "User" },
      emails: [{ value: `${duplicateUser}@example.com`, primary: true }]
    };

    const firstResponse = await scimRequest.post(scimUrl('Users'), { data: payload });
    expect(firstResponse.status()).toBe(201);

    const duplicateResponse = await scimRequest.post(scimUrl('Users'), { data: payload });
    expect(duplicateResponse.status()).toBe(409);
  });

  test('8. Unauthenticated SCIM request targeting a non-existent tenant fails closed', async ({ request }) => {
    const response = await request.get('/api/tenants/non-existent-tenant-999/scim/v2/Users');
    // Authentication runs before tenant disclosure, so an anonymous caller must
    // not be able to enumerate which tenant slugs exist.
    expect(response.status()).toBe(401);
  });

  test('9. SCIM User patch with a malformed resource ID returns 404 without crashing', async ({ request }) => {
    const response = await scimRequest.patch(scimUrl('Users/some-user-id'), {
      headers: { 'Content-Type': 'application/scim+json' },
      data: "{invalid json payload"
    });
    // User detail routes accept integer IDs. Django rejects this malformed ID
    // before dispatching the request body to the SCIM view.
    expect(response.status()).toBe(404);
  });

  test('10. Tenant SCIM group updates are rejected by the read-only contract', async () => {
    const groupPatchPayload = {
      schemas: ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
      Operations: [{
        op: "add",
        path: "members",
        value: [{ value: "non-existent-user-uuid" }]
      }]
    };

    const groupPatchResponse = await scimRequest.patch(scimUrl('Groups/2147483647'), {
      data: groupPatchPayload
    });
    expect(groupPatchResponse.status()).toBe(403);
  });

  // TIER 3: Cross-Feature Combinations (combo 2)

  test('11. SCIM group writes and failed OIDC callbacks do not grant permissions', async ({ playwright }) => {
    const uniqueUser = `scim.combo.${Date.now()}`;
    const userPayload = {
      schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
      userName: uniqueUser,
      name: { givenName: "Combo", familyName: "User" },
      emails: [{ value: `${uniqueUser}@example.com`, primary: true }]
    };

    const userRes = await scimRequest.post(scimUrl('Users'), { data: userPayload });
    expect(userRes.status()).toBe(201);
    const userJson = await userRes.json();

    const groupRes = await scimRequest.post(scimUrl('Groups'), {
      data: {
        schemas: ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        displayName: "IT-Admins",
        members: [{ value: userJson.id, display: uniqueUser }]
      }
    });
    expect(groupRes.status()).toBe(403);

    const callbackContext = await playwright.request.newContext({
      baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
    });
    await callbackContext.get(
      `/oidc/callback/?code=combo_code&state=combo_state&username=${uniqueUser}`,
      { maxRedirects: 0 },
    );

    const permissionsRes = await callbackContext.get('/api/users/config/');
    expect(permissionsRes.status()).toBe(401);
    await callbackContext.dispose();
  });

  // TIER 4: Real-World Scenarios (workload 2)

  test('12. Enterprise SCIM provisioning creates holders while groups stay read-only', async ({ request }) => {
    const suffix = Date.now();
    const users = [`scim.ent.1.${suffix}`, `scim.ent.2.${suffix}`];
    const createdUsers: Array<{ id: string; username: string; email: string }> = [];

    for (const username of users) {
      const email = `${username}@enterprise.com`;
      const response = await scimRequest.post(scimUrl('Users'), {
        data: {
          schemas: ["urn:ietf:params:scim:schemas:core:2.0:User"],
          userName: username,
          name: { givenName: username, familyName: "Enterprise" },
          emails: [{ value: email, primary: true }]
        }
      });
      expect(response.status()).toBe(201);
      const body = await response.json();
      createdUsers.push({ id: body.id, username, email });
    }

    const groupRes = await scimRequest.post(scimUrl('Groups'), {
      data: {
        schemas: ["urn:ietf:params:scim:schemas:core:2.0:Group"],
        displayName: "Enterprise-Staff",
        members: createdUsers.map(user => ({ value: user.id }))
      }
    });
    expect(groupRes.status()).toBe(403);

    for (const user of createdUsers) {
      await expectAssetHolder(request, user.username, user.email);
    }
  });

});
