# Authentication Configuration

Authentication is deployment-controlled. Configure only the providers your organization operates and test changes in a non-production environment before cutting over users.

## Local Authentication And MFA

Production settings can enforce MFA for applicable local users. Keep recovery procedures for lost authenticators and protect backup codes as credentials.

## LDAP

LDAP configuration defines the directory connection and how a successful directory identity maps to an ITAMbox user. Native Windows development environments may not provide the LDAP dependency used by supported Linux/Docker deployments, so validate LDAP in the same platform family used for production.

## SAML

SAML requires matching identity-provider and service-provider metadata, callback/ACS values, certificates, and claim mapping. Use the externally reachable ITAMbox URL when configuring the identity provider.

## OpenID Connect

OIDC configuration defines issuer/provider endpoints, client credentials, callback behavior, and claim mapping. Follow the [Upgrade](../operations/upgrades.md) runbook for the identity-binding rollout: legacy claim matching must not be treated as a safe permanent fallback after the binding migration.

## SSO And MFA Relationship

An external identity provider performs its own authentication and MFA. ITAMbox still applies application authorization after identity resolution. Local MFA enforcement should not be described as proof that an SSO user completed a second factor inside ITAMbox.

See [Sign-In, SSO, and MFA](../usage/sso-and-mfa.md) for the user-facing behavior and [Deployment Security](../security/deployment-security.md) for hardening guidance.
