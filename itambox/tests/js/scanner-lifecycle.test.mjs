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
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
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
];
const elements = new Map(ids.map((id) => [id, new FakeElement(id)]));
const body = new FakeElement('body');

globalThis.document = {
  body,
  addEventListener() {},
  getElementById(id) {
    return elements.get(id) || null;
  },
};
globalThis.window = { isSecureContext: true };
globalThis.gettext = (message) => message;
globalThis.interpolate = (message) => message;

const readers = [];
const stopResolvers = [];

class FakeHtml5Qrcode {
  constructor(readerId) {
    this.readerId = readerId;
    this.isScanning = false;
    this.cleared = false;
    readers.push(this);
  }

  start(_camera, _config, _onSuccess) {
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
