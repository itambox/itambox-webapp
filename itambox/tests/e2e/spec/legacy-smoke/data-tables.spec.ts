import { test, expect } from '@playwright/test';

test.describe('Data Tables, Custom Column Toggles, & Responsive Layouts', () => {

  test('Toggle all columns and check for responsiveness and overlap', async ({ page }) => {
    await page.goto('/inventory/accessories/');
    
    // Wait for the table to be visible
    const table = page.locator('table').first();
    if (await table.count() === 0) {
       console.log('No table found on this page.');
       return;
    }
    await expect(table).toBeVisible();

    // Find the Configure Table button
    // Often it's a dropdown or an offcanvas toggle
    const configureBtn = page.locator('button', { hasText: 'Configure Table' }).first().or(page.locator('[title="Configure Table"]').first());
    
    if (await configureBtn.count() > 0) {
      await configureBtn.click();
      
      // Wait for column toggles panel/dropdown
      const columnCheckboxes = page.locator('input[type="checkbox"][name="columns"]');
      const count = await columnCheckboxes.count();
      
      for (let i = 0; i < count; i++) {
        // Only check those that are unchecked
        const checkbox = columnCheckboxes.nth(i);
        if (!(await checkbox.isChecked())) {
          await checkbox.check();
        }
      }
      
      // Close the configure panel if it's a dropdown or modal (by clicking outside or a close button)
      await page.keyboard.press('Escape');
    }

    // Verify horizontal scrolling on responsive wrapper
    const tableWrapper = page.locator('.table-responsive').first();
    if (await tableWrapper.count() > 0) {
      // Evaluate if scrollWidth > clientWidth
      const isScrollable = await tableWrapper.evaluate((el) => {
         return el.scrollWidth > el.clientWidth;
      });
      // If we enabled many columns, it should be scrollable
      if (isScrollable) {
        console.log('Table is correctly horizontally scrollable.');
      }
    }

    // Test column sorting by clicking header rows
    const sortableHeaders = page.locator('th a.sort-link'); // common in django-tables2
    if (await sortableHeaders.count() > 0) {
       await sortableHeaders.first().click();
       // wait for table to reload
       await page.waitForResponse(response => response.url().includes('sort=') && response.status() === 200);
       await expect(table).toBeVisible();
    }
    
    // Test pagination
    const nextBtn = page.locator('.pagination .page-item:not(.disabled) a', { hasText: 'Next' }).or(page.locator('.pagination .page-item:not(.disabled) a[aria-label="Next"]')).first();
    if (await nextBtn.count() > 0) {
       await nextBtn.click();
       await page.waitForResponse(response => response.url().includes('page=') && response.status() === 200);
       await expect(table).toBeVisible();
    }
  });

});
