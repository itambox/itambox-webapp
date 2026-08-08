/**
 * Unit tests for the shared camera-scan gate (issue #262).
 *
 * The camera fires ~15 frames/s, so a single barcode held in view produces a
 * burst of identical detections. These tests drive that burst deterministically
 * with node:test fake timers (`mock.timers` over the `Date` API, which is what
 * ScanGate reads) rather than real sleeps.
 *
 * The module under test is DOM-free on purpose: it is the seam where the
 * throttle decision is made, so the decision can be tested at its real boundary
 * without a browser.
 */
import assert from 'node:assert/strict';
import test, { mock } from 'node:test';

import {
  DEFAULT_DUPLICATE_WINDOW_MS,
  ScanGate,
  ThrottledScanDispatcher,
} from './.build/scan-gate.mjs';

/**
 * Run `fn` with Date frozen at 0 and advanceable via `tick`.
 *
 * Awaits `fn` before restoring the clock — several cases settle promises inside
 * the fake-clock window, and tearing the clock down early would let them run
 * against the real one.
 */
async function withFakeClock(fn) {
  mock.timers.enable({ apis: ['Date'], now: 0 });
  try {
    return await fn((ms) => mock.timers.tick(ms));
  } finally {
    mock.timers.reset();
  }
}

/** A handler that records calls and hands back manually-settled promises. */
function deferredHandler() {
  const calls = [];
  const settlers = [];
  const handler = (code) => {
    calls.push(code);
    return new Promise((resolve, reject) => {
      settlers.push({ resolve, reject });
    });
  };
  return { calls, settlers, handler };
}


test('a barcode held in view for many frames triggers one scan action', async () => {
  await withFakeClock((tick) => {
    const gate = new ScanGate();
    const accepted = [];

    // ~15 fps for one second on the same payload.
    for (let frame = 0; frame < 15; frame += 1) {
      if (gate.accept('ASSET-001')) accepted.push('ASSET-001');
      gate.settle();
      tick(66);
    }

    assert.deepEqual(accepted, ['ASSET-001']);
  });
});


test('the same barcode fires again once the duplicate window has elapsed', async () => {
  await withFakeClock((tick) => {
    const gate = new ScanGate();

    assert.equal(gate.accept('ASSET-001'), true);
    gate.settle();

    tick(DEFAULT_DUPLICATE_WINDOW_MS - 1);
    assert.equal(gate.accept('ASSET-001'), false, 'still inside the duplicate window');

    tick(1);
    assert.equal(gate.accept('ASSET-001'), true, 'window elapsed — a deliberate re-scan');
  });
});


test('a different payload is not suppressed by the previous payload cooldown', async () => {
  await withFakeClock((tick) => {
    const gate = new ScanGate();

    assert.equal(gate.accept('ASSET-001'), true);
    gate.settle();

    tick(20); // well inside ASSET-001's duplicate window
    assert.equal(gate.accept('ASSET-002'), true, 'a distinct code must go through');
    gate.settle();

    tick(20);
    assert.equal(gate.accept('ASSET-003'), true);
  });
});


test('an unsettled scan blocks every further detection', async () => {
  await withFakeClock((tick) => {
    const gate = new ScanGate();

    assert.equal(gate.accept('ASSET-001'), true);
    assert.equal(gate.isBusy, true);

    tick(5000); // far beyond the duplicate window — in-flight still wins
    assert.equal(gate.accept('ASSET-001'), false, 'duplicate of the in-flight code');
    assert.equal(gate.accept('ASSET-002'), false, 'distinct code, action still in flight');

    gate.settle();
    assert.equal(gate.isBusy, false);
    assert.equal(gate.accept('ASSET-002'), true);
  });
});


test('the duplicate window is measured from when the action settled', async () => {
  await withFakeClock((tick) => {
    const gate = new ScanGate();

    assert.equal(gate.accept('ASSET-001'), true);
    tick(5000); // a slow round-trip, longer than the duplicate window
    gate.settle();

    assert.equal(gate.accept('ASSET-001'), false, 'window restarts at settle, not at accept');
    tick(DEFAULT_DUPLICATE_WINDOW_MS);
    assert.equal(gate.accept('ASSET-001'), true);
  });
});


test('reset clears both the in-flight flag and the duplicate memory', async () => {
  await withFakeClock(() => {
    const gate = new ScanGate();

    assert.equal(gate.accept('ASSET-001'), true);
    gate.reset();

    assert.equal(gate.isBusy, false);
    assert.equal(gate.accept('ASSET-001'), true, 'a re-opened scanner starts clean');
  });
});


test('the duplicate window is configurable', async () => {
  await withFakeClock((tick) => {
    const gate = new ScanGate({ duplicateWindowMs: 400 });

    assert.equal(gate.accept('ASSET-001'), true);
    gate.settle();
    tick(399);
    assert.equal(gate.accept('ASSET-001'), false);
    tick(1);
    assert.equal(gate.accept('ASSET-001'), true);
  });
});


test('dispatcher runs a duplicate burst exactly once while the action is pending', async () => {
  const { calls, settlers, handler } = deferredHandler();

  await withFakeClock(async (tick) => {
    const dispatcher = new ThrottledScanDispatcher(handler);

    for (let frame = 0; frame < 10; frame += 1) {
      dispatcher.dispatch('ASSET-001');
      tick(66);
    }

    assert.deepEqual(calls, ['ASSET-001'], 'one domain action per accepted scan');

    settlers[0].resolve({ found: true });
    await Promise.resolve();
    await Promise.resolve();

    // Still inside the duplicate window measured from the settle above.
    dispatcher.dispatch('ASSET-001');
    assert.deepEqual(calls, ['ASSET-001']);
  });
});


test('dispatcher re-arms after a rejected action so the scan can be retried', async () => {
  const { calls, settlers, handler } = deferredHandler();

  await withFakeClock(async (tick) => {
    const dispatcher = new ThrottledScanDispatcher(handler);

    dispatcher.dispatch('MISSING-001');
    assert.deepEqual(calls, ['MISSING-001']);

    settlers[0].reject(new Error('not_found'));
    await Promise.resolve();
    await Promise.resolve();

    dispatcher.dispatch('MISSING-001');
    assert.deepEqual(calls, ['MISSING-001'], 'failure path still honours the cooldown');

    tick(DEFAULT_DUPLICATE_WINDOW_MS);
    dispatcher.dispatch('MISSING-001');
    assert.deepEqual(calls, ['MISSING-001', 'MISSING-001'], 'retry without a page reload');
  });
});


test('dispatcher interleaves duplicate and distinct payloads deterministically', async () => {
  const { calls, settlers, handler } = deferredHandler();
  let settled = 0;

  const settleNext = async () => {
    settlers[settled].resolve(null);
    settled += 1;
    await Promise.resolve();
    await Promise.resolve();
  };

  await withFakeClock(async (tick) => {
    const dispatcher = new ThrottledScanDispatcher(handler);

    dispatcher.dispatch('A');
    await settleNext();
    tick(10);

    dispatcher.dispatch('A'); // duplicate — suppressed
    dispatcher.dispatch('B'); // distinct — accepted
    await settleNext();
    tick(10);

    dispatcher.dispatch('B'); // duplicate — suppressed
    dispatcher.dispatch('A'); // distinct from the last accepted code — accepted
    await settleNext();

    assert.deepEqual(calls, ['A', 'B', 'A']);
  });
});


test('a settlement from a closed scanner generation cannot release the next one', async () => {
  const { calls, settlers, handler } = deferredHandler();

  await withFakeClock(async () => {
    const dispatcher = new ThrottledScanDispatcher(handler);

    // Generation 0: a scan is still in flight when the user closes the overlay.
    dispatcher.dispatch('ASSET-001');
    assert.deepEqual(calls, ['ASSET-001']);

    dispatcher.reset(); // scanner closed and re-opened

    // Generation 1: the same payload is legitimately re-read straight away.
    dispatcher.dispatch('ASSET-001');
    assert.deepEqual(calls, ['ASSET-001', 'ASSET-001']);
    assert.equal(dispatcher.isBusy, true);

    // The abandoned generation-0 round-trip now lands. It must not touch the
    // gate that generation 1 is holding.
    settlers[0].resolve({ found: true });
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(dispatcher.isBusy, true, 'stale settlement released a live gate');
    dispatcher.dispatch('ASSET-001');
    assert.deepEqual(calls, ['ASSET-001', 'ASSET-001'], 'in-flight scan started twice');
  });
});


test('a stale rejection cannot re-arm the gate of the next scanner generation', async () => {
  const { calls, settlers, handler } = deferredHandler();

  await withFakeClock(async () => {
    const dispatcher = new ThrottledScanDispatcher(handler);

    dispatcher.dispatch('MISSING-001');
    dispatcher.reset();
    dispatcher.dispatch('MISSING-001');
    assert.deepEqual(calls, ['MISSING-001', 'MISSING-001']);

    settlers[0].reject(new Error('not_found'));
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(dispatcher.isBusy, true);
    dispatcher.dispatch('OTHER-002');
    assert.deepEqual(calls, ['MISSING-001', 'MISSING-001'], 'distinct code bypassed an in-flight scan');
  });
});


test('dispatcher releases the gate when the handler throws synchronously', async () => {
  await withFakeClock((tick) => {
    const calls = [];
    const dispatcher = new ThrottledScanDispatcher((code) => {
      calls.push(code);
      throw new Error('boom');
    });

    assert.throws(() => dispatcher.dispatch('ASSET-001'), /boom/);
    tick(DEFAULT_DUPLICATE_WINDOW_MS);

    assert.throws(() => dispatcher.dispatch('ASSET-001'), /boom/);
    assert.deepEqual(calls, ['ASSET-001', 'ASSET-001'], 'a throwing handler must not wedge the gate');
  });
});


test('dispatcher settles a synchronous handler immediately', async () => {
  await withFakeClock((tick) => {
    const calls = [];
    const dispatcher = new ThrottledScanDispatcher((code) => calls.push(code));

    dispatcher.dispatch('ASSET-001');
    tick(DEFAULT_DUPLICATE_WINDOW_MS);
    dispatcher.dispatch('ASSET-001');

    assert.deepEqual(calls, ['ASSET-001', 'ASSET-001']);
  });
});


test('dispatcher ignores blank detections without consuming the gate', async () => {
  await withFakeClock(() => {
    const calls = [];
    const dispatcher = new ThrottledScanDispatcher((code) => calls.push(code));

    dispatcher.dispatch('');
    dispatcher.dispatch('   ');
    dispatcher.dispatch('ASSET-001');

    assert.deepEqual(calls, ['ASSET-001']);
  });
});
