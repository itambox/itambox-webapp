# SCIM provisioning

ITAMbox implements [SCIM 2.0](https://datatracker.ietf.org/doc/html/rfc7644) Users and Groups resources for ordinary tenants and managing providers, with different write scopes per mount. The subset described on this page is the **frozen Stable contract**: within it, request and response behavior follows the compatibility promise of the current release line.

## Endpoints

| Scope | Base URL | Intended use |
|---|---|---|
| Tenant | `/api/tenants/<tenant_slug>/scim/v2/` | Provision tenant Users and read tenant-owned Groups |
| Provider | `/api/providers/<provider_slug>/scim/v2/` | Provision provider staff Users and provider-owned Groups |

Both mounts serve exactly these routes: `ServiceProviderConfig`, `Users`, `Users/<id>`, `Groups`, and `Groups/<id>`. The SCIM discovery endpoints `ResourceTypes` and `Schemas` are not served.

## Authentication

Authentication uses an HTTP Bearer token. Create a dedicated API token for an active, least-privilege service account; do not reuse a personal interactive token. For a non-superuser service account:

- scope the token to the target tenant or provider tenant;
- grant the token owner `organization.change_membership` in that scope;
- enable the token's `write_enabled` flag before using `POST`, `PUT`, `PATCH`, or `DELETE`;
- for provider Group operations, additionally grant `users.view_usergroup`, `users.add_usergroup`, `users.change_usergroup`, or `users.delete_usergroup` as required by the HTTP operation.

Read requests accept any valid token of the scoped owner; write requests additionally require the token's `write_enabled` flag. Expired tokens, inactive owners, and tokens scoped to a different tenant or provider are rejected with `401`, and no error response ever contains credential material.

SCIM never grants permissions. Provisioning creates an identity plus a membership (and, on tenant mounts, the linked asset holder profile); roles and permissions are granted in-app, so a provisioned account has no product access until an administrator grants it.

## Supported operations

| Scope and resource | Create | Read | Update | Delete |
|---|---|---|---|---|
| Tenant `Users` | Yes | Yes | Yes (`PUT`, `PATCH`) | Membership removal. The user is deactivated globally when no membership remains anywhere |
| Tenant `Groups` | No | Yes | No | No |
| Provider `Users` | Yes | Yes, also while the provider membership is inactive | Yes (`PUT`, `PATCH`), also while inactive | Membership removal, also while inactive. Same global deactivation rule |
| Provider `Groups` | Yes | Yes | Yes | Yes |

Tenant Group write requests return `403`; authorization changes remain explicit in-app operations. Provider Group writes require the corresponding `users` Group permissions and may include only active staff of that provider tenant. Provider synchronization reconciles SCIM-owned membership rows and preserves memberships created manually or by another identity source.

Both mounts share one strict request parser: the same document shapes are accepted and the same shapes are rejected with SCIM error envelopes. Unknown top-level resource keys (for example the enterprise extension or `schemas` extras) are ignored; attributes ITAMbox does not manage are explicit no-ops in `PATCH`, never silent destructive writes.

### Create and replace

`POST /Users` and `PUT /Users/<id>` accept `userName` (required, unique), `emails`, `name` (`givenName`, `familyName`), `active`, and `externalId`. `POST` is idempotent: replaying the same `userName` with the same `externalId` returns the existing resource (`200`) without rewriting identity; a genuine collision answers `409` with `scimType: uniqueness` and never hijacks the other identity.

### PATCH

`PATCH` accepts a `PatchOp` document (an `Operations` array of `add`, `replace`, and `remove` operations):

| Path | `add` / `replace` | `remove` |
|---|---|---|
| `active` | Sets the membership's active state | Rejected |
| `userName` | Renames the login | Rejected |
| `externalId` | Sets this mount's directory mapping | Clears the mapping |
| `emails`, `emails.value`, `emails[type eq "work"].value` | Sets the email | Clears the email |
| `name.givenName`, `name.familyName` | Sets the name part | Clears the name part |
| `name` (object with `givenName` / `familyName`) | Sets the supplied name parts | Rejected |

Unmanaged attributes (`displayName`, `nickName`, `title`, `userType`, `preferredLanguage`, `locale`, `timezone`, `profileUrl`, `employeeNumber`, phone numbers, addresses, photos, roles, groups, entitlements, the enterprise extension, and bracketed email paths for non-`work` types) are explicit no-ops: the operation is accepted and skipped, so identity providers that send supersets do not fail. Anything else (`move`, unknown paths, pathless operations without an unmanaged target) is rejected with `400`; `scimType` is included where the specification defines one, and a `remove` without a path answers `scimType: noTarget`. A single request may contain up to 1000 operations.

### Lifecycle: deactivate, deprovision, reprovision

- `active=false` always suspends only this mount's membership. It never touches other tenants' or providers' memberships.
- A resource stays addressable while inactive: `GET` and `PATCH` keep working, so an identity provider can inspect and re-enable it. Reactivating with `active=true` restores the membership immediately.
- The global login flag mirrors "has any active membership anywhere": when the last active membership is suspended, the account can no longer authenticate; a reactivation restores login.
- `DELETE` removes this mount's membership. The `User` row survives (deactivated when no membership remains anywhere); a later `POST` with the same `userName` and `externalId` re-provisions the membership and restores login without any manual account edit. De-provisioning in one tenant or provider never removes another scope's memberships.

## Filters

List requests accept the SCIM `filter` parameter with a single expression, `attribute operator value`:

- operators: `eq`, `ne`, `co`, `sw`, `ew`, `gt`, `ge`, `lt`, `le`, `pr`;
- filterable user attributes: `userName`, `email` / `emails` / `emails.value`, `externalId`, `active`, `id`, `displayName`;
- filterable group attributes: `displayName` / `name`, `externalId`, `id`;
- `emails[type eq "work"].value` is normalized to the email attribute; `id` accepts the opaque UUID or a legacy integer;
- `externalId` matching is case-sensitive; other string attributes are case-insensitive;
- logical operators (`and`, `or`, `not`), parentheses, and multi-expression filters are not supported and are rejected with `400`; expressions longer than 512 characters are rejected as well.

List responses are capped at 200 resources per request and advertise that cap in `ServiceProviderConfig`.

## ServiceProviderConfig

`GET ServiceProviderConfig` truthfully advertises the frozen subset: `patch` supported; `filter` supported with `maxResults` 200; `bulk`, `changePassword`, `sort`, and `etag` unsupported; exactly one authentication scheme (OAuth Bearer token). Values that are advertised as unsupported are not implemented, and implemented behavior is always advertised.

## Connect an identity provider

### Microsoft Entra ID

1. Create a non-gallery Enterprise Application.
2. Under **Provisioning**, select **Automatic**.
3. Choose the base URL for the required scope:
   - tenant URL for user provisioning and read-only group discovery;
   - provider URL when the identity provider must create or update provider-owned Groups.
4. Set **Secret Token** to the dedicated ITAMbox API token.
5. Test the connection against the pinned ITAMbox revision.
6. Review attribute mappings and provisioning scope before enabling the job.

Do not enable Group writes against the tenant endpoint; those operations are intentionally rejected.

### User attribute mapping

| Entra ID attribute | SCIM attribute | Notes |
|---|---|---|
| `userPrincipalName` | `userName` | Login username |
| `mail` | `emails[type=work].value` | Primary email |
| `displayName` | `displayName` | Accepted and skipped as an unmanaged attribute |
| `givenName` | `name.givenName` | Given name |
| `surname` | `name.familyName` | Family name |
| `accountEnabled` | `active` | Suspends or restores this mount's membership; see the lifecycle section |

## Not supported (by design)

- Bulk operations, password changes, sorting, and ETags / `If-Match` concurrency headers.
- `ResourceTypes` and `Schemas` discovery endpoints (see the endpoints section).
- Logical and multi-expression filters, and filtering by attributes outside the list above.
- Group nesting: Entra ID group hierarchies are flattened into the flat, tenant-owned groups on synchronization.

`ServiceProviderConfig` advertises filter support with a 200-resource maximum and OAuth Bearer authentication; ITAMbox accepts Bearer authentication only.
