# syntax=docker/dockerfile:1

# ---- Stage 1: build the frontend (SCSS + vendor copy + JS bundle) ----
# The compiled output lives in static/dist (git-ignored) and must be built so
# the image is reproducible rather than depending on the builder's disk state.
FROM node:20-slim AS frontend
WORKDIR /app
COPY itambox/package.json itambox/package-lock.json ./
RUN npm ci
COPY itambox/ ./
RUN npm run build:all


# ---- Stage 2: Python runtime ----
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    ITAMBOX_ENV=prod

WORKDIR /app

# System deps:
#   gcc                         - build any sdist-only wheels
#   postgresql-client           - pg_isready / psql for ops & entrypoint waits
#   libldap2-dev, libsasl2-dev  - python-ldap (django-auth-ldap)
#   xmlsec1                      - pysaml2 (djangosaml2) signature handling
#   libmagic1                    - python-magic MIME sniffing for upload validators
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc postgresql-client libldap2-dev libsasl2-dev xmlsec1 libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY itambox/requirements.txt .
RUN pip install -r requirements.txt

COPY itambox/ .
# Bring in the compiled frontend from the node stage.
COPY --from=frontend /app/static/dist ./static/dist

# Collect static assets at build time. collectstatic needs neither a database
# nor real secrets; pass a throwaway key so the prod SECRET_KEY guard does not
# trip during the build.
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
