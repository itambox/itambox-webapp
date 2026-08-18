import { test, expect } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

const repoRoot = path.resolve(__dirname, '../../../../..');
const tablerCssPath = path.join(repoRoot, 'itambox/static/dist/vendor/tabler/css/tabler.min.css');
const appCssPath = path.join(repoRoot, 'itambox/static/dist/itambox.css');
const purchaseOrderTemplatePath = path.join(
  repoRoot,
  'itambox/procurement/templates/procurement/purchaseorder_detail.html',
);
const baseTemplatePath = path.join(repoRoot, 'itambox/templates/base.html');
const lineItemsTemplatePath = path.join(
  repoRoot,
  'itambox/procurement/templates/procurement/includes/purchaseorder_lines_container.html',
);
const mobileActionBarTemplatePath = path.join(repoRoot, 'itambox/templates/includes/mobile_action_bar.html');

async function loadAppStyles(page: import('@playwright/test').Page): Promise<void> {
  await page.addStyleTag({ path: tablerCssPath });
  await page.addStyleTag({ path: appCssPath });
}

const purchaseOrderHtml = `
  <div class="page">
    <div class="page-wrapper">
      <main class="page-body">
        <div class="container-fluid">
          <div class="row g-3">
            <div class="col-lg-8 col-12">
              <div class="card shadow-sm border-0 mb-3">
                <div class="card-header border-0 bg-transparent pb-0">
                  <h3 class="card-title text-secondary">Purchase Order Details</h3>
                </div>
                <div class="card-body">
                  <div class="datagrid">
                    <div class="datagrid-item">
                      <div class="datagrid-title">Order Number</div>
                      <div class="datagrid-content"><strong>PO-2026-0387</strong></div>
                    </div>
                    <div class="datagrid-item">
                      <div class="datagrid-title">Supplier</div>
                      <div class="datagrid-content" data-testid="supplier">Northwind Workplace Technology GmbH</div>
                    </div>
                    <div class="datagrid-item">
                      <div class="datagrid-title">Expected Delivery Date</div>
                      <div class="datagrid-content" data-testid="expected-delivery">2026-09-30</div>
                    </div>
                    <div class="datagrid-item csp-style-05fdc6fe4835">
                      <div class="datagrid-title">Notes</div>
                      <div class="datagrid-content csp-style-ee488c67b751" data-testid="notes">Deliver the laptops to the Berlin service desk and call reception before unloading.</div>
                    </div>
                  </div>
                </div>
              </div>
              <div class="card shadow-sm border-0">
                <div class="card-header border-0 bg-transparent pb-0">
                  <h3 class="card-title text-secondary">Line Items</h3>
                </div>
                <div class="card-body">
                  <div class="table-responsive mb-4">
                    <table class="table table-vcenter card-table">
                      <thead>
                        <tr>
                          <th>Item</th>
                          <th>Unit Price</th>
                          <th>Ordered</th>
                          <th>Received</th>
                          <th>Total Cost</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr>
                          <td data-label="Item" data-testid="line-item">Lenovo ThinkPad T14 Gen 6 <span class="badge bg-blue-lt ms-1">Asset Type</span></td>
                          <td data-label="Unit Price">1,249.00 EUR</td>
                          <td data-label="Ordered">12</td>
                          <td data-label="Received">0</td>
                          <td data-label="Total Cost">14,988.00 EUR</td>
                          <td class="text-end" data-label="">
                            <button class="btn btn-sm btn-outline-secondary" type="button" aria-label="Edit line item">Edit</button>
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
    <nav class="mobile-action-bar d-lg-none" aria-label="Quick actions">
      <div class="mobile-action-bar__inner">
        <div class="mobile-action-bar__item">
          <button type="button" class="mobile-action-bar__btn"><span>Add</span></button>
        </div>
        <a href="#search" class="mobile-action-bar__btn"><span>Search</span></a>
        <button type="button" class="mobile-action-bar__btn mobile-action-bar__btn--scanner" aria-label="Open scanner">
          <span class="mobile-action-bar__scanner-fab" aria-hidden="true"></span>
          <span>Scan</span>
        </button>
        <a href="#home" class="mobile-action-bar__btn"><span>Home</span></a>
        <a href="#audits" class="mobile-action-bar__btn"><span>Audits</span></a>
      </div>
    </nav>
  </div>
`;

test('purchase order detail stays within a 390px mobile viewport', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.setContent(purchaseOrderHtml);
  await loadAppStyles(page);

  const pageWidth = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(pageWidth.scrollWidth - pageWidth.clientWidth).toBeLessThanOrEqual(1);

  for (const testId of ['supplier', 'expected-delivery', 'notes', 'line-item']) {
    const content = page.getByTestId(testId);
    await expect(content).toBeVisible();
    const box = await content.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(pageWidth.clientWidth + 1);
  }

  const actionControls = page.locator('.mobile-action-bar__btn');
  await expect(actionControls).toHaveCount(5);
  for (let index = 0; index < await actionControls.count(); index += 1) {
    const control = actionControls.nth(index);
    await expect(control).toBeVisible();
    const box = await control.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);
    expect(box!.x).toBeGreaterThanOrEqual(-1);
    expect(box!.x + box!.width).toBeLessThanOrEqual(pageWidth.clientWidth + 1);
    await control.focus();
    expect(await control.evaluate((element) => document.activeElement === element)).toBe(true);
  }

  const purchaseOrderTemplate = fs.readFileSync(purchaseOrderTemplatePath, 'utf8');
  for (const requiredMarkup of [
    'class="datagrid"',
    'class="datagrid-item csp-style-05fdc6fe4835"',
    'class="datagrid-content csp-style-ee488c67b751"',
    '{{ object.supplier }}',
    '{{ object.expected_delivery_date|default:"-" }}',
    '{{ object.notes }}',
    "procurement/includes/purchaseorder_lines_container.html",
    'data-label="{% translate "Request" %}"',
    'data-label="{% translate "Status" %}"',
  ]) {
    expect(purchaseOrderTemplate).toContain(requiredMarkup);
  }

  const baseTemplate = fs.readFileSync(baseTemplatePath, 'utf8');
  expect(baseTemplate).toContain('{% include "includes/mobile_action_bar.html" %}');

  const mobileActionBarTemplate = fs.readFileSync(mobileActionBarTemplatePath, 'utf8');
  expect(mobileActionBarTemplate).toContain('class="mobile-action-bar d-lg-none"');
  expect(mobileActionBarTemplate).toContain('class="mobile-action-bar__inner"');
  expect((mobileActionBarTemplate.match(/mobile-action-bar__btn(?=["\s])/g) ?? []).length).toBe(5);

  const lineItemsTemplate = fs.readFileSync(lineItemsTemplatePath, 'utf8');
  for (const label of ['Item', 'Unit Price', 'Ordered', 'Received', 'Total Cost']) {
    expect(lineItemsTemplate.split(`data-label="{% translate "${label}" %}"`).length - 1).toBeGreaterThanOrEqual(2);
  }
  expect(lineItemsTemplate.split('data-label=""').length - 1).toBeGreaterThanOrEqual(2);
});
