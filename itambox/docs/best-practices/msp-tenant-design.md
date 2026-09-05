# MSP Tenant Design

For most MSP deployments, map each customer to its own Tenant. This keeps ownership, access, reports, Jobs, attachments, and automation anchored to a clear customer boundary.

## Use Tenant Groups For Organization

Tenant Groups are useful for customer families, subsidiaries, service portfolios, or regional groupings. They provide navigation and aggregate workspace value without turning separate customers into one tenant.

## Keep Geography Separate

Use Regions, Sites, and Locations for physical geography. A Site called "Customer A" is not a substitute for a Tenant called "Customer A" if data isolation and customer ownership matter.

## Keep Finance Separate

Use Cost Centers for budget/allocation. One customer can have several Cost Centers without creating several security boundaries.

## Design For Daily Operations

Before importing data, test the model with common questions:

- Can a technician tell which customer owns an unknown laptop?
- Can customer-specific staff be restricted to their tenant?
- Can an MSP operator switch between customer and aggregate views predictably?
- Can one customer have several sites and cost centers without duplicating assets?
- Can reports and exports explain their scope clearly?

If those answers require special-case explanations, simplify the tenant model before loading production data.
