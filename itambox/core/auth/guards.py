"""Model-level privilege-escalation guard.

Privilege-escalation prevention ("you cannot grant a permission you do not
hold") historically lived only in form ``clean()`` methods. That left every
other write path — DRF serializers, SCIM provisioning, management commands,
direct model saves — free to assign arbitrary permissions. This module
centralises the check so a single utility can be called from any entry point.

The guard is deliberately tenant-explicit: it resolves the granting user's
*effective* permissions in a specific tenant (via the canonical
``TenantMembershipBackend._effective_perms``) rather than relying on the ambient
current-tenant contextvar. Superusers bypass it (trusted by definition).
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.auth import MembershipBackend


def validate_permission_grant(granting_user, permissions, container):
    """Raise ``ValidationError`` if ``granting_user`` may not grant ``permissions``.

    A non-superuser may only grant permission codenames they themselves hold in the
    target container (a ``Tenant`` for tenant-scoped grants, or a ``Provider`` for
    provider-scoped grants). ``permissions`` is any iterable of codename strings.

    Superusers, an absent granting user, and an empty permission set are all
    no-ops. When ``container`` is ``None`` a non-superuser holds nothing, so any
    non-empty grant is rejected (fail closed).
    """
    if granting_user is None:
        return
    if getattr(granting_user, 'is_superuser', False):
        return

    requested = set(permissions or [])
    if not requested:
        return

    backend = MembershipBackend()
    if container is None:
        held = frozenset()
    else:
        from organization.models import Provider
        if isinstance(container, Provider):
            held = backend._effective_perms_for_provider(granting_user, container)
        else:
            held = backend._effective_perms_for_tenant(granting_user, container)

    escalated = sorted(requested - set(held))
    if escalated:
        raise ValidationError(
            _("Privilege escalation detected: you cannot grant permissions you do "
              "not hold: %(perms)s") % {'perms': ', '.join(escalated)}
        )


def validate_group_membership_grant(granting_user, group):
    """Raise ``ValidationError`` if adding a member to ``group`` would let
    ``granting_user`` confer permissions they do not themselves hold.

    Adding a user to a UserGroup is a grant: the user inherits every role the group
    carries, plus access to each role's container. So for each role the group carries,
    the granting user must already hold every one of that role's permissions in the
    role's OWN container (``role.owner`` — the role's tenant OR provider). Each role is
    validated independently and the failures are aggregated into one message.

    Superusers and an absent granting user are no-ops (trusted / nothing to check).
    """
    if granting_user is None:
        return
    if getattr(granting_user, 'is_superuser', False):
        return

    # Resolve the group's roles tenant-context-independently. For a PERSISTED UserGroup,
    # ``group.roles.all()`` goes through Role's default (tenant-scoping) manager, which — in a
    # global/provider context with no matching active tenant (the normal state for a group
    # admin) — silently returns [], so the guard would iterate nothing and let an
    # over-privileged grant slip through. Query via Role._base_manager on the M2M reverse
    # relation instead (tenant-context-independent). The transient ``_RolesHolder`` shim used
    # by the create/edit form has no pk; its roles already come from Role._base_manager, so use
    # them directly.
    # inline import: avoid AppRegistryNotReady at module load.
    from organization.models import Role
    if getattr(group, 'pk', None) is not None:
        roles = Role._base_manager.filter(user_groups=group, deleted_at__isnull=True)
    else:
        roles = group.roles.all()

    errors = []
    for role in roles:
        try:
            validate_permission_grant(granting_user, role.permissions or [], role.owner)
        except ValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise ValidationError(errors)
