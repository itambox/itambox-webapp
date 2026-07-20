# Installation

This guide covers the source-built Docker Compose production stack. For a disposable evaluation with development settings and demo data, use "Evaluate from source" in the repository-root `README.md` instead.

ITAMbox has no published container image or tagged release yet. Pin every deployment to a reviewed commit; do not deploy a moving branch without reviewing its migrations and changelog.

## Prerequisites

- Docker Engine 24 or newer with Docker Compose v2.24.4 or newer
- A DNS name and TLS-capable reverse proxy or ingress
- Durable storage for PostgreSQL and uploaded media
- A secrets manager or encrypted backup location

## 1. Clone and pin the source

```bash
git clone https://github.com/itambox/itambox-webapp.git
cd itambox-webapp
DEPLOY_REVISION='full-reviewed-commit-sha'
git checkout --detach "$DEPLOY_REVISION"
cp .env.example .env
```

## 2. Configure production values

The Compose file forces `ITAMBOX_ENV=prod` for the application and worker. Edit `.env` before building.

### 2.1 Critical production variables

These must be set before the application can start in production:

| Variable | Production requirement |
|---|---|
| `ITAMBOX_SECRET_KEY` | Long random Django secret; production refuses the development fallback |
| `ITAMBOX_FIELD_ENCRYPTION_KEYS` | Dedicated comma-separated Fernet keyring; the first key encrypts and all listed keys decrypt |
| `ITAMBOX_API_TOKEN_PEPPERS` | JSON object mapping numeric rotation IDs to dedicated secrets of at least 50 characters |
| `ITAMBOX_DB_NAME`, `ITAMBOX_DB_USER`, `ITAMBOX_DB_PASSWORD` | PostgreSQL database credentials |
| `ITAMBOX_ALLOWED_HOSTS` | External host names plus `127.0.0.1` for the container health probe |
| `ITAMBOX_CSRF_TRUSTED_ORIGINS` | Scheme-qualified external HTTPS origins |
| `ITAMBOX_EMAIL_*` | Working SMTP settings for resets, alerts, and reports |

Generate independent values rather than reusing one secret:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The last value can be placed in a pepper mapping such as `{"1":"<generated-value>"}`. Invalid JSON is ignored and falls back to a `SECRET_KEY`-derived pepper, so verify the syntax before admitting users.

!!! danger "Back up the complete secret set"
    Back up `.env`, especially `ITAMBOX_SECRET_KEY`, `ITAMBOX_FIELD_ENCRYPTION_KEYS`, and `ITAMBOX_API_TOKEN_PEPPERS`, with the database and media. Losing the field-encryption keyring makes encrypted SMTP passwords, license keys, and webhook secrets unreadable. Losing token peppers invalidates existing API tokens.

### 2.2 Environment variable reference

The tables below document every variable available in `.env.example`, organized by category. Variables marked **prod-critical** must be reviewed or set before deploying to production.

---

#### 2.2.1 Environment selection

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_ENV` | Recommended | `prod` (when unset) | `dev` or `prod`. The settings loader fails closed: if neither this nor `ITAMBOX_DEBUG` is set, the app runs in production mode. Set to `dev` for local development. |
| `ITAMBOX_DEBUG` ⚠️ | Recommended | `False` (in prod) | Enable Django debug mode. **Never** set to `True` in production — exposes stack traces, settings, and environment variables to end users. |

---

#### 2.2.2 Public base URL

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_BASE_URL` | Recommended (prod) | *(empty)* | Fully qualified base URL used to build absolute URLs in QR code labels, email links, and outbound references. Must not have a trailing slash. In development, leave blank — the app uses the `itambox:<tag>` URI scheme by default. Example: `https://itam.example.com` |

---

#### 2.2.3 Core / security ⚠️ prod-critical

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_SECRET_KEY` ⚠️ | **Required** (prod) | *(insecure dev fallback)* | Django secret key used for cryptographic signing (sessions, CSRF tokens, password reset tokens). Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. The app refuses to boot in production with the fallback. |
| `ITAMBOX_FIELD_ENCRYPTION_KEYS` ⚠️ | **Required** (prod) | *(derived from SECRET_KEY)* | Fernet key(s) that encrypt sensitive stored values (license keys, SMTP passwords, webhook secrets). Comma-separated; the **first** key encrypts, **all** keys decrypt. Append the old key when rotating. Generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. When unset, the key is derived from `ITAMBOX_SECRET_KEY` as a dev convenience — then rotating `SECRET_KEY` makes every encrypted field **permanently unreadable**. Set a dedicated value and back it up with every database dump. |
| `ITAMBOX_API_TOKEN_PEPPERS` ⚠️ | **Required** (prod) | *(derived from SECRET_KEY)* | Server-side peppers used to HMAC-hash API tokens at rest. Must be a JSON object whose numeric keys are rotation IDs and values are dedicated secrets of ≥50 characters. The highest ID peppers new tokens; older IDs keep existing tokens valid. Example: `{"1":"replace-with-a-random-secret-of-at-least-50-characters"}`. When unset or malformed, falls back to a `SECRET_KEY`-derived pepper — acceptable for development only. |
| `ITAMBOX_ALLOWED_HOSTS` ⚠️ | **Required** (prod) | `localhost,127.0.0.1` | Comma-separated host/domain names the site may serve. Django's `ALLOWED_HOSTS` — requests with unrecognized `Host` headers receive HTTP 400. Always include `127.0.0.1` so the container health probe works. |
| `ITAMBOX_CSRF_TRUSTED_ORIGINS` ⚠️ | **Required** (prod) | *(empty)* | Origins trusted for cross-origin POST/PUT/PATCH/DELETE. Scheme-qualified (e.g. `https://itam.example.com`). Must match the external URL users access the site through. |

---

#### 2.2.4 HTTPS hardening

These are only applied when `ITAMBOX_ENV=prod` (or `ITAMBOX_DEBUG=False`). Leave SSL redirect on unless TLS is terminated upstream and you handle redirects there.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_SECURE_SSL_REDIRECT` | Optional | `True` | Redirect all HTTP requests to HTTPS. Set to `False` if TLS is terminated at a reverse proxy that already handles the redirect. |
| `ITAMBOX_HSTS_SECONDS` | Optional | `31536000` (1 year) | `Strict-Transport-Security` max-age in seconds. Browsers will refuse plain HTTP connections for this duration after the first HTTPS visit. |
| `ITAMBOX_HSTS_INCLUDE_SUBDOMAINS` | Optional | `True` | Apply HSTS policy to all subdomains, not just the apex domain. |
| `ITAMBOX_HSTS_PRELOAD` | Optional | `True` | Opt into browser HSTS preload lists. Only enable if you are committed to HTTPS permanently and can meet [hstspreload.org](https://hstspreload.org) requirements. |

---

#### 2.2.5 Logging

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_LOG_LEVEL` | Optional | `INFO` | Application log level. One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. Use `WARNING` in production to reduce noise; `DEBUG` for troubleshooting. |

---

#### 2.2.6 Database ⚠️ prod-critical

PostgreSQL 15+ is required. The `btree_gist` extension must be available (see [section 3](#3-build-and-initialize)).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_DB_ENGINE` | Optional | `django.db.backends.postgresql` | Django database backend. Only PostgreSQL is supported in production. |
| `ITAMBOX_DB_NAME` ⚠️ | **Required** (prod) | `itambox` | PostgreSQL database name. |
| `ITAMBOX_DB_USER` ⚠️ | **Required** (prod) | `itambox` | PostgreSQL user with full DDL/DML privileges on the database. |
| `ITAMBOX_DB_PASSWORD` ⚠️ | **Required** (prod) | `itambox` | Password for the database user. Change from the default immediately. |
| `ITAMBOX_DB_HOST` ⚠️ | **Required** (prod) | `localhost` | PostgreSQL host. Use the Compose service name (`db`) when using the bundled PostgreSQL container. |
| `ITAMBOX_DB_PORT` | Optional | `5432` | PostgreSQL port. |
| `ITAMBOX_DB_CONN_MAX_AGE` | Optional | `300` | Persistent connection lifetime in seconds. `0` closes connections after each request (not recommended for production). |
| `ITAMBOX_DB_SSLMODE` | Recommended | `prefer` | PostgreSQL SSL mode. Valid values: `prefer`, `require`, `verify-full`. Use `require` or `verify-full` for managed/remote PostgreSQL. |

---

#### 2.2.7 Cache / rate limiting

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_CACHE_BACKEND` | Recommended (prod) | `locmem` | Cache backend. `locmem` (per-process in-memory, not shared across workers) or `redis`. Use a Redis-protocol server in production (Valkey recommended — BSD-licensed fork of Redis) so throttles and rate-limit counters are shared across gunicorn workers. |
| `ITAMBOX_REDIS_URL` | Conditional | `redis://127.0.0.1:6379/1` | Redis connection URL. Required when `ITAMBOX_CACHE_BACKEND=redis`. |
| `ITAMBOX_CACHE_TIMEOUT` | Optional | `300` | Default cache TTL in seconds. |
| `RATELIMIT_CACHE` | Optional | `default` | Cache alias used for the login/invite rate limiter. Point at `redis` in production to share rate-limit state across workers. |

---

#### 2.2.8 Email ⚠️ prod-critical

In development, the app uses a local SMTP catcher automatically. In production, configure working SMTP settings for password resets, alerts, notifications, and reports.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_EMAIL_BACKEND` | Optional | `django.core.mail.backends.smtp.EmailBackend` | Django email backend. Use `django.core.mail.backends.console.EmailBackend` for development debugging. |
| `ITAMBOX_EMAIL_HOST` ⚠️ | **Required** (prod) | `localhost` | SMTP server hostname. |
| `ITAMBOX_EMAIL_PORT` | Optional | `587` | SMTP server port. Common values: `587` (STARTTLS), `465` (SSL), `25` (unencrypted — avoid). |
| `ITAMBOX_EMAIL_HOST_USER` ⚠️ | **Required** (prod) | *(empty)* | SMTP authentication username. |
| `ITAMBOX_EMAIL_HOST_PASSWORD` ⚠️ | **Required** (prod) | *(empty)* | SMTP authentication password. This value is stored encrypted at rest via `ITAMBOX_FIELD_ENCRYPTION_KEYS` if configured in the admin UI; the environment variable itself should still be protected. |
| `ITAMBOX_EMAIL_USE_TLS` | Optional | `True` | Use STARTTLS (port 587). Mutually exclusive with `EMAIL_USE_SSL`. |
| `ITAMBOX_EMAIL_USE_SSL` | Optional | `False` | Use implicit SSL (port 465). Mutually exclusive with `EMAIL_USE_TLS`. |
| `ITAMBOX_DEFAULT_FROM_EMAIL` | Optional | `ITAMbox <no-reply@example.com>` | Default sender address for all outgoing emails. Override to a real address in production. |
| `ITAMBOX_SERVER_EMAIL` | Optional | *(same as DEFAULT_FROM_EMAIL)* | From-address for server error emails (500 errors) sent to `ADMINS`. |
| `ITAMBOX_EMAIL_TIMEOUT` | Optional | `10` | Outbound SMTP timeout in seconds. Bounds web requests and background tasks against a dead mail server. Django's own default would be no timeout at all — keep this set. |

---

#### 2.2.9 Application behavior

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_DEFAULT_CURRENCY` | Optional | `EUR` | ISO 4217 currency code used as the fallback for money display. Each tenant can override its own currency. |
| `ITAMBOX_PAGINATOR_COUNT_CAP` | Optional | `100000` | Upper bound for list-view row counters. Below the cap, exact counts are shown; above, displays `100000+`. Set to `0` for unbounded `COUNT(*)` on every list view — slow at large scale. |
| `ITAMBOX_SESSION_COOKIE_AGE` | Optional | `1209600` (2 weeks) | Interactive session lifetime in seconds. After this duration of inactivity, users must re-authenticate. |
| `ITAMBOX_DOCS_ROOT` | Optional | `BASE_DIR/docs` | Filesystem path to compiled MkDocs output. Override to serve documentation from an external volume. |
| `ITAMBOX_REQUIRE_MFA` | Optional | `True` (in prod) | Enforce TOTP multi-factor authentication for local-password logins by superusers and owner-admin roles. SSO/LDAP/SAML/OIDC logins always delegate MFA to the identity provider regardless of this setting. Set to `False` to disable for local accounts. |

---

#### 2.2.10 Compliance

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_REQUIRE_CUSTODY_SIGNIN` | Optional | `True` | Require a digital signature on custody receipt sign-off. When enabled, users must provide an electronic signature before custody transfers are accepted. |
| `ITAMBOX_ALLOW_GLOBAL_CUSTODY_TEMPLATES` | Optional | `True` | Allow custody templates that are not scoped to a specific tenant. When `False`, templates must be assigned to individual tenants. |

---

#### 2.2.11 Data retention

Set to `0` to keep records forever. See [data-retention.md](data-retention.md) for the complete retention policy and pruning schedule.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_CHANGELOG_RETENTION_DAYS` | Optional | `365` | Days to retain object change history (`ObjectChange` records). Pruned daily by the `prune_changelog` scheduled task. Per-tenant overrides and legal holds are set via `Tenant.changelog_retention_days`. |
| `ITAMBOX_ALERTLOG_RETENTION_DAYS` | Optional | `180` | Days to retain alert log entries. |
| `ITAMBOX_NOTIFICATION_RETENTION_DAYS` | Optional | `90` | Days to retain notification history. |
| `ITAMBOX_QTASK_FAILED_RETENTION_DAYS` | Optional | `90` | Days to retain failed background task records. |

---

#### 2.2.12 Static / media paths

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_STATIC_ROOT` | Optional | *(Django-derived)* | Filesystem path for collected static files. Override to serve static assets from a dedicated volume or CDN origin. The default Compose stack uses WhiteNoise from the application image; an override is only needed for external static serving. |
| `ITAMBOX_MEDIA_ROOT` | Optional | *(Django-derived)* | Filesystem path for user-uploaded media (attachments, label images, QR code exports). Must be on durable, backed-up storage. |

---

#### 2.2.13 Plugins

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_PLUGINS` | Optional | *(empty)* | Comma-separated importable plugin package names. Each package must be installed in the Python environment. Example: `itambox_esign`. See `itambox/docs/plugins/` for the plugin development guide. |

---

#### 2.2.14 CORS

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_CORS_ALLOW_ALL_ORIGINS` | Optional | `False` | Allow cross-origin requests from any origin. **Do not enable in production** — it bypasses the browser same-origin policy entirely. |
| `ITAMBOX_CORS_ALLOWED_ORIGINS` | Optional | *(empty)* | Comma-separated list of origins permitted for cross-origin API requests. Example: `https://portal.example.com,https://dashboard.example.com`. Required if a separate frontend or external service calls the ITAMbox API from a browser. |

---

#### 2.2.15 Multi-tenant SSO (LDAP / SAML / OIDC)

Each variable accepts a JSON object keyed by tenant slug. These configure per-tenant identity provider integration. See also the [SCIM provisioning guide](../integration/scim.md).

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_TENANT_LDAP_CONFIGS` | Optional | *(empty)* | Per-tenant LDAP directory configuration. JSON object keyed by tenant slug. Each value is an object with keys: `SERVER_URI`, `BIND_DN`, `BIND_PASSWORD`, `USER_SEARCH_BASE`, `USER_SEARCH_FILTER`, and optional `REQUIRE_GROUP`. See `.env.example` for the full example. |
| `ITAMBOX_TENANT_SAML_CONFIGS` | Optional | *(empty)* | Per-tenant SAML 2.0 identity provider configuration. JSON object keyed by tenant slug. Uses `djangosaml2` / `pysaml2` configuration format. Each entry requires `entityid` and `metadata` (remote URL or inline XML). |
| `ITAMBOX_TENANT_OIDC_CONFIGS` | Optional | *(empty)* | Per-tenant OpenID Connect provider configuration. JSON object keyed by tenant slug. Each value is an object with standard `mozilla-django-oidc` keys: `OIDC_RP_CLIENT_ID`, `OIDC_RP_CLIENT_SECRET`, `OIDC_OP_AUTHORIZATION_ENDPOINT`, `OIDC_OP_TOKEN_ENDPOINT`, `OIDC_OP_USER_ENDPOINT`. |

!!! note "SSO configuration format"
    Each SSO variable is a single-line JSON string. Escape inner quotes or use a `.env`-compatible quoting strategy. Example for LDAP:
    ```
    ITAMBOX_TENANT_LDAP_CONFIGS='{"tenant-alpha": {"SERVER_URI": "ldap://ldap.alpha-corp.com", ...}}'
    ```

---

#### 2.2.16 Intune discovery

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ITAMBOX_TENANT_INTUNE_CONFIGS` | Optional | *(empty)* | Per-tenant Microsoft Intune device discovery configuration. JSON object keyed by tenant slug. Each value requires `TENANT_ID`, `CLIENT_ID`, and `CLIENT_SECRET` for Microsoft Graph API access. See [discovery-sync.md](../integration/discovery-sync.md) for the full discovery workflow. |

---

#### 2.2.17 DocuSign e-signature integration

Used by the optional `itambox_esign` plugin for digital signature workflows on custody receipts and other documents.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DOCUSIGN_INTEGRATION_KEY` | Conditional | *(empty)* | DocuSign integration key (client ID) from the DocuSign Admin console. Required if using the e-signature plugin. |
| `DOCUSIGN_USER_ID` | Conditional | *(empty)* | DocuSign user ID (GUID) of the impersonated system user. Required if using the e-signature plugin. |
| `DOCUSIGN_ACCOUNT_ID` | Conditional | *(empty)* | DocuSign account ID. Required if using the e-signature plugin. |
| `DOCUSIGN_RSA_PRIVATE_KEY` | Conditional | *(empty)* | RSA private key for JWT assertion authentication with DocuSign. Must be the full PEM-encoded key including `-----BEGIN RSA PRIVATE KEY-----` and footer. Required if using the e-signature plugin. |
| `DOCUSIGN_SANDBOX` | Optional | `True` | Use DocuSign demo/sandbox environment (`account-d.docusign.com`) instead of production (`account.docusign.com`). Set to `False` before processing real signatures. |

## 3. Build and initialize

```bash
docker compose build
docker compose run --rm app python manage.py migrate
docker compose run --rm app python manage.py createsuperuser
```

The image builds frontend assets and runs `collectstatic`; WhiteNoise serves those assets from the application image. A separate static-file volume or web-server mapping is not required by the included stack.

Do not run the full `seed_data` command against production. Its demo dataset contains public credentials and, without `--skip-drop`, clears domain data. Use the development evaluation path instead.

!!! warning "PostgreSQL `btree_gist` extension required"
    Migration `assets.0051` creates the `btree_gist` extension for reservation overlap constraints. The bundled PostgreSQL role can create it. For a managed or least-privilege external database, create the extension as an administrator before running migrations:

    ```sql
    CREATE EXTENSION IF NOT EXISTS btree_gist;
    ```

## 4. Terminate TLS at a reverse proxy

Production settings redirect HTTP to HTTPS and set secure-only session and CSRF cookies. Before starting the long-running services, route the external HTTPS host to the app's HTTP upstream on port 8000, preserve the `Host` header, and set `X-Forwarded-Proto: https`. Do not present `http://localhost:8000` as the user-facing URL.

Restrict the published port to the trusted proxy or firewall boundary; `docker-compose.yml` publishes port 8000 but does not provide TLS itself.

## 5. Verify

```bash
docker compose up -d
docker compose ps
docker compose logs --tail=100 app worker
curl -I https://itam.example.com/health/
```

Replace the example host with the configured external URL, then sign in with the superuser created during initialization.

Next, establish a tested [backup and restore](backup-restore.md) process before loading production data. Follow the [upgrade guide](upgrades.md) for later source revisions.
