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
