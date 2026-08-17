import { test, expect } from '@playwright/test';

test.describe('Issue #389 accessible names', () => {
  test('asset detail tabs expose names and follow the keyboard Tab path on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 740 });
    await page.goto('/assets/assets/');

    const assetLink = page.locator('table tbody a[href^="/assets/assets/"]').first();
    await expect(assetLink).toBeVisible();
    await assetLink.click();

    const tabs = page.locator('#detail-tabs [role="tab"]');
    await expect(tabs.first()).toBeVisible();
    const tabCount = await tabs.count();
    expect(tabCount).toBeGreaterThan(1);

    for (let index = 0; index < tabCount; index += 1) {
      const tab = tabs.nth(index);
      await expect(tab).toHaveAttribute('aria-label', /\S+/);
    }

    await expect(page.getByRole('tab', { name: 'Changelog' })).toBeVisible();
    const attachmentsTab = page.getByRole('tab', { name: 'Attachments' });
    if (await attachmentsTab.count() > 0) {
      await expect(attachmentsTab).toBeVisible();
    }

    await tabs.first().focus();
    await expect(tabs.first()).toBeFocused();
    for (let index = 1; index < tabCount; index += 1) {
      await page.keyboard.press('Tab');
      await expect(tabs.nth(index)).toBeFocused();
    }
  });
});
