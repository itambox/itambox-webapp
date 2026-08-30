import { expect, type APIRequestContext, type Page } from '@playwright/test';

export const allocationCreatePath = '/inventory/component-allocations/add/';
const allocationListPath = '/inventory/component-allocations/';
let configuredTenantId: string | undefined;

async function responseRows(request: APIRequestContext, path: string): Promise<Record<string, any>[]> {
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

export async function activateConfiguredTenant(page: Page, request: APIRequestContext): Promise<void> {
  const tenantSlug = process.env.E2E_TENANT_SLUG;
  if (!tenantSlug) throw new Error('E2E_TENANT_SLUG is required for component allocation tests.');
  const tenants = await responseRows(request, '/api/organization/tenants/?limit=100');
  const tenant = tenants.find((row) => row.slug === tenantSlug);
  expect(
    tenant,
    `E2E tenant ${tenantSlug} must be visible to the authenticated operator`,
  ).toBeDefined();
  configuredTenantId = String(tenant!.id);
  await page.goto(`/?switch_tenant=${tenant!.id}`, { waitUntil: 'networkidle' });
}

export async function allocationByNote(
  request: APIRequestContext,
  note: string,
): Promise<Record<string, any> | undefined> {
  const rows = await responseRows(request, '/api/inventory/component-allocations/?q=E2E-ISSUE393-');
  return rows.find((row) => row.notes === note);
}

export async function availableComponentValue(page: Page, request: APIRequestContext): Promise<string> {
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
  if (!value) throw new Error('The E2E seed must expose an available Component in the active tenant.');
  return value;
}

export async function visibleTomSelect(
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
  if (settleDropdown) await select.evaluate((element) => (element as any).tomselect.close());
  await expect(select).toHaveValue(value);
}

export async function visibleClear(page: Page, fieldName: string): Promise<void> {
  const select = page.locator(`select[data-tom-select][name="${fieldName}"]`);
  const wrapper = select.locator('xpath=following-sibling::div[contains(@class,"ts-wrapper")][1]');
  await wrapper.locator('.clear-button').click();
  await expect(select).toHaveValue('');
}

export async function cleanupAllocation(
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
    (response) => response.request().method() === 'POST' && new URL(response.url()).pathname === checkinPath,
  );
  page.once('dialog', (dialog) => dialog.accept());
  await button.click();
  expect((await responsePromise).status()).toBe(204);
  expect(await allocationByNote(request, note)).toBeUndefined();
}

export async function seededModularAssetComponentsPath(request: APIRequestContext): Promise<string> {
  const roles = await responseRows(request, '/api/assets/asset-roles/?limit=100');
  const modularRoleIds = new Set(
    roles
      .filter((row) => /server|modular|workstation|hypervisor/i.test(String(row.slug || '')))
      .map((row) => String(row.id)),
  );
  expect(modularRoleIds.size, 'The E2E seed must expose a component-capable asset role.').toBeGreaterThan(0);
  const assets = await responseRows(request, '/api/assets/assets/?limit=100');
  const asset = assets.find((row) => {
    const role = row.asset_role;
    return role !== null
      && typeof role === 'object'
      && modularRoleIds.has(String((role as Record<string, unknown>).id));
  });
  if (!asset || (typeof asset.id !== 'string' && typeof asset.id !== 'number')) {
    throw new Error('The E2E seed must expose one asset with a component-capable role.');
  }
  return `/assets/assets/${asset.id}/?tab=components`;
}
