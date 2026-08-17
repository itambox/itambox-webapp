# Changelog

Notable user-visible, operational, compatibility, and security changes to ITAMbox are recorded here. Internal refactors and routine dependency updates are omitted unless they change supported behavior or deployment requirements.

This changelog follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Public tags use dotted prereleases such as `v1.0.0-alpha.1`; Python metadata maps the same identity to its PEP 440 form (`1.0.0a1`) where required.

## [Unreleased]

### Changed

- The first German asset UI text chunk uses the established glossary terms and clearer labels for asset types, locations, custody receipts, and warranties.
- Tightened the first English asset/request text slice: selection errors, request labels, custody notifications, disposal guidance, and bulk receiving copy are shorter and more direct without changing behavior.

### Fixed

- Asset edits no longer clear purchase and in-service dates when localized HTML5 date controls are submitted after changing the tenant, location, or another field (issue #391).

## [1.0.0-beta.1] - 2026-08-16

### Added

- Scheduled reports with a cross-tenant scope now have an operator approval workflow: the schedule list shows the scope approval state, and a dedicated Scope Approval page approves or revokes the durable authorization (`reports.view_cross_tenant_reports`). Revocation keeps the approval history and fails delivery closed until a fresh approval covers the full scope.
- Resource grants can expire: expiry dates drive a system revocation sweep, and a scoped audit API exposes the grant-expiry history (issue #195).
- Webhook delivery is durable and observable: deliveries run through a state machine with retry handling and typed per-attempt outcomes instead of fire-and-forget sends (PR #349).
- The production GraphQL surface enables schema introspection for authenticated users and exposes an authenticated GraphiQL interface (PRs #372, #376).

### Changed

- The report-designer opt-in flag is now `ITAMBOX_FEATURE_REPORT_DESIGNER`; `ITAMBOX_REPORT_DESIGNER_ENABLED` remains a deprecated 1.x fallback. When the flag is off, scheduled delivery is skipped for non-grandfathered templates, while the migration-managed bounded grandfathered set continues to render and deliver; grandfathered templates are read-only until the designer is enabled.
- Event Rule conditions were withdrawn for 1.0: unsupported conditions fail closed and existing rows are preserved (issue #187).
- Alert-channel delivery now records typed, observable outcomes per delivery attempt (issue #185).
- Tenant-surface SCIM group reads require the `users.view_usergroup` permission (issue #193).
- Accessibility was qualified across full-page and HTMX interaction journeys, and the remaining review gaps were closed (issue #101).
- The runtime image refreshes its CA-certificate bundle during the build (issue #370).

### Fixed

- API token lifecycle: the key is shown once at creation, responses carry ETags, deletion works without a queryset, owner transfer returns 200, and creation without an active tenant fails closed (issues #341, #353).
- User configuration and asset-tag-sequence endpoints reject unknown request fields instead of silently ignoring them (issue #344, PR #354).
- `OPTIONS` on collection endpoints no longer raises 500 (issue #340).
- API root and API namespace root discovery return 200 for authenticated users instead of 500 (issues #345, #363).
- Scanner lookup resolves across all accessible tenants and accepts EAN/GTIN codes in every scan flow (issue #367).
- Frontend: page headers stack actions on mobile, the mobile header/footer order and sidebar search are corrected, the dashboard switcher aligns with the action row, and mobile dashboard changelog cards carry field headings (PRs #365, #366, #368, #373, issue #374).

### Security

- Dashboard creation through the API sets the owner from the request context and rejects owner spoofing (PR #352).
- The runtime image pins a patched nanoid dependency instead of the vulnerable override.

## [1.0.0-alpha.3] - 2026-08-12

### Added

- Added a shared typed external-integration error contract with retryable/terminal classification, safe user messages, bounded Graph retry handling, structured tenant/actor/request context, and documented follow-up boundaries for other adapters.
- Added the versioned webhook envelope v1 with additive `schema_version`, `event_id`, `delivery_id`, `attempt`, and owning-`tenant` fields across generic, Slack, and Teams payloads; strict additional-property validators must update their schemas.
- Published the 1.x compatibility, deprecation, and support policy together with a bounded external-contract inventory covering REST/GraphQL/SCIM surfaces, the webhook envelope, persisted choice values, contract-bearing settings, permission codenames, UI URL namespaces, and each capability's contract class and exclusions. A stdlib gate derives every enumerated surface from source and fails when the published contract and the code disagree.
- Added explicit Purchase Order lifecycle endpoints at `/api/procurement/purchase-orders/{id}/approve/`, `/order/`, `/receive/`, `/cancel/`, and `/reopen/`.
- Published the bounded procurement Stable qualification matrix, including existing UI, REST, service, tenant, permission, audit, currency, and PostgreSQL concurrency guarantees plus the deliberately absent surfaces.
- Added explicit, opt-in Event retention controlled by `ITAMBOX_EVENT_RETENTION_DAYS`, with observable pruning of expired events.
- Added opaque SCIM resource IDs and per-domain scoped `externalId` values; the previous identifier scheme remains readable through a 1.x dual-read window.

### Changed

- Intune discovery now records typed integration failures with safe user-facing messages and structured tenant-scoped job-log context; optional detected-software degradation is explicit in the completed result as `software_degraded`.
- Qualified the four-state Subscription lifecycle as Stable across model, UI, REST, GraphQL, import/export, assignment, seat-accounting, and daily-task boundaries, including idempotent retries and model-level tenant validation.
- Subscription status is now a closed four-state lifecycle (`active`, `suspended`, `cancelled`, `expired`) driven by explicit UI, REST, GraphQL, admin, and background actions. The canonical renewal-term field is `vendor_contract_auto_renews`; `auto_renewal` remains a 1.x read/write API compatibility alias.
- Purchase Order `status` and Purchase Order Line `qty_received` are read-only in the REST schema. API clients must use the corresponding lifecycle endpoint: differing direct writes now return HTTP 400 with sanctioned-action guidance, while identical values remain accepted and ignored for full-representation PUT compatibility. Existing rows require no migration.
- Asset Request auto-approval and the Beta Asset Request procurement seam are now opt-in through `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS`; fresh deployments leave requests pending. The legacy setting name remains a deprecated 1.x fallback with a startup warning.
- Alert rules with no configured channels now deliver nowhere instead of implicitly fanning out to every enabled tenant or tenant-less channel; platform-global in-app delivery remains explicit.
- Closed the Event action vocabulary: `EventRule.events` is validated against the supported action set ahead of the 1.0 contract freeze.
- Scheduled reports are gated on the report designer opt-in: with the designer disabled, the navigation entry is hidden, all scheduled-report routes return 404, and the background task fails closed while preserving saved rows.
- Completed the zero-error OpenAPI generation contract and the generated-client compatibility check.
- Updated locked runtime dependencies to redis-py 8.1 and django-redis 7.0.

### Fixed

- Made invalid generic list filters fail closed to an empty result while retaining field-level validation errors across full-page, HTMX, direct-query, and saved-filter requests.
- Serialized concurrent Purchase Order approvals so only one draft-to-approved transition succeeds, and preserved the Purchase Order currency on assets materialized by receiving.
- Scheduled-report runs now preserve their failure status and the original exception, keeping failed deliveries observable.
- Corrected the alert-channel delivery documentation to match shipped behavior (synchronous, single-attempt delivery).
- Made SCIM capability metadata truthful: removed the fake HTTP Basic capability, fixed `User.lastModified` handling, and wired request-id propagation.
- Custom (non-CRUD) permissions declared via `Meta.permissions` are now exposed in the role editor, making actions such as preparing and exporting custody receipts grantable through the UI.
- A valid signing session now overrides the seven-day custody bearer-link TTL, restoring assisted handoff on receipts older than seven days.
- Fixed custody handoff UI issues: QR codes have a proper quiet zone, the handoff panel is mobile-safe, actions wrap on small screens, Bootstrap collapses rebind after HTMX swaps, copy feedback is sticky with a clipboard fallback, and custody export links are no longer htmx-boosted.
- Fixed frontend issues: app-external links (docs, API, GraphQL, admin) are no longer htmx-boosted, the report preview button no longer leaks across pages, theme-token changelog panels and mobile stacking render correctly, Purchase Order and Resource Grant labels are localized, the responsive asset shell cleanup is complete, and scanner camera detections are throttled through one shared gate.

### Security

- Hardened the Intune Graph boundary against cross-registration token-cache reuse, bearer forwarding through redirects or untrusted `@odata.nextLink` hosts, provider-controlled device-ID path pivots, stale token lifetimes, and credential/payload leakage in exception and job logs.
- Added cross-tenant read/write matrices for contracts, Purchase Orders, and Purchase Order lines together with real two-connection receipt and approval race tests.
- Centralized Asset Request-to-Purchase Order linking in a tenant-locked, permission-checked, idempotent service and made failed UI linking roll back the new purchase order.
- Removed the unused global pip installation and ensurepip bootstrap from the production runtime image, with build-time checks that the copied runtime environment remains pip-free, while preserving locked uv-based dependency resolution in the builder.
- Global cross-tenant report aggregation is now permission-gated; the curated built-in report contract remains unchanged.
- Froze and proved the tenant resource-grant security boundary with cross-tenant read/write matrices and adversarial coverage.
- Removed the remaining unsafe-inline style dependency from the Content Security Policy and restored runtime dashboard styles under the hardened policy.
- Remediated the dependency security-gate findings in the locked dependency set.

## [1.0.0-alpha.2] - 2026-07-28

### Added

- Added deterministic CI ratchets for test coverage and certification, changed-code coverage, local imports, exception handling, OpenAPI diagnostics, architecture layers, and import cycles. Existing debt is explicit and cannot grow silently.
- Added an architecture decision record and contributor guidance for module layers, approved import directions, inline-import annotations, exception boundaries, and the supported public plugin API.
- Added focused security and authorization regression coverage for generic views, membership and role-grant services, login and tenant SSO entry points, inventory stock actions, and asset-form scoping.

### Changed

- Standardized Python formatting and import ordering with Ruff, reduced selected function complexity, and paid down selected findings while preserving the existing deterministic flake8 no-growth ratchet.
- Stabilized generic detail, list, create, edit, delete, and service-action views around fail-closed authorization checks, and standardized HTMX success responses for restore and service actions.
- Extracted membership and role-grant operations from presentation forms into explicit organization services with tenant-, container-, and delegated-scope enforcement.
- Consolidated accessory, consumable, and component stock actions on shared transactional services and authorization checks.
- Decomposed `AssetForm` initialization into focused collaborators while preserving tenant scoping, initial-value precedence, and validation behavior.
- Made OpenAPI generation deterministic and checked in the canonical schema plus a warning/error identity baseline.
- Made the daily subscription expiry/reminder task enumerate all tenants and use the local date, avoiding tenant omissions and one-day boundary drift.
- Updated locked dashboard and Django integration dependencies to GridStack 13.1.2, django-filter 26.1, django-htmx 1.28.0, and django-otp 1.7.0; refreshed frontend build and browser-test tooling and added browser coverage for GridStack initialization, resizing, and persisted layouts.

### Fixed

- Hardened login and tenant SSO entry points against stale tenant selection, unsafe fallback behavior, ambiguous provider routing, and signing-algorithm drift; corrected the documented tenant OIDC routes.
- Replaced security-sensitive silent exception handling with explicit, justified boundary behavior and a blocking policy gate.
- Removed an accidental internal gap report from release source archives.
- Updated PostCSS and brace-expansion dependencies past their reported security advisories.

### Security

- Added fail-closed authorization tests around shared action views, membership and RBAC mutations, tenant selection, SAML POST handling, and OIDC provider validation.
- Escaped configurable `AssetForm` tag-prefix help text so crafted values cannot break HTML attributes or inject active SVG markup.
- Made `domain-model -> presentation` imports unconditionally forbidden and made new module-top or deferred import cycles fail CI.
- Required every retained broad/silent exception, local import, cycle, and cross-layer dependency in the scanned production scope to carry a reviewable identity or inline justification.

### Known limitations and upgrade requirements

- The architecture, exception, local-import, OpenAPI, lint, and coverage baselines intentionally freeze pre-existing debt; they prevent growth but do not claim that the recorded debt is already removed.
- Upgrades from deployments that used arbitrary passphrases in `ITAMBOX_FIELD_ENCRYPTION_KEYS` must carry forward the exact previously derived Fernet key. Substituting a replacement key makes existing encrypted secrets unreadable; follow the installation guide before deploying this alpha.
- `ITAMBOX_TENANT_LDAP_CONFIGS`, `ITAMBOX_TENANT_SAML_CONFIGS`, `ITAMBOX_TENANT_OIDC_CONFIGS`, and `ITAMBOX_TENANT_INTUNE_CONFIGS` must contain JSON objects. Malformed JSON or non-object values now stop startup with `ImproperlyConfigured` instead of silently disabling the integration configuration.
- Review tenant-specific SAML and OIDC mappings before upgrading. SAML requests now require a live, configured tenant; a `default` SAML configuration is accepted only when exactly one live tenant makes it unambiguous, and missing, deleted, or inactive tenant bindings return 404.
- SaaS subscriptions, procurement, reporting, webhooks and event rules, SCIM, and the plugin lifecycle remain Beta. Their interfaces may change during the prerelease series.
- Alpha upgrades may include breaking migrations. No general version-skipping policy exists yet; review and test the exact target revision with a complete backup and rollback plan.
- The full pytest suite is not safe to run with `pytest-xdist`; use the default serial configuration.
- SQLite is not supported. PostgreSQL 15 or newer is required for development, tests, and production.

## [1.0.0-alpha.1] - 2026-07-24

### Added

- Multi-tenant asset lifecycle management for catalogues, assignments, check-in and check-out, reservations, warranties, maintenance, depreciation, disposal, and total cost history.
- Location-aware stock management for accessories, consumables, components, and kits, including barcode and QR workflows and transactional bulk operations.
- Software catalogues, installed-software records, license-seat management, suppliers, and cost centers.
- Beta subscription and procurement workflows for SaaS subscriptions, purchase orders and lines, contracts, and Asset Request fulfillment links.
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
- Standardized direct Python dependencies in `pyproject.toml` with an exact cross-platform `uv.lock`; CI, contributors, documentation, and Docker now consume the same locked resolution. ITAMbox remains intentionally non-packageable.
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
- This alpha establishes the first compatibility baseline. Until the draft release is reviewed and published, evaluate and deploy only from a pinned source revision.
- Alpha upgrades may include breaking migrations. No general version-skipping policy exists yet; review and test the exact target revision with a complete backup and rollback plan.
- The full pytest suite is not safe to run with `pytest-xdist`; use the default serial configuration.
- SQLite is not supported. PostgreSQL 15 or newer is required for development, tests, and production.

[Unreleased]: https://github.com/itambox/itambox-webapp/compare/v1.0.0-beta.1...HEAD
[1.0.0-beta.1]: https://github.com/itambox/itambox-webapp/releases/tag/v1.0.0-beta.1
[1.0.0-alpha.3]: https://github.com/itambox/itambox-webapp/releases/tag/v1.0.0-alpha.3
[1.0.0-alpha.2]: https://github.com/itambox/itambox-webapp/releases/tag/v1.0.0-alpha.2
[1.0.0-alpha.1]: https://github.com/itambox/itambox-webapp/releases/tag/v1.0.0-alpha.1
