import { expect, type Page } from '@playwright/test';

export type BrowserErrors = {
  console: string[];
  page: string[];
  server: string[];
};

export function createBrowserErrors(): BrowserErrors {
  return { console: [], page: [], server: [] };
}

export function attachBrowserErrorCollection(page: Page, errors: BrowserErrors): () => void {
  const onConsole = (message: import('@playwright/test').ConsoleMessage) => {
    if (message.type() === 'error') errors.console.push(message.text());
  };
  const onPageError = (error: Error) => errors.page.push(error.message);
  const onResponse = (response: import('@playwright/test').Response) => {
    if (response.status() >= 500) {
      // Keep query strings and response bodies out of diagnostics.
      const url = new URL(response.url());
      errors.server.push(`${response.status()} ${url.origin}${url.pathname}`);
    }
  };
  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('response', onResponse);
  return () => {
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
    page.off('response', onResponse);
  };
}

export function assertNoUnexpectedBrowserErrors(errors: BrowserErrors): void {
  expect(errors.page, 'Unexpected browser page errors').toEqual([]);
  expect(errors.console, 'Unexpected browser console errors').toEqual([]);
  expect(errors.server, 'Unexpected HTTP 5xx responses').toEqual([]);
}
