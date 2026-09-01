#!/usr/bin/env node

import { existsSync, mkdirSync, readFileSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const E2E_ROOT = resolve(dirname(fileURLToPath(import.meta.url)));
const REPO_ROOT = resolve(E2E_ROOT, '..', '..', '..');
const SPEC_ROOT = resolve(E2E_ROOT, 'spec');
const SPEC_FILE = /\.(?:spec|test)\.(?:ts|tsx|js|mjs|cjs)$/;
const SHA = /^[0-9a-f]{40}$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;

function fail(message) {
  console.error(`E2E selected-run failed: ${message}`);
  process.exitCode = 1;
  throw new Error(message);
}

function parseArguments(argv) {
  const result = {
    selection: process.env.E2E_SELECTION_FILE || resolve(E2E_ROOT, 'artifacts/e2e-selection.json'),
    discovery: process.env.E2E_DISCOVERY_FILE || resolve(E2E_ROOT, 'artifacts/e2e-discovery.json'),
    report: process.env.E2E_PLAYWRIGHT_REPORT || resolve(E2E_ROOT, 'playwright-report/results.json'),
    execution: process.env.E2E_EXECUTION_FILE || resolve(E2E_ROOT, 'artifacts/e2e-execution.json'),
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    const option = {
      '--selection': 'selection',
      '--discovery': 'discovery',
      '--report': 'report',
      '--execution': 'execution',
    }[argument];
    if (!option || index + 1 >= argv.length) {
      throw new Error(`unknown or incomplete option ${argument}`);
    }
    result[option] = resolve(argv[index + 1]);
    index += 1;
  }
  return result;
}

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortObject(value[key])]));
  }
  return value;
}

function writeCanonical(path, value) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(sortObject(value), null, 2)}\n`, 'utf8');
}

function readJson(path, label) {
  if (!existsSync(path)) throw new Error(`${label} is missing: ${path}`);
  let value;
  try {
    value = JSON.parse(readFileSync(path, 'utf8'));
  } catch (error) {
    throw new Error(`${label} is not valid JSON: ${String(error)}`);
  }
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${label} must be a JSON object.`);
  }
  return value;
}

function safeRelativePath(value, label) {
  if (typeof value !== 'string' || value.length === 0 || value.includes('\\') || value.includes('\0')) {
    throw new Error(`${label} is not a repository-relative POSIX path.`);
  }
  if (isAbsolute(value) || value.startsWith('/') || value.split('/').some((part) => part === '..' || part === '.') || value.split('/').includes('')) {
    throw new Error(`${label} contains an unsafe path.`);
  }
  return value;
}

function beneath(root, candidate, label) {
  const resolved = resolve(root, candidate);
  const prefix = root.endsWith(sep) ? root : `${root}${sep}`;
  if (resolved !== root && !resolved.startsWith(prefix)) {
    throw new Error(`${label} resolves outside the E2E root.`);
  }
  return resolved;
}

function listSpecFiles(root) {
  if (!existsSync(root)) return [];
  const target = statSync(root);
  if (target.isFile()) {
    return SPEC_FILE.test(root) ? [relative(E2E_ROOT, root).split(sep).join('/')] : [];
  }
  if (!target.isDirectory()) return [];
  const result = [];
  for (const entry of readdirSync(root, { withFileTypes: true })) {
    const path = resolve(root, entry.name);
    if (entry.isDirectory()) result.push(...listSpecFiles(path));
    else if (entry.isFile() && SPEC_FILE.test(entry.name)) {
      result.push(relative(E2E_ROOT, path).split(sep).join('/'));
    }
  }
  return result.sort();
}

function validateIdentity(selection) {
  for (const key of ['base_sha', 'head_sha', 'merge_base_sha']) {
    if (typeof selection[key] !== 'string' || !SHA.test(selection[key])) {
      throw new Error(`selection ${key} is malformed.`);
    }
  }
  if (typeof selection.changed_path_digest !== 'string' || !DIGEST.test(selection.changed_path_digest)) {
    throw new Error('selection changed_path_digest is malformed.');
  }
}

const SELECTION_KEYS = new Set([
  'base_sha',
  'changed_path_digest',
  'event_name',
  'head_sha',
  'merge_base_sha',
  'mode',
  'reasons',
  'schema',
  'scopes',
  'spec_paths',
]);

function validateStringList(value, label, { nonempty = false } = {}) {
  if (!Array.isArray(value) || (nonempty && value.length === 0)) {
    throw new Error(`${label} must be a${nonempty ? ' non-empty' : ''} array.`);
  }
  const copy = [...value];
  if (copy.some((item) => typeof item !== 'string' || item.length === 0 || /[\u0000-\u001f\u007f]/u.test(item))) {
    throw new Error(`${label} contains an invalid string.`);
  }
  if (JSON.stringify(copy) !== JSON.stringify([...new Set(copy)].sort())) {
    throw new Error(`${label} must be sorted and unique.`);
  }
  return copy;
}

function loadSelection(path) {
  const selection = readJson(path, 'selection');
  const actualKeys = Object.keys(selection).sort();
  const expectedKeys = [...SELECTION_KEYS].sort();
  if (JSON.stringify(actualKeys) !== JSON.stringify(expectedKeys)) {
    throw new Error('selection has missing or unknown keys.');
  }
  if (selection.schema !== 1) throw new Error('selection schema must be 1.');
  if (!['none', 'selected', 'full'].includes(selection.mode)) throw new Error('selection mode is unknown.');
  if (selection.event_name === 'pull_request' && selection.base_sha === selection.head_sha && selection.merge_base_sha !== selection.head_sha) {
    throw new Error('pull-request selection has inconsistent empty-diff identity.');
  }
  validateIdentity(selection);
  const scopes = validateStringList(selection.scopes, 'selection scopes');
  const specPaths = validateStringList(selection.spec_paths, 'selection spec_paths');
  if (!Array.isArray(selection.reasons)) throw new Error('selection reasons must be an array.');
  if (selection.mode === 'none') {
    if (scopes.length !== 0 || specPaths.length !== 0) throw new Error('none mode must not contain scopes or paths.');
    if (selection.reasons.some((reason) => reason?.safe_ignore !== true)) {
      throw new Error('none mode may contain only safe-ignore reasons.');
    }
    throw new Error('none mode cannot be executed by run-selected.mjs.');
  }
  if (selection.mode === 'full') {
    if (JSON.stringify(scopes) !== JSON.stringify(['all']) || JSON.stringify(specPaths) !== JSON.stringify(['spec'])) {
      throw new Error('full mode must execute the complete spec root.');
    }
  } else {
    if (scopes.length === 0 || specPaths.length === 0 || !scopes.includes('legacy-smoke') || !scopes.includes('smoke')) {
      throw new Error('selected mode has empty or incomplete scope evidence.');
    }
  }
  const previousReason = { path: '', status: '', matched_rule: '' };
  for (const [index, reason] of selection.reasons.entries()) {
    if (!reason || typeof reason !== 'object' || Array.isArray(reason)) throw new Error(`reason ${index} is invalid.`);
    const reasonKeys = Object.keys(reason).sort();
    const base = ['matched_rule', 'new_path', 'old_path', 'path', 'status'];
    const decision = ['escalation', 'safe_ignore', 'selected'].filter((key) => key in reason);
    const expectedReasonKeys = [...base, ...decision].sort();
    if (JSON.stringify(reasonKeys) !== JSON.stringify(expectedReasonKeys) || decision.length !== 1) {
      throw new Error(`reason ${index} has an invalid shape.`);
    }
    safeRelativePath(reason.path, `selection reason ${index}`);
    if (reason.old_path !== null) safeRelativePath(reason.old_path, `selection reason ${index} old_path`);
    if (reason.new_path !== null) safeRelativePath(reason.new_path, `selection reason ${index} new_path`);
    if (!['A', 'M', 'D', 'R', 'C'].includes(reason.status) || typeof reason.matched_rule !== 'string') {
      throw new Error(`reason ${index} has an invalid status/rule.`);
    }
    if (reason.path !== reason.old_path && reason.path !== reason.new_path) {
      throw new Error(`reason ${index} path is not one of its old/new identities.`);
    }
    const key = { path: reason.path, status: reason.status, matched_rule: reason.matched_rule };
    if (index > 0 && `${key.path}\u0000${key.status}\u0000${key.matched_rule}` < `${previousReason.path}\u0000${previousReason.status}\u0000${previousReason.matched_rule}`) {
      throw new Error('selection reasons must be canonically sorted.');
    }
    Object.assign(previousReason, key);
  }
  const seen = new Set();
  for (const pathValue of specPaths) {
    const pathName = safeRelativePath(pathValue, 'selection spec path');
    if (seen.has(pathName)) throw new Error(`duplicate selection spec path ${pathName}.`);
    seen.add(pathName);
    const target = beneath(E2E_ROOT, pathName, `selection spec path ${pathName}`);
    if (!existsSync(target)) throw new Error(`selected spec path does not exist: ${pathName}.`);
    if (listSpecFiles(target).length === 0) throw new Error(`selected spec path has no discovered spec files: ${pathName}.`);
  }
  return selection;
}

function specArguments(selection) {
  return selection.mode === 'full' ? ['spec'] : [...selection.spec_paths];
}

const PLAYWRIGHT_CLI = resolve(E2E_ROOT, 'node_modules/@playwright/test/cli.js');

function spawnFile(command, args, env) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, {
      cwd: E2E_ROOT,
      env,
      shell: false,
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.setEncoding('utf8');
    child.stderr.setEncoding('utf8');
    child.stdout.on('data', (chunk) => { stdout += chunk; });
    child.stderr.on('data', (chunk) => { stderr += chunk; });
    child.on('error', reject);
    child.on('close', (code, signal) => resolvePromise({ code: code ?? 1, signal, stdout, stderr }));
  });
}

const SPEC_DIR_NAMES = new Set([
  'accessibility',
  'apps',
  'contracts',
  'external',
  'layout',
  'legacy-smoke',
  'regressions',
  'smoke',
]);

function normaliseReportPath(file) {
  let candidate = String(file).replaceAll('\\', '/');
  const marker = candidate.lastIndexOf('/spec/');
  if (marker >= 0) candidate = candidate.slice(marker + 1);
  candidate = candidate.replace(/^\.\//, '').replace(/^\.\.\//, '');
  if (candidate.startsWith('spec/')) return candidate;
  if (candidate.startsWith('auth/')) return candidate;
  const [first] = candidate.split('/');
  return SPEC_DIR_NAMES.has(first) ? `spec/${candidate}` : null;
}

function collectJsonListing(value, output, parentFile = null, parentProject = null) {
  if (!value || typeof value !== 'object') return;
  const titleFile = typeof value.title === 'string' && /\.(?:spec|test)\.(?:ts|tsx|js|mjs|cjs)$/.test(value.title.trim())
    ? value.title.trim()
    : null;
  const file = normaliseReportPath(String(value.file || value.location?.file || titleFile || parentFile || '')) || parentFile;
  const project = value.projectName || value.project || parentProject || 'unknown';
  if (Array.isArray(value.specs)) {
    for (const spec of value.specs) {
      const specFile = normaliseReportPath(String(spec.file || file || '')) || file;
      const specProject = String(spec.tests?.[0]?.projectName || project);
      for (const test of Array.isArray(spec.tests) ? spec.tests : []) {
        if (!specFile) continue;
        output.tests.push({
          id: String(test.testId || spec.id || `${specFile}::${spec.title || 'test'}`),
          spec: specFile,
          project: String(test.projectName || specProject),
        });
      }
    }
  }
  if (Array.isArray(value.tests)) {
    for (const test of value.tests) {
      const testFile = normaliseReportPath(String(test.location?.file || file || '')) || file;
      if (testFile) {
        output.tests.push({
          id: String(test.testId || `${testFile}::${test.title || 'test'}`),
          spec: testFile,
          project: String(test.projectName || project),
        });
      }
    }
  }
  if (Array.isArray(value.suites)) {
    for (const suite of value.suites) collectJsonListing(suite, output, file, project);
  }
}

function parseListing(stdout, rawReport) {
  const output = { tests: [] };
  const candidates = [];
  if (rawReport && existsSync(rawReport)) candidates.push(readFileSync(rawReport, 'utf8'));
  candidates.push(stdout);
  for (const text of candidates) {
    try {
      const parsed = JSON.parse(text);
      collectJsonListing(parsed, output);
    } catch {
      // Older Playwright versions print a human list for --list; use the strict
      // path-shaped line grammar below instead of guessing arbitrary output.
    }
  }
  if (output.tests.length === 0) {
    const pattern = /(?:^|\s)(?:\.\.[\\/])?((?:spec[\\/])?(?:accessibility|apps|contracts|external|layout|legacy-smoke|regressions|smoke|auth)[\\/][A-Za-z0-9._\\/-]+\.(?:spec|test)\.(?:ts|tsx|js|mjs|cjs))(?:[:]\d+(?::\d+)?)?/g;
    for (const line of stdout.split(/\r?\n/)) {
      const match = pattern.exec(line);
      pattern.lastIndex = 0;
      if (!match) continue;
      const spec = normaliseReportPath(match[1]);
      if (!spec) continue;
      const project = line.match(/\[(setup-[^\]]+)\]/)?.[1] || line.match(/\[([^\]]+)\]/)?.[1] || 'unknown';
      output.tests.push({ id: line.trim(), spec, project });
    }
  }
  const unique = new Map(output.tests.map((test) => [`${test.project}\0${test.id}`, test]));
  output.tests = [...unique.values()].sort((left, right) =>
    `${left.spec}\0${left.project}\0${left.id}`.localeCompare(`${right.spec}\0${right.project}\0${right.id}`),
  );
  return output;
}

function focusedInFiles(specPaths) {
  const files = [];
  for (const specPath of specPaths) {
    const target = beneath(E2E_ROOT, specPath, `spec path ${specPath}`);
    if (statSync(target).isDirectory()) files.push(...listSpecFiles(target));
    else files.push(specPath);
  }
  return files.some((file) => {
    const source = readFileSync(beneath(E2E_ROOT, file, `spec file ${file}`), 'utf8');
    return /\b(?:test|describe|suite|it)\.only\s*\(|\b(?:fit|fdescribe|iit)\s*\(/.test(source);
  });
}

function identityFromAnnotations(test) {
  const annotations = Array.isArray(test.annotations) ? test.annotations : [];
  const identity = annotations.find((annotation) => annotation.type === 'e2e-identity')?.description;
  return typeof identity === 'string' ? identity : null;
}

function identityFromAttachments(result) {
  const attachment = Array.isArray(result.attachments)
    ? result.attachments.find((candidate) => candidate.name === 'e2e-identity')
    : undefined;
  if (!attachment) return null;
  if (typeof attachment.body === 'string') {
    try { return Buffer.from(attachment.body, 'base64').toString('utf8'); } catch { return null; }
  }
  if (typeof attachment.path === 'string' && existsSync(attachment.path)) return readFileSync(attachment.path, 'utf8');
  return null;
}

function cleanupStatus(test) {
  const values = [];
  for (const annotation of Array.isArray(test.annotations) ? test.annotations : []) {
    if (annotation.type === 'e2e-cleanup') values.push(annotation.description);
  }
  for (const result of Array.isArray(test.results) ? test.results : []) {
    for (const attachment of Array.isArray(result.attachments) ? result.attachments : []) {
      if (attachment.name !== 'e2e-cleanup') continue;
      if (typeof attachment.body === 'string') {
        try { values.push(JSON.parse(Buffer.from(attachment.body, 'base64').toString('utf8')).success ? 'success' : 'failure'); } catch { values.push('failure'); }
      }
    }
  }
  return values;
}

function normaliseAttemptStatus(status) {
  return String(status || 'unknown').replace('timedOut', 'timed_out');
}

export function finalPlaywrightStatus(test, attempts) {
  const finalAttempt = attempts.at(-1)?.status;
  if (finalAttempt) return normaliseAttemptStatus(finalAttempt);
  const aggregate = normaliseAttemptStatus(test?.status);
  return {
    expected: 'unknown',
    flaky: 'unknown',
    interrupted: 'interrupted',
    passed: 'passed',
    skipped: 'skipped',
    timed_out: 'timed_out',
    unexpected: 'failed',
  }[aggregate] || 'unknown';
}

function collectExecution(value, output, parentFile = null, parentProject = null) {
  if (!value || typeof value !== 'object') return;
  const titleFile = typeof value.title === 'string' && /\.(?:spec|test)\.(?:ts|tsx|js|mjs|cjs)$/.test(value.title.trim())
    ? value.title.trim()
    : null;
  const file = normaliseReportPath(String(value.file || value.location?.file || titleFile || parentFile || '')) || parentFile;
  const project = value.projectName || value.project || parentProject || 'unknown';
  if (Array.isArray(value.specs)) {
    for (const spec of value.specs) {
      const specFile = normaliseReportPath(String(spec.file || file || '')) || file;
      for (const test of Array.isArray(spec.tests) ? spec.tests : []) {
        if (!specFile) continue;
        const results = Array.isArray(test.results) ? test.results : [];
        const attempts = results.map((result, index) => ({
          retry: Number.isInteger(result.retry) ? result.retry : index,
          status: normaliseAttemptStatus(result.status),
          identity: identityFromAttachments(result) || identityFromAnnotations(test),
        }));
        output.tests.push({
          id: String(test.testId || spec.id || `${specFile}::${spec.title || 'test'}`),
          spec: specFile,
          project: String(test.projectName || project),
          status: finalPlaywrightStatus(test, attempts),
          expectedStatus: String(test.expectedStatus || 'passed'),
          attempts,
          cleanup: cleanupStatus(test),
        });
      }
    }
  }
  if (Array.isArray(value.tests)) {
    for (const test of value.tests) {
      const testFile = normaliseReportPath(String(test.location?.file || file || '')) || file;
      if (!testFile) continue;
      const results = Array.isArray(test.results) ? test.results : [];
      const attempts = results.map((result, index) => ({
        retry: Number.isInteger(result.retry) ? result.retry : index,
        status: normaliseAttemptStatus(result.status),
        identity: identityFromAttachments(result) || identityFromAnnotations(test),
      }));
      output.tests.push({
        id: String(test.testId || `${testFile}::${test.title || 'test'}`),
        spec: testFile,
        project: String(test.projectName || project),
        status: finalPlaywrightStatus(test, attempts),
        expectedStatus: String(test.expectedStatus || 'passed'),
        attempts,
        cleanup: cleanupStatus(test),
      });
    }
  }
  if (Array.isArray(value.suites)) {
    for (const suite of value.suites) collectExecution(suite, output, file, project);
  }
}

function reportExecution(reportPath) {
  if (!existsSync(reportPath)) return { malformed: true, error: 'Playwright JSON report is missing.', tests: [] };
  let parsed;
  try {
    parsed = JSON.parse(readFileSync(reportPath, 'utf8'));
  } catch (error) {
    return { malformed: true, error: `Playwright JSON report is malformed: ${String(error)}`, tests: [] };
  }
  const output = { malformed: false, error: null, tests: [] };
  collectExecution(parsed, output);
  const unique = new Map(output.tests.map((test) => [`${test.project}\0${test.id}`, test]));
  output.tests = [...unique.values()].sort((left, right) => `${left.spec}\0${left.project}\0${left.id}`.localeCompare(`${right.spec}\0${right.project}\0${right.id}`));
  return output;
}

async function main() {
  const paths = parseArguments(process.argv.slice(2));
  const selection = loadSelection(paths.selection);
  const git = process.platform === 'win32' ? 'git.exe' : 'git';
  const gitIdentity = await spawnFile(git, ['rev-parse', 'HEAD'], { ...process.env });
  if (gitIdentity.code !== 0 || !SHA.test(gitIdentity.stdout.trim())) {
    throw new Error(`could not resolve tested checkout SHA: ${gitIdentity.stderr.trim()}`);
  }
  const testedCheckoutSha = gitIdentity.stdout.trim();
  const selectedSpecPaths = [...selection.spec_paths].sort();
  const focus = focusedInFiles(selectedSpecPaths);

  const rawDiscoveryPath = resolve(dirname(paths.discovery), 'playwright-list.json');
  const discoveryRun = await spawnFile(
    process.execPath,
    [
      PLAYWRIGHT_CLI,
      'test',
      ...specArguments(selection),
      '--list',
      '--reporter=json',
    ],
    { ...process.env, PLAYWRIGHT_JSON_OUTPUT_NAME: rawDiscoveryPath },
  );
  const listing = parseListing(discoveryRun.stdout, rawDiscoveryPath);
  const allListingTests = listing.tests;
  const productListingTests = allListingTests.filter((test) => test.spec.startsWith('spec/'));
  const discoveredSpecs = [...new Set(productListingTests.map((test) => test.spec))].sort();
  const setupProjects = [...new Set([
    ...allListingTests.map((test) => test.project),
    ...discoveryRun.stdout
      .split(/\r?\n/)
      .map((line) => line.match(/\[(setup-[^\]]+)\]/)?.[1])
      .filter(Boolean),
  ].filter((project) => project.startsWith('setup-')))].sort();
  const discovery = {
    schema: 1,
    selection_identity: {
      event_name: selection.event_name,
      base_sha: selection.base_sha,
      head_sha: selection.head_sha,
      merge_base_sha: selection.merge_base_sha,
      changed_path_digest: selection.changed_path_digest,
    },
    tested_checkout_sha: testedCheckoutSha,
    selected_spec_paths: selectedSpecPaths,
    discovered_specs: discoveredSpecs,
    discovered_tests: productListingTests,
    setup_projects: setupProjects,
    registered_spec_paths: listSpecFiles(SPEC_ROOT),
    focused: focus,
  };
  writeCanonical(paths.discovery, discovery);
  if (discoveryRun.code !== 0 || discoveredSpecs.length === 0) {
    process.stdout.write(discoveryRun.stdout);
    process.stderr.write(discoveryRun.stderr);
    throw new Error(`Playwright discovery failed (exit ${discoveryRun.code}) or discovered no tests.`);
  }

  const executionRun = await spawnFile(
    process.execPath,
    [PLAYWRIGHT_CLI, 'test', ...specArguments(selection)],
    { ...process.env, PLAYWRIGHT_JSON_OUTPUT_NAME: paths.report },
  );
  process.stdout.write(executionRun.stdout);
  process.stderr.write(executionRun.stderr);
  const report = reportExecution(paths.report);
  const productReportTests = report.tests.filter((test) => test.spec.startsWith('spec/'));
  const execution = {
    schema: 1,
    selection,
    selection_identity: discovery.selection_identity,
    tested_checkout_sha: testedCheckoutSha,
    selected_spec_paths: selectedSpecPaths,
    executed_specs: [...new Set(productReportTests.map((test) => test.spec))].sort(),
    executed_tests: productReportTests.map(({ expectedStatus, cleanup, ...test }) => test),
    cleanup: {
      success: productReportTests.every((test) => !test.cleanup.includes('failure')),
      failures: productReportTests.filter((test) => test.cleanup.includes('failure')).map((test) => test.id).sort(),
    },
    focused: focus,
    report: {
      file: paths.report.split(sep).join('/'),
      malformed: report.malformed,
      error: report.error,
    },
  };
  writeCanonical(paths.execution, execution);
  if (executionRun.code !== 0 || report.malformed) process.exitCode = executionRun.code || 1;
  console.log(`E2E execution: ${selection.mode}; checkout ${testedCheckoutSha}; discovered ${listing.tests.length} tests; executed ${report.tests.length} tests.`);
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    if (process.exitCode !== 1) process.exitCode = 1;
    console.error(String(error));
  });
}
