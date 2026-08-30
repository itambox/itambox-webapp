import { test } from '@playwright/test';
import { mkdir } from 'fs/promises';
import { dirname, resolve } from 'path';
import { loginWithFreshSession, requiredEnv } from './login';

const storageState = resolve(__dirname, '../.auth/admin.json');

test('setup-admin creates an isolated admin session', async ({ page }) => {
  await loginWithFreshSession(page, requiredEnv('E2E_USERNAME'), requiredEnv('E2E_PASSWORD'));
  await mkdir(dirname(storageState), { recursive: true });
  await page.context().storageState({ path: storageState });
});
