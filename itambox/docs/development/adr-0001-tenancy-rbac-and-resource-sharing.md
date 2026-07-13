# ADR-0001: Tenancy, RBAC, and cross-tenant resource sharing

- **Status:** Accepted
- **Date:** 2026-07-12
- **Deciders:** Project owner
- **Scope:** `organization`, `users`, `core.auth`, `inventory`, and every app
  holding tenant-scoped operational data

This is the first architecture decision record for ITAMbox. ADRs live in
`docs/development/` and are numbered sequentially; they record decisions that
constrain future work, not implementation detail (that belongs in the module
docs and the code).

## Context

ITAMbox is multi-tenant with an MSP (managed service provider) layer on top:
a tenant with `is_provider=True` may manage other tenants via
`Tenant.managed_by` (depth 1 — chains are rejected, see
`organization/models.py::Tenant.clean`). Permission grants today are spread
over several mechanisms (`RoleAssignment` rows with reach/scope refinements,
`UserGroup` role links, direct permission JSON), and cross-tenant *resource*
use (one tenant consuming another tenant's stock) has no first-class
representation at all — tenant scoping is derived indirectly from catalogue
items or locations, and cross-tenant rows exist in the wild with no recorded
authorization.

This ADR fixes the target semantics for both problems. The migration plan
that implements it is the data-model remediation plan (phases 1–6); each
phase must be independently deployable and reversible, and the RBAC
migration must not be combined with the inventory migration.

## Decision — RBAC

1. **Provider technicians have a Membership only in the provider tenant.**
   Managing a customer never creates a membership row in the customer
   tenant. A user visible in a managed tenant's context is always resolved
   through a grant, not through membership.
2. **Different permissions per customer are represented through differently
   scoped role grants.** One grant per role × scope; "more rights at
   customer A than at customer B" is two grants with different scopes, never
   a per-customer permission override.
3. **UserGroups belong to exactly one tenant/provider.** No shared or
   cross-tenant groups.
4. **Group members reference Memberships in that owning tenant, not global
   Users.** A group can therefore never contain a principal that does not
   belong to the group's tenant.
5. **Groups are flat.** Nested LDAP/Entra groups are flattened during
   synchronization; the data model has no group-in-group relation.
6. **Roles are provider-owned and projected into managed tenants.** A
   provider defines the role once; scope rows project it into managed
   tenants. Customer tenants own their own roles for their own members
   (`Role.tenant`); cross-tenant behaviour comes from scope, never from
   attaching a customer's role to a foreign principal.
7. **Direct technician grants remain available for temporary exceptions.**
   Group grants are the norm; direct membership-level grants stay possible.
8. **Elevated direct grants require a reason and expiration**
   (`RoleGrant.reason`, `RoleGrant.valid_until`).
9. **Permissions are additive; there are no deny rules.** The effective
   permission set is the union of all applicable grants.
10. **Customers cannot approve, restrict, or revoke provider access.** There
    are no customer-side approval fields; the management relationship
    (`managed_by`) is the sole gate, and it is superuser/provider-side
    controlled.
11. **Clearing `managed_by` immediately makes managed-reach grants
    ineffective.** Grants with managed scope are valid only while the target
    tenant is managed by the role owner; resolution must re-check the
    management edge, not cache authorization past it.

### Target grant model (phase 5)

The competing grant paths are replaced by:

```text
GroupMembership(user_group, membership, added_by, source, external_id, added_at)
RoleGrant(membership | user_group [exactly one], role, granted_by, reason, valid_until)
RoleGrantScope(role_grant, scope_type: own|tenant|tenant_group|all_managed,
               tenant?, tenant_group?)
```

Rules: group members must have Memberships in the group's owning tenant; a
customer group uses customer-owned roles; a provider group uses
provider-owned roles; direct grants and group grants are additive; managed
scopes are valid only while the target is managed by the role owner; no
nested groups; no customer approval fields.

Migration order: add tables → backfill group members → convert
`UserGroup.roles` and `RoleAssignment` rows into RoleGrants + scopes → run
old and new resolution in comparison mode and investigate every disagreement
→ switch the auth backend → remove the old M2Ms and `RoleAssignment`.

Phase 5 expansion is implemented with `ITAMBOX_RBAC_RESOLVER_MODE=compare`
as the safe default. The comparison path returns the legacy decision while
logging differences. Operators must run `python manage.py
compare_rbac_resolvers` against production and resolve every reported row
before setting the mode to `new`; the legacy fields and compatibility pointer
remain intentionally present until that real-data gate is satisfied.

Today's `Role.shared_with_managed` mechanism (a provider role assignable by
managed-tenant admins to their own members) is a legitimate pattern under
this design and converts to a RoleGrant on the customer membership with a
provider-owned role and an own-tenant scope; the phase-1 integrity report
already treats it as consistent.

## Decision — cross-tenant resource sharing

1. **TenantGroup membership makes sharing eligible but never automatic.**
   Being in the same `TenantGroup` tree grants nothing by itself.
2. **Every cross-tenant resource use requires an explicit
   `TenantResourceGrant`.** No implicit sharing, ever.
3. **Initial grants target concrete stock pools: item plus location** — the
   stock-pool rows (`ComponentStock`, `AccessoryStock`, `ConsumableStock`),
   not catalogue items and not whole locations.
4. **Grants may target one tenant or a TenantGroup** (exactly one of the
   two).
5. **TenantGroup grants include descendant groups automatically.**
6. **Grantees may view and allocate/consume shared stock.**
7. **Grantees cannot adjust stock, edit the catalogue item, administer the
   grant, or re-share it.**
8. **Resource sharing is non-transitive.** Tenant B receiving A's stock does
   not make B an owner; the resolver never follows grants recursively.
9. **Resource grants do not grant user permissions; RBAC is checked
   separately.** A cross-tenant allocation requires both a covering grant
   for the tenant *and* the acting user's own RBAC permission in their
   active tenant.
10. **No approval, expiry, or contract-scheduling mechanism is needed for
    resource grants.** Revocation is soft-delete (`deleted_at`); existing
    assignments remain historical after revocation.

### Target sharing model (phase 2)

```text
TenantResourceGrant(tenant,                      # resource-owning tenant
                    grantee_tenant? | grantee_tenant_group? [exactly one],
                    resource_type, resource_id,   # allowlisted GFK
                    access_level: view|use,
                    granted_by, reason,
                    created_at/updated_at, deleted_at)
```

Constraints: owner ≠ direct grantee; only allowlisted resource models
(initially the three stock models); the resource must belong to the owner
tenant through its location; at most one active grant per (resource,
grantee); `PositiveBigIntegerField` object ids; indexes for resource,
grantee, group, and active-grant lookups. A generic reference is acceptable
here because this is centralized authorization infrastructure — but only
with a tight allowlist and orphan cleanup.

### Stock ownership (phase 4)

Each stock model gains a non-null `tenant`, derived from and required to
match `location.tenant`. Tenant scoping of stock uses `stock.tenant`, not
the catalogue item's tenant: a global catalogue item stays visible, its
stock does not become global. Assignments gain historical ownership fields
(`source_tenant`, `target_tenant`, `resource_grant`): same-tenant
assignments carry no grant; cross-tenant assignments must reference a grant
that covered the source stock and target tenant at creation time, and the
row survives later revocation as history.

### Authorization flow (phase 3)

One resolver service is used by UI, REST, GraphQL, imports, and background
tasks:

1. Resolve the pool owner from `stock.location.tenant`.
2. If the active tenant owns it → normal RBAC only.
3. Otherwise find an explicit direct grant or a grant targeting an ancestor
   `TenantGroup` of the active tenant's group.
4. Verify the requested access level (`view` < `use`).
5. Independently verify the user's RBAC permission in their active tenant.
6. Return the exact grant used, so the assignment records its provenance.

## Consequences

- Existing cross-tenant rows are **not** automatically legitimized: phase 1
  ships a read-only integrity report that classifies them (valid
  same-tenant / provider-to-managed / within a TenantGroup / ambiguous /
  unrelated-invalid) and produces *proposed* grants for operator review.
- Operational tenant fields become non-null once the report is clean
  (phase 6); redundant derived tenant fields are removed where ownership is
  fully derived.
- Hierarchy models get cycle validation and cycle-safe traversal
  (`TenantGroup`, `Location`, `Region`, `SiteGroup`, `CostCenter`, …).
- Uniqueness constraints are aligned with ownership scope.
- The delivery order is: integrity report + regression tests →
  `TenantResourceGrant` foundation → stock ownership + cross-tenant
  inventory wiring → RBAC consolidation → tenant nullability and relational
  constraints → catalogue/party/polymorphism refactors.
