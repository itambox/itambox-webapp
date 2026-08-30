import { test } from '@playwright/test';
import { mkdir } from 'fs/promises';
import { dirname, resolve } from 'path';
import { loginWithFreshSession, requiredEnv } from './login';

const storageState = resolve(__dirname, '../.auth/operator.json');

test('setup-operator creates an isolated tenant-operator session', async ({ page }) => {
  const username = requiredEnv('E2E_OPERATOR_USERNAME');
  const password = process.env.E2E_OPERATOR_PASSWORD || requiredEnv('E2E_PASSWORD');
  await loginWithFreshSession(page, username, password);
  await mkdir(dirname(storageState), { recursive: true });
  await page.context().storageState({ path: storageState });
});
