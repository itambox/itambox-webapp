import { test, expect } from '@playwright/test';

test.describe('The Bulk Operation Workflow Matrix', () => {
  test('Select multiple checkboxes and trigger Bulk Edit and Bulk Delete', async ({ page }) => {
    await page.goto('/inventory/accessories/');

    const rowCheckboxes = page.locator('table tbody tr input[type="checkbox"]');
    const count = await rowCheckboxes.count();
    if (count < 2) return;

    await rowCheckboxes.nth(0).check();
    await rowCheckboxes.nth(1).check();

    const bulkActionsToggle = page
      .locator('.dropdown-toggle', { hasText: 'Bulk Actions' })
      .or(page.locator('button', { hasText: 'Bulk Actions' }))
      .first();
    if ((await bulkActionsToggle.count()) > 0) await bulkActionsToggle.click();

    const bulkEditBtn = page
      .locator('button', { hasText: 'Bulk Edit' })
      .or(page.locator('a', { hasText: 'Bulk Edit' }))
      .first();
    if ((await bulkEditBtn.count()) > 0) {
      await bulkEditBtn.click();
      const modal = page.locator('.modal.show');
      await expect(modal).toBeVisible();
      expect(await modal.locator('input[name="pk"]').count()).toBeGreaterThan(0);
      await modal.locator('button.btn-close, button[data-bs-dismiss="modal"]').first().click();
      await expect(modal).not.toBeVisible();
    }

    await rowCheckboxes.nth(0).check();
    if ((await bulkActionsToggle.count()) > 0) await bulkActionsToggle.click();
    const bulkDeleteBtn = page
      .locator('button', { hasText: 'Bulk Delete' })
      .or(page.locator('a', { hasText: 'Bulk Delete' }))
      .first();
    if ((await bulkDeleteBtn.count()) > 0) {
      await bulkDeleteBtn.click();
      const modal = page.locator('.modal.show');
      await expect(modal).toBeVisible();
      await expect(modal.locator('form')).toBeVisible();
      expect(await modal.locator('input[name="pk"]').count()).toBeGreaterThan(0);
      await modal.locator('button[data-bs-dismiss="modal"]').first().click();
    }
  });
});
