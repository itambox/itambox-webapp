import { test, expect } from '@playwright/test';
import * as path from 'path';

const repoRoot = path.resolve(__dirname, '../../../../..');
const tablerCssPath = path.join(repoRoot, 'itambox/static/dist/vendor/tabler/css/tabler.min.css');
const appCssPath = path.join(repoRoot, 'itambox/static/dist/itambox.css');

async function loadAppStyles(page: import('@playwright/test').Page): Promise<void> {
  await page.addStyleTag({ path: tablerCssPath });
  await page.addStyleTag({ path: appCssPath });
}

const changelogHtml = `
  <div class="container-fluid">
    <div class="row">
      <div class="col-12 col-md-5">
        <div class="card"><h2 class="card-header">Change Details</h2></div>
      </div>
      <div class="col-12 col-md-7">
        <div class="card"><h2 class="card-header">Difference</h2>
          <pre class="change-diff">{"status": "active"}</pre>
        </div>
      </div>
    </div>
    <div class="row">
      <div class="col-12 col-md-6">
        <div class="card"><h2 class="card-header">Pre-Change Data</h2>
          <pre class="change-data"><span class="context">{"tenant": "A"}</span></pre>
        </div>
      </div>
      <div class="col-12 col-md-6">
        <div class="card"><h2 class="card-header">Post-Change Data</h2>
          <pre class="change-data"><span class="added">{"tenant": "B"}</span></pre>
        </div>
      </div>
    </div>
  </div>
`;

async function backgroundColorSum(page: import('@playwright/test').Page, selector: string): Promise<number> {
  return page.locator(selector).first().evaluate((element) => {
    const rgb = getComputedStyle(element).backgroundColor.match(/\d+/g)?.map(Number) ?? [];
    return rgb.length >= 3 ? (rgb[0] + rgb[1] + rgb[2]) : -1;
  });
}

test('changelog code panels follow light-theme tokens in light mode', async ({ page }) => {
  await page.setContent(changelogHtml);
  await loadAppStyles(page);

  // Light mode: the panels must use the light surface token (white / stone-100),
  // NOT Tabler's dark bare-`pre` default (--tblr-bg-surface-dark).
  expect(await backgroundColorSum(page, 'pre.change-data')).toBeGreaterThan(500);
  expect(await backgroundColorSum(page, 'pre.change-diff')).toBeGreaterThan(500);
});

test('changelog code panels stay dark and readable in dark mode', async ({ page }) => {
  await page.setContent(changelogHtml);
  await page.evaluate(() => document.documentElement.setAttribute('data-bs-theme', 'dark'));
  await loadAppStyles(page);

  expect(await backgroundColorSum(page, 'pre.change-data')).toBeLessThan(200);
  expect(await backgroundColorSum(page, 'pre.change-diff')).toBeLessThan(200);
});

test('changelog modules stack full-width on mobile in details/difference/pre/post order', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(changelogHtml);
  await loadAppStyles(page);

  const headers = page.locator('.card-header');
  await expect(headers.nth(0)).toHaveText('Change Details');
  await expect(headers.nth(1)).toHaveText('Difference');
  await expect(headers.nth(2)).toHaveText('Pre-Change Data');
  await expect(headers.nth(3)).toHaveText('Post-Change Data');

  const tops: number[] = [];
  for (let i = 0; i < 4; i++) {
    const box = await headers.nth(i).boundingBox();
    expect(box).not.toBeNull();
    tops.push(box!.y);
  }
  // Strictly stacked: each module starts below the previous one.
  for (let i = 1; i < tops.length; i++) {
    expect(tops[i]).toBeGreaterThan(tops[i - 1]);
  }

  // Each card is full viewport width minus container gutters (~24px each side).
  const cardBox = await page.locator('.card').first().boundingBox();
  expect(cardBox).not.toBeNull();
  expect(cardBox!.width).toBeGreaterThan(340);

  // No horizontal page overflow on mobile.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test('desktop keeps the side-by-side comparison layout', async ({ page }) => {
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.setContent(changelogHtml);
  await loadAppStyles(page);

  const firstRow = page.locator('.row').first();
  const boxes = await firstRow.locator('.card').all();
  const b1 = await boxes[0].boundingBox();
  const b2 = await boxes[1].boundingBox();
  expect(b1).not.toBeNull();
  expect(b2).not.toBeNull();
  expect(b2!.x).toBeGreaterThan(b1!.x); // side-by-side
  expect(Math.abs(b1!.y - b2!.y)).toBeLessThan(5);
});
