# Module Maturity

Maturity is declared per **capability**, not per Django app. The authoritative
list, the grade vocabulary, and the activation rules live in the
[Capability Registry](capability-registry.md); this page is the short reader's
guide and the map from the old app-level view of the world to the new one.

## What the grades mean

| Grade | Meaning |
|-------|---------|
| **Stable** | The data model and API are more settled and receive compatibility review. Before the first tagged compatibility baseline, this grade does not guarantee a deprecation cycle across arbitrary source revisions. |
| **Beta** | The module is functional and in active use, but the data model, API shape, or feature set may change between minor releases without a separate deprecation notice. Migrations may be required. Feedback is actively sought. |
| **Experimental** | Being designed in the open. Interfaces may change without notice, and the slice stays inert unless an operator turns it on. |

Beta is an interface-maturity label, not a data-safety or production-support
guarantee. Evaluate the specific deployed revision and its documented
limitations before production use.

## Current grades

### Stable

| Module | App label |
|--------|-----------|
| Assets | `assets` |
| Inventory & Stock | `inventory` |
| Organization | `organization` |
| Compliance (custody + audits) | `compliance` |
| Licenses | `licenses` |
| Software catalogue | `software` |
| Customization (tags, custom fields, dashboards) | `extras` |
| Users & Auth | `users` |
| SaaS Subscriptions | `subscriptions` |
| Purchase Orders & Contracts | `procurement` |
| Curated reports, alerts inbox, role grants, tenant resource grants | across apps |

### Beta and Experimental

| Capability | Area | Grade |
|------------|------|-------|
| Asset Request Procurement Seam | `procurement` | Beta |
| Report Designer | `extras` — reports | Beta |
| Scheduled Reports | `extras` — reports | Beta |
| Alert Rules and Channels | `extras` — alerting | Beta |
| Webhooks and Event Rules | `extras` — automation | Beta |
| SCIM Provisioning | `users` — API | Beta |
| Plugin System | infrastructure | Experimental |

Subscriptions and the purchase-order core graduated to Stable when the registry
landed; what remains Beta in procurement is the Asset Request fulfillment seam,
not purchase orders themselves. Tenant Resource Grants are a separate Stable,
security-critical capability with a dedicated
[threat model](tenant-resource-grant-security.md). See the
[Capability Registry](capability-registry.md) for each entry's activation mode,
source, and declared limitations.

## How it is implemented

Each application registers its own slices from `AppConfig.ready` into the
registry in `itambox/capabilities.py`. The generic list and detail views resolve
the owning capability *per model* and expose it as `capability_notice`, which
drives the banner and the header badge. `core.features.module_maturity` and
`core.features.is_beta_module` remain as registry-backed adapters for one
release; they hold no data of their own.

Navigation badges are driven by `beta=True` on individual `MenuGroup` instances
in `core/navigation/menu.py`.

Run `python manage.py capabilities` to see what a deployment has switched on.

## Promoting a module to Stable

See [Promoting a capability to Stable](capability-registry.md#promoting-a-capability-to-stable).
