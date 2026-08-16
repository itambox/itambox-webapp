# Tenant Resource Grant Security Boundary

`organization.resource_grants` is a Stable, always-on, security-critical capability. It permits one tenant to expose a specific stock pool to another tenant without weakening ITAMbox's default tenant isolation. This document is the frozen threat model and authorization contract for that boundary.

## Security objective

A stock pool owned by tenant A is visible or usable while tenant B is active only when all of the following independently hold:

1. the resource model is explicitly approved;
2. the pool has a live, provable owner;
3. one live grant covers the exact pool and active tenant;
4. the grant's access level satisfies the requested operation;
5. the actor has the required RBAC permission in the active tenant, or an actorless task supplies an explicit context-bound system authorization;
6. the requested surface uses the canonical resolver or its parity-preserving batch adapter.

Failure of any condition denies access. A grant authorizes a tenant, never a user.

## Actors and assets

### Actors

- **Owner-tenant actor** — accesses stock owned by the active tenant and must pass ordinary RBAC.
- **Direct grantee actor** — acts in the tenant named by a direct grant and must independently pass RBAC there.
- **Ancestor-group grantee actor** — acts in a tenant whose live group ancestry is covered by a group grant and must independently pass RBAC in the active tenant.
- **Unrelated, sibling, or reverse-direction actor** — receives no access from another tenant's grant.
- **Superuser** — bypasses ordinary RBAC through Django's permission contract, but does not bypass the requirement for an explicit cross-tenant resource grant.
- **Actorless background task** — a bare `user=None` is denied. Legitimate actorless work must enter a live tenant-bound `TaskContext` and pass an opaque `SystemAuthorizationContext` issued by `TaskContext.authorize_system()` for the exact tenant, permission, operation, non-empty reason, and current request ID. Direct construction is disabled; leaving the task or switching its ambient tenant invalidates the authorization.

Assignment rows are service-protected as well as resolver-protected. Create,
update, restore, and delete operations on
`AccessoryAssignment`, `ConsumableAssignment`, and `ComponentAllocation` rows
require the ephemeral, instance-bound write capability opened by canonical
inventory services after actor/system authorization. Direct ORM factories deny,
and bare instance `save()`, `restore()`, and `delete()` calls deny before stock
changes, including same-tenant and structurally grant-valid cross-tenant writes.
Provenance is derived from current persisted source and recipient FK rows;
caller-held relation caches never define `source_tenant`, `target_tenant`, or the
covering grant.
Actorless assignments persist the exact authorized system operation and reason.
Those values are immutable historical evidence; human-authorized rows leave them
empty, and neither later task context nor grant revocation rewrites them.

Returns are one locked transaction: load and authorize the live assignment with
`select_for_update()`, lock its concrete source pool, restore stock, and delete
the assignment. Concurrent returns therefore restore stock exactly once.
Stock-table actions use one batched resolver projection per rendered table,
never scalar authorization per candidate row.

### Protected assets

- Accessory, component, and consumable stock quantities and locations.
- Tenant and tenant-group isolation.
- Checkout/allocation provenance, including the exact authorizing grant.
- Grant and RBAC metadata, which must not be exposed through generic export or error detail.
- Assignment and recipient records created from a granted checkout.

## Trust boundary and approved resources

The owner is derived only from `stock.location.tenant`; catalogue ownership, caller input, and grant ownership do not redefine it. Ownerless or soft-deleted-owner pools are denied.

The approved resource allowlist is closed:

- `inventory.AccessoryStock`
- `inventory.ComponentStock`
- `inventory.ConsumableStock`

Adding a model is a security-contract change and requires an updated threat model, adversarial matrix, and independent review. Persisted rows outside this allowlist remain denied even if model validation was bypassed by bulk update, migration, or direct SQL.

## Canonical authorization path

`organization.access.resolve_stock_access()` is the decision primitive and `organization.services.resolve_stock_access` is its compatibility re-export. `resolved_shared_stock_ids()` is the only sanctioned queryset projection: it batches live owner, grant, ancestry, and RBAC evidence to avoid per-stock queries, but every returned stock object still passes `resolve_stock_access()`.

`TenantResourceGrant.objects` is deliberately unscoped because a grant connects
containers rather than belonging to one ambient tenant. Generic container-aware
surfaces must therefore use `visible_to_containers()` or fail closed; an ambient
tenant manager must never silently redefine grant visibility.

UI lists, checkout form references, REST list/detail permission checks, service mutations, imports, GraphQL, exports, and background tasks must use one of those paths. Production code must not authorize directly with `shared_resource_ids()` or a raw `TenantResourceGrant` query. An AST convention test freezes this rule.

The resolver returns a machine-readable reason and the exact authorizing grant. Direct tenant grants take deterministic precedence over ancestor-group grants. Grants are non-transitive and non-recursive: receiving access never makes the recipient an owner and never permits re-sharing.

Checkout and kit services also bind every destination holder, location, or asset to the live active tenant before grant evaluation or mutation. A grant that covers two sibling tenants does not permit an actor in one sibling to allocate stock into the other. Mixed actorless kits require a separate issued authorization for each assignment permission.

## Permission map

| Operation | Required grant level | Independent RBAC permission |
|---|---|---|
| Stock list/detail read | `view` (or stronger `use`) | `inventory.view_<stock model>` |
| Checkout source reference | `use` | `inventory.add_<assignment/allocation model>` |
| Checkout/allocation mutation | `use` | `inventory.add_<assignment/allocation model>` |
| Same-tenant stock operation | no cross-tenant grant | the operation's ordinary model permission |

The surrounding UI may require additional permissions, such as changing the catalogue item that launches a checkout. Those checks are additive and do not replace the resolver permission.

## Assumptions

- Django's tenant and permission context variables are isolated per request or task and restored after nested contexts.
- Soft-delete timestamps, membership activity, tenant-group ancestry, and role grants are authoritative inputs.
- Model `clean()` reduces malformed data but is not trusted as the only security boundary.
- Database transactions preserve stock mutation and provenance atomically.
- Generic views and exports may encounter deliberately unscoped authorization models and therefore must fail closed or use an explicitly sanctioned visibility helper.

## Failure modes and mitigations

| Failure mode | Mitigation |
|---|---|
| Missing or soft-deleted active tenant | deny with `no-active-tenant` before grant-type evaluation |
| Unsupported or malformed resource type | closed allowlist; deny with `unsupported-resource-type` |
| Ownerless or deleted owner | live owner lookup; deny with `owner-unresolvable` |
| Missing, revoked, deleted, moved, or unrelated grant | live exact-resource lookup; deny with `no-grant` |
| View grant used for a use operation | ordered access-level check; deny with `insufficient-access-level` |
| Unknown requested or persisted access level | closed vocabulary; deny with `invalid-access-level` rather than raising |
| Actor lacks operation permission | independent active-tenant RBAC; deny with `rbac-denied` |
| Actorless caller relies on `user=None` | deny unless an issuer-, tenant-, operation-, permission-, reason-, and request-ID-bound `SystemAuthorizationContext` is present |
| Checkout destination belongs to another tenant | reject before stock disclosure or mutation, even if a group grant covers both tenants |
| Group topology contains a deleted or broken ancestor | live-only ancestry walk; no coverage through the broken chain |
| Direct and group grants both cover the pool | direct grant wins and its exact primary key is returned |
| Queryset/form/API surface bypasses the resolver | canonical batch adapter plus AST convention gate |
| Generic export reaches grant rows directly | endpoint-level fail-closed test; no grant metadata export |
| Error handling leaks another tenant | stable denial reasons without foreign tenant names, counts, or object data |

Future grant expiry or revocation workflows must preserve durable attribution of
the actor or trusted-system operation and reason that changed authorization state;
adding lifecycle automation may not erase the original assignment provenance.

## Expiry, audit, and rollback contract

`TenantResourceGrant.valid_until` is nullable configuration. `NULL` means
perpetual; a non-null value is due when it is less than or equal to the fixed
cutoff captured by the hourly owner-tenant sweep. It never changes the meaning
of `is_active`, the default manager, the resolver, the batch projection, the
coverage check, or either active-grant uniqueness constraint. Liveness changes
only when the ordinary soft-deletion transition commits. The expiry index is
static (`deleted_at IS NULL AND valid_until IS NOT NULL`) and contains no clock
expression.

The scheduled expiry action is the explicit N5 exception for an operator-
configured system action. Each per-tenant task enters a live `TaskContext` with
`user_id=None`, installs a synthetic request ID before target resolution, and
uses an exact `SystemAuthorizationContext` for the delete permission,
`organization.resource_grant.expire`, and the non-empty scheduled-revocation
reason. A bare `user=None`, a tenantless context, a failed context entry, or a
changed ambient tenant cannot authorize the mutation. Every expiry delete has
one owner-tenant `ObjectChange` with `user=None` and one immutable evidence row.

The coordinator creates one generation-bound run per owner tenant and UTC
hour. Claim, completion, retry, enqueue-failure, and stale-lease repair are
compare-and-set transitions. Runs are tenant-isolated; malformed rows remain
live and receive a stable redacted remediation outcome. The read-only audit API
allows owner, direct-grantee, and live ancestor-group visibility while keeping
filtering after the same visibility boundary. Token scope pins the result to
its tenant. An unbound platform superuser is global, while a tenant- or
group-bound superuser remains container-limited. Hidden identifiers and filters
return neutral 404/empty results.

Expiry evidence is bound to the current revocation cycle. If its linked
`ObjectChange` is pruned, the audit row remains integrity-valid and is shown as
`unknown` with the run/deadline evidence retained. Manual, deleted-user, stale,
and actorless history is classified conservatively; no deleted human is
replaced with a fabricated system user. Lifecycle events remain owner-tenant
events and never recursively disclose resource names, reasons, or grantee-side
details.

Rollback is one named row at a time. The operator must correct or clear the
deadline before a validated model save restores `deleted_at=None`; bulk
restore, `QuerySet.update()`, direct SQL, and a writable REST restore action are
unsupported. The prior expiry change, run, and evidence remain untouched, and
the restored grant still requires independent RBAC. Reversing the migration
removes the field and operational tables only; it never restores a revoked
grant.

## Non-goals

- Grants do not share catalogue objects, tenants, memberships, role data, arbitrary locations, assignment history, or generic related objects.
- Grants do not create tenant management authority or RBAC permissions.
- Grants do not permit mutation of the owner's pool through ordinary stock CRUD endpoints; use occurs through sanctioned checkout/allocation services.
- Grants do not chain, recurse, or confer a right to create downstream grants.
- The generic export system is not a grant-aware data-transfer channel.

## Mandatory verification

Changes to the model, resolver, approved-resource allowlist, projection helper, or any exposed stock surface require the named boundary selector:

Run the Django selector from the repository root:

```bash
PYTHONPATH=itambox uv run --locked --group dev python -m pytest -q -p no:cacheprovider \
  itambox/assets/tests/test_existing.py \
  itambox/assets/tests/test_bulk_actions.py \
  itambox/assets/tests/test_bulk_scan.py \
  itambox/assets/tests/test_ean.py \
  itambox/assets/tests/test_graphql.py \
  itambox/assets/tests/test_issue260_asset_detail_responsive.py \
  itambox/compliance/tests/test_custody_rbac.py \
  itambox/core/tests/test_issue185_alert_delivery.py \
  itambox/itambox/tests/test_django_tables2_compat.py \
  itambox/itambox/tests/test_generic_view_components.py \
  itambox/itambox/tests/test_rest_api_options.py \
  itambox/itambox/tests/test_api_namespace_roots.py \
  itambox/itambox/tests/test_api_root.py \
  itambox/assets/tests/test_tables_category.py \
  itambox/assets/tests/test_requests.py \
  itambox/assets/tests/test_scanning.py \
  itambox/core/tests/test_alert_views.py \
  itambox/core/tests/test_import_boundaries.py \
  itambox/core/tests/test_csp_nonce_context.py \
  itambox/core/tests/test_csv_import_error_contracts.py \
  itambox/core/tests/test_e2e_workflow.py \
  itambox/core/tests/test_delivery_contracts.py \
  itambox/core/tests/test_events.py \
  itambox/core/tests/test_global_label_template_permissions.py \
  itambox/core/tests/test_html_sanitizer.py \
  itambox/core/tests/test_html_styles.py \
  itambox/core/tests/test_import_snipeit.py \
  itambox/core/tests/test_snipeit_stages_catalog.py \
  itambox/core/tests/test_snipeit_stages_contracts.py \
  itambox/core/tests/test_snipeit_stages_hardware.py \
  itambox/core/tests/test_snipeit_stages_inventory.py \
  itambox/core/tests/test_snipeit_stages_licenses.py \
  itambox/core/tests/test_snipeit_stages_orchestration.py \
  itambox/core/tests/test_snipeit_stages_organization.py \
  itambox/core/tests/test_integrity_report.py \
  itambox/core/tests/test_intune_sync.py \
  itambox/core/tests/test_integration_errors.py \
  itambox/core/tests/test_intune_task_contract.py \
  itambox/core/tests/test_label_renderer_html.py \
  itambox/core/tests/test_ldap_oidc_error_contracts.py \
  itambox/core/tests/test_mitigations_phase2.py \
  itambox/core/tests/test_nav_group_gate_unify.py \
  itambox/core/tests/test_objectchange_responsive.py \
  itambox/core/tests/test_oidc.py \
  itambox/core/tests/test_plugins.py \
  itambox/core/tests/test_report_characterization.py \
  itambox/core/tests/test_report_chart_html.py \
  itambox/core/tests/test_report_custom_html_removal.py \
  itambox/core/tests/test_report_designer_issue181.py \
  itambox/core/tests/test_report_designer_migration.py \
  itambox/core/tests/test_report_asset_disposal_eol.py \
  itambox/core/tests/test_report_contract_renewals.py \
  itambox/core/tests/test_report_custody_compliance.py \
  itambox/core/tests/test_report_currency.py \
  itambox/core/tests/test_report_export_formats.py \
  itambox/core/tests/test_report_hardware_inventory.py \
  itambox/core/tests/test_report_provider_registry.py \
  itambox/core/tests/test_report_tenant_scoping.py \
  itambox/core/tests/test_report_warranty_expiration.py \
  itambox/core/tests/test_phase3_task_audit.py \
  itambox/core/tests/test_reporting.py \
  itambox/core/tests/test_report_template_form_xss.py \
  itambox/compliance/tests/test_custody_permissions.py \
  itambox/compliance/tests/test_custody_rbac.py \
  itambox/compliance/tests/test_views.py \
  itambox/extras/tests/test_dashboard_all_accessible.py \
  itambox/extras/tests/test_dashboard_api.py \
  itambox/extras/tests/test_dashboard_widgets.py \
  itambox/extras/tests/test_eventrule_withdrawn_guards.py \
  itambox/extras/tests/test_eventrule_withdrawn_report.py \
  itambox/extras/tests/test_existing.py \
  itambox/extras/tests/test_scheduledreport_scope_approval.py \
  itambox/extras/tests/test_webhook_delivery_api.py \
  itambox/extras/tests/test_webhook_delivery_state_machine.py \
  itambox/extras/tests/test_webhook_delivery_ui.py \
  itambox/inventory/tests/test_accessories.py \
  itambox/inventory/tests/test_api_over_allocation.py \
  itambox/inventory/tests/test_assignment_system_authorization_provenance.py \
  itambox/inventory/tests/test_bulk_checkout.py \
  itambox/inventory/tests/test_checkout_permissions.py \
  itambox/inventory/tests/test_components.py \
  itambox/inventory/tests/test_concurrent_checkin.py \
  itambox/inventory/tests/test_consumables.py \
  itambox/inventory/tests/test_cross_tenant_checkout.py \
  itambox/inventory/tests/test_direct_assignment_writes.py \
  itambox/inventory/tests/test_kits.py \
  itambox/inventory/tests/test_scoping.py \
  itambox/inventory/tests/test_shared_stock_surfaces.py \
  itambox/inventory/tests/test_stock_action_permissions.py \
  itambox/inventory/tests/test_stock_fanout.py \
  itambox/inventory/tests/test_tenant_resource_grant_security.py \
  itambox/itambox/tests/test_capabilities.py \
  itambox/itambox/tests/test_capability_slices.py \
  itambox/organization/tests/test_resource_access.py \
  itambox/organization/tests/test_resource_grant_views.py \
  itambox/organization/tests/test_resource_grants.py \
  itambox/organization/tests/test_localization.py \
  itambox/organization/tests/test_role_form_provider_scope.py \
  itambox/users/tests/test_provider_patch.py \
  itambox/users/tests/test_provider_services.py \
  itambox/users/tests/test_release_blockers_scim.py \
  itambox/users/tests/test_scim.py \
  itambox/users/tests/test_scim_group_provisioning_contracts.py \
  itambox/users/tests/test_scim_identity.py \
  itambox/users/tests/test_scim_stress.py \
  itambox/users/tests/test_scim_tenant_group_read_permissions.py \
  itambox/users/tests/test_token_api.py \
  itambox/users/tests/test_user_config_api.py \
  itambox/users/tests/test_user_groups.py \
  scripts/tests/test_ci_workflow_policy.py \
  scripts/tests/test_contract_policy.py \
  scripts/tests/test_inline_style_workflow_policy.py \
  scripts/tests/test_inline_styles.py \
  scripts/tests/test_migration_audit.py \
  scripts/tests/test_release_policy.py \
  scripts/tests/test_security_gate.py \
  scripts/tests/test_typing_policy.py \
  itambox/assets/tests/test_audit_ws6_10.py \
  itambox/assets/tests/test_assignments.py \
  itambox/assets/tests/test_tag_sequence_api.py \
  itambox/assets/tests/test_tag_sequence_concurrency.py \
  itambox/core/tests/test_context_isolation.py \
  itambox/core/tests/test_management_commands.py \
  itambox/core/tests/test_media_isolation.py \
  itambox/core/tests/test_multi_tenant_auth.py \
  itambox/core/tests/test_security.py \
  itambox/core/tests/test_task_error_contracts.py \
  itambox/core/tests/test_snipeit_error_contracts.py \
  itambox/itambox/tests/test_capability_surfaces.py \
  itambox/procurement/tests/test_concurrency.py \
  itambox/procurement/tests/test_localization.py \
  itambox/subscriptions/tests/test_migrations.py \
  itambox/subscriptions/tests/test_models.py \
  itambox/users/tests/test_scim_migrations.py \
  scripts/tests/test_check_test_report.py \
  scripts/tests/test_check_xdist_matrix.py \
  itambox/core/tests/test_authorization_cache_sync.py \
  itambox/core/tests/test_issue100_import_cycles.py \
  itambox/core/tests/test_issue100_tenancy_boundaries.py \
  itambox/core/tests/test_issue183_alert_api.py \
  itambox/core/tests/test_issue183_alert_migration.py \
  itambox/core/tests/test_issue183_alerts.py \
  itambox/core/tests/test_issue29_tenant_scope.py \
  itambox/organization/tests/test_authorization_cache.py \
  itambox/core/tests/test_tasks.py \
  itambox/core/tests/test_kernel_leaves.py \
  itambox/core/tests/test_kernel_serialization.py \
  itambox/core/tests/test_pdf_renderer.py \
  itambox/organization/tests/test_resource_grant_audit_api.py \
  itambox/organization/tests/test_resource_grant_expiry.py \
  itambox/organization/tests/test_resource_grant_migrations.py
```

Run the contract-policy checks from the repository root:

```bash
PYTHONPATH=itambox uv run --locked --group dev python -m pytest -q -p no:cacheprovider scripts/tests/test_contract_policy.py
PYTHONPATH=itambox uv run --locked --group dev python scripts/check_contract_policy.py
```

The suite must cover all three approved stock models, direct and ancestor-group grantees, unrelated and reverse-direction tenants, a real A→B→C non-transitivity chain, revocation and soft deletion of active tenants, owners, locations, and groups, level ordering, missing RBAC, superusers, actorless tasks, exact system-operation binding, destination-tenant binding, persisted source/target derivation, deterministic grant provenance, direct ORM factory denial, bounded resolver and rendered-table query counts, concurrent single-restoration returns, list/detail/form/mutation surfaces, hostile foreign IDs, and direct generic export. Every candidate requires an independent adversarial tenant-security review; this boundary may not be added to a security-debt baseline.
