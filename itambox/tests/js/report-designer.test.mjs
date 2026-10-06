import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createContext, runInContext } from 'node:vm';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { transformSync } from 'esbuild';

const itamboxRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const source = readFileSync(resolve(itamboxRoot, 'static/src/report-designer.ts'), 'utf8');
const compiled = transformSync(source, {
  loader: 'ts',
  format: 'iife',
  target: 'es2020',
}).code;

// Read one report type's selectable column list straight from the designer
// source; the lists mirror the server-side published column keys.
function columnListFor(reportType) {
  const match = new RegExp(`'${reportType}':\\s*\\[([\\s\\S]*?)\\]`).exec(source);
  assert.ok(match, `columnsByReportType must declare '${reportType}'`);
  return [...match[1].matchAll(/'([^']+)'/g)].map((entry) => entry[1]);
}

class FakeNode {
  constructor(tagName, id = '') {
    this.tagName = tagName.toUpperCase();
    this.id = id;
    this.children = [];
    this.parentNode = null;
    this.nextSibling = null;
    this.listeners = new Map();
    this.queryResults = new Map();
    this.type = '';
    this.className = '';
    this.innerHTML = '';
    this.srcdoc = '';
    this.attributes = new Map();
    this.classList = {
      add: () => {},
      remove: () => {},
    };
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }

  querySelector(selector) {
    return this.queryResults.get(selector) || null;
  }

  getAttribute(name) {
    return this.attributes.get(name) || null;
  }

  insertBefore(node, referenceNode) {
    const index = referenceNode ? this.children.indexOf(referenceNode) : -1;
    if (index < 0) this.children.push(node);
    else this.children.splice(index, 0, node);
    node.parentNode = this;
  }
}

class FakeDocument {
  constructor({ submitButton, reportEditor, reportModal }) {
    this.readyState = 'complete';
    this.submitButton = submitButton;
    this.reportEditor = reportEditor;
    this.reportModal = reportModal;
    this.listeners = new Map();
    this.globalQueries = [];
  }

  getElementById(id) {
    if (id === 'report-template-editor') return this.reportEditor;
    if (id === 'previewModal') return this.reportModal;
    return null;
  }

  querySelector(selector) {
    this.globalQueries.push(selector);
    if (selector === 'input[name="submit"]' || selector === 'button[type="submit"]' || selector === '.btn-primary') {
      return this.submitButton;
    }
    return null;
  }

  createElement(tagName) {
    return new FakeNode(tagName);
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }
}

function runDesigner({ page }) {
  const submitButton = new FakeNode('button', `${page}-submit`);
  submitButton.type = 'submit';
  const submitParent = new FakeNode('div', `${page}-actions`);
  submitParent.insertBefore(submitButton, null);

  const reportEditor = page === 'report' ? new FakeNode('div', 'report-template-editor') : null;
  if (reportEditor) {
    reportEditor.queryResults.set('input[name="submit"], button[type="submit"]', submitButton);
  }
  const reportModal = page === 'report' ? new FakeNode('div', 'previewModal') : null;
  const document = new FakeDocument({ submitButton, reportEditor, reportModal });
  const context = createContext({
    console,
    document,
    gettext: (message) => message,
  });

  runInContext(compiled, context);
  return { document, submitParent };
}

test('report preview is not injected into an unrelated login submit form', () => {
  const { submitParent, document } = runDesigner({ page: 'login' });

  assert.equal(submitParent.children.length, 1, 'login keeps only its original submit button');
  assert.equal(submitParent.children[0].id, 'login-submit');
  assert.deepEqual(document.globalQueries, [], 'login must not be queried as a report form');
});

test('report preview remains available inside the report template editor', () => {
  const { submitParent } = runDesigner({ page: 'report' });

  assert.equal(submitParent.children.length, 2, 'report editor receives one preview button');
  assert.equal(submitParent.children[1].id, 'btn-preview-report');
});

test('report preview escapes error text before rendering it in the iframe', async () => {
  const submitButton = new FakeNode('button', 'report-submit');
  const submitParent = new FakeNode('div', 'report-actions');
  submitParent.insertBefore(submitButton, null);
  const reportEditor = new FakeNode('div', 'report-template-editor');
  reportEditor.queryResults.set('input[name="submit"], button[type="submit"]', submitButton);
  const reportModal = new FakeNode('div', 'previewModal');
  reportModal.attributes.set('data-preview-url', '/extras/reports/templates/preview/');
  const spinner = new FakeNode('div', 'previewSpinner');
  const frame = new FakeNode('iframe', 'previewFrame');
  const form = {};
  const document = {
    readyState: 'complete',
    getElementById(id) {
      return new Map([
        ['report-template-editor', reportEditor],
        ['previewModal', reportModal],
        ['div_id_included_columns', null],
        ['btn-preview-report', null],
        ['previewSpinner', spinner],
        ['previewFrame', frame],
      ]).get(id) || null;
    },
    querySelector(selector) {
      if (selector === '#report-template-editor form') return form;
      if (selector === 'meta[name="csp-nonce"]') return null;
      return null;
    },
    createElement(tagName) {
      return new FakeNode(tagName);
    },
    addEventListener() {},
  };
  const errorPayload = '<img src=x onerror=alert(1)>';
  const context = createContext({
    console: { error() {} },
    document,
    gettext: (message) => message,
    FormData: class {
      constructor() {}
    },
    bootstrap: { Modal: { getOrCreateInstance: () => ({ show() {} }) } },
    fetch: async () => ({ ok: false, text: async () => errorPayload }),
  });

  runInContext(compiled, context);
  submitParent.children[1].listeners.get('click')({ preventDefault() {} });
  await new Promise((resolve) => setImmediate(resolve));

  assert.ok(frame.srcdoc.includes('&lt;img src=x onerror=alert(1)&gt;'));
  assert.ok(!frame.srcdoc.includes(errorPayload));
});

test('report designer offers the agreement entitlement without license seat keys', () => {
  const subscriptionColumns = columnListFor('subscription_renewals');

  assert.ok(
    subscriptionColumns.includes('agreement_entitled_quantity'),
    'subscription columns must expose the agreement entitlement',
  );
  for (const seatKey of ['seats', 'assigned_seats', 'available_seats']) {
    assert.ok(!subscriptionColumns.includes(seatKey), `${seatKey} belongs to the license utilization report`);
  }
});

test('report designer omits the removed warranty provider column', () => {
  const warrantyColumns = columnListFor('warranty_expiration');

  assert.ok(warrantyColumns.includes('warranty_supplier'), 'warranty columns must expose the supplier');
  assert.ok(!warrantyColumns.includes('warranty_provider'), 'the removed provider column must not stay selectable');
});
