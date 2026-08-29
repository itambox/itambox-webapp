import { test, expect } from '../../fixtures/test';

const required = (name: string): string => {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is required for the external OIDC contract.`);
  return value;
};

test.describe('external OIDC contract', { tag: '@non-destructive' }, () => {
  test('the pinned provider publishes the configured issuer and signing endpoints', async ({ request }) => {
    const provider = required('E2E_OIDC_PROVIDER_URL').replace(/\/$/u, '');
    const response = await request.get(`${provider}/itambox-e2e/.well-known/openid-configuration`);
    expect(response.status()).toBe(200);
    const body = await response.json() as Record<string, unknown>;
    expect(body.issuer).toBe(`${provider}/itambox-e2e`);
    expect(body.jwks_uri).toBe(`${provider}/itambox-e2e/jwks`);
    expect(body.authorization_endpoint).toBe(`${provider}/itambox-e2e/authorize`);
    expect(body.token_endpoint).toBe(`${provider}/itambox-e2e/token`);
  });
});
