# Planning Your Deployment

A useful ITAMbox deployment starts with boundaries and shared vocabulary before it starts with individual assets. For an MSP, the most important early decision is how customer data maps to tenants.

## Decide Your Tenant Model

Use one tenant for each independently managed customer or business scope. Tenant Groups can organize related customers, subsidiaries, or service groupings without merging their records.

Do not use Sites, Regions, or Cost Centers as substitutes for tenant isolation. They answer different questions:

| Concept | Use It For |
| --- | --- |
| Tenant | Ownership, access scope, and customer boundary |
| Tenant Group | Grouping tenants for navigation and aggregate work |
| Region | Geographic hierarchy |
| Site | Building, campus, office, or facility |
| Location | Room, rack area, storage area, or other place inside a site |
| Cost Center | Financial ownership or allocation |
| Asset Holder | Person or holder that can receive an item |

See [MSP Tenant Design](../best-practices/msp-tenant-design.md) for examples.

## Establish Shared Vocabulary

Before importing thousands of records, decide how your team will use:

- Asset Types and Manufacturers
- Categories and Asset Roles
- Status Labels
- Sites and Locations
- Cost Centers
- Asset Holders
- Suppliers

Keep status labels operational. A label should help an operator decide what can happen to an asset next, not merely describe a vague business condition.

## Recommended Creation Order

A practical order for a new deployment is:

1. Create tenants and, where useful, Tenant Groups.
2. Create Regions, Sites, and Locations.
3. Create Cost Centers and Asset Holders.
4. Create Manufacturers, Categories, Asset Roles, Asset Types, and Status Labels.
5. Create or import Assets and inventory.
6. Add assignments, warranties, subscriptions, contracts, and procurement records.
7. Configure custom fields, alerts, reports, automation, and integrations after the core data model is stable.

This order is not mandatory. It reduces backtracking because later records can reference the objects they need from the start.

## Choose How To Populate Data

Use the web interface for small datasets and interactive setup. Use [Bulk Import](../integration/bulk_import_guide.md) for structured migrations. Use the [REST and GraphQL APIs](../integration/developer_guide.md) when another system should create or maintain records continuously. Discovery integrations can supplement authoritative data, but they should not replace a deliberate tenant and lifecycle model.

Continue with [Populating Data](populating-data.md) or [Recording the Asset Lifecycle](asset-lifecycle.md).
