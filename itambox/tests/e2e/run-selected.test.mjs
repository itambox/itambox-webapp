import assert from 'node:assert/strict';
import test from 'node:test';

import { finalPlaywrightStatus } from './run-selected.mjs';

test('maps Playwright aggregate success to the final passed attempt', () => {
  assert.equal(finalPlaywrightStatus({ status: 'expected' }, [{ status: 'passed' }]), 'passed');
  assert.equal(
    finalPlaywrightStatus({ status: 'flaky' }, [{ status: 'failed' }, { status: 'passed' }]),
    'passed',
  );
});

test('keeps skipped and failing outcomes fail-closed', () => {
  assert.equal(finalPlaywrightStatus({ status: 'skipped' }, []), 'skipped');
  assert.equal(finalPlaywrightStatus({ status: 'unexpected' }, [{ status: 'failed' }]), 'failed');
  assert.equal(finalPlaywrightStatus({ status: 'unexpected' }, [{ status: 'timedOut' }]), 'timed_out');
  assert.equal(finalPlaywrightStatus({ status: 'interrupted' }, []), 'interrupted');
  assert.equal(finalPlaywrightStatus({ status: 'mystery' }, []), 'unknown');
});
