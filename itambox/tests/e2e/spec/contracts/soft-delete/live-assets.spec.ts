import { test, expect } from '../../../fixtures/test';

test.describe('soft-delete contract', { tag: '@pr' }, () => {
  test('live asset list has a stable object-table boundary', async ({ page }) => {
    const response = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
    expect(response!.status(), 'live asset list response').toBe(200);
    await expect(page.locator('#object-list-table-container')).toBeVisible();
  });
});
