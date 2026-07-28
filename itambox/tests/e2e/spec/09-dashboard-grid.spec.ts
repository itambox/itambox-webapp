import { expect, test, type Locator } from '@playwright/test';

type GridItemState = {
  width: number;
  height: number;
};

async function readGridItemState(item: Locator): Promise<GridItemState> {
  return item.evaluate((element) => {
    const node = (element as HTMLElement & { gridstackNode?: { w?: number; h?: number } }).gridstackNode;
    if (!node?.w || !node?.h) {
      throw new Error('GridStack did not attach layout metadata to the dashboard widget.');
    }
    return { width: node.w, height: node.h };
  });
}

test('dashboard grid initializes and persists a resized widget', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });

  const gridRuntimeErrors: string[] = [];
  page.on('pageerror', error => gridRuntimeErrors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'warning' && message.text().includes('GridStack init error')) {
      gridRuntimeErrors.push(message.text());
    }
  });

  await page.goto('/', { waitUntil: 'networkidle' });

  const grid = page.locator('#dashboard-grid');
  await expect(grid).toHaveClass(/\bgrid-stack\b/);
  await expect.poll(() => page.evaluate(() => (window as Window & { __gsInitialized?: boolean }).__gsInitialized)).toBe(true);

  const item = grid.locator('.grid-stack-item').first();
  await expect(item).toBeVisible();
  const original = await readGridItemState(item);

  await page.locator('#unlock-dashboard').click();
  await expect(page.locator('#dashboard-unlocked-controls')).toBeVisible();

  const resizeHandle = item.locator('.ui-resizable-se');
  await expect(resizeHandle).toBeVisible();
  const handleBox = await resizeHandle.boundingBox();
  if (!handleBox) {
    throw new Error('GridStack resize handle has no bounding box.');
  }

  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox.x + 130, handleBox.y + 130, { steps: 8 });
  await page.mouse.up();

  await expect.poll(async () => {
    const resized = await readGridItemState(item);
    return resized.width !== original.width || resized.height !== original.height;
  }).toBe(true);
  const resized = await readGridItemState(item);

  const saveResponse = page.waitForResponse(response =>
    response.request().method() === 'POST' && response.url().includes('/save-layout/'),
  );
  await page.locator('#save-dashboard').click();
  expect((await saveResponse).ok()).toBe(true);

  await page.reload({ waitUntil: 'networkidle' });
  await expect.poll(() => page.evaluate(() => (window as Window & { __gsInitialized?: boolean }).__gsInitialized)).toBe(true);

  const persisted = await readGridItemState(page.locator('#dashboard-grid .grid-stack-item').first());
  expect(persisted).toEqual(resized);
  expect(gridRuntimeErrors).toEqual([]);
});
