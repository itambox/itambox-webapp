import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

const itamboxRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const scannerTemplates = [
  'templates/includes/mobile_action_bar.html',
  'templates/assets/bulk_scan.html',
  'compliance/templates/compliance/audits/audit_session_detail.html',
];

test('scanner overlays are visible when AssetScanner adds is-open', () => {
  for (const relativePath of scannerTemplates) {
    const source = readFileSync(resolve(itamboxRoot, relativePath), 'utf8');
    assert.match(source, /class="scanner-overlay(?:"|\s)/, relativePath);
    assert.doesNotMatch(source, /class="scanner-overlay\s+d-none"/, relativePath);
  }
});

test('scanner camera errors are assertive live regions', () => {
  for (const relativePath of scannerTemplates) {
    const source = readFileSync(resolve(itamboxRoot, relativePath), 'utf8');
    assert.match(source, /scanner-error[\s\S]*?role="alert"[\s\S]*?aria-live="assertive"[\s\S]*?aria-atomic="true"/, relativePath);
  }
});

const basketScannerContracts = [
  {
    path: 'static/src/scan-basket.ts',
    readerId: 'basket-scanner-reader',
    modalId: 'basket-scanner-modal',
    openId: 'basket-open-scanner-btn',
    closeId: 'basket-close-scanner-btn',
    torchId: 'basket-toggle-torch-btn',
  },
  {
    path: 'static/src/audit-basket.ts',
    readerId: 'audit-scanner-reader',
    modalId: 'audit-scanner-modal',
    openId: 'audit-open-scanner-btn',
    closeId: 'audit-close-scanner-btn',
    torchId: 'audit-toggle-torch-btn',
  },
];

test('basket entrypoints wire camera and keyboard paths separately', () => {
  for (const contract of basketScannerContracts) {
    const source = readFileSync(resolve(itamboxRoot, contract.path), 'utf8');
    assert.match(source, /import \{ AssetScanner \} from ['"]\.\/scanner['"];?/, contract.path);
    assert.match(source, /new AssetScanner\(\{/, contract.path);
    assert.match(source, new RegExp(`readerId: '${contract.readerId}'`), contract.path);
    assert.match(source, new RegExp(`modalId: '${contract.modalId}'`), contract.path);
    assert.match(source, new RegExp(`openBtnId: '${contract.openId}'`), contract.path);
    assert.match(source, new RegExp(`closeBtnId: '${contract.closeId}'`), contract.path);
    assert.match(source, new RegExp(`torchId: '${contract.torchId}'`), contract.path);
    assert.match(source, /onResult\(code: string\)[\s\S]*?return addByCode\(code\);/, contract.path);
    assert.match(source, /addEventListener\('keydown'/, contract.path);
    assert.match(source, /event\.key !== 'Enter'/, contract.path);
  }
});
