import assert from 'node:assert/strict';
import test from 'node:test';

import {
  compositionRefreshNeedsConfirmation,
  moveOrderedItem,
} from './.build/specification-editor.mjs';

test('keyboard section movement changes only the requested adjacent position', () => {
  assert.deepEqual(moveOrderedItem(['physical', 'compute', 'storage'], 1, -1), [
    'compute',
    'physical',
    'storage',
  ]);
  assert.deepEqual(moveOrderedItem(['physical', 'compute', 'storage'], 1, 1), [
    'physical',
    'storage',
    'compute',
  ]);
  assert.deepEqual(moveOrderedItem(['physical', 'compute'], 0, -1), ['physical', 'compute']);
});

test('composition refresh confirms only after draft input or section customization', () => {
  assert.equal(
    compositionRefreshNeedsConfirmation({ draftDirty: false, sectionsCustomized: false }),
    false,
  );
  assert.equal(
    compositionRefreshNeedsConfirmation({ draftDirty: true, sectionsCustomized: false }),
    true,
  );
  assert.equal(
    compositionRefreshNeedsConfirmation({ draftDirty: false, sectionsCustomized: true }),
    true,
  );
});
