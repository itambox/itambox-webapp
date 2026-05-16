import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: './spec',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1, // Single worker to avoid DB locking issues in basic testing
  reporter: 'list',
  use: {
    baseURL: 'http://localhost:8000',
    trace: 'on-first-retry',
    storageState: 'storageState.json', // Use authenticated state
  },
  globalSetup: require.resolve('./global-setup'),

  /*
  webServer: {
    command: '..\\..\\.venv\\Scripts\\python.exe manage.py runserver 8000',
    url: 'http://localhost:8000/',
    reuseExistingServer: !process.env.CI,
    cwd: '..\\',
    stdout: 'ignore',
    stderr: 'pipe',
  },
  */

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
