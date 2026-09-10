import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { deleteOwnedResource, getJsonRows, jsonResponse, type JsonObject } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';
import { createOwnedAsset } from '../../../fixtures/factories/assets';

function seededAssetType(rows: JsonObject[], slug: string): { id: string; slug: string } {
  const row = rows.find((candidate) => candidate.slug === slug);
  if (!row) throw new Error(`The E2E seed must expose asset type ${slug}.`);
  if (typeof row.id !== 'string' && typeof row.id !== 'number') {
    throw new Error(`Seeded asset type ${slug} has no usable ID.`);
  }
  return { id: String(row.id), slug };
}

function primaryKey(row: JsonObject, label: string): string {
  if (typeof row.id !== 'string' && typeof row.id !== 'number') {
    throw new Error(`${label} has no usable ID.`);
  }
  return String(row.id);
}


test('owned-asset factory persists an explicitly required false', { tag: '@pr' }, async ({
  api, activeTenant, cleanup, runId,
}) => {
  const tenant = requireActiveTenant(activeTenant);
  const owned = await createOwnedAsset(api, cleanup, tenant.id, runId);
  const saved = await jsonResponse(await api.get(`/api/assets/assets/${owned.id}/`), 200, 'required false readback');
  expect(saved.specifications).toMatchObject({ e2e_required_boolean: false });
  const rejected = await api.post('/api/assets/assets/', {
    data: {
      name: `${owned.name} missing required value`,
      asset_tag: `${owned.assetTag.slice(0, 42)}-MISSING`,
      asset_type_id: (saved.asset_type as JsonObject).id,
      tenant_id: tenant.id,
    },
  });
  expect(rejected.status()).toBe(400);
  expect(await rejected.json()).toHaveProperty('e2e_required_boolean');
});

test.describe('assets-owned specification editor', { tag: '@operator' }, () => {
  test('preserves a Type A draft through Type B and back to Type A', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const [assetTypes, statuses] = await Promise.all([
      getJsonRows(api, '/api/assets/asset-types/?limit=100', 'specification asset types'),
      getJsonRows(api, '/api/assets/status-labels/?limit=100', 'specification asset statuses'),
    ]);
    const typeA = seededAssetType(assetTypes, 'dell-latitude-5550');
    const typeB = seededAssetType(assetTypes, 'cisco-catalyst-9300');
    const deployableStatus = statuses.find((row) => row.type === 'deployable');
    if (!deployableStatus) throw new Error('The E2E seed must expose a deployable asset status.');
    const isolationAssetId = process.env.E2E_ISOLATION_ASSET_ID;
    if (!isolationAssetId) throw new Error('The E2E setup must expose a foreign-tenant asset ID.');
    const assetTag = `E2E-SPEC-${runId}`.toUpperCase().replace(/[^A-Z0-9-]/g, '-').slice(0, 50);
    const assetName = `E2E specification journey ${runId}`;

    const foreignAssetPath = `/assets/assets/${encodeURIComponent(isolationAssetId)}/`;
    // Use the same operator browser session without rendering Django's DEBUG error page.
    const deniedForeignAsset = await page.request.get(foreignAssetPath);
    expect(deniedForeignAsset.status(), `foreign-tenant GET ${foreignAssetPath}`).toBe(404);

    const created = await jsonResponse(
      await api.post('/api/assets/assets/', {
        data: {
          name: assetName,
          asset_tag: assetTag,
          asset_type_id: typeA.id,
          status_id: deployableStatus.id,
          tenant_id: tenant.id,
          specification_patch: {
            set: { e2e_required_boolean: false },
            clear: [],
          },
        },
      }),
      201,
      'create specification journey asset',
    );
    const assetId = primaryKey(created, 'created specification journey asset');
    expect(created.tenant).toMatchObject({ id: Number(tenant.id), slug: tenant.slug });
    cleanup.add(`specification journey asset ${assetTag}`, async () => {
      const current = await api.get(`/api/assets/assets/${assetId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(api, `/api/assets/assets/${assetId}/`, `delete specification journey asset ${assetTag}`);
    });

    const editPath = `/assets/assets/${assetId}/edit/`;
    const initialPage = await page.goto(editPath, { waitUntil: 'domcontentloaded' });
    expect(initialPage?.status(), `GET ${editPath}`).toBe(200);
    const form = page.locator('#asset-specification-form[data-specification-editor]');
    await expect(form).toHaveCount(1);
    await expect(form.locator('select[name="asset_type"]')).toHaveValue(typeA.id);
    await expect(form.locator('[name="tenant"]')).toHaveValue(tenant.id);

    const requiredBoolean = form.locator('[data-specification-key="e2e_required_boolean"]');
    await expect(requiredBoolean).toHaveCount(1);
    expect(await requiredBoolean.evaluate((element) => (element as HTMLSelectElement).required)).toBe(true);
    await requiredBoolean.selectOption('false');
    await expect(requiredBoolean).toHaveValue('false');

    const draftProcessor = `Draft CPU ${runId}`;
    const processor = form.locator('[data-specification-key="processor_model"]');
    await expect(processor).toHaveCount(1);
    await processor.fill(draftProcessor);
    await expect(form.locator('select[name="cf_processor_model__presence"]')).toHaveValue('value');

    const toTypeB = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST' && url.pathname === editPath;
    });
    await selectTomOption(form, 'asset_type', typeB.id);
    expect((await toTypeB).status(), 'Type A -> Type B draft reload').toBe(200);

    const typeBForm = page.locator('#asset-specification-form[data-specification-editor]');
    await expect(typeBForm.locator('select[name="asset_type"]')).toHaveValue(typeB.id);
    await expect(typeBForm.locator('[name="tenant"]')).toHaveValue(tenant.id);
    await expect(typeBForm.locator('[data-specification-key="processor_model"]')).toHaveCount(0);
    const hiddenProcessorDraft = typeBForm.locator(
      'input[type="hidden"][name="specification_draft__cf_processor_model"]',
    );
    await expect(hiddenProcessorDraft).toHaveCount(1);
    await expect(hiddenProcessorDraft).toHaveValue(JSON.stringify(draftProcessor));
    const hiddenRequiredBooleanDraft = typeBForm.locator(
      'input[type="hidden"][name="specification_draft__cf_e2e_required_boolean"]',
    );
    await expect(hiddenRequiredBooleanDraft).toHaveCount(1);
    // Draft transport preserves the HTML lexeme; persistence below must be a JSON boolean.
    await expect(hiddenRequiredBooleanDraft).toHaveValue(JSON.stringify("false"));

    const firmwarePresence = typeBForm.locator('select[name="cf_firmware_version__presence"]');
    await expect(firmwarePresence).toHaveCount(1);
    await firmwarePresence.selectOption('empty');
    await expect(firmwarePresence).toHaveValue('empty');
    await expect(typeBForm.locator('select[name="cf_hostname__presence"]')).toHaveValue('');

    const toTypeA = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST' && url.pathname === editPath;
    });
    await selectTomOption(typeBForm, 'asset_type', typeA.id);
    expect((await toTypeA).status(), 'Type B -> Type A draft reload').toBe(200);

    const returnedForm = page.locator('#asset-specification-form[data-specification-editor]');
    await expect(returnedForm.locator('select[name="asset_type"]')).toHaveValue(typeA.id);
    await expect(returnedForm.locator('[name="tenant"]')).toHaveValue(tenant.id);
    const returnedRequiredBoolean = returnedForm.locator('[data-specification-key="e2e_required_boolean"]');
    await expect(returnedRequiredBoolean).toHaveValue('false');
    expect(await returnedRequiredBoolean.evaluate((element) => (element as HTMLSelectElement).required)).toBe(true);
    await expect(returnedForm.locator('[data-specification-key="processor_model"]')).toHaveValue(draftProcessor);
    await expect(returnedForm.locator('select[name="cf_processor_model__presence"]')).toHaveValue('value');
    await expect(returnedForm.locator('input[name="specification_draft__cf_processor_model"]')).toHaveValue(
      JSON.stringify(draftProcessor),
    );
    await expect(returnedForm.locator('select[name="cf_firmware_version__presence"]')).toHaveValue('empty');
    await expect(returnedForm.locator('select[name="cf_hostname__presence"]')).toHaveValue('');

    const updateResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return response.request().method() === 'POST' && url.pathname === editPath;
    });
    await returnedForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponse).status(), 'save returned Type A specification').toBe(302);
    await page.waitForURL((url) => url.pathname === `/assets/assets/${assetId}/`);

    const saved = await jsonResponse(
      await api.get(`/api/assets/assets/${assetId}/`),
      200,
      'saved specification journey asset',
    );
    expect(saved.asset_type).toMatchObject({ id: Number(typeA.id) });
    expect(saved.tenant).toMatchObject({ id: Number(tenant.id), slug: tenant.slug });
    expect(saved.specifications).toMatchObject({
      processor_model: draftProcessor,
      firmware_version: '',
      e2e_required_boolean: false,
    });
    expect(Object.prototype.hasOwnProperty.call(saved.specifications, 'hostname')).toBe(false);
  });
});


test('orders model sections, distinguishes unknown from false, and retains stale drafts', { tag: '@pr' }, async ({
  page, api, cleanup, runId,
}) => {
  const types = await getJsonRows(api, '/api/assets/asset-types/?limit=100', 'model composition prerequisites');
  const source = types.find((row) => row.slug === 'dell-latitude-5550');
  expect(source).toBeTruthy();
  const manufacturer = source!.manufacturer as { id: number };
  const originalOrder = ['itambox/storage', 'itambox/compute-memory'];
  const modelName = `E2E model composition ${runId}`;
  const preview = await jsonResponse(await api.post(`/api/assets/asset-types/${source!.id}/composition-preview/`, {
    data: { fieldsets: originalOrder, specification_patch: { set: {}, clear: [] } },
  }), 200, 'preview the proposed model definition');
  expect(preview.can_apply).toBe(true);
  const created = await jsonResponse(await api.post('/api/assets/asset-types/', { data: {
    model: modelName, slug: `e2e-model-${runId}`.slice(0, 150),
    manufacturer_id: manufacturer.id, fieldsets: originalOrder,
    expected_definition_revision: preview.expected_definition_revision,
  } }), 201, 'create owned model composition');
  const id = primaryKey(created, 'owned model composition');
  cleanup.add(`model composition ${id}`, async () => {
    await deleteOwnedResource(api, `/api/assets/asset-types/${id}/`, 'delete owned model composition');
  });
  expect(created.fieldsets).toEqual(originalOrder);
  expect(Object.prototype.hasOwnProperty.call(created.specifications, 'hot_swap_supported')).toBe(false);
  const editPath = `/assets/types/${id}/edit/`;
  expect((await page.goto(editPath))?.status()).toBe(200);
  const form = page.locator('#asset-type-specification-form[data-specification-editor]');
  await expect(form).toHaveCount(1);
  const selected = form.locator('[data-specification-selected-list] [data-specification-fieldset]');
  await expect(selected).toHaveCount(2);
  const originalIds = await selected.evaluateAll((elements) => elements.map((element) => element.getAttribute('data-fieldset-id')));
  const reload = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === editPath);
  await selected.nth(1).locator('[data-specification-move="up"]').click();
  expect((await reload).status()).toBe(200);
  await expect(form.locator('[name="custom_fieldsets"]')).toHaveCount(2);
  expect(await form.locator('[name="custom_fieldsets"]').evaluateAll((elements) => elements.map((element) => (element as HTMLInputElement).value))).toEqual([...originalIds].reverse());
  await expect(form.locator('[name="cf_hot_swap_supported__presence"]')).toHaveValue('');
  await expect(form.locator('[name="cf_hot_swap_supported"]')).not.toBeChecked();
  const saveOrder = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === editPath);
  await form.getByRole('button', { name: 'Update', exact: true }).click();
  expect((await saveOrder).status()).toBe(302);
  await page.waitForURL((url) => url.pathname === `/assets/types/${id}/`);
  const reordered = await jsonResponse(await api.get(`/api/assets/asset-types/${id}/`), 200, 'ordered model readback');
  expect(reordered.fieldsets).toEqual([...originalOrder].reverse());
  expect(Object.prototype.hasOwnProperty.call(reordered.specifications, 'hot_swap_supported')).toBe(false);

  expect((await page.goto(editPath))?.status()).toBe(200);
  await form.locator('[name="cf_hot_swap_supported__presence"]').selectOption('value');
  await expect(form.locator('[name="cf_hot_swap_supported"]')).not.toBeChecked();
  const saveFalse = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === editPath);
  await form.getByRole('button', { name: 'Update', exact: true }).click();
  expect((await saveFalse).status()).toBe(302);
  await page.waitForURL((url) => url.pathname === `/assets/types/${id}/`);
  const persisted = await jsonResponse(await api.get(`/api/assets/asset-types/${id}/`), 200, 'explicit model false readback');
  expect(persisted.specifications).toMatchObject({ hot_swap_supported: false });

  expect((await page.goto(editPath))?.status()).toBe(200);
  const winnerName = `${modelName} winner`;
  const winner = await jsonResponse(await api.patch(`/api/assets/asset-types/${id}/`, {
    headers: { 'If-Match': String(persisted.resource_revision) }, data: { model: winnerName },
  }), 200, 'concurrent native model update');
  await form.locator('[name="model"]').fill(`${modelName} stale draft`);
  const stale = page.waitForResponse((response) => response.request().method() === 'POST' && new URL(response.url()).pathname === editPath);
  await form.getByRole('button', { name: 'Update', exact: true }).click();
  expect((await stale).status()).toBe(200);
  await expect(form.locator('[data-specification-stale]')).toBeVisible();
  await expect(form.locator('[name="model"]')).toHaveValue(`${modelName} stale draft`);
  const after = await jsonResponse(await api.get(`/api/assets/asset-types/${id}/`), 200, 'stale rejection readback');
  expect(after.model).toBe(winnerName);
  expect(after.resource_revision).toBe(winner.resource_revision);
  expect(after.specifications).toEqual(persisted.specifications);
});
