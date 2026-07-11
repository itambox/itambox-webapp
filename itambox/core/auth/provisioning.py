"""Shared helpers for just-in-time (JIT) SSO membership provisioning.

The OIDC / SAML / LDAP backends all map an IdP-supplied group claim to a tenant
role and assign it on login. The group→role mapping itself is operator-defined
(trusted config), so auto-provisioning the mapped role — including a privileged
one — is the intended behaviour. These helpers are therefore DELIBERATELY
UNGUARDED: they do not call validate_assignment_grant/validate_permission_grant,
because there is no acting admin — the "granting user" is the operator who wrote
the IdP mapping. The risks worth guarding are (a) no audit trail of who got
elevated to Admin/Manager, and (b) deployments that want JIT privilege
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

    Deliberately unguarded (trusted operator config) — see the module docstring.
    """
    from django.conf import settings
    from organization.models import Role, Membership, RoleAssignment

    autocreate_privileged = getattr(settings, 'ITAMBOX_SSO_AUTOCREATE_PRIVILEGED_ROLES', True)
    is_privileged = db_role_name in PRIVILEGED_ROLE_NAMES
    username = getattr(user, 'username', user)
    tenant_slug = getattr(tenant, 'slug', tenant)

    # _base_manager: SSO machinery must resolve roles independent of the ambient
    # tenant context (the tenant-scoped default manager fails closed to .none()).
    role = Role._base_manager.filter(
        tenant=tenant, name=db_role_name, deleted_at__isnull=True,
    ).first()
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

    # SSO is authoritative for the direct own-reach grant set (mirroring the old
    # overwrite-on-login behaviour): the resolved role becomes the membership's ONLY
    # own-reach assignment. Managed-reach grants are left untouched — they are
    # provisioned separately (provision_provider_membership) or granted by admins.
    membership, _ = Membership.objects.get_or_create(user=user, tenant=tenant)
    membership.assignments.filter(reach=RoleAssignment.REACH_OWN).exclude(role=role).delete()
    RoleAssignment.objects.get_or_create(
        membership=membership, role=role, reach=RoleAssignment.REACH_OWN,
    )
    return role


def _get_or_create_role(tenant, name, permissions_for_role, source):
    from organization.models import Role
    # _base_manager + explicit soft-delete filter: see provision_membership.
    role = Role._base_manager.filter(
        tenant=tenant, name=name, deleted_at__isnull=True,
    ).first()
    if role is None:
        role = Role._base_manager.create(
            tenant=tenant,
            name=name,
            description=f'Auto-provisioned {name} role via {source}',
            permissions=permissions_for_role(name),
        )
    return role


def provision_provider_membership(user, provider_tenant, role_name, source):
    """Provision MSP-staff access for an SSO login at a managing tenant.

    Used when an IdP group claim maps to a role owned by the MANAGING
    (``is_provider``) tenant — configured under the managing tenant's slug in
    ``ITAMBOX_TENANT_<OIDC|SAML|LDAP>_CONFIGS`` via the
    ``*_GROUP_PROVIDER_ROLE_MAPPING`` dicts. Creates/reactivates the user's
    Membership AT the managing tenant and a managed-reach RoleAssignment for the
    mapped role. The grant starts with empty explicit coverage
    (``managed_scope='explicit'``, no assigned tenants) — an admin refines which
    managed tenants it reaches, matching the pre-collapse default.

    Unlike tenant-role provisioning, a missing Role is NOT auto-created (staff
    roles are privileged and few): the assignment is logged and skipped. Every
    assignment is logged for audit. Returns the Membership, or None if the role
    was missing. Deliberately unguarded (trusted operator config) — see the
    module docstring; ``granted_by`` stays None (system/IdP-mapped grant).
    """
    from organization.models import Role, Membership, RoleAssignment

    username = getattr(user, 'username', user)
    provider_slug = getattr(provider_tenant, 'slug', provider_tenant)

    if not getattr(provider_tenant, 'is_provider', False):
        # Misconfiguration guard: a managed-reach assignment at a non-provider tenant
        # fails model validation; refuse cleanly instead of breaking the SSO login.
        logger.warning(
            "%s: staff role mapping targets tenant '%s' which is not a managing "
            "(is_provider) tenant; skipping staff assignment for user '%s'.",
            source, provider_slug, username,
        )
        return None

    # _base_manager: the ambient tenant context during SSO login is the CUSTOMER
    # tenant, so the tenant-scoped manager would never see the managing tenant's roles.
    role = Role._base_manager.filter(
        tenant=provider_tenant, name=role_name, deleted_at__isnull=True,
    ).first()
    if role is None:
        logger.warning(
            "%s: group claim mapped user '%s' to staff role '%s' at managing tenant '%s', "
            "but it does not exist; skipping staff assignment.",
            source, username, role_name, provider_slug,
        )
        return None

    logger.warning(
        "%s: assigning staff role '%s' (managed reach) to user '%s' at managing tenant '%s' "
        "via group claim.",
        source, role_name, username, provider_slug,
    )
    membership, _ = Membership.objects.get_or_create(user=user, tenant=provider_tenant)
    RoleAssignment.objects.get_or_create(
        membership=membership, role=role, reach=RoleAssignment.REACH_MANAGED,
        defaults={'managed_scope': RoleAssignment.SCOPE_EXPLICIT},
    )
    if not membership.is_active:
        membership.is_active = True
        membership.save()
    return membership
