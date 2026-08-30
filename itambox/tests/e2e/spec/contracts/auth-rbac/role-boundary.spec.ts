import { test, expect } from '../../../fixtures/test';

test.describe('authentication and RBAC contract', () => {
  test.describe('operator', { tag: '@operator' }, () => {
    test('operator can read and reach the asset mutation boundary', async ({ page }) => {
      const response = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
      expect(response!.status(), 'operator asset list response').toBe(200);
      await expect(page.locator('h2.page-title')).toContainText(/Assets/i);
      const create = await page.goto('/assets/assets/add/', { waitUntil: 'domcontentloaded' });
      expect(create?.status(), 'operator asset create boundary').toBe(200);
      await expect(page.locator('form[method="post"] input[name="asset_tag"]')).toHaveCount(1);
    });
  });

  test.describe('viewer', { tag: '@viewer' }, () => {
    test('viewer can read but both UI and route deny asset mutation', async ({ page }) => {
      const response = await page.goto('/assets/assets/', { waitUntil: 'domcontentloaded' });
      expect(response!.status(), 'viewer asset list response').toBe(200);
      await expect(page.locator('h2.page-title')).toContainText(/Assets/i);
      await expect(page.getByRole('link', { name: /Add Asset/i })).toHaveCount(0);

      const denied = await page.request.get('/assets/assets/add/', { maxRedirects: 0 });
      expect(denied.status(), 'viewer asset create boundary').toBe(403);
      await expect(page.locator('form[method="post"] input[name="asset_tag"]')).toHaveCount(0);
    });
  });
});
