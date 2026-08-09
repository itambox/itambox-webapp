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

const scannerMarkupContracts = [
  {
    path: 'templates/includes/mobile_action_bar.html',
    ids: [
      'global-open-scanner-btn',
      'global-scanner-modal',
      'global-scanner-reader',
      'global-toggle-torch-btn',
      'global-close-scanner-btn',
      'global-scanner-error',
      'global-scanner-feedback',
    ],
  },
  {
    path: 'templates/assets/bulk_scan.html',
    ids: [
      'basket-open-scanner-btn',
      'basket-scanner-modal',
      'basket-scanner-reader',
      'basket-toggle-torch-btn',
      'basket-close-scanner-btn',
      'basket-scanner-error',
      'basket-scan-feedback',
      'basket-scanner-count',
    ],
  },
  {
    path: 'compliance/templates/compliance/audits/audit_session_detail.html',
    ids: [
      'audit-open-scanner-btn',
      'audit-scanner-modal',
      'audit-scanner-reader',
      'audit-toggle-torch-btn',
      'audit-close-scanner-btn',
      'audit-scanner-error',
      'audit-scan-feedback',
      'audit-scanner-count',
    ],
  },
];

test('rendered scanner markup keeps global, bulk, and audit IDs distinct', () => {
  const owners = new Map();
  for (const contract of scannerMarkupContracts) {
    const source = readFileSync(resolve(itamboxRoot, contract.path), 'utf8');
    for (const id of contract.ids) {
      assert.match(source, new RegExp(`id="${id}"`), `${contract.path} is missing ${id}`);
      assert.equal(owners.has(id), false, `${id} is shared by ${owners.get(id)} and ${contract.path}`);
      owners.set(id, contract.path);
    }
  }
});

test('basket keyboard handlers pass Enter values directly to addByCode', () => {
  for (const contract of basketScannerContracts) {
    const source = readFileSync(resolve(itamboxRoot, contract.path), 'utf8');
    const enterHandler = source.match(/input\.addEventListener\('keydown',[\s\S]*?\n\s*}\);/);
    assert.ok(enterHandler, `${contract.path} is missing its keyboard handler`);
    assert.match(enterHandler[0], /const value = input\.value;/, contract.path);
    assert.match(enterHandler[0], /addByCode\(value\);/, contract.path);
    assert.doesNotMatch(enterHandler[0], /dispatcher\./, contract.path);
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
    assert.match(source, /cameraScanner\s*=\s*new AssetScanner\(\{/, contract.path);
    assert.match(source, new RegExp(`readerId: '${contract.readerId}'`), contract.path);
    assert.match(source, new RegExp(`modalId: '${contract.modalId}'`), contract.path);
    assert.match(source, new RegExp(`openBtnId: '${contract.openId}'`), contract.path);
    assert.match(source, new RegExp(`closeBtnId: '${contract.closeId}'`), contract.path);
    assert.match(source, new RegExp(`torchId: '${contract.torchId}'`), contract.path);
    assert.match(source, /onResult\(code: string, sessionGeneration: number\)[\s\S]*?return addByCode\(code, sessionGeneration\);/, contract.path);
    assert.match(source, /addEventListener\('keydown'/, contract.path);
    assert.match(source, /event\.key !== 'Enter'/, contract.path);
    assert.match(source, /addByCode\(value\);/, contract.path);
    assert.match(source, /isCurrentCameraAction\(sessionGeneration\)/, contract.path);
  }
});
