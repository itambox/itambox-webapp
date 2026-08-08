import assert from 'node:assert/strict';
import test from 'node:test';

class FakeClassList {
  #values = new Set();

  add(...names) {
    names.forEach((name) => this.#values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.#values.delete(name));
  }

  contains(name) {
    return this.#values.has(name);
  }
}

class FakeElement {
  constructor(id) {
    this.id = id;
    this.classList = new FakeClassList();
    this.dataset = {};
    this.listeners = new Map();
    this.attributes = new Map();
    this.textContent = '';
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }

  setAttribute(name, value) {
    this.attributes.set(name, value);
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  querySelector() {
    return null;
  }
}

const ids = [
  'reader',
  'modal',
  'torch',
  'open',
  'close',
  'open-scanner-btn',
  'audit-scanner-reader',
  'audit-scanner-modal',
  'audit-toggle-torch-btn',
  'audit-close-scanner-btn',
  'audit-scanner-error',
  'barcode-scan-input',
  'global-scanner-reader',
  'global-scanner-modal',
  'global-toggle-torch-btn',
  'global-open-scanner-btn',
  'global-close-scanner-btn',
  'global-scanner-error',
  'global-scanner-feedback',
];
const elements = new Map(ids.map((id) => [id, new FakeElement(id)]));
const body = new FakeElement('body');

globalThis.document = {
  body,
  listeners: new Map(),
  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  },
  dispatchEvent() {},
  getElementById(id) {
    return elements.get(id) || null;
  },
};
globalThis.window = { isSecureContext: true, location: {}, setTimeout, clearTimeout };
globalThis.gettext = (message) => message;
globalThis.interpolate = (message, context) => message.replace('%(code)s', context.code);
globalThis.fetch = () => Promise.resolve({ ok: false });

const readers = [];
const stopResolvers = [];
const startResolvers = [];
let deferNextStart = false;

class FakeHtml5Qrcode {
  constructor(readerId) {
    this.readerId = readerId;
    this.isScanning = false;
    this.cleared = false;
    readers.push(this);
  }

  start(_camera, _config, onSuccess) {
    this.onSuccess = onSuccess;
    if (deferNextStart) {
      deferNextStart = false;
      return new Promise((resolve) => {
        startResolvers.push(() => {
          this.isScanning = true;
          resolve();
        });
      });
    }
    this.isScanning = true;
    return Promise.resolve();
  }

  getRunningTrackCapabilities() {
    return {};
  }

  stop() {
    return new Promise((resolve) => {
      stopResolvers.push(() => {
        this.isScanning = false;
        resolve();
      });
    });
  }

  clear() {
    this.cleared = true;
  }
}

globalThis.Html5Qrcode = FakeHtml5Qrcode;
const { AssetScanner } = await import('./.build/scanner.mjs');

function config(onResult = () => undefined) {
  return {
    readerId: 'reader',
    modalId: 'modal',
    torchId: 'torch',
    openBtnId: 'open',
    closeBtnId: 'close',
    onResult,
  };
}

test('a stale reader stop callback cannot clear a replacement scanner', async () => {
  readers.length = 0;
  stopResolvers.length = 0;
  const scanner = new AssetScanner(config());

  await scanner.start();
  scanner.stop();
  await scanner.start();
  assert.equal(readers.length, 2);
  assert.equal(stopResolvers.length, 1);

  // Resolve reader A after reader B has replaced the instance field.
  stopResolvers[0]();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(readers[0].cleared, true, 'the stopped reader is cleaned up');
  assert.equal(readers[1].cleared, false, 'the replacement reader remains active');
});

test('a stale pending start cannot mutate the replacement scanner generation', async () => {
  readers.length = 0;
  stopResolvers.length = 0;
  startResolvers.length = 0;
  deferNextStart = true;
  const results = [];
  const scanner = new AssetScanner(config((code) => results.push(code)));

  const staleStart = scanner.start();
  scanner.stop();
  await scanner.start();
  assert.equal(readers.length, 2);
  assert.equal(startResolvers.length, 1);

  startResolvers[0]();
  await staleStart;
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(stopResolvers.length, 1, 'the stale reader is cleaned up');
  stopResolvers[0]();
  await new Promise((resolve) => setImmediate(resolve));

  readers[0].onSuccess('STALE');
  readers[1].onSuccess('LIVE');
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(results, ['LIVE']);
});

test('audit scanner entrypoint opens the rendered audit overlay', async () => {
  document.listeners.get('DOMContentLoaded')();
  const openHandler = elements.get('open-scanner-btn').listeners.get('click');
  assert.equal(typeof openHandler, 'function');
  openHandler();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(elements.get('audit-scanner-modal').classList.contains('is-open'), true);
  assert.equal(typeof readers.at(-1).onSuccess, 'function');
});

test('global scanner errors stay in the open overlay live region', async () => {
  document.listeners.get('DOMContentLoaded')();
  const openHandler = elements.get('global-open-scanner-btn').listeners.get('click');
  assert.equal(typeof openHandler, 'function');
  openHandler();
  await new Promise((resolve) => setImmediate(resolve));

  const reader = readers.at(-1);
  assert.equal(typeof reader.onSuccess, 'function');
  reader.onSuccess('TAG-404');
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));

  const feedback = elements.get('global-scanner-feedback');
  assert.equal(feedback.textContent, 'No asset matches: TAG-404');
  assert.equal(feedback.classList.contains('is-visible'), true);
  assert.equal(feedback.getAttribute('role'), 'alert');
  assert.equal(feedback.getAttribute('aria-live'), 'assertive');
});
