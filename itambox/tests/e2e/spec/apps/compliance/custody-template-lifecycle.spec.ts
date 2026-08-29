import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { jsonResponse } from '../../../helpers/api';

test.describe('compliance-owned custody template lifecycle', { tag: '@pr' }, () => {
  test('creates, edits, previews, reads back, and deletes a tenant custody template', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const createPath = '/compliance/custody-templates/add/';
    const originalName = `E2E Custody Template ${runId}`;
    const renamedName = `${originalName} Reviewed`;
    const originalTerms = `Owned custody terms ${runId}`;
    const updatedTerms = `${originalTerms} updated`;

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Tenant').selectOption(tenant.id);
    await createForm.getByLabel('Name').fill(originalName);
    await createForm.getByLabel('Signature Provider').selectOption('local');
    await createForm.getByLabel('Eula text').fill(originalTerms);
    await createForm.getByLabel('Disclaimer').fill(`Owned custody disclaimer ${runId}`);
    await createForm.getByLabel('Qms reference').fill(`E2E-QMS-${runId}`.slice(0, 90));
    await createForm.getByLabel('Require acceptance').check();
    await createForm.getByLabel('Active').check();

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'custody template create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/compliance\/custody-templates\/(\d+)\/$/);
    if (!detailMatch) {
      throw new Error(`Custody template create returned an unexpected location: ${detailLocation || '<missing>'}`);
    }
    const templateId = detailMatch[1];
    const detailPath = `/compliance/custody-templates/${templateId}/`;

    cleanup.add(`custody template ${originalName}`, async () => {
      const current = await api.get(`/api/compliance/custody-templates/${templateId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      const deletion = await api.delete(`/api/compliance/custody-templates/${templateId}/`);
      expect(deletion.status(), await deletion.text()).toBe(204);
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByRole('heading', { name: originalName, exact: true })).toBeVisible();
    const created = await jsonResponse(
      await api.get(`/api/compliance/custody-templates/${templateId}/`),
      200,
      'created custody template readback',
    );
    expect(created).toMatchObject({
      id: Number(templateId),
      name: originalName,
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
      signature_provider: 'local',
      eula_text: originalTerms,
      disclaimer: `Owned custody disclaimer ${runId}`,
      require_acceptance: true,
      is_active: true,
    });

    const updatePath = `/compliance/custody-templates/${templateId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await updateForm.getByLabel('Eula text').fill(updatedTerms);
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Update', exact: true }).click();
    expect((await updateResponsePromise).status(), 'custody template update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    const updated = await jsonResponse(
      await api.get(`/api/compliance/custody-templates/${templateId}/`),
      200,
      'updated custody template readback',
    );
    expect(updated).toMatchObject({ id: Number(templateId), name: renamedName, eula_text: updatedTerms });

    const previewPath = `/compliance/custody-templates/${templateId}/preview/`;
    const preview = await page.goto(previewPath, { waitUntil: 'domcontentloaded' });
    expect(preview?.status(), `GET ${previewPath}`).toBe(200);
    await expect(page.getByText('Live Template Preview Sandbox', { exact: true })).toBeVisible();
    await expect(page.getByText(updatedTerms, { exact: true })).toBeVisible();
    await expect(page.getByText(`Owned custody disclaimer ${runId}`, { exact: true })).toBeVisible();

    const deletePath = `/compliance/custody-templates/${templateId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.getByText(renamedName, { exact: false })).toBeVisible();
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: 'Confirm Deletion', exact: true }).click();
    expect((await deleteResponsePromise).status(), 'custody template delete response').toBe(302);
    expect((await api.get(`/api/compliance/custody-templates/${templateId}/`)).status()).toBe(404);
  });
});
