# SCIM provisioning

!!! warning "Status: Beta"
    SCIM behavior and schema extensions may change during the prerelease series. Pin the deployed revision and test identity-provider mappings before enabling automatic provisioning.

ITAMbox implements [SCIM 2.0](https://datatracker.ietf.org/doc/html/rfc7644) Users and Groups resources with different write scopes for ordinary tenants and managing providers.

## Endpoints

| Scope | Base URL | Intended use |
|---|---|---|
| Tenant | `/api/tenants/<tenant_slug>/scim/v2/` | Provision tenant Users and read tenant-owned Groups |
| Provider | `/api/providers/<provider_slug>/scim/v2/` | Provision provider staff Users and provider-owned Groups |

Authentication uses an HTTP Bearer token. Create a dedicated API token for an active, least-privilege service account; do not reuse a personal interactive token. For a non-superuser service account:

- scope the token to the target tenant or provider tenant;
- grant the token owner `organization.change_membership` in that scope;
- enable the token's `write_enabled` flag before using `POST`, `PUT`, `PATCH`, or `DELETE`;
- for provider Group operations, additionally grant `users.view_usergroup`, `users.add_usergroup`, `users.change_usergroup`, or `users.delete_usergroup` as required by the HTTP operation.

Both base URLs expose `ServiceProviderConfig`, `Users`, `Users/<id>`, `Groups`, and `Groups/<id>`.

## Supported operations

| Scope and resource | Create | Read | Update | Delete |
|---|---|---|---|---|
| Tenant `Users` | Yes | Yes | Yes | Membership removal; user deactivates when no memberships remain |
| Tenant `Groups` | No | Yes | No | No |
| Provider `Users` | Yes | While provider membership is active | While provider membership is active | Membership removal while active; user deactivates when no memberships remain |
| Provider `Groups` | Yes | Yes | Yes | Yes |

Tenant Group write requests return `403`; authorization changes remain explicit in-app operations. Provider Group writes require the corresponding `users` Group permissions and may include only active staff of that provider tenant. Provider synchronization reconciles SCIM-owned membership rows and preserves memberships created manually or by another identity source.

Provider User detail operations resolve only active provider memberships. After `active=false`, the same provider SCIM endpoint returns `404` for that user and cannot reactivate, inspect, or delete the resource. Reactivate the provider membership in ITAMbox before managing it through SCIM again.

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

Provider User and Group list endpoints currently ignore the SCIM `filter` parameter. Verify that the identity provider can operate with paged, unfiltered lists before enabling automatic provisioning; do not rely on filter-based discovery at provider scope.

### User attribute mapping

| Entra ID attribute | SCIM attribute | Notes |
|---|---|---|
| `userPrincipalName` | `userName` | Login username |
| `mail` | `emails[type=work].value` | Primary email |
| `displayName` | `displayName` | Display value |
| `givenName` | `name.givenName` | Given name |
| `surname` | `name.familyName` | Family name |
| `accountEnabled` | `active` | Deactivation removes the resource from provider detail operations; reactivation currently requires an in-app membership change |

## Current limitations

- Bulk operations, password changes, sorting, and ETags are not supported.
- List responses are capped at 200 resources per request.
- Provider User and Group list endpoints do not apply SCIM filters. Tenant filtering is separate and must still be tested with the selected identity provider.
- Provider Group membership sync skips users who are not active staff of that provider; provision Users first.
- `ServiceProviderConfig` currently overstates provider filtering and advertises HTTP Basic authentication. Use Bearer authentication only and treat provider filtering as unsupported until the implementation and metadata agree.
