# SCIM Provisioning

!!! warning "Status: Beta"
    SCIM provisioning is Beta. The endpoint behaviour is stable for RFC 7644 core operations,
    but schema extensions and advanced filtering are still evolving.
    Breaking changes will be noted in the changelog.

ITAMbox implements a [SCIM 2.0](https://datatracker.ietf.org/doc/html/rfc7644) provisioning endpoint
that allows identity providers (Azure AD / Entra ID, Okta, JumpCloud, …) to automatically create,
update, and deactivate user accounts.

## Endpoint

```
/api/scim/v2/
```

Authentication uses **Bearer token** (generate an API token from your user profile).

## Supported Operations

| Resource | Create | Read | Update | Delete |
|---|---|---|---|---|
| `Users` | ✅ | ✅ | ✅ | ✅ (soft-deactivate) |
| `Groups` | — | — | — | — |

Group provisioning is not yet implemented.

## Connecting an Identity Provider

### Azure AD / Entra ID

1. In Entra ID, create a new **Enterprise Application** → **Non-gallery**.
2. Under **Provisioning**, set mode to **Automatic**.
3. Set **Tenant URL** to `https://itam.example.com/api/scim/v2/`.
4. Set **Secret Token** to a valid ITAMbox API token.
5. Save and click **Test Connection**.
6. Enable provisioning and assign users/groups to the application.

### Attribute Mapping

| Entra ID attribute | SCIM attribute | Notes |
|---|---|---|
| `userPrincipalName` | `userName` | Used as the login username |
| `mail` | `emails[type=work].value` | Primary email |
| `displayName` | `displayName` | Shown in the UI |
| `givenName` | `name.givenName` | |
| `surname` | `name.familyName` | |
| `accountEnabled` | `active` | Deactivated users cannot log in |

## Known Limitations

- Group provisioning is not implemented (Beta scope).
- Filter expressions beyond `eq` on `userName` and `externalId` may not be supported.
- TypeScript-side i18n for provisioning status messages is in English only.
