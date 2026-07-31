# Capability Registry

A **capability** is one shippable contract: a named slice of the product with a
declared maturity, a declared way of being switched on, and a declared owner.
Every capability is registered from the `AppConfig.ready` hook of the
application that owns it, into the domain-blind registry in
`itambox/capabilities.py`.

This replaces the old `{app_label: grade}` map. That map could only ever say
one thing per Django app, and `extras` alone holds a Stable alert inbox, a
Stable curated report catalogue, and four separately-graded Beta slices. Grading
it wholesale was wrong for something no matter which grade was picked.

## What the grades mean

| Grade | Meaning |
|-------|---------|
| **Stable** | The data model and API are more settled and receive compatibility review. Before the first tagged compatibility baseline, this grade does not guarantee a deprecation cycle across arbitrary source revisions. |
| **Beta** | Functional and in active use, but the data model, API shape, or feature set may change between minor releases without a separate deprecation notice. Migrations may be required. Feedback is actively sought. |
| **Experimental** | Being designed in the open. Interfaces may change without notice, and the slice is inert unless an operator turns it on. |

A grade is an interface-maturity label, not a data-safety or production-support
guarantee. Evaluate the specific deployed revision and its declared limitations
before production use.

## Activation

`activation` says *how* a slice turns on; `activation_source` says *what*
decides it.

| Mode | Meaning |
|------|---------|
| **always-on** | Cannot be switched off. Mandatory for Stable, and the only mode a `security_critical` capability may use. |
| **enabled** | On by default, and observable as off when a deployment has nothing configured. |
| **opt-in** | Inert until an operator does something. A fresh deployment reports it inactive. |

| Source | Meaning |
|--------|---------|
| **always** | Nothing is consulted. |
| **configured** | A settings value an operator supplies. |
| **object-enabled** | Rows the operator created and switched on. |
| **operator-flag** | A deployment-wide switch such as an environment variable. |

Three invariants are enforced at construction, not by review:

* **Stable means always-on.** A Stable capability carries no probe and cannot
  report inactive. If a slice can be switched off, it is not Stable.
* **Non-Stable means probed.** Beta and Experimental entries must say how a
  deployment turns them on, and must declare at least one limitation.
* **Security-critical means undeactivatable.** An entry that guards a boundary
  may not carry a probe at all, so no deployment state can report it off.

Activation is evaluated live and never cached. A probe observes — it reads a
setting or counts rows — and never mutates. A probe that raises fails closed,
and only its exception *type* is ever published.

## Registered capabilities

| Key | Title | Grade | Mode | Source | Owner |
|-----|-------|-------|------|--------|-------|
| `alerting.inbox` | Alerts and Notifications | Stable | always-on | always | area:operations |
| `alerting.rules` | Alert Rules and Channels | Beta | enabled | object-enabled | area:operations |
| `automation.webhooks` | Webhooks and Event Rules | Beta | opt-in | object-enabled | area:operations |
| `organization.role_grants` | Role Grants | Stable | always-on | always | area:auth-rbac |
| `platform.plugins` | Plugin System | Experimental | opt-in | operator-flag | area:plugins |
| `procurement.core` | Purchase Orders and Contracts | Stable | always-on | always | area:procurement |
| `procurement.requisition_seam` | Asset Request Procurement Seam | Beta | enabled | configured | area:procurement |
| `reporting.curated` | Curated Reports | Stable | always-on | always | area:operations |
| `reporting.designer` | Report Designer | Beta | opt-in | operator-flag | area:operations |
| `reporting.scheduled` | Scheduled Reports | Beta | enabled | object-enabled | area:operations |
| `subscriptions.tracking` | SaaS Subscriptions | Stable | always-on | always | area:subscriptions |
| `users.scim_provisioning` | SCIM Provisioning | Beta | opt-in | object-enabled | area:auth-rbac |

`organization.role_grants` is the one `security_critical` entry, which is why it
has no activation probe.

### Ownership

A reference belongs to exactly one capability. References stay dotted strings
and are resolved late, so nothing is imported to register a declaration and a
stale reference is a report rather than a boot failure.

- `alerting.inbox` — `core.Notification`, `extras.AlertLog`
- `alerting.rules` — `extras.AlertRule`, `extras.NotificationChannel`
- `automation.webhooks` — `extras.EventRule`, `extras.WebhookEndpoint`
- `organization.role_grants` — `organization.RoleGrant`
- `platform.plugins` — `itambox.plugins`
- `procurement.core` — `procurement.Contract`, `procurement.PurchaseOrder`, `procurement.PurchaseOrderLine`
- `procurement.requisition_seam` — `procurement.FulfillmentLink`
- `reporting.curated` — `core.reports`
- `reporting.designer` — `extras.ReportTemplate`
- `reporting.scheduled` — `extras.ReportGenerationArchive`, `extras.ScheduledReport`
- `subscriptions.tracking` — `subscriptions.Provider`, `subscriptions.Subscription`, `subscriptions.SubscriptionAssignment`
- `users.scim_provisioning` — `users.api.scim`

## Observation and enforcement

Most capability probes are **observational**: they report the state that already
decides behaviour and do not add another authorization or execution gate:

* `procurement.requisition_seam` reads
  `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS`. The seam and automatic
  approval are inert when the setting is absent. A configured JSON object
  reports only presence booleans, never threshold values. The legacy
  `REQUISITION_AUTO_APPROVAL_THRESHOLDS` name remains a 1.x fallback and emits
  a startup deprecation warning. The seam remains Beta: partial receipts can
  still require manual reconciliation.
* `reporting.scheduled`, `alerting.rules`, and `automation.webhooks` count
  enabled rows. A deployment that already has an enabled event rule keeps
  reporting active; nothing new switches off.
* `platform.plugins` reads `PLUGINS`, which is genuinely empty by default.
* `users.scim_provisioning` observes the tenant-bound API tokens that the SCIM
  authenticators already require. It reports active when at least one token is
  write-enabled, unexpired, owned by an active user, and belongs to a
  non-deleted tenant. The probe exposes only presence booleans and does not gate
  the SCIM endpoints.

`reporting.designer` is deliberately enforced. The operator flag
`ITAMBOX_REPORT_DESIGNER_ENABLED` defaults to `False`; while inactive, designer
navigation is hidden and its eight routes return 404. Set the flag to `True` to
retain or enable access to saved report templates. The Stable curated-report
catalogue is unaffected. Scheduled delivery of existing report templates
continues, but a fresh deployment must enable the designer to author a template
before it can create a scheduled report.

## Where the grade shows up

* **UI** — `itambox/views/generic/capability_notices.py` resolves the owning
  capability per model. The generic list and detail views put the result in
  `capability_notice`; `generic/includes/beta_banner.html` renders the banner and
  `generic/includes/capability_badge.html` the header badge. The notice is
  derived from the declared contract, never from activation state: a Beta slice
  a deployment switched off is still Beta, and a probe that cannot reach the
  database must not silently turn a banner off.
* **OpenAPI** — `itambox/api/openapi.py` adds `x-itambox-maturity` to every
  operation whose model a capability owns. Absence means "no capability claims
  this endpoint", which is a different statement from "stable".
* **Operators** — `python manage.py capabilities` prints class, mode, current
  state, source kind, and whether a value is present. Add `--format json` for a
  machine-readable form. It never prints a value, and a failing probe is
  reported by exception type alone, so a credential cannot reach a terminal.

## Adding a capability

1. Return the declarations from a `_capabilities()` method and hand them to
   `registry.register_all(...)` from the owning application's `AppConfig.ready`.
   Registration is idempotent per identical declaration and a later call can
   finish after a partial failure; do not guard the batch by returning early on
   its first key.
2. Give it an `area:*` label that exists in the repository's label set (the
   architecture policy holds the authoritative list).
3. List the dotted references it owns. A reference may belong to only one
   capability, and `python manage.py capabilities` reports any that no longer
   resolve.
4. Declare at least one limitation for anything non-Stable.
5. Add the row to the table above and the bullet to *Ownership*. The tests in
   `itambox/tests/test_capability_slices.py` compare this document against the
   registry and fail on drift.

## Promoting a capability to Stable

1. Change `maturity` to `STABLE`, drop the probe, set `activation` to
   `ALWAYS_ON` and `activation_source` to `SOURCE_ALWAYS`, and empty
   `limitations` — the entry will refuse to construct otherwise.
2. Remove `beta=True` from the corresponding `MenuGroup` in
   `core/navigation/menu.py`.
3. Update this document.

## Deprecated adapters

`core.features.module_maturity` and `core.features.is_beta_module` still answer
at the app level, backed by the registry rather than by a literal map. An
application is graded only when a single capability owns *every* model in it;
anything finer grades `stable` there and resolves per model through
`capability_notice`. Both are scheduled for removal one release after the
registry lands. Use `registry.owner_of("<app_label>.<Model>")` instead.

## Related

- [Module Maturity](module-maturity.md)
- [ADR 0001 — Architecture Boundaries and Layering](adr-0001-architecture-boundaries-and-layering.md)
- [OpenAPI Schema Policy](openapi-schema-policy.md)
