# Permissions And Role Grants

ITAMbox authorization combines user identity, tenant access, roles/grants, object scope, and the action being attempted. A workspace changes context; it does not create permission.

## Tenant Membership

Membership connects a user to a tenant and can carry role information. In an MSP, one technician can have memberships in several customer tenants without those tenants becoming visible to one another.

## Roles And Grants

Roles group permissions for a tenant. Additional grant mechanisms can extend access for specific provider/resource relationships where supported. Prefer role design that mirrors job responsibilities instead of granting broad permissions individually to many users.

## Effective Access

When troubleshooting access, answer these questions in order:

1. Is the user active and able to sign in?
2. Is the target tenant within the user's accessible scope?
3. Does the user have the required action permission for that tenant/object?
4. Is the current workspace hiding the object even though the user could access it elsewhere?
5. Is the object in a lifecycle state that makes the action unavailable?

A 404 can be an intentional fail-closed response for an inaccessible cross-tenant object. Do not infer that the record does not exist merely from another user's URL.

## API Tokens

Token authentication adds token properties such as tenant scope, write access, expiration, and network restrictions. The underlying user/token authorization still applies. See [API Tokens and Provisioning](../usage/api-tokens-and-scim.md).

For tenant resource-grant expiry operations, see [Resource Grant Expiry](../operations/resource-grant-expiry.md).
