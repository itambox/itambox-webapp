import js from '@eslint/js';
import globals from 'globals';

export default [
  js.configs.recommended,
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
      'no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      'no-var': 'error',
      'prefer-const': 'warn',
      'eqeqeq': ['error', 'always', { null: 'ignore' }],
    },
    ignores: [
      'static/dist/**',
      'node_modules/**',
      '**/*.min.js',
    ],
  },
];
