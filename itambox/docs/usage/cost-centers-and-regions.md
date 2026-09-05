# Organization, Cost Centers, And Geography

ITAMbox separates customer/ownership boundaries, physical geography, and financial allocation. Keeping these concepts distinct makes MSP reporting and access behavior easier to understand.

## Tenants And Tenant Groups

A Tenant is the primary ownership and access scope for tenant-aware data. In an MSP, one customer normally maps to one tenant. Tenant Groups organize related tenants and provide aggregate workspace options without merging customer records.

See [Workspaces](../features/workspaces.md) and [MSP Tenant Design](../best-practices/msp-tenant-design.md).

## Regions, Sites, And Locations

Regions form a geographic hierarchy. Sites represent facilities such as offices, campuses, or datacenters. Locations describe places inside a Site such as a room, storage area, or rack zone.

Use this hierarchy to answer **where is it?** Do not use it to represent customer access boundaries.

## Cost Centers

Cost Centers represent financial allocation. Use them to answer **who pays for it?** or **which budget owns it?** without changing the tenant that owns the record.

A customer can have several Cost Centers. A Cost Center can outlive one device generation and continue to provide reporting continuity.

## Asset Holders

Asset Holders represent people or other holder identities used in assignment workflows. An Asset Holder is different from a sign-in User: the person carrying a laptop does not necessarily need an ITAMbox account.

## Planning Example

An MSP managing Contoso might create:

- Tenant: **Contoso Ltd**
- Region: **Germany**
- Site: **Frankfurt Office**
- Locations: **Storage**, **Floor 2**, **Server Room**
- Cost Centers: **Finance**, **Engineering**
- Asset Holders: individual employees

The tenant remains Contoso across all of those records. Geography and cost allocation add context inside the customer boundary.
