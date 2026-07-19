import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

export default defineConfig({
  testDir: './spec',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Single worker to avoid DB locking issues in basic testing
  reporter: 'list',
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
    trace: 'on-first-retry',
    storageState: path.resolve(__dirname, 'storageState.json'), // Use authenticated state
  },
  globalSetup: require.resolve('./global-setup'),

  /* Start the Django dev server before running tests.
   * Set E2E_NO_WEBSERVER=1 to reuse an already-running server (e.g. during
   * development). In CI the server is started by this block automatically. */
  webServer: process.env.E2E_NO_WEBSERVER
    ? undefined
    : {
        command: process.platform === 'win32'
          ? '..\\..\\.venv\\Scripts\\python.exe manage.py runserver 8000'
          : '../../.venv/bin/python manage.py runserver 8000',
        url: 'http://localhost:8000/',
        reuseExistingServer: !process.env.CI,
        cwd: '..',
        stdout: 'pipe',
        stderr: 'pipe',
        timeout: 30000,
      },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
