"""Privilege-escalation guards.

"You cannot grant what you do not hold." The guard is enforced per write path
(forms/views), not at the model layer — every role/assignment/group write path
must call in here. The per-surface escalation test suite
(organization/tests/test_escalation_surface.py) is the net that keeps new write
paths honest.

Containers are always Tenants now: administering a provider (MSP) tenant is just
holding permissions inside that tenant, and reach into managed tenants is a
property of the individual RoleAssignment, validated by
:func:`validate_assignment_grant`.
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.auth import MembershipBackend


def validate_permission_grant(granting_user, permissions, tenant):
    """Raise ``ValidationError`` if ``granting_user`` may not grant ``permissions``.

    A non-superuser may only grant permission codenames they themselves hold in the
    target ``tenant``. ``permissions`` is any iterable of codename strings.

    Superusers, an absent granting user, and an empty permission set are all
    no-ops. When ``tenant`` is ``None`` a non-superuser holds nothing, so any
    non-empty grant is rejected (fail closed).
    """
    if granting_user is None:
        return
    if getattr(granting_user, 'is_superuser', False):
        return

    requested = set(permissions or [])
    if not requested:
        return

    if tenant is None:
        held = frozenset()
    else:
        held = MembershipBackend()._effective_perms_for_tenant(granting_user, tenant)

    escalated = sorted(requested - set(held))
    if escalated:
        raise ValidationError(
            _("Privilege escalation detected: you cannot grant permissions you do "
              "not hold: %(perms)s") % {'perms': ', '.join(escalated)}
        )


def validate_assignment_grant(granting_user, role, membership_tenant, reach='own',
                              requested_tenant_ids=None):
    """Guard creating/editing one RoleAssignment.

    Always: the role's permissions must be ⊆ the actor's effective permissions in
    ``membership_tenant`` (via :func:`validate_permission_grant`).

    For ``reach='managed'`` additionally:
      * the actor must hold ``organization.add_roleassignment`` or
        ``organization.change_roleassignment`` in ``membership_tenant``, and
      * the actor's OWN managed coverage must be a superset of the requested
        coverage — you cannot hand out broader reach than you have.
        ``requested_tenant_ids=None`` means SCOPE_ALL was requested (callers
        resolve tenant-group refinements to concrete ids before calling).

    Superusers and an absent granting user are no-ops.
    """
    if granting_user is None:
        return
    if getattr(granting_user, 'is_superuser', False):
        return

    validate_permission_grant(
        granting_user, getattr(role, 'permissions', None) or [], membership_tenant,
    )
    if reach != 'managed':
        return

    if not (
        granting_user.has_perm('organization.add_roleassignment', obj=membership_tenant)
        or granting_user.has_perm('organization.change_roleassignment', obj=membership_tenant)
    ):
        raise ValidationError(_(
            "You are not allowed to grant reach into managed tenants."
        ))

    # inline import: core.auth.guards -> organization would cycle at module load.
    from organization.models import RoleAssignment

    own_ids = set()
    own_all = False
    for assignment in RoleAssignment.objects.filter(
        reach=RoleAssignment.REACH_MANAGED,
        membership__user=granting_user,
        membership__tenant=membership_tenant,
        membership__is_active=True,
    ).select_related('scope_group', 'membership'):
        if (assignment.managed_scope or RoleAssignment.SCOPE_EXPLICIT) == RoleAssignment.SCOPE_ALL:
            own_all = True
            break
        own_ids |= assignment.scoped_tenant_ids()

    if own_all:
        return
    if requested_tenant_ids is None:
        raise ValidationError(_(
            "You cannot grant reach into all managed tenants — your own reach is narrower."
        ))
    missing = set(requested_tenant_ids) - own_ids
    if missing:
        raise ValidationError(_(
            "You cannot grant reach into tenants outside your own reach."
        ))


def validate_group_membership_grant(granting_user, group):
    """Raise ``ValidationError`` if adding a member to ``group`` would let
    ``granting_user`` confer permissions they do not themselves hold.

    Adding a user to a UserGroup is a grant: the user inherits every role the group
    carries in that role's owning tenant. So for each role the group carries, the
    granting user must already hold every one of that role's permissions in the
    role's own tenant. Each role is validated independently and the failures are
    aggregated into one message.

    Superusers and an absent granting user are no-ops (trusted / nothing to check).
    """
    if granting_user is None:
        return
    if getattr(granting_user, 'is_superuser', False):
        return

    # Resolve the group's roles tenant-context-independently: for a PERSISTED group the
    # default (tenant-scoping) Role manager can silently return [] outside a matching
    # tenant context, so query via Role._base_manager. The transient ``_RolesHolder``
    # shim used by the create/edit form has no pk; its roles already come from
    # Role._base_manager, so use them directly.
    # inline import: avoid AppRegistryNotReady at module load.
    from organization.models import Role
    if getattr(group, 'pk', None) is not None:
        roles = Role._base_manager.filter(user_groups=group, deleted_at__isnull=True)
    else:
        roles = group.roles.all()

    errors = []
    for role in roles:
        try:
            validate_permission_grant(granting_user, role.permissions or [], role.tenant)
        except ValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise ValidationError(errors)
