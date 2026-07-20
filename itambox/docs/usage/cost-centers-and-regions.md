# Cost Centers & Regions

ITAMbox provides several organisational grouping mechanisms to help you
structure your asset inventory, track financial ownership, and navigate large
multi-tenant deployments. This guide covers Cost Centers, Regions, Site Groups,
and Tenant Groups — when to use each and how they work together.

---

## Cost Centers

A **Cost Center** represents a financial tracking code — a department, business
unit, or budget line — used to scope and track financial ownership for assets,
licenses, subscriptions, and contracts. Cost Centers are organised
hierarchically: a top-level instance is a primary cost centre, and child
instances represent sub-departments within it.

### Creating a Cost Center

Navigate to **Organization → Cost Centers** and click **Add**. Each cost centre
has the following attributes:

| Field | Description |
|---|---|
| **Name** | Display name (e.g. `Research & Development`). |
| **Code** | Short identifier code (e.g. `CC-RD-100`). Must be unique per active tenant. |
| **Parent** | Optional parent link to another Cost Center, enabling hierarchical nesting. |
| **Tenant** | Tenant this cost centre is scoped to. When set, the cost centre is only visible within that tenant. Leave blank for a shared/global cost centre. |
| **Description** | Optional details explaining the cost centre's scope. |
| **Is Active** | Inactive cost centres are hidden from selection lists but preserved for audit history. |

Cost Centres support custom fields via `CustomFieldDataMixin` — you can attach
tenant-specific attributes using the standard [Custom Fields](custom-fields.md)
mechanism.

### Hierarchical Nesting

The `parent` field enables multi-level hierarchies:

```
Corporate IT (CC-IT-001)
  ├── Infrastructure (CC-IT-010)
  │   ├── Network Team (CC-IT-011)
  │   └── Servers & Storage (CC-IT-012)
  └── End-User Services (CC-IT-020)
```

The model exposes two computed properties:

| Property | Description |
|---|---|
| **depth** | Zero-based depth in the hierarchy (0 = top-level, 1 = direct child, etc.). |
| **full_path** | Slash-joined name path from root to this node (e.g. `Corporate IT / Infrastructure / Network Team`). |

> [!IMPORTANT]
> The model enforces cycle detection: you cannot set a parent that would create
> a circular reference (e.g. making an ancestor point to one of its descendants).

### Assigning Cost Centers to Objects

Cost Centres appear as a selectable foreign key on assets, licenses,
subscriptions, and contracts. The exact field name varies by model, but the
pattern is consistent:

- **Assets** — assign a cost centre during creation or edit to track which
  department owns the hardware.
- **Licenses** — scope software entitlements to a specific budget line.
- **Subscriptions** — link SaaS subscriptions to the department that pays for
  them.
- **Contracts** — associate procurement contracts with the responsible cost
  centre.

When viewing lists, you can filter by cost centre and add the cost centre column
to list views via the column selector.

### Tenant Scoping

Cost Centres use `TenantScopingSoftDeleteManager`, which means:

- A cost centre with a `tenant` set is only visible to members of that tenant
  and to provider-staff users with managed reach into it.
- A cost centre with `tenant=None` is **global** — visible in every tenant's
  context. Use this for organisation-wide codes like "Corporate Overhead" that
  all business units reference.

> [!WARNING]
> Global cost centres (tenant=None) are visible across all tenants. Do not use
> them to store tenant-specific financial details that should remain isolated.

For full model reference, see
[docs/models/organization/costcenter.md](../models/organization/costcenter.md).

---

## Regions

A **Region** represents a geographic grouping of sites. Regions are
hierarchical — a region can have a parent region to represent sub-regions
(e.g. `US-East` as a child of `United States`).

### Creating a Region

Navigate to **Organization → Regions** and click **Add**:

| Field | Description |
|---|---|
| **Name** | Unique regional name (e.g. `North America`, `EMEA`). |
| **Parent** | Optional parent region for nested grouping (e.g. `Europe` → `Western Europe`). |
| **Description** | Optional functional details about the regional boundary. |

Regions are **global reference data** (`changelog_global = True`) — they are
not tenant-scoped. A region you create is available to all tenants, and
changelog entries for region mutations are attributed to `tenant=None`
(global audit trail).

### Assigning Sites to Regions

Every [Site](../models/organization/site.md) has a `region` foreign key. When
creating or editing a site, select the appropriate region from the dropdown.
This enables:

- **Region-based filtering** — filter asset lists, licence assignments, and
  inventory reports by the region of the site the asset belongs to.
- **Geographic reporting** — group dashboards and exports by region to show
  hardware distribution across continents, countries, or states.
- **Map visualisation** — when site latitude/longitude is populated, regions
  provide the grouping layer for spatial dashboards.

### Example Hierarchy

```
Global
 ├── North America
 │   ├── US-East
 │   └── US-West
 ├── Europe (EMEA)
 │   ├── Western Europe
 │   └── Central Europe
 └── Asia-Pacific (APAC)
```

For full model reference, see
[docs/models/organization/region.md](../models/organization/region.md).

---

## Site Groups

A **Site Group** provides a way to logically cluster sites under a flat or
hierarchical category that is **separate from geography**. Where Regions answer
"where is this site?", Site Groups answer "what kind of facility is this?"

### Creating a Site Group

Navigate to **Organization → Site Groups** and click **Add**:

| Field | Description |
|---|---|
| **Name** | Unique group name (e.g. `Data Centres`, `Branch Offices`). |
| **Parent** | Optional hierarchical parent site group. |
| **Description** | Optional descriptive comments. |

Like Regions, Site Groups are global reference data — they are not
tenant-scoped.

### Assigning Sites to Site Groups

Every Site has a `group` foreign key. When creating or editing a site, select
the appropriate group. A site can belong to **both** a Region and a Site Group
simultaneously — they are independent dimensions:

| Site | Region | Site Group |
|---|---|---|
| HQ Building A | North America → US-East | Corporate Offices |
| Ashburn DC-1 | North America → US-East | Data Centres |
| London Office | EMEA → Western Europe | Branch Offices |
| Frankfurt DC | EMEA → Central Europe | Data Centres |

### Use Cases

- **Facility-type reporting** — total assets in all data centres vs. all branch
  offices, regardless of geography.
- **Operational filtering** — show only data centre assets when planning
  hardware refreshes, or only branch office assets when planning WAN upgrades.
- **Compliance scoping** — apply different audit schedules to data centres
  (quarterly) vs. branch offices (annual).

For full model reference, see
[docs/models/organization/sitegroup.md](../models/organization/sitegroup.md).

---

## Tenant Groups

A **Tenant Group** organises tenants into logical hierarchies for reporting,
navigation, and access-control grouping. For example, you might group tenants by
organisational structure: `Subsidiaries` → `Regional Branches`, or by
department: `Engineering` → `DevOps`.

### Creating a Tenant Group

Navigate to **Organization → Tenant Groups** and click **Add**:

| Field | Description |
|---|---|
| **Name** | Unique group name (e.g. `Engineering Departments`). |
| **Parent** | Hierarchical parent group. |
| **Description** | Optional notes. |

Unlike Regions and Site Groups, Tenant Groups use
`TenantScopingSoftDeleteManager` — a user sees only the groups containing a
tenant they are a member of, plus those groups' ancestors.

### Assigning Tenants to Tenant Groups

Every [Tenant](../models/organization/tenant.md) has a `group` foreign key.
Assign a tenant to a group when creating or editing the tenant.

### Tenant Groups in the Navigation Picker

The tenant selector in the top navigation bar supports switching between:

- **Single Tenant** — the default workspace view, scoped to one tenant.
- **Tenant Group** — aggregates all tenants within a group (and its descendant
  subgroups). Lists, filters, and exports combine data across the group's
  subtree.
- **All Accessible Tenants** — combines every tenant you have a membership in
  (plus managed reach).

This scoping determines which data the list views, dashboard widgets, and
report generators include.

### Tenant Groups and RBAC

Tenant Groups are also used in **role grant scopes**. When a provider-staff user
is granted a role with a `SCOPE_TENANT_GROUP` scope, the grant covers all
tenants in that group's descendant subtree. This lets you grant access to entire
organisational branches in a single operation.

For full model reference, see
[docs/models/organization/tenantgroup.md](../models/organization/tenantgroup.md).

---

## Choosing the Right Grouping

| If you need to… | Use |
|---|---|
| Track which department owns/budgeted for a device | **Cost Center** |
| Show all hardware in a geographic region | **Region** |
| Filter by facility type (office vs. data centre) | **Site Group** |
| Aggregate reporting across multiple tenants | **Tenant Group** |
| Assign a device to a physical building | **Site** (with Region + Site Group) |
| Assign a device to a specific room within a building | **Location** (child of Site) |

---

## Related Documentation

- [Sites](../models/organization/site.md) — physical facilities
- [Locations](../models/organization/location.md) — rooms/areas within sites
- [Tenants](../models/organization/tenant.md) — data-isolation boundaries
- [Custom Fields](custom-fields.md) — extend these models with tenant-specific attributes
- [Reports & Exports](reports-and-exports.md) — use these groupings in reports
