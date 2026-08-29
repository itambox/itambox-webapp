import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { jsonResponse } from '../../../helpers/api';

test.describe('organization-owned site lifecycle', { tag: '@pr' }, () => {
  test('creates, edits, reloads, reads back, and hard-deletes an owned site', async ({
    page,
    request,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const createPath = '/organization/sites/add/';
    const originalName = `E2E Site ${runId}`;
    const renamedName = `${originalName} Renamed`;
    const slug = `e2e-site-${runId}`.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 90);

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(originalName);
    await createForm.getByLabel('Slug').fill(slug);
    await createForm.getByLabel('Status').selectOption('active');
    await createForm.getByLabel('Tenant').selectOption(tenant.id);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'site create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/organization\/sites\/(\d+)\/$/);
    if (!detailMatch) throw new Error(`Site create returned an unexpected location: ${detailLocation || '<missing>'}`);
    const siteId = detailMatch[1];
    const detailPath = `/organization/sites/${siteId}/`;

    cleanup.add(`organization site ${slug}`, async () => {
      const current = await request.get(`/api/organization/sites/${siteId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      const deletion = await request.delete(`/api/organization/sites/${siteId}/`);
      expect(deletion.status(), await deletion.text()).toBe(204);
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByRole('heading', { name: originalName, exact: true })).toBeVisible();
    const created = await jsonResponse(
      await request.get(`/api/organization/sites/${siteId}/`),
      200,
      'created site readback',
    );
    expect(created).toMatchObject({
      id: Number(siteId),
      name: originalName,
      slug,
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const updatePath = `/organization/sites/${siteId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await updateForm.getByLabel('Description').fill(`Owned site lifecycle ${runId}`);
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponsePromise).status(), 'site update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: renamedName, exact: true })).toBeVisible();
    const updated = await jsonResponse(
      await request.get(`/api/organization/sites/${siteId}/`),
      200,
      'updated site readback',
    );
    expect(updated).toMatchObject({
      id: Number(siteId),
      name: renamedName,
      slug,
      description: `Owned site lifecycle ${runId}`,
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const deletePath = `/organization/sites/${siteId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.getByText(renamedName, { exact: true })).toBeVisible();
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: 'Confirm Delete', exact: true }).click();
    expect((await deleteResponsePromise).status(), 'site delete response').toBe(302);
    expect((await request.get(`/api/organization/sites/${siteId}/`)).status()).toBe(404);
  });
});
