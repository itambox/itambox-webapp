import { test, expect } from '../../fixtures/test';
import { createOwnedAsset } from '../../fixtures/factories/assets';
import { getJsonRows } from '../../helpers/api';
import { selectTomOption } from '../../helpers/forms';
import * as path from 'path';

const aggregateStorageState = path.resolve(__dirname, '../../.auth/aggregate.json');
const TENANT_A_SLUG = 'helix-rnd';
const TENANT_B_SLUG = 'helix-mfg';

type AccessibleTenant = {
  id: string;
  name: string;
  slug: string;
};

function accessibleTenant(rows: Record<string, unknown>[], slug: string): AccessibleTenant {
  const matches = rows.filter((row) => row.slug === slug);
  if (matches.length !== 1) {
    throw new Error(`Aggregate E2E principal must expose exactly one accessible tenant ${slug}.`);
  }
  const rawId = matches[0].id;
  if (typeof rawId !== 'string' && typeof rawId !== 'number') {
    throw new Error(`Accessible tenant ${slug} has no usable ID.`);
  }
  const rawName = matches[0].name;
  if (typeof rawName !== 'string' || !rawName.trim()) {
    throw new Error(`Accessible tenant ${slug} has no usable name.`);
  }
  return { id: String(rawId), name: rawName, slug };
}

test.describe('aggregate bulk basket switch-back regression (issue #438)', { tag: ['@pr', '@aggregate'] }, () => {
  test.use({ storageState: aggregateStorageState });

  test('restores tenant A basket rows and proceeds after A to B to A, then submits only A', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    // The seeded aggregate principal (lars.eklund) has a single MSP membership
    // with an all-managed RoleGrant. Prove both target tenants through the real
    // authenticated REST projection before creating owned records for them.
    const aggregatePage = await page.goto('/assets/assets/?switch_all_accessible=1', { waitUntil: 'domcontentloaded' });
    expect(aggregatePage?.status(), 'aggregate asset list response').toBe(200);
    await expect(page.locator('a[href*="switch_all_accessible=1"].active').first()).toHaveCount(1);

    const accessibleTenants = await getJsonRows(
      page.request,
      '/api/organization/tenants/?limit=100',
      'aggregate tenant visibility',
    );
    const tenantA = accessibleTenant(accessibleTenants, TENANT_A_SLUG);
    const tenantB = accessibleTenant(accessibleTenants, TENANT_B_SLUG);

    // The setup API uses the existing E2E superuser token only to provision
    // disposable records. Browser authorization is still exercised by the
    // aggregate lars.eklund session above and throughout the journey. The
    // pending job is cancelled before worker execution, so the factory's
    // default cleanup removes the still-live assets after the test.
    const assetA1 = await createOwnedAsset(api, cleanup, tenantA.id, `${runId}-a1`, {
      tagScope: 'issue438',
    });
    const assetA2 = await createOwnedAsset(api, cleanup, tenantA.id, `${runId}-a2`, {
      tagScope: 'issue438',
    });
    const assetB1 = await createOwnedAsset(api, cleanup, tenantB.id, `${runId}-b1`, {
      tagScope: 'issue438',
    });

    // Search by the unique per-run name, then select each owned row by its exact
    // stable asset tag rather than relying on table ordering or seeded IDs.
    const listPath = `/assets/assets/?switch_all_accessible=1&q=${encodeURIComponent(runId)}`;
    const listResponse = await page.goto(listPath, { waitUntil: 'domcontentloaded' });
    expect(listResponse?.status(), `GET ${listPath}`).toBe(200);
    await expect(page.locator('a[href*="switch_all_accessible=1"].active').first()).toHaveCount(1);

    const listRows = page.locator('#object-list-table-container table tbody tr');
    await expect(listRows).toHaveCount(3);
    for (const asset of [assetA1, assetA2, assetB1]) {
      const row = listRows.filter({ has: page.getByText(asset.assetTag, { exact: true }) });
      await expect(row, `asset-list row for ${asset.assetTag}`).toHaveCount(1);
      await row.locator('input[type="checkbox"][name="pk"]').check();
    }

    const disposeButton = page.locator('.btn-bulk-scan-seed[data-scan-url*="dispose/scan"]');
    await expect(disposeButton).toHaveCount(1);
    await expect(disposeButton).toBeEnabled();
    await Promise.all([
      page.waitForURL(/\/assets\/assets\/dispose\/scan\//),
      disposeButton.click(),
    ]);

    const tenantSelect = page.locator('#scan-basket-form select[name="tenant"]');
    const basketRows = page.locator('#scan-basket-rows tr.scan-basket-row');
    const basketCount = page.locator('#scan-basket-count');
    const keptAside = page.locator('#scan-basket-kept-aside');
    const submit = page.locator('#scan-basket-submit');
    await expect(tenantSelect).toHaveCount(1);

    // A genuinely mixed seed has no implicit target tenant and therefore no
    // active rows until the operator explicitly chooses one.
    await expect(tenantSelect).toHaveValue('');
    await expect(basketRows).toHaveCount(0);
    await expect(basketCount).toHaveText('0');
    await expect(keptAside).toBeHidden();
    await expect(submit).toBeDisabled();

    const rowFor = (assetTag: string) => basketRows.filter({ has: page.getByText(assetTag, { exact: true }) });
    const expectActiveAssets = async (expected: string[], absent: string[]) => {
      await expect(basketRows).toHaveCount(expected.length);
      for (const assetTag of expected) await expect(rowFor(assetTag)).toHaveCount(1);
      for (const assetTag of absent) await expect(rowFor(assetTag)).toHaveCount(0);
    };
    const expectProceeds = async (assetTag: string, value: string) => {
      const row = rowFor(assetTag);
      await expect(row).toHaveCount(1);
      await expect(row.locator('input[data-field="proceeds"]')).toHaveValue(value);
    };

    // Select tenant A through the actual Tom Select-backed target control.
    await selectTomOption(page, 'tenant', tenantA.id);
    await expect(tenantSelect).toHaveValue(tenantA.id);
    await expectActiveAssets([assetA1.assetTag, assetA2.assetTag], [assetB1.assetTag]);
    await expect(basketCount).toHaveText('2');
    await expect(keptAside).toBeVisible();
    await expect(keptAside).toContainText('1 asset from another tenant is kept aside');
    await expect(submit).toBeEnabled();

    // Distinct values exercise the real per-row inputs and make both identity
    // preservation and accidental value swapping observable after switching.
    await rowFor(assetA1.assetTag).locator('input[data-field="proceeds"]').fill('111.11');
    await rowFor(assetA2.assetTag).locator('input[data-field="proceeds"]').fill('222.22');
    await expectProceeds(assetA1.assetTag, '111.11');
    await expectProceeds(assetA2.assetTag, '222.22');

    // Switching to B must render only B's sub-basket while retaining A's rows
    // and proceeds in the inactive per-tenant basket.
    await selectTomOption(page, 'tenant', tenantB.id);
    await expect(tenantSelect).toHaveValue(tenantB.id);
    await expectActiveAssets([assetB1.assetTag], [assetA1.assetTag, assetA2.assetTag]);
    await expect(basketCount).toHaveText('1');
    await expect(keptAside).toBeVisible();
    await expect(keptAside).toContainText('2 assets from another tenant are kept aside');
    await expect(submit).toBeEnabled();
    await rowFor(assetB1.assetTag).locator('input[data-field="proceeds"]').fill('333.33');
    await expectProceeds(assetB1.assetTag, '333.33');

    // Core #438 contract: returning to A restores exact row identity, count,
    // kept-aside state, and the original proceeds on the original rows.
    await selectTomOption(page, 'tenant', tenantA.id);
    await expect(tenantSelect).toHaveValue(tenantA.id);
    await expectActiveAssets([assetA1.assetTag, assetA2.assetTag], [assetB1.assetTag]);
    await expect(basketCount).toHaveText('2');
    await expect(keptAside).toBeVisible();
    await expect(keptAside).toContainText('1 asset from another tenant is kept aside');
    await expectProceeds(assetA1.assetTag, '111.11');
    await expectProceeds(assetA2.assetTag, '222.22');
    await expect(submit).toBeEnabled();

    // Disposal uses a confirmation modal. Capture the actual browser POST so
    // the resulting job boundary is proven with exact PKs and per-row values,
    // not merely by the visible row count.
    await submit.click();
    const confirmation = page.locator('#bulk-dispose-confirm-modal');
    await expect(confirmation).toBeVisible();
    await expect(page.locator('#scan-basket-dispose-confirm-text')).toContainText('2 assets');

    const disposeRequest = page.waitForRequest((request) => {
      const url = new URL(request.url());
      return request.method() === 'POST' && url.pathname === '/assets/assets/bulk-dispose/';
    });
    const jobNavigation = page.waitForURL(/\/jobs\/\d+\/?$/);
    await page.locator('#scan-basket-confirm-submit').click();
    const request = await disposeRequest;
    await jobNavigation;

    const posted = new URLSearchParams(request.postData() || '');
    expect(posted.get('tenant')).toBe(tenantA.id);
    expect(posted.getAll('pk').sort()).toEqual([assetA1.id, assetA2.id].sort());
    expect(posted.getAll('pk')).not.toContain(assetB1.id);
    expect(posted.get(`proceeds_${assetA1.id}`)).toBe('111.11');
    expect(posted.get(`proceeds_${assetA2.id}`)).toBe('222.22');
    expect(posted.get(`proceeds_${assetB1.id}`)).toBeNull();

    // The current E2E environment intentionally has no qcluster worker, so a
    // newly created job remains Pending. The job detail route/title proves that
    // the real disposal submit created the tenant-bound bulk operation with the
    // exact A-only target count. The supported cancel action prevents execution
    // before the factory cleanup runs; submission navigates away, so the
    // originating basket context is intentionally not asserted after submit.
    const jobMatch = new URL(page.url()).pathname.match(/^\/jobs\/(\d+)\/$/);
    if (!jobMatch) throw new Error(`Bulk disposal did not navigate to a job detail: ${page.url()}`);
    const jobId = jobMatch[1];
    await expect(page.locator('#job-detail')).toBeVisible();
    await expect(page.locator('h2.page-title')).toHaveText('Bulk Disposal: 2 Assets');
    await expect(page.locator('#job-detail .badge')).toHaveText('Pending');

    const cancelPath = `/jobs/${jobId}/cancel/`;
    const cancelResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === cancelPath,
    );
    await page.getByRole('button', { name: /Cancel job$/ }).click();
    expect((await cancelResponse).status(), 'job cancellation response').toBe(302);
    await page.waitForURL((url) => url.pathname === `/jobs/${jobId}/`);
    await expect(page.locator('#job-detail .badge')).toHaveText('Failed');
  });
});
