import { test } from '@playwright/test';
import { mkdir } from 'fs/promises';
import { dirname, resolve } from 'path';
import { loginWithFreshSession, requiredEnv } from './login';

const storageState = resolve(__dirname, '../.auth/aggregate.json');

test('setup-aggregate creates the explicit aggregate-operator session', async ({ page }) => {
  await loginWithFreshSession(page, requiredEnv('E2E_AGGREGATE_USERNAME'), requiredEnv('E2E_AGGREGATE_PASSWORD'));
  await mkdir(dirname(storageState), { recursive: true });
  await page.context().storageState({ path: storageState });
});
