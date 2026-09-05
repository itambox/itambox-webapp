# Workspaces

A workspace is the active tenant scope used by the web interface. It lets the same operator move between one customer, a group of customers, and authorized aggregate views without changing accounts.

## Available Workspace Types

**Single Tenant** scopes work to one accessible tenant. This is the clearest choice for day-to-day changes.

**Tenant Group** aggregates accessible tenants that belong to the selected group hierarchy.

**All Tenants** represents the operator's accessible tenant set. For a superuser, the aggregate can represent the global dataset rather than a membership-limited set.

**Automatic** is a saved preference that does not force one specific workspace. ITAMbox resolves an accessible workspace at sign-in or when the stored selection is no longer valid.

## Scope Is Not Permission

Selecting a workspace does not grant access to records. A user still needs the relevant permission for the tenant and action. Likewise, having permission for several tenants does not mean every operation can run without a concrete tenant.

This distinction matters for MSP operators: broad visibility is useful for oversight, but writes must still have an unambiguous ownership boundary.

## Lists, Search, And Exports

Tenant-aware lists follow the active workspace and the user's effective access. Filters operate on the already scoped result set. Exports from a list follow the visible/authorized queryset rather than bypassing tenant scope.

Global search also respects access boundaries. [Find by Scan](../usage/scanning.md) intentionally searches across accessible tenants so a technician can identify an unknown device without guessing the customer first.

## Bulk Actions

A bulk action may be available in an aggregate workspace but still require a target tenant. The scan baskets for **Bulk Check-in**, **Bulk Check-out**, and **Bulk Disposal** follow this pattern in **All Tenants** scope.

Do not assume that selecting records from several tenants implies they can be changed together. If a workflow requires one tenant, switch to that tenant or choose the target tenant offered by the workflow.

## Reports, Jobs, And Attachments

Reports and Jobs preserve scope information because generated output can outlive the request that created it. Cross-tenant scheduled reports can require explicit scope approval. Generated Job attachments are re-authorized when downloaded, so losing access can also remove access to previously generated files.

See [Reports, Exports, and Labels](../usage/reports-and-exports.md), [Background Jobs](background-jobs.md), and [Report Scope Approvals](../administration/report-scope-approvals.md).

For a compact behavior matrix, see [Workspace Scope Reference](../reference/workspace-scope.md).
