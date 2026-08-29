import { test, expect } from '../../fixtures/test';

test.describe('generic object contract', { tag: '@pr' }, () => {
  test('the exact search form renders and returns a document', async ({ page }) => {
    const response = await page.goto('/search/?q=e2e-contract', { waitUntil: 'domcontentloaded' });
    expect(response, 'Search must return a document').not.toBeNull();
    expect(response!.status(), 'Search response').toBe(200);
    await expect(page.locator('form[action="/search/"]')).toHaveCount(1);
  });
});
