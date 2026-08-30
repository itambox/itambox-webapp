import { test } from '@playwright/test';
import { mkdir } from 'fs/promises';
import { dirname, resolve } from 'path';
import { loginWithFreshSession, requiredEnv } from './login';

const storageState = resolve(__dirname, '../.auth/viewer.json');

test('setup-viewer creates an isolated read-only tenant-member session', async ({ page }) => {
  const username = requiredEnv('E2E_VIEWER_USERNAME');
  const password = process.env.E2E_VIEWER_PASSWORD || requiredEnv('E2E_PASSWORD');
  await loginWithFreshSession(page, username, password);
  await mkdir(dirname(storageState), { recursive: true });
  await page.context().storageState({ path: storageState });
});
