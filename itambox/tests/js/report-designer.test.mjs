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
  }

  addEventListener(name, handler) {
    this.listeners.set(name, handler);
  }

  querySelector(selector) {
    return this.queryResults.get(selector) || null;
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
