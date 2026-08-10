# Custody permission surface and capability matrix

This document is the permission inventory for issue #259, **Define role-based custody template and signing view permissions for IT technicians**.

It deliberately separates:

- the behaviour present on `origin/main` at the start of this change; and
- the target contract for the issue implementation.

The target contract is based on the local design reference `issue-259-readiness.md`, §5.2 Option B, §5.3, §5.4 and §5.5, and the decisions recorded in the decision log below. Slice D implements the assisted-signing session and export parts of that contract. The readiness file is a local, uncommitted design reference and is not part of the product documentation or implementation change.

No bearer token, signature payload, credential, or complete EULA is reproduced in this document.

## 1. Authorization vocabulary and tenant boundary

ITAMbox uses Django permission codenames resolved through the tenant membership backend. The canonical checks are `user.has_perm(codename, obj=object)` and the generic/API permission classes; role names are seed conveniences, not a second authorization mechanism.

The custody models have an important asymmetry:

- `CustodyTemplate` has a nullable `tenant` and optional `tenant_group`. Its `TenantScopingSoftDeleteManager` supports tenant-owned, tenant-group, and global/shared templates (`itambox/compliance/models.py:33-53`). A visible template is not automatically editable: mutation still requires the corresponding object permission and scope.
- `CustodyReceipt` has no direct tenant foreign key. Its tenant is derived from `receipt.asset.tenant` (`itambox/compliance/models.py:118-127`). The default manager is intentionally unscoped so the public token view can resolve a bearer token, but every internal list, detail, prepare, and export query must scope through `asset__tenant`.
- `AssetHolder.tenant` and a holder's linked `User` are domain data. They do not replace the active tenant/membership authorization decision. Recipient binding is a separate rule: consent is allowed only for the intended holder.

The issue uses these action families:

| Action | Meaning in this document |
| --- | --- |
| `view` | Read a template or an internal receipt. |
| `add` | Create a template or another ordinary model object through the generic CRUD/API surface. |
| `change` | Modify an existing template or ordinary model object. It is not a substitute for preparing a custody session. |
| `delete` | Delete a template through the generic CRUD/API surface. |
| `prepare` | Start the technician-authorized, server-bound custody handoff/session. It never changes the recipient or signs for the recipient. |
| `export` | Deliberately transfer the finished receipt outside the normal internal detail view. It is a separate, stricter capability. |

## 2. UI route inventory

### 2.1 Custody-template routes

The current routes are declared in `itambox/compliance/urls.py:33-44` and implemented in `itambox/compliance/views.py:210-295`.

| Route and view | Surface today on `origin/main` | Target after issue #259 | Tenant scope and failure contract |
| --- | --- | --- | --- |
| `custody-templates/` — `CustodyTemplateListView` | `view_custodytemplate`, supplied by `ObjectListView`. The queryset is tenant-scoped and may include permitted shared/global templates. | Keep `view_custodytemplate`. The list must not imply template-management rights or receipt rights. A Technician may see templates but must not change template policy. | Active tenant/group scope from the generic tenant-scoping mixin. An authenticated object outside the scope is hidden according to the generic 404 boundary; missing permission is the normal generic permission denial. |
| `custody-templates/add/` — `CustodyTemplateEditView` without `pk` | `add_custodytemplate`. | Keep `add_custodytemplate` for administrators/superadmins in an allowed creation scope. The Technician seed must not grant it. | The selected tenant is checked again in `ObjectEditView.form_valid`. A cross-tenant or unauthorized selected tenant is rejected without creating a row. |
| `custody-templates/<pk>/` — `CustodyTemplateDetailView` | `view_custodytemplate`. The detail also loads `template.receipts.all()` for its embedded table. | Keep `view_custodytemplate` for template content. The embedded receipt table is a separate receipt surface and must use `view_custodyreceipt` plus `asset__tenant` scope; template visibility must not grant receipt visibility. | Template objects are resolved through the tenant-scoped queryset. Foreign-tenant objects are hidden as 404 by the generic object authorization. |
| `custody-templates/<pk>/edit/` — `CustodyTemplateEditView` | `change_custodytemplate`. | Keep `change_custodytemplate`, restricted to administrators/superadmins and the template's permitted scope. A Technician can view but cannot change the template policy. | Object-level permission is anchored to the template and selected tenant. Foreign-tenant mutation must not be exposed as a valid edit. |
| `custody-templates/<pk>/clone/` — `CustodyTemplateCloneView` | The generic clone path treats the unsaved clone as a creation and therefore requires `add_custodytemplate`; the source object is also resolved through the tenant-scoped view. | Keep the `add_custodytemplate` decision for the new object. Cloning is template management, not preparation. | The source and destination scope must both be authorized. No Technician template-policy mutation is implied by receipt preparation. |
| `custody-templates/<pk>/delete/` — `CustodyTemplateDeleteView` | `delete_custodytemplate`. | Keep `delete_custodytemplate` for administrators/superadmins only in the permitted scope. | Generic object-level scope and protected-delete handling apply; a foreign-tenant object is not disclosed as deletable. |
| `custody-templates/<pk>/preview/` — `custody_template_preview` | `login_required` plus `view_custodytemplate`. The view builds an unsaved preview receipt and renders `compliance/custody/sign_portal.html`; it is not a real receipt and must not be treated as consent. | Keep preview tied to `view_custodytemplate`. Preview remains non-mutating and must not be a shortcut to `prepare`, recipient consent, or export. | The template lookup must remain tenant-safe. Preview data is synthetic/unsaved; no receipt token or holder data may be leaked from an unrelated tenant. |

The generic authorization paths used by these routes are:

- `itambox/itambox/views/generic/list_.py:34-44` — list views derive `view_<model>`;
- `itambox/itambox/views/generic/detail.py:43-68` — detail views derive the same permission and anchor it to the object;
- `itambox/itambox/views/generic/edit.py:27-50` — add versus change is selected from whether the object exists;
- `itambox/itambox/views/generic/delete.py:26-40` — delete is object-scoped; and
- `itambox/itambox/views/generic/authorization.py:20-93` — codename construction, object lookup, and the fail-closed 404 boundary.

### 2.2 Public recipient route and target internal receipt routes

On `origin/main`, `compliance/urls.py:42-44` contains only the bearer-token signing route. There is no internal receipt list, internal receipt detail, prepare route, or export route.

| Surface | Route/view today | Target after issue #259 | Permission and binding |
| --- | --- | --- | --- |
| Recipient signing/consent | `custody/sign/<str:token>/` — `custody_eula_sign` (`views.py:151-191`). The route resolves the opaque token, optionally requires login, checks expiry/completed state, and when authenticated checks the user against the holder. | Keep a dedicated recipient flow. A Technician or administrator permission must never turn the operator into the signer. The intended holder remains the only consent principal. Option B adds a server-bound prepare/session handoff before this step. | No `sign_custodyreceipt` codename for Technicians. A valid recipient token/session and the intended-holder binding are required. `REQUIRE_CUSTODY_SIGNIN=True` remains the production default; the explicit `False` configuration is tested separately and must not create an operator impersonation path. |
| Internal receipt list | No route on `origin/main`; receipt rows appear only in embedded tables. | Add an authenticated internal list surface with `view_custodyreceipt`. | Query through `asset__tenant` and the active authorized tenant. Missing internal permission returns `403` with the internal-permission error contract; a foreign tenant is hidden as `404`. No bearer token is needed to render internal rows. |
| Internal receipt detail | No route on `origin/main`; accepted data is shown through the public token route. | Add an authenticated internal detail surface with `view_custodyreceipt`. | Same `asset__tenant` boundary and object-level permission as the list. It is independent of recipient consent and must not be an alias for `custody_eula_sign`. |
| Prepare/hand off a signing session | No separate session exists. The asset page links directly to the raw token with `?onsite=true`. | **Implemented in Slice D:** `compliance:custodyreceipt_prepare` provides the Option-B prepare/session action using `prepare_custodyreceipt`. It binds the receipt, asset tenant, intended holder, operator, creation/expiry, and session state. | A Technician may prepare in the own/effective tenant. The operator cannot replace the intended holder, set the Technician as signer, or use prepare to mutate acceptance. Unauthorized internal callers receive the internal `403`, never `wrong-recipient`. |
| Finished receipt internal view | No separate internal route. `receipt_success.html` is reached through the same token route and is subject to the public-link expiry order. | Use the internal detail surface and `view_custodyreceipt`; an authorized operator may inspect a finished receipt independently of the expired public bearer link. The recipient may see only the result belonging to the recipient-bound flow. | Tenant isolation remains mandatory. The public recipient link may be expired while the authorized internal view remains available. |
| Export | No custody-specific export route on `origin/main`. | **Implemented in Slice D:** `compliance:custodyreceipt_export` provides an explicit export action guarded by `export_custodyreceipt`. | Separate from `view_custodyreceipt`; same tenant/object check. Technician is denied by default. Recipient has no internal export capability. |

### 2.3 Embedded surfaces

Embedded surfaces are authorization boundaries, not merely presentation details. They must not expose a raw token or a receipt payload just because the surrounding object is visible.

#### Asset detail

`itambox/assets/views/asset_views.py:174-180` builds `CustodyReceiptTable` from `CustodyReceipt.objects.filter(asset=asset)`. Lines `203-216` select the latest receipt for the active `AssetHolder` assignment and put its token into the context. The template `itambox/templates/assets/includes/detail/asset_assignment.html:57-99` then renders:

- a signed-receipt link to `custody_eula_sign`;
- an on-site signing link with `?onsite=true`; and
- a copyable bearer URL.

Today the surrounding asset detail is controlled by `view_asset`, so an asset viewer can be presented with custody links without a separate custody decision. After this issue:

- `view_asset` alone must not grant internal receipt visibility;
- internal receipt rows/actions require `view_custodyreceipt` and the asset tenant boundary;
- preparing an on-site handoff requires `prepare_custodyreceipt`;
- the operator UI must not disclose the raw bearer token merely to offer an internal view; and
- recipient consent must remain a separately bound recipient step.

The asset page may still show a non-sensitive custody status when the asset itself is visible, but it must not render unverified asset/holder/EULA details from an unauthorized receipt.

#### Asset-holder detail

`itambox/organization/views/assetholder_views.py:90-97` builds a `CustodyReceiptTable` from `CustodyReceipt.objects.filter(holder=assetholder)`. The holder detail template `itambox/templates/organization/assetholders/assetholder_detail.html:61-70` exposes a “Custody EULAs” tab and the related table. Today the enclosing `AssetHolderDetailView` uses the generic `view_assetholder` permission, and the table's action points at the public token route.

After this issue, the holder page must use an explicit receipt decision and an `asset__tenant` filter. A holder profile being visible does not make every receipt with that holder value visible, particularly when the holder relation is not a sufficient tenant authorization source. An intended recipient may see their own completed result through the recipient-bound flow; unrelated users must receive the defined denial and no receipt payload.

#### Template detail

`CustodyTemplateDetailView` (`itambox/compliance/views.py:216-235`) loads `template.receipts.all()` and renders the receipt table in `itambox/compliance/templates/compliance/custodytemplates/custodytemplate_detail.html:95-112`. Today the reverse relation is not a dedicated internal permission surface and its action opens the raw token link.

After this issue, template content remains protected by `view_custodytemplate`, while receipt rows require `view_custodyreceipt` and are scoped by the asset tenant. The table action must point to internal detail/prepare actions according to the caller's capabilities, not automatically to recipient consent.

### 2.4 Tables and UI links

`itambox/compliance/tables.py:25-75` defines `CustodyReceiptTable`. Its `render_actions()` currently reverses `compliance:custody_eula_sign` with `record.token` and labels that action “View”. This conflates internal viewing and public signing and places a bearer credential in an internal table link.

The target table contract is:

- internal operators use the internal receipt detail route when they have `view_custodyreceipt`;
- a prepare action is offered only with `prepare_custodyreceipt` and only for a pending receipt in the authorized tenant;
- recipient consent links are shown only for the intended recipient/recipient handoff, not as a generic operator action; and
- export is offered only with `export_custodyreceipt`.

## 3. API inventory

The compliance API is mounted under `/api/compliance/` by `itambox/itambox/api/urls.py`. The routes are registered in `itambox/compliance/api/urls.py:13-18`:

- `/custody-templates/` → `CustodyTemplateViewSet`;
- `/custody-receipts/` → `CustodyReceiptViewSet`; and
- the unrelated compliance audit/maintenance routes.

### 3.1 Current API authorization

`itambox/compliance/api/views.py:46-64` uses `ITAMBoxModelViewSet` and the generic API permission stack:

- `GET` requires `view_<model>`;
- `POST` requires `add_<model>`;
- `PUT`/`PATCH` require `change_<model>`; and
- `DELETE` requires `delete_<model>`.

`itambox/itambox/api/permissions.py:17-126` implements the method-to-codename map and object-level `user.has_perms(..., obj)`. `StrictTenantPermission` (`permissions.py:136-196`) hides foreign-tenant objects as 404. The receipt viewset adds the necessary derived scope with `_scope_by_asset_tenant()` (`compliance/api/views.py:24-43`), because a receipt has no direct tenant field.

`itambox/compliance/api/serializers.py:67-122` makes the token, acceptance state, signature artifacts, timestamps, and verification fields read-only. This is a hard security boundary: an API client must not forge acceptance by PATCHing a receipt.

### 3.2 Target API contract

The existing CRUD API contract remains in force, with the following custody-specific requirements:

| API surface | Permission after issue | Required behaviour |
| --- | --- | --- |
| Template list/detail | `view_custodytemplate` | Return only the active authorized template scope; visibility does not grant mutation. |
| Template create/update/delete | `add_custodytemplate`, `change_custodytemplate`, `delete_custodytemplate` | Keep generic object/tenant checks. Technician seed does not receive template-policy mutation rights. |
| Receipt list/detail | `view_custodyreceipt` | Always filter through `asset__tenant`; foreign-tenant detail is 404. |
| Receipt create/update/delete, where the existing API exposes them | Existing `add/change/delete_custodyreceipt` checks plus domain policy | Never make acceptance/signature fields writable. A client cannot use PATCH to fake consent. Any new custody action must be explicit rather than hidden in generic change. |
| Prepare/session API, if exposed by the implementation | `prepare_custodyreceipt` | Bind operator, intended holder, receipt, tenant, expiry, and state; never accept a caller-supplied replacement signer. |
| Export | `export_custodyreceipt` | Use the same `asset__tenant` scope and object-level decision as internal detail; do not route through the public bearer token. |

The API and UI must agree on the foreign-tenant result: no receipt payload and a neutral 404 where object existence could otherwise be enumerated. Recipient-specific wrong-recipient responses belong to the recipient flow, not to a generic internal permission error.

## 4. Object-level authorization and error contract

### 4.1 Generic UI and API enforcement

The generic UI layer derives model permissions and passes the object to the membership backend. `PermissionResolver.object_under_check()` and the tenant-scoping mixins fail closed for authenticated users when an object is outside the active scope. The API layer performs both method permission checks and strict object tenant checks.

For `CustodyReceipt`, neither layer may assume that `obj.tenant` exists. The authorization anchor is the receipt's asset tenant. Any implementation that calls the membership backend with an unscoped receipt queryset, or that relies on the holder's tenant instead of `asset__tenant`, is incomplete.

### 4.2 Public recipient versus internal operator errors

The target response classes are deliberately distinguishable:

| Situation | HTTP result | Body/rendering rule |
| --- | ---: | --- |
| Invalid, malformed, changed, or unknown token | `404` | Neutral invalid/unavailable response. No receipt, asset, holder, EULA, or token payload. |
| Known token/session whose recipient link has expired | `410` | Explicit expired response. No receipt payload. |
| Authenticated user is not the intended holder | `403` | `wrong-recipient` response. No payload and no mutation. It must not be replaced by the internal-permission wording. |
| Authenticated internal caller lacks `view_custodyreceipt` or `prepare_custodyreceipt` | `403` | Internal-custody-permission response. Never render the recipient error. |
| Receipt belongs to a foreign tenant on an internal route or API detail | `404` | Neutral not-found response to prevent cross-tenant existence disclosure. |
| Valid authorized prepare | `2xx` | Session/handoff result with operator and intended-holder binding; no signer impersonation. |

The order of checks matters. Invalid/foreign objects must not be rendered before the boundary decision. Expired and wrong-recipient paths must not render the receipt's asset, holder, or EULA data. A wrong recipient must not reach the mutation transaction.

## 5. Model permissions, seed, and authentication

### 5.1 Model permission declarations

On `origin/main`, `CustodyTemplate` and `CustodyReceipt` rely on generated Django model permissions; there are no custody-specific `Meta.permissions` entries in `itambox/compliance/models.py`. The generic codenames are therefore:

- `view_custodytemplate`, `add_custodytemplate`, `change_custodytemplate`, `delete_custodytemplate`;
- `view_custodyreceipt`, `add_custodyreceipt`, `change_custodyreceipt`, `delete_custodyreceipt`.

The target adds two explicit action permissions as model-level Django permissions:

- `prepare_custodyreceipt`; and
- `export_custodyreceipt`.

There is intentionally no `sign_custodyreceipt` grant for the Technician role. Recipient consent is a principal/binding rule, not an operator capability.

### 5.2 Seed and role resolution

The current seed in `itambox/core/management/commands/_seed/access.py:106-141` gives `Administrator` all permissions and gives `Technician` every non-delete permission in the operational apps. That current broad definition includes custody template mutation and generic receipt mutation and does not match the target matrix.

The target Technician custody grant is deliberately narrow:

- permit template/receipt viewing through the applicable `view_*` permissions;
- permit `prepare_custodyreceipt` in the effective tenant scope;
- do not grant `add/change/delete_custodytemplate` as a way to change custody policy; and
- do not grant `export_custodyreceipt` by default.

The exact effective permission set is still resolved by membership, role grants, grant scopes, and expiry; it is not inferred from the display name “Technician”.

`MembershipBackend` (`itambox/core/auth/__init__.py:55-69`, `:268-294`) unions permissions over valid tenant grants and resolves object permissions against the object's tenant. For receipts, the implementation must first establish the asset-derived tenant boundary. Superuser bypass applies to internal global visibility, but it does not bypass intended-recipient binding during consent.

## 6. Capability matrix

The matrix below describes effective permissions in the active/effective tenant scope. A “yes” in the recipient column always means **only when the principal is itself the intended holder**. It never means “an administrator may sign for somebody else”.

Each positive cell names the permission codename that makes the capability available. “No” means that the role has no such capability, not that a surrounding page may leak the underlying receipt. “Own/effective tenant” includes only the tenants reached by the membership grant and its scope; it is not a shortcut to global access. The recipient-signing cells are the sole exception to an internal permission requirement and are still bound to the intended holder, a valid token/session, and the consent state machine.

| Role / principal | See template | Manage templates in own permitted scope | Internal receipt list/detail | Prepare session | Recipient sign/consent | See finished receipt internally | Export |
| --- | --- | --- | --- | --- | --- | --- | --- |
|| **Superadmin** (`is_superuser`) | **Yes** — `compliance.view_custodytemplate`; global and tenant-scoped templates | **Yes** — `add_custodytemplate`, `change_custodytemplate`, `delete_custodytemplate`; global/tenant/group scope as allowed | **Yes** — `compliance.view_custodyreceipt`; all tenants through the privileged internal scope | **Implemented — Yes** — `compliance.prepare_custodyreceipt`, route `compliance:custodyreceipt_prepare`; all authorized tenants | **Only if self is the intended holder**; no admin override and no operator signer substitution | **Yes** — `compliance.view_custodyreceipt` | **Implemented — Yes** — `compliance.export_custodyreceipt`, route `compliance:custodyreceipt_export` |
|| **Administrator / Tenant Admin** | **Yes** — `compliance.view_custodytemplate`; own tenant and permitted shared scope | **Yes** — `add/change/delete_custodytemplate` in own permitted scope; no implicit global/group mutation | **Yes** — `compliance.view_custodyreceipt`; own/effective tenant only | **Implemented — Yes** — `compliance.prepare_custodyreceipt`, route `compliance:custodyreceipt_prepare`; own/effective tenant only | **Only as own intended holder**; no admin override | **Yes** — `compliance.view_custodyreceipt`; own/effective tenant | **Implemented — Yes** — `compliance.export_custodyreceipt`, route `compliance:custodyreceipt_export`; own/effective tenant |
|| **IT Technician** | **Yes** — `compliance.view_custodytemplate` | **No by default** — no custody template-policy mutation (`add/change/delete_custodytemplate`) | **Yes** — `compliance.view_custodyreceipt`; own/effective tenant and only scoped records | **Implemented — Yes** — `compliance.prepare_custodyreceipt`, route `compliance:custodyreceipt_prepare`; own/effective tenant | **Only if self is the intended holder**; no Technician signer permission and no operator consent | **Yes** — `compliance.view_custodyreceipt`; own/effective tenant | **No by default** — no `compliance.export_custodyreceipt` (route exists but is denied) |
| **Ordinary User / Recipient without internal custody role** | **No** internal template catalog permission | **No** | **No** internal receipt list/detail | **No** | **Yes only when self is the intended holder**, through a valid recipient token/session; no internal permission is needed for the recipient step | **Only the own completed result in the bound recipient flow**; not an internal catalog read | **No internal export**; only a separately specified own-recipient result, if ever introduced |
| **Unrelated user in the same tenant** | No receipt content through custody surfaces | **No** | **No** | **No** | **No** — `403` with `wrong-recipient`; no mutation and no receipt payload | **No** | **No** |
| **User from another tenant** | **No** for the foreign tenant | **No** | **No** — internal UI/API object access is neutral `404` | **No** — no cross-tenant session or recipient payload; deny without disclosure | **No**; a recipient request must not turn a cross-tenant object into a readable result | **No** | **No** — neutral `404`/deny according to the surface, never a payload |

### Matrix invariants

1. `view_custodyreceipt` is the internal read capability. It is not a recipient-signing capability.
2. `prepare_custodyreceipt` authorizes the operator handoff only. It cannot alter `holder`, `request.user`, signer identity, acceptance, or signature evidence.
3. `export_custodyreceipt` is independent of view and is not seeded to Technicians.
4. A superuser's global internal rights do not make the superuser the intended recipient.
5. The ordinary recipient's “yes” is a holder-binding decision, not a role grant.
6. Cross-tenant internal reads are 404, not a useful 403, and no error variant includes unverified domain data.

## 7. Slice D — Assisted-signing session, handoff, and export

Slice D implements the binding decision from §5.2 Option B: the operator prepares a short-lived server-side session, then the intended holder performs the consent step as a separate principal. The prepare action is not a signing action and does not change the receipt's acceptance state.

### 7.1 Session model and lifecycle

The persisted `CustodySigningSession` binds these values:

- `receipt`, with the tenant boundary derived through `receipt__asset__tenant`;
- `operator`, the authenticated prepare user;
- `intended_holder`, copied from the receipt and never supplied by the operator;
- its opaque one-time `token`;
- `created_at` and `expires_at`; and
- lifecycle fields `consumed_at`, `canceled_at`, and `outcome` (`accepted` or `declined`).

The default `CUSTODY_SIGNING_SESSION_TTL` is 30 minutes. It is independent of the seven-day public bearer-link TTL: expiry is checked server-side and an expired session cannot be used to reach the consent mutation. The derived status is `active`, `expired`, `consumed`, or `canceled`; a session is consumed atomically with the first terminal accept or decline operation. Reuse, guessing, changing the receipt identifier, and crossing a tenant boundary never produce a usable handoff. Session material is opaque and is not written to logs, changelog snapshots, or internal HTML.

### 7.2 Prepare flow

`POST` to `compliance:custodyreceipt_prepare` requires an authenticated internal caller with `compliance.prepare_custodyreceipt` on the receipt's asset tenant. The route resolves the receipt through the `asset__tenant` boundary before performing the capability check. It creates a new session for a pending receipt with a holder; multiple sessions may exist for the same receipt, but only an active, unconsumed, unexpired session can be handed off. Preparing a second session does not overwrite the first session or alter the receipt.

The request does not accept a replacement recipient. The holder is copied from the receipt by the server, and the operator is taken from the authenticated request. A receipt without a holder is rejected without creating a session. Missing capability returns the internal custody permission error, while a foreign receipt is neutral `404`; neither path uses the recipient-facing `wrong-recipient` response.

### 7.3 Recipient handoff and consent

The prepared session is handed to the intended recipient, who must authenticate as the holder before accepting or declining. The sign path validates the session, receipt, tenant, holder binding, expiry, and active state before entering the receipt transaction. The operator never becomes the signer. Accept stores the normal verification/signature data and consumes the session; decline stores the distinct declined terminal state and consumes the session. An expired, unknown, forged, already-consumed, other-receipt, or cross-tenant session is neutral and cannot mutate either the session or receipt. A wrong authenticated recipient receives `wrong-recipient` and cannot reach the mutation.

The existing public-link rules remain in force: unknown links use `custody_link_unavailable` (`404`), expired links use `custody_link_expired` (`410`), and the intended-recipient check uses `wrong_recipient` (`403`). Session-specific failures are `custody_session_unavailable` (`404`) for an unknown, malformed, other-receipt, or cross-tenant session, and `custody_session_expired_or_used` (`410`) for an expired, consumed, or canceled session. These responses never render receipt, asset, holder, EULA, token, or session payload.

### 7.4 Export route

`GET` to `compliance:custodyreceipt_export` is an authenticated internal action guarded independently by `compliance.export_custodyreceipt`. It uses the same asset-derived tenant boundary as the internal detail view and is available only for an accepted receipt. The response contains the verification data needed to validate the finished receipt, but never the raw public bearer token or an active signing/session secret.

The export route returns the internal custody permission error (`403`) when the caller lacks the capability, including the seeded Technician role. A pending receipt and a foreign-tenant receipt are not exportable and are returned as neutral `404` responses. Superadmin export remains available through the privileged internal scope.

### 7.5 Audit presentation

The internal receipt detail keeps the two principals distinguishable: the session history identifies the prepare operator and its creation/expiry/consumption timestamps, while the receipt identifies the authenticated consent recipient, consent time, terminal result, and verification data. Multiple prepare sessions remain separately auditable. Internal list/detail HTML contains no raw public token and no session secret; the audit view is therefore useful without recreating a bearer link.

## 8. Consent, expiry, and mutation semantics

The existing recipient implementation already uses a transaction and `select_for_update()` in `_process_custody_post()` (`itambox/compliance/views.py:87-149`). The target keeps the following invariants:

- invalid, changed, or unknown tokens do not mutate a receipt;
- expired recipient links return `410` and do not render receipt data;
- a wrong recipient returns `403 wrong-recipient` before the mutation transaction;
- an internal caller lacking the required custody permission receives the internal `403`, never the recipient error;
- the intended holder cannot be replaced by the prepare operator;
- an accept POST writes acceptance/signature evidence only after recipient binding and a non-empty signature;
- decline, empty signature, accepted, and already-declined states remain distinct;
- concurrent accept/decline submissions result in exactly one successful terminal transition under the row lock; and
- the REST API cannot write acceptance or signature fields directly because the serializer keeps them read-only.

`REQUIRE_CUSTODY_SIGNIN=True` is the production default. Tests also exercise the explicit `False` setting. In the safe `False` mode, disabling the login redirect does not authorize an anonymous consent POST: a bearer-link visitor still receives `403 recipient_authentication_required` unless authenticated as the intended holder. Whichever authentication mode is active, the implementation must preserve the distinction between the operator who prepares a session and the intended recipient who consents.

## 9. Decision log

These decisions are binding for issue #259 and supersede the unresolved recommendations in the original HOLD section of the readiness report.

| Decision | Binding outcome | Design reference |
| --- | --- | --- |
| On-site flow | **Option B**: a Technician-authorized, server-side bound prepare/signing session plus a separate internal receipt view. The operator and recipient are separate principals. | `issue-259-readiness.md`, §5.2, Option B; target implications in §5.3 and §6. |
| Error contract | Invalid/unknown token → **404** neutral; expired token/session → **410** without payload; wrong recipient → **403** `wrong-recipient`; missing internal permission → **403** with the internal-custody message; foreign tenant → **404**. | `issue-259-readiness.md`, §5.5 and draft criteria §6 “Token, Ablauf und Fehler”. |
| New action codenames | Add **`prepare_custodyreceipt`** and **`export_custodyreceipt`** as explicit Django model permissions. | `issue-259-readiness.md`, §5.1 proposed codenames and the issue decision recorded for implementation. |
| Technician seed | Narrow the custody-specific Technician grant to viewing plus preparation. **No export by default** and no custody-template policy mutation. | `issue-259-readiness.md`, §5.3 target matrix, together with the maintainer decision that the Technician seed is narrower. |
| Technician signing codename | Do **not** create or grant `sign_custodyreceipt` to Technicians. | `issue-259-readiness.md`, §5.1/§5.3: consent is not an internal administrative right. |
| Recipient principal | The intended Recipient remains the only signer/consenter. Superadmin, Tenant Admin, and Technician privileges never override the holder binding. | `issue-259-readiness.md`, §5.1, §5.2 Option B, and §6 “Prepare- und Consent-Semantik”. |
|| Sign-in policy | `REQUIRE_CUSTODY_SIGNIN=True` remains the production default. The explicit `False` mode is covered by regression tests and must not give an operator a recipient override. | `issue-259-readiness.md`, §5.4 “Configuration”, §6 “Token, Ablauf und Fehler”, plus the issue decision to test both settings. |
|| Assisted-signing session | **Option B is implemented:** prepare is a persisted, short-lived, one-time session bound to receipt, asset tenant, operator, and intended holder. Prepare never changes recipient identity or acceptance; only the authenticated intended holder can consume the handoff through consent. | `issue-259-readiness.md`, §5.2 Option B, §5.4, §6 “Prepare- und Consent-Semantik”; Slice D routes `compliance:custodyreceipt_prepare` and the recipient handoff. |
|| Export separation | **Implemented:** finished-receipt export is a separate route guarded by `export_custodyreceipt`, independent from internal view and denied to the seeded Technician role. | `issue-259-readiness.md`, §5.3/§5.5 and §6 “API und interne Darstellung”; Slice D route `compliance:custodyreceipt_export`. |

The readiness document remains a read-only design reference. If implementation details (for example, concrete URL names) differ from the conceptual surfaces above, the route declaration and tests are the final executable contract, but the permission separation, tenant boundary, error classes, and recipient-binding decisions are not optional.

## 10. Review checklist

- [ ] Every internal receipt query uses `asset__tenant` or an equivalent centralized boundary decision.
- [ ] No embedded table turns an internal “view” action into a raw-token signing link.
- [ ] Template visibility and template management remain separate from receipt view and prepare.
- [ ] Technician has view + prepare, but not template-policy mutation or export by default.
- [ ] There is no Technician sign codename or admin recipient override.
- [ ] Invalid, expired, wrong-recipient, insufficient-internal-permission, and foreign-tenant responses are distinguishable and payload-safe.
- [ ] Prepare cannot overwrite intended-holder identity.
- [ ] Recipient POST, row locking, terminal states, and API read-only acceptance fields are covered by tests.
- [ ] Both `REQUIRE_CUSTODY_SIGNIN=True` and `False` are tested without real credentials or bearer tokens.
