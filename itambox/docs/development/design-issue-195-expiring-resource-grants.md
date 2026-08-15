# Design: expiring tenant resource grants (issue #195 / WP-22)

**Status:** Proposed for maintainer approval  
**Capability:** `organization.resource_grants` — Stable, security-critical, always on  
**Source snapshot:** `design/issue-195-expiring-grants` at `7aea051f`  
**Hard prerequisite:** WP-21 / issue #194, satisfied

## 1. Context

`TenantResourceGrant` is the authorization edge that permits one tenant, or the
members of one tenant group, to reach one of three approved stock resources.
It deliberately does not grant a user permission: the canonical resolver still
requires both a live grant and independent RBAC. The model documents that
non-transitive contract at `organization/models.py:1247-1261`, and the
Stable/security-critical capability declaration makes it always on at
`organization/apps.py:61-74`.

Today a grant is perpetual until it is soft-deleted. Its default manager is a
plain `SoftDeleteManager`, deliberately unscoped because a grant connects
containers, and generic export is explicitly prohibited
(`organization/models.py:1263-1276`). The model has no `valid_until` field;
its current data fields end with `granted_by` and `reason`
(`organization/models.py:1293-1347`). The two active-grant uniqueness
constraints depend only on `deleted_at IS NULL`
(`organization/models.py:1349-1386`).

WP-22 adds an optional deadline without adding a second definition of “live.”
The deadline is interpreted only by a scheduled sweep. When the deadline is
due, the sweep performs the existing soft-revocation transition. Until that
transition commits, the row remains a live grant. After it commits, every
existing reader sees an ordinary revoked grant.

That choice is intentionally different from membership role grants. A
`RoleGrant` has an indexed nullable `valid_until`
(`organization/models.py:774-825`), and its authorization query interprets the
clock on every read (`organization/models.py:759-771`). Resource grants must
not copy that query-time pattern: there may be no “expired but live” resolver
state and no clock predicate in a uniqueness constraint.

WP-21 froze the threat model and mandatory isolation suite. It states that the
default grant manager is deliberately unscoped and that generic container
surfaces must invoke `visible_to_containers()`
(`docs/development/tenant-resource-grant-security.md:69-82`). It also requires
future lifecycle automation to preserve durable actor or trusted-system
attribution without erasing assignment provenance
(`docs/development/tenant-resource-grant-security.md:103-124`). This design
extends those contracts; it does not reopen them.

### 1.1 Repository findings that correct premises in the issue brief

Three current-source details affect the implementation plan.

1. `visible_to_containers()` is already the required gateway, but its present
   direct-`tenant` branch filters only owner-side rows
   (`organization/services/resource_access.py:46-71`). Because
   `TenantResourceGrant` has a direct `tenant` field, the current helper cannot
   yet provide the issue's owner **or** direct-grantee **or** group-grantee
   audit visibility. WP-22 must extend this helper with an explicit
   `TenantResourceGrant` branch; calling the current helper unchanged would be
   a security-correct owner-only result but an incomplete issue implementation.

2. The reviewed OpenAPI artifacts are `itambox/schema.yaml` and
   `scripts/openapi_diagnostics_baseline.json`
   (`docs/development/openapi-schema-policy.md:22-30`).
   `openapi-diagnostics.generated.json` is a local investigation output
   (`docs/development/openapi-schema-policy.md:82-89`), while
   `diagnostics.generated.json` is the CI artifact name
   (`docs/development/openapi-schema-policy.md:96-106`). The implementation
   updates the tracked schema, adds no diagnostic identity, and reviews the
   generated report; it does not treat either generated report as a tracked
   baseline.

3. `scripts/resource_grant_test_manifest.json` currently uses LF with one-space
   JSON indentation, visible at `scripts/resource_grant_test_manifest.json:1-5`,
   not two-space indentation. WP-22 should preserve the file's current canonical
   serialization unless a separate policy change deliberately reformats it.
   The semantic requirement remains unchanged: every added or changed Python
   test belongs in both `changed_tests` and `mandatory_tests`, and the mandatory
   order is copied into the threat-model selector.

### 1.2 Goals

- Add a nullable deadline whose `NULL` value preserves every existing grant.
- Revoke due grants through the existing soft-delete transition.
- Keep the resolver, batch projection, coverage checks, and active uniqueness
  constraints clock-free.
- Run actorless per-tenant revocation in an entered `TaskContext` with a valid,
  operation-bound `SystemAuthorizationContext`.
- Write exactly one `ObjectChange` per expiry revocation with `user=None`, the
  owner tenant, the synthetic request ID, transition time, and the triggering
  deadline.
- Make duplicate, overlapping, and retried execution converge without duplicate
  revocations or duplicate change records.
- Persist an operator-readable run outcome and the exact grants revoked by it.
- Add an additive read-only audit API that includes active and revoked grants
  but never bypasses container visibility.
- Route create and revoke transitions through the existing event, alert, and
  notification pipeline without resolving or disclosing cross-tenant resource
  details.
- Specify a one-row rollback that preserves all historical audit evidence and
  restores access only when RBAC independently allows it.

These goals build on the canonical resolver at
`organization/access.py:89-173`, the batched projection at
`organization/access.py:206-299`, and the coverage check at
`inventory/abstract_models.py:350-367`.

### 1.3 Non-goals

- No REST create, update, revoke, restore, or bulk action.
- No automated restore-all operation.
- No attestation, periodic review, or approval workflow.
- No fourth resource type; the closed allowlist remains the three inventory
  stock models at `organization/models.py:1278-1291`.
- No grant-to-user semantics, transitive sharing, superuser grant bypass, or
  change to the grant-plus-RBAC decision.
- No query-time `valid_until` check in `shared_resource_ids()`,
  `resolve_stock_access()`, `resolved_shared_stock_ids()`, or
  `_grant_coverage_problems()`.
- No change to either partial uniqueness constraint.
- No `Now()` or database clock expression in a PostgreSQL partial-index
  predicate.
- No runtime image, dependency, `pip`, or packaging work.

The canonical grant query currently relies on the soft-delete manager and group
ancestry only (`organization/access.py:31-54`). That remains the complete
resolver behavior after WP-22.

## 2. Decisions

### D1. A nullable deadline changes data, not the definition of liveness

Add `valid_until = models.DateTimeField(blank=True, null=True, ...)` to
`TenantResourceGrant`. Do not add a default and do not backfill. `NULL` means
perpetual. A non-null deadline is operator configuration for the sweep, not an
independent live-state flag.

`TenantResourceGrant.is_active` continues to mean only
`deleted_at is None` (`organization/models.py:1388-1394`). The default manager
continues to hide soft-deleted rows, and both partial unique constraints keep
their existing `deleted_at__isnull=True` predicates
(`organization/models.py:1349-1386`).

Rationale: a single durable state transition makes every existing authorization
reader agree, even when clocks differ or a worker was offline. It also preserves
the security proof that “no live grant means denial.”

Rejected alternative: copy the `RoleGrant` read filter from the canonical
RBAC grant query at `organization/rbac.py:29-40`. That would create a second
liveness definition and allow constraints, coverage checks, and resolvers to
disagree.

### D2. Add one clock-free sweep index; leave the constraints untouched

Add a conditional index ordered by `tenant, valid_until` with a static predicate:

```text
fields = (tenant, valid_until)
condition = deleted_at IS NULL AND valid_until IS NOT NULL
name = org_trg_active_expiry_idx
```

Do not set `db_index=True` on `valid_until`; the targeted index is the access
path needed by the per-tenant due query and avoids an additional global index.
The existing resource indexes remain at `organization/models.py:1378-1385`.

The predicate contains no comparison with current time. The sweep supplies a
normal bound parameter, `valid_until__lte=cutoff`, at execution time. Neither
active uniqueness constraint changes.

Rationale: PostgreSQL can use the tenant/deadline index for the bounded due scan,
while the index remains immutable and valid for a partial-index predicate.

### D3. The existing owner UI accepts an optional future deadline for both grantee forms

Add `valid_until` to the existing non-REST creation form for both a direct
tenant grantee and a tenant-group grantee. The current form exposes grantee,
access level, and reason only (`organization/forms/resource_grant_form.py:20-26`)
and validates exactly one grantee (`organization/forms/resource_grant_form.py:67-82`).

Validation rules:

- blank is valid and means perpetual;
- a supplied value must be timezone-aware after Django form normalization;
- it must be strictly later than the validation-time `timezone.now()`;
- the rule is identical for direct and group grants;
- server-derived owner/resource/grantor behavior remains unchanged.

The owner-only create view continues to bind the resource and `granted_by` on
the server (`organization/views/resource_grant_views.py:72-126`). No edit form
is introduced. A mistaken deadline is corrected through the documented
one-row administrative rollback, not a general grant edit surface.

Rationale: shipping the field and sweep together is useful only if the existing
authorized creation surface can configure the deadline. A group-only exclusion
has no security basis; the grantee kind does not change owner authority or the
soft-revocation semantics.

### D4. An hourly coordinator creates deterministic per-tenant runs

Register one hourly django-q2 coordinator schedule from `OrganizationConfig`.
The app currently registers capabilities in `ready()`
(`organization/apps.py:6-40`); it should add a `post_migrate` schedule hook using
the concurrency-safe `register_schedule()` helper whose locking and duplicate
collapse are defined at `core/schedules.py:1-16` and `core/schedules.py:27-80`.
The subscriptions app is the domain precedent for app-owned schedule
registration (`subscriptions/apps.py:47-59`).

The coordinator:

1. takes one database `timezone.now()` value as `cutoff`;
2. rounds the schedule slot to the start of the current UTC hour;
3. enumerates live tenant IDs through an explicitly unscoped manager;
4. obtains or creates one run row per `(tenant, schedule_slot)`;
5. dispatches each per-tenant task with `transaction.on_commit()`; and
6. never performs grant mutation itself.

**Run claim protocol.** The run row is the single coordination point. Its
persisted `generation` starts at 1 and every queued delivery carries
`(tenant_id, run_id, generation)`. Dispatch and completion use these explicit
compare-and-set transitions:

1. The creator commits a `queued`, generation-1 row with
   `dispatch_stale_at = now + queue_stale_interval`, then its `on_commit`
   callback enqueues that exact generation. Another coordinator may read the
   row but may not reclaim a fresh queue entry before `dispatch_stale_at`.
2. A worker claims only with
   `UPDATE ... WHERE state='queued' AND generation=<delivered generation>
   AND (next_retry_at IS NULL OR next_retry_at <= now)`, changing it to
   `running`, incrementing `attempt_count`, and setting a bounded
   `lease_expires_at` while clearing the previous retryable outcome. A
   duplicate delivery changes zero rows and exits
   without touching run status or grants.
3. A successful/final worker completes only with
   `UPDATE ... WHERE state='running' AND generation=<claimed generation>`,
   changing it to `complete`, setting one final outcome and `finished_at`,
   and clearing lease/retry fields. Zero changed rows means stale completion;
   it is discarded. A complete row is immutable.
4. A retryable failure uses that same running+generation CAS to set `queued`,
   increment `generation`, set `next_retry_at` and a new
   `dispatch_stale_at`, then schedules exactly the new generation on commit.
5. An enqueue exception is caught by the callback and marks exactly that
   queued generation `enqueue_failed`. If that status write is itself blocked
   by the same database outage, the row remains queued; after
   `dispatch_stale_at` the repair CAS below invalidates that generation and
   safely dispatches a new one.
6. The coordinator repairs only: (a) `enqueue_failed`; (b) queued rows whose
   `next_retry_at` is due and `dispatch_stale_at` has passed; or (c) running
   rows whose `lease_expires_at` has passed. Its CAS includes the observed
   state **and generation**, increments generation, clears the lease, writes
   `queued` with a fresh dispatch deadline, and enqueues only the new
   generation. Delayed old deliveries are therefore stale by construction.

Repeated coordinators for the same `(tenant, schedule_slot)` otherwise do
nothing. If the run exists but its tenant argument is mismatched, the matching
generation is completed terminal and redacted without entering a tenantless
context. If the run row itself is missing, no run UI row can truthfully exist:
the task emits the typed terminal result and structured redacted task log; it
does not claim that a nonexistent record is visible or silently recreate it.

An hourly cadence bounds normal overrun to one schedule interval. Catch-up does
not require replaying missed hours: every run searches `valid_until <= cutoff`,
so the first run after downtime catches every still-live overdue grant. A unique
run key makes repeated coordinators harmless.

Rationale: per-tenant tasks give isolation, bounded transactions, tenant-specific
status, and truthful failures. A single global transaction would make one bad
tenant block all others. A management command alone would not provide an
in-product schedule or durable per-tenant outcome.

### D5. Every mutation uses an exact actorless system authorization contract

Each per-tenant task enters:

```text
TaskContext(
    tenant_id=<owner tenant id>,
    user_id=None,
    operation="organization.resource_grants.expiry_sweep",
)
```

Inside that context it requests:

```text
permission = "organization.delete_tenantresourcegrant"
operation  = "organization.resource_grant.expire"
reason     = "Scheduled revocation of tenant resource grants whose valid_until deadline has elapsed."
```

`TaskContext` creates the synthetic request ID at
`core/tasks/context.py:140-142`. `authorize_system()` rejects an unentered, actor-bound, tenantless,
mismatched, or blank authorization and binds permission, operation, reason,
and request ID at `core/tasks/context.py:170-194`. The issued frozen context
becomes invalid when the ambient tenant or request changes
(`core/context.py:96-132`). The `reason` is a required, task-supplied,
nonblank audit string: `SystemAuthorizationContext.is_valid_for()` validates
that the reason is nonblank but does not compare it
(`core/context.py:113-132`), so the design treats the reason as
task-supplied audit content, not as a value the consumer verifies verbatim.

The service mutation must call the existing tenant-operation authorization
path, which validates a human actor or an exact system authorization
(`organization/access.py:176-203`). Direct `QuerySet.update()` of `deleted_at`
is forbidden because it would bypass model validation, signals, and
`ChangeLoggingMixin`.

The expiry actor is always `user=None`. The display label “System” already used
when an `ObjectChange` has no user is a presentation label, not a fabricated
human (`core/models.py:245-269`). The run and API continue to serialize the
actual user value as null.

### D6. Row locks plus live-state rechecks provide exactly-once effects

The task selects candidate IDs at the fixed cutoff, then processes each in a
short `transaction.atomic()` block. It reloads the row through the unfiltered
base manager with `select_for_update()` and rechecks all eligibility conditions
before mutation.

**One revocation service for both paths.** Manual owner revocation and the
expiry sweep must go through the same `revoke_resource_grant(...)` service:
load through `_base_manager`, `select_for_update()`, recheck live state,
authorize (human delete permission in the active tenant, or the exact system
authorization for the sweep), then soft-delete. Today the manual revoke view
uses the generic delete flow without lock/recheck
(`organization/views/resource_grant_views.py:140-155`); a manual request can
load a live instance, the sweep can revoke it, and the stale manual instance
could then write a second `deleted_at` and a second delete `ObjectChange`,
and signal classification could disagree (already-deleted → deleted). The
shared service eliminates that race; a real manual-versus-sweep race test
asserts exactly one transition, one delete `ObjectChange`, and consistent
event classification.

The service calls the grant's existing soft-delete path. That path snapshots the
row and saves the soft-delete transition atomically
(`organization/models.py:1396-1415`). `ChangeLoggingMixin` obtains pre-change
state and emits create/update/delete audit data at
`core/models.py:410-446`.

After the save, in the same transaction, the service finds the single delete
`ObjectChange` by tenant, content type, object ID, action, and the current
synthetic request ID. It then creates the per-revocation evidence row. If the
change row is absent or ambiguous, the transaction fails closed and rolls back
the grant mutation.

Two workers can discover the same candidate, but only one can lock it while
live. The loser reloads a non-live row and records no mutation, no second
`ObjectChange`, and no second evidence row. A later run does not discover it
through the live candidate query. Evidence uniqueness on the delete
`ObjectChange` and `(grant, revoked_at)` enforces at most one expiry attribution
for that revocation while still permitting a restored grant to expire again.

Rationale: database row state, not task delivery promises, determines whether
the effect occurs. This remains safe after a failure halfway through a tenant:
committed rows are already deleted and the retry processes only remaining live
rows.

### D7. Persist one tenant run row and one row per expiry revocation

Add two operational models in the organization app.

`TenantResourceGrantExpiryRun` records:

- owner `tenant` (`PROTECT`);
- deterministic `schedule_slot` and immutable `cutoff`;
- execution `state`: queued, running, enqueue_failed, or complete;
- nullable `outcome` using the existing `TaskStatus` values;
- persisted positive `generation` (starts at 1), `attempt_count`,
  `started_at`, `last_attempt_at`, `finished_at`, `next_retry_at`,
  `dispatch_stale_at`, and `lease_expires_at`;
- reconstructible `revoked_count`, `invalid_count`, and
  `remaining_due_count`;
- stable `error_code` and a redacted `error_message`; and
- BaseModel creation/update timestamps.

`TenantResourceGrantExpiryRevocation` records:

- `run` (`CASCADE` when the run reaches its retention limit);
- `grant` (`PROTECT`);
- linked `ObjectChange` (`SET_NULL`, one-to-one);
- immutable `triggering_valid_until`, `revoked_at`, and synthetic `request_id`.

Both use `TenantScopingManager` and `deny_global_tenant = True`, following the
tenant-derived operational record precedent at
`compliance/models.py:223-229` and `compliance/models.py:295-300`. The
evidence model derives its tenant exclusively through
`tenant_lookup="run__tenant"` — there is **no duplicated direct `tenant`
field**. A duplicated field would be a fail-closed hazard: the models
deliberately omit `ChangeLoggingMixin`, and the repository's automatic
`clean()` signal runs only for `ChangeLoggingMixin` models
(`core/signals.py:53-58`), so nothing would ever validate the duplicate.
`tenant_lookup="run__tenant"` scopes the raw row to its run tenant; it does
**not** by itself make a corrupt run-A/grant-B row invisible. Therefore the
model exposes one reviewed `integrity_valid()` queryset and every run count,
run-detail, audit serializer, operator table, and admin surface must use it.
It requires `run__tenant_id=F("grant__tenant_id")` and, when
`object_change` is non-null, also requires all of:

- `object_change.tenant_id == run.tenant_id`;
- `changed_object_type == ContentType(TenantResourceGrant)`;
- `changed_object_id == grant_id`;
- `action == delete`; and
- `object_change.request_id == evidence.request_id`.

A null ObjectChange caused by retention remains integrity-valid and maps to
`kind=unknown` (A15). A non-null mismatch is excluded from every exposed
query/count and raises a redacted integrity alert; corruption tests use bulk
insertion to prove that wrong-tenant, wrong-grant, wrong-action, and
wrong-request evidence cannot affect counts or serialize identifiers.

The operational models do not soft-delete and do not use
`ChangeLoggingMixin`: changing run bookkeeping must not recursively create
authorization-change events. The grant and `ObjectChange` remain the durable
authorization history. Set `default_permissions = ()` on both operational
models, following `compliance/models.py:271-275`; the operator UI is
authorized by the existing grant view permission rather than creating an
independent run-record authority.

Constraints and indexes:

- unique `(tenant, schedule_slot)` on runs;
- unique `ObjectChange` and unique `(grant, revoked_at)` on expiry-revocation
  evidence;
- index `(tenant, -schedule_slot)` for the operator list;
- index `(tenant, outcome, -schedule_slot)` for failures;
- index `(run, grant)` on evidence (both are local fields).

Run-state constraints make invalid retention/claim combinations
unrepresentable:

| State | Allowed outcome | Required timing fields |
|---|---|---|
| `queued` | null or retryable | `finished_at=NULL`, `lease_expires_at=NULL`; `next_retry_at` optional |
| `running` | null | `finished_at=NULL`, non-null `lease_expires_at` |
| `enqueue_failed` | retryable | `finished_at=NULL`, `lease_expires_at=NULL` |
| `complete` | success, partial, skipped, or terminal | non-null `finished_at`; lease/retry fields null |

Check constraints also require `generation >= 1` and `attempt_count >= 0`.
Only the CAS transitions in D4 change state/generation; ordinary model/admin
edits cannot bypass them.

The `(grant, revoked_at)` key and one-to-one change link avoid a clock- or
current-state predicate. The service additionally requires one item per delete
`ObjectChange`. A restored and later re-expired grant has a new `deleted_at`, a
new deletion change, and a distinct evidence row; the old evidence remains
attached to its original run until normal retention prunes that run.

Retention follows the **existing effective changelog policy** rather than adding
a new setting. Runs and their evidence use the tenant's
`changelog_retention_days`, falling back to
`ITAMBOX_CHANGELOG_RETENTION_DAYS` (default 365 at
`core/settings/base.py:536`). Zero remains an unlimited/legal-hold value. The
pruner already calculates that effective per-tenant cutoff for `ObjectChange`
at `core/management/commands/prune_changelog.py:307-370`; WP-22 extends that
same class pass to the run rows, deleting evidence by cascade only after the
corresponding retention cutoff. Consequently no new `ITAMBOX_*` setting or
external-contract-inventory row is proposed; the existing setting is already
published at `docs/development/external-contract-inventory.md:498-502`.

Retention has a terminal-state rule and a safe timestamp:

- only **terminal** runs (success/partial/skipped/terminal) are prunable, and
  only against their `finished_at` — never `schedule_slot` (a very late
  retry that completed long after its slot would otherwise be deleted while
  holding fresh revocations) and never `created_at` (an unresolved run must
  stay visible);
- queued, running, retryable-queued, and `enqueue_failed` runs are always
  preserved;
- deletion order is evidence/run first, then `ObjectChange` under its own
  normal retention — a pruned `ObjectChange` while evidence still exists is a
  supported, defined API state (`kind=unknown`, A14/A15), never a crash;
- the per-tenant override, global fallback, and zero/legal-hold semantics
  apply to runs/evidence exactly as they apply to changelog rows.

### D8. The audit API is a new read-only, container-scoped surface

Register `TenantResourceGrantAuditViewSet` under:

```text
GET /api/organization/resource-grant-audit/
GET /api/organization/resource-grant-audit/{id}/
```

The organization API is already mounted at `/api/organization/`
(`itambox/api/urls.py:21-31`), and its local router is defined at
`organization/api/urls.py:1-31`. The viewset subclasses
`ITAMBoxReadOnlyModelViewSet`, whose surface is list/retrieve only
(`itambox/api/viewsets.py:79-80`), never `ITAMBoxModelViewSet`, whose mutation
mixins are defined at `itambox/api/viewsets.py:83-94`.

The base queryset starts with `TenantResourceGrant._base_manager` so revoked
rows are available. Before search, filter, ordering, pagination, or retrieve, it
must call:

```text
visible_to_containers(request.user, queryset, "organization.view_tenantresourcegrant")
```

Visibility is **request-scope-aware**, not merely user-aware. The current
helper derives every tenant the user can reach independently of the active
request scope (`organization/services/resource_access.py:52-62`); for the
grant audit surface that would defeat the token's single-tenant boundary
(`TokenPermissions` pins a token request to one tenant,
`itambox/api/permissions.py:17-95`) and would let a group-bound superuser
become platform-global (the middleware supports an active tenant group for
superusers, `itambox/middleware.py:290-312`). The grant-specific helper branch
therefore takes the active request scope into account and resolves visible
grant rows as follows:

| Request scope | Visible grant containers |
|---|---|
| Token-authenticated request | exactly `request.auth.tenant_id` (token pin) |
| Active tenant | exactly that tenant |
| Active tenant group | the live group subtree, intersected with the user's authorized tenants |
| All-accessible scope (non-superuser) | the canonical authorized-tenant set |
| Superuser with active tenant | exactly the active tenant ID |
| Superuser with active group | the selected group subtree (never global) |
| Genuinely unbound platform superuser | global (the explicit platform-authorized reader) |

Only the last row is global. The grant-specific branch is implemented
**before** the helper's current unconditional superuser return at
`organization/services/resource_access.py:52-53` so an ordinary
tenant-context grant-audit request can never silently become
platform-global. Other unfiltered container models retain their current
superuser behavior.

The container helper remains the only visibility gateway. Extend its explicit
unfiltered-model handling (`organization/services/resource_access.py:35-43`)
so a grant is visible when any visible container is:

- the owner `tenant`;
- the direct `grantee_tenant`; or
- a tenant whose live group or live ancestor group equals
  `grantee_tenant_group`.

The group ancestry must reuse `get_ancestor_tenant_group_ids(...,
live_only=True)`, the same helper the canonical resolver exports at
`organization/access.py:19-28` and uses for grants at
`organization/access.py:43-49`. It must not hand-roll a different hierarchy
walk.

For a normal user, `visible_to_containers()` first derives container IDs and
requires the requested permission on each container
(`organization/services/resource_access.py:54-62`). An authenticated user with
no authorized container gets `.none()`. Filters are always applied **after**
this visibility result, so an unrelated ID cannot alter a count or reveal that
a hidden value exists.

The endpoint uses a dedicated `TenantResourceGrantAuditPermission` because
`StrictTenantPermission` assumes a single owner-tenant boundary and its object
rules are not the owner/grantee/group visibility rule. The permission composes
token authentication and scope enforcement from `TokenPermissions`
(`itambox/api/permissions.py:17-95`) with these rules:

- only safe methods are accepted;
- the user must hold `organization.view_tenantresourcegrant` on at least one
  active container;
- object permission reruns the same container-visibility predicate;
- no permission grants a mutation because no mutation route exists.

Serializer fields:

| Field | Contract and privacy rule |
|---|---|
| `id`, `url` | Grant identity and read-only detail URL. |
| `state` | `active` iff `deleted_at` is null, otherwise `revoked`; no third state. |
| `owner` | `{id, name}` for an already-visible row. |
| `grantee_type`, `grantee` | Exactly one of tenant or group, `{id, name}`. |
| `resource_type`, `resource_id` | Approved model label and opaque primary key; never resolve a resource name or serialize the GenericFK object. |
| `access_level`, `reason` | Existing grant data, visible only after row authorization. |
| `granted_by_id`, `created_at`, `valid_until` | Creation attribution and configured deadline; human display data is not expanded recursively. |
| `revoked_at` | Existing `deleted_at`, null for live rows. |
| `revocation.kind` | `none`, `expiry`, `manual`, or `unknown`; expiry requires evidence linked to the delete change. |
| `revocation.user_id` | Null for expiry; nullable ID for a visible manual change; never replace null with a system user. |
| `revocation.request_id`, `revocation.time` | Values from the matching delete `ObjectChange`, nullable only for legacy/unknown history. |
| `revocation.triggering_valid_until`, `revocation.expiry_run_id` | Populated only from expiry evidence matching the grant's current `revoked_at`. |

`ObjectChange` already stores tenant, user, request, action, object identity,
and pre/post snapshots at `core/models.py:164-217`. Classification is
deterministic and conservative:

1. active grant → `none`;
2. integrity-valid evidence whose `revoked_at` equals the grant's **current**
   `deleted_at` (the current revocation), with a non-null, identity-valid
   delete ObjectChange → `expiry` and `user_id=null`. Evidence retained from
   an earlier restore/re-revoke cycle is deliberately not used: a grant that
   expired, was restored, and was later manually revoked is classified from
   its current revocation only;
3. no current-revocation expiry evidence, but an identity-valid delete
   ObjectChange for the current `deleted_at` whose `user_id` is still
   non-null → `manual`;
4. all other revoked states — stale evidence from an earlier cycle,
   pruned/null change link, deleted human user, actorless history without
   evidence, or identity mismatch — → `unknown`.

`ObjectChange.user_name` is display history, not a reserved actor-kind marker;
it can never distinguish a deleted human from the system actor. The serializer
may join only the integrity-valid evidence/change or the latest
tenant/content-type/object/action-matching historical delete change. It must
not expose raw `prechange_data` or `postchange_data`.

Supported filters are `state`, `owner_tenant_id`, `grantee_tenant_id`,
`grantee_tenant_group_id`, `resource_type_id`, `resource_id`, `access_level`,
`valid_until_before`, `valid_until_after`, `revoked_before`, and
`revoked_after`. Exact identifiers make rollback lookup possible; there is no
free-text search that resolves resource names. Every ID filter is a raw
scalar `NumberFilter(field_name=...)` — never an FK-style
`ModelChoiceFilter`, which would return a 400 invalid-choice for a foreign ID
and become an existence oracle (A8). The current API viewsets attach
`DjangoFilterBackend` explicitly (`organization/api/views.py:54-59`); the audit
view follows that established pattern with a dedicated filter set.

Related-row joins stay tenant-constrained: expiry evidence and `ObjectChange`
rows are owner-tenant scoped, while a grantee legitimately retrieves a grant
owned by another tenant. After the grant row itself passes `visible_to_containers()`, the serializer
may join evidence only through `integrity_valid()` (whose local scope equality
is `run__tenant_id == grant__tenant_id`) and may join a historical change only
through `_base_manager` with explicit tenant, content type, object ID, and
delete-action predicates. It never performs an unscoped generic join. Any
identity mismatch yields `kind=unknown` without serializing the mismatched row.

### D9. Existing change events are the lifecycle notification contract

Grant creation already passes through `ChangeLoggingMixin`, and both manual and
expiry revocation use the same soft-delete save. The signal layer detects a
soft-delete transition as a delete event (`core/signals.py:53-112`) and dispatches
event rules and watcher notifications after commit
(`core/signals.py:115-133`). WP-22 does not invent an “expired but live” event
or a second event bus.

Lifecycle event behavior is:

- creation: existing `create` ObjectChange/Event;
- manual owner revocation: existing `delete` ObjectChange/Event with human user;
- sweep revocation: existing `delete` ObjectChange/Event with `user=None`, plus
  linked expiry-run evidence;
- rollback: human-attributed **update** `ObjectChange` (the deadline
  correction plus `deleted_at=None` is one validated model save) plus the
  signal layer's **restore** `Event` with the human operator. There is no
  `restore` value in `ObjectChangeActionChoices`
  (`core/choices.py:6-20`); the design never fabricates one.

Event routing derives the event tenant from the object's owner tenant
(`core/events.py:83-110`). Tenant-owned rules are selected for that tenant, plus
explicit platform-global rules (`core/events.py:131-178`). Notification template
data remains the minimal safe event dictionary
(`core/events.py:326-375`). WP-22 must not add grantee-side recursive delivery,
resolve `grant.resource`, include resource names, include `reason`, or include
another tenant's contact data. An owner-tenant delete event tells operators that
sharing changed; the run UI and authorized audit detail explain why.

Rationale: this gives lifecycle awareness through the supported alerting
mechanism without turning a cross-container authorization edge into an
information-distribution channel.

### D10. Rollback is a human-attributed one-row restore, never field removal

The operator runbook restores one grant at a time by primary key from the audit
API or run detail. The administrative service must:

1. enter `TaskContext` with the human operator's `user_id`, owner `tenant_id`,
   and operation `organization.resource_grant.rollback`;
2. load the row through `_base_manager` and lock it;
3. require `deleted_at` to be non-null and the operator to hold the normal owner
   restore/delete authority;
4. clear or correct `valid_until` **before** restoring, so the next sweep does
   not immediately revoke the row again;
5. clear `deleted_at` through one validated model save, not `QuerySet.update()`;
6. preserve the prior expiry `ObjectChange`, run, and evidence row; and
7. rerun the normal resolver/RBAC verification.

`SoftDeleteMixin.restore()` currently clears `deleted_at` and saves
(`core/mixins.py:157-176`), but the runbook needs the combined deadline+restore
operation so no transient expired-live row is committed. If active uniqueness
now conflicts with a replacement grant, restore fails cleanly and the operator
must resolve that single-row conflict; the two uniqueness constraints at
`organization/models.py:1360-1377` remain authoritative.

The **named, single-row operator surface** is a management command
(`manage.py restore_resource_grant --grant <pk> --tenant <id> --user <id>
[--valid-until <iso>|--clear-deadline]`) that prompts for or requires
explicit operator confirmation, loads the row through `_base_manager`, locks
it, and invokes the same locked rollback service as steps 1-7 above. The
command is the only supported rollback entry point; the runbook references it
by name, never raw SQL. Its tests prove one update `ObjectChange` plus one
restore `Event`, retention of the prior expiry `ObjectChange`/evidence, and
RBAC re-verification after restore.

Dropping `valid_until` in a reverse migration cannot and must not undo an
already-committed `deleted_at`. The historical `ObjectChange` is not edited or
deleted by rollback. Restored access still requires independent RBAC through the
canonical decision at `organization/access.py:376-418`.

## 3. Data model and migration design

### 3.1 `TenantResourceGrant.valid_until`

Planned model field:

| Property | Value |
|---|---|
| Django type | `DateTimeField` |
| Nullability | `null=True`, `blank=True` |
| Default | none |
| Meaning of null | perpetual until manually revoked |
| Meaning of value | sweep configuration; due when `value <= cutoff` |
| Direct index | none |
| Conditional index | `(tenant, valid_until)` where live and non-null |

Place the field beside creation attribution and reason so lifecycle data remains
cohesive; those fields currently occupy `organization/models.py:1330-1347`.
Do not change `is_active`, managers, checks, unique constraints, or the approved
resource allowlist.

### 3.2 Operational models

Place the run and evidence models beside `TenantResourceGrant` in
`organization/models.py`; they are part of the same security-critical lifecycle,
not generic task infrastructure. Add both model labels to the capability's
`owns` tuple, which currently names only `organization.TenantResourceGrant`
(`organization/apps.py:62-74`). This keeps capability ownership and U6 drift
checks accurate.

The regular operator list/detail belongs in
`organization/views/resource_grant_views.py`, next to the existing grant list,
create, and revoke flows (`organization/views/resource_grant_views.py:1-7`).
The admin registration remains read-only for outcome/evidence fields; the
existing grant admin already demonstrates intentional `_base_manager` use for
revoked rows (`organization/admin.py:112-132`).

### 3.3 Forward migration

Generate, do not hand-write, the next organization migration after
`organization/migrations/0102_alter_tenantresourcegrant_options.py:1-18`.
It performs schema-only additions:

- nullable `valid_until`;
- the static conditional expiry index;
- expiry run and evidence tables with their constraints and indexes.

There is no data migration and no default. PostgreSQL therefore reports NULL for
every existing grant. A migration test creates realistic direct, group, active,
and revoked grants in the old state, migrates forward, and asserts every
`valid_until` is null, every original primary key and `deleted_at` survives, and
assignment provenance remains unchanged.

The first coordinator/sweep against that forward-migrated fixture must create a
truthful skipped/no-due run with zero eligible and zero revoked rows. This is the
proof that deployment cannot revoke historical grants merely by enabling the
worker.

### 3.4 Reverse migration

The reverse removes the two operational tables, the expiry index, and the
nullable field. It does not touch `TenantResourceGrant.deleted_at` or any asset
assignment provenance.

The serial migration test follows the repository's `MigrationExecutor` pattern:
migrate to the old state, create rows with historical models, migrate forward,
assert, migrate back, assert again, and restore the latest state. The existing
pattern is at `subscriptions/tests/test_migrations.py:7-50`.

Run a second reverse scenario after an expiry sweep. The grant remains revoked
after the field is removed. The test also proves the pre-existing assignment's
grant provenance is not rewritten. Removing the operational tables necessarily
removes their run detail, but the separately retained `ObjectChange` remains
subject to its normal retention policy; reverse migration is not rollback.

### 3.5 Constraint and state invariants

| Invariant | Enforcement |
|---|---|
| Exactly one grantee | Existing check constraint, unchanged (`organization/models.py:1351-1358`). |
| Owner differs from direct grantee | Existing check constraint, unchanged (`organization/models.py:1361-1363`). |
| Only one live equivalent grant | Existing two partial unique constraints, unchanged (`organization/models.py:1360-1377`). |
| Live means one thing | `deleted_at IS NULL`; default manager and `is_active` only. |
| Deadline never enters an index clock predicate | Static `valid_until IS NOT NULL` only. |
| Existing rows remain perpetual | Nullable addition with no default/backfill. |
| Every expiry delete has one change | evidence row has one-to-one `ObjectChange`; transaction rolls back if missing. |
| Evidence identity matches run/grant/change | Derived manager scope uses `run__tenant`; every exposed read/count uses `integrity_valid()` to require run/grant tenant equality and the exact linked change tenant/type/object/action/request identity; mismatches are excluded and redacted. |
| Run scope is never global | non-null owner tenant, tenant-scoped manager, `deny_global_tenant=True`. |

## 4. Sweep design

### 4.1 Task boundaries

Add a coordinator and one per-tenant task under `core/tasks/`, matching the
repository's django-q2 task placement. Cross-tenant task code already enumerates
unscoped IDs and enters `TaskContext` for each tenant at
`subscriptions/tasks.py:90-156`. The coordinator remains actorless and performs
no grant mutation; each tenant task has one explicit owner boundary.

The tenant task signature contains only stable primitive identifiers:

```text
sweep_expired_resource_grants(
    tenant_id: int,
    run_id: int,
    generation: int,
) -> TaskResult
```

It reloads the run and tenant through unscoped base managers, verifies that the
run tenant equals the argument and that the delivered generation claims the
queued row, then enters `TaskContext`. A mismatched tenant on an existing run
is completed terminal/redacted by generation CAS; a missing run has no row to
show and is observable only through the typed task result and structured task
log. Neither case falls back to a tenantless context. `TaskContext` explicitly
treats a bad target identifier as fatal rather than global at
`core/tasks/context.py:196-202`.

**Synthetic request ID on failure paths.** `TaskContext.__enter__()` clears
the ambient user and tenant before resolution, but currently installs the
synthetic `_request_id` only after `_resolve_principal_and_tenant()` succeeds
(`core/tasks/context.py:110-142`), and its setup-failure logger uses
`self.log_context` before restoring the outer context
(`core/tasks/context.py:148-155`). During synchronous execution inside a web
request, a bad tenant/run path would therefore log the *human* request's ID
as if it belonged to the failed task. WP-22 must fix `TaskContext` so a fresh
synthetic request ID — or an explicit clear — is installed **before**
resolution and restored on failure, and must add a nested/synchronous
bad-tenant test proving the outer request ID is neither logged nor persisted
and that no system authorization can be issued from a failed entry.

### 4.2 Eligibility query

For run cutoff `C` and owner tenant `T`, candidates are exactly:

```text
TenantResourceGrant._base_manager.filter(
    tenant_id=T,
    deleted_at__isnull=True,
    valid_until__isnull=False,
    valid_until__lte=C,
)
```

`<=` makes exactly-due rows eligible. The explicit `tenant_id` prevents ambient
or manager changes from widening the mutation. `_base_manager` is used because
the code must recheck already-revoked state under a row lock; the initial query
still states `deleted_at__isnull=True` explicitly.

Before mutation, the service verifies:

- the tenant is still live;
- the row is still live and owner-matched;
- the deadline is present and still `<= C`;
- exactly one grantee is structurally present;
- the GenericFK content type is in the closed allowlist;
- the issued system authorization is valid for the exact permission and
  operation.

The target resource is deliberately **not** part of expiry eligibility. A
stock pool can legitimately be moved or deleted while its grant is overdue;
expiry implies revocation, and resolver denial caused by a moved/missing
target is not a substitute for committing the revocation transition. The
model's `clean()` already skips the target/ownership check once `deleted_at`
is set (`organization/models.py:1439-1455`), which is exactly the property
the sweep relies on: the soft-delete save of a stale-target row is valid.
Ordinary target disappearance or movement therefore revokes like any other
due grant (S21). Only genuinely corrupted rows that cannot undergo the
audited transition at all (for example a grantee structure that violates the
exactly-one-grantee invariant) are left live with a redacted stable terminal
code and an explicit remediation contract — the operator re-creates the grant
or repairs the row through a reviewed data-fix path; the sweep never repairs
malformed data itself (S22).

### 4.3 Outcome taxonomy and retry policy

Use the existing `TaskStatus` values — success, partial, skipped, retryable, and
terminal — from `core/tasks/utils.py:30-46`. Do not add another semantic error
taxonomy.

Stable task codes:

| Outcome | Code | Meaning |
|---|---|---|
| `success` | `resource_grant_expiry_succeeded` | Every eligible valid row was revoked. |
| `skipped` | `resource_grant_expiry_no_due` | No eligible live row existed at the cutoff. |
| `partial` | `resource_grant_expiry_partial` | Safe rows were revoked; malformed/permanent rows were skipped. |
| `retryable` | `resource_grant_expiry_db_retry` | A classified transient boundary interrupted the run. |
| `terminal` | `resource_grant_expiry_tenant_unresolvable` | Tenant/run scope could not be proven. |
| `terminal` | `resource_grant_expiry_invalid_grant` | Every candidate was permanently malformed or unauthorized. |
| `terminal` | `resource_grant_expiry_terminal` | Other permanent failure, with a safe generic message. |

`classify_task_error()` recognizes only the narrow transient boundary set and
otherwise returns terminal (`core/tasks/utils.py:75-84`). The task catches at its
outer boundary, classifies, writes the run status, logs only exception type plus
stable code, and raises `RetryableTaskError` or `TerminalTaskError` with a safe
message. Existing tests require a database `OperationalError` to be retryable
without raw secret text and data errors to be terminal
(`core/tests/test_task_error_contracts.py:103-128`).

Retry policy is finite: three one-shot retries after 60, 120, and 240 seconds,
all referencing the same run ID/cutoff but carrying the newly incremented
generation. The one-shot schedule approach has an existing precedent at
`core/tasks/webhooks.py:345-410`. `attempt_count` increments only when a
worker successfully claims queued→running; the retry CAS stores
`next_retry_at`, increments generation, and dispatches that exact generation
only on commit. After the third retryable failure, a running+generation CAS
completes the run terminal with code
`resource_grant_expiry_retry_exhausted` and no raw database message.

The cluster timeout/retry interval is 600/660 seconds
(`core/settings/base.py:463-480`), so a crashed or timed-out delivery can also be
redelivered by django-q2. Generation claim plus grant row locks/live-state
rechecks make that delivery safe. The next hourly coordinator repairs an
expired running lease or stale queued dispatch by the generation-incrementing
CAS in D4, never by creating a second run for the same slot.

### 4.4 Transaction and count semantics

Counts are recomputed from durable evidence plus the current fixed-cutoff due
query, not incremented optimistically. This prevents a crash between row
commit and run completion from double-counting on retry:

- `revoked_count`: `integrity_valid()` evidence rows linked to this run;
- `remaining_due_count`: currently live owner rows whose non-null deadline is
  still `<= run.cutoff` after the attempt; and
- `invalid_count`: the subset of `remaining_due_count` that fails the
  structural checks and therefore needs remediation.

The design intentionally drops `eligible_count` and
`already_revoked_count`: a candidate discovered by a losing overlapping run
is not reconstructible without a durable candidate ledger, and the issue does
not require such a ledger. A losing run truthfully reports zero revocations
and its current remaining-due result; it never invents a historical count.

`started_at` is the first attempt start. `last_attempt_at` changes for each
delivery. `finished_at` is set only for success, partial, skipped, or terminal.
A retryable row remains queued with a visible `next_retry_at` and last safe
error.

### 4.5 Boundary and concurrency matrix

This table is the normative sweep behavior and is mirrored exactly by the test
plan in section 6.2.

| ID | Setup at fixed cutoff `C` | Expected mutation | Expected audit/run result |
|---|---|---|---|
| S1 | `valid_until=NULL`, live, tenant A | none | skipped/no-due; zero ObjectChange |
| S2 | `valid_until>C`, live, tenant A | none | skipped/no-due; zero ObjectChange |
| S3 | `valid_until=C`, live, tenant A | soft-delete once | success; one evidence row and one delete ObjectChange |
| S4 | `valid_until<C`, live, tenant A | soft-delete once | success; triggering deadline preserved |
| S5 | overdue but already deleted | none | not eligible; no new ObjectChange |
| S6 | tenant B overdue while sweeping A | none to B | A run cannot count, name, or mutate B row |
| S7 | overdue malformed/unsupported row | leave live | partial if peers succeed, otherwise terminal; redacted code |
| S8 | unrelated model row with a deadline-like field | none | never queried or reported |
| S9 | successful run followed by another run | none on second run | second skipped/no-due; no duplicate change/evidence |
| S10 | two workers/runs race for one due grant | one soft-delete total | one delete ObjectChange; losing run records zero revocations and no invented historical count |
| S17 | same run delivered twice (duplicate queue delivery) | one soft-delete total | second delivery claims/updates nothing; run outcome immutable; one ObjectChange/evidence |
| S18 | stale attempt completion after a newer attempt claimed the run | none | rejected; run keeps the newer attempt's outcome and timestamps |
| S19 | coordinator commits run but enqueue fails after `on_commit` | none until retried | run visible as `enqueue_failed`; next coordinator generation-CAS dispatches once |
| S23 | enqueue fails and persisting `enqueue_failed` is blocked by the same transient DB outage | none until repaired | row remains queued; after `dispatch_stale_at` coordinator increments generation and dispatches; any old generation is stale |
| S20 | status-write failure on a completed run | revocation already committed | outcome preserved on retry via recomputation; no duplicate change |
| S21 | overdue grant whose target was moved or deleted | soft-delete once | revokes normally; no target-validity requirement |
| S22 | corrupted row (grantee structure violates exactly-one-grantee) | leave live | terminal with stable redacted code; remediation contract documented |
| S11 | transient DB failure after partial commits | retry remaining rows | retryable then success/partial; committed rows not duplicated |
| S12 | missing, deleted, or mismatched tenant/run | none | existing mismatched run becomes terminal/redacted; missing run yields typed terminal task result/log only (no fictitious UI row) |
| S13 | direct/group grant plus RBAC before deadline | access before; denial after sweep | superuser without live grant also denied after sweep |
| S14 | assignment created through grant before expiry | assignment unchanged | provenance grant remains readable as permitted history |
| S15 | forward-migrated historical grants, all NULL | none | first sweep skipped/no-due |
| S16 | one expired grant restored with corrected/NULL deadline | clear `deleted_at` once | human restore change; access returns only if RBAC still permits |

### 4.6 In-product observability

Add an owner-tenant, read-only “Resource grant expiry runs” list and detail page.
The list shows schedule slot, cutoff, state/outcome, attempt count, counts,
started/finished/next-retry times, and stable error code/message. The detail
lists evidence rows and links each to the existing grant audit detail.

Visibility requires the same owner-container view permission as the audit API;
grantees can audit grants involving them but do not see the owner's operational
run aggregation. This avoids revealing the owner's unrelated grant counts. An
unbound platform superuser may view all runs; a tenant-bound superuser sees only
that tenant's runs, matching D8.

The existing grant list explicitly scopes owner/grantee/group involvement and
fails unrelated users closed (`organization/views/resource_grant_views.py:34-59`).
The new run UI uses tenant-scoped run managers rather than reusing the unscoped
grant manager.

## 5. Audit API contract

### 5.1 Pagination and neutral failures

Normal DRF pagination applies after visibility and filters. A hidden row and a
nonexistent row are indistinguishable on detail: HTTP 404 with DRF semantic code
`not_found`. A filter containing another tenant's identifiers returns HTTP 200
with an empty page and count zero; it does not return 403, because 403 would
confirm the hidden identifier exists. Missing read permission returns 403
`permission_denied`; unauthenticated access returns 401 `not_authenticated`.
Unsupported mutation methods return 405 `method_not_allowed`.

The API response never resolves `resource_type/resource_id` to the target model,
which is important because the grant's GenericFK target can belong to another
container. The model deliberately disables generic export
(`organization/models.py:1272-1276`); the audit endpoint is the one reviewed,
scoped exception, not permission to re-enable generic paths.

### 5.2 Normative response matrix

This table is the complete endpoint behavior and is mirrored exactly by the API
test plan in section 6.3.

| ID | Caller and row/query | List | Detail | Required result |
|---|---|---:|---:|---|
| A1 | owner A, active direct grant | 200 / included | 200 | `state=active`, visible fields only |
| A2 | direct grantee B, active tenant grant | 200 / included | 200 | same row; no target resource name resolution |
| A3 | tenant C in grantee group or descendant, active group grant | 200 / included | 200 | ancestry uses live canonical helper |
| A4 | owner A, manually revoked grant | 200 / included | 200 | `state=revoked`, `kind=manual`, human user ID if retained |
| A5 | grantee B/C, sweep-revoked grant | 200 / included | 200 | `kind=expiry`, `user_id=null`, deadline/run/request/time present |
| A6 | unrelated tenant D guesses grant ID | 200 / absent | 404 `not_found` | no existence, field, reason, or resource disclosure |
| A7 | unrelated tenant D lists without filters | 200 / empty | n/a | count zero for its visible set only |
| A8 | authorized caller filters on unrelated owner/grantee/group/resource ID | 200 / empty | n/a | count zero; no name/reason/count oracle |
| A9 | authenticated caller lacks view permission | 403 | 403 | `permission_denied`; queryset content not consulted |
| A10 | any caller uses POST/PUT/PATCH/DELETE | 405 | 405 | `method_not_allowed`; no model mutation |
| A10a | unauthenticated caller uses a mutation method | 401 | 401 | authentication runs before method dispatch; `not_authenticated` wins over 405 |
| A10b | authenticated caller lacks permission and uses a mutation method | 403 | 403 | `permission_denied` wins over 405; the method-dispatch 405 is only reachable after auth+permission pass |
| A11 | explicit platform superuser unbound / tenant-bound | 200 / global or involved | 200 if in selected scope | unbound is platform-global; bound is container-limited |
| A11a | group-bound superuser (active group, no active tenant) | 200 / group subtree only | 200 if in subtree | selected group subtree, never global |
| A11b | token-bound to A; caller has view permission in A and another tenant B | 200 / A-involved rows only | 200 only for A-involved row; B-only row is 404 | token pin wins even though the user can access B |
| A11c | token-bound to A; caller has view permission only in B | 403 | 403 | token-tenant permission is required before queryset evaluation |
| A11d | internally contradictory token/current-tenant state | 403 | 403 | fail closed before queryset evaluation; scopes are never merged |
| A12 | authorized tenant with no grants | 200 / empty | 404 for guesses | Stable core unchanged; no implicit access |
| A13 | unauthenticated caller | 401 | 401 | `not_authenticated`; no count or object lookup |
| A14 | revoked row whose delete `ObjectChange` was pruned by retention | 200 / included | 200 | `kind=unknown`; `request_id`/`time` nullable; never fabricated history |
| A15 | expiry evidence exists but its `ObjectChange` link is null (pruned) | 200 / included | 200 | `kind=unknown` fallback, redacted; run/deadline fields still from evidence |
| A16 | manual delete change whose user FK was cleared by user deletion | 200 / included | 200 | `kind=unknown`, `user_id=null`; `user_name` is display history, not a reserved actor-kind marker |
| A17 | actorless delete history without integrity-valid expiry evidence | 200 / included | 200 | `kind=unknown`; no expiry/manual claim and no fabricated user |
| A18 | expiry-revoked, restored, then manually re-revoked grant | 200 / included | 200 | `kind=manual` with the current delete change; earlier expiry evidence is not reported for the current revocation |
| A19 | expiry-revoked, restored, then expired again | 200 / included | 200 | `kind=expiry` with the new run/deadline; earlier expiry evidence is ignored |

### 5.3 OpenAPI and generated-client integration

The view, serializer, filter parameters, pagination, nested revocation object,
and error statuses must be explicit enough for drf-spectacular to produce zero
new warning/error identities. Review and update:

- `itambox/schema.yaml`;
- the generated TypeScript declaration used by the smoke path; and
- runtime API tests for every A-row.

Do not add a diagnostic identity to
`scripts/openapi_diagnostics_baseline.json`; new warnings are forbidden by
policy (`docs/development/openapi-schema-policy.md:62-65`). Run
`make openapi-write` on canonical Linux/Python 3.12, then
`make openapi-check` as documented at
`docs/development/openapi-schema-policy.md:108-126`. Review paths, methods,
operation IDs, components, parameters, and response status codes as external
contract surfaces (`docs/development/openapi-schema-policy.md:35-42`).

Extend the supported generated-client smoke so the typed client performs audit
list and detail GETs and has no generated mutation method for this path. The
existing client workflow and pinned generator are described at
`docs/development/openapi-schema-policy.md:44-60`.

### 5.4 Generic bypass resistance

Keep `generic_export_allowed=False`. Add regression coverage that generic API,
search, and export discovery treats the audit endpoint as the only grant audit
path and cannot instantiate an unscoped generic grant queryset. The existing
model test already proves generic export fails closed at
`organization/tests/test_resource_grants.py:264-303`.

Any future generic surface must recognize the model as one requiring
`visible_to_containers()` through
`is_container_scoped_unfiltered()`
(`organization/services/resource_access.py:74-76`). It may not copy the audit
query or use `TenantResourceGrant._base_manager` directly.

## 6. Test plan

All tests use PostgreSQL and pytest-django. Migration and race tests carry
`@pytest.mark.serial_only`; that marker is reserved for migration/global/race
semantics at `pyproject.toml:105-118`. Per-worker test databases remain unique
under xdist as configured at `conftest.py:23-34` and `conftest.py:56-85`.

### 6.1 Model and form tests

Extend `organization/tests/test_resource_grants.py`, whose current cases cover
validation, active uniqueness, revoke-then-regrant, parent deletion, and generic
export (`organization/tests/test_resource_grants.py:59-303`). Assert:

- `valid_until` defaults to null;
- future and null values pass full validation;
- `is_active` ignores the deadline and follows only `deleted_at`;
- both existing unique constraints behave identically before and after the field;
- the expiry index definition has no `Now`/clock expression;
- approved models and grantee checks are unchanged.

Extend `organization/tests/test_resource_grant_views.py`, whose current create
and owner-only revoke coverage is at
`organization/tests/test_resource_grant_views.py:108-202`. Assert:

- blank creates a perpetual direct or group grant;
- a future aware deadline is saved;
- exactly-now/past form input is rejected without creating a grant;
- owner/resource/grantor remain server-controlled;
- no edit view is introduced;
- grantee and unrelated users cannot set or change the deadline.

### 6.2 Sweep boundary/concurrency tests

Create `organization/tests/test_resource_grant_expiry.py`. This table repeats
section 4.5 exactly and is the required parametrized/transactional suite.

| ID | Setup at fixed cutoff `C` | Expected mutation | Expected audit/run result |
|---|---|---|---|
| S1 | `valid_until=NULL`, live, tenant A | none | skipped/no-due; zero ObjectChange |
| S2 | `valid_until>C`, live, tenant A | none | skipped/no-due; zero ObjectChange |
| S3 | `valid_until=C`, live, tenant A | soft-delete once | success; one evidence row and one delete ObjectChange |
| S4 | `valid_until<C`, live, tenant A | soft-delete once | success; triggering deadline preserved |
| S5 | overdue but already deleted | none | not eligible; no new ObjectChange |
| S6 | tenant B overdue while sweeping A | none to B | A run cannot count, name, or mutate B row |
| S7 | overdue malformed/unsupported row | leave live | partial if peers succeed, otherwise terminal; redacted code |
| S8 | unrelated model row with a deadline-like field | none | never queried or reported |
| S9 | successful run followed by another run | none on second run | second skipped/no-due; no duplicate change/evidence |
| S10 | two workers/runs race for one due grant | one soft-delete total | one delete ObjectChange; losing run records zero revocations and no invented historical count |
| S17 | same run delivered twice (duplicate queue delivery) | one soft-delete total | second delivery claims/updates nothing; run outcome immutable; one ObjectChange/evidence |
| S18 | stale attempt completion after a newer attempt claimed the run | none | rejected; run keeps the newer attempt's outcome and timestamps |
| S19 | coordinator commits run but enqueue fails after `on_commit` | none until retried | run visible as `enqueue_failed`; next coordinator generation-CAS dispatches once |
| S23 | enqueue fails and persisting `enqueue_failed` is blocked by the same transient DB outage | none until repaired | row remains queued; after `dispatch_stale_at` coordinator increments generation and dispatches; any old generation is stale |
| S20 | status-write failure on a completed run | revocation already committed | outcome preserved on retry via recomputation; no duplicate change |
| S21 | overdue grant whose target was moved or deleted | soft-delete once | revokes normally; no target-validity requirement |
| S22 | corrupted row (grantee structure violates exactly-one-grantee) | leave live | terminal with stable redacted code; remediation contract documented |
| S11 | transient DB failure after partial commits | retry remaining rows | retryable then success/partial; committed rows not duplicated |
| S12 | missing, deleted, or mismatched tenant/run | none | existing mismatched run becomes terminal/redacted; missing run yields typed terminal task result/log only (no fictitious UI row) |
| S13 | direct/group grant plus RBAC before deadline | access before; denial after sweep | superuser without live grant also denied after sweep |
| S14 | assignment created through grant before expiry | assignment unchanged | provenance grant remains readable as permitted history |
| S15 | forward-migrated historical grants, all NULL | none | first sweep skipped/no-due |
| S16 | one expired grant restored with corrected/NULL deadline | clear `deleted_at` once | human restore change; access returns only if RBAC still permits |

S10 and S11 are serial transaction tests with independent database connections,
barriers, and deterministic cutoffs; they must not use mocks to simulate the row
lock. S11 injects a real classified database boundary at a controlled service
seam, then invokes the retry with the same run. The assertions count matching
`ObjectChange` and evidence rows, not merely final model state.

S17-S20 and S23 exercise the persisted state machine, not a Python-only mock:
they assert the worker receives `generation`, only one queued+generation claim
changes a row, stale completion changes zero rows, expired running leases and
stale queued dispatches increment generation, and complete outcomes remain
immutable. Model-constraint tests attempt every invalid state/outcome/timestamp
combination in D7 and require PostgreSQL rejection. Separate bulk-corruption
tests build evidence with mismatched run/grant tenant, changed-object type/ID,
action, and request ID and prove `integrity_valid()` excludes each from the
operator UI, admin, API, and every run count while null-ObjectChange retention
evidence remains visible only as `kind=unknown`.

Every S3/S4/S10/S11 expiry change asserts all required audit fields:

- `user_id is None`;
- owner `tenant_id`;
- grant content type and object ID;
- delete transition with pre/post `deleted_at`;
- `deleted_at` and `ObjectChange.time` are generated independently
  (`SoftDeleteMixin.soft_delete()` and `_log_change` each call
  `timezone.now()`), so the assertion is: evidence `revoked_at` equals the
  chosen canonical source (`deleted_at` of the soft-delete save), and
  `ObjectChange.time` is ordered after that value within the same committed
  operation — never exact equality between the two independently generated
  timestamps;
- non-null synthetic request ID equal to evidence `request_id`; and
- pre-change `valid_until` equal to evidence `triggering_valid_until`.

This assertion exercises the synchronous audit write in
`ChangeLoggingMixin._log_change`, which requires a request ID and derives the
tenant from the object or ambient context (`core/models.py:272-352`). It must
also assert there is no user row pretending to be “System.”

### 6.3 Audit API tests

Create/extend a focused organization API test module. This table repeats section
5.2 exactly; a test case exists for every row.

| ID | Caller and row/query | List | Detail | Required result |
|---|---|---:|---:|---|
| A1 | owner A, active direct grant | 200 / included | 200 | `state=active`, visible fields only |
| A2 | direct grantee B, active tenant grant | 200 / included | 200 | same row; no target resource name resolution |
| A3 | tenant C in grantee group or descendant, active group grant | 200 / included | 200 | ancestry uses live canonical helper |
| A4 | owner A, manually revoked grant | 200 / included | 200 | `state=revoked`, `kind=manual`, human user ID if retained |
| A5 | grantee B/C, sweep-revoked grant | 200 / included | 200 | `kind=expiry`, `user_id=null`, deadline/run/request/time present |
| A6 | unrelated tenant D guesses grant ID | 200 / absent | 404 `not_found` | no existence, field, reason, or resource disclosure |
| A7 | unrelated tenant D lists without filters | 200 / empty | n/a | count zero for its visible set only |
| A8 | authorized caller filters on unrelated owner/grantee/group/resource ID | 200 / empty | n/a | count zero; no name/reason/count oracle |
| A9 | authenticated caller lacks view permission | 403 | 403 | `permission_denied`; queryset content not consulted |
| A10 | any caller uses POST/PUT/PATCH/DELETE | 405 | 405 | `method_not_allowed`; no model mutation |
| A10a | unauthenticated caller uses a mutation method | 401 | 401 | authentication runs before method dispatch; `not_authenticated` wins over 405 |
| A10b | authenticated caller lacks permission and uses a mutation method | 403 | 403 | `permission_denied` wins over 405; the method-dispatch 405 is only reachable after auth+permission pass |
| A11 | explicit platform superuser unbound / tenant-bound | 200 / global or involved | 200 if in selected scope | unbound is platform-global; bound is container-limited |
| A11a | group-bound superuser (active group, no active tenant) | 200 / group subtree only | 200 if in subtree | selected group subtree, never global |
| A11b | token-bound to A; caller has view permission in A and another tenant B | 200 / A-involved rows only | 200 only for A-involved row; B-only row is 404 | token pin wins even though the user can access B |
| A11c | token-bound to A; caller has view permission only in B | 403 | 403 | token-tenant permission is required before queryset evaluation |
| A11d | internally contradictory token/current-tenant state | 403 | 403 | fail closed before queryset evaluation; scopes are never merged |
| A12 | authorized tenant with no grants | 200 / empty | 404 for guesses | Stable core unchanged; no implicit access |
| A13 | unauthenticated caller | 401 | 401 | `not_authenticated`; no count or object lookup |
| A14 | revoked row whose delete `ObjectChange` was pruned by retention | 200 / included | 200 | `kind=unknown`; `request_id`/`time` nullable; never fabricated history |
| A15 | expiry evidence exists but its `ObjectChange` link is null (pruned) | 200 / included | 200 | `kind=unknown` fallback, redacted; run/deadline fields still from evidence |
| A16 | manual delete change whose user FK was cleared by user deletion | 200 / included | 200 | `kind=unknown`, `user_id=null`; `user_name` is display history, not a reserved actor-kind marker |
| A17 | actorless delete history without integrity-valid expiry evidence | 200 / included | 200 | `kind=unknown`; no expiry/manual claim and no fabricated user |
| A18 | expiry-revoked, restored, then manually re-revoked grant | 200 / included | 200 | `kind=manual` with the current delete change; earlier expiry evidence is not reported for the current revocation |
| A19 | expiry-revoked, restored, then expired again | 200 / included | 200 | `kind=expiry` with the new run/deadline; earlier expiry evidence is ignored |

For A6-A8, capture query responses and assert the hidden owner's name, grantee
name, group name, resource ID, reason text, deadline, run ID, and total hidden
count do not occur anywhere. For A10, test collection and detail routes plus
router metadata/OPTIONS; do not settle for asserting that the serializer is
read-only.

### 6.4 Resolver, provenance, alerting, and no-grant tests

Rerun and extend the WP-21 adversarial suite at
`inventory/tests/test_tenant_resource_grant_security.py:194-258` for S13 and
S14. Before the deadline, direct/group access succeeds only with RBAC. After the
sweep, the unchanged resolver denies because the default manager no longer sees
the row. A superuser still has no grant bypass. Existing assignments retain the
grant foreign key and remain readable history according to normal permissions.

Add alert pipeline assertions beside the existing change/alert suites:

- grant create emits one owner-tenant create event;
- manual revoke emits one owner-tenant delete event with human audit user;
- expiry emits one owner-tenant delete event with null audit user;
- rollback emits one restore event with human audit user;
- no tenant-scoped delivery for an unrelated or grantee tenant is created;
- message/template context contains no resource name, reason, or foreign tenant
  detail;
- dispatch failure does not roll back an already-committed revocation and is
  visible through existing delivery observability.

The append-only event model and actions are defined at
`extras/models.py:229-266`; durable typed alert delivery state is at
`extras/models.py:1536-1628`. Reuse the current alert behavior suites rather
than create a separate notification subsystem.

The capability cannot literally be deactivated: it is Stable,
security-critical, `ALWAYS_ON`, and has no probe
(`organization/apps.py:62-74`). Therefore U6 for WP-22 has two parts:

1. retain the registry harness proof that security-critical capabilities have no
   deactivation path; the harness intentionally excludes Stable/always-on keys
   (`itambox/tests/capability_harness.py:1-32`); and
2. run the inactive-data/no-grant state with no resource grants and with all
   historical deadlines null, proving the Stable core and unrelated stock CRUD
   are unchanged.

This corrects the phrase “inactive/no-grant” without weakening U6 by pretending
the security-critical capability can be switched off.

### 6.5 Migration, rollback, and retention tests

Add `organization/tests/test_resource_grant_migrations.py`, serial-only, with:

- forward migration of realistic active/revoked direct/group grants;
- all existing deadlines null and first sweep no-op;
- row IDs, `deleted_at`, and assignment provenance unchanged;
- reverse to old schema with rows/provenance intact;
- forward, expiry revoke, reverse: revoked state remains;
- restoration only through the one-row runbook service, with a new human restore
  change and prior system delete change retained;
- conflict with an active replacement grant fails without partial restoration.

Extend prune tests to prove expiry runs/evidence use the same tenant override,
global fallback, and zero/legal-hold semantics as changelog rows. The pruner's
current class selection and per-tenant behavior are explicit at
`core/management/commands/prune_changelog.py:31-32` and
`core/management/commands/prune_changelog.py:307-370`.

Retention tests additionally assert: queued, running, retryable-queued, and
`enqueue_failed` runs survive a prune pass; a terminal run is aged by
`finished_at` (a late-completed run is
not pruned early); deletion removes evidence before `ObjectChange`; and the
audit API keeps returning `kind=unknown` rows (A14/A15) during any temporary
`ObjectChange`-null interval instead of failing.

### 6.6 Mandatory manifest and selector ripple

The WP-21 security document's mandatory selector begins at
`docs/development/tenant-resource-grant-security.md:140` and continues through
`docs/development/tenant-resource-grant-security.md:276`. Implementation must:

1. enumerate every Python test file changed or added by WP-22;
2. add each to both `changed_tests` and `mandatory_tests` in
   `scripts/resource_grant_test_manifest.json`;
3. keep mandatory files first and in the exact order documented;
4. update the documented selector to equal `mandatory_tests`; and
5. update manifest provenance/base commit according to the gate's reviewed
   workflow.

The import-boundary gate asserts the documented selector equals mandatory and
that the documented set equals changed plus baseline at
`core/tests/test_import_boundaries.py:944-959`. It also compares Git-detected
changed tests to the manifest at `core/tests/test_import_boundaries.py:964-987`.
Preserve LF and the manifest's current one-space indentation.

### 6.7 Verification commands and coverage

The implementation verification set is:

1. focused organization model/form/view/API/expiry/migration tests;
2. `inventory/tests/test_tenant_resource_grant_security.py`;
3. the complete selector in `tenant-resource-grant-security.md`;
4. `core/tests/test_task_error_contracts.py` and queue-failure tests;
5. alert/change-log suites, including issue #183/#185 coverage;
6. the U6 capability harness and no-grant scenario;
7. both xdist and serial security lanes with no skips;
8. migration forward/reverse rehearsal on a fresh PostgreSQL database;
9. `make openapi-write`, `make openapi-check`, and generated-client smoke;
10. lint, format, typing, architecture, exception-policy, and contract gates;
11. `make coverage-diff`.

The differential gate is branch-aware and fails closed for unmeasured changed
production files (`scripts/check_diff_coverage.py:2-24`); the policy target is
85 percent at `scripts/coverage_policy.py:84`. New eligibility, retry,
permission, redaction, and rollback branches therefore need direct assertions,
not incidental execution.

## 7. Acceptance mapping

| Issue acceptance evidence | Concrete mechanism | Required test evidence |
|---|---|---|
| Forward/reverse migration; old rows NULL; first sweep no-op; rows/provenance survive | Nullable no-default field; schema-only forward; reverse never edits grant/assignment rows | Migration suite section 6.5 plus S15 |
| Future, exactly due, overdue, NULL, already revoked, duplicate workers, transient retry, second-run idempotency | Fixed cutoff, `<=`, live predicate, row lock/recheck, finite generation-bound retry | All S1-S23 rows, especially S3/S4/S9-S11/S17-S23 |
| Each expiry writes one ObjectChange with null user, tenant, grant, transition, time, deadline | Entered actorless TaskContext; exact system authorization; synchronous change plus linked evidence in one transaction | Required audit assertions following matrix S |
| API owner/grantee/group visibility, unrelated/revoked/guessed/filter/permission/method negatives, and restore/re-revoke classification | `_base_manager` then request-scope-aware `visible_to_containers`; read-only viewset and dedicated permission; current-revocation-bound classification | All A1-A19 rows, including A10a/b, A11a-d, and A18/A19, exactly |
| Zero-diagnostics API baseline | Explicit serializer/filter/status schema; no new diagnostic identity | `make openapi-write/check`, schema review, typed-client GET smoke |
| Per-row rollback clears deleted state and restores only with RBAC | Locked combined deadline correction + restore; human TaskContext; no bulk operation | S16 plus migration/rollback suite |
| Dropping field does not restore | Reverse migration removes schema only | reverse-after-expiry migration case |
| Historical ObjectChange survives rollback | Restore adds a new change and never edits prior delete change | S16 and explicit two-change assertion |
| Tenant A sweep cannot touch B/future/NULL/deleted/malformed/unrelated | Explicit owner filter plus row-level full recheck and fail-closed invalid handling | S1/S2/S5-S8/S12 |
| Grant + RBAC matrix before/after, including superuser | Resolver remains clock-free and sees only live manager rows | S13 and WP-21 adversarial rerun |
| Assignment provenance remains readable history | Sweep updates grant `deleted_at` only | S14 and migration fixture assertions |
| Generic API/search/export cannot bypass visibility | Generic export remains disabled; unfiltered model registry requires helper | A6-A8 and generic bypass regression |
| Second run and concurrent workers do not duplicate changes | Live row lock/recheck; one-to-one change evidence; deterministic run key | S9/S10 |
| Transient failures use typed retry without double writes | Existing classifier/errors; same run/cutoff; finite one-shot retries | S11 plus task-error contract suite |
| Permanent tenant/data failures fail closed and are visible | Terminal stable codes; no raw exception; durable status when the run exists; typed task log when it does not | S7/S12, run UI tests, and missing-run task-log test |
| Run start/end/outcome/count and redacted error visible in product | Tenant-scoped run/evidence models and read-only owner UI | run list/detail permission and rendering tests |
| Notifications do not leak or recurse cross-tenant | Existing owner-tenant Event pipeline; minimal payload; no resource resolution or grantee fanout | lifecycle alert assertions section 6.4 |
| U6 inactive/no-grant state leaves Stable core unchanged | Retain always-on registry proof; add empty/all-NULL data scenario | U6 harness plus A12/S1/S15 |
| WP-21 selector, queue failures, targeted tests, serial suite, migration rehearsal, coverage diff pass | Manifest/doc ripple and verification sequence | section 6.6/6.7 gate results |

**Decision-to-test-to-acceptance crosswalk.** Every decision has a named
test and an acceptance row; stable IDs (`S#`/`A#`) are used throughout:

| Decision | Named tests | Acceptance row |
|---|---|---|
| D1 nullable deadline, single liveness | §6.1 model/form tests; S1/S2/S15 | migration evidence; first-sweep no-op |
| D2 clock-free sweep index | §6.1 index-predicate test; migration suite | zero-diagnostics + migration evidence |
| D3 UI form deadline for both grantee kinds | §6.1 view/form tests | UI/form acceptance evidence in §6.1 |
| D4 hourly coordinator, duplicate collapse, stale repair, enqueue/status failure | S17-S20/S23; schedule registration tests (unique schedule, no duplicate rows) | §10 rollout step 5; run UI tests |
| D5 exact system authorization, task-supplied reason | task-error contract suite; §6.2 audit assertions | one-ObjectChange audit assertion |
| D6 shared revoke service, row locks, claim protocol, stale-target/corrupt-row behavior | S9/S10/S17/S18/S20-S22; manual-vs-sweep race test | second-run/concurrency and fail-closed data evidence |
| D7 run/evidence models, integrity-valid reads, terminal-only retention | §6.5 retention tests including `enqueue_failed`; bulk corruption tests; A14/A15 | run observability evidence |
| D8 request-scope-aware audit visibility | All A1-A17 rows including A11a-d | audit negatives evidence |
| D9 lifecycle events, redaction, no recursion | §6.4 alert assertions | notification non-leak evidence |
| D10 named rollback command, update+restore change | §6.5 rollback/command tests; S16 | per-row rollback evidence |
| Retry exhaustion (S4.3) | task-error contract suite: after 3 retries run is terminal `resource_grant_expiry_retry_exhausted`, no raw DB message | run observability evidence |
| Missing/unknown API states and restore/re-revoke classification (A14-A19) | §6.3 rows A14-A19 | audit negatives evidence |
| Queue/status failure (`enqueue_failed` or status write unavailable) | S19/S23 | run observability and stale-dispatch recovery evidence |

## 8. Implementation map

This is a design-to-file map, not an instruction to combine unrelated changes.
Each implementation change should remain reviewable against the decisions above.

| Area | Planned file(s) | Current anchor and purpose |
|---|---|---|
| Grant field/index and run models | `organization/models.py` | Grant model/constraints at `organization/models.py:1247-1386`; add nullable field, static index, run/evidence models |
| Generated migration | next `organization/migrations/` file | Current latest `organization/migrations/0102_alter_tenantresourcegrant_options.py:1-18` |
| Mutation service | `organization/services/resource_access.py` or focused sibling | Current access compatibility/helper module at `organization/services/resource_access.py:1-76`; keep system mutation behind service authorization |
| Container visibility | `organization/services/resource_access.py` | Extend current owner-only direct-field branch at `organization/services/resource_access.py:46-71` |
| Task and retry boundary | focused `core/tasks/` module | Typed taxonomy at `core/tasks/utils.py:30-84`; TaskContext at `core/tasks/context.py:140-194` |
| Hourly schedule | `organization/apps.py` | Current ready/capability wiring at `organization/apps.py:6-40`; use `core/schedules.py:27-80` |
| Owner UI/form/table | resource-grant view/form/table/admin/URL modules | Current list/create/revoke at `organization/views/resource_grant_views.py:34-155`; form at `organization/forms/resource_grant_form.py:20-82`; table at `organization/tables.py:425-483`; URLs at `organization/urls.py:102-111` |
| Read-only audit API | organization API views/serializers/urls plus filter | Current viewset imports/pattern at `organization/api/views.py:1-59`; router at `organization/api/urls.py:1-31` |
| Event pipeline | preferably no production change | Existing transition classification and dispatch at `core/signals.py:53-133`; tests prove reuse/redaction |
| Retention | `core/management/commands/prune_changelog.py` and retention docs | Current effective tenant cutoff at `core/management/commands/prune_changelog.py:307-370` |
| Capability ownership | `organization/apps.py`, capability docs/tests | Current owns tuple at `organization/apps.py:62-74` |
| OpenAPI | `itambox/schema.yaml`, API tests/client smoke | Reviewed-artifact policy at `docs/development/openapi-schema-policy.md:22-65` |
| Security selector | threat-model doc and manifest | Mandatory selector at `docs/development/tenant-resource-grant-security.md:140-276`; manifest at `scripts/resource_grant_test_manifest.json:1-5` |
| Rollback entry point | new `core/management/commands/restore_resource_grant.py` + shared rollback service | Named single-row command per D10; runbook invocation; tests in §6.5 |
| TaskContext failure-path fix | `core/tasks/context.py` | Fresh synthetic request ID before resolution (D4.1); nested bad-tenant test |
| Run claim/state machine | run model fields + task boundary | CAS claim, attempt generation, immutable terminal outcomes (D4/D6) |

## 9. Documentation and operator runbook updates

### 9.1 Threat model

Update `docs/development/tenant-resource-grant-security.md` with:

- `valid_until` semantics and the invariant that only soft deletion changes
  liveness;
- the clock-free resolver/coverage/constraint rule;
- N5's explicit exception for scheduled, operator-configured system action;
- `user=None` plus synthetic request/system authorization requirements;
- tenant-isolated coordinator/task/run behavior;
- audit API owner/direct/group visibility and neutral failures;
- event-payload redaction and no grantee-side recursive notifications;
- the per-row restore rule and non-restorative reverse migration; and
- the updated mandatory selector.

Keep the current Stable qualification and mandatory verification commands at
`docs/development/tenant-resource-grant-security.md:3-16` and
`docs/development/tenant-resource-grant-security.md:278-285`.

### 9.2 Operator runbook

Add `docs/operations/resource-grant-expiry.md` and link it from MkDocs navigation.
It must cover:

1. how to confirm the hourly schedule and worker health;
2. how to read queued/running/retryable/partial/terminal/success/skipped runs;
3. the stable error codes and which are safe to retry;
4. how to navigate a run to the read-only grant audit detail;
5. how to diagnose an unresolvable tenant without changing another tenant;
6. how to correct a mistaken deadline and restore exactly one row through the
   human-attributed service;
7. how to verify RBAC before expecting access to return;
8. how to handle a partial-unique conflict with a replacement live grant;
9. why `QuerySet.update()`, bulk restore, and direct SQL are unsupported;
10. why reversing/dropping the field does not undo revocations; and
11. how changelog retention/legal hold governs run/evidence retention
    (terminal-only pruning by `finished_at`, queued/running/retryable runs
    are always preserved, evidence prunes before `ObjectChange`, and the
    resulting `kind=unknown` audit rows are expected and safe).

The retention guide currently enumerates operational data classes and defaults
at `docs/operations/data-retention.md:1-15` and documents prune controls at
`docs/operations/data-retention.md:27-43`. Add expiry runs/evidence as a child of
the changelog retention class, not a new independently configured class.

### 9.3 API and compatibility documentation

Document the additive GET-only audit route, serializer states, visibility
matrix, error codes, filter ordering, and platform-superuser rule. Update the
OpenAPI contract and generated-client smoke. No new `ITAMBOX_*` setting is
introduced, so the settings table at
`docs/development/external-contract-inventory.md:473-503` does not change.

Update the capability registry ownership list to include the two operational
models. Do not change maturity, security-critical status, activation, or
limitations; those are the frozen contract recorded at
`docs/development/capability-registry.md:57-79`.

## 10. Rollout and operational safety

Ship the nullable field, static index, run tables, task, schedule, UI, and audit
API in one release. The migration may run before workers restart, but no code
revision should expose a writable `valid_until` without also containing the
sweep. During a rolling deployment, old processes ignore the nullable field and
continue using soft-delete liveness; new processes do the same until they sweep.

Deployment sequence:

1. apply the generated migration;
2. verify every pre-existing grant deadline is null;
3. run the forward migration/no-op rehearsal against production-shaped data;
4. restart web and django-q2 workers on the same revision;
5. verify the unique hourly schedule and no duplicate schedule rows;
6. observe the first per-tenant runs, expected to be skipped unless operators
   created new deadlines after deployment;
7. verify OpenAPI schema/runtime parity; and
8. retain the prior application build for code rollback, with the documented
   warning that schema reversal never restores revoked rows.

Operational alarms should key on terminal runs, exhausted retries, stale running
runs older than the q2 retry window, and partial runs with nonzero invalid count.
They should identify only tenant ID, run ID, stable code, counts, and timestamps;
raw exception text, grant reason, resource name, and cross-tenant details never
enter logs. This follows `TaskContext.log_context`, which emits operation,
tenant, actor, and request IDs at `core/tasks/context.py:160-168`.

## 11. Maintainer decisions (2026-08-15)

1. **Approved:** an **hourly** coordinator with three finite retries at 60,
   120, and 240 seconds, as specified in D4/§4.3. The hourly cadence and the
   retry backoff are part of the Stable behavior from the first release.
2. **Approved:** the proposed platform boundary in D8 — a genuinely unbound
   platform superuser has global audit/run visibility, a tenant- or
   group-bound superuser is restricted to the selected container/subtree,
   and every other caller needs an active authorized container. Platform-level
   visibility is therefore included in WP-22.

Both decisions are binding for the implementation; the design sections D4/D8
already reflect them.
