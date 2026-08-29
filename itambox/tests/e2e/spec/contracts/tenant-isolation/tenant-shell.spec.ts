import { test, expect } from '../../fixtures/test';

test.describe('tenant isolation contract', { tag: '@pr' }, () => {
  test('REST tenant visibility and rendered tenant identity agree', async ({ page, request, activeTenant }) => {
    const response = await request.get('/api/organization/tenants/?limit=100');
    expect(response.status(), 'Tenant visibility response').toBe(200);
    const payload: unknown = await response.json();
    const rows = Array.isArray(payload)
      ? payload
      : payload !== null && typeof payload === 'object' && Array.isArray((payload as { results?: unknown }).results)
        ? (payload as { results: unknown[] }).results
        : null;
    if (!rows || rows.length === 0 || !rows.some((row) => row !== null && typeof row === 'object' && String((row as { id?: unknown }).id) === activeTenant.id)) {
      throw new Error('REST visibility must include the attested active tenant.');
    }
    await expect(page.getByTestId('active-tenant')).toHaveAttribute('data-tenant-id', activeTenant.id);
  });
});
