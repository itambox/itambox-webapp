# Contributing to ITAMbox

ITAMbox accepts focused changes through pull requests. This guide describes the repository's development and review gates; deeper implementation notes live in [DEVELOPMENT.md](DEVELOPMENT.md), and authorization boundaries are recorded in the [tenancy, RBAC, and resource-sharing ADR](itambox/docs/development/adr-0001-tenancy-rbac-and-resource-sharing.md).

## Before you start

- Search the existing issues and pull requests before opening duplicate work.
- Discuss large features, schema changes, and compatibility breaks in an issue before implementation.
- Keep each pull request to one concern. Separate cleanup from behavioral changes unless the cleanup is required for the fix.
- Report vulnerabilities privately through [SECURITY.md](SECURITY.md), never in a public issue.
- Follow the [security scanning and suppression policy](itambox/docs/development/security-scanning.md) for dependency, secret, or scanner-policy changes.

## Prerequisites

| Tool | Supported version or purpose |
|---|---|
| Python | Minimum metadata version 3.12; Python 3.12 is the currently qualified and canonical interpreter for CI, Docker, native Windows development, and lint |
| PostgreSQL | 15 or newer; CI uses PostgreSQL 16 and SQLite is not supported |
| Node.js | 20, with the npm version supplied by that release |
| Git | Current maintained release |
| Docker Compose | Optional for deployment and production smoke tests; the smoke test requires v2.24.4 or newer |
| GNU Make | Optional convenience wrapper; on Windows, use it from Git Bash or WSL |

Native Windows Python 3.12 is supported for the application and normal test suite. LDAP development requires Docker, Linux, or WSL because `python-ldap` has no supported native Windows wheel. Native Windows also uses the documented file-validation fallback instead of libmagic. See [DEVELOPMENT.md](DEVELOPMENT.md#native-windows-support).

### Python version policy

Project metadata requires Python 3.12 or newer, but Python 3.12 is the only currently qualified and canonical interpreter: production Docker images, every blocking CI workflow, native Windows development, and the Flake8 identity baseline all run on it. Python 3.11 and older are not supported, and the lint gate refuses non-canonical interpreters rather than producing results that cannot be compared to the baseline.

Newer Python minor versions (3.13 and later) satisfy the metadata minimum but are evaluated conservatively and separately before becoming supported. A new version is adopted only after, on that interpreter: every pinned dependency installs from a compatible release (including native Windows wheels), the full test suite and the deterministic gates pass in CI, the Flake8 identity baseline is re-validated against the pinned toolchain (tokenizer changes can alter reported findings), and the dependency lock is resolved and validated. Until that evaluation lands as its own reviewed change, newer interpreters are unsupported for development and production even if the application appears to run on them.

## Set up a development checkout

Fork the repository if you do not have branch access, then clone your fork. Organization contributors can clone the main repository directly.

```bash
git clone https://github.com/itambox/itambox-webapp.git
cd itambox-webapp
python -m pip install --user "uv==0.11.31"
uv --version
```

Synchronize the canonical development environment and install the Git hooks:

```bash
uv lock --check
uv sync --locked --group dev
uv run --locked --group dev pre-commit install
```

uv owns the repository-root `.venv`. `pyproject.toml` is the sole source of
direct dependencies and `uv.lock` is the exact environment contract. Do not use
`pip install .` or `pip install -e .`; ITAMbox deliberately does not build a
Python package.

For a dependency change, edit only `pyproject.toml`, regenerate `uv.lock` with
uv `0.11.31`, and review both files together. Use `uv lock --upgrade-package
<name>` for a targeted update or `uv lock --upgrade` for a deliberate full
refresh; normal setup and CI must use `--locked` and must never rewrite the lock.

Copy the environment template, select development mode, and point it at a PostgreSQL database whose user can create test databases and install the `btree_gist` extension.

```bash
cp .env.example .env
# PowerShell: Copy-Item .env.example .env
```

Set `ITAMBOX_ENV=dev` and the `ITAMBOX_DB_*` values in `.env`. If you need a disposable local database and already have Docker, this is sufficient for development and tests:

```bash
docker run --name itambox-postgres-dev --rm -d \
  -e POSTGRES_DB=itambox \
  -e POSTGRES_USER=itambox \
  -e POSTGRES_PASSWORD=itambox \
  -p 5432:5432 \
  postgres:16
```

Build the frontend and initialize the application:

```bash
cd itambox
npm ci
npm run build:all
uv run --locked --group dev python manage.py migrate
uv run --locked --group dev python manage.py seed_data --skip-drop
uv run --locked --group dev python manage.py runserver
```

The seed creates and updates public demo users, organizations, and assets. Use it only against a disposable development database. `--skip-drop` prevents the command from clearing existing records; running `seed_data` without that flag resets domain data. The application is available at <http://127.0.0.1:8000>.

## Branches and commits

Start from an up-to-date `main` branch:

```bash
git switch main
git pull --ff-only
git switch -c fix/short-description
```

Use a branch prefix that describes the work:

- `feat/` for user-visible features
- `fix/` for defects
- `docs/` for documentation
- `test/` for test-only work
- `ci/` for automation
- `chore/` for maintenance

Use the repository's established Conventional Commit style, for example:

```text
fix(assets): preserve custody history on check-in
docs: document the production backup sequence
```

Use `!` or a `BREAKING CHANGE:` footer when a change breaks an API, route, configuration contract, or migration path.

## Implementation expectations

- Follow the neighboring Django app instead of introducing a new local pattern. Standard app layout and cross-layer wiring are documented in [DEVELOPMENT.md](DEVELOPMENT.md).
- Generate migrations with `makemigrations`; do not hand-write them unless the migration cannot be expressed safely by Django and the pull request explains why.
- Preserve tenant scoping and object-level permissions in UI, REST, GraphQL, background jobs, imports, and bulk actions. Cross-tenant access must use explicit `RoleGrant` and `RoleGrantScope` records described by the [authorization ADR](itambox/docs/development/adr-0001-tenancy-rbac-and-resource-sharing.md).
- Use scoped managers and established tenant-aware service boundaries rather than unscoped model queries. Add regression tests for fixes and tests for new behavior; tenant-aware tests should use `TenantTestMixin`.
- Build shared API behavior on `itambox.api`, keep generic object detail/edit/delete routes primary-key based, and retain slugs only where an integration contract explicitly requires them.
- Follow the existing HTMX partial/modal/toast conventions, and propagate `TaskContext` through django-q2 jobs so tenant and actor attribution is preserved.
- Regenerate the OpenAPI schema locally after serializer or endpoint changes and review the diff. The generated schema is currently ignored, so do not force-add it unless the release policy changes.
- Rebuild frontend assets after TypeScript, SCSS, or vendor changes.
- Update user or operator documentation and the current unreleased section of [CHANGELOG.md](CHANGELOG.md) for user-visible changes.

## Run the checks

Run targeted tests while developing, then run the relevant full gates before opening a pull request. The Python suite is not xdist-safe yet, so do not use `pytest -n auto` for the full suite.

From the repository root:

```bash
uv run --locked --group dev pre-commit run --all-files
uv run --locked --only-group dev python scripts/check_flake8_baseline.py
```

From `itambox/`:

```bash
uv run --locked --group dev python manage.py makemigrations --check --dry-run
uv run --locked --group dev python manage.py check
uv run --locked --group dev pytest --cov=. --cov-report=term --cov-fail-under=45

npm ci
npm run build:all
npm run typecheck
npx eslint static/src
```

Documentation dependencies are isolated in their own locked group:

```bash
uv sync --locked --only-group docs
cd itambox
uv run --locked --only-group docs mkdocs build --strict
```

Changes to authentication, authorization, tenant scoping, GraphQL, or background-task attribution should also run the relevant adversarial and boundary suites, including:

```bash
uv run --locked --group dev pytest assets/tests/test_graphql_adversarial.py
uv run --locked --group dev pytest core/tests/test_tenant_security.py core/tests/test_security_boundaries.py
```

For UI flows, run the Playwright suite. Its preflight requires the repository-root `.venv`, an available migrated database, an active superuser, `E2E_USERNAME` and `E2E_PASSWORD`, and an installed Playwright browser:

```bash
cd itambox
uv run --locked --group dev python manage.py createsuperuser
cd tests/e2e
npm ci
npx playwright install chromium
export E2E_USERNAME='<superuser username>'
export E2E_PASSWORD='<superuser password>'
cd ../../..
make e2e
```

For Dockerfiles, Compose configuration, entrypoints, health checks, worker startup, caching, or production settings, run the isolated production smoke test from the repository root:

```bash
./scripts/docker-smoke-test.sh
```

The smoke test requires Docker Compose v2.24.4 or newer and tears down its own temporary stack on exit.

## Open the pull request

A reviewable pull request includes:

1. A concise explanation of the problem and why the chosen fix is appropriate.
2. A linked issue when one exists.
3. The exact automated and manual verification performed.
4. Screenshots or a short recording for visible interface changes.
5. Migration, deployment, rollback, and compatibility notes when applicable.
6. Documentation and changelog updates for user-visible behavior.

Keep generated files in sync with their sources. Do not include unrelated formatting changes, local environment files, credentials, database dumps, build output, or editor state.

By submitting a contribution, you agree that it is licensed under the repository's [Apache License 2.0](LICENSE), as described by the license's contribution terms.
