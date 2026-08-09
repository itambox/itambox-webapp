import assert from 'node:assert/strict';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import test from 'node:test';

const itamboxRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const fakeTimers = new Map();
let nextTimerId = 1;

class FakeClassList {
  constructor() {
    this.values = new Set();
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  toggle(name, force) {
    const shouldAdd = force === undefined ? !this.values.has(name) : force;
    if (shouldAdd) this.values.add(name);
    else this.values.delete(name);
    return shouldAdd;
  }

  contains(name) {
    return this.values.has(name);
  }
}

class FakeElement {
  constructor(id) {
    this.id = id;
    this.classList = new FakeClassList();
    this.dataset = {};
    this.listeners = new Map();
    this.children = [];
    this.attributes = new Map();
    this.textContent = '';
    this.value = '';
    this.disabled = false;
    this.parentNode = null;
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

  appendChild(child) {
    this.children.push(child);
    child.parentNode = this;
    return child;
  }

  removeChild(child) {
    this.children = this.children.filter((candidate) => candidate !== child);
  }

  remove() {
    this.parentNode?.removeChild(this);
  }

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }

  focus() {}
}

class FakeDocument {
  constructor(ids) {
    this.elements = new Map(ids.map((id) => [id, new FakeElement(id)]));
    this.listeners = new Map();
    this.body = new FakeElement('body');
    this.elements.set('body', this.body);
  }

  getElementById(id) {
    return this.elements.get(id) || null;
  }

  createElement(tagName) {
    return new FakeElement(tagName);
  }

  querySelectorAll() {
    return [];
  }

  addEventListener(name, handler) {
    const handlers = this.listeners.get(name) || [];
    handlers.push(handler);
    this.listeners.set(name, handlers);
  }

  dispatchEvent(event) {
    for (const handler of this.listeners.get(event.type) || []) handler(event);
    return true;
  }
}

function makeDom(kind) {
  const rootId = kind === 'audit' ? 'audit-basket-root' : 'scan-basket-root';
  const inputId = kind === 'audit' ? 'audit-basket-input' : 'scan-basket-input';
  const formId = kind === 'audit' ? 'audit-basket-form' : 'scan-basket-form';
  const rowsId = kind === 'audit' ? 'audit-basket-rows' : 'scan-basket-rows';
  const templateId = kind === 'audit' ? 'audit-basket-row-template' : 'scan-basket-row-template';
  const countId = kind === 'audit' ? 'audit-basket-count' : 'scan-basket-count';
  const openId = kind === 'audit' ? 'audit-open-scanner-btn' : 'basket-open-scanner-btn';
  const modalId = kind === 'audit' ? 'audit-scanner-modal' : 'basket-scanner-modal';
  const readerId = kind === 'audit' ? 'audit-scanner-reader' : 'basket-scanner-reader';
  const feedbackId = kind === 'audit' ? 'audit-scan-feedback' : 'basket-scan-feedback';
  const closeId = kind === 'audit' ? 'audit-close-scanner-btn' : 'basket-close-scanner-btn';
  const torchId = kind === 'audit' ? 'audit-toggle-torch-btn' : 'basket-toggle-torch-btn';
  const errorId = kind === 'audit' ? 'audit-scanner-error' : 'basket-scanner-error';
  const ids = [
    rootId,
    inputId,
    formId,
    rowsId,
    templateId,
    countId,
    'scan-basket-empty',
    'scan-basket-clear',
    'scan-basket-submit',
    'audit-basket-empty',
    'audit-basket-clear',
    'audit-basket-submit',
    openId,
    modalId,
    readerId,
    feedbackId,
    closeId,
    torchId,
    errorId,
    'django-messages',
  ];
  const document = new FakeDocument(ids);
  const root = document.getElementById(rootId);
  root.dataset.validateUrl = '/validate';
  root.dataset.resolveUrl = '/resolve';
  root.dataset.mode = 'checkin';
  document.getElementById(templateId).content = { cloneNode: () => ({ querySelector: () => null }) };
  document.getElementById(formId).submit = () => {};
  return { document, ids: { inputId, modalId, openId, readerId, feedbackId } };
}

function installGlobals(dom, readers, fetchImpl) {
  const schedule = (callback) => {
    const id = nextTimerId++;
    fakeTimers.set(id, callback);
    return id;
  };
  globalThis.document = dom.document;
  globalThis.window = {
    isSecureContext: true,
    setTimeout: schedule,
    clearTimeout: (id) => fakeTimers.delete(id),
  };
  globalThis.getComputedStyle = (element) => ({
    display: element.classList.contains('is-open') ? 'block' : 'none',
  });
  globalThis.setTimeout = schedule;
  globalThis.clearTimeout = (id) => fakeTimers.delete(id);
  globalThis.gettext = (message) => message;
  globalThis.interpolate = (message, context) => message.replace(/%\(([^)]+)\)s/g, (_, key) => context[key]);
  globalThis.fetch = fetchImpl;
  globalThis.Html5Qrcode = class {
    constructor(readerId) {
      this.readerId = readerId;
      this.isScanning = false;
      this.cleared = false;
      readers.push(this);
    }

    start(_camera, _config, onSuccess) {
      this.onSuccess = onSuccess;
      this.isScanning = true;
      return Promise.resolve();
    }

    stop() {
      this.isScanning = false;
      return Promise.resolve();
    }

    clear() {
      this.cleared = true;
    }

    getRunningTrackCapabilities() {
      return {};
    }
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise((resolvePromise) => setImmediate(resolvePromise));
}

test('real bulk and audit entrypoints keep manual input ungated and camera feedback in the overlay', async () => {
  const originalTimers = { setTimeout: globalThis.setTimeout, clearTimeout: globalThis.clearTimeout };
  const bundleNames = { audit: 'audit-basket.mjs', bulk: 'scan-basket.mjs' };

  try {
    for (const kind of ['audit', 'bulk']) {
      const dom = makeDom(kind);
      const readers = [];
      const pending = [];
      installGlobals(dom, readers, (url) => new Promise((resolvePromise) => pending.push({ url, resolvePromise })));
      const bundleUrl = pathToFileURL(resolve(itamboxRoot, `tests/js/.build/${bundleNames[kind]}`)).href;
      await import(`${bundleUrl}?runtime=${kind}-${Date.now()}`);
      dom.document.dispatchEvent({ type: 'DOMContentLoaded' });

      const input = dom.document.getElementById(dom.ids.inputId);
      const openButton = dom.document.getElementById(dom.ids.openId);
      const feedback = dom.document.getElementById(dom.ids.feedbackId);
      const messages = dom.document.getElementById('django-messages');

      assert.equal(typeof input.listeners.get('keydown'), 'function', `${kind} manual handler is wired`);
      assert.equal(typeof openButton.listeners.get('click'), 'function', `${kind} camera handler is wired`);

      input.value = 'MANUAL-ONE';
      input.listeners.get('keydown')({ key: 'Enter', preventDefault() {} });
      input.value = 'USB-TWO';
      input.listeners.get('keydown')({ key: 'Enter', preventDefault() {} });
      assert.equal(pending.length, 2, `${kind} manual/USB entries bypass the camera gate`);

      pending.splice(0).forEach(({ resolvePromise }) => resolvePromise({ ok: false, status: 404 }));
      await flush();
      assert.equal(messages.children.length, 2, `${kind} closed-overlay errors use visible fallback toasts`);

      messages.children = [];
      openButton.listeners.get('click')();
      await flush();
      const reader = readers.at(-1);
      assert.equal(typeof reader.onSuccess, 'function', `${kind} camera reader is wired`);
      reader.onSuccess('CAMERA-404');
      assert.equal(pending.length, 1, `${kind} camera detection starts one domain action`);
      pending.splice(0).forEach(({ resolvePromise }) => resolvePromise({ ok: false, status: 404 }));
      await flush();

      assert.match(feedback.textContent, /No asset matches: CAMERA-404/);
      assert.equal(messages.children.length, 0, `${kind} open-overlay feedback is not hidden below the overlay`);
    }
  } finally {
    globalThis.setTimeout = originalTimers.setTimeout;
    globalThis.clearTimeout = originalTimers.clearTimeout;
  }
});
