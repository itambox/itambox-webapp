# Capability Maturity

ITAMbox declares maturity **per capability**, not per module. A capability is a
named slice of the product with a declared maturity, a declared way of being
switched on, and declared known limitations. This page explains what the grades
mean for a deployment; it is the public contract behind the maturity banners
and badges shown in the application UI.

## Grades

| Grade | Meaning for an operator | Upgrade/compatibility consequence |
|---|---|---|
| **Stable** | Part of the supported product contract. Always on; there is no switch to disable it. | Covered by the compatibility promise of the current release line. Breaking changes are handled through the normal upgrade path and changelog. |
| **Beta** | Functional and in active use, but the data model, API shape, configuration, or feature set may change between minor releases. May be enabled by default, opt-in through configuration, or opt-in through an application object — see the table below. | Check the capability's activation and limitations before depending on it. A future upgrade may require rebuilding saved objects (for example report templates) or adjusting configuration. |
| **Experimental** | Not covered by the 1.0 compatibility promise. Interfaces may change in any release. Always opt-in. | Pin both ITAMbox and the capability together (for example the plugin and its host revision) and test each combination in a non-production environment before deployment. |

Security-critical capabilities (for example authorization and tenant resource
grants) are always Stable-grade **and cannot be deactivated**: no configuration
can switch the authorization path off.

## Activation modes

| Mode | How it is switched on | Example in this release |
|---|---|---|
| Enabled by default | Part of every deployment; no action needed. | Curated Reports, Alerts and Notifications, Purchase Orders and Contracts, SaaS Subscriptions, Role Grants, Tenant Resource Grants |
| Opt-in through configuration | An operator sets an environment variable / setting; takes effect on restart. | Report Designer (`ITAMBOX_FEATURE_REPORT_DESIGNER`), Plugin System (`ITAMBOX_PLUGINS`), Asset Request auto-approval (`ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS`), Scheduled Reports (flag plus an active schedule row) |
| Opt-in through an application object | An administrator creates or enables a record in the application. | Webhooks and Event Rules (an enabled `EventRule`), Alert Rules and Channels (an active `AlertRule`), SCIM Provisioning (a tenant-scoped API token with write access) |

A capability that is not switched on is reported as **inactive** — it keeps its
declared grade, but contributes none of its surfaces until it is activated.

## Capabilities in this release

| Capability | Grade | Activation | Operator documentation |
|---|---|---|---|
| Curated Reports | Stable | Enabled by default | [Reports & Exports](../usage/reports-and-exports.md) |
| Report Designer | Beta | Opt-in: `ITAMBOX_FEATURE_REPORT_DESIGNER` | [Reports & Exports](../usage/reports-and-exports.md) |
| Scheduled Reports | Beta | Opt-in: flag plus an active schedule row | [Reports & Exports](../usage/reports-and-exports.md) |
| Alerts and Notifications | Stable | Enabled by default | [Alerts & Notifications](../usage/alerts-and-notifications.md) |
| Alert Rules and Channels | Beta | Opt-in: an active `AlertRule` | [Alerts & Notifications](../usage/alerts-and-notifications.md) |
| Webhooks and Event Rules | Beta | Opt-in: an enabled `EventRule` | [Webhooks & Automation](../usage/webhooks-and-automation.md) |
| Role Grants | Stable | Enabled by default (security-critical) | — |
| Tenant Resource Grants | Stable | Enabled by default (security-critical) | [Resource Grant Expiry](resource-grant-expiry.md) |
| Purchase Orders and Contracts | Stable | Enabled by default | [Contracts & Purchase Orders](../usage/contracts-and-purchase-orders.md) |
| Asset Request Procurement Seam | Beta | Opt-in: `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS` | [Asset Requests](../usage/asset-requests-and-reservations.md) |
| SaaS Subscriptions | Stable | Enabled by default | — |
| SCIM Provisioning | Beta | Opt-in: tenant-scoped API token with write access | [SCIM Provisioning](../integration/scim.md) |
| Plugin System | Experimental | Opt-in: `ITAMBOX_PLUGINS` | [Plugin guide](../plugins/getting_started.md) |

## Verifying the declared state

The `capabilities` management command reports every capability, its declared
grade and activation mode, its **current** state, and what kind of source
decides it — without printing any configured value:

```bash
python manage.py capabilities
python manage.py capabilities --format json
```

```text
CAPABILITY                          CLASS    MODE       STATE     SOURCE          VALUE
reporting.designer                  beta     opt-in     inactive  operator-flag   absent
automation.webhooks                 beta     opt-in     inactive  object-enabled  absent
organization.resource_grants *      stable   always-on  active    always          present
```

(* marks security-critical capabilities, which cannot be deactivated.)

A capability whose probe cannot run (for example because the database is
unreachable) is reported as `error` with the exception *type* only — never the
exception message, so a failing probe cannot leak a credential into a terminal
or log shipper.

## What this page does not cover

Module-level or repository-internal maturity definitions, promotion processes,
test matrices, and gate identifiers are maintainer material and are not part
of the public operator contract.