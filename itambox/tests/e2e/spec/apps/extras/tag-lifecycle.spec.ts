import { test, expect } from '../../../fixtures/test';
import { jsonResponse } from '../../../helpers/api';

test.describe('extras-owned tag lifecycle', { tag: '@pr' }, () => {
  test('creates, edits, reloads, reads back, and hard-deletes a metadata tag', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    const createPath = '/extras/tags/create/';
    const originalName = `E2E Tag ${runId}`;
    const renamedName = `${originalName} Reviewed`;
    const slug = `e2e-tag-${runId}`.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 90);

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(originalName);
    await createForm.getByLabel('Slug').fill(slug);
    await createForm.getByLabel('Color').fill('#1f77b4');
    await createForm.getByLabel('Description').fill(`Owned tag lifecycle ${runId}`);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'tag create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/extras\/tags\/(\d+)\/$/);
    if (!detailMatch) throw new Error(`Tag create returned an unexpected location: ${detailLocation || '<missing>'}`);
    const tagId = detailMatch[1];
    const detailPath = `/extras/tags/${tagId}/`;

    cleanup.add(`metadata tag ${slug}`, async () => {
      const current = await api.get(`/api/extras/tags/${tagId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      const deletion = await api.delete(`/api/extras/tags/${tagId}/`);
      expect(deletion.status(), await deletion.text()).toBe(204);
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByRole('heading', { name: originalName, exact: true })).toBeVisible();
    const created = await jsonResponse(await api.get(`/api/extras/tags/${tagId}/`), 200, 'created tag readback');
    expect(created).toMatchObject({
      id: Number(tagId),
      name: originalName,
      slug,
      color: '1f77b4',
      description: `Owned tag lifecycle ${runId}`,
    });

    const updatePath = `/extras/tags/${tagId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await updateForm.getByLabel('Color').fill('#ff7f0e');
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponsePromise).status(), 'tag update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: renamedName, exact: true })).toBeVisible();
    const updated = await jsonResponse(await api.get(`/api/extras/tags/${tagId}/`), 200, 'updated tag readback');
    expect(updated).toMatchObject({ id: Number(tagId), name: renamedName, slug, color: 'ff7f0e' });

    const deletePath = `/extras/tags/${tagId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.locator('strong').filter({ hasText: renamedName })).toHaveCount(1);
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: 'Confirm Deletion', exact: true }).click();
    expect((await deleteResponsePromise).status(), 'tag delete response').toBe(302);
    expect((await api.get(`/api/extras/tags/${tagId}/`)).status()).toBe(404);
  });
});
