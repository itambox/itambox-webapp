"""Organization signals — unified RBAC wiring."""
from django.apps import apps
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver

from .models import AssetHolder, Membership, Role, RoleAssignment


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


@receiver([post_save, post_delete], sender=RoleAssignment)
def clear_assignment_cache(sender, instance, **kwargs):
    """A grant/revoke changes effective perms — for managed reach potentially in
    many tenants at once, so clear all of the user's per-tenant caches."""
    _clear_user_perm_caches(instance.membership.user)


@receiver(m2m_changed, sender=RoleAssignment.assigned_tenants.through)
def clear_cache_on_assignment_scope_change(sender, instance, action, pk_set, **kwargs):
    """Explicit-scope refinement changes alter which tenants a grant covers."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        _clear_user_perm_caches(instance.membership.user)


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
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if isinstance(instance, User):
        _clear_user_perm_caches(instance)
    else:  # instance is a UserGroup; pk_set holds user ids
        for user in User.objects.filter(pk__in=pk_set or []):
            _clear_user_perm_caches(user)
