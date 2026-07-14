# Changelog

All notable changes to ITAMbox are documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Version numbers follow [Semantic Versioning](https://semver.org/).

---

## [1.0.0-alpha1] - unreleased

### Security
- Encrypt `EmailSettings.smtp_password` at rest using Fernet (derived from `SECRET_KEY`)
- Fix attachment-delete cross-tenant boundary hole; added security test matrix
- Added `StrictTenantPermission` DRF permission enforcing object-level tenant boundary on all API detail endpoints

### Added
- Custom fields generalised across all major models (assets, inventory, organization, licenses, software, subscriptions)
- MSP demo dataset via `manage.py seed_data`
- Full SCIM v2 provisioning endpoint for user synchronisation
- Alert rules, notification channels, and alert log moved to `extras` app
- Report templates, scheduled reports, and generation archives in `extras`
- Webhook and event-rule engine in `extras`
- Export templates and label printing in `extras`
- Journal entries and bookmarks in `extras`
- Software catalogue app (`software`) with installed-software tracking
- Subscription tracking app (`subscriptions`) for SaaS seats
- License seat management app (`licenses`)
- Compliance app with audit sessions and custody receipts
- Procurement app with purchase orders and requisitions

### Changed
- Framework split complete: canonical API implementation lives in `itambox/api/`; `core/api/` shim package removed — all importers now reference `itambox.api` directly
- URL routing is pk-based throughout; slug is a stable natural key for import/export only
- `AssetMaintenance` moved from `compliance` to `assets`
- `AuditSession` / `AssetAudit` moved to `compliance`
- `InstalledSoftware` moved from `assets` to `software`
- `NotificationChannel`, `AlertRule`, `AlertLog` moved from `core` to `extras`
- HTMX pattern standardised: boosted requests receive `content_partial_name`; service views return `204 + HX-Trigger`
- Template chrome deduplicated: shared `stat_card`, `related_table_tab` includes extracted

### Fixed
- String fields store empty string instead of NULL (consistent blank handling across ORM)
- Slug-routing fallbacks removed; pk routing is canonical
- LDAP backend skips gracefully when not configured
- Redis cache backend config corrected for production
- `pyOpenSSL` pinned for SAML compatibility

---

[1.0.0-alpha1]: https://github.com/itambox-itam/itambox-webapp/commits/main
