# Working Across Tenants

The workspace selector controls the scope in which you are working. It is especially important in an MSP deployment, where the same operator may have access to many customer tenants.

ITAMbox can offer:

- a single tenant workspace;
- a Tenant Group workspace;
- **All Tenants**;
- **Automatic** as a personal default, which lets ITAMbox choose an accessible workspace instead of forcing a stored selection.

A workspace never grants permission by itself. It narrows or aggregates data the signed-in user is already allowed to access.

## Prefer A Single Tenant For Mutating Work

A single-tenant workspace provides the clearest context for creating, editing, assigning, or disposing records. Some aggregate workflows deliberately require you to choose a target tenant before the operation can continue.

For example, an operator can open a bulk scan basket while using **All Tenants**, but the mutation must be bound to a concrete tenant before assets are resolved or submitted.

## Use Aggregate Workspaces For Oversight

Tenant Group and **All Tenants** workspaces are useful for comparing customers, searching broadly, reviewing Jobs, and producing authorized aggregate output. The exact behavior depends on the feature and the operator's permissions.

**Find by Scan** is a special case: it searches across tenants the user can access even when a different tenant is currently selected. Tenant-bound audit and scan-basket workflows do not behave that way.

See [Workspaces](../features/workspaces.md) for the full behavior and [Workspace Scope Reference](../reference/workspace-scope.md) for a concise matrix.
