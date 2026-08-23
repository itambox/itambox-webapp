/**
 * i18n-catalog.test.mjs — deterministic JavaScript catalogue regression gate.
 *
 * Validates that every statically extractable gettext()/ngettext() call under
 * itambox/static/src has a non-empty, non-fuzzy German entry in the djangojs
 * catalogue, with matching named placeholders and complete plural forms.
 *
 * Run via `npm run test:unit` (explicitly listed in package.json).
 */
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { resolve } from 'node:path';
import test from 'node:test';

import {
  CLASSES,
  classifyMessages,
  extractSourceMessages,
  inventory,
  namedPlaceholders,
  parsePo,
  unescapePo,
} from './lib/i18n-catalog-lib.mjs';

const itamboxRoot = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const sourceDir = resolve(itamboxRoot, 'static/src');
const poPath = resolve(itamboxRoot, 'locale/de/LC_MESSAGES/djangojs.po');

// ---------------------------------------------------------------------------
// PO reader unit coverage (catalogue features actually supported by the gate)
// ---------------------------------------------------------------------------

test('PO reader: multiline msgid, escapes, fuzzy flags, and indexed plurals', () => {
  const { entries, errors } = parsePo(`
#, fuzzy
msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

msgid "Line one\\n"
"Line two"
msgstr "Zeile eins\\n"
"Zeile zwei"

#, fuzzy
msgid "fuzzy message"
msgstr "unscharfe Nachricht"

msgid "One asset"
msgid_plural "%(count)s assets"
msgstr[0] "Ein Asset"
msgstr[1] "%(count)s Assets"
`);
  assert.equal(errors.length, 0, JSON.stringify(errors));
  const header = entries.get('');
  assert.ok(header, 'header entry present');
  assert.ok(header.flags.has('fuzzy'), 'header keeps its fuzzy flag');
  const multiline = entries.get('Line one\nLine two');
  assert.ok(multiline, 'multiline msgid joined with a real newline');
  assert.equal(multiline.msgstr[0], 'Zeile eins\nZeile zwei');
  const fuzzy = entries.get('fuzzy message');
  assert.ok(fuzzy.flags.has('fuzzy'), 'fuzzy flag attaches to the following msgid');
  const plural = entries.get('One asset');
  assert.equal(plural.msgidPlural, '%(count)s assets');
  assert.equal(plural.msgstr[0], 'Ein Asset');
  assert.equal(plural.msgstr[1], '%(count)s Assets');
});

test('PO reader: duplicate identity is reported as an error', () => {
  const { errors } = parsePo(`
msgid "dup"
msgstr "eins"

msgid "dup"
msgstr "zwei"
`);
  assert.equal(errors.length, 1);
  assert.match(errors[0], /duplicate msgid/);
});

test('PO reader: msgid_plural without a preceding msgid is an error', () => {
  const { errors } = parsePo(`
msgid_plural "orphan"
msgstr[0] "x"
`);
  assert.ok(errors.some((error) => /msgid_plural without/.test(error)), JSON.stringify(errors));
});

test('PO reader: unrecognized content is an error', () => {
  const { errors } = parsePo(`
msgid "ok"
msgstr "fine"
garbage line
`);
  assert.ok(errors.some((error) => /unrecognized line/.test(error)), JSON.stringify(errors));
});

test('PO reader: msgctxt keys the entry under its contextual identity', () => {
  const { entries, errors } = parsePo(`
msgctxt "menu"
msgid "Save"
msgstr "Speichern"

msgid "Save"
msgstr "Sichern"
`);
  assert.equal(errors.length, 0, JSON.stringify(errors));
  // The contextual entry must not shadow the plain gettext identity, and vice
  // versa: a plain gettext("Save") source matches only the context-free entry.
  assert.equal(entries.get('menu\u0004Save').msgstr[0], 'Speichern');
  assert.equal(entries.get('Save').msgstr[0], 'Sichern');
});

test('PO reader: indexed msgstr without msgid_plural is an error', () => {
  const { errors } = parsePo(`
msgid "singular"
msgstr[0] "Einzahl"
`);
  assert.ok(errors.some((error) => /indexed msgstr without msgid_plural/.test(error)), JSON.stringify(errors));
});

test('PO unescape: standard gettext escapes', () => {
  assert.equal(unescapePo('a\\"b'), 'a"b');
  assert.equal(unescapePo('a\\\\b'), 'a\\b');
  assert.equal(unescapePo('a\\nb'), 'a\nb');
  assert.equal(unescapePo('a\\tb'), 'a\tb');
});

// ---------------------------------------------------------------------------
// Named-placeholder extraction
// ---------------------------------------------------------------------------

test('namedPlaceholders: extracts %(name)s forms and masks literal %%', () => {
  assert.deepEqual([...namedPlaceholders('%(count)s of %(total)d')].sort(), ['count', 'total']);
  assert.deepEqual([...namedPlaceholders('100%% complete: %(count)s')], ['count']);
  assert.equal(namedPlaceholders('no placeholders here').size, 0);
});

// ---------------------------------------------------------------------------
// TypeScript extraction (fixture level)
// ---------------------------------------------------------------------------

test('extraction: only static literals are accepted; property calls are ignored; dynamic args are reported', () => {
  const { messages, dynamic } = extractSourceMessages([
    {
      path: 'fixture.ts',
      source: `
const a = gettext('Static single');
const b = gettext(\`Static template\`);
const c = gettext(\`Dynamic \${value}\`);
const d = someObject.gettext('Property access');
const e = ngettext('One asset', '%(count)s assets', count);
const f = gettext(dynamicVariable);
`,
    },
  ]);
  assert.equal(messages.length, 3);
  const ids = messages.map((message) => message.msgid).sort();
  assert.deepEqual(ids, ['One asset', 'Static single', 'Static template']);
  const pluralMessage = messages.find((message) => message.callee === 'ngettext');
  assert.equal(pluralMessage.plural, '%(count)s assets');
  assert.equal(pluralMessage.line, 6);
  assert.equal(dynamic.length, 2);
  for (const call of dynamic) assert.equal(call.reason, 'non-literal argument');
  assert.ok(dynamic.every((call) => call.path === 'fixture.ts'));
});

test('extraction: plain JavaScript sources are scanned too', () => {
  const { messages, dynamic } = extractSourceMessages([
    {
      path: 'fixture.js',
      source: `
const a = gettext('From plain JS');
`,
    },
  ]);
  assert.equal(messages.length, 1);
  assert.equal(messages[0].msgid, 'From plain JS');
  assert.equal(dynamic.length, 0);
});

test('extraction: unsupported catalogue calls fail closed as dynamic', () => {
  const { messages, dynamic } = extractSourceMessages([
    {
      path: 'fixture.ts',
      source: `
const a = pgettext('menu', 'Save');
const b = gettext_noop('Save');
`,
    },
  ]);
  assert.equal(messages.length, 0);
  assert.equal(dynamic.length, 2);
  for (const call of dynamic) {
    assert.equal(call.reason, 'catalogue call not supported by this gate yet');
  }
});

// ---------------------------------------------------------------------------
// Classification (fixture level — proves each failure mode is detected)
// ---------------------------------------------------------------------------

test('classification: detects missing, empty, fuzzy, placeholder mismatch, and plural gaps', () => {
  const { entries } = parsePo(`
msgid "has translation"
msgstr "hat Übersetzung"

msgid "empty one"
msgstr ""

#, fuzzy
msgid "fuzzy one"
msgstr "unscharf"

msgid "%(count)s things"
msgstr "Dinge"

msgid "singular source with plural entry"
msgid_plural "plural sources"
msgstr[0] "eins"
msgstr[1] "zwei"

msgid "plural source"
msgid_plural "plural sources"
msgstr[0] "eine Quelle"
msgstr[1] ""

msgid "plural placeholder source"
msgid_plural "plural placeholder sources"
msgstr[0] "%(count)s Quelle"
msgstr[1] "Quellen"
`);

  const { results } = classifyMessages(
    [
      { msgid: 'has translation', plural: null, path: 'x.ts', line: 1, callee: 'gettext' },
      { msgid: 'empty one', plural: null, path: 'x.ts', line: 2, callee: 'gettext' },
      { msgid: 'fuzzy one', plural: null, path: 'x.ts', line: 3, callee: 'gettext' },
      { msgid: '%(count)s things', plural: null, path: 'x.ts', line: 4, callee: 'gettext' },
      { msgid: 'singular source with plural entry', plural: null, path: 'x.ts', line: 5, callee: 'gettext' },
      { msgid: 'plural source', plural: 'plural sources', path: 'x.ts', line: 6, callee: 'ngettext' },
      { msgid: 'plural placeholder source', plural: 'plural placeholder sources', path: 'x.ts', line: 7, callee: 'ngettext' },
      { msgid: 'not in catalog', plural: null, path: 'x.ts', line: 8, callee: 'gettext' },
    ],
    entries,
  );

  const byClass = Object.fromEntries(CLASSES.map((name) => [name, results.filter((r) => r.class === name)]));
  assert.equal(byClass.translated.length, 1);
  assert.equal(byClass.missing.length, 1);
  assert.equal(byClass.empty.length, 1);
  assert.equal(byClass.fuzzy.length, 1);
  // Both the singular translation ("Dinge") and the second plural form
  // ("Quellen") drop the %(count)s placeholder.
  assert.equal(byClass['placeholder-mismatch'].length, 2);
  // Plural-entry-for-singular-source, empty msgstr[1], ... -> incomplete.
  assert.equal(byClass['plural-incomplete'].length, 2);
});

// ---------------------------------------------------------------------------
// The gate: real source tree vs. the real German catalogue
// ---------------------------------------------------------------------------

test('catalogue parity: every static gettext()/ngettext() source message has a valid German entry', () => {
  const report = inventory({ sourceDir, poPath });

  const failures = [];

  if (report.errors.length > 0) {
    failures.push(`catalogue parse errors:\n${report.errors.map((error) => `  - ${error}`).join('\n')}`);
  }
  if (report.dynamic.length > 0) {
    failures.push(
      `dynamic/non-literal translation calls (convert to a static form or add a narrow exception):\n${report.dynamic
        .map((call) => `  - ${call.path}:${call.line} ${call.callee} — ${call.reason}`)
        .join('\n')}`,
    );
  }
  for (const result of report.results) {
    if (result.class !== 'translated') {
      failures.push(
        `${result.path}:${result.line} ${result.callee}(${JSON.stringify(result.msgid)}) -> ${result.class}\n${result.problems
          .map((problem) => `  - ${problem.detail}`)
          .join('\n')}`,
      );
    }
  }

  assert.equal(
    failures.length,
    0,
    [
      `JavaScript catalogue gate failed (${failures.length} problem(s) among ${report.messages.length} source messages):`,
      ...failures,
    ].join('\n\n'),
  );

  assert.equal(report.messages.length > 0, true, 'expected at least one static source message');
});
