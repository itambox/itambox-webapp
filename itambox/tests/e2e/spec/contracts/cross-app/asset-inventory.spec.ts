import { test, expect } from '../../../fixtures/test';
import {
  activateConfiguredTenant,
  allocationByNote,
  allocationCreatePath,
  availableComponentValue,
  cleanupAllocation,
  seededModularAssetComponentsPath,
  visibleTomSelect,
} from '../../../helpers/component-allocation';

test.describe('cross-app asset and inventory contract', { tag: '@pr' }, () => {
  test('asset-detail component quick-add redirects, reads back, and reverses through inventory', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    const note = `E2E-ISSUE393-quickadd-${runId}`;
    await activateConfiguredTenant(page, api);
    const componentsPath = await seededModularAssetComponentsPath(api);
    await page.goto(componentsPath, { waitUntil: 'domcontentloaded' });
    const assetPath = new URL(componentsPath, 'http://localhost').pathname;
    const button = page.locator(
      'button[hx-get*="component-allocations/add"][hx-get*="_quickadd=1"]',
    );
    await expect(button).toBeVisible();
    await button.click();
    const modal = page.locator('#quick-add-modal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('select[name="from_location"]')).toHaveCount(0);

    const componentValue = await availableComponentValue(page, api);
    await visibleTomSelect(page, 'component', componentValue);
    await modal.locator('input[name="qty"]').fill('1');
    await modal.locator('textarea[name="notes"]').fill(note);

    const responsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === allocationCreatePath,
    );
    await modal.locator('input[type="submit"], button[type="submit"]').first().click();
    const response = await responsePromise;
    expect(response.status()).toBe(204);
    expect(response.headers()['hx-redirect']).toBe(assetPath);
    await page.waitForURL((url) => url.pathname === assetPath, { timeout: 15000 });
    await expect(modal).not.toBeVisible();

    await expect
      .poll(async () => (await allocationByNote(api, note))?.id, { timeout: 15000 })
      .toBeTruthy();
    const allocation = await allocationByNote(api, note);
    if (!allocation) throw new Error(`Component allocation ${note} disappeared after browser creation.`);
    cleanup.add(`component allocation ${note}`, async () => {
      await cleanupAllocation(page, api, String(allocation.id), note);
    });
    expect(allocation.from_location).toBeNull();
    expect(String(allocation.component.id)).toBe(componentValue);
  });
});
