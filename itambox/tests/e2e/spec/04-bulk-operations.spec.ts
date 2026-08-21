import { test, expect, type Page } from '@playwright/test';

const aggregateOperatorUsername = process.env.E2E_AGGREGATE_USERNAME || 'lars.eklund';
const aggregateOperatorPassword = process.env.ITAMBOX_SEED_PASSWORD || 'itambox2026';

async function loginAsAggregateOperator(page: Page) {
  await page.context().clearCookies();
  await page.goto('/accounts/login/', { waitUntil: 'networkidle' });
  await page.fill('input[name="username"]', aggregateOperatorUsername);
  await page.fill('input[name="password"]', aggregateOperatorPassword);
  await Promise.all([
    page.waitForNavigation({ waitUntil: 'networkidle' }),
    page.click('button[type="submit"]'),
  ]);
  await expect(page.locator('.alert-danger, .errorlist, [data-testid="login-error"]')).toHaveCount(0);
}

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

  test('All accessible tenants keeps a seeded check-in basket through tenant selection and creates a job', async ({ page }) => {
    // The full demo seed gives this non-superuser administrator access to multiple
    // managed tenants. The global E2E storage state is a superuser, whose All
    // Tenants scope intentionally does not render the aggregate target selector.
    await loginAsAggregateOperator(page);
    await page.goto('/assets/assets/?switch_all_accessible=1', { waitUntil: 'networkidle' });
    await expect(page.locator('a[href*="switch_all_accessible=1"].active').first()).toHaveCount(1);

    const rows = page.locator('#object-list-table-container table tbody tr');
    await expect(rows.first()).toBeVisible();
    const rowIndexesByTenant = new Map<string, number[]>();
    for (let index = 0; index < await rows.count(); index += 1) {
      const row = rows.nth(index);
      const tenantCell = row.locator('td[data-label="Tenant"]');
      const checkbox = row.locator('input[type="checkbox"][name="pk"]');
      if (await tenantCell.count() === 0 || await checkbox.count() === 0) continue;
      const tenantName = (await tenantCell.innerText()).trim();
      const indexes = rowIndexesByTenant.get(tenantName) || [];
      indexes.push(index);
      rowIndexesByTenant.set(tenantName, indexes);
    }

    const selectedTenant = [...rowIndexesByTenant.entries()].find(([, indexes]) => indexes.length >= 2);
    if (!selectedTenant) throw new Error('The full E2E seed must expose two assets from one accessible tenant.');
    const [tenantName, selectedIndexes] = selectedTenant;
    const selectedPks: string[] = [];
    for (const index of selectedIndexes.slice(0, 2)) {
      const checkbox = rows.nth(index).locator('input[type="checkbox"][name="pk"]');
      selectedPks.push(await checkbox.inputValue());
      await checkbox.check();
    }

    const checkinButton = page.locator('.btn-bulk-scan-seed', { hasText: 'Check-in' });
    await expect(checkinButton).toBeEnabled();
    await Promise.all([
      page.waitForURL(/\/assets\/assets\/checkin\/scan\//),
      checkinButton.click(),
    ]);

    const tenantSelect = page.locator('#scan-basket-form select[name="tenant"]');
    await expect(tenantSelect).toHaveCount(1);
    await expect.poll(() => tenantSelect.inputValue()).not.toBe('');
    const selectedTenantValue = await tenantSelect.inputValue();
    const selectedOption = (await tenantSelect.locator('option:checked').textContent())?.trim();
    expect(selectedOption).toBe(tenantName);

    const basketRows = page.locator('#scan-basket-rows tr.scan-basket-row');
    const basketPks = page.locator('#scan-basket-rows input[name="pk"]');
    await expect(basketRows).toHaveCount(2);
    await expect(page.locator('#scan-basket-count')).toHaveText('2');
    await expect(page.locator('#basket-scanner-count')).toHaveText('2');
    expect((await basketPks.allInputValues()).sort()).toEqual(selectedPks.sort());

    // Re-selecting the automatically chosen compatible target exercises the
    // same change handler that caused #424 without discarding any seeded rows.
    await tenantSelect.evaluate((element, value) => {
      const select = element as HTMLSelectElement;
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    }, selectedTenantValue);
    await expect(basketRows).toHaveCount(2);
    await expect(page.locator('#scan-basket-count')).toHaveText('2');
    await expect(basketPks).toHaveCount(2);

    const submit = page.locator('#scan-basket-submit');
    await expect(submit).toBeEnabled();
    await Promise.all([
      page.waitForURL(/\/jobs\/\d+\/?$/),
      submit.click(),
    ]);
    await expect(page.locator('#job-detail')).toBeVisible();
    await expect(page.locator('h2.page-title')).toHaveText('Bulk Check-in: 2 Assets');
  });

});
