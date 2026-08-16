import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const repoRoot = path.resolve(__dirname, '../../../../..');
const tablerCssPath = path.join(repoRoot, 'itambox/static/dist/vendor/tabler/css/tabler.min.css');
const appCssPath = path.join(repoRoot, 'itambox/static/dist/itambox.css');
const activityTemplatePath = path.join(
  repoRoot,
  'itambox/extras/templates/extras/dashboard/widgets/activity.html',
);

async function loadAppStyles(page: import('@playwright/test').Page): Promise<void> {
  await page.addStyleTag({ path: tablerCssPath });
  await page.addStyleTag({ path: appCssPath });
}

const activityLabels = ['Time', 'User', 'Full Name', 'Action', 'Type', 'Object', 'Request ID'];

const changelogWidgetHtml = `
  <div class="table-responsive">
    <table class="table table-vcenter card-table table-striped table-hover mb-0">
      <thead>
        <tr>
          ${activityLabels.map((label) => `<th>${label}</th>`).join('')}
        </tr>
      </thead>
      <tbody>
        <tr>
          <td class="text-nowrap text-secondary" data-label="Time">2026-08-16 12:34</td>
          <td class="text-nowrap" data-label="User"><span class="fw-medium">qonTrixz</span></td>
          <td class="text-nowrap text-secondary" data-label="Full Name">René Rettig</td>
          <td data-label="Action"><span class="badge bg-info">Updated</span></td>
          <td class="text-nowrap text-secondary" data-label="Type">Asset</td>
          <td class="text-nowrap text-truncate pe-0" data-label="Object"><a href="/assets/1/">A very long asset name</a></td>
          <td class="text-nowrap text-secondary" data-label="Request ID" title="request-123456789">request-123456789</td>
        </tr>
      </tbody>
    </table>
  </div>
`;

test('dashboard changelog cards expose headings and right-align values on mobile', async ({ page }) => {
  const activityTemplate = fs.readFileSync(activityTemplatePath, 'utf8');
  for (const label of activityLabels) {
    expect(activityTemplate).toContain(`data-label="{% translate \"${label}\" %}"`);
  }

  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(changelogWidgetHtml);
  await loadAppStyles(page);

  const cells = page.locator('tbody td');
  await expect(cells).toHaveCount(activityLabels.length);

  const mobileCells = await cells.evaluateAll((elements) =>
    elements.map((element) => ({
      label: element.getAttribute('data-label'),
      textAlign: getComputedStyle(element).textAlign,
      beforeContent: getComputedStyle(element, '::before').content,
    })),
  );

  expect(mobileCells.map((cell) => cell.label)).toEqual(activityLabels);
  expect(mobileCells.every((cell) => cell.textAlign === 'right')).toBe(true);
  expect(mobileCells.map((cell) => cell.beforeContent.replace(/^"|"$/g, ''))).toEqual(activityLabels);

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});

test('dashboard changelog last card heading has no sticky shadow in dark mobile mode', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(changelogWidgetHtml);
  await page.evaluate(() => document.documentElement.setAttribute('data-bs-theme', 'dark'));
  await loadAppStyles(page);

  const lastCell = page.locator('tbody td').last();
  await expect(lastCell).toHaveAttribute('data-label', 'Request ID');
  const lastCellBefore = await lastCell.evaluate((element) => ({
    content: getComputedStyle(element, '::before').content,
    backgroundImage: getComputedStyle(element, '::before').backgroundImage,
  }));

  expect(lastCellBefore.content).toBe('"Request ID"');
  expect(lastCellBefore.backgroundImage).toBe('none');
});
