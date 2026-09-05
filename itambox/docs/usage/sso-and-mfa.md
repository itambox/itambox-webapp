# Sign-In, SSO, And MFA

ITAMbox supports local accounts and external identity-provider sign-in. Deployment configuration determines which SSO providers are available and whether local users must complete multi-factor authentication.

## Local Sign-In

Local users sign in with their ITAMbox credentials when local authentication is enabled. Account state and login policy still apply even when the username/password is correct.

## Single Sign-On

Supported SSO integrations include LDAP-backed authentication and configured SAML or OpenID Connect providers. The identity provider controls its own authentication ceremony, including any MFA it requires.

ITAMbox maps the authenticated identity to an application user and then applies ITAMbox permissions and tenant scope. SSO does not bypass tenant authorization.

Administrators should use [Authentication Configuration](../configuration/authentication.md) for provider settings and rollout behavior.

## Multi-Factor Authentication

When MFA is enforced for an applicable local account, the user must enroll a second factor before continuing normal use. Backup codes are single-use recovery credentials and should be stored separately from the primary password/device.

SSO users normally satisfy second-factor requirements at the identity provider rather than enrolling a duplicate local factor solely because ITAMbox supports MFA for local accounts.

## Account And Access Problems

If sign-in succeeds but an expected tenant or action is missing, investigate membership and permissions rather than the identity provider first. See [Permissions and Role Grants](../administration/permissions.md).

For irreversible OIDC identity-binding upgrade behavior, follow the operator runbook in [Upgrades](../operations/upgrades.md).
