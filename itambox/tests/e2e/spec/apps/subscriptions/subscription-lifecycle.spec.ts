import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { deleteOwnedResource, getJsonRows, jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('subscriptions-owned lifecycle actions', { tag: '@pr' }, () => {
  test('creates, suspends, resumes, reloads, reads back, and deletes a subscription', async ({
    page,
    api,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const providers = await getJsonRows(api, '/api/subscriptions/providers/?limit=100', 'subscription provider');
    expect(providers, 'the seeded E2E database must provide a visible subscription provider').not.toHaveLength(0);
    const providerId = String(providers[0].id);

    const createPath = '/subscriptions/subscriptions/add/';
    const name = `E2E Subscription ${runId}`;
    const slug = `e2e-subscription-${runId}`.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 90);
    const createPage = await page.goto(createPath, { waitUntil: 'domcontentloaded' });
    expect(createPage?.status(), `GET ${createPath}`).toBe(200);
    const createForm = page.locator('form[method="post"]').filter({ has: page.locator('input[name="name"]') });
    await expect(createForm).toHaveCount(1);
    await createForm.getByLabel('Name').fill(name);
    await createForm.getByLabel('Slug').fill(slug);
    await selectTomOption(createForm, 'provider', providerId);
    await selectTomOption(createForm, 'type', 'saas');
    await createForm.getByLabel('Renewal Cost').fill('120.00');
    await createForm.getByLabel('Currency').fill('USD');
    await selectTomOption(createForm, 'billing_cycle', 'annual');
    await createForm.getByLabel('Licensed Quantity').fill('5');
    await createForm.getByLabel('Description').fill(`Owned subscription lifecycle ${runId}`);
    await selectTomOption(createForm, 'tenant', tenant.id);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'subscription create response').toBe(302);
    expect(createResponse.headers()['location']).toBe('/subscriptions/subscriptions/');
    const matches = (await getJsonRows(
      api,
      `/api/subscriptions/subscriptions/?q=${encodeURIComponent(name)}`,
      'created subscription lookup',
    )).filter((row) => row.name === name);
    expect(matches).toHaveLength(1);
    const subscriptionId = String(matches[0].id);
    const detailPath = `/subscriptions/subscriptions/${subscriptionId}/`;

    cleanup.add(`subscription ${slug}`, async () => {
      const current = await api.get(`/api/subscriptions/subscriptions/${subscriptionId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      await deleteOwnedResource(
        api,
        `/api/subscriptions/subscriptions/${subscriptionId}/`,
        `delete subscription ${slug}`,
      );
    });

    await page.goto(detailPath, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('h2.page-title')).toContainText(name);
    const created = await jsonResponse(
      await api.get(`/api/subscriptions/subscriptions/${subscriptionId}/`),
      200,
      'created subscription readback',
    );
    expect(created).toMatchObject({
      id: Number(subscriptionId),
      name,
      slug,
      type: 'saas',
      status: 'active',
      renewal_cost: '120.00',
      currency: 'USD',
      billing_cycle: 'annual',
      licensed_quantity: 5,
      provider: expect.objectContaining({ id: Number(providerId) }),
      tenant: expect.objectContaining({ id: Number(tenant.id) }),
    });

    const suspendPath = `/subscriptions/subscriptions/${subscriptionId}/suspend/`;
    const suspendResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === suspendPath,
    );
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: /Suspend$/ }).click();
    expect((await suspendResponsePromise).status(), 'subscription suspend response').toBe(204);
    expect(
      await jsonResponse(
        await api.get(`/api/subscriptions/subscriptions/${subscriptionId}/`),
        200,
        'suspended subscription readback',
      ),
    ).toMatchObject({ status: 'suspended' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: /Resume$/ })).toBeVisible();

    const resumePath = `/subscriptions/subscriptions/${subscriptionId}/resume/`;
    const resumeResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === resumePath,
    );
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: /Resume$/ }).click();
    expect((await resumeResponsePromise).status(), 'subscription resume response').toBe(204);
    expect(
      await jsonResponse(
        await api.get(`/api/subscriptions/subscriptions/${subscriptionId}/`),
        200,
        'resumed subscription readback',
      ),
    ).toMatchObject({ status: 'active' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: /Suspend$/ })).toBeVisible();

    const deletePath = `/subscriptions/subscriptions/${subscriptionId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.locator('strong').filter({ hasText: name })).toHaveCount(1);
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: /Confirm Deletion$/ }).click();
    expect((await deleteResponsePromise).status(), 'subscription delete response').toBe(302);
    expect((await api.get(`/api/subscriptions/subscriptions/${subscriptionId}/`)).status()).toBe(404);
  });
});
