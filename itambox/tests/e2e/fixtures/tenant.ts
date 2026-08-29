import { expect, type APIRequestContext, type Page, type TestInfo } from '@playwright/test';
import { getJsonRows } from '../helpers/api';

const SHARED_TARGETS = new Set(['demo.itambox.dev', 'itambox.dev']);

export function assertSafeTarget(testInfo: TestInfo): void {
  const configured = String(testInfo.project.use.baseURL || process.env.E2E_BASE_URL || 'http://localhost:8000');
  let url: URL;
  try {
    url = new URL(configured);
  } catch (error) {
    throw new Error(`E2E target URL is invalid: ${String(error)}`);
  }
  const host = url.hostname.toLowerCase();
  const local = host === 'localhost' || host === '127.0.0.1' || host === '::1' || host === '[::1]';
  if (local) return;

  if (testInfo.tags.includes('@non-destructive') && testInfo.project.name === 'remote-smoke') return;

  if (SHARED_TARGETS.has(host)) {
    throw new Error(`Destructive E2E is permanently blocked for shared target ${url.origin}.`);
  }

  if (process.env.E2E_ALLOW_DESTRUCTIVE !== '1') {
    throw new Error(
      `Destructive E2E is blocked for ${url.origin}; only localhost is allowed by default. ` +
        'Set E2E_ALLOW_DESTRUCTIVE=1 for a reviewed disposable target.',
    );
  }
  const active = process.env.E2E_TENANT_SLUG;
  const disposable = process.env.E2E_DESTRUCTIVE_TENANT_SLUG;
  if (!active || !disposable || active !== disposable) {
    throw new Error(
      'Remote destructive E2E requires E2E_TENANT_SLUG and E2E_DESTRUCTIVE_TENANT_SLUG ' +
        'to name the same dedicated disposable tenant.',
    );
  }
}

export type ActiveTenant = {
  id: string;
  slug: string;
};

export function requireActiveTenant(value: ActiveTenant | null): ActiveTenant {
  if (value === null) {
    throw new Error('This workflow requires an authenticated, REST-attested active tenant.');
  }
  return value;
}

export async function attestActiveTenant(page: Page, request: APIRequestContext): Promise<ActiveTenant> {
  const slug = process.env.E2E_TENANT_SLUG;
  if (!slug) throw new Error('E2E_TENANT_SLUG is required for active-tenant attestation.');

  const tenants = await getJsonRows(request, '/api/organization/tenants/?limit=100', 'tenant visibility');
  const matches = tenants.filter((row) => row.slug === slug);
  expect(matches, `REST tenant visibility must contain exactly one ${slug}`).toHaveLength(1);
  const rawId = matches[0].id;
  if (typeof rawId !== 'string' && typeof rawId !== 'number') {
    throw new Error(`REST tenant ${slug} has no usable ID.`);
  }
  const id = String(rawId);
  const response = await page.goto(`/?switch_tenant=${encodeURIComponent(id)}`, { waitUntil: 'domcontentloaded' });
  expect(response, 'Tenant switch must return a document response').not.toBeNull();
  expect(response!.status(), `Tenant switch ${slug}`).toBeLessThan(400);

  const shellHook = page.getByTestId('active-tenant');
  await expect(shellHook, 'Rendered shell must attest the active tenant').toHaveCount(1);
  await expect(shellHook).toHaveAttribute('data-tenant-id', id);
  await expect(shellHook).toHaveAttribute('data-tenant-slug', slug);
  return { id, slug };
}
