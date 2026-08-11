import { test, expect, APIRequestContext } from '@playwright/test';

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    throw new Error(`E2E prerequisite missing: ${name}`);
  }
  return value;
}

const scimTenantSlug = requiredEnv('E2E_TENANT_SLUG');
const isolationTenantSlug = requiredEnv('E2E_ISOLATION_TENANT_SLUG');
const tenantGroupName = requiredEnv('E2E_TENANT_GROUP_NAME');
const scimToken = requiredEnv('E2E_SCIM_TOKEN');
const oidcProviderUrl = requiredEnv('E2E_OIDC_PROVIDER_URL');
const oidcSubject = requiredEnv('E2E_OIDC_SUBJECT');
const oidcEmail = requiredEnv('E2E_OIDC_EMAIL');
const baseURL = process.env.E2E_BASE_URL || 'http://localhost:8000';
const appOrigin = new URL(baseURL).origin;
const providerOrigin = new URL(oidcProviderUrl).origin;
const scimHeaders = { Authorization: `Bearer ${scimToken}` };
const scimUrl = (path: string) => `/api/tenants/${scimTenantSlug}/scim/v2/${path}`;

const scimErrorSchema = 'urn:ietf:params:scim:api:messages:2.0:Error';
const scimListSchema = 'urn:ietf:params:scim:api:messages:2.0:ListResponse';
const scimUserSchema = 'urn:ietf:params:scim:schemas:core:2.0:User';
const scimGroupSchema = 'urn:ietf:params:scim:schemas:core:2.0:Group';

type ScimUserFixture = {
  id: string;
  userName: string;
  email: string;
  externalId: string;
};

async function expectScimError(response: Awaited<ReturnType<APIRequestContext['get']>>, status: number) {
  expect(response.status()).toBe(status);
  const body = await response.json();
  expect(body).toMatchObject({
    schemas: [scimErrorSchema],
    status: String(status),
  });
  expect(body.detail).toEqual(expect.any(String));
  return body;
}

async function createScimUser(request: APIRequestContext, label: string): Promise<ScimUserFixture> {
  const suffix = `${Date.now()}-${label}`;
  const user = {
    userName: `e2e.scim.${suffix}`,
    email: `e2e.scim.${suffix}@example.com`,
    externalId: `e2e-external-${suffix}`,
  };
  const response = await request.post(scimUrl('Users'), {
    data: {
      schemas: [scimUserSchema],
      externalId: user.externalId,
      userName: user.userName,
      name: { givenName: 'E2E', familyName: label },
      emails: [{ value: user.email, primary: true, type: 'work' }],
      active: true,
    },
  });

  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body).toMatchObject({
    schemas: [scimUserSchema],
    userName: user.userName,
    externalId: user.externalId,
    name: { givenName: 'E2E', familyName: label },
    emails: expect.arrayContaining([expect.objectContaining({ value: user.email, primary: true })]),
    active: true,
    meta: {
      resourceType: 'User',
      location: expect.stringContaining('/Users/'),
    },
  });
  expect(body.id).toMatch(/^[0-9a-f-]{36}$/i);

  return { ...user, id: body.id };
}

function expectScimListBody(body: Record<string, unknown>) {
  expect(body).toMatchObject({
    schemas: [scimListSchema],
    totalResults: expect.any(Number),
    itemsPerPage: expect.any(Number),
    startIndex: 1,
    Resources: expect.any(Array),
  });
}

test.describe('SSO and SCIM 2.0 Provisioning Specs', () => {
  let scimRequest: APIRequestContext;

  test.beforeAll(async ({ playwright }) => {
    scimRequest = await playwright.request.newContext({
      baseURL,
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

  test('1. OIDC login initiation rejects an unknown tenant', async ({ request }) => {
    const response = await request.get('/oidc/authenticate/e2e-missing-tenant/', {
      maxRedirects: 0,
    });

    expect(response.status()).toBe(404);
  });

  test('2. OIDC callback without initiation fails closed at the login boundary', async ({ playwright }) => {
    const callbackContext = await playwright.request.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    try {
      const callback = await callbackContext.get(
        '/oidc/callback/?code=uninitiated-code&state=uninitiated-state',
        { maxRedirects: 0 },
      );
      expect(callback.status()).toBe(302);
      expect(callback.headers()['location']).toBe('/');

      const dashboard = await callbackContext.get('/', { maxRedirects: 0 });
      expect(dashboard.status()).toBe(302);
      expect(dashboard.headers()['location']).toMatch(/^\/accounts\/login\//);
    } finally {
      await callbackContext.dispose();
    }
  });

  test('3. Tenant SCIM ServiceProviderConfig advertises the supported contract', async () => {
    const response = await scimRequest.get(scimUrl('ServiceProviderConfig'));

    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({
      schemas: ['urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig'],
      patch: { supported: true },
      bulk: { supported: false },
      filter: { supported: true, maxResults: 200 },
      changePassword: { supported: false },
      sort: { supported: false },
      etag: { supported: false },
      authenticationSchemes: [expect.objectContaining({ type: 'oauthbearertoken', primary: true })],
    });
  });

  test('4. Tenant SCIM User create persists and is readable through list and detail APIs', async () => {
    const user = await createScimUser(scimRequest, 'Create');

    const detail = await scimRequest.get(scimUrl(`Users/${user.id}`));
    expect(detail.status()).toBe(200);
    const detailBody = await detail.json();
    expect(detailBody).toMatchObject({
      schemas: [scimUserSchema],
      id: user.id,
      userName: user.userName,
      externalId: user.externalId,
      active: true,
      emails: expect.arrayContaining([expect.objectContaining({ value: user.email })]),
      meta: { location: scimUrl(`Users/${user.id}`) },
    });

    const list = await scimRequest.get(
      `${scimUrl('Users')}?filter=${encodeURIComponent(`userName eq "${user.userName}"`)}`,
    );
    expect(list.status()).toBe(200);
    const listBody = await list.json();
    expectScimListBody(listBody);
    expect(listBody.totalResults).toBe(1);
    expect(listBody.Resources).toEqual(
      expect.arrayContaining([expect.objectContaining({ id: user.id, userName: user.userName })]),
    );
  });

  test('5. Tenant SCIM User PATCH updates identity and tenant active state', async () => {
    const user = await createScimUser(scimRequest, 'Patch');
    const replacementExternalId = `${user.externalId}-updated`;

    const response = await scimRequest.patch(scimUrl(`Users/${user.id}`), {
      data: {
        schemas: ['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
        Operations: [
          { op: 'replace', path: 'name.givenName', value: 'Patched' },
          { op: 'replace', path: 'active', value: false },
          { op: 'replace', path: 'externalId', value: replacementExternalId },
        ],
      },
    });

    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({
      schemas: [scimUserSchema],
      id: user.id,
      userName: user.userName,
      externalId: replacementExternalId,
      name: { givenName: 'Patched' },
      active: false,
    });

    const persisted = await scimRequest.get(scimUrl(`Users/${user.id}`));
    expect(persisted.status()).toBe(200);
    expect(await persisted.json()).toMatchObject({
      id: user.id,
      externalId: replacementExternalId,
      name: { givenName: 'Patched' },
      active: false,
    });
  });

  test('6. Tenant SCIM User PUT replaces the supported identity fields', async () => {
    const user = await createScimUser(scimRequest, 'Put');
    const replacement = {
      userName: `${user.userName}.replaced`,
      email: `${user.userName}.replaced@example.com`,
      externalId: `${user.externalId}-replaced`,
    };

    const response = await scimRequest.put(scimUrl(`Users/${user.id}`), {
      data: {
        schemas: [scimUserSchema],
        externalId: replacement.externalId,
        userName: replacement.userName,
        name: { givenName: 'Replaced', familyName: 'Identity' },
        emails: [{ value: replacement.email, primary: true }],
        active: true,
      },
    });

    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({
      schemas: [scimUserSchema],
      id: user.id,
      userName: replacement.userName,
      externalId: replacement.externalId,
      name: { givenName: 'Replaced', familyName: 'Identity' },
      emails: expect.arrayContaining([expect.objectContaining({ value: replacement.email })]),
      active: true,
    });
  });

  test('7. Tenant SCIM User DELETE removes the tenant membership and resource visibility', async () => {
    const user = await createScimUser(scimRequest, 'Delete');

    const deleted = await scimRequest.delete(scimUrl(`Users/${user.id}`));
    expect(deleted.status()).toBe(204);

    await expectScimError(await scimRequest.get(scimUrl(`Users/${user.id}`)), 404);
    const list = await scimRequest.get(
      `${scimUrl('Users')}?filter=${encodeURIComponent(`userName eq "${user.userName}"`)}`,
    );
    expect(list.status()).toBe(200);
    const body = await list.json();
    expectScimListBody(body);
    expect(body.totalResults).toBe(0);
  });

  test('8. Tenant SCIM duplicate username returns a typed 409 uniqueness error', async () => {
    const user = await createScimUser(scimRequest, 'Duplicate');
    const duplicate = await scimRequest.post(scimUrl('Users'), {
      data: {
        schemas: [scimUserSchema],
        externalId: `${user.externalId}-second`,
        userName: user.userName,
        name: { givenName: 'Duplicate', familyName: 'User' },
        emails: [{ value: `${user.userName}.second@example.com`, primary: true }],
        active: true,
      },
    });

    const body = await expectScimError(duplicate, 409);
    expect(body.scimType).toBe('uniqueness');
  });

  test('9. Tenant SCIM malformed User resource IDs return a typed 404', async () => {
    const response = await scimRequest.patch(scimUrl('Users/not-a-resource-id'), {
      data: {
        schemas: ['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
        Operations: [{ op: 'replace', path: 'active', value: false }],
      },
    });

    await expectScimError(response, 404);
  });

  test('10. Tenant SCIM Groups remain read-only and expose tenant-owned data only', async () => {
    const filter = encodeURIComponent(`displayName eq "${tenantGroupName}"`);
    const list = await scimRequest.get(`${scimUrl('Groups')}?filter=${filter}`);
    expect(list.status()).toBe(200);
    const listBody = await list.json();
    expectScimListBody(listBody);
    expect(listBody.totalResults).toBe(1);
    expect(listBody.Resources).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          schemas: [scimGroupSchema],
          displayName: tenantGroupName,
          members: [],
          meta: expect.objectContaining({ resourceType: 'Group' }),
        }),
      ]),
    );

    const group = listBody.Resources[0];
    expect(group.id).toMatch(/^[0-9a-f-]{36}$/i);
    const detailUrl = scimUrl(`Groups/${group.id}`);
    const detail = await scimRequest.get(detailUrl);
    expect(detail.status()).toBe(200);
    expect(await detail.json()).toMatchObject({
      schemas: [scimGroupSchema],
      id: group.id,
      displayName: tenantGroupName,
      members: [],
      meta: expect.objectContaining({ location: detailUrl }),
    });

    const create = await scimRequest.post(scimUrl('Groups'), {
      data: { schemas: [scimGroupSchema], displayName: `${tenantGroupName} create-attempt`, members: [] },
    });
    await expectScimError(create, 403);

    const replace = await scimRequest.put(detailUrl, {
      data: { schemas: [scimGroupSchema], displayName: `${tenantGroupName} replace-attempt`, members: [] },
    });
    await expectScimError(replace, 403);

    const patch = await scimRequest.patch(detailUrl, {
      data: {
        schemas: ['urn:ietf:params:scim:api:messages:2.0:PatchOp'],
        Operations: [{ op: 'replace', path: 'displayName', value: `${tenantGroupName} patch-attempt` }],
      },
    });
    await expectScimError(patch, 403);

    const remove = await scimRequest.delete(detailUrl);
    await expectScimError(remove, 403);

    const unchanged = await scimRequest.get(detailUrl);
    expect(unchanged.status()).toBe(200);
    expect(await unchanged.json()).toMatchObject({ displayName: tenantGroupName, members: [] });
  });

  test('11. Tenant-scoped bearer auth rejects another tenant and anonymous unknown tenants without disclosure', async ({ request }) => {
    const foreignTenant = await scimRequest.get(`/api/tenants/${isolationTenantSlug}/scim/v2/Users`);
    await expectScimError(foreignTenant, 401);

    const anonymousUnknown = await request.get('/api/tenants/e2e-missing-tenant/scim/v2/Users');
    const body = await expectScimError(anonymousUnknown, 401);
    expect(body.detail.toLowerCase()).not.toContain('not found');
    expect(body.detail.toLowerCase()).not.toContain('e2e-missing-tenant');
  });

  test('12. OIDC provider errors terminate an existing authenticated UI session', async ({ browser }) => {
    const authenticatedContext = await browser.newContext({
      baseURL,
      storageState: { cookies: [], origins: [] },
    });
    try {
      const page = await authenticatedContext.newPage();
      await page.goto('/');
      await page.fill('input[name="username"]', requiredEnv('E2E_USERNAME'));
      await page.fill('input[name="password"]', requiredEnv('E2E_PASSWORD'));
      await Promise.all([
        page.waitForNavigation({ waitUntil: 'load' }),
        page.click('button[type="submit"]'),
      ]);

      const beforeLogout = await authenticatedContext.request.get('/', { maxRedirects: 0 });
      expect(beforeLogout.status()).toBe(200);

      const callback = await authenticatedContext.request.get(
        '/oidc/callback/?error=access_denied&state=expired_state',
        { maxRedirects: 0 },
      );
      expect(callback.status()).toBe(302);
      expect(callback.headers()['location']).toBe('/');

      const afterLogout = await authenticatedContext.request.get('/', { maxRedirects: 0 });
      expect(afterLogout.status()).toBe(302);
      expect(afterLogout.headers()['location']).toMatch(/^\/accounts\/login\//);
    } finally {
      await authenticatedContext.close();
    }
  });

  test('13. Positive OIDC login provisions a tenant-bound user and asset holder', async ({ browser }) => {
    const oidcContext = await browser.newContext({ baseURL });
    try {
      const page = await oidcContext.newPage();
      await page.setExtraHTTPHeaders({ 'X-Forwarded-For': '127.0.0.2' });
      const loginResponse = await page.goto('/accounts/login/');
      if (loginResponse === null) {
        throw new Error('OIDC login page navigation returned no response');
      }
      expect(loginResponse.status()).toBe(200);

      const oidcLink = page.getByRole('link', { name: 'Sign in with E2E OIDC (OIDC)' });
      await expect(oidcLink).toHaveCount(1);
      await expect(oidcLink).toBeVisible();
      const linkHref = await oidcLink.getAttribute('href');
      if (!linkHref) {
        throw new Error('E2E OIDC login link has no target');
      }
      const initiationHref = new URL(linkHref, baseURL);
      expect(initiationHref.pathname).toBe('/oidc/authenticate/helix-rnd/');

      const initiationResponsePromise = page.waitForResponse(response => {
        const url = new URL(response.url());
        return (
          response.request().method() === 'GET' &&
          url.origin === appOrigin &&
          url.pathname === '/oidc/authenticate/helix-rnd/'
        );
      });
      const providerResponsePromise = page.waitForResponse(response => {
        const url = new URL(response.url());
        return (
          response.request().method() === 'GET' &&
          url.origin === providerOrigin &&
          url.pathname === '/itambox-e2e/authorize'
        );
      });
      await oidcLink.click();

      const initiationResponse = await initiationResponsePromise;
      expect(initiationResponse.status()).toBe(302);
      const initiationLocation = initiationResponse.headers()['location'];
      if (!initiationLocation) {
        throw new Error('OIDC initiation did not return a provider location');
      }
      const initiationUrl = new URL(initiationLocation, baseURL);
      expect(initiationUrl.origin).toBe(providerOrigin);
      expect(initiationUrl.pathname).toBe('/itambox-e2e/authorize');
      const initiationState = initiationUrl.searchParams.get('state');
      if (!initiationState) {
        throw new Error('OIDC initiation did not include state');
      }

      const providerResponse = await providerResponsePromise;
      expect(providerResponse.status()).toBe(200);
      const providerPageUrl = new URL(providerResponse.url());
      expect(providerPageUrl.origin).toBe(providerOrigin);
      expect(providerPageUrl.pathname).toBe('/itambox-e2e/authorize');
      const subjectInput = page.locator('input[name="username"]');
      await expect(subjectInput).toBeVisible();
      const signInButton = page.getByRole('button', { name: 'Sign-in' });
      await expect(signInButton).toBeVisible();
      await subjectInput.fill(oidcSubject);

      const providerPostResponsePromise = page.waitForResponse(response => {
        const url = new URL(response.url());
        return (
          response.request().method() === 'POST' &&
          url.origin === providerOrigin &&
          url.pathname === '/itambox-e2e/authorize'
        );
      });
      const callbackResponsePromise = page.waitForResponse(response => {
        const url = new URL(response.url());
        return (
          response.request().method() === 'GET' &&
          url.origin === appOrigin &&
          url.pathname === '/oidc/callback/'
        );
      });
      const dashboardResponsePromise = page.waitForResponse(response => {
        const url = new URL(response.url());
        return response.request().method() === 'GET' && url.origin === appOrigin && url.pathname === '/';
      });
      await signInButton.click();

      const [providerPostResponse, callbackResponse, dashboardResponse] = await Promise.all([
        providerPostResponsePromise,
        callbackResponsePromise,
        dashboardResponsePromise,
      ]);
      expect(providerPostResponse.status()).toBe(302);
      const callbackLocation = providerPostResponse.headers()['location'];
      if (!callbackLocation) {
        throw new Error('OIDC provider did not return the application callback location');
      }
      const callbackUrl = new URL(callbackLocation, oidcProviderUrl);
      const callbackState = callbackUrl.searchParams.get('state');
      const callbackCode = callbackUrl.searchParams.get('code');
      if (
        callbackUrl.origin !== appOrigin ||
        callbackUrl.pathname !== '/oidc/callback/' ||
        !callbackCode ||
        !callbackState ||
        callbackState !== initiationState
      ) {
        throw new Error('OIDC provider callback location failed the bounded state/code contract');
      }

      expect(callbackResponse.status()).toBe(302);
      if (callbackResponse.headers()['location'] !== '/') {
        throw new Error('OIDC callback did not redirect to the dashboard root');
      }
      expect(dashboardResponse.status()).toBe(200);
      if (new URL(dashboardResponse.url()).pathname !== '/') {
        throw new Error('OIDC callback did not complete at the dashboard root');
      }
      await expect(page).toHaveTitle('Dashboard - ITAMbox');
      await expect(page.locator('#dashboard-grid')).toBeVisible();
      await expect(page.locator('.workspace-switcher-name')).toHaveText('Helix Biopharma AG');

      const membershipResponse = await page.goto(`/organization/memberships/?q=${encodeURIComponent(oidcEmail)}`);
      if (membershipResponse === null) {
        throw new Error('Membership list navigation returned no response');
      }
      expect(membershipResponse.status()).toBe(200);
      expect(new URL(page.url()).pathname).toBe('/organization/memberships/');
      const membershipRows = page.locator('table tbody tr');
      await expect(membershipRows).toHaveCount(1);
      const membershipRow = membershipRows.first();
      await expect(membershipRow.getByRole('link', { name: oidcEmail, exact: true })).toHaveCount(1);
      await expect(membershipRow.getByRole('link', { name: 'Admin', exact: true })).toHaveCount(1);
      await expect(membershipRow.locator('.mdi-check-circle-outline')).toHaveCount(1);

      const assetHolderResponse = await page.goto(`/organization/asset-holders/?q=${encodeURIComponent(oidcEmail)}`);
      if (assetHolderResponse === null) {
        throw new Error('AssetHolder list navigation returned no response');
      }
      expect(assetHolderResponse.status()).toBe(200);
      expect(new URL(page.url()).pathname).toBe('/organization/asset-holders/');
      const assetHolderRows = page.locator('table tbody tr');
      await expect(assetHolderRows).toHaveCount(1);
      const assetHolderRow = assetHolderRows.first();
      await expect(assetHolderRow.getByRole('link', { name: oidcEmail, exact: true })).toHaveCount(1);
      await expect(assetHolderRow).toContainText('E2E');
      await expect(assetHolderRow).toContainText('OIDC');
      await expect(assetHolderRow).toContainText('Helix Biopharma AG');
    } finally {
      await oidcContext.close();
    }
  });
});
