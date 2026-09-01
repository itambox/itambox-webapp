import { expect, test as base, type APIRequestContext, type Page } from '@playwright/test';
import { createCleanupRegistry, type CleanupRegistry } from './cleanup';
import { attestActiveTenant, assertSafeTarget, type ActiveTenant } from './tenant';
import {
  assertNoUnexpectedBrowserErrors,
  attachBrowserErrorCollection,
  createBrowserErrors,
  type BrowserErrors,
} from '../helpers/errors';
import { retrySafeName } from '../helpers/names';

export type E2EFixtures = {
  activeTenant: ActiveTenant | null;
  api: APIRequestContext;
  appErrors: BrowserErrors;
  cleanup: CleanupRegistry;
  runId: string;
  targetSafety: void;
};

async function closeWithCleanup(registry: CleanupRegistry): Promise<void> {
  await registry.run();
}

export const test = base.extend<E2EFixtures>({
  targetSafety: [
    async ({}, use, testInfo) => {
      assertSafeTarget(testInfo);
      await use(undefined);
    },
    { auto: true },
  ],

  api: async ({ playwright }, use) => {
    const token = process.env.E2E_API_TOKEN;
    if (!token) throw new Error('E2E_API_TOKEN is required for authenticated REST setup and readback.');
    const api = await playwright.request.newContext({
      baseURL: process.env.E2E_BASE_URL || 'http://localhost:8000',
      extraHTTPHeaders: { Authorization: `Token ${token}` },
    });
    try {
      await use(api);
    } finally {
      await api.dispose();
    }
  },

  appErrors: [
    async ({ page }, use) => {
      const errors = createBrowserErrors();
      const detach = attachBrowserErrorCollection(page, errors);
      try {
        await use(errors);
      } finally {
        detach();
      }
      assertNoUnexpectedBrowserErrors(errors);
    },
    { auto: true },
  ],

  cleanup: [
    async ({ page: _page, request: _request, api: _api }, use, testInfo) => {
      const registry = createCleanupRegistry();
      try {
        await use(registry);
      } finally {
        try {
          await closeWithCleanup(registry);
          testInfo.annotations.push({ type: 'e2e-cleanup', description: 'success' });
          await testInfo.attach('e2e-cleanup', {
            body: Buffer.from(JSON.stringify({ success: true }), 'utf8'),
            contentType: 'application/json',
          });
        } catch (error) {
          testInfo.annotations.push({ type: 'e2e-cleanup', description: 'failure' });
          await testInfo.attach('e2e-cleanup', {
            body: Buffer.from(JSON.stringify({ success: false }), 'utf8'),
            contentType: 'application/json',
          });
          throw error;
        }
      }
    },
    { auto: true },
  ],

  activeTenant: [
    async ({ page, api }, use, testInfo) => {
      if (
        ['anonymous', 'remote-smoke'].includes(testInfo.project.name)
        || testInfo.tags.includes('@aggregate')
      ) {
        await use(null);
        return;
      }
      await use(await attestActiveTenant(page, api));
    },
    { auto: true },
  ],

  runId: [
    async ({}, use, testInfo) => {
      const identity = retrySafeName(testInfo, testInfo.testId);
      testInfo.annotations.push({ type: 'e2e-identity', description: identity });
      await testInfo.attach('e2e-identity', {
        body: Buffer.from(identity, 'utf8'),
        contentType: 'text/plain',
      });
      const previous = process.env.E2E_CURRENT_TEST_ID;
      process.env.E2E_CURRENT_TEST_ID = identity;
      try {
        await use(identity);
      } finally {
        if (previous === undefined) delete process.env.E2E_CURRENT_TEST_ID;
        else process.env.E2E_CURRENT_TEST_ID = previous;
      }
    },
    { auto: true },
  ],
});

export { expect };
export type { Page };
