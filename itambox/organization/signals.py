"""Organization signals — unified RBAC wiring + resource-grant hygiene."""
from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .models import (
    AssetHolder,
    Membership,
    Role,
    RoleAssignment,
    RoleGrant,
    RoleGrantScope,
    TenantResourceGrant,
)
from .rbac_sync import (
    delete_group_member_shadow,
    delete_group_role_shadow,
    delete_role_assignment_shadow,
    sync_group_member,
    sync_group_role,
    sync_role_assignment,
)


def _clear_user_perm_caches(user):
    """Drop every per-tenant effective-perm / membership cache memoized on the user."""
    for attr in list(user.__dict__):
        if attr.startswith('_perms_tenant_') or attr.startswith('_tenant_membership_'):
            delattr(user, attr)


@receiver(post_save, sender=Membership)
def bind_asset_holder_on_membership(sender, instance, created, **kwargs):
    """Bind an unclaimed AssetHolder profile to the joining user's account.

    Relocated from the deleted invitation-accept flow: whenever a user gains a
    tenant membership (admin form, SCIM, quick-add, seed), an AssetHolder row in
    that tenant with a matching email and no linked user is claimed by the
    account. Uses ``_base_manager``: membership creation often runs outside the
    joining tenant's context (SCIM, provider flows), where the tenant-scoped
    default manager would silently return nothing.
    """
    if not created:
        return
    email = (instance.user.email or '').strip()
    if not email:
        return
    holder = AssetHolder._base_manager.filter(
        tenant_id=instance.tenant_id,
        email__iexact=email,
        user__isnull=True,
        deleted_at__isnull=True,
    ).first()
    if holder is not None:
        holder.user = instance.user
        holder.save(update_fields=['user'])


@receiver([post_save, post_delete], sender=Membership)
def clear_membership_cache(sender, instance, **kwargs):
    """Membership rows gate every grant on them — bust the user's perm caches."""
    _clear_user_perm_caches(instance.user)


@receiver(post_save, sender=RoleAssignment)
@receiver(pre_delete, sender=RoleAssignment)
def clear_assignment_cache(sender, instance, **kwargs):
    """A grant/revoke changes effective perms — for managed reach potentially in
    many tenants at once, so clear all of the user's per-tenant caches."""
    _clear_user_perm_caches(instance.membership.user)


def _clear_role_grant_caches(grant):
    if grant.membership_id:
        _clear_user_perm_caches(grant.membership.user)
        return
    if grant.user_group_id:
        memberships = grant.user_group.group_memberships.select_related('membership__user')
        for group_membership in memberships:
            _clear_user_perm_caches(group_membership.membership.user)


@receiver(post_save, sender=RoleGrant)
@receiver(pre_delete, sender=RoleGrant)
def clear_role_grant_cache(sender, instance, **kwargs):
    _clear_role_grant_caches(instance)


@receiver(post_save, sender=RoleGrantScope)
@receiver(pre_delete, sender=RoleGrantScope)
def clear_role_grant_scope_cache(sender, instance, **kwargs):
    # Django's cascade collector may delete the parent before delivering the
    # child's signal.  The RoleGrant signal already invalidates that cascade;
    # only invalidate here when the parent still exists (standalone scope
    # create/delete).
    grant = RoleGrant._base_manager.filter(pk=instance.role_grant_id).first()
    if grant is not None:
        _clear_role_grant_caches(grant)


@receiver(post_save, sender=apps.get_model('users', 'GroupMembership'))
@receiver(pre_delete, sender=apps.get_model('users', 'GroupMembership'))
def clear_group_membership_cache(sender, instance, **kwargs):
    _clear_user_perm_caches(instance.membership.user)


@receiver(post_save, sender=RoleAssignment)
def sync_assignment_to_role_grant(sender, instance, **kwargs):
    """Keep the phase-5 shadow grant current while legacy remains writable."""
    sync_role_assignment(instance)


@receiver(pre_delete, sender=RoleAssignment)
def delete_assignment_role_grant_shadow(sender, instance, **kwargs):
    delete_role_assignment_shadow(instance.pk)


@receiver(m2m_changed, sender=RoleAssignment.assigned_tenants.through)
def clear_cache_on_assignment_scope_change(sender, instance, action, pk_set, **kwargs):
    """Explicit-scope refinement changes alter which tenants a grant covers."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        _clear_user_perm_caches(instance.membership.user)
        sync_role_assignment(instance)


@receiver(post_save, sender=Role)
def clear_perms_cache_on_role_change(sender, instance, **kwargs):
    """Bust effective-perm caches for every user reached via this role —
    through assignments and through user groups."""
    for assignment in instance.assignments.select_related('membership__user').all():
        _clear_user_perm_caches(assignment.membership.user)
    UserGroup = apps.get_model('users', 'UserGroup')
    for group in UserGroup._base_manager.filter(roles=instance).prefetch_related('members'):
        for user in group.members.all():
            _clear_user_perm_caches(user)
    for grant in instance.role_grants.select_related(
        'membership__user', 'user_group',
    ).prefetch_related('user_group__group_memberships__membership__user'):
        _clear_role_grant_caches(grant)


@receiver(m2m_changed, sender=Role.user_groups.through)
def clear_cache_on_group_roles_change(sender, instance, action, pk_set, **kwargs):
    """Bust caches when a UserGroup's role set changes (either M2M side)."""
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    UserGroup = apps.get_model('users', 'UserGroup')
    if isinstance(instance, UserGroup):
        for user in instance.members.all():
            _clear_user_perm_caches(user)
    else:  # instance is a Role; pk_set holds group ids
        for group in UserGroup._base_manager.filter(pk__in=pk_set or []).prefetch_related('members'):
            for user in group.members.all():
                _clear_user_perm_caches(user)


@receiver(m2m_changed, sender=apps.get_model('users', 'UserGroup').members.through)
def clear_cache_on_group_members_change(sender, instance, action, pk_set, **kwargs):
    """Bust caches when users are added to / removed from a group."""
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    User = get_user_model()
    if isinstance(instance, User):
        _clear_user_perm_caches(instance)
    else:  # instance is a UserGroup; pk_set holds user ids
        for user in User.objects.filter(pk__in=pk_set or []):
            _clear_user_perm_caches(user)


@receiver(m2m_changed, sender=Role.user_groups.through)
def sync_group_roles_to_role_grants(sender, instance, action, reverse, pk_set, **kwargs):
    """One-way legacy group-role M2M shadowing for the comparison window."""
    UserGroup = apps.get_model('users', 'UserGroup')

    if action == 'pre_clear':
        if reverse:
            instance._phase5_clear_group_ids = list(
                UserGroup._base_manager.filter(roles=instance).values_list('pk', flat=True)
            )
        else:
            instance._phase5_clear_role_ids = list(
                Role._base_manager.filter(user_groups=instance).values_list('pk', flat=True)
            )
        return
    if action == 'post_add':
        if reverse:
            for group in UserGroup._base_manager.filter(pk__in=pk_set or []):
                sync_group_role(group, instance)
        else:
            for role in Role._base_manager.filter(pk__in=pk_set or []):
                sync_group_role(instance, role)
    elif action == 'post_remove':
        if reverse:
            for group_id in pk_set or []:
                delete_group_role_shadow(group_id, [instance.pk])
        else:
            delete_group_role_shadow(instance.pk, pk_set or [])
    elif action == 'post_clear':
        if reverse:
            for group_id in getattr(instance, '_phase5_clear_group_ids', []):
                delete_group_role_shadow(group_id, [instance.pk])
        else:
            delete_group_role_shadow(
                instance.pk,
                getattr(instance, '_phase5_clear_role_ids', []),
            )


@receiver(m2m_changed, sender=apps.get_model('users', 'UserGroup').members.through)
def sync_group_users_to_group_memberships(sender, instance, action, reverse, pk_set, **kwargs):
    """One-way legacy global-user M2M shadowing when owner Membership exists."""
    UserGroup = apps.get_model('users', 'UserGroup')

    if action == 'pre_clear':
        if reverse:
            instance._phase5_clear_member_group_ids = list(
                UserGroup._base_manager.filter(members=instance).values_list('pk', flat=True)
            )
        else:
            instance._phase5_clear_member_user_ids = list(
                instance.members.values_list('pk', flat=True)
            )
        return
    if action == 'post_add':
        if reverse:
            for group in UserGroup._base_manager.filter(pk__in=pk_set or []):
                sync_group_member(group, instance.pk)
        else:
            for user_id in pk_set or []:
                sync_group_member(instance, user_id)
    elif action == 'post_remove':
        if reverse:
            for group_id in pk_set or []:
                delete_group_member_shadow(group_id, [instance.pk])
        else:
            delete_group_member_shadow(instance.pk, pk_set or [])
    elif action == 'post_clear':
        if reverse:
            for group_id in getattr(instance, '_phase5_clear_member_group_ids', []):
                delete_group_member_shadow(group_id, [instance.pk])
        else:
            delete_group_member_shadow(
                instance.pk,
                getattr(instance, '_phase5_clear_member_user_ids', []),
            )


@receiver(pre_save, sender=apps.get_model('users', 'UserGroup'))
def remember_group_owner(sender, instance, **kwargs):
    if instance.pk:
        instance._phase5_previous_tenant_id = (
            sender._base_manager.filter(pk=instance.pk)
            .values_list('tenant_id', flat=True)
            .first()
        )


@receiver(post_save, sender=apps.get_model('users', 'UserGroup'))
def resync_group_shadow_after_owner_change(sender, instance, created, **kwargs):
    """Re-evaluate derivability if the legacy group's owning tenant changes."""
    if created or getattr(instance, '_phase5_previous_tenant_id', instance.tenant_id) == instance.tenant_id:
        return
    delete_group_role_shadow(instance.pk)
    delete_group_member_shadow(instance.pk)
    for role in Role._base_manager.filter(user_groups=instance):
        sync_group_role(instance, role)
    for user_id in instance.members.values_list('pk', flat=True):
        sync_group_member(instance, user_id)


# --------------------------------------------------------------------------
# TenantResourceGrant orphan cleanup (ADR-0001 phase 2): a generic FK cannot
# cascade, so when an approved stock pool is hard-deleted, revoke (soft-
# delete) every active grant that references it. Revoked grants stay as the
# audit trail; assignments keep their provenance pointer.
# --------------------------------------------------------------------------

def _revoke_grants_for_deleted_resource(sender, instance, **kwargs):
    ct = ContentType.objects.get_for_model(sender)
    for grant in TenantResourceGrant.objects.filter(
        resource_type=ct, resource_id=instance.pk, deleted_at__isnull=True,
    ):
        grant.delete()  # SoftDeleteMixin: sets deleted_at (revocation)


def _connect_resource_grant_cleanup():
    for label in TenantResourceGrant.APPROVED_RESOURCE_MODELS:
        app_label, model_name = label.split('.')
        model = apps.get_model(app_label, model_name)
        post_delete.connect(
            _revoke_grants_for_deleted_resource,
            sender=model,
            dispatch_uid=f'trg_orphan_cleanup_{model_name}',
        )


_connect_resource_grant_cleanup()
