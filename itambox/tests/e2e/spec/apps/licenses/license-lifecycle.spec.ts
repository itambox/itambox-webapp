import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { deleteOwnedResource, getJsonRows, jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('licenses-owned entitlement lifecycle', { tag: '@pr' }, () => {
  test('creates, expands, reloads, reads back, and hard-deletes a tenant license', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const softwareRows = await getJsonRows(api, '/api/software/software/?limit=100', 'license software');
    expect(softwareRows, 'the seeded E2E database must provide software').not.toHaveLength(0);
    const softwareId = String(softwareRows[0].id);
    const createPath = '/licenses/add/';
    const originalName = `E2E License ${runId}`;
    const renamedName = `${originalName} Expanded`;

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(originalName);
    await selectTomOption(createForm, 'license_type', 'perpetual_seat');
    await selectTomOption(createForm, 'software', softwareId);
    await createForm.getByLabel('Seats').fill('2');
    await createForm.getByLabel('Notes').fill(`Owned license lifecycle ${runId}`);
    await selectTomOption(createForm, 'tenant', tenant.id);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'license create response').toBe(302);
    expect(createResponse.headers()['location']).toBe('/licenses/');
    const matches = (await getJsonRows(
      api,
      `/api/licenses/licenses/?q=${encodeURIComponent(originalName)}`,
      'created license lookup',
    )).filter((row) => row.name === originalName);
    expect(matches).toHaveLength(1);
    const licenseId = String(matches[0].id);
    const detailPath = `/licenses/${licenseId}/`;

    cleanup.add(`license ${originalName}`, async () => {
      const current = await api.get(`/api/licenses/licenses/${licenseId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(api, `/api/licenses/licenses/${licenseId}/`, `delete license ${originalName}`);
    });

    await page.goto(detailPath, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('h2.page-title')).toContainText(originalName);
    const created = await jsonResponse(
      await api.get(`/api/licenses/licenses/${licenseId}/`),
      200,
      'created license readback',
    );
    expect(created).toMatchObject({
      id: Number(licenseId),
      name: originalName,
      license_type: 'perpetual_seat',
      seats: 2,
      available_seats: 2,
      notes: `Owned license lifecycle ${runId}`,
      software: expect.objectContaining({ id: Number(softwareId) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const updatePath = `/licenses/${licenseId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await updateForm.getByLabel('Seats').fill('3');
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    const updateResponse = await updateResponsePromise;
    expect(updateResponse.status(), 'license update response').toBe(302);
    expect(updateResponse.headers()['location']).toBe('/licenses/');
    await page.goto(detailPath, { waitUntil: 'domcontentloaded' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('h2.page-title')).toContainText(renamedName);
    const updated = await jsonResponse(
      await api.get(`/api/licenses/licenses/${licenseId}/`),
      200,
      'updated license readback',
    );
    expect(updated).toMatchObject({
      id: Number(licenseId),
      name: renamedName,
      seats: 3,
      available_seats: 3,
      software: expect.objectContaining({ id: Number(softwareId) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const deletePath = `/licenses/${licenseId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.locator('strong').filter({ hasText: renamedName })).toHaveCount(1);
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: /Confirm Deletion$/ }).click();
    expect((await deleteResponsePromise).status(), 'license delete response').toBe(302);
    expect((await api.get(`/api/licenses/licenses/${licenseId}/`)).status()).toBe(404);
  });
});
