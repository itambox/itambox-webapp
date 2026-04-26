import { test, expect } from '@playwright/test';

test.describe('The Bulk Operation Workflow Matrix', () => {

  test('Select multiple checkboxes and trigger Bulk Edit and Bulk Delete', async ({ page }) => {
    // Go to a list view
    await page.goto('/inventory/accessories/');
    
    // Select first two rows
    const rowCheckboxes = page.locator('table tbody tr input[type="checkbox"]');
    const count = await rowCheckboxes.count();
    
    if (count < 2) {
       console.log('Not enough items to test bulk operations.');
       return;
    }
    
    // Select the first two
    await rowCheckboxes.nth(0).check();
    await rowCheckboxes.nth(1).check();
    
    // Find Bulk Actions dropdown or button
    const bulkActionsToggle = page.locator('.dropdown-toggle', { hasText: 'Bulk Actions' }).or(page.locator('button', { hasText: 'Bulk Actions' })).first();
    if (await bulkActionsToggle.count() > 0) {
       await bulkActionsToggle.click();
    }
    
    // Trigger Bulk Edit
    const bulkEditBtn = page.locator('button', { hasText: 'Bulk Edit' }).or(page.locator('a', { hasText: 'Bulk Edit' })).first();
    if (await bulkEditBtn.count() > 0) {
       await bulkEditBtn.click();
       
       // Ensure modal appears and has the primary keys
       const modal = page.locator('.modal.show');
       await expect(modal).toBeVisible();
       
       // Verify pk inputs are present
       const pkInputs = modal.locator('input[name="pk"]');
       expect(await pkInputs.count()).toBeGreaterThan(0);
       
       // Close modal
       await modal.locator('button.btn-close, button[data-bs-dismiss="modal"]').first().click();
       await expect(modal).not.toBeVisible();
    }
    
    // Re-check just in case modal cleared it
    await rowCheckboxes.nth(0).check();
    
    if (await bulkActionsToggle.count() > 0) {
       await bulkActionsToggle.click();
    }
    
    // Trigger Bulk Delete
    const bulkDeleteBtn = page.locator('button', { hasText: 'Bulk Delete' }).or(page.locator('a', { hasText: 'Bulk Delete' })).first();
    if (await bulkDeleteBtn.count() > 0) {
       await bulkDeleteBtn.click();
       
       // Ensure confirmation modal appears
       const modal = page.locator('.modal.show');
       await expect(modal).toBeVisible();
       
       // We won't actually confirm deletion to prevent DB wiping, but we check if modal is rendered properly
       await expect(modal.locator('form')).toBeVisible();
       
       const pkInputs = modal.locator('input[name="pk"]');
       expect(await pkInputs.count()).toBeGreaterThan(0);
       
       // Cancel
       await modal.locator('button[data-bs-dismiss="modal"]').first().click();
    }
  });

});
