import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import test from 'node:test';

const itamboxRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const buildDirectory = resolve(itamboxRoot, 'tests/js/.build');
const buildPath = resolve(buildDirectory, 'object-detail-refresh.mjs');

function buildObjectDetail() {
  mkdirSync(buildDirectory, { recursive: true });
  const esbuild = process.env.ITAMBOX_ESBUILD_BIN || resolve(
    itamboxRoot,
    'node_modules/.bin',
    process.platform === 'win32' ? 'esbuild.cmd' : 'esbuild',
  );
  execFileSync(
    esbuild,
    [
      'static/src/object-detail.ts',
      '--bundle',
      '--format=esm',
      '--platform=browser',
      `--outfile=${buildPath}`,
      '--log-level=warning',
    ],
    { cwd: itamboxRoot, stdio: 'ignore', shell: process.platform === 'win32' },
  );
}

class FakeEventTarget {
  constructor() {
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatchEvent(event) {
    for (const listener of this.listeners.get(event.type) || []) listener(event);
    return true;
  }
}

class FakeDocument {
  constructor(hasListContent) {
    this.readyState = 'loading';
    this.body = new FakeEventTarget();
    this.hasListContent = hasListContent;
    this.listeners = new Map();
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  getElementById(id) {
    return id === 'object-list-dynamic-content' && this.hasListContent ? {} : null;
  }

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }
}

async function loadObjectDetail({ hasListContent }) {
  const document = new FakeDocument(hasListContent);
  const ajaxCalls = [];
  let reloadCount = 0;
  const window = new FakeEventTarget();
  window.location = {
    href: 'http://127.0.0.1/assets/1/',
    reload() {
      reloadCount += 1;
    },
  };
  window.history = { replaceState() {} };
  globalThis.document = document;
  globalThis.window = window;
  globalThis.htmx = {
    ajax(...args) {
      ajaxCalls.push(args);
    },
  };
  delete globalThis.bootstrap;
  buildObjectDetail();
  await import(`${pathToFileURL(buildPath).href}?case=${hasListContent ? 'list' : 'detail'}-${Date.now()}-${Math.random()}`);
  return { document, ajaxCalls, reloadCount: () => reloadCount };
}

test('list refresh events do not trigger a duplicate detail reload', async () => {
  const { document, ajaxCalls, reloadCount } = await loadObjectDetail({ hasListContent: true });

  document.body.dispatchEvent({ type: 'tableRefreshRequired' });
  document.body.dispatchEvent({ type: 'licenseUpdated' });

  assert.equal(ajaxCalls.length, 0);
  assert.equal(reloadCount(), 0);
});

test('detail refresh events use the HTMX detail request when no list is mounted', async () => {
  const { document, ajaxCalls, reloadCount } = await loadObjectDetail({ hasListContent: false });

  document.body.dispatchEvent({ type: 'tableRefreshRequired' });

  assert.deepEqual(ajaxCalls, [['GET', 'http://127.0.0.1/assets/1/', { target: 'body', swap: 'outerHTML' }]]);
  assert.equal(reloadCount(), 0);
});
