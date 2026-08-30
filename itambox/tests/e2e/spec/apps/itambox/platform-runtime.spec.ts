import { test, expect } from '../../../fixtures/test';

test.describe('ITAMbox browser platform runtime', { tag: '@pr' }, () => {
  test('registers the PWA service worker and keeps installability metadata after reload', async ({ page }) => {
    const dashboard = await page.goto('/', { waitUntil: 'load' });
    expect(dashboard?.status(), 'dashboard shell response').toBe(200);
    await expect(page.locator('link[rel="manifest"]')).toHaveAttribute('href', '/manifest.json');
    await expect(page.locator('meta[name="theme-color"]')).toHaveCount(2);

    const health = await page.request.get('/health/');
    expect(health.status(), 'health endpoint response').toBe(200);
    expect(await health.json()).toMatchObject({ status: 'ok', checks: { database: 'ok' } });

    const manifest = await page.request.get('/manifest.json');
    expect(manifest.status(), 'PWA manifest response').toBe(200);
    expect(await manifest.json()).toMatchObject({
      name: expect.stringContaining('ITAMbox'),
      short_name: 'ITAMbox',
      start_url: '/',
      display: 'standalone',
      icons: expect.arrayContaining([
        expect.objectContaining({ src: expect.stringContaining('icon-192'), sizes: '192x192' }),
        expect.objectContaining({ src: expect.stringContaining('icon-512'), sizes: '512x512' }),
      ]),
    });

    const registration = await page.evaluate(async () => {
      const ready = await navigator.serviceWorker.ready;
      return { active: ready.active?.state || null, scope: ready.scope };
    });
    expect(registration.active).toBe('activated');
    expect(new URL(registration.scope).pathname).toBe('/');

    const serviceWorker = await page.request.get('/service-worker.js');
    expect(serviceWorker.status(), 'service worker response').toBe(200);
    expect(serviceWorker.headers()['content-type']).toContain('javascript');
    expect((await serviceWorker.text()).length).toBeGreaterThan(100);

    await page.reload({ waitUntil: 'load' });
    expect(
      await page.evaluate(async () => Boolean(await navigator.serviceWorker.getRegistration('/'))),
      'service worker registration survives reload',
    ).toBe(true);
  });
});
