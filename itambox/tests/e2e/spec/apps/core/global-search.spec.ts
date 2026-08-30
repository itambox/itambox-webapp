import { test, expect } from '../../../fixtures/test';
import { createOwnedAsset } from '../../../fixtures/factories/assets';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { jsonResponse } from '../../../helpers/api';

test.describe('core-owned global search', { tag: '@pr' }, () => {
  test('finds an owned asset by exact tag and preserves the result after reload', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const asset = await createOwnedAsset(api, cleanup, tenant.id, runId);
    const searchPath = `/search/?q=${encodeURIComponent(asset.assetTag)}&lookup=iexact`;

    const response = await page.goto(searchPath, { waitUntil: 'domcontentloaded' });
    expect(response?.status(), `GET ${searchPath}`).toBe(200);
    await expect(
      page.getByRole('heading', { name: `Search Results for "${asset.assetTag}"`, exact: true }),
    ).toBeVisible();
    const row = page.locator('tbody tr').filter({ has: page.getByText(asset.assetTag, { exact: true }) });
    await expect(row).toHaveCount(1);
    await expect(row.getByText(asset.assetTag, { exact: true })).toBeVisible();
    const result = row.getByRole('link', { name: asset.name, exact: true });
    await expect(result).toHaveCount(1);
    await expect(result).toHaveAttribute('href', `/assets/assets/${asset.id}/`);
    await expect(page.locator('.badge').filter({ hasText: '1 found' })).toHaveCount(1);

    const readback = await jsonResponse(
      await api.get(`/api/assets/assets/${asset.id}/`),
      200,
      'search asset readback',
    );
    expect(readback).toMatchObject({ id: Number(asset.id), asset_tag: asset.assetTag });

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('link', { name: asset.name, exact: true })).toHaveCount(1);
  });
});
