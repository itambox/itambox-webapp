import { expect, type Page } from '@playwright/test';

export async function expectHtmxTrigger(page: Page, path: string, trigger: string): Promise<void> {
  const response = await page.waitForResponse((candidate) => {
    const url = new URL(candidate.url());
    return candidate.request().method() === 'POST' && url.pathname === path;
  });
  expect(response.status(), `POST ${path}`).toBe(204);
  expect(response.headers()['hx-trigger'], `HX-Trigger for ${path}`).toContain(trigger);
}

export async function waitForHtmxIdle(page: Page, root: string): Promise<void> {
  await expect(page.locator(root), `HTMX root ${root}`).toHaveAttribute('aria-busy', 'false');
}
