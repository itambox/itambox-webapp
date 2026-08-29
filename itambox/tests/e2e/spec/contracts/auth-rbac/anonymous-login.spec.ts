import { test, expect } from '../../../fixtures/test';

test.describe('anonymous authentication contract', { tag: '@anonymous' }, () => {
  test('anonymous users are redirected to the login boundary', async ({ page }) => {
    const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
    expect(response, 'anonymous dashboard request must return a response').not.toBeNull();
    expect(response!.status()).toBe(302);
    await expect(page).toHaveURL(/\/accounts\/login\//);
    await expect(page.getByRole('button', { name: 'Sign in', exact: true })).toBeVisible();
  });
});
