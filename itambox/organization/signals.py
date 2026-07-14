"""Organization signals for canonical RBAC and resource-grant hygiene."""
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from core.auth.cache import (
    invalidate_authorization_topology,
    invalidate_user_authorization_cache,
)

from .models import (
    AssetHolder,
    Membership,
    Role,
    RoleGrant,
    RoleGrantScope,
    Tenant,
    TenantGroup,
    TenantResourceGrant,
)


def _clear_user_perm_caches(*users_or_ids, using=None):
    for user_or_id in set(users_or_ids):
        if user_or_id is not None:
            invalidate_user_authorization_cache(user_or_id, using=using)


def _membership_user_ids(membership_ids):
    return set(Membership._base_manager.filter(
        pk__in={pk for pk in membership_ids if pk},
    ).values_list('user_id', flat=True))


def _group_user_ids(group_ids):
    return set(GroupMembership._base_manager.filter(
        user_group_id__in={pk for pk in group_ids if pk},
    ).values_list('membership__user_id', flat=True))


def _role_grant_user_ids(grant):
    return (
        _membership_user_ids({grant.membership_id})
        | _group_user_ids({grant.user_group_id})
    )


def _role_grant_id_user_ids(grant_ids):
    rows = RoleGrant._base_manager.filter(
        pk__in={pk for pk in grant_ids if pk},
    ).values_list('membership_id', 'user_group_id')
    membership_ids = set()
    group_ids = set()
    for membership_id, group_id in rows:
        membership_ids.add(membership_id)
        group_ids.add(group_id)
    return _membership_user_ids(membership_ids) | _group_user_ids(group_ids)


@receiver(post_save, sender=Membership)
def bind_asset_holder_on_membership(sender, instance, created, **kwargs):
    """Bind an unclaimed tenant AssetHolder with the joining user's email."""
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


@receiver(post_save, sender=Membership)
def clear_membership_cache(sender, instance, **kwargs):
    affected = set(getattr(instance, '_authz_previous_user_ids', ()))
    affected.add(instance.user_id)
    _clear_user_perm_caches(*affected, using=kwargs.get('using'))


@receiver(pre_save, sender=Membership)
def snapshot_membership_cache_principals(sender, instance, **kwargs):
    if not instance.pk:
        instance._authz_previous_user_ids = set()
        return
    instance._authz_previous_user_ids = set(
        sender._base_manager.filter(pk=instance.pk).values_list('user_id', flat=True)
    )


@receiver(pre_delete, sender=Membership)
def clear_deleted_membership_cache(sender, instance, **kwargs):
    _clear_user_perm_caches(instance.user_id, using=kwargs.get('using'))


def _clear_role_grant_caches(grant):
    _clear_user_perm_caches(*_role_grant_user_ids(grant))


@receiver(post_save, sender=RoleGrant)
def clear_role_grant_cache(sender, instance, **kwargs):
    affected = set(getattr(instance, '_authz_previous_user_ids', ()))
    affected.update(_role_grant_user_ids(instance))
    _clear_user_perm_caches(*affected, using=kwargs.get('using'))


@receiver(pre_save, sender=RoleGrant)
def snapshot_role_grant_cache_principals(sender, instance, **kwargs):
    if not instance.pk:
        instance._authz_previous_user_ids = set()
        return
    old = sender._base_manager.filter(pk=instance.pk).first()
    instance._authz_previous_user_ids = _role_grant_user_ids(old) if old else set()


@receiver(pre_delete, sender=RoleGrant)
def clear_deleted_role_grant_cache(sender, instance, **kwargs):
    _clear_user_perm_caches(
        *_role_grant_user_ids(instance),
        using=kwargs.get('using'),
    )


@receiver(post_save, sender=RoleGrantScope)
def clear_role_grant_scope_cache(sender, instance, **kwargs):
    grant_ids = set(getattr(instance, '_authz_previous_grant_ids', ()))
    grant_ids.add(instance.role_grant_id)
    _clear_user_perm_caches(
        *_role_grant_id_user_ids(grant_ids),
        using=kwargs.get('using'),
    )


@receiver(pre_save, sender=RoleGrantScope)
def snapshot_role_grant_scope_cache_principals(sender, instance, **kwargs):
    if not instance.pk:
        instance._authz_previous_grant_ids = set()
        return
    instance._authz_previous_grant_ids = set(
        sender._base_manager.filter(pk=instance.pk).values_list(
            'role_grant_id', flat=True,
        )
    )


@receiver(pre_delete, sender=RoleGrantScope)
def clear_deleted_role_grant_scope_cache(sender, instance, **kwargs):
    _clear_user_perm_caches(
        *_role_grant_id_user_ids({instance.role_grant_id}),
        using=kwargs.get('using'),
    )


GroupMembership = apps.get_model('users', 'GroupMembership')
UserGroup = apps.get_model('users', 'UserGroup')


@receiver(post_save, sender=GroupMembership)
def clear_group_membership_cache(sender, instance, **kwargs):
    affected = set(getattr(instance, '_authz_previous_user_ids', ()))
    affected.update(_membership_user_ids({instance.membership_id}))
    _clear_user_perm_caches(*affected, using=kwargs.get('using'))


@receiver(pre_save, sender=GroupMembership)
def snapshot_group_membership_cache_principals(sender, instance, **kwargs):
    if not instance.pk:
        instance._authz_previous_user_ids = set()
        return
    old_membership_ids = sender._base_manager.filter(pk=instance.pk).values_list(
        'membership_id', flat=True,
    )
    instance._authz_previous_user_ids = _membership_user_ids(old_membership_ids)


@receiver(pre_delete, sender=GroupMembership)
def clear_deleted_group_membership_cache(sender, instance, **kwargs):
    _clear_user_perm_caches(
        *_membership_user_ids({instance.membership_id}),
        using=kwargs.get('using'),
    )


@receiver(post_save, sender=UserGroup)
@receiver(pre_delete, sender=UserGroup)
def clear_user_group_cache(sender, instance, **kwargs):
    _clear_user_perm_caches(
        *_group_user_ids({instance.pk}),
        using=kwargs.get('using'),
    )


@receiver(post_save, sender=Role)
def clear_perms_cache_on_role_change(sender, instance, **kwargs):
    grants = instance.role_grants.select_related(
        'membership__user', 'user_group',
    ).prefetch_related('user_group__group_memberships__membership__user')
    for grant in grants:
        _clear_user_perm_caches(
            *_role_grant_user_ids(grant),
            using=kwargs.get('using'),
        )


@receiver(post_save, sender=Tenant)
@receiver(post_delete, sender=Tenant)
@receiver(post_save, sender=TenantGroup)
@receiver(post_delete, sender=TenantGroup)
def clear_authorization_cache_on_topology_change(sender, instance, **kwargs):
    """Management edges and group trees are live authorization boundaries."""
    invalidate_authorization_topology(using=kwargs.get('using'))


def _revoke_grants_for_deleted_resource(sender, instance, **kwargs):
    content_type = ContentType.objects.get_for_model(sender)
    for grant in TenantResourceGrant.objects.filter(
        resource_type=content_type,
        resource_id=instance.pk,
        deleted_at__isnull=True,
    ):
        grant.delete()


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
