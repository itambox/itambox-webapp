import assert from 'node:assert/strict';
import test from 'node:test';

import { attemptIdentity, finalPlaywrightStatus } from './run-selected.mjs';

test('maps Playwright aggregate success to the final passed attempt', () => {
  assert.equal(finalPlaywrightStatus({ status: 'expected' }, [{ status: 'passed' }]), 'passed');
  assert.equal(
    finalPlaywrightStatus({ status: 'flaky' }, [{ status: 'failed' }, { status: 'passed' }]),
    'passed',
  );
});

test('synthesizes canonical identities for legacy specs without the fixture', () => {
  const testInfo = {
    spec: 'spec/accessibility/issue101.spec.ts',
    project: 'admin',
    id: 'd5271b4adfaf6988b612-0851ce23573cfd7af394',
    title: 'full-page dashboard has no axe violations',
  };
  const failed = { status: 'failed', retry: 0, attachments: [] };
  const passed = { status: 'passed', retry: 1, attachments: [] };

  const attempt0 = attemptIdentity(failed, testInfo, 0);
  const attempt1 = attemptIdentity(passed, testInfo, 1);

  assert.match(attempt0, /(?:^|-)r0(?:-|$)/);
  assert.match(attempt1, /(?:^|-)r1(?:-|$)/);
  assert.notEqual(attempt0, attempt1);
  assert.ok(!/[\u0000-\u001f\u007f]/.test(attempt0));
  assert.ok(!/[\u0000-\u001f\u007f]/.test(attempt1));
});

test('keeps an attested fixture identity that satisfies the attempt contract', () => {
  const testInfo = {
    spec: 'spec/apps/assets/asset-catalog-lifecycle.spec.ts',
    project: 'admin',
  };
  const result = {
    status: 'passed',
    retry: 0,
    attachments: [
      {
        name: 'e2e-identity',
        contentType: 'text/plain',
        body: Buffer.from('e2e-admin-w7-r0-33902272197-ab1d57415a0421ba933b-be118247f8b11fd2c868', 'utf8')
          .toString('base64'),
      },
    ],
  };

  assert.equal(
    attemptIdentity(result, testInfo, 0),
    'e2e-admin-w7-r0-33902272197-ab1d57415a0421ba933b-be118247f8b11fd2c868',
  );
});

test('synthesizes when the attested identity does not match the attempt retry', () => {
  const testInfo = {
    spec: 'spec/apps/lab.spec.ts',
    project: 'admin',
    annotations: [{ type: 'e2e-identity', description: 'e2e-admin-w0-r0-33902272197-lab' }],
  };

  const identity = attemptIdentity({ status: 'passed', retry: 1 }, testInfo, 1);

  assert.match(identity, /(?:^|-)r1(?:-|$)/);
  assert.notEqual(identity, 'e2e-admin-w0-r0-33902272197-lab');
});

test('keeps skipped and failing outcomes fail-closed', () => {
  assert.equal(finalPlaywrightStatus({ status: 'skipped' }, []), 'skipped');
  assert.equal(finalPlaywrightStatus({ status: 'unexpected' }, [{ status: 'failed' }]), 'failed');
  assert.equal(finalPlaywrightStatus({ status: 'unexpected' }, [{ status: 'timedOut' }]), 'timed_out');
  assert.equal(finalPlaywrightStatus({ status: 'interrupted' }, []), 'interrupted');
  assert.equal(finalPlaywrightStatus({ status: 'mystery' }, []), 'unknown');
});
