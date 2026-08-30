import { test, expect } from '../../../fixtures/test';
import { createOwnedAsset } from '../../../fixtures/factories/assets';
import { createOwnedAssetHolder } from '../../../fixtures/factories/identity';
import { jsonResponse } from '../../../helpers/api';
import * as path from 'path';

const aggregateStorageState = path.resolve(__dirname, '../../../.auth/aggregate.json');

test.describe('jobs contract', { tag: ['@pr', '@aggregate'] }, () => {
  test.use({ storageState: aggregateStorageState });

  test('owned aggregate check-in basket creates a cancellable tenant-bound job', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    const tenantId = process.env.E2E_TENANT_ID;
    if (!tenantId) throw new Error('E2E_TENANT_ID is required for aggregate job qualification.');
    const holder = await createOwnedAssetHolder(api, cleanup, tenantId, `e2e-job-holder-${runId}`);
    const first = await createOwnedAsset(api, cleanup, tenantId, `${runId}-job-a`);
    const second = await createOwnedAsset(api, cleanup, tenantId, `${runId}-job-b`);
    for (const asset of [first, second]) {
      const checkout = await api.post(`/api/assets/assets/${asset.id}/checkout/`, {
        data: { holder_id: Number(holder.id), notes: `Jobs contract ${runId}` },
      });
      expect(checkout.status(), await checkout.text()).toBe(200);
      cleanup.add(`check in job asset ${asset.assetTag}`, async () => {
        const current = await jsonResponse(
          await api.get(`/api/assets/assets/${asset.id}/`),
          200,
          `job asset ${asset.assetTag} cleanup readback`,
        );
        if (current.assigned_to === null) return;
        const checkin = await api.post(`/api/assets/assets/${asset.id}/checkin/`, {
          data: { notes: `Jobs contract cleanup ${runId}` },
        });
        expect(checkin.status(), await checkin.text()).toBe(200);
      });
    }

    const listPath = `/assets/assets/?switch_all_accessible=1&q=${encodeURIComponent(runId)}`;
    const list = await page.goto(listPath, { waitUntil: 'domcontentloaded' });
    expect(list?.status(), `GET ${listPath}`).toBe(200);
    await expect(page.locator('a[href*="switch_all_accessible=1"].active').first()).toHaveCount(1);
    const rows = page.locator('#object-list-table-container table tbody tr');
    await expect(rows).toHaveCount(2);
    const selectedPks: string[] = [];
    for (const asset of [first, second]) {
      const row = rows.filter({ has: page.getByText(asset.assetTag, { exact: true }) });
      await expect(row).toHaveCount(1);
      const checkbox = row.locator('input[type="checkbox"][name="pk"]');
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
    await expect(tenantSelect).toHaveValue(tenantId);
    const basketPks = page.locator('#scan-basket-rows input[name="pk"]');
    await expect(basketPks).toHaveCount(2);
    expect(
      (await basketPks.evaluateAll((inputs) => inputs.map((input) => (input as HTMLInputElement).value))).sort(),
    ).toEqual(selectedPks.sort());

    const submit = page.locator('#scan-basket-submit');
    await expect(submit).toBeEnabled();
    await Promise.all([
      page.waitForURL(/\/jobs\/\d+\/?$/),
      submit.click(),
    ]);
    const jobMatch = new URL(page.url()).pathname.match(/^\/jobs\/(\d+)\/$/);
    if (!jobMatch) throw new Error(`Bulk check-in did not navigate to a job detail: ${page.url()}`);
    const jobId = jobMatch[1];
    await expect(page.locator('#job-detail')).toBeVisible();
    await expect(page.locator('h2.page-title')).toHaveText('Bulk Check-in: 2 Assets');
    await expect(page.locator('#job-detail .badge')).toHaveText('Pending');

    const cancelPath = `/jobs/${jobId}/cancel/`;
    const cancelResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === cancelPath,
    );
    await page.getByRole('button', { name: /Cancel job$/ }).click();
    expect((await cancelResponse).status(), 'job cancellation response').toBe(302);
    await page.waitForURL((url) => url.pathname === `/jobs/${jobId}/`);
    await expect(page.locator('#job-detail .badge')).toHaveText('Failed');
    await expect(page.getByText(/cancelled/i)).toBeVisible();

    for (const asset of [first, second]) {
      const readback = await jsonResponse(
        await api.get(`/api/assets/assets/${asset.id}/`),
        200,
        `cancelled job asset ${asset.assetTag}`,
      );
      expect(readback.assigned_to).not.toBeNull();
    }
  });
});
