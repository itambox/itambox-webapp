import { expect, type Page } from '@playwright/test';

/** Exercise one exact application route without hiding a missing surface. */
export async function openOwnedSurface(page: Page, route: string, heading: RegExp): Promise<void> {
  const response = await page.goto(route, { waitUntil: 'domcontentloaded' });
  expect(response, `GET ${route} must return a document`).not.toBeNull();
  expect(response!.status(), `GET ${route}`).toBe(200);
  await expect(page.locator('h2.page-title'), `page heading for ${route}`).toContainText(heading);
}
