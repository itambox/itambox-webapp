# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.11.31@sha256:ecd4de2f060c64bea0ff8ecb182ddf46ba3fcccdc8a60cfdbaf20d1a047d7437 AS uv

# ---- Stage 1: build the frontend (SCSS + vendor copy + JS bundle) ----
FROM node:20-slim AS frontend
WORKDIR /app
COPY itambox/package.json itambox/package-lock.json ./
RUN npm ci
COPY itambox/ ./
RUN npm run build:all


# ---- Stage 2: resolve the exact production Python environment ----
FROM python:3.12-slim-bookworm AS python-deps

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc libldap2-dev libsasl2-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project


# ---- Stage 3: build documentation from its locked dependency group ----
FROM python:3.12-slim-bookworm AS docs

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --only-group docs --no-install-project
COPY itambox/mkdocs.yml ./itambox/mkdocs.yml
COPY itambox/docs/ ./itambox/docs/
WORKDIR /app/itambox
RUN /app/.venv/bin/mkdocs build --strict


# ---- Stage 4: minimal runtime image ----
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ITAMBOX_ENV=prod \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

# Runtime tools and libraries for PostgreSQL, LDAP, SAML/xmlsec, and libmagic.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-client libldap-2.5-0 libsasl2-2 xmlsec1 libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-deps /app/.venv /app/.venv
COPY itambox/ .
COPY --from=frontend /app/static/dist ./static/dist
COPY --from=docs /app/itambox/static/docs ./static/docs

# Collect static assets at build time. No database access is required, but prod
# settings reject missing secrets, so use a throwaway build-only value.
RUN ITAMBOX_SECRET_KEY=build-time-collectstatic-only-not-a-real-secret \
    python manage.py collectstatic --noinput

# Drop privileges.
RUN useradd --system --uid 1001 --create-home appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# Web server. The background worker (django-q2) runs from the same image with
# `python manage.py qcluster` — see docker-compose.yml.
CMD ["gunicorn", "core.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120"]
