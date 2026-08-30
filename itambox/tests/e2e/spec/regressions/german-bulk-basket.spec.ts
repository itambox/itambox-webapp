import { test, expect } from '@playwright/test';
import * as path from 'path';

const aggregateStorageState = path.resolve(__dirname, '../../.auth/aggregate.json');

/**
 * Issue #437: the two bulk-basket tenant strings were untranslated in the
 * German JavaScript catalogue, so German sessions rendered English text.
 *
 * This spec activates German for the aggregate operator, seeds a mixed-tenant
 * check-in basket, and verifies the two repaired messages render in German:
 *
 *   1. the no-target-tenant gate toast;
 *   2. the kept-aside notice (with the %(count)s placeholder replaced).
 *
 * Deliberately NOT covered here: the lossless switch-back journey (#438) and
 * the same-tenant re-selection journey (#424 spec in 04-bulk-operations).
 */
test.describe('German bulk basket catalogue (issue #437)', () => {
  test.use({ storageState: aggregateStorageState });

  test('mixed-tenant basket renders the German gate toast and kept-aside notice', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (error) => pageErrors.push(String(error)));

    // Activate German via the language cookie (the mechanism exercised by
    // users/tests/test_i18n.py).
    const baseURL = test.info().project.use.baseURL || 'http://localhost:8000';
    await page.context().addCookies([{ name: 'django_language', value: 'de', url: baseURL }]);

    await page.goto('/assets/assets/?switch_all_accessible=1', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('a[href*="switch_all_accessible=1"].active').first()).toHaveCount(1);

    const rows = page.locator('#object-list-table-container table tbody tr');
    await expect(rows.first()).toBeVisible();
    // Collect rows by tenant. The tenant link href is language-independent;
    // the td[data-label="Tenant"] attribute is translated ("Mandant" in the
    // German UI), so it cannot be used here.
    const rowIndexesByTenant = new Map<string, number[]>();
    const tenantNames = new Map<string, string>();
    for (let index = 0; index < await rows.count(); index += 1) {
      const row = rows.nth(index);
      const tenantLink = row.locator('a[href*="/organization/tenants/"]').first();
      const checkbox = row.locator('input[type="checkbox"][name="pk"]');
      if (await tenantLink.count() === 0 || await checkbox.count() === 0) continue;
      const tenantHref = (await tenantLink.getAttribute('href')) ?? '';
      tenantNames.set(tenantHref, (await tenantLink.innerText()).trim());
      const indexes = rowIndexesByTenant.get(tenantHref) || [];
      indexes.push(index);
      rowIndexesByTenant.set(tenantHref, indexes);
    }
    const tenants = [...rowIndexesByTenant.entries()];
    if (tenants.length < 2) {
      throw new Error('The full E2E seed must expose assets from at least two accessible tenants.');
    }
    const [tenantAHref, indexesA] = tenants[0];
    const [tenantBHref, indexesB] = tenants[1];
    const tenantA = tenantNames.get(tenantAHref) ?? tenantAHref;
    const tenantB = tenantNames.get(tenantBHref) ?? tenantBHref;

    // Seed the basket with one asset from tenant A and one from tenant B so
    // the target-tenant selector stays empty (mixed seeds never auto-select).
    for (const index of [indexesA[0], indexesB[0]]) {
      await rows.nth(index).locator('input[type="checkbox"][name="pk"]').check();
    }

    // The seed button labels are translated ("Check-in" → "Einchecken"), so
    // select the check-in variant via its language-independent data-scan-url.
    const checkinButton = page.locator('.btn-bulk-scan-seed[data-scan-url*="checkin/scan"]');
    await expect(checkinButton).toBeEnabled();
    await Promise.all([
      page.waitForURL(/\/assets\/assets\/checkin\/scan\//),
      checkinButton.click(),
    ]);

    const tenantSelect = page.locator('#scan-basket-form select[name="tenant"]');
    await expect(tenantSelect).toHaveCount(1);
    // Mixed-tenant seeds never auto-select a tenant, so the active basket is
    // empty (count 0) until the operator picks a target — by design (#424).
    await expect.poll(() => tenantSelect.inputValue()).toBe('');
    await expect(page.locator('#scan-basket-count')).toHaveText('0');

    // (1) No-target-tenant gate renders the German toast. The UI keeps the
    // scan input disabled until a tenant is chosen; re-enable it just long
    // enough to exercise the real keydown listener and guard branch.
    await page.locator('#scan-basket-input').evaluate((input) => {
      const el = input as HTMLInputElement;
      el.disabled = false;
      el.value = 'NO-SUCH-ASSET';
      el.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    });
    await expect(
      page.locator('.toast.show').getByText('Wählen Sie vor dem Scannen einen Ziel-Mandanten aus.'),
    ).toBeVisible();

    // (2) Select tenant A: its seeded row becomes the active basket and the
    // tenant-B asset is kept aside — the German notice shows the substituted
    // count (1), never the raw %(count)s.
    const tenantAValue = await tenantSelect.evaluate((select, name) => {
      const option = Array.from((select as HTMLSelectElement).options).find(
        (candidate) => candidate.textContent?.trim() === name,
      );
      if (!option) throw new Error(`No target-tenant option for ${name}`);
      return option.value;
    }, tenantA);

    await tenantSelect.evaluate((select, value) => {
      const el = select as HTMLSelectElement;
      el.value = value;
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }, tenantAValue);

    const keptAside = page.locator('#scan-basket-kept-aside');
    await expect(page.locator('#scan-basket-count')).toHaveText('1');
    await expect(keptAside).toBeVisible();
    await expect(keptAside).toContainText('1 Asset aus einem anderen Mandanten wird zurückgehalten');
    await expect(keptAside).toContainText('Wechseln Sie den Ziel-Mandanten, um es anzuzeigen');
    await expect(keptAside).not.toContainText('%(count)s');
    // The tenant-B basket row is not rendered for the active tenant A.
    await expect(page.locator('#scan-basket-rows tr.scan-basket-row')).toHaveCount(1);

    expect(pageErrors).toEqual([]);
  });
});
