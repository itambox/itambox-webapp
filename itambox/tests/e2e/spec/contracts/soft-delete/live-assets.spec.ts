import { test, expect } from '../../../fixtures/test';
import { deleteOwnedResource, jsonResponse } from '../../../helpers/api';

test.describe('soft-delete contract', { tag: '@pr' }, () => {
  test('browser delete, recycle-bin restore, re-delete, and purge stay observable', async ({
    page,
    api,
    cleanup,
    runId,
  }) => {
    const name = `E2E Recyclable Manufacturer ${runId}`;
    const slug = `e2e-recyclable-${runId}`.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 90);
    const created = await jsonResponse(
      await api.post('/api/assets/manufacturers/', {
        data: { name, slug, description: `Soft-delete contract ${runId}` },
      }),
      201,
      'create recyclable manufacturer',
    );
    const manufacturerId = String(created.id);
    cleanup.add(`recyclable manufacturer ${slug}`, async () => {
      const current = await api.get(`/api/assets/manufacturers/${manufacturerId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(
        api,
        `/api/assets/manufacturers/${manufacturerId}/`,
        `delete recyclable manufacturer ${slug}`,
      );
    });

    const deletePath = `/assets/manufacturers/${manufacturerId}/delete/`;
    const firstDelete = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(firstDelete?.status(), `GET ${deletePath}`).toBe(200);
    const firstDeleteResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: /Confirm Deletion$/ }).click();
    expect((await firstDeleteResponse).status(), 'first soft-delete response').toBe(302);
    expect((await api.get(`/api/assets/manufacturers/${manufacturerId}/`)).status()).toBe(404);

    const recyclePath = `/assets/manufacturers/?deleted=true&q=${encodeURIComponent(name)}`;
    const recycle = await page.goto(recyclePath, { waitUntil: 'domcontentloaded' });
    expect(recycle?.status(), `GET ${recyclePath}`).toBe(200);
    let row = page.locator('tbody tr').filter({ has: page.getByRole('link', { name, exact: true }) });
    await expect(row).toHaveCount(1);
    const restore = row.getByRole('link', { name: 'Restore', exact: true });
    const restoreHref = await restore.getAttribute('href');
    if (!restoreHref) throw new Error('Recycle-bin Restore action has no href.');
    const restorePath = new URL(restoreHref, 'http://localhost').pathname;
    const restoreResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === restorePath,
    );
    page.once('dialog', (dialog) => dialog.accept());
    await restore.click();
    expect((await restoreResponse).status(), 'manufacturer restore response').toBe(204);
    expect(
      await jsonResponse(
        await api.get(`/api/assets/manufacturers/${manufacturerId}/`),
        200,
        'restored manufacturer readback',
      ),
    ).toMatchObject({ id: Number(manufacturerId), name, slug });
    await expect(row).toHaveCount(0);

    const secondDelete = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(secondDelete?.status(), `GET ${deletePath} after restore`).toBe(200);
    const secondDeleteResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: /Confirm Deletion$/ }).click();
    expect((await secondDeleteResponse).status(), 'second soft-delete response').toBe(302);

    await page.goto(recyclePath, { waitUntil: 'domcontentloaded' });
    row = page.locator('tbody tr').filter({ has: page.getByRole('link', { name, exact: true }) });
    await expect(row).toHaveCount(1);
    const purge = row.getByRole('link', { name: 'Delete Permanently', exact: true });
    const purgeHref = await purge.getAttribute('href');
    if (!purgeHref) throw new Error('Recycle-bin purge action has no href.');
    const purgePath = new URL(purgeHref, 'http://localhost').pathname;
    const purgeResponse = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === purgePath,
    );
    page.once('dialog', (dialog) => dialog.accept());
    await purge.click();
    expect((await purgeResponse).status(), 'manufacturer purge response').toBe(204);
    await expect(row).toHaveCount(0);
    expect((await api.get(`/api/assets/manufacturers/${manufacturerId}/`)).status()).toBe(404);
  });
});
