import { test, expect } from '../../fixtures/test';

test.describe('jobs contract', { tag: '@pr' }, () => {
  test('the asset scanner entry point is reachable without hiding a missing route', async ({ page }) => {
    const response = await page.goto('/assets/assets/checkin/scan/', { waitUntil: 'domcontentloaded' });
    expect(response, 'asset scanner must return a document').not.toBeNull();
    expect(response!.status(), 'asset scanner response').toBe(200);
    await expect(page.locator('h2.page-title')).toContainText(/Check.?in|Scan/i);
  });
});
