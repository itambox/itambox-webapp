import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { getJsonRows } from '../../../helpers/api';

test.describe('tenant isolation contract', () => {
  test.describe('operator', { tag: '@operator' }, () => {
    test('foreign tenant is absent from REST and cannot replace the rendered active tenant', async ({
      page,
      api,
      activeTenant,
    }) => {
      const tenant = requireActiveTenant(activeTenant);
      const isolationSlug = process.env.E2E_ISOLATION_TENANT_SLUG;
      const isolationId = process.env.E2E_ISOLATION_TENANT_ID;
      if (!isolationSlug) throw new Error('E2E_ISOLATION_TENANT_SLUG is required.');
      if (!isolationId) throw new Error('E2E_ISOLATION_TENANT_ID is required.');

      const visible = await getJsonRows(api, '/api/organization/tenants/?limit=100', 'operator tenant visibility');
      expect(visible.some((row) => String(row.id) === tenant.id && row.slug === tenant.slug)).toBe(true);
      expect(visible.some((row) => row.slug === isolationSlug)).toBe(false);

      const switchAttempt = await page.goto(`/?switch_tenant=${encodeURIComponent(isolationId)}`, {
        waitUntil: 'domcontentloaded',
      });
      expect(switchAttempt?.status(), 'foreign tenant switch attempt').toBe(200);
      await expect(page.getByTestId('active-tenant')).toHaveAttribute('data-tenant-id', tenant.id);
      await expect(page.getByTestId('active-tenant')).toHaveAttribute('data-tenant-slug', tenant.slug);

      const afterAttempt = await getJsonRows(
        api,
        '/api/organization/tenants/?limit=100',
        'operator tenant visibility after switch attempt',
      );
      expect(afterAttempt.some((row) => row.slug === isolationSlug)).toBe(false);
    });
  });
});
