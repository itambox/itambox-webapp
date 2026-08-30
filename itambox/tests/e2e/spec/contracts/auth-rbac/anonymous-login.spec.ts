import { test, expect } from '../../../fixtures/test';

test.describe('anonymous authentication contract', { tag: '@anonymous' }, () => {
  test('anonymous users are redirected to the login boundary', async ({ page }) => {
    const boundary = await page.request.get('/', { maxRedirects: 0 });
    expect(boundary.status(), 'anonymous dashboard redirect').toBe(302);
    expect(boundary.headers()['location']).toMatch(/^\/accounts\/login\//);

    const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
    expect(response, 'anonymous login boundary must return a response').not.toBeNull();
    expect(response!.status()).toBe(200);
    await expect(page).toHaveURL(/\/accounts\/login\//);
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  });
});
