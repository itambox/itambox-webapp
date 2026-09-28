import { expect, test, type Locator, type Page } from '@playwright/test';

/**
 * Qualifies the ECharts-based dashboard charts end to end:
 *  - initial rendering (canvas size, live instance, seed data)
 *  - tooltip values sourced from the rendered dataset
 *  - light/dark switching through the real toggle
 *  - HTMX (boosted) replacement re-initializes the charts without duplicates
 *  - GridStack widget resize grows the chart instead of clipping it
 *  - empty datasets render the localized empty state
 *  - hostile label text stays inert (no HTML/JavaScript execution)
 *  - production CSP compatibility and a console/error-free run
 */

const STATUS_SELECTOR = '.itambox-status-labels-chart';
const AGE_SELECTOR = '.itambox-asset-age-chart';

const IGNORED_CONSOLE_PATTERNS = [/favicon/i, /source.?map/i, /devtools/i, /djDebug|debug.toolbar/i];

function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
  page.on('console', (message) => {
    if (message.type() !== 'error') return;
    const text = message.text();
    if (IGNORED_CONSOLE_PATTERNS.some((pattern) => pattern.test(text))) return;
    errors.push(`console: ${text}`);
  });
  return errors;
}

type ChartState = {
  canvases: number;
  instanceId: string | null;
  width: number;
  height: number;
  counts: number[];
};

async function readChartState(chart: Locator): Promise<ChartState> {
  return chart.evaluate((element) => {
    const canvas = element.querySelector('canvas');
    const box = (canvas ?? element).getBoundingClientRect();
    let counts: number[] = [];
    try {
      const parsed = JSON.parse(element.getAttribute('data-chart-data') || '[]');
      counts = Array.isArray(parsed) ? parsed.map((entry) => Number((entry as { count?: unknown }).count ?? 0)) : [];
    } catch {
      counts = [];
    }
    return {
      canvases: element.querySelectorAll('canvas').length,
      instanceId: element.getAttribute('_echarts_instance_'),
      width: box.width,
      height: box.height,
      counts,
    };
  });
}

async function removeDebugToolbar(page: Page): Promise<void> {
  const debugToolbar = page.locator('#djDebug');
  if (await debugToolbar.count()) {
    await debugToolbar.evaluate((element) => element.remove());
  }
}

/** Hovers the chart ring and returns the tooltip text reporting an asset count. */
async function hoverForTooltip(page: Page, chart: Locator): Promise<string> {
  await chart.scrollIntoViewIfNeeded();
  const box = await chart.boundingBox();
  if (!box) throw new Error('Chart container has no bounding box.');
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height * 0.44;
  const ring = Math.min(box.width, box.height) * 0.26;
  const candidates: Array<[number, number]> = [
    [centerX + ring, centerY],
    [centerX + ring * 0.85, centerY],
    [centerX, centerY + ring],
    [centerX - ring, centerY],
    [centerX, centerY - ring],
    [centerX + ring * 0.7, centerY + ring * 0.7],
  ];
  const covered: string[] = [];
  for (const [x, y] of candidates) {
    // A fixed overlay (sidebar, debug toolbar, floating chrome) would swallow
    // the hover silently; only drive the mouse when the chart owns the point.
    const topmost = await chart.evaluate(
      (element, [px, py]) => {
        const top = document.elementFromPoint(px, py);
        const covering = top
          ? `${top.tagName}${top.id ? '#' + top.id : ''}${
              typeof top.className === 'string' && top.className ? '.' + top.className.split(' ')[0] : ''
            }`
          : 'none';
        return { owns: top !== null && element.contains(top), covering };
      },
      [x, y] as [number, number],
    );
    if (!topmost.owns) {
      covered.push(`${Math.round(x)},${Math.round(y)} -> ${topmost.covering}`);
      continue;
    }
    await page.mouse.move(x, y);
    const tooltip = page.locator('text=/\\d+ assets/').first();
    try {
      await tooltip.waitFor({ state: 'visible', timeout: 2000 });
      return (await tooltip.innerText()).replace(/\s+/g, ' ').trim();
    } catch {
      // Missed a slice boundary; try the next ring position.
    }
  }
  throw new Error(
    `No slice tooltip appeared on the chart within the expected positions (covered candidates: ${
      covered.join('; ') || 'none'
    }).`,
  );
}

test('dashboard charts render with ECharts, keep their data and tooltips, and need no CSP exception', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors = collectPageErrors(page);

  const response = await page.goto('/', { waitUntil: 'networkidle' });
  expect(response, 'dashboard document must be served').not.toBeNull();
  const csp = response?.headers()['content-security-policy'] ?? '';
  expect(csp, 'production CSP header is served').toContain("style-src-attr 'unsafe-inline'");
  expect(csp, 'the charts must not rely on unsafe-eval').not.toContain('unsafe-eval');

  await removeDebugToolbar(page);

  const statusChart = page.locator(STATUS_SELECTOR);
  await expect(statusChart).toHaveCount(1);
  await expect(statusChart).toBeVisible();
  await expect.poll(async () => (await readChartState(statusChart)).canvases).toBe(1);

  const statusState = await readChartState(statusChart);
  expect(statusState.instanceId, 'a live ECharts instance is attached').toBeTruthy();
  expect(statusState.width).toBeGreaterThan(0);
  expect(statusState.height).toBeGreaterThan(0);
  expect(statusState.counts.length).toBeGreaterThan(0);
  expect(statusState.counts.reduce((sum, value) => sum + value, 0)).toBeGreaterThan(0);

  const ageChart = page.locator(AGE_SELECTOR);
  await expect(ageChart).toHaveCount(1);
  await expect.poll(async () => (await readChartState(ageChart)).canvases).toBe(1);
  const ageState = await readChartState(ageChart);
  expect(ageState.instanceId, 'a live ECharts instance is attached').toBeTruthy();

  const tooltipText = await hoverForTooltip(page, statusChart);
  const tooltipMatch = tooltipText.match(/(\d[\d.,]*)\s+assets/);
  expect(tooltipMatch, `tooltip "${tooltipText}" must report an asset count`).not.toBeNull();
  const tooltipValue = Number(tooltipMatch![1].replace(/[^\d]/g, ''));
  expect(statusState.counts, 'the tooltip value comes from the rendered dataset').toContain(tooltipValue);

  // A repeated initialization pass must not create duplicate instances.
  await page.evaluate(() => document.body.dispatchEvent(new Event('htmx:afterSettle')));
  await expect.poll(async () => (await readChartState(statusChart)).canvases).toBe(1);
  expect((await readChartState(statusChart)).instanceId).toBe(statusState.instanceId);
  expect((await readChartState(ageChart)).canvases).toBe(1);

  expect(errors).toEqual([]);
});

test('theme switching, a HTMX swap, and a GridStack resize keep single chart instances', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors = collectPageErrors(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await removeDebugToolbar(page);

  const statusChart = page.locator(STATUS_SELECTOR);
  await expect.poll(async () => (await readChartState(statusChart)).instanceId).toBeTruthy();
  const before = await readChartState(statusChart);

  // Light <-> dark switching through the real toggle re-renders without re-mounting.
  const html = page.locator('html');
  const initialTheme = await html.getAttribute('data-bs-theme');
  await page.locator('.color-mode-toggle:visible').first().click();
  await expect.poll(async () => html.getAttribute('data-bs-theme')).not.toBe(initialTheme);
  await expect.poll(async () => (await readChartState(statusChart)).canvases).toBe(1);
  expect((await readChartState(statusChart)).instanceId).toBe(before.instanceId);
  await page.locator('.color-mode-toggle:visible').first().click();
  await expect.poll(async () => html.getAttribute('data-bs-theme')).toBe(initialTheme);

  // A boosted navigation to the dashboard replaces the content and re-mounts the charts.
  const swapResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname === '/' &&
      response.request().method() === 'GET' &&
      response.request().headers()['hx-request'] === 'true'
    );
  });
  await page.getByRole('link', { name: 'Home' }).first().click();
  expect((await swapResponse).ok()).toBe(true);
  await expect(statusChart).toBeVisible();
  await expect.poll(async () => (await readChartState(statusChart)).canvases).toBe(1);
  const afterSwap = await readChartState(statusChart);
  expect(afterSwap.instanceId, 'the swapped-in chart is re-initialized').toBeTruthy();
  expect(afterSwap.counts.length).toBeGreaterThan(0);
  expect(await page.locator(STATUS_SELECTOR).count()).toBe(1);
  expect(await page.locator(AGE_SELECTOR).count()).toBe(1);

  // A GridStack resize grows the chart canvas instead of clipping it.
  const chartItem = page.locator('.grid-stack-item', { has: page.locator(STATUS_SELECTOR) });
  await expect(chartItem).toHaveCount(1);
  await page.locator('#unlock-dashboard').click();
  await expect(page.locator('#dashboard-unlocked-controls')).toBeVisible();
  await chartItem.hover();
  const handle = chartItem.locator('.ui-resizable-se');
  await expect(handle).toBeVisible();
  const handleBox = await handle.boundingBox();
  if (!handleBox) throw new Error('GridStack resize handle has no bounding box.');

  const widthBefore = (await readChartState(statusChart)).width;
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleBox.x + 120, handleBox.y + 120, { steps: 8 });
  await page.mouse.up();

  await expect.poll(async () => (await readChartState(statusChart)).width).toBeGreaterThan(widthBefore);
  expect((await readChartState(statusChart)).canvases).toBe(1);
  await page.locator('#lock-dashboard').click();

  expect(errors).toEqual([]);
});

test('empty chart datasets and hostile labels stay safe at runtime', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  const errors = collectPageErrors(page);
  await page.goto('/', { waitUntil: 'networkidle' });
  await removeDebugToolbar(page);

  // Probe containers exercise the runtime paths that the seeded data cannot reach:
  // an empty dataset and a label carrying an HTML/JavaScript injection attempt.
  await page.evaluate(() => {
    const host = document.createElement('div');
    host.id = 'e2e-chart-probe-host';
    host.style.width = '320px';
    // Keep the probe above fixed chrome (sidebar, toolbars): a body-level block
    // at x=0 would sit underneath the sidebar and swallow every hover.
    host.style.position = 'fixed';
    host.style.right = '24px';
    host.style.bottom = '24px';
    host.style.zIndex = '5000';

    const empty = document.createElement('div');
    empty.id = 'e2e-empty-chart';
    empty.className = 'itambox-status-labels-chart';
    empty.style.width = '320px';
    empty.style.height = '240px';
    empty.setAttribute('data-chart-type', 'doughnut');
    empty.setAttribute('data-chart-data', '[]');

    const hostile = document.createElement('div');
    hostile.id = 'e2e-hostile-chart';
    hostile.className = 'itambox-status-labels-chart';
    hostile.style.width = '320px';
    hostile.style.height = '240px';
    hostile.setAttribute('data-chart-type', 'pie');
    hostile.setAttribute(
      'data-chart-data',
      JSON.stringify([{ name: '<img src=x onerror="window.__chartXss=1">', count: 5, color: '#206bc4' }]),
    );

    host.append(empty, hostile);
    document.body.append(host);
  });
  await page.evaluate(() => document.body.dispatchEvent(new Event('htmx:afterSettle')));

  const empty = page.locator('#e2e-empty-chart');
  await expect(empty).toContainText('No assets assigned to active status labels.');
  expect(await empty.locator('canvas').count()).toBe(0);

  const hostile = page.locator('#e2e-hostile-chart');
  await expect.poll(async () => (await readChartState(hostile)).canvases).toBe(1);
  const tooltipText = await hoverForTooltip(page, hostile);
  expect(tooltipText, 'the hostile label is rendered as inert text').toContain('<img');
  const payload = await page.evaluate(() => ({
    injected: document.querySelectorAll('img[src="x"]').length,
    flag: (window as Window & { __chartXss?: number }).__chartXss,
  }));
  expect(payload.injected, 'no element is created from the label text').toBe(0);
  expect(payload.flag, 'no script from the label text executed').toBeUndefined();

  // Removing the probe host releases its instances through the HTMX cleanup path.
  await page.evaluate(() => {
    const host = document.getElementById('e2e-chart-probe-host');
    if (host) {
      document.body.dispatchEvent(new CustomEvent('htmx:beforeCleanupElement', { detail: { elt: host } }));
      host.remove();
    }
  });

  expect(errors).toEqual([]);
});
