# API Tokens And Provisioning

API Tokens authenticate automation and integrations without using an interactive browser session. SCIM uses a suitably scoped token to provision identity data through the supported service-provider endpoints.

## API Tokens

Create a token for one integration purpose and give it only the access that integration needs. Token configuration can include tenant scope, write access, expiration, and network restrictions where supported.

The secret is shown when the token is created. Store it in a secret manager and do not place it in scripts, tickets, screenshots, or documentation.

A token's scope does not become broader because the token owner can use **All Tenants** in the browser. API authorization is evaluated from the token and current permissions.

Use the standard `Authorization: Token ...` header described in [REST and GraphQL](../integration/developer_guide.md).

## SCIM Provisioning

SCIM is intended for identity-provider-driven user lifecycle operations. Configure a tenant-scoped token with the required write access, then follow [SCIM Provisioning](../integration/scim.md) for endpoints, supported operations, and provider setup.

Do not use a broad human administrator token as a shortcut for SCIM. A dedicated integration credential is easier to rotate, audit, and revoke.

## Permissions

Tokens do not replace ITAMbox authorization. Tenant membership, roles/grants, object scope, and token properties all contribute to whether a request succeeds.

See [Permissions and Role Grants](../administration/permissions.md), [API Token](../models/users/token.md), and [Deployment Security](../security/deployment-security.md).
