/**
 * js-catalog-inventory.mjs — diagnostic report: TypeScript source catalogue vs.
 * the German djangojs.po. Prints a stable, sorted inventory with counts.
 *
 * Usage (from itambox/):
 *   node tests/js/lib/js-catalog-inventory.mjs
 */
import { inventory } from './i18n-catalog-lib.mjs';

const report = inventory({
  sourceDir: new URL('../../../static/src', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'),
  poPath: new URL('../../../locale/de/LC_MESSAGES/djangojs.po', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1'),
});

const byClass = {};
for (const result of report.results) {
  (byClass[result.class] ??= []).push(result);
}

console.log(`Source files inspected : ${report.files.length}`);
console.log(`Static gettext calls  : ${report.messages.length}`);
console.log(`Dynamic calls         : ${report.dynamic.length}`);
console.log(`Catalogue entries     : ${report.entries.size}`);
console.log(`PO parse errors       : ${report.errors.length}`);
console.log('');
console.log('Classification counts:');
for (const klass of ['translated', 'missing', 'empty', 'fuzzy', 'plural-incomplete', 'placeholder-mismatch']) {
  console.log(`  ${klass.padEnd(22)} ${report.counts[klass]}`);
}
console.log('');

for (const [klass, items] of Object.entries(byClass)) {
  if (klass === 'translated') continue;
  console.log(`=== ${klass.toUpperCase()} (${items.length}) ===`);
  for (const item of items.sort((a, b) => a.path.localeCompare(b.path) || a.line - b.line)) {
    console.log(`${item.path}:${item.line} ${item.callee}(${JSON.stringify(item.msgid)})`);
    for (const problem of item.problems) console.log(`    - [${problem.code}] ${problem.detail}`);
  }
  console.log('');
}

if (report.dynamic.length > 0) {
  console.log('=== DYNAMIC CALLS ===');
  for (const call of report.dynamic) {
    console.log(`${call.path}:${call.line} ${call.callee} — ${call.reason}`);
  }
}

if (report.errors.length > 0) {
  console.log('=== PO PARSE ERRORS ===');
  for (const error of report.errors) console.log(error);
}
