import { defineConfig, devices } from '@playwright/test';
import * as path from 'path';

const authDir = path.resolve(__dirname, '.auth');
const jsonReport = process.env.PLAYWRIGHT_JSON_OUTPUT_NAME || path.resolve(__dirname, 'playwright-report/results.json');

const browserDefaults = {
  ...devices['Desktop Chrome'],
};

export default defineConfig({
  testDir: './spec',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['list'],
    ['json', { outputFile: jsonReport }],
  ],
  outputDir: path.resolve(__dirname, 'test-results'),
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },

  /* Start the Django dev server before running tests.
   * Set E2E_NO_WEBSERVER=1 to reuse an already-running server (e.g. during
   * development). In CI the server is started by the workflow for one shared,
   * disposable environment. */
  webServer: process.env.E2E_NO_WEBSERVER
    ? undefined
    : {
        command: process.platform === 'win32'
          ? '..\\.venv\\Scripts\\python.exe manage.py runserver 8000'
          : '../.venv/bin/python manage.py runserver 8000',
        url: 'http://localhost:8000/',
        reuseExistingServer: !process.env.CI,
        cwd: '../..',
        stdout: 'pipe',
        stderr: 'pipe',
        timeout: 30000,
      },

  projects: [
    {
      name: 'setup-admin',
      testDir: __dirname,
      testMatch: /auth\/admin\.setup\.ts/,
      use: { ...browserDefaults, storageState: undefined },
    },
    {
      name: 'setup-operator',
      testDir: __dirname,
      testMatch: /auth\/operator\.setup\.ts/,
      use: { ...browserDefaults, storageState: undefined },
    },
    {
      name: 'setup-viewer',
      testDir: __dirname,
      testMatch: /auth\/viewer\.setup\.ts/,
      use: { ...browserDefaults, storageState: undefined },
    },
    {
      name: 'setup-aggregate',
      testDir: __dirname,
      testMatch: /auth\/aggregate\.setup\.ts/,
      use: { ...browserDefaults, storageState: undefined },
    },
    {
      name: 'admin',
      dependencies: ['setup-admin', 'setup-aggregate'],
      grepInvert: /@(anonymous|non-destructive|operator|viewer)/,
      use: { ...browserDefaults, storageState: path.join(authDir, 'admin.json') },
    },
    {
      name: 'operator',
      dependencies: ['setup-operator'],
      grep: /@operator/,
      use: { ...browserDefaults, storageState: path.join(authDir, 'operator.json') },
    },
    {
      name: 'viewer',
      dependencies: ['setup-viewer'],
      grep: /@viewer/,
      use: { ...browserDefaults, storageState: path.join(authDir, 'viewer.json') },
    },
    {
      name: 'anonymous',
      grep: /@anonymous/,
      use: { ...browserDefaults, storageState: { cookies: [], origins: [] } },
    },
    {
      name: 'remote-smoke',
      grep: /@non-destructive/,
      use: { ...browserDefaults, storageState: undefined },
    },
  ],
});
