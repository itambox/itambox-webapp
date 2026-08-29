import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { getJsonRows, jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('software-owned catalog lifecycle', { tag: '@pr' }, () => {
  test('creates, edits, reloads, reads back, and hard-deletes tenant software', async ({
    page,
    request,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const manufacturers = await getJsonRows(request, '/api/assets/manufacturers/?limit=100', 'software manufacturer');
    expect(manufacturers, 'the seeded E2E database must provide a manufacturer').not.toHaveLength(0);
    const manufacturerId = String(manufacturers[0].id);
    const createPath = '/software/software/add/';
    const originalName = `E2E Software ${runId}`;
    const renamedName = `${originalName} Preview`;

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(originalName);
    await selectTomOption(createForm, 'manufacturer', manufacturerId);
    await createForm.getByLabel('Version').fill('1.0');
    await createForm.getByLabel('Description').fill(`Owned software lifecycle ${runId}`);
    await selectTomOption(createForm, 'tenant', tenant.id);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'software create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/software\/software\/(\d+)\/$/);
    if (!detailMatch) throw new Error(`Software create returned an unexpected location: ${detailLocation || '<missing>'}`);
    const softwareId = detailMatch[1];
    const detailPath = `/software/software/${softwareId}/`;

    cleanup.add(`software ${originalName}`, async () => {
      const current = await request.get(`/api/software/software/${softwareId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      const deletion = await request.delete(`/api/software/software/${softwareId}/`);
      expect(deletion.status(), await deletion.text()).toBe(204);
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByRole('heading', { name: originalName, exact: true })).toBeVisible();
    const created = await jsonResponse(
      await request.get(`/api/software/software/${softwareId}/`),
      200,
      'created software readback',
    );
    expect(created).toMatchObject({
      id: Number(softwareId),
      name: originalName,
      version: '1.0',
      description: `Owned software lifecycle ${runId}`,
      manufacturer: expect.objectContaining({ id: Number(manufacturerId) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const updatePath = `/software/software/${softwareId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await updateForm.getByLabel('Version').fill('2.0');
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponsePromise).status(), 'software update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: renamedName, exact: true })).toBeVisible();
    const updated = await jsonResponse(
      await request.get(`/api/software/software/${softwareId}/`),
      200,
      'updated software readback',
    );
    expect(updated).toMatchObject({
      id: Number(softwareId),
      name: renamedName,
      version: '2.0',
      manufacturer: expect.objectContaining({ id: Number(manufacturerId) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const deletePath = `/software/software/${softwareId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.locator('strong').filter({ hasText: renamedName })).toHaveCount(1);
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: 'Confirm Delete', exact: true }).click();
    expect((await deleteResponsePromise).status(), 'software delete response').toBe(302);
    expect((await request.get(`/api/software/software/${softwareId}/`)).status()).toBe(404);
  });
});
