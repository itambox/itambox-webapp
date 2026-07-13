"""One-way legacy-to-RoleGrant shadow synchronization for comparison mode."""
import logging

from django.db import transaction

from organization.models import Membership, RoleAssignment, RoleGrant, RoleGrantScope, Tenant
from users.models import GroupMembership

logger = logging.getLogger('itambox.auth.rbac')


def _legacy_scope_rows(assignment, grant):
    if assignment.reach == RoleAssignment.REACH_OWN:
        return [RoleGrantScope(role_grant=grant, scope_type=RoleGrantScope.SCOPE_OWN)]

    scope = assignment.managed_scope or RoleAssignment.SCOPE_EXPLICIT
    if scope == RoleAssignment.SCOPE_ALL:
        return [
            RoleGrantScope(role_grant=grant, scope_type=RoleGrantScope.SCOPE_ALL_MANAGED)
        ]
    if scope == RoleAssignment.SCOPE_TENANT_GROUP:
        if assignment.scope_group_id is None:
            return []
        return [
            RoleGrantScope(
                role_grant=grant,
                scope_type=RoleGrantScope.SCOPE_TENANT_GROUP,
                tenant_group_id=assignment.scope_group_id,
            )
        ]
    tenant_ids = Tenant._base_manager.filter(
        reach_assignments=assignment,
    ).values_list('pk', flat=True)
    return [
        RoleGrantScope(
            role_grant=grant,
            scope_type=RoleGrantScope.SCOPE_TENANT,
            tenant_id=tenant_id,
        )
        for tenant_id in tenant_ids
    ]


@transaction.atomic
def sync_role_assignment(assignment):
    """Create/update the lossless RoleGrant shadow of one RoleAssignment."""
    grant, _ = RoleGrant.objects.update_or_create(
        legacy_assignment=assignment,
        defaults={
            'membership': assignment.membership,
            'user_group': None,
            'role': assignment.role,
            'granted_by': assignment.granted_by,
        },
    )
    RoleGrantScope.objects.filter(role_grant=grant).delete()
    RoleGrantScope.objects.bulk_create(_legacy_scope_rows(assignment, grant))
    return grant


def delete_role_assignment_shadow(assignment_id):
    RoleGrant.objects.filter(legacy_assignment_id=assignment_id).delete()


@transaction.atomic
def sync_group_role(group, role):
    """Shadow a derivable legacy group-role link; remove invalid projections."""
    if group.tenant_id is None or role.tenant_id != group.tenant_id:
        RoleGrant.objects.filter(user_group=group, role=role).delete()
        logger.warning(
            'Phase-5 shadow skipped non-derivable group role group_id=%s role_id=%s',
            group.pk,
            role.pk,
        )
        return None
    grant, _ = RoleGrant.objects.update_or_create(
        user_group=group,
        role=role,
        defaults={'membership': None, 'legacy_assignment': None},
    )
    RoleGrantScope.objects.update_or_create(
        role_grant=grant,
        scope_type=RoleGrantScope.SCOPE_OWN,
        defaults={'tenant': None, 'tenant_group': None},
    )
    grant.scopes.exclude(scope_type=RoleGrantScope.SCOPE_OWN).delete()
    return grant


def delete_group_role_shadow(group_id, role_ids=None):
    queryset = RoleGrant.objects.filter(user_group_id=group_id, legacy_assignment__isnull=True)
    if role_ids is not None:
        queryset = queryset.filter(role_id__in=role_ids)
    queryset.delete()


def sync_group_member(group, user_id, *, source='manual', external_id=''):
    """Shadow a legacy global-user link when an owning Membership is derivable."""
    if group.tenant_id is None:
        logger.warning(
            'Phase-5 shadow skipped member of global group group_id=%s user_id=%s',
            group.pk,
            user_id,
        )
        return None
    membership = Membership.objects.filter(
        tenant_id=group.tenant_id,
        user_id=user_id,
    ).first()
    if membership is None:
        logger.warning(
            'Phase-5 shadow skipped group member without owner membership '
            'group_id=%s user_id=%s tenant_id=%s',
            group.pk,
            user_id,
            group.tenant_id,
        )
        return None
    group_membership, _ = GroupMembership.objects.update_or_create(
        user_group=group,
        membership=membership,
        defaults={'source': source, 'external_id': external_id},
    )
    return group_membership


def delete_group_member_shadow(group_id, user_ids=None):
    queryset = GroupMembership.objects.filter(user_group_id=group_id)
    if user_ids is not None:
        queryset = queryset.filter(membership__user_id__in=user_ids)
    queryset.delete()
