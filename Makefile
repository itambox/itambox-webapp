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

.PHONY: help setup run migrate seed test coverage coverage-diff coverage-baseline lint format format-check e2e clean

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
	@echo "  make lint          - Run pre-commit style and syntax checks on all files"
	@echo "  make format        - Sort imports then format Python source with Ruff"
	@echo "  make format-check  - Check import order and formatting without writing (CI-safe)"
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

lint:
	$(UV_DEV) pre-commit run --all-files

# Idempotent: import sort runs before formatting, and re-running produces no
# further diff. Ruff owns formatting/import order only -- see [tool.ruff] in
# pyproject.toml; Flake8 (make lint) remains the separate semantic gate.
format:
	$(UV_DEV) ruff check --select I --fix $(FORMAT_TARGETS)
	$(UV_DEV) ruff format $(FORMAT_TARGETS)

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
