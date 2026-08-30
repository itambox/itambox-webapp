import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { deleteOwnedResource, getJsonRows, jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('procurement-owned contract lifecycle', { tag: '@pr' }, () => {
  test('creates, activates, reloads, reads back, and deletes a tenant contract', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const suppliers = await getJsonRows(api, '/api/assets/suppliers/?limit=100', 'contract supplier');
    expect(suppliers, 'the seeded E2E database must provide a supplier').not.toHaveLength(0);
    const supplierId = String(suppliers[0].id);
    const createPath = '/procurement/contracts/add/';
    const originalName = `E2E Support Contract ${runId}`;
    const renamedName = `${originalName} Active`;
    const contractNumber = `E2E-CTR-${runId}`.toUpperCase().replace(/[^A-Z0-9-]/g, '-').slice(0, 90);

    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(originalName);
    await createForm.getByLabel('Contract number').fill(contractNumber);
    await selectTomOption(createForm, 'contract_type', 'support');
    await selectTomOption(createForm, 'status', 'draft');
    await selectTomOption(createForm, 'tenant', tenant.id);
    await selectTomOption(createForm, 'supplier', supplierId);
    await createForm.locator('input[name="cost"]').fill('1200.00');
    await selectTomOption(createForm, 'currency', 'USD');
    await selectTomOption(createForm, 'billing_cycle', 'annual');
    await createForm.getByLabel('Start date').fill('2026-09-01');
    await createForm.getByLabel('End date').fill('2027-09-01');
    await createForm.getByLabel('Notes').fill(`Owned procurement lifecycle ${runId}`);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Save Contract', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'contract create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/procurement\/contracts\/(\d+)\/$/);
    if (!detailMatch) throw new Error(`Contract create returned an unexpected location: ${detailLocation || '<missing>'}`);
    const contractId = detailMatch[1];
    const detailPath = `/procurement/contracts/${contractId}/`;

    cleanup.add(`procurement contract ${contractNumber}`, async () => {
      const current = await api.get(`/api/procurement/contracts/${contractId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(
        api,
        `/api/procurement/contracts/${contractId}/`,
        `delete procurement contract ${contractNumber}`,
      );
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.locator('h2.page-title')).toContainText(originalName);
    const created = await jsonResponse(
      await api.get(`/api/procurement/contracts/${contractId}/`),
      200,
      'created contract readback',
    );
    expect(created).toMatchObject({
      id: Number(contractId),
      name: originalName,
      contract_number: contractNumber,
      contract_type: 'support',
      status: 'draft',
      cost: '1200.00',
      currency: 'USD',
      billing_cycle: 'annual',
      start_date: '2026-09-01',
      end_date: '2027-09-01',
      supplier: expect.objectContaining({ id: Number(supplierId) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const updatePath = `/procurement/contracts/${contractId}/edit/`;
    const updatePage = await page.goto(updatePath, { waitUntil: 'domcontentloaded' });
    expect(updatePage?.status(), `GET ${updatePath}`).toBe(200);
    const updateForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await updateForm.getByLabel('Name').fill(renamedName);
    await selectTomOption(updateForm, 'status', 'active');
    await updateForm.locator('input[name="cost"]').fill('1500.00');
    const updateResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === updatePath,
    );
    await updateForm.getByRole('button', { name: 'Save Contract', exact: true }).click();
    expect((await updateResponsePromise).status(), 'contract update response').toBe(302);
    await page.waitForURL((url) => url.pathname === detailPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.locator('h2.page-title')).toContainText(renamedName);
    const updated = await jsonResponse(
      await api.get(`/api/procurement/contracts/${contractId}/`),
      200,
      'updated contract readback',
    );
    expect(updated).toMatchObject({ id: Number(contractId), name: renamedName, status: 'active', cost: '1500.00' });

    const deletePath = `/procurement/contracts/${contractId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.locator('h2.page-title')).toContainText(renamedName);
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: /Confirm Deletion$/ }).click();
    expect((await deleteResponsePromise).status(), 'contract delete response').toBe(302);
    expect((await api.get(`/api/procurement/contracts/${contractId}/`)).status()).toBe(404);
  });
});
