import { expect, type Page } from '@playwright/test';

export function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`E2E authentication prerequisite ${name} is missing.`);
  return value;
}

export async function loginWithFreshSession(page: Page, username: string, password: string): Promise<void> {
  const initial = await page.goto('/accounts/login/', { waitUntil: 'domcontentloaded' });
  expect(initial, 'Login page must return a document').not.toBeNull();
  expect(initial!.status(), 'Login page status').toBe(200);
  await expect(page.locator('input[name="username"]')).toHaveCount(1);
  await expect(page.locator('input[name="password"]')).toHaveCount(1);

  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);
  const loginResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return response.request().method() === 'POST' && url.pathname === '/accounts/login/';
  });
  await Promise.all([
    loginResponse,
    page.waitForURL((url) => url.pathname !== '/accounts/login/', { waitUntil: 'domcontentloaded' }),
    page.getByRole('button', { name: 'Sign in', exact: true }).click(),
  ]);
  const response = await loginResponse;
  expect(response.status(), 'Password login response').toBe(302);
  await expect(page).not.toHaveURL(/\/accounts\/login\//);
}
