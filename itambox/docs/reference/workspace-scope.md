# Workspace Scope Reference

This page summarizes how the main workspace forms affect common workflows. Permission checks always apply in addition to workspace scope.

| Workflow | Single Tenant | Tenant Group | All Tenants |
| --- | --- | --- | --- |
| Tenant-aware lists | One tenant | Accessible tenants in group | Accessible tenants |
| Global search | Access-limited | Access-limited | Access-limited |
| Find by Scan | Searches accessible tenants | Searches accessible tenants | Searches accessible tenants |
| Audit scanning | Tenant-bound | Requires a concrete audit tenant | Requires a concrete audit tenant |
| Bulk scan baskets | Tenant-bound | Must resolve a concrete target | Target tenant required for aggregate mutation |
| List exports | Current authorized result set | Aggregate where supported/authorized | Aggregate where supported/authorized |
| Scheduled reports | Single-tenant scope | Cross-tenant approval may apply | Cross-tenant approval may apply |
| Jobs/attachments | Bound to persisted scope | Bound to persisted scope | Bound to persisted scope |

**All Tenants** is the current UI label. In explanatory text, "accessible tenants" describes the authorization boundary for non-superusers; it is not a separate workspace label.
