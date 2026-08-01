# Compatibility, deprecation, and support policy for 1.x

This is ITAMbox's promise about change: what a 1.x release may do to a surface
somebody already depends on, how long a removal takes, and which guarantees no
maturity grade is allowed to trade away. It applies to the surfaces enumerated
in the [external contract inventory](external-contract-inventory.md) and to
nothing else — a promise about a surface nobody listed is unfalsifiable, so the
inventory is deliberately bounded and this policy is deliberately narrow.

The [capability registry](capability-registry.md) says what each slice *is*
(grade, activation mode, declared limitations) and enforces those rules at
construction. This document says what each grade *promises*, which the registry
does not encode. The two are checked against each other rather than kept in
step by hand.

!!! note "Pre-release status"
    Until `v1.0.0` is published, this document describes the contract that 1.0
    will ship under. It is not a retroactive promise about the alpha and beta
    prereleases already tagged, whose changes are recorded in `CHANGELOG.md`.
    Prerelease support status is in `SECURITY.md`.

## Requirement identifiers

`N*` (non-functional), `X*` (cross-cutting safety), `S*` (Stable-class), and
`U*` (capability-registry) identifiers name acceptance criteria of the 1.0
milestone. This document does not restate the milestone; it restates only the
criteria that bear on compatibility, and names the rest collectively as the
**N1-N11 and X1-X6 safety floors**.

## What this policy covers

| Area | Where it is enumerated |
|---|---|
| REST, GraphQL, and SCIM surfaces | [external contract inventory](external-contract-inventory.md) |
| Report identifiers and column keys | inventory |
| Webhook and domain-event envelope | inventory |
| Contract-bearing `ITAMBOX_*` settings | inventory |
| UI URL namespaces and route shape | inventory |
| Permission codenames persisted in role JSON | inventory |
| Persisted choice values | inventory |
| Capability contract class and exclusions | inventory |

## What this policy deliberately does not cover

Naming these keeps the promise honest; each is a separate, reviewable piece of
work and none of them is implied by publishing this document.

- **No repository-wide inventory.** Only the surfaces listed above are covered.
  An internal Python name, a template, a table column, a form field, a CSS
  class, or a management-command flag carries no compatibility promise here.
- **No `/api/v1/` migration.** The unversioned prefix stays exactly where it is
  for 1.x; see [API versioning](#api-and-wire-versioning).
- **No implementation, remediation, endpoint, flag, or migration** is created by
  this policy. It describes surfaces that already exist.
- **No runtime, base-image, or dependency claim.** Supported Python, PostgreSQL,
  and image contents are release metadata, covered by the
  [release runbook](release-checklist.md) and `README.md`.
- **No deployment topology promise.** Reverse-proxy, worker, and cache
  topologies are operator choices documented in `operations/`.
- **No plugin API promise beyond the Experimental class.** The public plugin
  surface is graded Experimental; layer membership in the
  [architecture policy](architecture-policy.md) describes structure and promises
  nothing.

## Contract classes

Every surface in the inventory is sold under exactly one of four classes. The
class is derived from the owning capability's registry declaration, so a slice
cannot be promoted in prose without being promoted in code.

| Class | Registry declaration | Short form |
|---|---|---|
| **Stable** | `stable` + `always-on` | supported |
| **Beta enabled** | `beta` + `enabled` | on by default, clearly labelled Beta |
| **Beta opt-in** | `beta` + `opt-in` | optional, disabled by default |
| **Experimental** | `experimental` + `opt-in` | optional, disabled by default |

Stable is the only class the registry can construct without an activation
probe, which is why "Stable" and "can be switched off" are mutually exclusive
states rather than a review convention. A surface with no owning capability has
no class; absence of a class is not a claim of stability.

## Stable

- **Data and responses are additive-only for the whole of 1.x.** A minor release
  may add a field, a filter, an optional request parameter, or an enum value to
  an enum published as *open*. It may not change the meaning of an existing one.
- **Identity is frozen.** Existing names, types, closed enum values,
  primary-key URLs, documented state machines, and permission codenames do not
  change within 1.x. Renaming is a removal plus an addition and follows the
  removal rule below.
- **Removal takes two minor releases of removal notice**, published in
  `CHANGELOG.md` and in the inventory, before the release that removes it.
- **A Stable surface is never removed in a patch release**, regardless of
  notice already served.
- **The promise cannot be weakened within 1.x.** A Stable surface may not be
  regraded to Beta or Experimental, and its class may not be qualified with new
  conditions after publication. The only in-1.x movement is toward a stronger
  promise.
- **S5 realistic rollback** applies: every release that changes a Stable
  persisted surface must be rehearsed against a realistic restore-first
  rollback — stop writes, restore the verified predecessor backup, deploy the
  predecessor commit, re-run its checks — as described in
  [migration verification](migration-verification.md) and the
  [recovery qualification drill](../operations/recovery-drill.md).

## Beta enabled

A Beta enabled capability is on by default, so it owes an explicit Beta label
wherever it surfaces. That labelling is **a release obligation on Beta enabled
work, not a claim that every surface already carries it** — the same distinction
the safety floors draw between an obligation and a description of today.

Stated for what ships now: the list and detail banner, the header badge, and
`x-itambox-maturity` in the OpenAPI document are derived from the capability
declaration and are in place. Navigation is labelled per *group* — the Beta flag
lives on the menu group, not on the menu item — so the `Alerting` group, which
holds the Stable `alerting.inbox` alongside the Beta `alerting.rules`, cannot
truthfully carry a wholesale Beta label and does not carry one. Labelling
navigation per item is a product change; WP-2 makes none.

- **It preserves the data an operator already recorded.** A change may reshape
  how data is entered or displayed; it may not silently discard rows an
  operator created under the previous shape.
- **A breaking change lands only in a minor release, never in a patch.**
- **Removal takes one minor release of notice and an export** — the removing
  release must still be able to get the operator's data out, in a documented
  format, before the surface disappears.
- **It has a real inactive state.** A deployment that has configured nothing
  reports the capability inactive through `python manage.py capabilities`, and
  an inactive capability is inert rather than half-working. The banner is
  derived from the declared contract, not from activation state: a Beta slice a
  deployment switched off is still Beta.

## Beta opt-in

- **It is inert on a fresh deployment.** No rows, no routes doing work, no
  background schedule, no outbound traffic until an operator switches it on.
- **The activation surface itself is held to the Stable standard.** The setting
  name, the flag's accepted values, and the object field that turns the slice on
  are Stable even while everything behind them is Beta — an operator must not
  have to rediscover how to switch a capability on at every minor release.
- **Anything it puts on the wire must carry its own independent wire version**,
  distinct from the application version and from the REST API generation, so a
  consumer can pin against the wire rather than the release. This is an
  obligation on the work that ships a wire, not a description of every wire that
  exists today — see [outbound wires as they stand](#outbound-wires-as-they-stand).

## Experimental

- **It is reached only through an explicit configuration opt-in.** There is no
  default-on Experimental surface.
- **It may change in any release, including a patch**, with the change recorded
  in `CHANGELOG.md`. No deprecation cycle applies.
- **Support is security fixes only.** Functional defects in an Experimental
  surface are accepted as feedback, not as regressions against a promise.
- **Consumers must pin the exact revision** they validated against. There is no
  compatible-range claim for an Experimental surface.

## The security-critical marker

A capability declared `security_critical` guards a boundary. The registry
refuses to construct one that carries an activation probe, so no deployment
state, setting, or row can report it off. `organization.role_grants` is the only
entry that carries the **security-critical marker** today.

The marker is orthogonal to the contract class: it constrains *activation*, not
*change*. A security-critical capability is Stable by construction (it is
always-on), so the Stable promises above apply to it in full.

## Safety floors no class waives

The **N1-N11 and X1-X6 safety floors** apply identically to Stable, Beta
enabled, Beta opt-in, and Experimental surfaces. A maturity grade is an
interface-maturity label; it is never a licence to lower one of these:

1. tenant isolation;
2. authorization, including object-level authorization;
3. preservation of data an operator recorded;
4. secret handling — no credential, key, or token value reaches a log, a
   diagnostics row, a task payload, or a rendered page;
5. asynchronous correctness — idempotency, retry, and durable outcome;
6. observability of who changed what, when, and why;
7. rollback and export.

No Beta or Experimental grade waives any of them. A change that would lower one
of these floors is a security change and is reviewed as one, whatever the
surface's class.

## Tenant isolation and RBAC

Tenant isolation is absolute and classless: **tenant B cannot read, write, list,
filter, export, or reference** tenant A's data through any surface, at any
maturity grade, through any activation state.

- **Service and data layers enforce the same authorization the UI does.** A
  check that exists only in a view is not an authorization control; the service
  and the queryset must fail closed on their own.
- **Permission codenames are a Stable surface.** They are persisted as JSON
  strings in `organization.Role.permissions` — plain `app_label.codename`
  entries, with no foreign key to `auth.Permission` — so renaming a model or a
  codename silently invalidates a stored grant. The enumerated custom codenames
  are in the [inventory](external-contract-inventory.md).
- **No grade, no account type, and no unlinked view is an exception.** No Beta
  capability is exempt from the tenant boundary, and no superuser account is
  exempt from it. A view that is merely absent from navigation is not exempt
  either. Any wording that reads otherwise is rejected by rule `C-DOC2` below,
  in this policy, in the [inventory](external-contract-inventory.md), in
  `CHANGELOG.md`, and in the limitation strings a capability declares in its
  `AppConfig` — the four places a shortfall actually gets written down.

## Asynchronous work, idempotency, and observability

**N9 and S6** govern what a background write leaves behind. Two exceptions are
published here rather than left to be rediscovered:

- **Deployment activation diagnostics have no in-product actor.** A capability
  probe, a startup warning, and `python manage.py capabilities` describe
  deployment state. They are not attributable to a person and are not recorded
  in the object changelog.
- **A scheduled `TaskContext(tenant_id=...)` write carries a synthetic request
  identifier and a `None` user** when the schedule has no bound principal. It
  still retains the tenant, the object, the transition, the time, and the
  trigger, and it is **never presented as a human action**. A change with no
  attributable actor is displayed as a system action, not as an empty or
  guessed user.

Beyond attribution, every asynchronous surface owes **idempotency, retry, and
durable-outcome** obligations: a task that runs twice must not double its
effect, a retry policy must be finite and declared, and the outcome must be
observable after the worker exits.

**X2-X4 are obligations on capability work, not claims about today.** They bind
the change that promotes, extends, or ships an asynchronous capability; they do
not assert that every existing delivery path already meets them. Where a shipped
path does not, its shortfall stays declared as a capability limitation rather
than being quietly absorbed into this policy. Two such declarations stand today,
registered against `automation.webhooks` and `alerting.rules`:

- webhook deliveries are fire-and-forget, with no delivery log and no replay;
- alert-channel delivery failures are logged, not retried.

Typed failure contracts, task naming, dispatch-on-commit, and the retry ladder
are tracked separately in issue #99 and are deliberately not restated here;
duplicating them would create a second, drifting copy of that contract.

## Migrations, rollback, and backward compatibility

This section is policy only; it adds no migration and changes no runtime.

- **N11 reverse-or-export rehearsal.** A release that changes a persisted
  contract surface must rehearse in CI either a reverse of the migration or an
  export of the affected data, and must record which of the two it relied on. A
  migration classified `upgrade-only` or irreversible in
  [migration verification](migration-verification.md) satisfies N11 through the
  export arm, never through an unproven reverse.
- **S5 realistic rollback** (above) is the restore-first path, and is the only
  supported rollback for an irreversible migration.
- Predecessor identity, parity evidence, and the blockers that prevent a blanket
  upgrade claim stay where they are already recorded, in
  [migration verification](migration-verification.md).

### Deferred, documented, not implemented

Each of these is a decision published now and implemented later or never. None
of them is created, changed, or scheduled by this document.

- **The subscription renewal-term alias remains supported through 1.x.**
  `subscriptions.Subscription.vendor_contract_auto_renews` is the canonical
  field; the canonical wire name is `vendor_contract_auto_renews`. The legacy
  `auto_renewal` name remains a read/write alias on the model,
  REST API, and GraphQL API for the whole of 1.x and is **removed no earlier than
  2.0**. New UI and documentation use only the canonical, non-ambiguous name.
- **The legacy auto-approval setting keeps a compatibility read through 1.x.**
  `ITAMBOX_REQUISITION_AUTO_APPROVAL_THRESHOLDS` is the canonical environment
  and Django-settings name. `REQUISITION_AUTO_APPROVAL_THRESHOLDS` remains a
  deprecated fallback for the whole of 1.x and emits a startup warning when it
  supplies the value. The canonical name wins when both are present. If neither
  name is configured, automatic approval and the Asset Request procurement seam
  are inactive; there are no built-in thresholds.
- **Integer-keyed SCIM detail routes remain supported for the whole of 1.x** as a
  compatibility read. Both SCIM mounts use string-compatible `Users/<str:pk>` and
  `Groups/<str:pk>` converters. Decimal identifiers resolve legacy integer
  primary keys, while opaque stable identifiers resolve the new SCIM resource ID;
  successful responses emit opaque IDs only. Legacy integer lookup is removed no
  earlier than 2.0, after the documented migration notice and re-sync window.

## API and wire versioning

**`/api/` is the version 1 compatibility convention for the whole of 1.x.** The
unversioned prefix is not a missing version; it is the name under which
generation 1 is promised. It is a naming and compatibility convention only:
there is **no version segment, no negotiation header, and no runtime version
seam** in the application today, and this policy creates none. Nothing at
runtime inspects, selects, or negotiates an API generation.

It follows that a breaking generation cannot be introduced by re-labelling the
existing prefix. If one ever becomes necessary, it **would have to be mounted
separately as `/api/v2/`**, alongside the generation-1 tree, with the
generation-1 surface then following the Stable removal rule. No such work is
performed, scheduled, or implied here, and **no `/api/v1/` migration is
performed** — moving the existing tree under a version segment would itself be
the breaking change the convention exists to avoid.

### Outbound wires as they stand

**External wires carry versions independent of the application release.** That
is the rule for a wire that has a version. Stated truthfully for what ships
today:

- The outbound webhook envelope **carries no wire version, no event identifier,
  and no idempotency key**. Its fields are exactly `event`, `model`,
  `object_id`, `timestamp`, and `data`, optionally signed with
  `X-Hub-Signature-256`. `automation.webhooks` is Beta opt-in and declares the
  payload schema unfrozen; a consumer must treat the envelope as versionless and
  pin against the release it validated against.
- **`contract_version` versions the registry declaration**, not any wire. It is
  metadata about the capability record, it never appears in an outbound payload,
  and nothing may read it as a webhook, SCIM, or GraphQL wire version.
- SCIM schema URIs and the GraphQL schema carry their own identities, set by
  their respective specifications rather than by ITAMbox's release number.

### Enum openness

**Every inventoried enum is marked explicitly open or closed.** An *open* enum
may gain values in a minor release and a consumer must tolerate an unknown one;
a *closed* enum is frozen for 1.x and a consumer may switch exhaustively on it.
Which is which is recorded per enum in the
[inventory](external-contract-inventory.md) and enforced by rule `C-ENUM2`.

The **nine `ScheduledReport` frequency values** are an explicit Stable-graded
carve-out inside a surrounding Beta capability: `reporting.scheduled` is Beta
enabled and may change, but the persisted frequency vocabulary is closed and
frozen for 1.x, because those nine strings are written into rows that outlive
the capability's own churn. The carve-out is deliberate and narrow — it covers
the frequency values only, not the schedule model, the delivery behaviour, or
anything else the capability owns. The three `AlertRule` severities are the same
shape: the Stable `alerting.inbox` persists them on every `AlertLog` row, so the
severity vocabulary is closed while the Beta rule engine around it is not.

### UI URLs

UI URL coverage is bounded on purpose. The application namespaces are
inventoried and promised; **individual UI route names are not frozen**. The
repository declares several hundred of them, and freezing every one would make
an ordinary view rename a breaking change while promising nothing an integrator
actually depends on. What is promised: the namespace set, the pk-based route
shape, and the named root entry routes listed in the inventory.

## Configuration renames

A contract-bearing `ITAMBOX_*` setting is not renamed silently. A rename ships
with **a compatibility read and a startup warning**: the new name wins, the old
name keeps working for the rest of 1.x, and a deployment still using the old
name is told so at startup. Dropping the compatibility read is a removal and
follows the Stable removal rule. Settings outside the contract are listed as
out of scope in the inventory with the reason.

## Cross-tenant resource grants

`TenantResourceGrant` lets one tenant share one stock pool with another, under a
tight `APPROVED_RESOURCE_MODELS` allowlist. It is **security-critical** in the
sense this policy uses the word — it decides whether one tenant may reach
another's rows — and the rules around it are deliberately stricter than any
contract class alone would imply. It is not itself a registered capability;
`organization.role_grants` is the one entry carrying the registry's
`security_critical` flag, and grants are governed here instead.

The grant model reaches no REST, GraphQL, or SCIM surface, so its access levels
are deliberately absent from the inventory's enum tables. That is a scoping
decision about *what is frozen*, not a relaxation of the duties below.

- **There is no baseline escape.** A grant surface may not be added to any
  policy baseline as accepted debt, at any severity; the coupling it introduces
  has to be correct at review time rather than grandfathered.
- Every change to the grant model, its resolver, or its approved-resource
  allowlist requires adversarial review — a reviewer whose brief is to break the
  boundary, not to confirm it holds.
- Widening `APPROVED_RESOURCE_MODELS` carries a threat-model duty: the change
  must state what a hostile grantee can now reach, and what stops it.

## How this policy is enforced

`scripts/check_contract_policy.py` derives the enumerated surfaces from source
with `ast` and compares them against the published inventory. It imports no
Django and reaches no database, so it runs on a bare interpreter alongside the
other repository gate suites, and it never writes a tracked document.

```bash
uv run --locked --only-group dev python scripts/check_contract_policy.py
uv run --locked --only-group dev python scripts/check_contract_policy.py --list
uv run --locked --only-group dev python -m unittest scripts.tests.test_contract_policy
```

CI runs it without a dedicated workflow step: `scripts/tests/test_contract_policy.py`
is picked up by the repository gate-suite discovery, and the suite asserts the
gate reports no finding. That is deliberate — the workflow policy forbids
enumerating individual gate suites by name, because a hand-written list is a
list that silently goes stale.

| Rule | What it blocks |
|---|---|
| `C-ENUM1` | A published choice set that no longer matches source |
| `C-ENUM2` | A value added to or removed from an enum declared closed for 1.x |
| `C-SET1` | An `ITAMBOX_*` setting that is neither published nor excluded |
| `C-SET2` | A published setting no settings module reads |
| `C-SET3` | A setting both published and declared out of scope |
| `C-CAP1` | A registered capability missing from the inventory |
| `C-CAP2` | A capability published under the wrong class or activation |
| `C-CAP3` | A non-Stable capability published with no exclusions |
| `C-CAP4` | An inventory row no `AppConfig` registers |
| `C-CAP5` | A declared limitation whose published exclusions summary was never re-read |
| `C-PERM1` | A custom permission codename that is not published |
| `C-PERM2` | A published codename no model declares |
| `C-HOOK1` | A webhook envelope field that changed shape |
| `C-HOOK2` | A change to the header the delivery task signs with |
| `C-SCIM1` | A SCIM route that is routed but not published |
| `C-SCIM2` | A published SCIM route that is not routed |
| `C-URL1` | A UI URL namespace that is declared but not published |
| `C-URL2` | A published namespace no URLconf declares |
| `C-URL3` | A published root entry route that no longer exists |
| `C-DOC1` | A required promise deleted from this document |
| `C-DOC2` | Wording that reads as an isolation or authorization escape |
| `C-DOC3` | A missing published document |

The gate is complementary to, not a replacement for, the capability-registry
drift tests in `itambox/tests/test_capability_slices.py`, which run under Django
and own grade, activation, and limitation drift (U5/U7). What is added here is
the contract class and the exclusions, which the registry does not publish.

## Milestone exit-criteria evidence

| Criterion | Evidence |
|---|---|
| Class promises published | *Stable*, *Beta enabled*, *Beta opt-in*, *Experimental* above |
| Security-critical marker published | *The security-critical marker* above |
| Legal 1.x transitions published | *Contract classes*, class sections, *API and wire versioning* |
| Bounded inventory published | [external contract inventory](external-contract-inventory.md) |
| Deferred exclusions explicit | *What this policy deliberately does not cover*, *Deferred, documented, not implemented* |
| Tenant/RBAC statements published | *Tenant isolation and RBAC* |
| Async/observability exceptions published | *Asynchronous work, idempotency, and observability* |
| Migration and rollback policy published | *Migrations, rollback, and backward compatibility* |
| Drift verifiable, not assertable | `scripts/check_contract_policy.py`, `scripts/tests/test_contract_policy.py` |

## Related

- [External contract inventory](external-contract-inventory.md)
- [Capability Registry](capability-registry.md)
- [Module Maturity](module-maturity.md)
- [Migration verification](migration-verification.md)
- [Release runbook](release-checklist.md)
- [OpenAPI Schema Policy](openapi-schema-policy.md)
- [Security Test Expectations](security-test-expectations.md)
- [Recovery qualification drill](../operations/recovery-drill.md)
