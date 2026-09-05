# ITAMbox Documentation

ITAMbox is a self-hosted IT asset management system for organizations and managed service providers. It brings hardware, software, subscriptions, procurement, custody, maintenance, and operational records into one tenant-aware system.

The documentation is organized by what you are trying to do rather than by the source code that implements it.

## Start Here

If you are deploying ITAMbox for the first time, begin with [Planning Your Deployment](getting-started/planning.md). It explains the core objects and a practical order for creating or importing data.

If you already have data in the system, these pages cover the workflows used most often:

- [Workspaces](features/workspaces.md) explains single-tenant, tenant-group, and **All Tenants** views.
- [Assets and Assignments](features/assets-assignments.md) covers the operational asset lifecycle.
- [Search, Filters, and Tables](features/search-tables.md) explains how lists, filters, selection, and exports work together.
- [Scanning Assets](usage/scanning.md) covers **Find by Scan**, scan baskets, and audit scanning.
- [Background Jobs](features/background-jobs.md) explains long-running operations and generated files.
- [Reports, Exports, and Labels](usage/reports-and-exports.md) explains the different output workflows.

## How ITAMbox Organizes Data

A **Tenant** is the main ownership and access boundary for operational data. In an MSP deployment, a tenant normally represents a customer. In an internal deployment, it can represent a business unit or another independently managed scope.

**Tenant Groups** organize related tenants and can be used as a broader workspace. **Sites** and **Locations** describe physical geography. **Cost Centers** describe financial allocation. **Asset Holders** represent people or other holders that can receive equipment.

An **Asset** is a uniquely tracked physical item. Asset Types, Manufacturers, Categories, Roles, Status Labels, warranties, maintenance records, assignments, and disposal records add the context needed to manage that item through its lifecycle.

See [MSP Tenant Design](best-practices/msp-tenant-design.md) for planning guidance and the [Data Model](models/organization/tenant.md) section for field-level definitions.

## Documentation Families

**Getting Started** provides guided setup and first workflows. **Features** explains user-visible behavior. **Configuration** covers deployment-controlled settings. **Customization** covers behavior administrators define inside ITAMbox. **Integrations** documents external contracts such as REST, GraphQL, SCIM, discovery, and webhooks. **Administration** covers permissions, Jobs, recovery, and ongoing operations. **Data Model** is the canonical object and field reference.

Capability maturity is maintained in one place: [Capability Maturity](operations/capability-maturity.md). Individual feature pages mention limitations only when they materially affect how the feature is used.

## Context-Sensitive Help

Many list, detail, and edit pages include a help link. Model views normally open the corresponding Data Model page. Those links are part of the application experience, so the model-reference paths are intentionally kept stable even as the documentation navigation evolves.
