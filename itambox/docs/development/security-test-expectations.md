# Security test expectations

This document is the normative reference for what a test must **assert** when it
covers security- or tenant-boundary-sensitive behaviour. It exists because
coverage and correctness are different measurements, and only one of them is
automated.

The gates described in the [Test coverage policy](test-coverage-policy.md) —
`scripts/check_coverage_baseline.py` and `scripts/check_diff_coverage.py` — prove
that lines **ran**. The differential gate is branch-aware, so a new `if` whose
`else` never executes does not count, but no gate can inspect an assertion. A
test that issues a cross-tenant request, ignores the response, and asserts
nothing still drives both numbers to 100%. This document defines the assertions
those executed lines are required to carry.

Both gates run from the repository root against the measured report:

```console
make coverage
make coverage-diff
```

A reviewer may block a pull request that meets the coverage target but does not
meet these expectations. Failing to meet them is a review defect, not a gate
bypass: the assertions are visible in the diff.

These expectations apply to the complete serial suite run against a clean
PostgreSQL database. That suite remains the correctness source of truth; a
subset, a `-k` selection, or an xdist run is not evidence that these
expectations are met.

Test paths below are repository-root relative. Every module and test name cited
was confirmed present in the tree at the time of writing.

## General rules

**Assert the boundary, not the happy path.** A test that only proves the
authorized actor succeeds proves nothing about authorization. Every
security-relevant test needs the denied case, and the denied case is the one
that must assert an exact outcome.

**Prove the negative.** "The foreign row did not appear in the response" is a
weaker claim than "the request 404'd". Assert the status code, the exception
type, or the exact queryset contents. Where the codebase's convention is to hide
existence rather than deny access — UI detail, REST detail, bulk PK sets — assert
the hiding behaviour precisely: `404`, not `403`, and dropped PKs, not error
messages.

**Assert fail-closed behaviour on missing or invalid context.** Absent tenant,
absent user, contradictory scope, expired grant, revoked membership, cache
outage, and deleted tenant are all inputs. Each must be asserted to deny or to
scope down, never to widen. A write attempted without a resolvable tenant must
be asserted to leave no row at all, not merely to return an error.

**No test may weaken an assertion to pass.** `assertNotEqual(response.status_code,
500)`, `assertIn(status, (403, 404))`, `assertTrue(qs.count() <= 1)`, and
`assertGreaterEqual(len(results), 0)` are not assertions about a boundary. If the
correct status is genuinely ambiguous, resolve the ambiguity in the code first.
Broadening an assertion because a behaviour changed is a change of contract and
belongs in the same review as the code change.

**Every new tenant-scoped model, viewset, and generic view needs its own
boundary test.** The registry sweeps are narrow: only
`test_tenant_scoped_models_have_tenant_scoped_all_objects` sweeps models, and
nothing sweeps DRF viewsets or generic CBVs. Adding a model to the registry
therefore satisfies one sweep and leaves the request-level boundary unproven.

**Assert on the object, not only on the response.** After a denied mutation,
refetch through `all_objects` and assert the row is unchanged and un-deleted. A
view can return `404` and still have written.

## Tenant scoping

### Expectations

1. A UI detail, edit, delete, or clone request for a foreign-tenant object MUST
   assert `404`, and MUST assert that the object still exists afterwards.
2. A REST list MUST assert the response contains **exactly** the authorized set,
   by identity, not merely that it excludes one foreign row.
3. REST detail, `PATCH`, and `DELETE` on a foreign-tenant object MUST assert
   `404` — never `403`, which discloses existence.
4. A bulk `POST` mixing own-tenant and foreign PKs MUST assert the foreign rows
   are silently dropped: own rows changed, foreign rows byte-identical.
5. Under the all-accessible scope, a test MUST assert the result equals the
   authorized tenant set and is never the unfiltered global queryset.
6. Contradictory scope (a pinned tenant plus all-accessible) MUST be asserted to
   fail closed, not to resolve to either input.
7. A create with no resolvable tenant MUST assert that no row was written at
   all — a global, tenant-less row is the failure mode being tested.
8. Global surfaces (search, changelog, GraphQL, exports) MUST have a leak test
   asserting foreign objects are absent from results, not just from the rendered
   page.
9. A soft-deleted tenant MUST be asserted to remove access, including for
   previously valid sessions and tokens.
10. New tenant-scoped models MUST add a per-app cross-tenant module mirroring
    `test_phase1_cross_tenant.py`; the model registry sweep does not substitute
    for it.

### Reference tests

| Behaviour | Module and test |
|---|---|
| UI detail 404s cross-tenant | `itambox/core/tests/test_tenant_security.py::test_ui_detail_cross_tenant_404` |
| REST detail 404s cross-tenant | `itambox/core/tests/test_tenant_security.py::test_api_detail_cross_tenant_404` |
| Bulk delete drops foreign PKs | `itambox/core/tests/test_tenant_security.py::test_bulk_delete_cross_tenant_pks_are_dropped` |
| Bulk edit leaves foreign rows unmodified | `itambox/core/tests/test_tenant_security.py::test_bulk_edit_cross_tenant_pks_are_not_modified` |
| All-accessible is never the global scope | `itambox/core/tests/test_issue29_tenant_scope.py::test_all_accessible_is_never_the_global_scope` |
| Contradictory scope fails closed | `itambox/core/tests/test_issue29_tenant_scope.py::test_contradictory_tenant_and_all_accessible_scope_fails_closed` |
| Create without tenant writes no global row | `itambox/itambox/tests/test_issue134_api_scope.py::test_create_without_tenant_fails_closed_no_global_row` |
| REST list returns exactly the authorized set | `itambox/itambox/tests/test_issue134_api_scope.py::test_list_returns_exactly_authorized_set` |
| Search does not leak across tenants | `itambox/core/tests/test_security_finish.py::test_search_does_not_leak_cross_tenant_data` |
| GraphQL cross-tenant query and mutation denied | `itambox/core/tests/test_security_boundaries.py::test_graphql_cross_tenant_query_denied` |
| Per-app pattern to copy | `itambox/licenses/tests/test_phase1_cross_tenant.py`, `itambox/inventory/tests/test_cross_tenant_checkout.py` |

## Authorization and RBAC

### Expectations

1. A permission test MUST assert the resolved permission set, not that a single
   view returned `200`. Union across direct grants and group grants is the unit
   under test.
2. Authorization MUST be asserted to key off permission **content**. A role named
   `admin` or `owner` without the permission MUST be asserted to be rejected, and
   an arbitrarily named role holding it MUST be asserted to be accepted.
3. A suspended membership, an inactive group, and a soft-deleted role MUST each
   be asserted to contribute **nothing** — assert the denial, not a reduced count.
4. An expired grant MUST be asserted inert, and MUST be asserted to expire
   without performing a write or bumping the generation counter.
5. An escalation test MUST assert the actor cannot grant a permission the actor
   does not itself hold, in every projection (own, explicit, group, all-managed).
6. Membership, grant, scope, and group-membership changes MUST each be asserted
   to invalidate any warmed authorization cache.
7. A cache outage MUST be asserted to recompute and deny — never to enable a
   shortcut or serve a stale permit.
8. MFA policy tests MUST assert the enforcement decision follows the current
   role, including a mid-session role upgrade with no re-login.
9. A new view MUST assert the anonymous, authenticated-without-permission, and
   authenticated-with-permission cases; the middle case is the one that fails
   silently when a decorator is dropped.
10. A new DRF viewset MUST assert its object-level boundary directly. Nothing
    asserts that a viewset declares `StrictTenantPermission`, so the declaration
    is not evidence.

### Reference tests

| Behaviour | Module and test |
|---|---|
| Union across direct and group roles | `itambox/core/tests/test_tenant_security.py::test_multi_role_membership_is_union` |
| Suspended membership grants nothing | `itambox/core/tests/test_tenant_security.py::test_suspended_membership_own_roles_grant_nothing` |
| Soft-deleted role contributes nothing | `itambox/core/tests/test_security_boundaries.py::test_soft_deleted_role_on_membership_contributes_nothing` |
| Grant expiry performs no write | `itambox/core/tests/test_issue29_tenant_scope.py::test_expiring_grant_is_removed_without_save_or_generation_bump` |
| Actor cannot grant unheld permission | `itambox/organization/tests/test_escalation_surface.py::test_actor_cannot_grant_permission_they_do_not_hold` |
| Permission content, not role name | `itambox/users/tests/test_provider_scim.py::test_authorization_is_permission_content_not_role_name` |
| Cache invalidation on membership change | `itambox/core/tests/test_issue29_tenant_scope.py::test_membership_change_invalidates_warmed_accessible_set` |
| Cache outage never permits a shortcut | `itambox/core/tests/test_authorization_cache_sync.py::test_cache_outage_never_enables_request_shortcut` |
| Mid-session role upgrade enforced | `itambox/core/tests/test_sec_mfa.py::test_midsession_role_upgrade_is_enforced_without_relogin` |
| Per-view permission matrix | `itambox/compliance/tests/test_permissions.py`, `itambox/itambox/tests/test_generic_cbv.py::PermissionEnforcementTests` |

## API tokens

### Expectations

1. A test touching token storage MUST assert the plaintext is absent from the
   persisted row and that only the HMAC digest and the key preview are stored.
2. Token authentication MUST be asserted to pin the request to the token's
   tenant, overriding any ambient or all-accessible scope.
3. A token MUST be asserted unable to reach another tenant's detail endpoint,
   and a bulk create through a token MUST assert every written row carries the
   token's tenant.
4. A read-only token MUST be asserted to reject writes at the request layer with
   the exact status, and to still allow reads.
5. IP restriction tests MUST cover the allowed address, the disallowed address,
   the trusted-proxy forwarding case, and the spoofed `X-Forwarded-For` case
   without proxy trust. The spoof case MUST assert rejection.
6. CIDR and IPv6 forms MUST be asserted, not assumed to follow from the IPv4
   case.
7. Revocation inputs — inactive membership, deleted tenant — MUST each be
   asserted to fail authentication, not merely to narrow the queryset.
8. Expiry MUST be asserted at the layer under test. A model-level expiry
   assertion does not prove the DRF authentication class rejects the request.
9. Any change to token hashing MUST assert pepper-rotation semantics: a token
   digested under an older pepper id still authenticates once a newer pepper is
   added, and removing a pepper invalidates the tokens bound to it.

### Reference tests

| Behaviour | Module and test |
|---|---|
| Plaintext never persisted | `itambox/users/tests/test_models.py::TokenModelTests::test_token_plaintext_is_not_stored_at_rest` |
| Token pins the request scope | `itambox/users/tests/test_token_ip_auth.py::test_authentication_replaces_all_accessible_with_token_tenant` |
| Spoofed forwarding rejected | `itambox/users/tests/test_token_ip_auth.py::test_spoofed_forwarded_for_ignored_without_proxy_trust` |
| Read-only token rejects writes | `itambox/users/tests/test_token_ip_auth.py::test_read_only_token_rejected_for_post` |
| Inactive membership revokes the token | `itambox/users/tests/test_token_ip_auth.py::test_inactive_membership_revokes_token_authentication` |
| Token bulk create pins every row | `itambox/itambox/tests/test_issue134_api_scope.py::TokenSingleTenantTests::test_token_bulk_create_without_tenant_pins_every_row` |
| Token permission context must match | `itambox/core/tests/test_sec_token_perm.py::test_token_permission_context_must_match_token_tenant` |

## Field encryption

This is the thinnest domain in the suite. `itambox/core/tests/test_crypto.py`
holds two tests. Treat any change under `itambox/core/crypto.py` as requiring
new assertions rather than relying on existing coverage.

### Expectations

1. A new encrypted field MUST assert that the value at rest carries the `enc$`
   sentinel and does not equal the plaintext. Reading back the decrypted
   property alone does not prove the column is encrypted.
2. `save()` MUST be asserted idempotent: re-saving an already-encrypted value
   must not double-encrypt.
3. Keyring behaviour MUST be asserted with more than one key: ciphertext written
   under an older key must decrypt while that key is still listed, and must stop
   decrypting once it is dropped.
4. Rotation MUST be asserted end-to-end for the new field — encrypt under the
   old key, rotate, drop the old key, assert the value still decrypts. The
   rotation test names its models explicitly, so a new field is not covered until
   it is added there.
5. Error paths MUST be asserted, not documented: `decrypt_string` raises
   `ValueError` both on a missing `enc$` prefix and on corrupt ciphertext.
6. An encrypted value MUST be asserted absent from every derived surface it can
   leak through — changelog snapshots, CSV/YAML export, REST responses, GraphQL
   results, and log records.
7. Configuration posture MUST be asserted where it is security-relevant, for
   example the production warning when the key is derived from `SECRET_KEY`.

### Reference tests

| Behaviour | Module and test |
|---|---|
| Multi-key decrypt under an older key | `itambox/core/tests/test_crypto.py::test_multi_key_encryption_consolidation` |
| Rotation covers every encrypted field | `itambox/core/tests/test_rotate_encryption_keys.py::test_rotation_covers_all_encrypted_fields` |
| Encrypt-on-save and idempotence | `itambox/core/tests/test_tenant_security.py::EmailSettingsEncryptionTestCase::test_save_is_idempotent_for_already_encrypted` |
| Full field lifecycle | `itambox/licenses/tests/test_models.py::test_license_product_key_encryption_lifecycle` |
| Derived-key production warning | `itambox/core/tests/test_prod_settings.py::test_derived_encryption_key_warns_in_prod` |

## Data import

This section covers CSV/YAML **data** import. `itambox/core/tests/test_import_boundaries.py`
is about Python module import boundaries and is not relevant here.

### Expectations

1. Row-cap tests MUST assert both sides of the limit: a file **at**
   `MAX_IMPORT_ROWS` is accepted and a file **over** it is rejected.
2. Lookup expressions MUST be asserted against an allowlist, with `regex` and
   `iregex` asserted rejected by name.
3. Security-sensitive models MUST be asserted non-importable through the generic
   import surface, and the direct URL MUST be asserted to `404` **even for a
   superuser**.
4. Database error messages surfaced per row MUST be asserted sanitized —
   assert the raw exception text is absent, not only that some message appeared.
5. Export tests MUST assert formula triggers are neutralized and that the
   filename cannot carry header injection.
6. Import MUST be asserted to require the `add` permission, with the
   without-permission case asserting the gate, not just a different template.
7. An import that carries a tenant column or a foreign-tenant foreign-key id
   MUST be asserted to write into the acting tenant only, and MUST be asserted
   never to resolve a relation into another tenant.
8. Upsert paths MUST assert the matched-existing case and the
   nonexistent-identifier case separately; silent creation on a failed match is
   the failure mode.
9. A new `@register_import_form` registration MUST come with a test asserting the
   form is reachable for the intended model and that no unintended model became
   importable.
10. Import work executed asynchronously MUST assert the task runs inside a
    `TaskContext`, otherwise the writes are unscoped and unlogged.

### Reference tests

| Behaviour | Module and test |
|---|---|
| Row cap at and over the limit | `itambox/core/tests/test_phase0_import_search.py::ImportRowCapTests::test_at_limit_csv_is_accepted`, `::test_over_limit_csv_is_invalid` |
| Lookup allowlist | `itambox/core/tests/test_phase0_import_search.py::SearchLookupAllowlistTests::test_iregex_and_regex_not_allowed` |
| Sensitive models not importable | `itambox/core/tests/test_import_security_models.py::test_direct_generic_import_urls_are_404_even_for_superuser` |
| Row error sanitization | `itambox/core/tests/test_a5_import_sanitize.py::test_integrity_error_row_message_is_sanitized` |
| CSV formula and filename injection | `itambox/core/tests/test_csv_utils.py::test_csv_safe_neutralizes_formula_triggers`, `::test_safe_csv_filename_strips_header_injection` |
| Permission gating and upsert flow | `itambox/assets/tests/test_import_export.py::ImportExportPermissionTestCase`, `::test_csv_import_upsert_existing` |
| Upload extension hardening | `itambox/core/tests/test_upload_hardening.py::test_dangerous_extensions_rejected` |

## SCIM provisioning

### Expectations

1. A SCIM token scoped to tenant A MUST be asserted rejected against tenant B,
   and asserted still working against tenant A in the same module.
2. Authorization MUST be asserted by permission content: roles named `admin` and
   `owner` without the permission are rejected; a differently named role holding
   it is accepted.
3. `active: false` MUST be asserted to deprovision **only** the calling tenant,
   and MUST assert the shared global user object is unmutated.
4. `PATCH` and `PUT` MUST be asserted not to mutate shared global user state as a
   side effect of a tenant-scoped operation.
5. Group synchronisation MUST be asserted to reject self-escalation and
   escalation of another principal.
6. Group operations MUST be asserted to reconcile only SCIM-originated
   memberships, leaving locally managed memberships intact.
7. Expired tokens, invalid tokens, and unknown tenant slugs MUST be asserted to
   return SCIM-shaped errors — assert the error body shape, not only the status.
8. Adversarial input MUST be asserted handled, not merely survived: filter
   parsing against SQL injection, malformed filters, empty and non-JSON bodies,
   and over-long identifiers.
9. Provider isolation MUST be asserted: a token bound to one provider cannot act
   through another.
10. SCIM-originated mutations SHOULD be asserted to produce an `ObjectChange`
    attributed to the SCIM principal; a provisioning path that writes without
    attribution is an audit gap.

### Reference tests

| Behaviour | Module and test |
|---|---|
| Token scoped to tenant A rejected against B | `itambox/users/tests/test_release_blockers_scim.py::test_token_scoped_to_tenant_a_is_rejected_against_tenant_b` |
| Role name is not authority | `itambox/users/tests/test_release_blockers_scim.py::test_role_named_admin_without_the_permission_is_rejected` |
| Deprovision only this tenant | `itambox/users/tests/test_scim.py::test_scim_active_false_deprovisions_this_tenant_only` |
| Shared global user untouched | `itambox/users/tests/test_scim.py::test_scim_patch_does_not_mutate_shared_global_user` |
| Group sync rejects escalation | `itambox/users/tests/test_provider_scim.py::test_group_sync_rejects_self_and_other_escalation` |
| Only SCIM memberships reconciled | `itambox/users/tests/test_provider_scim.py::test_put_and_patch_reconcile_only_scim_memberships` |
| Provider isolation | `itambox/users/tests/test_provider_scim.py::test_provider_group_isolation` |
| Adversarial input and SCIM error shape | `itambox/users/tests/test_scim_stress.py::test_sql_injection_username_filter`, `::test_expired_token_returns_scim_error` |

## Task context

Background writes are the easiest place to lose both tenant scoping and audit
attribution. `ChangeLoggingMixin` skips logging entirely when `_request_id` or
`_current_user` is unset, so a task that forgets `TaskContext` writes unscoped
rows and produces no `ObjectChange` — with no error anywhere.

### Expectations

1. A new task MUST assert it runs inside a `TaskContext`, by asserting the
   observable consequence: the write is tenant-scoped **and** an `ObjectChange`
   row exists for it.
2. The `ObjectChange` MUST be asserted attributed to the task's user, not to
   whichever user happened to be ambient.
3. Attribution MUST be asserted to follow the **object's** tenant, not the
   ambient scope, where the two differ.
4. Entering a `TaskContext` MUST be asserted to clear the ambient scope, and
   exiting MUST be asserted to restore it exactly.
5. A failure inside `__enter__` MUST be asserted to restore **every** ambient
   contextvar, not only the tenant.
6. A `TaskContext` MUST be asserted never to inherit a membership from another
   tenant, and never to bind an inactive membership.
7. An invalid tenant or an unauthorized user MUST be asserted rejected with the
   outer scope left intact.
8. Tasks that can run reentrantly MUST assert nested `TaskContext` behaviour
   explicitly; the outer scope must be restored, not flattened.
9. Because the worker reuses processes, a task test SHOULD assert no scope
   survives past the task boundary — run a second, differently scoped task and
   assert its result is unaffected by the first.

### Reference tests

| Behaviour | Module and test |
|---|---|
| Clear then restore ambient scope | `itambox/core/tests/test_issue29_tenant_scope.py::test_task_context_clears_then_restores_all_accessible_scope` |
| Enter-failure restores everything | `itambox/core/tests/test_issue29_tenant_scope.py::test_task_context_enter_failure_restores_every_ambient_context` |
| Never inherits a foreign membership | `itambox/core/tests/test_issue29_tenant_scope.py::test_task_context_does_not_inherit_membership_from_another_tenant` |
| Never binds an inactive membership | `itambox/core/tests/test_issue29_tenant_scope.py::test_task_context_never_binds_inactive_membership` |
| Invalid tenant rejected, outer scope kept | `itambox/core/tests/test_issue29_tenant_scope.py::test_task_context_rejects_invalid_tenant_and_restores_outer_scope` |
| Task write produces an `ObjectChange` | `itambox/core/tests/test_phase3_task_audit.py::test_scheduled_report_run_logs_object_change` |

## Destructive operations

### Expectations

1. Delete MUST be asserted to **soft**-delete: the row is still reachable through
   `all_objects` with `deleted_at` set, and absent from the default manager.
2. Purge MUST be asserted to **hard**-delete: the row is absent from
   `all_objects` afterwards.
3. Restore MUST be asserted to make the object visible through the default
   manager again, with the relations that matter re-established.
4. `all_objects` MUST be asserted to be itself tenant-scoped. It is the manager
   that bypasses soft-delete filtering, not the tenant boundary.
5. Cross-tenant restore and cross-tenant purge MUST each be asserted blocked, by
   asserting the target row's state afterwards, not only the response.
6. Restore MUST be asserted unable to resurrect a grant the acting user could not
   have created, across every projection, including the bulk path where one
   unsafe row must be skipped while a safe row still restores.
7. Bulk endpoints MUST be asserted to reject non-tenant models and syntactically
   invalid model names before touching any row.
8. Soft-delete uniqueness MUST be asserted behaviourally: after soft-deleting a
   row, re-creating one with the same name or slug must succeed.
9. Cascade behaviour MUST be asserted explicitly for both soft and hard delete;
   an orphaned or wrongly cascaded child is invisible in a status-code assertion.
10. Hard-delete and purge entry points MUST assert the permission actually
    required to reach them, separately from the soft-delete permission.
11. Soft-deleted rows MUST be asserted absent from every read surface the model
    is exposed on: REST, GraphQL, search, export, and list views.

### Reference tests

| Behaviour | Module and test |
|---|---|
| `all_objects` is tenant-scoped | `itambox/core/tests/test_tenant_security.py::RecycleBinTenantScopingTestCase::test_all_objects_is_tenant_scoped` |
| `all_objects` includes soft-deleted rows | `itambox/core/tests/test_tenant_security.py::test_all_objects_includes_soft_deleted_rows` |
| Registry sweep over tenant-scoped models | `itambox/core/tests/test_tenant_security.py::test_tenant_scoped_models_have_tenant_scoped_all_objects` |
| Cross-tenant restore and purge blocked | `itambox/core/tests/test_tenant_security.py::test_recycle_bin_restore_cross_tenant_blocked`, `::test_recycle_bin_purge_cross_tenant_blocked` |
| Soft delete, restore, hard purge | `itambox/itambox/tests/test_generic_cbv.py::ObjectDeleteViewTests::test_post_delete_soft_deletes`, `::RestoreAndPurgeTests::test_purge_hard_deletes_object` |
| Restore cannot escalate a grant | `itambox/core/tests/test_restore_grant_escalation.py::test_bulk_restore_skips_unsafe_row_and_restores_safe_row` |
| Bulk model-name allowlist | `itambox/core/tests/test_security_finish.py::BulkModelNameAllowlistTests::test_bulk_delete_rejects_non_tenant_model` |
| Unique constraint freed by soft delete | `itambox/subscriptions/tests/test_phase0_provider_softdelete.py::test_recreate_after_soft_delete_reuses_name_slug` |
| Cascade soft and hard delete | `itambox/core/tests/test_models.py::test_cascade_soft_delete_and_hard_delete` |

## Known gaps

These boundaries are currently unasserted. They are recorded here as work items,
not as exemptions: a change that touches one of them is expected to close it,
and none of them may be cited as precedent for omitting an assertion.

| # | Gap | Domain |
|---|---|---|
| 1 | No enumeration test asserting that every DRF viewset and every generic CBV `404`s cross-tenant. `test_tenant_scoped_models_have_tenant_scoped_all_objects` sweeps models only. | Tenant scoping |
| 2 | `StrictTenantPermission` is never unit-tested by name. It appears in test docstrings only, and nothing asserts that every viewset declares it. | RBAC |
| 3 | Pepper rotation is untested. Nothing asserts a token hashed under an older pepper id still authenticates after a newer pepper is added, nor that removing a pepper invalidates its tokens. | API tokens |
| 4 | No test asserts an **expired** token is rejected at the DRF authentication layer; expiry is asserted at model level only. | API tokens |
| 5 | No test asserts `decrypt_string` raises on a missing `enc$` prefix or on corrupt ciphertext. Both are explicit `ValueError` paths in `itambox/core/crypto.py`, and the function's docstring contradicts the code by claiming the original ciphertext is returned. | Field encryption |
| 6 | No sweep asserts that every encrypted field is covered by `rotate_encryption_keys`; the rotation test names three models explicitly. | Field encryption |
| 7 | No test asserts an encrypted value never appears in a changelog snapshot, an export, or an API response. | Field encryption |
| 8 | Nothing asserts an imported row is pinned to the acting tenant. A CSV carrying a tenant column or a foreign-tenant foreign-key id is not proven unable to write into or reference another tenant. | Data import |
| 9 | The `@register_import_form` registry itself is unvalidated — nothing asserts which models it exposes. | Data import |
| 10 | The asynchronous `itambox/core/tasks/csv_import.py` path is untested, including its `TaskContext` usage. | Data import |
| 11 | SCIM-originated mutations are not asserted to appear in the change log attributed to the SCIM principal. | SCIM |
| 12 | No SCIM rate-limit or replay test exists. | SCIM |
| 13 | Nothing asserts that every registered task and schedule actually enters a `TaskContext`. A task that forgets the wrapper runs unscoped and, because `_request_id` and `_current_user` are unset, unlogged. | Task context |
| 14 | No nested/reentrant `TaskContext` test and no worker-reuse leakage test. | Task context |
| 15 | Hard delete and `purge_deleted` are not asserted to require a distinct elevated permission. | Destructive operations |
| 16 | Soft-deleted rows are not asserted excluded from export, REST, GraphQL, and search as a class — only per-model, ad hoc. | Destructive operations |
| 17 | `purge_deleted` is not asserted tenant-scoped; the management-command test exercises the dry-run path only. | Destructive operations |

## Related

Three documents divide this ground and none replaces another:

- [Test coverage policy](test-coverage-policy.md) owns the measurement — which
  lines ran, and how the ratchets are enforced.
- This document owns the assertions those lines must carry.
- [Security scanning policy](security-scanning.md) owns supply chain and
  secrets, which no first-party test covers.
