# Template and Style Linting

Issue #94 makes the authored template and stylesheet sources blocking quality gates.
The gates are deliberately separate from the application test suite so a parser or
formatter regression is visible without waiting for the database-backed tests.

## Canonical commands

Run the check-only gates from the repository root:

```bash
make lint-templates
make lint-styles
make inline-style-check
```

`make lint` also runs both gates through the mandatory full-repository pre-commit
hooks. The djLint and Stylelint hooks never rewrite files; their intentional fix
commands are separate.

Intentional cleanup commands are separate:

```bash
make format-templates
make format-styles
```

For direct frontend use from `itambox/`:

```bash
npm run lint:styles
npm run lint:styles:fix
```

The `:fix` command is for a deliberate local cleanup only. CI invokes only
`npm run lint:styles`.

The CSP/inline-style gate is documented separately in
[CSP and inline-style policy](csp-inline-style-policy.md). It is check-only;
there is no automatic rewrite command because dynamic values require a reviewed
CSS/HTML boundary.

## Template gate

- Tool: `djlint==1.43.2`, installed in the locked `dev` dependency group.
- Profile: `django`.
- Scope: tracked HTML files selected by the shared `scripts/lint_templates.py`
  inventory (the Makefile and CI both invoke that wrapper). It uses the pathspecs
  `itambox/**/templates/**/*.html`, `itambox/**/templates/*.html`, and
  `itambox/templates/*.html`.
- Mode: `--check --lint --statistics`; no baseline and no growth allowance.
- Formatting is owned by `make format-templates`, never by CI or pre-commit.

The standard structural and formatting rules are blocking. H005, H016, H021, and
H030 are the only global rule exclusions: language metadata/title/accessibility
and CSP/SEO concerns belong to issues #101 and #24 rather than this gate.

ITAMbox uses the `django-template-partials` syntax `{% startpartial %} ...
{% endpartial %}`. djLint's custom-block grammar derives the closing tag from the
opening name and therefore cannot model this third-party `startpartial`/`endpartial`
pair. T038 is consequently ignored **only for the 13 known partial templates**,
through the `tool.djlint.per-file-ignores` table in `pyproject.toml`. This is not a
global unknown-tag exemption; all other template files remain subject to T038.

Adding another per-file exception requires a concrete parser limitation, a reason
in this document/configuration, and review. Inline ignore comments are not used.

## Style gate

- Tools: `stylelint==17.14.1` and `stylelint-config-standard-scss==17.0.0`.
- Configuration: `itambox/.stylelintrc.json`.
- Scope: authored `*.css` and `*.scss` below `itambox/static/src/`.
- Generated output under `static/dist/` and vendor files are not lint inputs and are
  not committed artifacts.
- Mode: check-only in CI and pre-commit.

The standard SCSS profile remains active. Two narrow compatibility provisions are
centralized in the configuration:

1. `selector-class-pattern` accepts the project's first-party BEM modifiers and
   elements (`__` and `--`) while still requiring lowercase, hyphenated names.
2. `property-no-vendor-prefix` permits only the authored WebKit fallback
   properties `-webkit-backdrop-filter` and `-webkit-mask-image`.

These are not a general Stylelint disable and do not cover arbitrary framework
selectors or properties. New exceptions must be narrowly scoped and documented.

## Dependencies and CI

`uv.lock` and `itambox/package-lock.json` are committed. CI uses locked installs
and Python 3.12/Node.js 20. The `template-lint`, `stylelint`, and `inline-style` jobs run the full
allowed source inventories; the pull-request path filters only decide whether the
workflow starts. Template, SCSS/CSS, configuration, lockfile, Makefile, and
pre-commit changes all trigger the workflow.

The Sass build still checks that authored SCSS compiles. It is separate from
Stylelint and generated CSS remains transient.
