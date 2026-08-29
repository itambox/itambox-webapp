import { test, expect } from '../../fixtures/test';
import { openOwnedSurface } from '../../helpers/app-surface';

test.describe('ITAMbox platform qualification surface', { tag: '@pr' }, () => {
  test('health endpoint remains reachable from the authenticated shell', async ({ page }) => {
    const response = await page.request.get('/health/');
    expect(response.status(), 'GET /health/').toBe(200);
    await openOwnedSurface(page, '/', /Dashboard/i);
  });
});
