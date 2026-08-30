import { test, expect } from '../../../fixtures/test';
import { deleteOwnedResource, jsonResponse } from '../../../helpers/api';

test.describe('generic object presentation contract', { tag: '@pr' }, () => {
  test('generic create, detail, edit, reload, and REST readback agree for a manufacturer', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    const createPath = '/assets/manufacturers/add/';
    const originalName = `E2E Manufacturer ${runId}`;
    const renamedName = `${originalName} Reviewed`;
    const slug = `e2e-manufacturer-${runId}`.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 90);

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.locator('input[name="name"]').fill(originalName);
    await createForm.getByLabel('Slug').fill(slug);
    await createForm.getByLabel('Description').fill(`Generic object contract ${runId}`);
    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'manufacturer create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/assets\/manufacturers\/(\d+)\/$/);
    if (!detailMatch) {
      throw new Error(`Manufacturer create returned an unexpected location: ${detailLocation || '<missing>'}`);
    }
    const manufacturerId = detailMatch[1];
    const detailPath = `/assets/manufacturers/${manufacturerId}/`;

    cleanup.add(`manufacturer ${slug}`, async () => {
      const current = await api.get(`/api/assets/manufacturers/${manufacturerId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(
        api,
        `/api/assets/manufacturers/${manufacturerId}/`,
        `delete manufacturer ${slug}`,
      );
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByRole('heading', { name: originalName, exact: true })).toBeVisible();
    expect(
      await jsonResponse(
        await api.get(`/api/assets/manufacturers/${manufacturerId}/`),
        200,
        'created manufacturer readback',
      ),
    ).toMatchObject({
      id: Number(manufacturerId),
      name: originalName,
      slug,
      description: `Generic object contract ${runId}`,
    });

    const updatePath = `/assets/manufacturers/${manufacturerId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.locator('input[name="name"]').fill(renamedName);
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponsePromise).status(), 'manufacturer update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: renamedName, exact: true })).toBeVisible();
    expect(
      await jsonResponse(
        await api.get(`/api/assets/manufacturers/${manufacturerId}/`),
        200,
        'updated manufacturer readback',
      ),
    ).toMatchObject({ id: Number(manufacturerId), name: renamedName, slug });
  });
});
