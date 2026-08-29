import { test, expect } from '../../../fixtures/test';
import type { APIRequestContext, Page } from '@playwright/test';

const allocationCreatePath = '/inventory/component-allocations/add/';
const allocationListPath = '/inventory/component-allocations/';
let configuredTenantId: string | undefined;

async function responseRows(
  request: APIRequestContext,
  path: string,
): Promise<Record<string, any>[]> {
  const scopedPath = new URL(path, 'http://itambox.local');
  if (configuredTenantId) scopedPath.searchParams.set('switch_tenant', configuredTenantId);
  const response = await request.get(`${scopedPath.pathname}${scopedPath.search}`);
  expect(response.status(), await response.text()).toBe(200);
  const payload = await response.json();
  if (Array.isArray(payload)) return payload;
  if (!payload || typeof payload !== 'object' || !Array.isArray(payload.results)) {
    throw new Error(`Expected a result list from ${path}.`);
  }
  return payload.results as Record<string, any>[];
}

async function activateConfiguredTenant(page: Page, request: APIRequestContext): Promise<void> {
  const tenantSlug = process.env.E2E_TENANT_SLUG;
  if (!tenantSlug) throw new Error('E2E_TENANT_SLUG is required for component allocation tests.');
  const tenants = await responseRows(request, '/api/organization/tenants/?limit=100');
  const tenant = tenants.find((row) => row.slug === tenantSlug);
  expect(
    tenant,
    `E2E tenant ${tenantSlug} must be visible to the authenticated operator`,
  ).toBeDefined();
  configuredTenantId = String(tenant!.id);
  const apiSwitch = await request.get(`/?switch_tenant=${tenant!.id}`);
  expect(apiSwitch.status(), await apiSwitch.text()).toBe(200);
  await page.goto(`/?switch_tenant=${tenant!.id}`, { waitUntil: 'networkidle' });
}

async function allocationByNote(
  request: APIRequestContext,
  note: string,
): Promise<Record<string, any> | undefined> {
  const rows = await responseRows(request, '/api/inventory/component-allocations/?q=E2E-ISSUE393-');
  return rows.find((row) => row.notes === note);
}

async function availableComponentValue(page: Page, request: APIRequestContext): Promise<string> {
  const options = await page.locator('select[name="component"] option').evaluateAll((elements) =>
    elements
      .map((element) => ({
        value: (element as HTMLOptionElement).value,
        label: (element.textContent || '').trim(),
      }))
      .filter((option) => option.value),
  );
  const rows = await responseRows(request, '/api/inventory/components/?limit=100');
  const availableIds = new Set(
    rows.filter((row) => Number(row.available_stock) > 0).map((row) => String(row.id)),
  );
  const value = options.find((option) => availableIds.has(option.value))?.value;
  if (!value)
    throw new Error('The E2E seed must expose an available Component in the active tenant.');
  return value;
}

async function visibleTomSelect(
  page: Page,
  fieldName: string,
  value: string,
  settleDropdown = true,
): Promise<void> {
  const select = page.locator(`select[data-tom-select][name="${fieldName}"]`);
  await expect(select).toHaveCount(1);
  const label = (await select.locator(`option[value="${value}"]`).textContent())?.trim();
  if (!label) throw new Error(`Tom Select option ${fieldName}=${value} is missing.`);

  const wrapper = select.locator('xpath=following-sibling::div[contains(@class,"ts-wrapper")][1]');
  if (settleDropdown) await wrapper.locator('.ts-control').click();
  else await wrapper.locator('.ts-control').click({ force: true });
  const input = wrapper.locator('.dropdown-input:visible, .ts-control input:visible').first();
  await input.fill(label);
  const visibleOption = wrapper.locator(`.ts-dropdown .option[data-value="${value}"]`);
  if (settleDropdown) await visibleOption.click();
  else await visibleOption.click({ force: true });
  if (settleDropdown) {
    // The value change above is a visible option click. close() only settles
    // dropdown chrome so the next visible control can receive pointer input.
    await select.evaluate((element) => (element as any).tomselect.close());
  }
  await expect(select).toHaveValue(value);
}

async function visibleClear(page: Page, fieldName: string): Promise<void> {
  const select = page.locator(`select[data-tom-select][name="${fieldName}"]`);
  const wrapper = select.locator('xpath=following-sibling::div[contains(@class,"ts-wrapper")][1]');
  await wrapper.locator('.clear-button').click();
  await expect(select).toHaveValue('');
}

async function cleanupAllocation(
  page: Page,
  request: APIRequestContext,
  allocationId: string,
  note: string,
): Promise<void> {
  await page.goto(`${allocationListPath}?q=E2E-ISSUE393-`, { waitUntil: 'networkidle' });
  const checkinPath = `/inventory/components/allocations/${allocationId}/checkin/`;
  const button = page.locator(`button[hx-post="${checkinPath}"]`);
  await expect(button).toBeVisible();
  const responsePromise = page.waitForResponse(
    (response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === checkinPath,
  );
  page.once('dialog', (dialog) => dialog.accept());
  await button.click();
  expect((await responsePromise).status()).toBe(204);
  expect(await allocationByNote(request, note)).toBeUndefined();
}

async function firstModularAsset(page: Page): Promise<string> {
  await page.goto('/assets/assets/?per_page=100', { waitUntil: 'networkidle' });
  const hrefs = await page
    .locator('a[href^="/assets/assets/"]')
    .evaluateAll((links) => [
      ...new Set(
        links
          .map((link) => (link as HTMLAnchorElement).getAttribute('href'))
          .filter((href): href is string =>
            Boolean(href && /^\/assets\/assets\/\d+\/$/.test(href)),
          ),
      ),
    ]);
  for (const href of hrefs) {
    const componentsPath = `${href}?tab=components`;
    await page.goto(componentsPath, { waitUntil: 'domcontentloaded' });
    const button = page.locator(
      'button[hx-get*="component-allocations/add"][hx-get*="_quickadd=1"]',
    );
    if ((await button.count()) > 0 && (await button.isVisible())) return componentsPath;
  }
  throw new Error(
    'The E2E seed must expose one modular asset with the Add Component quick-add action.',
  );
}

test.describe('Issue #393 Component Allocation observability', () => {
  test.setTimeout(120_000);

  test('full-page create is target-only and clear/replace matches POST and readback', async ({
    page,
    request,
    cleanup,
    runId,
  }) => {
    const invalidNote = `E2E-ISSUE393-invalid-${runId}`;
    const successNote = `E2E-ISSUE393-success-${runId}`;

    await activateConfiguredTenant(page, request);
    await page.goto(allocationCreatePath, { waitUntil: 'networkidle' });
    await expect(page.locator('select[name="from_location"]')).toHaveCount(0);
    await expect(page.getByText('From Location', { exact: true })).toHaveCount(0);

    const componentValue = await availableComponentValue(page, request);
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
    expect(await allocationByNote(request, invalidNote)).toBeUndefined();

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

    const allocation = await allocationByNote(request, successNote);
    expect(allocation).toBeDefined();
    expect(allocation!.from_location).toBeNull();
    expect(String(allocation!.assigned_holder.id)).toBe(holderValues[0]);
    cleanup.add(`component allocation ${successNote}`, async () => {
      await cleanupAllocation(page, request, String(allocation!.id), successNote);
    });
  });

  test('asset-detail quick-add returns HX-Redirect and leaves no stale modal or HTMX errors', async ({
    page,
    request,
    cleanup,
    runId,
  }) => {
    const note = `E2E-ISSUE393-quickadd-${runId}`;
    await activateConfiguredTenant(page, request);
    const componentsPath = await firstModularAsset(page);
    const assetPath = new URL(componentsPath, 'http://localhost').pathname;
    const button = page.locator(
      'button[hx-get*="component-allocations/add"][hx-get*="_quickadd=1"]',
    );
    await button.click();
    const modal = page.locator('#quick-add-modal');
    await expect(modal).toBeVisible();
    await expect(modal.locator('select[name="from_location"]')).toHaveCount(0);

    const componentValue = await availableComponentValue(page, request);
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
      .poll(async () => (await allocationByNote(request, note))?.id, { timeout: 15000 })
      .toBeTruthy();
    const allocation = await allocationByNote(request, note);
    expect(allocation!.from_location).toBeNull();
    cleanup.add(`component allocation ${note}`, async () => {
      await cleanupAllocation(page, request, String(allocation!.id), note);
    });
  });
});
