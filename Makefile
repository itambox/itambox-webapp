# ==============================================================================
# ITAMbox Development Automation Makefile
# ==============================================================================
#
# POSIX-oriented: these recipes require a Bourne-compatible shell and GNU
# Make. On Windows, use Git Bash or WSL; GNU Make must be installed separately
# (e.g. via Chocolatey, Scoop, or MSYS2). Native PowerShell/cmd cannot run
# `make` recipes.

ifeq ($(OS),Windows_NT)
    HOST_PYTHON := py -3.12
    VENV_BIN := .venv/Scripts
else
    HOST_PYTHON := python3.12
    VENV_BIN := .venv/bin
endif

VENV_PYTHON := $(VENV_BIN)/python

.PHONY: help setup run migrate seed test lint e2e clean

help:
	@echo "ITAMbox Development Automation Command Hub"
	@echo "==========================================="
	@echo "Available commands:"
	@echo "  make setup   - Create virtual environment and install dependencies"
	@echo "  make run     - Start local development server with debug active"
	@echo "  make migrate - Run database migrations"
	@echo "  make seed    - Wipe database and seed mock organization and assets data"
	@echo "  make test    - Run all automated unit and integration tests"
	@echo "  make lint    - Run pre-commit style and syntax checks on all files"
	@echo "  make e2e     - Run Playwright end-to-end browser test suite"
	@echo "  make clean   - Remove cache, temporary database, and virtual environment"

setup:
	$(HOST_PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install --upgrade pip
	$(VENV_PYTHON) -m pip install -r requirements-dev.txt
	$(VENV_PYTHON) -m pre_commit install

run:
	$(VENV_PYTHON) itambox/manage.py migrate
	ITAMBOX_DEBUG=true $(VENV_PYTHON) itambox/manage.py runserver

migrate:
	$(VENV_PYTHON) itambox/manage.py migrate

seed:
	$(VENV_PYTHON) itambox/manage.py seed_data

test:
	cd itambox && ../$(VENV_PYTHON) -m pytest

lint:
	$(VENV_PYTHON) -m pre_commit run --all-files

e2e:
	@echo "Running Playwright E2E suite..."
	cd itambox/tests/e2e && npm ci && npm test

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .venv
