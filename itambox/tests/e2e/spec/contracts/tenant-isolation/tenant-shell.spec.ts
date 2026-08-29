import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';

test.describe('tenant isolation contract', { tag: '@pr' }, () => {
  test('REST tenant visibility and rendered tenant identity agree', async ({ page, request, activeTenant }) => {
    const tenant = requireActiveTenant(activeTenant);
    const response = await request.get('/api/organization/tenants/?limit=100');
    expect(response.status(), 'Tenant visibility response').toBe(200);
    const payload: unknown = await response.json();
    const rows = Array.isArray(payload)
      ? payload
      : payload !== null && typeof payload === 'object' && Array.isArray((payload as { results?: unknown }).results)
        ? (payload as { results: unknown[] }).results
        : null;
    if (!rows || rows.length === 0 || !rows.some((row) => row !== null && typeof row === 'object' && String((row as { id?: unknown }).id) === tenant.id)) {
      throw new Error('REST visibility must include the attested active tenant.');
    }
    await expect(page.getByTestId('active-tenant')).toHaveAttribute('data-tenant-id', tenant.id);
  });
});
