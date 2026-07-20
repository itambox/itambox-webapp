# Changelog

Notable user-visible, operational, compatibility, and security changes to ITAMbox are recorded here. Internal refactors and routine dependency updates are omitted unless they change supported behavior or deployment requirements.

This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The source currently reports `1.0.0-alpha1`, but no version has been tagged or published yet. Until the first tag exists, release material remains under **Unreleased**.

## [Unreleased]

### Added

- Multi-tenant asset lifecycle management for catalogues, assignments, check-in and check-out, reservations, warranties, maintenance, depreciation, disposal, and total cost history.
- Location-aware stock management for accessories, consumables, components, and kits, including barcode and QR workflows and transactional bulk operations.
- Software catalogues, installed-software records, license-seat management, suppliers, and cost centers.
- Beta subscription and procurement workflows for SaaS subscriptions, purchase orders and lines, contracts, and fulfillment links. Requisition-style requests remain part of the asset-request workflow.
- Custody receipts, digital sign-off, audit campaigns, reconciliation reports, frozen audit evidence, and CSV export.
- Tenant roles, tenant groups, delegated resource grants, scoped administration, and provenance-aware sharing for managed-service-provider environments.
- Search, tags, custom fields, saved filters, journals, attachments, labels, dashboards, reports, alerts, notification channels, event rules, and webhooks.
- REST APIs with OpenAPI, Swagger UI, and ReDoc; a scoped GraphQL schema with depth and field-count limits.
- LDAP, SAML, and OIDC sign-in; TOTP for privileged local accounts; Microsoft Intune discovery sync; and Beta SCIM 2.0 provisioning. Tenant endpoints expose Groups read-only; provider-scoped endpoints provision provider-owned Groups.
- Import and export tooling, including a Snipe-IT migration command, model-aware CSV import, and reusable export templates.
- django-q2 background jobs with tenant and user attribution, job monitoring, pending-job cancellation, retries, and retention controls. Running jobs cannot be forcibly stopped.
- A Beta plugin framework with UI, API, navigation, alert, and GraphQL extension points.
- German localization and progressive-web-app metadata for installable browser experiences.
- A production-oriented Docker Compose stack with PostgreSQL, Valkey, an application worker, health checks, a mandatory production secret-key check, and an isolated smoke test.
- MkDocs operator, integration, model, plugin, and developer documentation, including generated data-model diagrams and a release checklist.

### Changed

- Replaced legacy role assignments with the canonical `RoleGrant` and `RoleGrantScope` authorization model, including explicit cross-tenant scopes. This is a breaking prerelease data-model change.
- Standardized generic object detail, edit, and delete routes on numeric primary keys; integration routes may continue to use slugs where their contracts require them.
- Moved shared API infrastructure to `itambox.api` and standardized tenant-aware REST behavior.
- Standardized HTMX navigation, partial rendering, modal actions, toast events, and table refresh behavior.
- Made PostgreSQL mandatory in every environment and moved production cache, rate-limit, and SAML replay state to a shared Valkey or Redis backend. django-q2 continues to use PostgreSQL's ORM broker.
- Separated runtime and contributor dependencies. ITAMbox is installed from a source checkout or locally built container image and is intentionally not a pip-installable package.
- Added explicit Stable and Beta maturity labels so prerelease compatibility expectations are visible per module.

### Removed

- Removed the legacy tenant-invitation flow in favor of explicit membership and provisioning workflows.
- Consolidated the former MSP `Provider` model and dashboard into the tenant tree and scoped RBAC model.
- Removed the former `core.api` compatibility shim after moving shared API infrastructure to `itambox.api`.
- Removed legacy configuration-context behavior that no longer matched the tenant and custom-field model.

### Fixed

- Enforced data-integrity rules for active assignments, license seats, reservation overlap, soft-delete uniqueness, proceeds, and tenant-group cycles.
- Corrected tenant scope restoration, accessible-scope caching, bulk permission checks, and delegated-resource revocation edge cases.
- Made LDAP and file validation fail safely when native dependencies are unavailable on Windows.
- Restored production Docker startup checks, worker validation, PWA installability, Playwright preflight behavior, and mobile header layout.
- Updated list filtering for django-tables2 3 query-string behavior and removed legacy slug-routing fallbacks from generic UI views.
- Corrected production cache configuration and added warnings for unsafe per-process cache use in multi-worker deployments.
- Added concurrency and database constraints for workflows that previously allowed conflicting assignments, reservations, or allocations.

### Security

- Added object-level tenant enforcement and adversarial coverage for UI, REST, GraphQL, import, bulk-action, attachment, and download boundaries.
- Hardened tenant and delegated-resource authorization, role editing, privilege changes, background-task context, and API-token permission evaluation.
- Stored API tokens as peppered hashes and supported pepper rotation without retaining plaintext tokens.
- Encrypted SMTP passwords, license keys, and webhook secrets with the rotatable `ITAMBOX_FIELD_ENCRYPTION_KEYS` Fernet keyring; development installs without a configured keyring fall back to a `SECRET_KEY`-derived key.
- Blocked webhook SSRF, including redirects, private and link-local targets, and DNS-rebinding attempts.
- Added TOTP enforcement for privileged local accounts, login rate limits, SAML replay protection, secure upload and archive validation, and a nonce-based script Content Security Policy.
- Hardened CSV output, redirect validation, and template rendering against formula injection, open redirects, and cross-site scripting.
- Added deterministic, attributed change records and configurable retention or legal holds for operational audit data.

### Known limitations

- SaaS subscriptions, procurement, reporting, webhooks and event rules, SCIM, and the plugin lifecycle remain Beta. Their interfaces may change during the prerelease series.
- There is no tagged release, published container image, or compatibility baseline yet. Evaluate and deploy only from a pinned source revision.
- Alpha upgrades may include breaking migrations. No general version-skipping policy exists yet; review and test the exact target revision with a complete backup and rollback plan.
- The full pytest suite is not safe to run with `pytest-xdist`; use the default serial configuration.
- SQLite is not supported. PostgreSQL 15 or newer is required for development, tests, and production.

[Unreleased]: https://github.com/itambox/itambox-webapp/commits/main
