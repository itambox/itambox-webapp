import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const repoRoot = path.resolve(__dirname, '../../../../..');
const brandTemplatePath = path.join(repoRoot, 'itambox', 'templates/global_includes/_brand_lockup.html');
const tablerCssPath = path.join(repoRoot, 'itambox/static/dist/vendor/tabler/css/tabler.min.css');
const itamboxCssPath = path.join(repoRoot, 'itambox/static/dist/itambox.css');

function renderedBrand(): string {
  return fs.readFileSync(brandTemplatePath, 'utf8')
    .replace("{{ brand_height|default:'36px' }}", '36px');
}

async function mountMobileHeader(page: import('@playwright/test').Page, width: number) {
  await page.setViewportSize({ width, height: 740 });
  await page.setContent(`
    <div class="page">
      <aside class="navbar navbar-vertical navbar-expand-lg">
        <div class="container-fluid">
          <button class="navbar-toggler" type="button" aria-label="Toggle navigation">
            <span class="navbar-toggler-icon"></span>
          </button>
          <h1 class="navbar-brand">
            <a href="#" class="d-flex align-items-center">${renderedBrand()}</a>
          </h1>
          <div data-testid="mobile-header-actions" class="d-flex d-lg-none align-items-center gap-2 ms-auto me-2">
            <button class="nav-link px-0" aria-label="Enable light mode">☼</button>
            <div class="nav-item"><a href="#" class="nav-link px-0" aria-label="Show notifications">♧</a></div>
            <div class="nav-item">
              <a href="#" class="nav-link d-flex align-items-center lh-1 text-reset p-0" aria-label="Open user menu">
                <div class="ps-1 text-end" style="max-width: 96px;">
                  <div class="fw-bold text-truncate small">Demo Administrator</div>
                  <div class="text-muted text-truncate" style="font-size: 0.65rem;">Admin</div>
                </div>
              </a>
            </div>
          </div>
        </div>
      </aside>
    </div>
  `);
  await page.addStyleTag({ path: tablerCssPath });
  await page.addStyleTag({ path: itamboxCssPath });
}

async function expectSingleRowHeader(page: import('@playwright/test').Page, maxLogoHeight: number) {
  const container = page.locator('.navbar-vertical > .container-fluid');
  const toggler = page.locator('.navbar-toggler');
  const brand = page.locator('.navbar-brand');
  const logo = brand.locator('svg');
  const actions = page.getByTestId('mobile-header-actions');
  const [containerBox, togglerBox, brandBox, logoBox, actionsBox] = await Promise.all([
    container.boundingBox(),
    toggler.boundingBox(),
    brand.boundingBox(),
    logo.boundingBox(),
    actions.boundingBox(),
  ]);

  expect(containerBox).not.toBeNull();
  expect(togglerBox).not.toBeNull();
  expect(brandBox).not.toBeNull();
  expect(logoBox).not.toBeNull();
  expect(actionsBox).not.toBeNull();

  const centerYs = [togglerBox!, brandBox!, actionsBox!].map(box => box.y + box.height / 2);
  expect(Math.max(...centerYs) - Math.min(...centerYs)).toBeLessThanOrEqual(1);
  expect(containerBox!.height).toBeLessThanOrEqual(56);
  expect(logoBox!.height).toBeLessThanOrEqual(maxLogoHeight);
  expect(logoBox!.x + logoBox!.width).toBeLessThanOrEqual(actionsBox!.x);
}

test('mobile header stays on one row at 360px with 28px logo', async ({ page }) => {
  await mountMobileHeader(page, 360);
  await expectSingleRowHeader(page, 28);
});

test('mobile header stays on one row at 320px with scaled-down logo', async ({ page }) => {
  await mountMobileHeader(page, 320);
  await expectSingleRowHeader(page, 22);
});
