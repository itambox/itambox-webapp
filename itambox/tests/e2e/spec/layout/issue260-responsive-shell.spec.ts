import { test, expect } from '@playwright/test';
import * as path from 'path';

const repoRoot = path.resolve(__dirname, '../../../../..');
const tablerCssPath = path.join(repoRoot, 'itambox/static/dist/vendor/tabler/css/tabler.min.css');
const appCssPath = path.join(repoRoot, 'itambox/static/dist/itambox.css');
const appJsPath = path.join(repoRoot, 'itambox/static/dist/itambox.js');

async function loadAppStyles(page: import('@playwright/test').Page): Promise<void> {
  await page.addStyleTag({ path: tablerCssPath });
  await page.addStyleTag({ path: appCssPath });
}

test('mobile footer reserves the fixed action bar and exposes GraphQL', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(`
    <div class="page">
      <div class="page-wrapper">
        <main class="page-body"><div style="height: 500px"></div></main>
        <footer class="footer footer-transparent d-print-none py-2">
          <div class="container-fluid d-flex justify-content-between align-items-center">
            <ul class="list-inline mb-0 fs-2">
              <li class="list-inline-item"><a href="/graphql/" aria-label="GraphQL API">GraphQL</a></li>
            </ul>
          </div>
        </footer>
      </div>
      <div class="mobile-action-bar d-lg-none">
        <div class="mobile-action-bar__inner"><span>Quick actions</span></div>
      </div>
    </div>
  `);
  await loadAppStyles(page);

  const footer = page.locator('footer.footer');
  const graphqlLink = footer.locator('a[href="/graphql/"]');
  await expect(graphqlLink).toBeVisible();
  const paddingBottom = await footer.evaluate((element) => parseFloat(getComputedStyle(element).paddingBottom));
  expect(paddingBottom).toBeGreaterThanOrEqual(60);
});

test('mobile topbar clears transient hover but preserves focus and expanded state', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(`
    <div class="mobile-topbar-actions">
      <button class="nav-link" type="button">Theme</button>
      <div class="nav-item">
        <a class="nav-link" href="#" aria-expanded="false">User</a>
      </div>
    </div>
  `);
  await loadAppStyles(page);

  const theme = page.locator('.mobile-topbar-actions > .nav-link');
  await theme.hover();
  await page.waitForTimeout(350);
  const hoverBackground = await theme.evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(hoverBackground).toBe('rgba(0, 0, 0, 0)');

  await theme.focus();
  const focusOutline = await theme.evaluate((element) => getComputedStyle(element).outlineStyle);
  expect(focusOutline).toBe('solid');

  const userLink = page.locator('.mobile-topbar-actions .nav-item > .nav-link');
  await userLink.evaluate((element) => {
    element.setAttribute('aria-expanded', 'true');
    element.parentElement?.classList.add('show');
  });
  const expandedBackground = await userLink.evaluate((element) => getComputedStyle(element).backgroundColor);
  expect(expandedBackground).not.toBe('rgba(0, 0, 0, 0)');
});

test('mobile select-all mirrors row selection, tri-state, and empty scopes', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(`
    <div class="js-selection-scope" data-testid="with-rows">
      <div class="mobile-select-all">
        <label class="mobile-select-all__label"><input type="checkbox" data-select-all="true" aria-label="Select all on this page"></label>
      </div>
      <table class="card-table">
        <thead><tr><th><input type="checkbox" name="select_all" data-select-all="true" aria-label="Toggle all"></th></tr></thead>
        <tbody>
          <tr><td><input type="checkbox" name="pk" value="1"></td></tr>
          <tr><td><input type="checkbox" name="pk" value="2"></td></tr>
        </tbody>
      </table>
    </div>
    <div class="js-selection-scope" data-testid="without-rows">
      <input type="checkbox" data-select-all="true" aria-label="Select all on this page">
    </div>
  `);
  await loadAppStyles(page);
  await page.evaluate(() => {
    (window as typeof window & { gettext: (value: string) => string }).gettext = (value) => value;
    (window as typeof window & { interpolate: (value: string, vars: { count: number }) => string }).interpolate = (value, vars) => value.replace('%(count)s', String(vars.count));
  });
  await page.addScriptTag({ path: appJsPath });

  const rows = page.getByTestId('with-rows').locator('input[name="pk"]');
  const mobileSelectAll = page.getByTestId('with-rows').locator('.mobile-select-all [data-select-all]');
  const headerSelectAll = page.getByTestId('with-rows').locator('thead [data-select-all]');
  const emptySelectAll = page.getByTestId('without-rows').locator('[data-select-all]');

  await expect(mobileSelectAll).toBeEnabled();
  await expect(emptySelectAll).toBeDisabled();
  const mobileLabelBox = await page.getByTestId('with-rows').locator('.mobile-select-all__label').boundingBox();
  expect(mobileLabelBox).not.toBeNull();
  expect(mobileLabelBox!.height).toBeGreaterThanOrEqual(44);

  await rows.nth(0).check();
  await expect(mobileSelectAll).toBeChecked({ checked: false });
  expect(await mobileSelectAll.evaluate((element) => (element as HTMLInputElement).indeterminate)).toBe(true);
  expect(await headerSelectAll.evaluate((element) => (element as HTMLInputElement).indeterminate)).toBe(true);

  await mobileSelectAll.check();
  await expect(rows.nth(0)).toBeChecked();
  await expect(rows.nth(1)).toBeChecked();
  await expect(headerSelectAll).toBeChecked();
  expect(await mobileSelectAll.evaluate((element) => (element as HTMLInputElement).indeterminate)).toBe(false);

  await rows.nth(0).uncheck();
  await expect(mobileSelectAll).toBeChecked({ checked: false });
  expect(await mobileSelectAll.evaluate((element) => (element as HTMLInputElement).indeterminate)).toBe(true);

  await mobileSelectAll.check();
  await expect(rows.nth(0)).toBeChecked();
  await expect(rows.nth(1)).toBeChecked();
  await mobileSelectAll.uncheck();
  await expect(rows.nth(0)).not.toBeChecked();
  await expect(rows.nth(1)).not.toBeChecked();
});

test('mobile edit and camera actions remain accessible icon-only touch targets', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 740 });
  await page.setContent(`
    <div class="page-header">
      <a href="/edit/"
         class="btn btn-primary detail-edit-action"
         aria-label="Edit"
         title="Edit">
        <i class="mdi mdi-pencil-outline" aria-hidden="true"></i>
        <span class="detail-edit-action__label ms-1">Edit</span>
      </a>
    </div>
    <div class="input-group input-group-lg">
      <input id="scan-basket-input" class="form-control">
      <button type="button"
              class="btn btn-primary scan-basket-camera"
              id="basket-open-scanner-btn"
              aria-label="Camera"
              title="Camera">
        <i class="mdi mdi-camera" aria-hidden="true"></i>
        <span class="visually-hidden">Camera</span>
      </button>
    </div>
  `);
  await loadAppStyles(page);

  const edit = page.getByRole('link', { name: 'Edit' });
  const camera = page.getByRole('button', { name: 'Camera' });
  await expect(edit).toBeVisible();
  await expect(camera).toBeVisible();
  await expect(edit.locator('.detail-edit-action__label')).toBeHidden();
  const hiddenCameraLabel = await camera.locator('.visually-hidden').evaluate((element) => {
    const style = getComputedStyle(element);
    return style.position === 'absolute'
      && style.overflow === 'hidden'
      && parseFloat(style.width) <= 1
      && parseFloat(style.height) <= 1;
  });
  expect(hiddenCameraLabel).toBe(true);
  const editBox = await edit.boundingBox();
  const cameraBox = await camera.boundingBox();
  expect(editBox).not.toBeNull();
  expect(cameraBox).not.toBeNull();
  expect(editBox!.width).toBeGreaterThanOrEqual(44);
  expect(editBox!.height).toBeGreaterThanOrEqual(44);
  expect(cameraBox!.width).toBeGreaterThanOrEqual(44);
  expect(cameraBox!.height).toBeGreaterThanOrEqual(44);
});
