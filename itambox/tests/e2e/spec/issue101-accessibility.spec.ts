import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test.describe('Issue #101 accessibility qualification', () => {
  test('full-page dashboard has no axe violations', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('#page-body-main')).toBeVisible();

    const results = await new AxeBuilder({ page }).analyze();
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });

  test('HTMX list refresh keeps a usable focus target and has no axe violations', async ({ page }) => {
    await page.goto('/inventory/accessories/');
    const list = page.locator('#object-list-dynamic-content');
    await expect(list).toBeVisible();

    const search = page.locator('form.filter-form-inline input[type="search"]');
    await expect(search).toBeVisible();
    await search.fill('issue-101-focus-probe');
    await search.press('Enter');
    await expect(list).toBeVisible();
    await expect(search).toBeFocused();

    const results = await new AxeBuilder({ page }).include('#page-content-wrapper').analyze();
    expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);
  });

  test('modal keyboard flow returns focus to its trigger', async ({ page }) => {
    await page.goto('/inventory/accessories/');
    const trigger = page.locator('[hx-target="#modal-placeholder"]').first();
    await expect(trigger).toBeVisible();
    await trigger.focus();
    await trigger.press('Enter');

    const modal = page.locator('.modal.show');
    await expect(modal).toBeVisible();
    await expect(modal).toContainText(/Action|Configure|Checkout|Clone|Delete/);

    await page.keyboard.press('Escape');
    await expect(modal).not.toBeVisible();
    await expect(trigger).toBeFocused();
  });

  for (const theme of ['light', 'dark'] as const) {
    test(`primary controls remain keyboard reachable in ${theme} theme with reduced motion`, async ({ page }) => {
      await page.goto('/');
      await page.evaluate((selectedTheme) => {
        document.documentElement.setAttribute('data-bs-theme', selectedTheme);
      }, theme);
      await page.emulateMedia({ reducedMotion: 'reduce' });

      const results = await new AxeBuilder({ page }).analyze();
      expect(results.violations, JSON.stringify(results.violations, null, 2)).toEqual([]);

      const skipLink = page.getByRole('link', { name: 'Skip to main content' });
      await skipLink.focus();
      await expect(skipLink).toBeFocused();
      await expect(page.getByRole('main')).toBeVisible();

      const motion = await page.evaluate(() => {
        const probe = document.createElement('div');
        probe.className = 'htmx-indicator';
        document.body.appendChild(probe);
        const style = getComputedStyle(probe);
        const result = {
          animationDuration: style.animationDuration,
          transitionDuration: style.transitionDuration,
        };
        probe.remove();
        return result;
      });
      expect(motion.animationDuration).toBe('0.01ms');
      expect(motion.transitionDuration).toBe('0.01ms');
    });
  }
});
