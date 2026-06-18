import js from '@eslint/js';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default [
  {
    ignores: [
      'static/dist/**',
      'node_modules/**',
      '**/*.min.js',
    ],
  },
  js.configs.recommended,
  // Non-type-checked TypeScript rules (keeps lint fast and green without a
  // type-aware program — type errors are caught separately by `npm run typecheck`).
  ...tseslint.configs.recommended,
  {
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
      globals: {
        ...globals.browser,
        ...globals.es2020,
        // Django / HTMX globals
        htmx: 'readonly',
        bootstrap: 'readonly',
        TomSelect: 'readonly',
        GridStack: 'readonly',
        // ITAMbox globals
        ITAMboxState: 'readonly',
      },
    },
    rules: {
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-var': 'error',
      'prefer-const': 'warn',
      'eqeqeq': ['error', 'always', { null: 'ignore' }],
    },
  },
  {
    // TypeScript-specific rule tuning. The @typescript-eslint plugin supersedes
    // the core no-unused-vars rule, so disable the core one and configure the
    // type-aware variant to honour the `_`-prefix convention for intentionally
    // unused bindings (args, locals, and caught errors).
    files: ['**/*.ts'],
    rules: {
      'no-unused-vars': 'off',
      '@typescript-eslint/no-unused-vars': ['warn', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
      // The codebase deliberately uses `as any` at a handful of loosely-typed
      // library / HTMX-detail boundaries; tsc (strict) is the source of truth
      // for types, so this syntactic rule is disabled rather than flooding lint.
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
];
