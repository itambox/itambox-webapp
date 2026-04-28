# ==============================================================================
# ITAMbox Development Automation Makefile
# ==============================================================================

.PHONY: help setup run migrate seed test lint clean

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
	@echo "  make clean   - Remove cache, temporary database, and virtual environment"

setup:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	.venv/bin/pip install -e .[postgres,redis,dev]
	.venv/bin/pre-commit install

run:
	.venv/bin/python itambox/manage.py migrate
	ITAMBOX_DEBUG=true .venv/bin/python itambox/manage.py runserver

migrate:
	.venv/bin/python itambox/manage.py migrate

seed:
	.venv/bin/python itambox/manage.py seed_data

test:
	.venv/bin/python itambox/manage.py test

lint:
	.venv/bin/pre-commit run --all-files

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	rm -rf .venv
