import { test } from '@playwright/test';
import { mkdir } from 'fs/promises';
import { dirname, resolve } from 'path';
import { loginWithFreshSession, requiredEnv } from './login';

const storageState = resolve(__dirname, '../.auth/viewer.json');

test('setup-viewer creates an isolated read-only tenant-member session', async ({ page }) => {
  await loginWithFreshSession(page, requiredEnv('E2E_VIEWER_USERNAME'), requiredEnv('E2E_VIEWER_PASSWORD'));
  await mkdir(dirname(storageState), { recursive: true });
  await page.context().storageState({ path: storageState });
});
