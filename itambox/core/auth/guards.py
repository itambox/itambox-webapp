"""Privilege-escalation guards for canonical RoleGrant writes."""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.auth import MembershipBackend


def validate_permission_grant(granting_user, permissions, tenant):
    """Enforce "you cannot grant permissions you do not hold"."""
    if granting_user is None or getattr(granting_user, 'is_superuser', False):
        return
    requested = set(permissions or [])
    if not requested:
        return
    held = (
        MembershipBackend()._effective_perms_for_tenant(granting_user, tenant)
        if tenant is not None else frozenset()
    )
    escalated = sorted(requested - set(held))
    if escalated:
        raise ValidationError(_(
            "Privilege escalation detected: you cannot grant permissions you do "
            "not hold: %(perms)s"
        ) % {'perms': ', '.join(escalated)})


def validate_role_grant(
    granting_user,
    role,
    principal_tenant,
    *,
    scope_type='own',
    requested_tenant_ids=None,
):
    """Validate permission content and managed coverage for one RoleGrant aggregate."""
    if granting_user is None or getattr(granting_user, 'is_superuser', False):
        return

    validate_permission_grant(
        granting_user,
        getattr(role, 'permissions', None) or [],
        principal_tenant,
    )
    if scope_type == 'own':
        return

    if not (
        granting_user.has_perm('organization.add_rolegrant', obj=principal_tenant)
        or granting_user.has_perm('organization.change_rolegrant', obj=principal_tenant)
    ):
        raise ValidationError(_("You are not allowed to grant reach into managed tenants."))

    # inline imports: avoid core.auth <-> organization import cycles at load time.
    from organization.models import RoleGrantScope, Tenant
    from organization.rbac import applicable_grants

    requested_permissions = set(getattr(role, 'permissions', None) or [])
    own_ids = set()
    all_managed_permissions = set()
    has_all_managed_scope = False
    for grant in applicable_grants(granting_user):
        if grant.principal_tenant_id != principal_tenant.pk:
            continue
        has_valid_all_managed_scope = (
            grant.role.tenant_id == principal_tenant.pk
            and principal_tenant.is_provider
            and any(
                scope.scope_type == RoleGrantScope.SCOPE_ALL_MANAGED
                for scope in grant.scopes.all()
            )
        )
        if has_valid_all_managed_scope:
            has_all_managed_scope = True
            all_managed_permissions.update(grant.role.permissions or [])
        own_ids.update(grant.scoped_tenant_ids())

    if scope_type in (
        RoleGrantScope.SCOPE_TENANT_GROUP,
        RoleGrantScope.SCOPE_ALL_MANAGED,
    ):
        missing_permissions = sorted(
            requested_permissions - all_managed_permissions
        )
        if not has_all_managed_scope or missing_permissions:
            detail = (
                _(" Missing permissions: %(perms)s")
                % {'perms': ', '.join(missing_permissions)}
                if missing_permissions else ''
            )
            raise ValidationError(
                _(
                    "You cannot grant a dynamic managed-tenant scope unless you "
                    "hold equivalent permission authority across all managed tenants."
                ) + detail
            )
        return
    if requested_tenant_ids is None:
        raise ValidationError(_(
            "You cannot grant reach into all managed tenants; your own reach is narrower."
        ))
    requested_tenant_ids = set(requested_tenant_ids)
    missing = requested_tenant_ids - own_ids
    if missing:
        raise ValidationError(_(
            "You cannot grant reach into tenants outside your own reach."
        ))

    # Reach alone is not authority to delegate the permissions carried by a
    # role. Prove the actor holds the complete requested permission set inside
    # every concrete target tenant; otherwise an own-tenant Admin grant could be
    # combined with an unrelated read-only/empty coverage grant to manufacture
    # Admin access in that customer.
    targets = list(Tenant._base_manager.filter(
        pk__in=requested_tenant_ids,
        deleted_at__isnull=True,
    ))
    if len(targets) != len(requested_tenant_ids):
        raise ValidationError(_(
            "You cannot grant reach into tenants outside your own reach."
        ))
    for target in targets:
        validate_permission_grant(granting_user, requested_permissions, target)


def _live_role_grant_scope_request(
    grant,
    *,
    restoring_role_id=None,
    restoring_user_group_id=None,
):
    """Return canonical validation args only when ``grant`` can become effective."""
    if not grant.is_active:
        return None

    if grant.membership_id:
        principal = grant.membership
        if not principal.is_active:
            return None
        principal_tenant = principal.tenant
    elif grant.user_group_id:
        principal = grant.user_group
        if not principal.is_active or (
            principal.deleted_at is not None
            and principal.pk != restoring_user_group_id
        ):
            return None
        principal_tenant = principal.tenant
    else:
        return None

    role = grant.role
    if principal_tenant.deleted_at is not None or role.tenant.deleted_at is not None:
        return None
    if role.deleted_at is not None and role.pk != restoring_role_id:
        return None

    has_own = False
    explicit_tenant_ids = set()
    has_live_group_scope = False
    has_all_managed = False

    managed_shape_is_live = (
        principal_tenant.pk == role.tenant_id
        and role.tenant.is_provider
    )
    for scope in grant.scopes.all():
        if scope.scope_type == 'own':
            has_own = (
                role.tenant_id == principal_tenant.pk
                or (
                    grant.membership_id
                    and role.shared_with_managed
                    and role.tenant.is_provider
                    and principal_tenant.managed_by_id == role.tenant_id
                )
            )
        elif scope.scope_type == 'all_managed' and managed_shape_is_live:
            # This remains a live dynamic capability even before the provider
            # has its first customer; restoring it must prove all-managed authority.
            has_all_managed = True
        elif scope.scope_type == 'tenant' and managed_shape_is_live:
            target = scope.tenant
            if (
                target is not None
                and target.deleted_at is None
                and target.managed_by_id == role.tenant_id
            ):
                explicit_tenant_ids.add(target.pk)
        elif scope.scope_type == 'tenant_group' and managed_shape_is_live:
            target_group = scope.tenant_group
            if target_group is None or target_group.deleted_at is not None:
                continue
            # inline imports: avoid core.auth <-> organization import cycles at load time.
            from organization.access import get_descendant_tenant_group_ids
            from organization.models import Tenant

            has_live_group_scope = has_live_group_scope or Tenant._base_manager.filter(
                managed_by_id=role.tenant_id,
                group_id__in=get_descendant_tenant_group_ids(
                    target_group.pk,
                    live_only=True,
                ),
                deleted_at__isnull=True,
            ).exists()

    if has_all_managed:
        return 'all_managed', None
    if has_live_group_scope:
        return 'tenant_group', grant.scoped_tenant_ids()
    if explicit_tenant_ids:
        return 'tenant', explicit_tenant_ids
    if has_own:
        return 'own', None
    return None


def validate_role_reactivation_grants(granting_user, role):
    """Validate retained grants that restoring ``role`` would make effective."""
    if granting_user is None or getattr(granting_user, 'is_superuser', False):
        return

    errors = []
    grants = role.role_grants.select_related(
        'membership__tenant',
        'user_group__tenant',
        'role__tenant',
    ).prefetch_related(
        'scopes',
        'scopes__tenant',
        'scopes__tenant_group',
    )
    for grant in grants:
        request = _live_role_grant_scope_request(
            grant,
            restoring_role_id=role.pk,
        )
        if request is None:
            continue
        scope_type, requested_tenant_ids = request
        try:
            validate_role_grant(
                granting_user,
                role,
                grant.tenant,
                scope_type=scope_type,
                requested_tenant_ids=requested_tenant_ids,
            )
        except ValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise ValidationError(errors)


def validate_group_membership_grant(granting_user, group):
    """Validate every live permission and scope inherited by joining ``group``."""
    if granting_user is None or getattr(granting_user, 'is_superuser', False):
        return
    if not group.is_active:
        return

    errors = []
    grants = group.role_grants.select_related(
        'membership__tenant',
        'role__tenant',
        'user_group__tenant',
    ).prefetch_related(
        'scopes', 'scopes__tenant', 'scopes__tenant_group',
    )
    for grant in grants:
        request = _live_role_grant_scope_request(
            grant,
            restoring_user_group_id=group.pk,
        )
        if request is None:
            continue
        scope_type, requested_tenant_ids = request
        try:
            validate_role_grant(
                granting_user,
                grant.role,
                group.tenant,
                scope_type=scope_type,
                requested_tenant_ids=requested_tenant_ids,
            )
        except ValidationError as exc:
            errors.extend(exc.messages)
    if errors:
        raise ValidationError(errors)
