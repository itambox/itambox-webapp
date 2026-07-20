# API Tokens, SCIM Provisioning & RBAC

This guide covers programmatic access to ITAMbox: API tokens for REST API
authentication, SCIM 2.0 for automated user provisioning, and the basics of
role-based access control (RBAC).

---

## API Tokens

API tokens authenticate REST API requests. Each token is owned by a user,
scoped to a tenant, and carries a set of capabilities (read/write, expiry,
IP restrictions) that determine what the bearer can do.

### Creating a Token

Tokens are created in the user profile page (**User Menu → Profile → API
Tokens**). You must have an active user account and at least one tenant
membership.

1. Navigate to your user profile and click **Add Token**.
2. Fill in the token attributes (see table below).
3. Click **Create**. The full plaintext token is displayed **once** —
   copy it immediately. After the page is refreshed, only the `key_preview`
   (first 8 characters) is shown.

| Attribute | Description |
|---|---|
| **Description** | Human-readable label (e.g. `CI/CD Pipeline`, `SCIM Provisioning`). |
| **Write Enabled** | When checked, the token authorises `POST`, `PUT`, `PATCH`, and `DELETE` requests. Uncheck for read-only access. |
| **Expires** | Optional expiry date. Tokens without an expiry never expire (not recommended for long-lived automation). |
| **Allowed IPs** | Optional CIDR list restricting which IP addresses can use this token (e.g. `["10.0.0.0/8", "192.168.1.5"]`). Leave blank to allow any source. |
| **Tenant** | The tenant this token is scoped to. API requests authenticated with this token operate within this tenant's data boundary. |

> [!IMPORTANT]
> The plaintext token secret is generated with `secrets.token_hex(20)` (40 hex
> characters). It is **never stored in plaintext** — only an HMAC-SHA256 digest
> and a short key preview are persisted. If you lose the plaintext, you must
> create a new token.

### Token Scope: Tenant vs Provider

A token is always scoped to one tenant:

- **Customer tenant token** — operates within that single tenant's data
  boundary. Used for tenant-local automation (asset imports, custom scripts,
  CI/CD pipelines).
- **Provider tenant token** — operates within the provider tenant's boundary.
  Provider-scoped tokens can access managed-tenant data when the token owner
  has appropriate managed reach (via RoleGrant scopes). This is how SCIM
  provisioning for managed tenants works — the SCIM service account's token
  is scoped to the provider tenant.

### Write-Enabled vs Read-Only

| Flag | Permitted Methods | Use Case |
|---|---|---|
| **Write Enabled** (checked) | `GET`, `POST`, `PUT`, `PATCH`, `DELETE` | Full automation, SCIM provisioning, data imports |
| **Read-Only** (unchecked) | `GET`, `HEAD`, `OPTIONS` | Dashboards, monitoring, read-only integrations |

> [!WARNING]
> Even with a write-enabled token, the token owner must hold the corresponding
> RBAC permissions. For example, creating an asset via the API requires both a
> write-enabled token **and** the `assets.add_asset` permission in the token's
> tenant scope. The token does not bypass the permission system.

### Token Expiry

Set the `expires` field to automatically revoke the token at a future date.
Tokens with no expiry (`expires=None`) are valid indefinitely. Best practice:

- **Service accounts** — set a reasonable expiry (90–365 days) and rotate
  before expiration.
- **Temporary access** — always set an expiry.
- **CI/CD pipelines** — use shorter expiries (30–90 days) with automated
  rotation in your pipeline.

### Key Hashing and `ITAMBOX_API_TOKEN_PEPPERS`

API tokens are stored as HMAC-SHA256 digests, keyed by **server-side peppers**.
This means that even if the database is compromised, the token digests cannot
be reversed without the pepper secrets.

Configure peppers in `.env`:

```bash
ITAMBOX_API_TOKEN_PEPPERS='{"1":"a-50-plus-character-random-secret-here-xxxxxxxxxxxxxxxx","2":"another-random-secret-for-rotation-yyyyyyyyyyyyyyyyyy"}'
```

| Property | Description |
|---|---|
| **Format** | JSON object: `{"<int-id>": "<secret-string>"}` |
| **Highest ID** | Peppers new tokens. When you add a new pepper with a higher ID, newly created tokens use it. |
| **All IDs** | Decrypt existing tokens. Never remove old pepper IDs while tokens hashed with them still exist — doing so makes those tokens unusable. |
| **Secret length** | At least 50 characters, random. Generate with `python -c "import secrets; print(secrets.token_urlsafe(64))"`. |
| **Fallback** | If unset, falls back to `SECRET_KEY` as a single pepper (acceptable for development; set dedicated peppers in production). |

### Token Rotation Best Practices

1. **Create the replacement token first** — generate a new token while the
   old one is still valid.
2. **Update all consumers** — scripts, CI/CD configs, SCIM provisioning
   configs — to use the new token.
3. **Verify** — confirm all integrations work with the new token.
4. **Delete the old token** — removes it from the database. Revoked tokens
   cannot be re-activated.
5. **Rotate peppers periodically** — append a new pepper ID to
   `ITAMBOX_API_TOKEN_PEPPERS` (new tokens use it automatically). Remove old
   peppers only after migrating or revoking all tokens hashed under them.

### Using a Token

Include the token in the `Authorization` header of API requests:

```bash
curl -H "Authorization: Token abc123def456..." \
     https://itam.example.com/api/assets/assets/
```

The token's tenant scope is applied automatically — you do not need to pass a
tenant header. IP restrictions are checked on every request; requests from
non-allowed IPs receive HTTP 403.

---

## SCIM 2.0 Provisioning

!!! warning "Status: Beta"
    SCIM behaviour and schema extensions may change during the prerelease
    series. Pin the deployed revision and test identity-provider mappings
    before enabling automatic provisioning.

ITAMbox implements [SCIM 2.0](https://datatracker.ietf.org/doc/html/rfc7644)
Users and Groups resources for automated identity lifecycle management. SCIM
allows identity providers like Microsoft Entra ID, Okta, or OneLogin to
provision, update, and deprovision user accounts and group memberships
automatically.

### Overview

SCIM provisioning in ITAMbox operates at two scopes:

| Scope | Base URL | Purpose |
|---|---|---|
| **Tenant** | `/api/tenants/<tenant_slug>/scim/v2/` | Provision users into a specific tenant. Read tenant-owned groups (read-only). |
| **Provider** | `/api/providers/<provider_slug>/scim/v2/` | Provision provider-staff users and manage provider-owned groups. |

Both scopes expose the standard SCIM endpoints: `ServiceProviderConfig`,
`Users`, `Users/<id>`, `Groups`, and `Groups/<id>`.

### Authentication

SCIM endpoints use **HTTP Bearer token** authentication. Create a dedicated
API token for a least-privilege service account — do not reuse personal tokens.

For a non-superuser service account, ensure:

1. The token is scoped to the target tenant or provider tenant.
2. The token owner has `organization.change_membership` permission in that
   scope.
3. The token is **write-enabled** for `POST`, `PUT`, `PATCH`, or `DELETE`
   operations.
4. For provider Group operations, the owner also needs the corresponding
   `users.*_usergroup` permissions.

### Supported Operations

| Scope & Resource | Create | Read | Update | Delete |
|---|---|---|---|---|
| Tenant `Users` | Yes | Yes | Yes | Yes (membership removal; user deactivates when no memberships remain) |
| Tenant `Groups` | No | Yes | No | No |
| Provider `Users` | Yes | While membership active | While membership active | Yes (membership removal; user deactivates when no memberships remain) |
| Provider `Groups` | Yes | Yes | Yes | Yes |

> [!IMPORTANT]
> Tenant Group write requests return `403` — authorization changes for tenant
> users remain explicit in-app operations. Provider Groups require the
> corresponding `users` Group permissions and may include only active staff
> of that provider tenant.

### Connecting Microsoft Entra ID

1. In Entra ID, create a **non-gallery Enterprise Application**.
2. Under **Provisioning**, select **Automatic**.
3. Set the **Tenant URL** to the appropriate base URL:
   - `/api/tenants/<tenant_slug>/scim/v2/` for tenant user provisioning
     and read-only group discovery.
   - `/api/providers/<provider_slug>/scim/v2/` when the identity
     provider needs to create or update provider-owned Groups.
4. Set **Secret Token** to the dedicated ITAMbox API token (as a Bearer token).
5. Test the connection.
6. Review attribute mappings and provisioning scope before enabling the job.

### User Attribute Mapping (Entra ID)

| Entra ID Attribute | SCIM Attribute | Notes |
|---|---|---|
| `userPrincipalName` | `userName` | Login username |
| `mail` | `emails[type eq "work"].value` | Primary email |
| `displayName` | `displayName` | Display value |
| `givenName` | `name.givenName` | Given name |
| `surname` | `name.familyName` | Family name |
| `accountEnabled` | `active` | Deactivation removes the resource from provider detail operations |

### Limitation: Reactivation After Deactivation

When a provider User is set to `active=false` via SCIM, the provider SCIM
endpoint returns `404` for that user and cannot reactivate, inspect, or delete
the resource. To manage the user through SCIM again, reactivate the provider
membership in ITAMbox first.

### Current Limitations

- **Bulk operations** — not supported (`POST /Bulk`).
- **Password changes** — not supported via SCIM. Use the IdP's native password
  management.
- **Sorting and ETags** — not supported.
- **List pagination** — capped at 200 resources per request.
- **Provider filtering** — Provider User and Group list endpoints do not apply
  SCIM `filter` parameters. Verify your IdP can operate with paged, unfiltered
  lists before enabling automatic provisioning.
- **Provider Group membership sync** — skips users who are not active staff of
  that provider. Provision Users first.

> [!NOTE]
> The `ServiceProviderConfig` endpoint currently overstates provider filtering
> support and advertises HTTP Basic authentication. Use **Bearer token
> authentication only** and treat provider filtering as unsupported until the
> implementation and metadata agree.

For the full SCIM specification details including endpoint schemas, operation
semantics, and provider-specific configuration, see the dedicated SCIM
integration guide:
[docs/integration/scim.md](../integration/scim.md).

---

## RBAC Basics

ITAMbox uses a unified role-based access control (RBAC) system with one
container type (Tenant), one permission vocabulary, and additive grants.

### Core Concepts

| Concept | Description |
|---|---|
| **Tenant** | The data-isolation boundary. Every object lives in one tenant. |
| **Membership** | A user's binding to one tenant — the "person belongs here" anchor. |
| **Role** | A named set of permission codenames (e.g. `assets.view_asset`, `assets.add_asset`). Owned by one tenant. |
| **RoleGrant** | Assigns a role to a membership or UserGroup, with scopes defining where the role applies. |
| **RoleGrantScope** | Child of RoleGrant defining the reach: own-tenant, specific managed tenants, tenant groups, or all managed tenants. |
| **UserGroup** | A flat application group owned by one tenant. Memberships are added via `GroupMembership` rows. |
| **GroupMembership** | Links a tenant membership to a UserGroup, making the user inherit all role grants on that group. |

### Permission Resolution

When checking `user.has_perm('assets.view_asset')`, the system:

1. **Superuser** — always returns `True`.
2. **Inactive user** (`is_active=False`) — always returns `False`.
3. **Resolves the tenant context** — from the object's `tenant` attribute,
   the ambient current tenant, or the group-scoped tenant set.
4. **Collects effective permissions** — the frozen union of:
   - Direct `RoleGrant` rows on the user's Membership in the target tenant.
   - `RoleGrant` rows on any UserGroup the user belongs to in that tenant.
   - Each grant's permissions are additive; deny is unsupported.
5. **Checks scope coverage** — the grant's `RoleGrantScope` rows must include
   the target tenant.

Permissions are cached per-tenant on the user object for the duration of the
request (`_perms_tenant_<pk>`), so repeated `has_perm` calls in a single
request cost at most three database queries.

### Role Scopes

| Scope Type | Meaning |
|---|---|
| `own` | The role applies only in the principal's own tenant. |
| `tenant` | The role applies in one specific managed tenant. |
| `tenant_group` | The role applies in all tenants under a specific TenantGroup (and its descendants). |
| `all_managed` | The role applies in every tenant managed by the role's owning provider. |

> [!WARNING]
> Managed scopes (`tenant`, `tenant_group`, `all_managed`) require that the
> principal tenant **is** the role's owning provider and that the role owner
> has `is_provider=True`. Cross-tenant access is explicitly modelled — there
> is no implicit propagation.

### Provider (MSP) Mode

A tenant with `is_provider=True` is a **managing tenant**. Other tenants can
set their `managed_by` field to point to a provider (depth-1 only — chains are
not supported). Provider-staff users hold a Membership in the provider tenant
and are granted roles with managed scopes to reach into managed tenants.

### User Groups

User Groups (`users.UserGroup`) are flat, tenant-owned containers. They do not
nest — there is deliberately no group-in-group relation. External directory
nesting (LDAP OU trees, Entra ID group hierarchies) is **flattened** into
`GroupMembership` rows during SSO/SCIM sync.

A User Group:
- Is owned by exactly one tenant (cannot be reassigned after creation).
- May carry `RoleGrant` rows that apply to all its members.
- May have its memberships sourced from manual assignment, SCIM, LDAP, OIDC,
  or SAML.

### Role Best Practices

1. **Least privilege** — create narrow roles with only the permissions needed
   for a specific job function, then compose access by granting multiple roles.
2. **Use groups for shared access** — grant roles to User Groups rather than
   individual memberships for teams. Add/remove people from the group instead
   of managing individual grants.
3. **Expire privileged grants** — direct grants of Admin/Manager roles require
   a `valid_until` date and a `reason`. The system enforces this at the model
   level.
4. **Audit regularly** — review role grants (Organization → Memberships →
   Grants tab) and remove stale assignments.

### Permission Vocabulary

Permissions follow Django's `app_label.codename` convention:

```
assets.view_asset       assets.add_asset        assets.change_asset      assets.delete_asset
licenses.view_license   licenses.add_license    licenses.change_license  licenses.delete_license
organization.view_tenant organization.add_tenant ...
users.view_user         users.add_user          users.change_user        users.delete_user
extras.view_dashboard   extras.change_dashboard  ...
```

The full list of available permissions is determined by the installed Django
apps and their registered models.

---

## Related Documentation

- [SCIM Integration Guide](../integration/scim.md) — full SCIM spec details
- [SSO & MFA](sso-and-mfa.md) — identity provider configuration
- [Tenants](../models/organization/tenant.md) — tenant model and MSP mode
- [Users](../models/users/user.md) — user account model
- [Webhooks & Automation](webhooks-and-automation.md) — event-driven integrations
