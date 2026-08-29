import { test, expect } from '../../../fixtures/test';
import {
  activateConfiguredTenant,
  allocationByNote,
  allocationCreatePath,
  availableComponentValue,
  cleanupAllocation,
  visibleClear,
  visibleTomSelect,
} from '../../../helpers/component-allocation';

test.describe('inventory-owned component allocation', { tag: '@pr' }, () => {
  test('full-page create is target-only and clear/replace matches POST and readback', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    const invalidNote = `E2E-ISSUE393-invalid-${runId}`;
    const successNote = `E2E-ISSUE393-success-${runId}`;

    await activateConfiguredTenant(page, api);
    await page.goto(allocationCreatePath, { waitUntil: 'networkidle' });
    await expect(page.locator('select[name="from_location"]')).toHaveCount(0);
    await expect(page.getByText('From Location', { exact: true })).toHaveCount(0);

    const componentValue = await availableComponentValue(page, api);
    const holderValues = await page
      .locator('select[name="assigned_holder"] option')
      .evaluateAll((options) =>
        options.map((option) => (option as HTMLOptionElement).value).filter(Boolean),
      );
    expect(holderValues.length).toBeGreaterThanOrEqual(2);

    await visibleTomSelect(page, 'component', componentValue, false);
    await visibleTomSelect(page, 'assigned_holder', holderValues[0], false);
    await visibleClear(page, 'assigned_holder');
    await page.locator('input[name="qty"]').fill('1');
    await page.locator('textarea[name="notes"]').fill(invalidNote);

    const form = page.locator('select[name="component"]').locator('xpath=ancestor::form[1]');
    const invalidResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === allocationCreatePath,
    );
    await form.locator('input[type="submit"], button[type="submit"]').first().click();
    const invalidResponse = await invalidResponsePromise;
    expect(invalidResponse.status()).toBe(200);
    const invalidPost = new URLSearchParams(invalidResponse.request().postData() || '');
    expect(invalidPost.get('assigned_holder')).toBe('');
    expect(invalidPost.has('from_location')).toBe(false);
    await expect(
      page.getByText(/select either an Asset Holder, a Location, or an Asset/i),
    ).toBeVisible();
    expect(await allocationByNote(api, invalidNote)).toBeUndefined();

    await visibleTomSelect(page, 'assigned_holder', holderValues[0], false);
    await visibleTomSelect(page, 'assigned_holder', holderValues[1], false);
    await visibleTomSelect(page, 'assigned_holder', holderValues[0], false);
    await page.locator('textarea[name="notes"]').fill(successNote);

    const successResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname === allocationCreatePath,
    );
    await form.locator('input[type="submit"], button[type="submit"]').first().click();
    const successResponse = await successResponsePromise;
    expect(successResponse.status()).toBe(302);
    const successPost = new URLSearchParams(successResponse.request().postData() || '');
    expect(successPost.get('assigned_holder')).toBe(holderValues[0]);
    expect(successPost.has('from_location')).toBe(false);

    const allocation = await allocationByNote(api, successNote);
    expect(allocation).toBeDefined();
    cleanup.add(`component allocation ${successNote}`, async () => {
      await cleanupAllocation(page, api, String(allocation!.id), successNote);
    });
    expect(allocation!.from_location).toBeNull();
    expect(String(allocation!.assigned_holder.id)).toBe(holderValues[0]);
  });
});
