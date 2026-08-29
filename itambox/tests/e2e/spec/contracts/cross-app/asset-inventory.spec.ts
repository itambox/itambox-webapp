import { test, expect } from '../../fixtures/test';

test.describe('cross-app contract', { tag: '@pr' }, () => {
  test('asset and inventory surfaces are independently mounted', async ({ page }) => {
    const asset = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
    expect(asset!.status(), 'asset surface response').toBe(200);
    const inventory = await page.goto('/inventory/', { waitUntil: 'domcontentloaded' });
    expect(inventory!.status(), 'inventory surface response').toBe(200);
    await expect(page.locator('h2.page-title')).toContainText(/Inventory/i);
  });
});
