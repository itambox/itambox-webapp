/**
 * i18n-catalog-lib.mjs — deterministic machinery for the JavaScript catalogue gate.
 *
 * Shared by:
 *   - tests/js/i18n-catalog.test.mjs  (regression gate)
 *   - scripts/js-catalog-inventory.mjs (diagnostic inventory report)
 *
 * Responsibilities:
 *   1. PO parsing     — a small deterministic reader for the catalogue features the
 *                       repository's djangojs.po actually uses: multiline msgid,
 *                       msgid_plural, indexed msgstr[n], fuzzy flags, escaped strings,
 *                       msgctxt (optional), duplicate identities reported as errors.
 *   2. TS extraction  — walks itambox/static/src (all .ts files) with the TypeScript
 *                       compiler API and accepts only statically extractable string
 *                       literals or no-substitution template literals passed to the
 *                       global gettext()/ngettext() calls. Dynamic/non-literal calls
 *                       are reported separately and never guessed.
 *   3. Classification — compares source messages against the German catalogue and
 *                       classifies each as translated / missing / empty / fuzzy /
 *                       plural-incomplete / placeholder-mismatch.
 */
import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

// ---------------------------------------------------------------------------
// PO parsing
// ---------------------------------------------------------------------------

const PO_ESCAPES = { n: '\n', t: '\t', r: '\r', '"': '"', '\\': '\\' };

/** Unescape a quoted PO value ("a\nb" → 'a\nb'). Returns the cooked string. */
export function unescapePo(value) {
  let out = '';
  for (let i = 0; i < value.length; i += 1) {
    const ch = value[i];
    if (ch === '\\' && i + 1 < value.length) {
      const next = value[i + 1];
      if (next in PO_ESCAPES) {
        out += PO_ESCAPES[next];
        i += 1;
        continue;
      }
    }
    out += ch;
  }
  return out;
}

/**
 * Parse a PO file into entries keyed by catalogue identity
 * (`msgctxt\u0004msgid`, or plain `msgid` when no context is present).
 *
 * @returns {{ entries: Map<string, PoEntry>, errors: string[] }}
 */
export function parsePo(content, filePath = '<catalog>') {
  const entries = new Map();
  const errors = [];

  const lines = content.split(/\r?\n/);
  let entry = null;
  let section = null; // null | 'msgctxt' | 'msgid' | 'msgid_plural' | 'msgstr'
  let pluralIndex = null;
  let lineNo = 0;
  // In PO format, #: / #, comments precede the msgid they describe; msgctxt
  // likewise precedes its msgid.
  let pending = { flags: new Set(), references: [], msgctxt: null };

  const flush = () => {
    if (!entry) return;
    const { msgid, msgctxt } = entry;
    const identity = msgctxt === null ? msgid : `${msgctxt}\u0004${msgid}`;
    if (entries.has(identity)) {
      errors.push(`${filePath}:${entry.line}: duplicate msgid ${JSON.stringify(msgid)}`);
    } else {
      entries.set(identity, entry);
    }
    entry = null;
  };

  for (const raw of lines) {
    lineNo += 1;
    const line = raw.trim();

    if (line === '' || line.startsWith('#~')) {
      if (line === '') {
        flush();
        pending = { flags: new Set(), references: [], msgctxt: null };
      }
      continue;
    }
    if (line.startsWith('#')) {
      if (line.startsWith('#,')) {
        for (const flag of line.slice(2).split(',')) {
          const trimmed = flag.trim();
          if (trimmed) pending.flags.add(trimmed);
        }
      } else if (line.startsWith('#:')) {
        pending.references.push(line.slice(2).trim());
      }
      continue;
    }

    let match;
    if ((match = line.match(/^msgctxt\s+(.*)$/))) {
      if (entry) {
        errors.push(`${filePath}:${lineNo}: msgctxt inside an entry`);
        continue;
      }
      pending.msgctxt = unescapePo(stripQuotes(match[1], filePath, lineNo, errors));
    } else if ((match = line.match(/^msgid\s+(.*)$/))) {
      flush();
      entry = newPoEntry('', lineNo);
      entry.flags = pending.flags;
      entry.references = pending.references;
      entry.msgctxt = pending.msgctxt;
      pending = { flags: new Set(), references: [], msgctxt: null };
      entry.msgid = unescapePo(stripQuotes(match[1], filePath, lineNo, errors));
      section = 'msgid';
      pluralIndex = null;
    } else if ((match = line.match(/^msgid_plural\s+(.*)$/))) {
      if (!entry || entry.msgidPlural !== null) {
        errors.push(`${filePath}:${lineNo}: msgid_plural without a preceding msgid`);
        continue;
      }
      entry.msgidPlural = unescapePo(stripQuotes(match[1], filePath, lineNo, errors));
      section = 'msgid_plural';
    } else if ((match = line.match(/^msgstr(\[(\d+)\])?\s+(.*)$/))) {
      if (!entry) {
        errors.push(`${filePath}:${lineNo}: msgstr without a preceding msgid`);
        continue;
      }
      pluralIndex = match[2] === undefined ? 0 : Number(match[2]);
      if (match[2] !== undefined && entry.msgidPlural === null) {
        errors.push(
          `${filePath}:${lineNo}: indexed msgstr without msgid_plural (${JSON.stringify(entry.msgid)})`,
        );
        continue;
      }
      const value = stripQuotes(match[3], filePath, lineNo, errors);
      entry.msgstr[pluralIndex] = (entry.msgstr[pluralIndex] ?? '') + unescapePo(value);
      section = 'msgstr';
    } else if (line.startsWith('"') && section && entry) {
      // Continuation line for the active section.
      const value = unescapePo(stripQuotes(line, filePath, lineNo, errors));
      if (section === 'msgid') entry.msgid += value;
      else if (section === 'msgid_plural') entry.msgidPlural += value;
      else if (section === 'msgstr') entry.msgstr[pluralIndex] = (entry.msgstr[pluralIndex] ?? '') + value;
    } else if (line.length > 0) {
      errors.push(`${filePath}:${lineNo}: unrecognized line ${JSON.stringify(raw)}`);
    }
  }
  flush();

  return { entries, errors };
}

function newPoEntry(msgid, line) {
  return {
    msgid,
    msgctxt: null,
    msgidPlural: null,
    msgstr: [],
    flags: new Set(),
    references: [],
    line,
  };
}

function stripQuotes(value, filePath, lineNo, errors) {
  if (!(value.startsWith('"') && value.endsWith('"'))) {
    errors.push(`${filePath}:${lineNo}: expected a quoted value, got ${JSON.stringify(value)}`);
    return value;
  }
  return value.slice(1, -1);
}

// ---------------------------------------------------------------------------
// TypeScript source extraction
// ---------------------------------------------------------------------------

/** Recursively collect translatable source files under `dir`. */
const SOURCE_EXTENSIONS = new Set(['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs']);

export function listTsSourceFiles(dir) {
  const files = [];
  const walk = (current) => {
    for (const name of fs.readdirSync(current).sort()) {
      const full = path.join(current, name);
      const stat = fs.statSync(full);
      if (stat.isDirectory()) {
        if (name === 'node_modules') continue;
        walk(full);
      } else {
        const lower = name.toLowerCase();
        const isSource =
          SOURCE_EXTENSIONS.has(path.extname(lower)) && !lower.endsWith('.d.ts') && !lower.endsWith('.d.mts');
        if (isSource) files.push(full);
      }
    }
  };
  walk(dir);
  return files;
}

/** ScriptKind for a source path. */
function scriptKindOf(filePath) {
  const lower = filePath.toLowerCase();
  if (lower.endsWith('.tsx') || lower.endsWith('.jsx')) return ts.ScriptKind.TSX;
  if (lower.endsWith('.ts') || lower.endsWith('.mts') || lower.endsWith('.cts')) return ts.ScriptKind.TS;
  return ts.ScriptKind.JS;
}

const CATALOGUE_CALLS = new Set(['gettext', 'ngettext']);

// Catalogue-related globals declared in globals.d.ts that the gate does not
// support yet. Calls to them fail closed (reported as dynamic) instead of
// being silently ignored, so adding e.g. pgettext("...") to the sources
// requires extending the gate and the catalogue together.
const UNSUPPORTED_CATALOGUE_CALLS = new Set(['pgettext', 'npgettext', 'gettext_noop']);

/**
 * Extract statically translatable gettext()/ngettext() calls from TS sources.
 *
 * @param {{path: string, source: string}[]} sources
 * @returns {{ messages: SourceMessage[], dynamic: DynamicCall[] }}
 *
 * SourceMessage: { msgid, plural|null, path, line, callee }
 * DynamicCall:   { path, line, callee, reason }
 */
export function extractSourceMessages(sources) {
  const messages = [];
  const dynamic = [];

  for (const { path: filePath, source } of sources) {
    const sf = ts.createSourceFile(
      filePath,
      source,
      ts.ScriptTarget.Latest,
      true,
      scriptKindOf(filePath),
    );

    const visit = (node) => {
      if (ts.isCallExpression(node)) {
        const callee = node.expression;
        if (ts.isIdentifier(callee)) {
          const kind = callee.text;
          if (UNSUPPORTED_CATALOGUE_CALLS.has(kind)) {
            dynamic.push({
              path: filePath,
              line: sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1,
              callee: kind,
              reason: 'catalogue call not supported by this gate yet',
            });
            return;
          }
          if (CATALOGUE_CALLS.has(kind)) {
            const line = sf.getLineAndCharacterOfPosition(node.getStart(sf)).line + 1;
            const required = kind === 'ngettext' ? 2 : 1;
            const literals = node.arguments.slice(0, required).map(staticStringOf);
            if (literals.some((value) => value === null)) {
              dynamic.push({ path: filePath, line, callee: kind, reason: 'non-literal argument' });
            } else if (kind === 'gettext') {
              messages.push({ msgid: literals[0], plural: null, path: filePath, line, callee: kind });
            } else {
              messages.push({
                msgid: literals[0],
                plural: literals[1],
                path: filePath,
                line,
                callee: kind,
              });
            }
          }
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(sf);
  }

  return { messages, dynamic };
}

/** Cooked string of a StringLiteral / NoSubstitutionTemplateLiteral, else null. */
function staticStringOf(node) {
  if (node && (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node))) {
    return node.text;
  }
  return null;
}

// ---------------------------------------------------------------------------
// Classification
// ---------------------------------------------------------------------------

/**
 * Named Django-style placeholders (%(name)s, %(name)d, ...). %% pairs (literal
 * percent signs) are masked before scanning so they never count as placeholders.
 */
export function namedPlaceholders(value) {
  const masked = String(value).replace(/%%/g, '');
  const found = new Set();
  const re = /%\((\w+)\)[a-zA-Z]/g;
  let match;
  while ((match = re.exec(masked)) !== null) found.add(match[1]);
  return found;
}

export const CLASSES = ['translated', 'missing', 'empty', 'fuzzy', 'plural-incomplete', 'placeholder-mismatch'];

/**
 * Compare extracted source messages with the parsed catalogue.
 *
 * @param {SourceMessage[]} messages
 * @param {Map<string, PoEntry>} entries
 * @param {{ pluralForms?: number }} options
 * @returns {{ results: object[], counts: Record<string, number> }}
 */
export function classifyMessages(messages, entries, { pluralForms = 2 } = {}) {
  const counts = Object.fromEntries(CLASSES.map((name) => [name, 0]));
  const results = [];

  for (const message of messages) {
    const identity = message.msgid;
    const entry = entries.get(identity);
    const problems = [];

    if (!entry) {
      problems.push({ code: 'missing', detail: `catalogue has no entry for ${JSON.stringify(message.msgid)}` });
    } else {
      if (entry.flags.has('fuzzy')) {
        problems.push({ code: 'fuzzy', detail: 'catalogue entry is marked fuzzy' });
      }
      if (message.plural === null) {
        if (entry.msgidPlural !== null) {
          problems.push({
            code: 'plural-incomplete',
            detail: 'catalogue entry declares a plural form for a singular source message',
          });
        }
        const value = entry.msgstr[0] ?? '';
        if (value === '') problems.push({ code: 'empty', detail: 'German msgstr is empty' });
        else if (placeholderMismatch(message.msgid, value) !== null) {
          problems.push({
            code: 'placeholder-mismatch',
            detail: placeholderMismatch(message.msgid, value),
          });
        }
      } else {
        if (entry.msgidPlural === null) {
          problems.push({
            code: 'plural-incomplete',
            detail: 'catalogue entry has no msgid_plural for an ngettext() source message',
          });
        } else if (entry.msgidPlural !== message.plural) {
          problems.push({
            code: 'plural-incomplete',
            detail: `catalogue plural ${JSON.stringify(entry.msgidPlural)} differs from source plural ${JSON.stringify(message.plural)}`,
          });
        }
        for (let index = 0; index < pluralForms; index += 1) {
          const value = entry.msgstr[index] ?? '';
          if (value === '') {
            problems.push({
              code: 'plural-incomplete',
              detail: `German plural form msgstr[${index}] is empty or missing`,
            });
          } else {
            const expected = index === 0 ? message.msgid : message.plural;
            const mismatch = placeholderMismatch(expected, value);
            if (mismatch !== null) {
              problems.push({ code: 'placeholder-mismatch', detail: mismatch });
            }
          }
        }
      }
    }

    const klass = problems.length === 0 ? 'translated' : problems[0].code;
    counts[klass] += 1;
    results.push({
      path: message.path,
      line: message.line,
      callee: message.callee,
      msgid: message.msgid,
      plural: message.plural,
      problems,
      class: klass,
    });
  }

  return { results, counts };
}

function placeholderMismatch(source, translation) {
  const inSource = namedPlaceholders(source);
  const inTranslation = namedPlaceholders(translation);
  for (const name of inSource) {
    if (!inTranslation.has(name)) {
      return `placeholder %(${name})s is missing in the German translation`;
    }
  }
  for (const name of inTranslation) {
    if (!inSource.has(name)) {
      return `German translation introduces placeholder %(${name})s that is not in the source`;
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Inventory helper
// ---------------------------------------------------------------------------

/**
 * Full inventory: walk the TS tree, parse the catalogue, classify everything.
 * @param {{ sourceDir: string, poPath: string, pluralForms?: number }} options
 */
export function inventory({ sourceDir, poPath, pluralForms = 2 }) {
  const files = listTsSourceFiles(sourceDir);
  const sources = files.map((filePath) => ({ path: filePath, source: fs.readFileSync(filePath, 'utf8') }));
  const poContent = fs.readFileSync(poPath, 'utf8');
  const { entries, errors } = parsePo(poContent, poPath);
  const { messages, dynamic } = extractSourceMessages(sources);
  const { results, counts } = classifyMessages(messages, entries, { pluralForms });
  return { files, sources, entries, errors, messages, dynamic, results, counts };
}
