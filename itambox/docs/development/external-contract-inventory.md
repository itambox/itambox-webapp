# External contract inventory (1.x)

This is the **bounded inventory** the
[compatibility policy](compatibility-policy.md) applies to. A promise about a
surface nobody enumerated cannot be checked, so the promise and the enumeration
are published together — and everything outside this document carries no 1.x
compatibility promise at all.

`scripts/check_contract_policy.py` derives each surface below from source with
`ast` and fails when this document and the code disagree. The anchors in the
page source (`<!-- contract-inventory: … -->`) are what the gate reads; they are
invisible in the rendered page and must stay immediately above the table or
block they introduce.

!!! warning "Bounded on purpose"
    This is not a repository-wide inventory. Internal Python names, templates,
    table columns, form fields, management-command flags, and database column
    names are deliberately absent and are free to change. So are the persisted
    values of `core.JobStatusChoices` and
    `organization.TenantResourceGrant.ACCESS_CHOICES`, which reach no REST,
    GraphQL, SCIM, or webhook surface — freezing them would freeze an internal.
    Cross-tenant resource grants remain governed by the review duties in the
    [compatibility policy](compatibility-policy.md).

## API generation

The **unversioned `/api/` prefix is the version 1 convention** for the whole of
1.x. It is a naming convention, not a runtime seam: nothing negotiates,
inspects, or selects a generation, and there is no version segment to change.
The REST tree is mounted once, under `/api/`, with these application
namespaces:

| Mount | Namespace |
|---|---|
| `/api/assets/` | `api:assets_api` |
| `/api/compliance/` | `api:compliance_api` |
| `/api/core/` | `api:core_api` |
| `/api/extras/` | `api:extras_api` |
| `/api/inventory/` | `api:inventory_api` |
| `/api/licenses/` | `api:licenses_api` |
| `/api/organization/` | `api:organization_api` |
| `/api/procurement/` | `api:procurement_api` |
| `/api/software/` | `api:software_api` |
| `/api/subscriptions/` | `api:subscriptions_api` |
| `/api/users/` | `api:users_api` |

The OpenAPI document at `/api/schema/` is the enumerated REST surface; it is
checked into `itambox/schema.yaml` and gated by the
[OpenAPI schema policy](openapi-schema-policy.md), so this page does not repeat
its operations. Operations whose model a capability owns carry
`x-itambox-maturity`; absence of that marker means "no capability claims this
endpoint", which is not a claim of stability.

GraphQL is served at a single endpoint and exposes `assets`, `inventory`,
`licenses`, `software`, and `subscriptions`. Its schema carries its own
identity; it is not versioned by the application release number.

## SCIM routes

Both SCIM mounts are tenant- or provider-scoped and route detail operations on
**integer primary keys**. Identity providers persist these URLs, so the key
shape is a 1.x compatibility surface — see the SCIM entry under *Deferred,
documented, not implemented* in the policy.

<!-- contract-inventory: scim-routes -->

| Route | Mount prefix | Contract class |
|---|---|---|
| `scim:ServiceProviderConfig` | `/api/tenants/<tenant_slug>/scim/v2/` | `beta-opt-in` |
| `scim:Users` | `/api/tenants/<tenant_slug>/scim/v2/` | `beta-opt-in` |
| `scim:Users/<int:pk>` | `/api/tenants/<tenant_slug>/scim/v2/` | `beta-opt-in` |
| `scim:Groups` | `/api/tenants/<tenant_slug>/scim/v2/` | `beta-opt-in` |
| `scim:Groups/<int:pk>` | `/api/tenants/<tenant_slug>/scim/v2/` | `beta-opt-in` |
| `provider_scim:ServiceProviderConfig` | `/api/providers/<provider_slug>/scim/v2/` | `beta-opt-in` |
| `provider_scim:Users` | `/api/providers/<provider_slug>/scim/v2/` | `beta-opt-in` |
| `provider_scim:Users/<int:pk>` | `/api/providers/<provider_slug>/scim/v2/` | `beta-opt-in` |
| `provider_scim:Groups` | `/api/providers/<provider_slug>/scim/v2/` | `beta-opt-in` |
| `provider_scim:Groups/<int:pk>` | `/api/providers/<provider_slug>/scim/v2/` | `beta-opt-in` |

The `v2` in the mount path is the SCIM specification's schema version, not an
ITAMbox API generation.

## Webhook and domain-event envelope

The outbound envelope for a non-Slack, non-Teams target has exactly these
fields. It is signed with `X-Hub-Signature-256` (HMAC-SHA256 over the serialised
body) when the endpoint carries a secret.

<!-- contract-inventory: webhook-envelope -->

| Field | Value |
|---|---|
| `event` | the action, from `core.EventActionChoices` |
| `model` | `app_label.model_name` of the changed object |
| `object_id` | primary key of the changed object |
| `timestamp` | ISO-8601 event time |
| `data` | the event's recorded payload |

**The envelope carries no wire version, no event identifier, and no idempotency
key.** `automation.webhooks` is Beta opt-in and declares its payload schema
unfrozen, so a consumer pins against the release it validated against.
`contract_version` on a capability declaration versions the registry record and
never appears in this payload.

Slack and Teams targets receive those vendors' own message shapes instead; those
are the vendors' contracts, not ITAMbox's.

## Persisted choice values

**Every inventoried enum is marked explicitly open or closed.** *Open* means a
minor release may add a value and a consumer must tolerate an unknown one.
*Closed* means the value set is frozen for the whole of 1.x and a consumer may
switch exhaustively on it. Rule `C-ENUM2` enforces the closed sets against
source.

### `ScheduledReport` frequency — closed

An explicit Stable-graded carve-out inside the Beta enabled
`reporting.scheduled` capability: these nine strings are persisted on schedule
rows that outlive the capability's own churn. The carve-out covers the frequency
values only.

<!-- contract-inventory: enum extras.ScheduledReport.FREQUENCY_CHOICES -->

| Value | Meaning |
|---|---|
| `once` | Run one time |
| `hourly` | Every hour |
| `daily` | Every day |
| `weekly` | Every week |
| `biweekly` | Every two weeks |
| `monthly` | Every month |
| `quarterly` | Every quarter |
| `yearly` | Every year |
| `cron` | Custom cron expression |

### `AlertRule` severity — closed

Stable-graded for the same reason: the Stable `alerting.inbox` persists a
severity on every `AlertLog` row, even though the Beta `alerting.rules` engine
that produces them may change.

<!-- contract-inventory: enum extras.AlertRule.SEVERITY_CHOICES -->

| Value | Meaning |
|---|---|
| `info` | Informational |
| `warning` | Warning |
| `critical` | Critical |

### Subscription status — closed

<!-- contract-inventory: enum subscriptions.SubscriptionStatusChoices -->

| Value | Meaning |
|---|---|
| `active` | Active |
| `expired` | Expired |
| `cancelled` | Cancelled |
| `pending` | Pending |
| `suspended` | Suspended |
| `renewing` | Renewing |
| `trial` | Trial |

### Purchase-order status — closed

The documented state machine of the Stable `procurement.core` capability.

<!-- contract-inventory: enum procurement.PurchaseOrder.STATUS_CHOICES -->

| Value | Meaning |
|---|---|
| `draft` | Draft |
| `approved` | Approved |
| `ordered` | Ordered |
| `partial` | Partially received |
| `received` | Received |
| `cancelled` | Cancelled |

### `AlertRule` type — open

<!-- contract-inventory: enum extras.AlertRule.ALERT_TYPE_CHOICES -->

| Value | Meaning |
|---|---|
| `low_stock` | Low stock |
| `upcoming_eol` | Upcoming end-of-life |
| `license_expiry` | License expiry |
| `renewal_due` | Renewal due |
| `warranty_expiry` | Warranty expiry |
| `audit_overdue` | Audit overdue |

### Report type — open

Persisted on `extras.ReportTemplate`, owned by the Beta opt-in
`reporting.designer` capability.

<!-- contract-inventory: enum extras.ReportTemplate.REPORT_TYPE_CHOICES -->

| Value | Meaning |
|---|---|
| `asset_summary` | Asset inventory summary |
| `license_utilization` | License utilization |
| `subscription_renewals` | Subscription renewals |
| `asset_maintenance` | Asset maintenance and repairs |
| `asset_depreciation` | Asset depreciation summary |
| `software_inventory` | Software catalog and installations |
| `contract_renewals` | Contract renewals and expirations |
| `warranty_expiration` | Warranty expiration |
| `asset_disposal_eol` | Asset disposal and end-of-life |
| `hardware_inventory` | Hardware inventory |
| `custody_compliance` | Custody and EULA sign-off compliance |

### Scheduled-report delivery format — open

<!-- contract-inventory: enum extras.ScheduledReport.FORMAT_CHOICES -->

| Value | Meaning |
|---|---|
| `html` | HTML email |
| `csv` | CSV attachment |
| `pdf` | PDF attachment |
| `xlsx` | Excel attachment |

### Billing cycle — open

<!-- contract-inventory: enum subscriptions.BillingCycleChoices -->

| Value | Meaning |
|---|---|
| `monthly` | Monthly |
| `quarterly` | Quarterly |
| `annual` | Annual |
| `biannual` | Biannual |
| `multi_year` | Multi-year |
| `onetime` | One-time |

### Object-change action — open

Persisted on every changelog row and exposed at `/api/core/object-changes/`.

<!-- contract-inventory: enum core.ObjectChangeActionChoices -->

| Value | Meaning |
|---|---|
| `create` | Created |
| `update` | Updated |
| `delete` | Deleted |
| `checkout` | Checked out |
| `checkin` | Checked in |
| `audit` | Audited |

### Event action — open

The vocabulary of the webhook envelope's `event` field.

<!-- contract-inventory: enum core.EventActionChoices -->

| Value | Meaning |
|---|---|
| `create` | Create |
| `update` | Update |
| `delete` | Delete |

### Report column keys — open

Persisted in `extras.ReportTemplate.included_columns` as a JSON list of these
strings. Published as a block rather than a table because ninety-one rows of
prose would not get reviewed. Only columns matching a template's report type
render.

<!-- contract-inventory: enum extras.ReportTemplateForm.COLUMN_CHOICES -->

```text
asset_tag
name
manufacturer
model
serial_number
status
location
assigned_to
purchase_cost
purchase_date
warranty_months
license_name
software
seats
assigned_seats
available_seats
utilization_rate
subscription_name
provider
billing_cycle
cost
end_date
maintenance_asset
maintenance_type
maintenance_status
maintenance_cost
maintenance_start_date
maintenance_completion_date
maintenance_downtime
salvage_value
depreciation_months
current_value
software_name
version
category
license_type
installed_count
contract_number
contract_name
contract_type
contract_status
contract_supplier
contract_start_date
contract_end_date
contract_renewal_date
contract_days_until_expiry
contract_cost
contract_billing_cycle
contract_auto_renew
contract_covered_assets
contract_sla_response_time
contract_sla_resolution_time
contract_coverage_hours
warranty_asset
warranty_type
warranty_provider
warranty_start_date
warranty_end_date
warranty_days_remaining
warranty_status
warranty_cost
warranty_reference
disposal_asset
disposal_date
disposal_method
disposal_sanitization_method
disposal_sanitization_certificate
disposal_sanitized_by
disposal_recipient
disposal_proceeds
disposal_weee_compliant
disposal_notes
hw_item_type
hw_name
hw_manufacturer
hw_category
hw_part_number
hw_total_stock
hw_available
hw_min_qty
hw_status
custody_asset
custody_holder
custody_status
custody_accepted_date
custody_eula_version
custody_signature_provider
custody_qms_reference
custody_ip_address
custody_created_date
license_count
```

## Permission codenames

Permissions are persisted as plain JSON strings in
**`organization.Role.permissions`** — a list of `app_label.codename` entries with
no foreign key to `auth.Permission`, so a rename silently invalidates a stored
grant. **Tenant Role is the user-facing name** for this model; `TenantRole` was
the historical class name and was dropped in
`organization/migrations/0027_drop_legacy_role_models.py`. No first-party module
imports that symbol — the historical migrations are the only place it names a
model, and the handful of test modules that still spell the word use it as a
local alias or a test-class name for today's `Role`.

Every model additionally gets Django's default `add_`, `change_`, `delete_`, and
`view_` codenames; those follow the model name and are Stable while the model
is. The **custom** codenames — the ones declared in `Meta.permissions` and not
derivable from a model name — are enumerated here.

<!-- contract-inventory: permissions -->

| Codename | Grants |
|---|---|
| `assets.dispose_asset` | Record asset disposal / end-of-life |
| `assets.add_delegated_assetrequest` | Request assets on behalf of others |
| `assets.approve_assetrequest` | Approve asset requests |
| `assets.fulfill_assetrequest` | Fulfill or claim asset requests |
| `core.view_recyclebin` | View the Recycle Bin |
| `core.change_recyclebin` | Restore from the Recycle Bin |
| `core.delete_recyclebin` | Purge from the Recycle Bin |
| `procurement.receive_purchaseorder` | Receive stock against a purchase order |
| `procurement.approve_purchaseorder` | Approve or submit a purchase order |

## UI URLs

**Namespaces are inventoried; individual route names are not.** The repository
declares several hundred UI route names, and freezing all of them would make an
ordinary view rename a breaking change while promising an integrator nothing
they depend on.

What is promised: the namespace set below, the pk-based route shape (URLs route
on primary keys — slugs are stable natural keys for import, export, and
filtering, never for routing), and the named root entry routes.

<!-- contract-inventory: ui-namespaces -->

| Namespace | Mount |
|---|---|
| `assets` | `/assets/` |
| `compliance` | `/compliance/` |
| `extras` | `/extras/` |
| `inventory` | `/inventory/` |
| `licenses` | `/licenses/` |
| `organization` | `/organization/` |
| `procurement` | `/procurement/` |
| `software` | `/software/` |
| `subscriptions` | `/subscriptions/` |
| `users` | `/users/` |

<!-- contract-inventory: entry-routes -->

| Route name | Purpose |
|---|---|
| `dashboard` | Application root |
| `login` | Credential and SSO entry point |
| `search` | Global search |
| `scan_resolve` | Barcode / QR resolution target |
| `health` | Liveness endpoint for operators |
| `graphql` | GraphQL endpoint |

The gate checks these six names still exist; it deliberately does not assert
the reverse, so the root URLconf stays free to grow.

## Contract-bearing settings

Every `ITAMBOX_*` name the application reads is either published here or listed
as out of scope in `scripts/contract_policy.py` with a reason, and rule `C-SET1`
fails on a name that is in neither. "Reads" is derived with `ast`, not searched
for as text: an environment read (`os.environ.get`, `os.getenv`,
`os.environ[…]`), a Django settings read (`getattr(settings, …)`,
`settings.NAME`), and the settings package's own assignment of a name all count;
a comment, a docstring, and a warning message do not. The derivation covers the
whole first-party application tree — so a knob read where it is used, like
`ITAMBOX_FIELD_ENCRYPTION_KEYS` in `core/crypto.py`, is in scope — and excludes
tests, migrations, and generated trees. Out-of-scope names are deployment-local
knobs — database, mail, TLS, CORS, logging, filesystem, and drill-credential
parameters — that change no API, no persisted value, and no integration wire.

Settings without the `ITAMBOX_` prefix are outside this table by construction.
Where a differently named Django settings *attribute* carries a 1.x
compatibility requirement it is named in the policy instead; the only one today
is `REQUISITION_AUTO_APPROVAL_THRESHOLDS`, which is read with `getattr` and
built-in fallbacks and has no environment variable at all.

<!-- contract-inventory: settings -->

| Setting | What depends on it |
|---|---|
| `ITAMBOX_ENV` | Environment selection; fails closed to production |
| `ITAMBOX_BASE_URL` | Public base URL embedded in QR labels and outbound links |
| `ITAMBOX_DEFAULT_CURRENCY` | Fallback ISO 4217 currency for money display |
| `ITAMBOX_PAGINATOR_COUNT_CAP` | Upper bound of the list-page row counter |
| `ITAMBOX_SESSION_COOKIE_AGE` | Session lifetime |
| `ITAMBOX_DOCS_ROOT` | Filesystem path of the compiled documentation |
| `ITAMBOX_CACHE_BACKEND` | Shared-cache selection; rate limiting and SAML replay protection depend on it |
| `ITAMBOX_FIELD_ENCRYPTION_KEYS` | Field-encryption key set; value never published |
| `ITAMBOX_API_TOKEN_PEPPERS` | API-token pepper set; value never published |
| `ITAMBOX_REQUIRE_MFA` | TOTP enforcement for privileged local logins |
| `ITAMBOX_REQUIRE_CUSTODY_SIGNIN` | Signature requirement on custody sign-off |
| `ITAMBOX_ALLOW_GLOBAL_CUSTODY_TEMPLATES` | Whether custody templates may be tenant-less |
| `ITAMBOX_REPORT_DESIGNER_ENABLED` | Activation flag of the Beta opt-in report designer |
| `ITAMBOX_PLUGINS` | Activation list of the Experimental plugin system |
| `ITAMBOX_TENANT_LDAP_CONFIGS` | Per-tenant LDAP configuration |
| `ITAMBOX_TENANT_SAML_CONFIGS` | Per-tenant SAML configuration |
| `ITAMBOX_TENANT_OIDC_CONFIGS` | Per-tenant OIDC configuration |
| `ITAMBOX_TENANT_INTUNE_CONFIGS` | Per-tenant Intune discovery configuration |
| `ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES` | Whether an SSO group claim may auto-create a privileged role |
| `ITAMBOX_CHANGELOG_RETENTION_DAYS` | Retention of object-change rows |
| `ITAMBOX_ALERTLOG_RETENTION_DAYS` | Retention of alert-log rows |
| `ITAMBOX_NOTIFICATION_RETENTION_DAYS` | Retention of notification rows |
| `ITAMBOX_QTASK_FAILED_RETENTION_DAYS` | Retention of failed background-task rows |

The activation flags above are Beta or Experimental *activation surfaces*, which
the policy holds to the Stable standard even while what they switch on is not.

Two of these names are read as Django settings attributes rather than from the
environment. `ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES` is read only as
`getattr(settings, …, True)` in `core/auth/provisioning.py`, and
`ITAMBOX_FIELD_ENCRYPTION_KEYS` is read from the environment *or* from settings
in `core/crypto.py`. Both are named here because the name itself is what a
deployment depends on; how a deployment supplies a value is a configuration
question the [SSO and MFA guide](../usage/sso-and-mfa.md) answers, not a
compatibility promise this document makes.

## Capabilities

One row per registered capability. `Class` is derived from the registry
declaration, so a slice cannot be promoted in prose without being promoted in
code; `Exclusions` **summarises** the limitations the declaration carries, in
this page's words rather than the declaration's.

A summary nothing binds is a claim that quietly stops being true, so this one is
pinned to what it summarises: `scripts/contract_policy.py` records the exact
declared limitation text each cell was written against, and rule `C-CAP5` fails
when a limitation is reworded, added, or removed. A limitation change therefore
cannot leave a stale summary standing here — the gate blocks until the cell has
been re-read against the new text. The authoritative grade, activation source,
and limitation wording live in the [capability registry](capability-registry.md)
and its drift tests; this table adds the contract class, which the registry does
not publish.

<!-- contract-inventory: capabilities -->

| Capability | Class | Activation | Scope | Exclusions |
|---|---|---|---|---|
| `alerting.inbox` | `stable` | always-on | Notification and alert-log records | none |
| `alerting.rules` | `beta-enabled` | enabled | Alert rules and notification channels | Evaluation is daily, not on write; channel delivery failures are logged, not retried |
| `automation.webhooks` | `beta-opt-in` | opt-in | Event rules and webhook endpoints | Payload schema is not frozen; deliveries are fire-and-forget with no delivery log or replay |
| `organization.role_grants` | `stable` | always-on | Role grants; the one security-critical entry | none |
| `platform.plugins` | `experimental` | opt-in | Plugin loading and plugin API | Lifecycle hooks are still being defined, so a plugin that loads today may need changes; plugin code runs in-process with full database access and is not sandboxed |
| `procurement.core` | `stable` | always-on | Purchase orders, order lines, contracts | none |
| `procurement.requisition_seam` | `beta-enabled` | enabled | Requisition fulfillment links | Request-to-order-line reservation is incomplete; auto-approval thresholds are process-wide, not per tenant |
| `reporting.curated` | `stable` | always-on | Curated report catalogue | none |
| `reporting.designer` | `beta-opt-in` | opt-in | Saved report templates | Column, filter, and grouping model is expected to change; saved templates may need rebuilding |
| `reporting.scheduled` | `beta-enabled` | enabled | Scheduled reports and generation archive | Delivery depends on a running worker; archive retention is not per-schedule configurable |
| `subscriptions.tracking` | `stable` | always-on | Providers, subscriptions, assignments | none |
| `users.scim_provisioning` | `beta-opt-in` | opt-in | Tenant- and provider-scoped SCIM mounts | PATCH semantics and filtering are partial; the tenant mount provisions Users and exposes Groups read-only, only provider mounts provision Groups |

## Prospective surfaces

Recorded so a later change inherits a decision rather than inventing one. None
of these exists today and none is scheduled by this document.

| Surface | Promise |
|---|---|
| `subscriptions.Subscription.auto_renewal` | If WP-7 replaces it with `vendor_contract_auto_renews`, the legacy name keeps a read alias through the whole of 1.x and the alias is removed no earlier than 2.0 |

## Related

- [Compatibility, deprecation, and support policy](compatibility-policy.md)
- [Capability Registry](capability-registry.md)
- [OpenAPI Schema Policy](openapi-schema-policy.md)
- [SCIM provisioning](../integration/scim.md)
- [Webhooks and automation](../usage/webhooks-and-automation.md)
