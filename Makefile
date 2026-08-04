# ==============================================================================
# ITAMbox Development Automation Makefile
# ==============================================================================
#
# POSIX-oriented: these recipes require a Bourne-compatible shell and GNU
# Make. On Windows, use Git Bash or WSL; GNU Make must be installed separately
# (e.g. via Chocolatey, Scoop, or MSYS2). Native PowerShell/cmd cannot run
# `make` recipes.

UV := uv
UV_DEV := $(UV) run --locked --group dev

.PHONY: help setup run migrate seed test coverage coverage-diff coverage-baseline openapi-check openapi-write exception-check exception-baseline architecture-check architecture-baseline typecheck lint lint-templates lint-styles inline-style-check format format-check format-templates format-styles e2e clean

FORMAT_TARGETS := itambox scripts

# Same measurement CI performs: complete serial suite, clean database, branch
# coverage, and the report artifacts the quality gates read.
#
# --cov-config is required: coverage.py reads its configuration from the current
# directory, and pytest runs from itambox/, so without it the root pyproject.toml
# is ignored -- no branch measurement, and migrations and tests counted as source.
COVERAGE_ARGS := -o addopts="--tb=short -p no:warnings" --create-db \
	--cov=. --cov-config=../pyproject.toml --cov-report=term-missing:skip-covered \
	--cov-report=xml:coverage.xml --cov-report=json:coverage.json \
	--cov-report=html:htmlcov --junitxml=junit.xml --durations=25

help:
	@echo "ITAMbox Development Automation Command Hub"
	@echo "==========================================="
	@echo "Available commands:"
	@echo "  make setup         - Create virtual environment and install dependencies"
	@echo "  make run           - Start local development server with debug active"
	@echo "  make migrate       - Run database migrations"
	@echo "  make seed          - Wipe database and seed mock organization and assets data"
	@echo "  make test          - Run all automated unit and integration tests"
	@echo "  make coverage      - Run the suite with branch coverage and check the quality gates"
	@echo "  make coverage-diff - Check differential coverage of the current branch (needs make coverage first)"
	@echo "  make coverage-baseline - Record the measured coverage as the reviewed baseline"
	@echo "  make openapi-check  - Verify deterministic schema and no-growth diagnostics baseline"
	@echo "  make openapi-write  - Update reviewed OpenAPI artifacts (Linux/Python 3.12 only)"
	@echo "  make exception-check - Verify broad/pass-only exception policy and baseline"
	@echo "  make exception-baseline - Record reviewed exception-handler cleanup"
	@echo "  make architecture-check - Verify the architecture boundary graph and baseline"
	@echo "  make architecture-baseline - Record reviewed architecture-boundary cleanup"
	@echo "  make typecheck     - Check the statically typed modules (mypy + django-stubs allowlist)"
	@echo "  make lint          - Run pre-commit style and syntax checks on all files"
	@echo "  make lint-templates - Check all authored Django templates with djLint"
	@echo "  make lint-styles   - Check all authored CSS/SCSS with Stylelint"
	@echo "  make inline-style-check - Check CSP inline-style policy"
	@echo "  make format        - Sort imports then format Python source with Ruff"
	@echo "  make format-check  - Check import order and formatting without writing (CI-safe)"
	@echo "  make format-templates - Reformat authored Django templates (intentional write)"
	@echo "  make format-styles - Auto-fix authored CSS/SCSS (intentional write)"
	@echo "  make e2e           - Run Playwright end-to-end browser test suite"
	@echo "  make clean         - Remove cache, temporary database, and virtual environment"

setup:
	$(UV) lock --check
	$(UV) sync --locked --group dev
	$(UV_DEV) pre-commit install

run:
	$(UV_DEV) python itambox/manage.py migrate
	ITAMBOX_DEBUG=true $(UV_DEV) python itambox/manage.py runserver

migrate:
	$(UV_DEV) python itambox/manage.py migrate

seed:
	$(UV_DEV) python itambox/manage.py seed_data

test:
	cd itambox && $(UV_DEV) pytest

coverage:
	cd itambox && $(UV_DEV) pytest $(COVERAGE_ARGS)
	$(UV_DEV) python scripts/check_test_report.py --report itambox/junit.xml
	$(UV_DEV) python scripts/check_coverage_baseline.py --coverage-json itambox/coverage.json

# Differential gate for the current branch. Defaults to origin/main as the base;
# override with `make coverage-diff BASE_REF=origin/release-1.0`.
BASE_REF ?= origin/main
coverage-diff:
	$(UV_DEV) python scripts/check_diff_coverage.py --base-ref $(BASE_REF) --coverage-json itambox/coverage.json

# Records the measured rates as the reviewed baseline. Recording a DECLINE
# additionally requires --allow-decline --reason "..." (see
# itambox/docs/development/test-coverage-policy.md).
coverage-baseline:
	$(UV_DEV) python scripts/check_coverage_baseline.py --coverage-json itambox/coverage.json --write-baseline

openapi-check:
	PYTHONPATH= PYTHONHASHSEED=0 $(UV_DEV) python scripts/check_openapi_schema.py

# Canonical writes are guarded by the script and only work on Linux/Python 3.12.
# Existing baselines may remove fixed identities but never accept new debt.
openapi-write:
	PYTHONPATH= PYTHONHASHSEED=0 $(UV_DEV) python scripts/check_openapi_schema.py --write-schema --write-baseline

exception-check:
	PYTHONPATH= $(UV_DEV) python scripts/check_exception_policy.py

# Records only cleanup of known identities; the script refuses new debt and
# security-sensitive silent handlers even in write mode.
exception-baseline:
	PYTHONPATH= $(UV_DEV) python scripts/check_exception_policy.py --write-baseline

architecture-check:
	PYTHONPATH= $(UV_DEV) python scripts/check_architecture.py

# Normalises ordering and re-stamps the fingerprint. Drops rows that are no
# longer observed and refuses newly observed ones; see
# itambox/docs/development/architecture-policy.md for the bootstrap sequence.
architecture-baseline:
	PYTHONPATH= $(UV_DEV) python scripts/check_architecture.py --write-baseline

# Needs the full dev environment, not --only-group dev: the django-stubs mypy
# plugin imports the settings module, so Django itself must be installed. There
# is no write mode -- admitting a module to scripts/typing_checked_modules.json
# is a reviewed edit. Linux is the authority; the gate says so on other
# platforms rather than presenting a local green run as CI parity.
typecheck:
	PYTHONPATH= $(UV_DEV) python scripts/check_typing_policy.py

lint:
	$(UV_DEV) pre-commit run --all-files

# Full authored-template inventory; the central djLint configuration in
# pyproject.toml owns profile, scope exclusions, and the 13 explicit partial
# template exceptions. This target is check-only.
lint-templates:
	$(UV_DEV) python scripts/lint_templates.py --check --lint --statistics

# Stylelint's config and source scope live under itambox/. This target is
# check-only; use format-styles for the intentional local cleanup pass.
lint-styles:
	cd itambox && npm run lint:styles

inline-style-check:
	$(UV_DEV) python scripts/check_inline_styles.py

# Idempotent: import sort runs before formatting, and re-running produces no
# further diff. Ruff owns formatting/import order only -- see [tool.ruff] in
# pyproject.toml; Flake8 (make lint) remains the separate semantic gate.
format:
	$(UV_DEV) ruff check --select I --fix $(FORMAT_TARGETS)
	$(UV_DEV) ruff format $(FORMAT_TARGETS)

format-templates:
	$(UV_DEV) python scripts/lint_templates.py --reformat --statistics

format-styles:
	cd itambox && npm run lint:styles:fix

format-check:
	$(UV_DEV) ruff check --select I $(FORMAT_TARGETS)
	$(UV_DEV) ruff format --check $(FORMAT_TARGETS)

e2e:
	@echo "Running Playwright E2E suite..."
	cd itambox/tests/e2e && npm ci && npm test

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .venv
