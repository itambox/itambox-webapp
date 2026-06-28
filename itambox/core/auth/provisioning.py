"""Shared helpers for just-in-time (JIT) SSO membership provisioning.

The OIDC / SAML / LDAP backends all map an IdP-supplied group claim to a tenant
role and assign it on login. The group→role mapping itself is operator-defined
(trusted config), so auto-provisioning the mapped role — including a privileged
one — is the intended behaviour. The risks worth guarding are (a) no audit trail
of who got elevated to Admin/Manager, and (b) deployments that want JIT privilege
escalation locked down entirely.

This module centralises the assignment so that:
- every assignment of a privileged role (Admin/Manager) via a group claim is
  logged for audit, and
- operators can set ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES=False to refuse
  auto-creating a privileged role that does not already exist (it is downgraded
  to Member instead), without changing the default behaviour.
"""
import logging

logger = logging.getLogger('itambox.auth.sso')

# Roles considered privileged for audit-logging / hardening purposes.
PRIVILEGED_ROLE_NAMES = {'Admin', 'Manager'}


def provision_membership(user, tenant, db_role_name, permissions_for_role, source):
    """Resolve a tenant role for an SSO login and (re)assign the membership.

    `permissions_for_role` is a callable mapping a role name to its permission
    list (the backend's own get_permissions_for_role). `source` is a short label
    ('OIDC'/'SAML'/'LDAP') used in log lines.
    """
    from django.conf import settings
    from organization.models import Role, Membership

    autocreate_privileged = getattr(settings, 'ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES', True)
    is_privileged = db_role_name in PRIVILEGED_ROLE_NAMES
    username = getattr(user, 'username', user)
    tenant_slug = getattr(tenant, 'slug', tenant)

    role = Role.objects.filter(tenant=tenant, name=db_role_name).first()
    if role is None and is_privileged and not autocreate_privileged:
        logger.warning(
            "%s: group claim mapped user '%s' to privileged role '%s' in tenant '%s', but it "
            "does not exist and ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES is off; assigning Member.",
            source, username, db_role_name, tenant_slug,
        )
        role = _get_or_create_role(tenant, 'Member', permissions_for_role, source)
    else:
        if role is None:
            role = _get_or_create_role(tenant, db_role_name, permissions_for_role, source)
        if is_privileged:
            logger.warning(
                "%s: assigning privileged role '%s' to user '%s' in tenant '%s' via group claim.",
                source, db_role_name, username, tenant_slug,
            )

    # roles is an M2M now: ensure the membership exists, then make the SSO-resolved
    # role its role set (SSO is authoritative for the direct role, mirroring the old
    # single-FK overwrite-on-login behaviour).
    membership, _ = Membership.objects.get_or_create(user=user, tenant=tenant)
    membership.roles.set([role])
    return role


def _get_or_create_role(tenant, name, permissions_for_role, source):
    from organization.models import Role
    role, _ = Role.objects.get_or_create(
        tenant=tenant,
        name=name,
        defaults={
            'description': f'Auto-provisioned {name} role via {source}',
            'permissions': permissions_for_role(name),
        },
    )
    return role


def provision_provider_membership(user, provider, provider_role_name, source):
    """Resolve a Role for an SSO login and (re)assign the user's Membership.

    Used when an IdP group claim maps to a PROVIDER-level role (MSP staff), via the
    ``ITAMBOX_PROVIDER_<OIDC|SAML|LDAP>_CONFIGS`` ``*_GROUP_PROVIDER_ROLE_MAPPING`` settings.
    `source` is a short label ('OIDC'/'SAML'/'LDAP') used in log lines.

    Unlike tenant-role provisioning, a missing Role is NOT auto-created (provider
    roles are privileged and few): the assignment is logged and skipped. Every assignment
    is logged for audit. Returns the Membership, or None if the role was missing.
    """
    from organization.models import Role
    from organization.models import Membership

    username = getattr(user, 'username', user)
    provider_slug = getattr(provider, 'slug', provider)

    role = Role.objects.filter(provider=provider, name=provider_role_name).first()
    if role is None:
        logger.warning(
            "%s: group claim mapped user '%s' to provider role '%s' in provider '%s', but it "
            "does not exist; skipping provider membership assignment.",
            source, username, provider_role_name, provider_slug,
        )
        return None

    logger.warning(
        "%s: assigning provider role '%s' to user '%s' in provider '%s' via group claim.",
        source, provider_role_name, username, provider_slug,
    )
    membership, _ = Membership.objects.get_or_create(
        user=user, provider=provider,
        defaults={'person_type': Membership.PERSON_STAFF, 'tenant_scope': Membership.SCOPE_EXPLICIT},
    )
    membership.roles.add(role)
    if not membership.is_active:
        membership.is_active = True
        membership.save()
    return membership
