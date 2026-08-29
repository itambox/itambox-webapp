import { test, expect } from '../../fixtures/test';

test.describe('asset custody contract', { tag: '@pr' }, () => {
  test('asset detail exposes an explicit custody action surface', async ({ page }) => {
    const response = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
    expect(response!.status(), 'asset list response').toBe(200);
    const firstDetail = page.locator('a[href^="/assets/assets/"][href$="/"]').first();
    await expect(firstDetail).toBeVisible();
    await firstDetail.click();
    await expect(page.locator('h2.page-title')).toContainText(/Asset/i);
    await expect(page.getByRole('link', { name: /Checkout|Check.?out/i })).toHaveCount(1);
  });
});
