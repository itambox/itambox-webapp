import assert from 'node:assert/strict';
import test from 'node:test';

import { parseSuperuserCount } from './preflight-output.mjs';


test('parseSuperuserCount extracts a marked count from noisy Django output', () => {
  const output = [
    'UserWarning: insecure development key',
    '2026-07-19 INFO xmlschema resource loaded',
    '__E2E_SUPERUSER_COUNT__=1',
    '',
  ].join('\n');

  assert.equal(parseSuperuserCount(output), 1);
});


test('parseSuperuserCount rejects output without the marker', () => {
  assert.equal(parseSuperuserCount('1 active superuser'), null);
});
