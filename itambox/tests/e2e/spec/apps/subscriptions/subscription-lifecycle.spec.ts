import { test, expect } from '../../../fixtures/test';
import { requireActiveTenant } from '../../../fixtures/tenant';
import { jsonResponse } from '../../../helpers/api';
import { selectTomOption } from '../../../helpers/forms';

test.describe('subscriptions-owned lifecycle actions', { tag: '@pr' }, () => {
  test('creates, suspends, resumes, reloads, reads back, and deletes a subscription', async ({
    page,
    request,
    activeTenant,
    cleanup,
    runId,
  }) => {
    const tenant = requireActiveTenant(activeTenant);
    const providerName = `E2E Provider ${runId}`;
    const providerSlug = `e2e-provider-${runId}`.toLowerCase().replace(/[^a-z0-9-]/g, '-').slice(0, 90);
    const provider = await jsonResponse(
      await request.post('/api/subscriptions/providers/', {
        data: {
          name: providerName,
          slug: providerSlug,
          tenant_id: tenant.id,
          is_active: true,
        },
      }),
      201,
      'create subscription provider',
    );
    const providerId = String(provider.id);
    cleanup.add(`subscription provider ${providerSlug}`, async () => {
      const current = await request.get(`/api/subscriptions/providers/${providerId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      const deletion = await request.delete(`/api/subscriptions/providers/${providerId}/`);
      expect(deletion.status(), await deletion.text()).toBe(204);
    });

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
    await createForm.getByLabel('Subscription Type').selectOption('saas');
    await createForm.getByLabel('Renewal Cost').fill('120.00');
    await createForm.getByLabel('Currency').fill('USD');
    await createForm.getByLabel('Billing Cycle').selectOption('annual');
    await createForm.getByLabel('Licensed Quantity').fill('5');
    await createForm.getByLabel('Description').fill(`Owned subscription lifecycle ${runId}`);
    await selectTomOption(createForm, 'tenant', tenant.id);

    const createResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === createPath,
    );
    await createForm.getByRole('button', { name: 'Create', exact: true }).click();
    const createResponse = await createResponsePromise;
    expect(createResponse.status(), 'subscription create response').toBe(302);
    const detailLocation = createResponse.headers()['location'];
    const detailMatch = detailLocation?.match(/^\/subscriptions\/subscriptions\/(\d+)\/$/);
    if (!detailMatch) {
      throw new Error(`Subscription create returned an unexpected location: ${detailLocation || '<missing>'}`);
    }
    const subscriptionId = detailMatch[1];
    const detailPath = `/subscriptions/subscriptions/${subscriptionId}/`;

    cleanup.add(`subscription ${slug}`, async () => {
      const current = await request.get(`/api/subscriptions/subscriptions/${subscriptionId}/`);
      if (current.status() === 404) return;
      expect(current.status(), await current.text()).toBe(200);
      const deletion = await request.delete(`/api/subscriptions/subscriptions/${subscriptionId}/`);
      expect(deletion.status(), await deletion.text()).toBe(204);
    });

    await page.waitForURL((url) => url.pathname === detailPath);
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible();
    const created = await jsonResponse(
      await request.get(`/api/subscriptions/subscriptions/${subscriptionId}/`),
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
    await page.getByRole('button', { name: 'Suspend', exact: true }).click();
    expect((await suspendResponsePromise).status(), 'subscription suspend response').toBe(204);
    expect(
      await jsonResponse(
        await request.get(`/api/subscriptions/subscriptions/${subscriptionId}/`),
        200,
        'suspended subscription readback',
      ),
    ).toMatchObject({ status: 'suspended' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: 'Resume', exact: true })).toBeVisible();

    const resumePath = `/subscriptions/subscriptions/${subscriptionId}/resume/`;
    const resumeResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === resumePath,
    );
    page.once('dialog', (dialog) => dialog.accept());
    await page.getByRole('button', { name: 'Resume', exact: true }).click();
    expect((await resumeResponsePromise).status(), 'subscription resume response').toBe(204);
    expect(
      await jsonResponse(
        await request.get(`/api/subscriptions/subscriptions/${subscriptionId}/`),
        200,
        'resumed subscription readback',
      ),
    ).toMatchObject({ status: 'active' });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('button', { name: 'Suspend', exact: true })).toBeVisible();

    const deletePath = `/subscriptions/subscriptions/${subscriptionId}/delete/`;
    const deletePage = await page.goto(deletePath, { waitUntil: 'domcontentloaded' });
    expect(deletePage?.status(), `GET ${deletePath}`).toBe(200);
    await expect(page.locator('strong').filter({ hasText: name })).toHaveCount(1);
    const deleteResponsePromise = page.waitForResponse((response) =>
      response.request().method() === 'POST' && new URL(response.url()).pathname === deletePath,
    );
    await page.getByRole('button', { name: 'Confirm Delete', exact: true }).click();
    expect((await deleteResponsePromise).status(), 'subscription delete response').toBe(302);
    expect((await request.get(`/api/subscriptions/subscriptions/${subscriptionId}/`)).status()).toBe(404);
  });
});
