import { test, expect } from '../../../fixtures/test';
import { createOwnedAsset } from '../../../fixtures/factories/assets';
import { createOwnedAssetHolder } from '../../../fixtures/factories/identity';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('assets-owned lifecycle', { tag: '@pr' }, () => {
  test('creates owned prerequisites and checks an asset out and back in', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const holder = await createOwnedAssetHolder(api, cleanup, tenant.id, `e2e-holder-${runId}`);
    const asset = await createOwnedAsset(api, cleanup, tenant.id, runId);
    const detailPath = `/assets/assets/${asset.id}/`;

    const initial = await page.goto(detailPath, { waitUntil: 'domcontentloaded' });
    expect(initial?.status(), `GET ${detailPath}`).toBe(200);
    await expect(page.getByText(asset.assetTag, { exact: true })).toBeVisible();

    const checkoutOpen = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'GET'
        && url.pathname === `/assets/assets/${asset.id}/checkout/`;
    });
    await expect(page.getByTestId('asset-checkout-action')).toHaveCount(1);
    await page.getByTestId('asset-checkout-action').click();
    expect((await checkoutOpen).status()).toBe(200);

    const checkoutForm = page.locator('#asset-checkout-form');
    await expect(checkoutForm).toBeVisible();
    await selectTomOption(checkoutForm, 'target_type', 'holder');
    await selectTomOption(checkoutForm, 'assigned_holder', holder.id);
    const checkoutWrite = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST'
        && url.pathname === `/assets/assets/${asset.id}/checkout/`;
    });
    await checkoutForm.getByRole('button', { name: 'Assign Asset', exact: true }).click();
    expect((await checkoutWrite).status()).toBe(204);
    await page.waitForURL((url) => url.pathname === detailPath);

    const checkedOut = await jsonResponse(
      await api.get(`/api/assets/assets/${asset.id}/`),
      200,
      'asset checkout readback',
    );
    const assignment = checkedOut.assigned_to;
    expect(assignment).toMatchObject({ id: Number(holder.id), type: 'assetholder' });
    await expect(page.getByTestId('asset-assignment-state')).toContainText('E2E Holder');

    const checkinOpen = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'GET'
        && url.pathname === `/assets/assets/${asset.id}/checkin/`;
    });
    await expect(page.getByTestId('asset-checkin-action')).toHaveCount(1);
    await page.getByTestId('asset-checkin-action').click();
    expect((await checkinOpen).status()).toBe(200);

    const checkinForm = page.locator('#asset-checkin-form');
    await expect(checkinForm).toBeVisible();
    const checkinWrite = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST'
        && url.pathname === `/assets/assets/${asset.id}/checkin/`;
    });
    await checkinForm.getByRole('button', { name: 'Check In', exact: true }).click();
    expect((await checkinWrite).status()).toBe(204);
    await page.waitForURL((url) => url.pathname === detailPath);

    const checkedIn = await jsonResponse(
      await api.get(`/api/assets/assets/${asset.id}/`),
      200,
      'asset checkin readback',
    );
    expect(checkedIn.assigned_to).toBeNull();
    await expect(page.getByTestId('asset-assignment-state')).toContainText('No active assignment');
  });
});
