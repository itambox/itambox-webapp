import { test, expect } from '../../fixtures/test';

test.describe('always-run E2E smoke', { tag: '@smoke' }, () => {
  test('attested dashboard shell is healthy', async ({ page, activeTenant }) => {
    const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
    expect(response, 'Dashboard must return a document').not.toBeNull();
    expect(response!.status(), 'Dashboard response').toBe(200);
    await expect(page).toHaveTitle(/Dashboard - ITAMbox/i);
    await expect(page.getByTestId('active-tenant')).toHaveAttribute('data-tenant-id', activeTenant.id);
    await expect(page.locator('#page-body-main')).toBeVisible();
  });
});
