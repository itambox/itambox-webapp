import { chromium, FullConfig } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

/**
 * Global setup for Playwright E2E tests.
 *
 * Authenticates once and saves storage state so individual tests don't need to
 * log in. Credentials are read from environment variables (never hard-coded):
 *
 *   E2E_BASE_URL  – base URL of the running Django app (default: http://localhost:8000)
 *   E2E_USERNAME  – login username (required)
 *   E2E_PASSWORD  – login password (required)
 *
 * On failure, writes a screenshot to ``storageStateError.png`` for diagnosis.
 */

function requiredEnv(name: string): string {
  const value = process.env[name];
  if (!value) {
    console.error(`\n  E2E prerequisite missing: ${name} is not set.\n`);
    console.error('  Export E2E_USERNAME and E2E_PASSWORD before running the suite.\n');
    console.error('  Example:\n');
    console.error('    export E2E_USERNAME=admin');
    console.error('    export E2E_PASSWORD=admin123\n');
    process.exit(1);
  }
  return value;
}

async function globalSetup(config: FullConfig) {
  const baseURL = process.env.E2E_BASE_URL || 'http://localhost:8000';
  const username = requiredEnv('E2E_USERNAME');
  const password = requiredEnv('E2E_PASSWORD');

  const browser = await chromium.launch();
  const page = await browser.newPage();

  try {
    await page.goto(`${baseURL}/`, { waitUntil: 'networkidle' });

    await page.waitForSelector('input[name="username"]', { timeout: 10000 });
    await page.fill('input[name="username"]', username);
    await page.fill('input[name="password"]', password);
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle' }),
      page.click('button[type="submit"]'),
    ]);

    // Verify login succeeded (no error message, redirected away from login)
    const errorEl = await page.$('.alert-danger, .errorlist, [data-testid="login-error"]');
    if (errorEl) {
      throw new Error('Login failed — check E2E_USERNAME / E2E_PASSWORD.');
    }

    const storageStatePath = path.resolve(__dirname, 'storageState.json');
    await page.context().storageState({ path: storageStatePath });
    console.log(`  E2E auth state saved → ${storageStatePath}`);
  } catch (e) {
    const screenshotPath = path.resolve(__dirname, 'storageStateError.png');
    await page.screenshot({ path: screenshotPath, fullPage: true });
    console.error(`\n  E2E global setup failed: ${(e as Error).message}`);
    console.error(`  Screenshot saved → ${screenshotPath}\n`);
    throw e;
  } finally {
    await browser.close();
  }
}

export default globalSetup;
