import { test, expect } from '../../../fixtures/test';

test.describe('authentication and RBAC contract', () => {
  test.describe('operator', { tag: '@operator' }, () => {
    test('operator can read the attested asset list', async ({ page }) => {
      const response = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
      expect(response!.status(), 'operator asset list response').toBe(200);
      await expect(page.locator('h2.page-title')).toContainText(/Assets/i);
    });
  });

  test.describe('viewer', { tag: '@viewer' }, () => {
    test('viewer can read but is not presented with an asset mutation action', async ({ page }) => {
      const response = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
      expect(response!.status(), 'viewer asset list response').toBe(200);
      await expect(page.locator('h2.page-title')).toContainText(/Assets/i);
      await expect(page.getByRole('link', { name: /Add Asset/i })).toHaveCount(0);
    });
  });
});
