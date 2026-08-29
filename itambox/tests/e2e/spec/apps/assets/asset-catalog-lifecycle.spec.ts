import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { deleteOwnedResource, getJsonRows, jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('assets-owned catalog lifecycle', { tag: '@pr' }, () => {
  test('creates, edits, reloads, reads back, and soft-deletes an owned asset', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const [assetTypes, assetRoles, statuses] = await Promise.all([
      getJsonRows(api, '/api/assets/asset-types/?limit=100', 'asset type prerequisites'),
      getJsonRows(api, '/api/assets/asset-roles/?limit=100', 'asset role prerequisites'),
      getJsonRows(api, '/api/assets/status-labels/?limit=100', 'asset status prerequisites'),
    ]);
    expect(assetTypes).not.toHaveLength(0);
    expect(assetRoles).not.toHaveLength(0);
    const deployable = statuses.find((row) => row.type === 'deployable');
    if (!deployable) throw new Error('The E2E seed must expose a deployable asset status.');

    const createPath = '/assets/assets/add/';
    const originalName = `E2E Catalog Asset ${runId}`;
    const renamedName = `${originalName} Reviewed`;
    const assetTag = `E2E-CATALOG-${runId}`.toUpperCase().replace(/[^A-Z0-9-]/g, '-').slice(0, 90);
    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(originalName);
    await createForm.getByLabel('Asset tag').fill(assetTag);
    await selectTomOption(createForm, 'asset_type', String(assetTypes[0].id));
    await selectTomOption(createForm, 'asset_role', String(assetRoles[0].id));
    await selectTomOption(createForm, 'status', String(deployable.id));
    await selectTomOption(createForm, 'tenant', tenant.id);
    await createForm.getByLabel('Notes').fill(`Owned asset catalog lifecycle ${runId}`);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'asset create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/assets\/assets\/(\d+)\/$/);
    if (!detailMatch) throw new Error(`Asset create returned an unexpected location: ${detailLocation || '<missing>'}`);
    const assetId = detailMatch[1];
    const detailPath = `/assets/assets/${assetId}/`;

    cleanup.add(`catalog asset ${assetTag}`, async () => {
      const current = await api.get(`/api/assets/assets/${assetId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(api, `/api/assets/assets/${assetId}/`, `delete catalog asset ${assetTag}`);
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByText(assetTag, { exact: true })).toBeVisible();
    const created = await jsonResponse(await api.get(`/api/assets/assets/${assetId}/`), 200, 'created asset readback');
    expect(created).toMatchObject({
      id: Number(assetId),
      name: originalName,
      asset_tag: assetTag,
      asset_type: expect.objectContaining({ id: Number(assetTypes[0].id) }),
      asset_role: expect.objectContaining({ id: Number(assetRoles[0].id) }),
      status: expect.objectContaining({ id: Number(deployable.id) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const updatePath = `/assets/assets/${assetId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await updateForm.getByLabel('Notes').fill(`Updated owned asset lifecycle ${runId}`);
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponsePromise).status(), 'asset update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: new RegExp(renamedName) })).toBeVisible();
    expect(await jsonResponse(await api.get(`/api/assets/assets/${assetId}/`), 200, 'updated asset readback')).toMatchObject({
      id: Number(assetId),
      name: renamedName,
      asset_tag: assetTag,
      notes: `Updated owned asset lifecycle ${runId}`,
    });

    const deletePath = `/assets/assets/${assetId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.getByText(assetTag, { exact: false })).toBeVisible();
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: 'Confirm Deletion', exact: true }).click();
    expect((await deleteResponsePromise).status(), 'asset delete response').toBe(302);
    expect((await api.get(`/api/assets/assets/${assetId}/`)).status()).toBe(404);
  });
});
