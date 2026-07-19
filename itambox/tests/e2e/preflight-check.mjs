#!/usr/bin/env node

/**
 * E2E preflight check — validates prerequisites before running Playwright tests.
 *
 * Called automatically by `npm test` (via the `pretest` script). Run directly
 * with `node preflight-check.mjs` for a standalone prerequisite report.
 *
 * Checks:
 *   1. E2E_USERNAME / E2E_PASSWORD are set
 *   2. Python virtual environment exists
 *   3. Django system checks pass
 *   4. Database migrations are applied
 *   5. A usable superuser account exists (basic seed data sanity)
 */

import { execSync } from 'child_process';
import { existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { platform } from 'os';

const __dirname = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(__dirname, '..', '..', '..');

const venvDir = platform() === 'win32' ? '.venv\\Scripts' : '.venv/bin';
const pythonExe = platform() === 'win32'
  ? resolve(repoRoot, '.venv', 'Scripts', 'python.exe')
  : resolve(repoRoot, '.venv', 'bin', 'python');
const managePy = resolve(repoRoot, 'itambox', 'manage.py');

let errors = 0;

function fail(msg) {
  console.error(`  ✗  ${msg}`);
  errors++;
}

function ok(msg) {
  console.log(`  ✓  ${msg}`);
}

function run(cmd, opts = {}) {
  try {
    return execSync(cmd, {
      encoding: 'utf-8',
      stdio: 'pipe',
      cwd: repoRoot,
      ...opts,
    });
  } catch (e) {
    return null;
  }
}

// ── Check 1: environment variables ──────────────────────────────────────────
console.log('\nE2E Prerequisites');
console.log('─────────────────\n');

if (process.env.E2E_USERNAME) {
  ok(`E2E_USERNAME = ${process.env.E2E_USERNAME}`);
} else {
  fail('E2E_USERNAME is not set');
}

if (process.env.E2E_PASSWORD) {
  ok('E2E_PASSWORD is set');
} else {
  fail('E2E_PASSWORD is not set');
}

// ── Check 2: virtual environment ────────────────────────────────────────────
if (existsSync(pythonExe)) {
  ok(`Python virtualenv found at ${pythonExe}`);
} else {
  fail(`Python virtualenv not found at ${pythonExe}`);
}

// ── Check 3: Django system checks ───────────────────────────────────────────
if (existsSync(pythonExe) && existsSync(managePy)) {
  const checkResult = run(`"${pythonExe}" manage.py check --deploy 2>&1`, {
    cwd: resolve(repoRoot, 'itambox'),
  });
  if (checkResult && !checkResult.includes('ERROR')) {
    // --deploy may emit warnings; those are informational
    const errorLines = checkResult.split('\n').filter(l => l.includes('ERROR'));
    if (errorLines.length === 0) {
      ok('Django system checks passed');
    } else {
      fail(`Django system checks failed:\n${checkResult.trim()}`);
    }
  } else if (checkResult) {
    fail(`Django system checks failed:\n${checkResult.trim()}`);
  } else {
    fail('Django system checks could not run (is the database available?)');
  }
}

// ── Check 4: migrations ─────────────────────────────────────────────────────
if (existsSync(pythonExe) && existsSync(managePy)) {
  const migResult = run(`"${pythonExe}" manage.py showmigrations --plan 2>&1`, {
    cwd: resolve(repoRoot, 'itambox'),
  });
  if (migResult) {
    const unapplied = migResult.split('\n').filter(l => l.startsWith('[ ]'));
    if (unapplied.length === 0) {
      ok('All migrations applied');
    } else {
      fail(`${unapplied.length} unapplied migration(s). Run: make migrate`);
    }
  } else {
    fail('Could not check migrations (is the database available?)');
  }
}

// ── Check 5: seed data / superuser ──────────────────────────────────────────
if (existsSync(pythonExe) && existsSync(managePy)) {
  const shellCmd = `"${pythonExe}" manage.py shell -c "from django.contrib.auth import get_user_model; print(get_user_model().objects.filter(is_superuser=True, is_active=True).count())" 2>&1`;
  const userResult = run(shellCmd, { cwd: resolve(repoRoot, 'itambox') });
  if (userResult !== null) {
    const count = parseInt(userResult.trim(), 10);
    if (!isNaN(count) && count > 0) {
      ok(`Seed data present (${count} active superuser(s))`);
    } else {
      fail('No active superuser found. Run: make seed');
    }
  } else {
    fail('Could not query user data');
  }
}

// ── Summary ─────────────────────────────────────────────────────────────────
console.log('');
if (errors === 0) {
  console.log('All E2E prerequisites satisfied. ✓\n');
  process.exit(0);
} else {
  console.error(`${errors} prerequisite(s) missing. Fix them and re-run.\n`);
  console.error('Quick start:');
  console.error('  export E2E_USERNAME=admin');
  console.error('  export E2E_PASSWORD=admin123');
  console.error('  make seed          # ensure seed data exists');
  console.error('  make migrate       # ensure migrations are applied');
  console.error('  cd itambox/tests/e2e && npm test\n');
  process.exit(1);
}
