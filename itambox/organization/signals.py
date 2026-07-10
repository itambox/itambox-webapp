"""Organization signals — unified RBAC wiring."""
from django.db.models.signals import post_save, post_delete, m2m_changed
from django.dispatch import receiver

from .models import AssetHolder, Tenant, Membership, Role


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
    if not created or not instance.tenant_id:
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
    """Drop the per-(user, tenant) effective-perm caches whose source row changed."""
    user = instance.user
    if instance.tenant_id:
        for attr in (f'_perms_tenant_{instance.tenant_id}', f'_tenant_membership_{instance.tenant_id}'):
            if hasattr(user, attr):
                delattr(user, attr)
    if instance.provider_id:
        attr = f'_perms_provider_{instance.provider_id}'
        if hasattr(user, attr):
            delattr(user, attr)


@receiver(post_save, sender=Role)
def clear_perms_cache_on_role_change(sender, instance, **kwargs):
    """Bust effective-perm caches for every user reached via this role."""
    for membership in instance.memberships.select_related('user').all():
        user = membership.user
        for attr in (
            f'_perms_tenant_{membership.tenant_id}',
            f'_perms_provider_{membership.provider_id}',
        ):
            if hasattr(user, attr):
                delattr(user, attr)


@receiver(m2m_changed, sender=Membership.roles.through)
def clear_cache_on_membership_roles_change(sender, instance, action, pk_set, **kwargs):
    """Bust effective-perm caches when a Membership's roles are modified."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        user = instance.user
        for attr in (
            f'_perms_tenant_{instance.tenant_id}',
            f'_perms_provider_{instance.provider_id}',
            f'_tenant_membership_{instance.tenant_id}',
        ):
            if hasattr(user, attr):
                delattr(user, attr)


@receiver(m2m_changed, sender=Role.memberships.through)
def clear_cache_on_role_memberships_change(sender, instance, action, pk_set, **kwargs):
    """Bust effective-perm caches when a Role's memberships are modified."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        if isinstance(instance, Role):
            for membership in instance.memberships.select_related('user').all():
                user = membership.user
                for attr in (
                    f'_perms_tenant_{membership.tenant_id}',
                    f'_perms_provider_{membership.provider_id}',
                    f'_tenant_membership_{membership.tenant_id}',
                ):
                    if hasattr(user, attr):
                        delattr(user, attr)
        elif isinstance(instance, Membership):
            user = instance.user
            for attr in (
                f'_perms_tenant_{instance.tenant_id}',
                f'_perms_provider_{instance.provider_id}',
                f'_tenant_membership_{instance.tenant_id}',
            ):
                if hasattr(user, attr):
                    delattr(user, attr)


@receiver(m2m_changed, sender=Role.user_groups.through)
def clear_cache_on_group_roles_change(sender, instance, action, pk_set, **kwargs):
    """Bust effective-perm caches for all members of a UserGroup when its roles change."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        from users.models import UserGroup
        if isinstance(instance, UserGroup):
            for member in instance.members.all():
                for attr in list(member.__dict__):
                    if attr.startswith('_perms_tenant_') or attr.startswith('_perms_provider_') or attr.startswith('_tenant_membership_'):
                        delattr(member, attr)
        elif isinstance(instance, Role):
            for group in instance.user_groups.prefetch_related('members').all():
                for member in group.members.all():
                    for attr in list(member.__dict__):
                        if attr.startswith('_perms_tenant_') or attr.startswith('_perms_provider_') or attr.startswith('_tenant_membership_'):
                            delattr(member, attr)


from django.apps import apps

@receiver(m2m_changed, sender=apps.get_model('users', 'UserGroup').members.through)
def clear_cache_on_group_members_change(sender, instance, action, pk_set, **kwargs):
    """Bust effective-perm caches when users are added/removed from a UserGroup."""
    if action in ('post_add', 'post_remove', 'post_clear'):
        from users.models import UserGroup
        from django.contrib.auth import get_user_model
        User = get_user_model()
        if isinstance(instance, UserGroup):
            users = User.objects.filter(pk__in=pk_set) if pk_set else instance.members.all()
            for user in users:
                for attr in list(user.__dict__):
                    if attr.startswith('_perms_tenant_') or attr.startswith('_perms_provider_') or attr.startswith('_tenant_membership_'):
                        delattr(user, attr)
        elif isinstance(instance, User):
            for attr in list(instance.__dict__):
                if attr.startswith('_perms_tenant_') or attr.startswith('_perms_provider_') or attr.startswith('_tenant_membership_'):
                    delattr(instance, attr)


@receiver(post_save, sender=Tenant)
def instantiate_default_provider_roles(sender, instance, created, **kwargs):
    """When a provider-managed tenant is created, materialise the provider's
    ``is_default`` roles as tenant-scoped Roles so the tenant ships with the MSP's
    standard role set.

    Each ``Role(scope=provider, provider=<provider>, is_default=True)`` is copied into the
    new tenant as ``Role(scope=tenant, tenant=<new>)`` with the same name + permissions.
    No-op for non-provider tenants.
    """
    if not created or not instance.provider_id:
        return
    default_roles = Role._base_manager.filter(
        scope=Role.SCOPE_PROVIDER, provider_id=instance.provider_id,
        is_default=True, deleted_at__isnull=True,
    )
    for src in default_roles:
        Role._base_manager.get_or_create(
            scope=Role.SCOPE_TENANT, tenant=instance, name=src.name,
            defaults={
                'description': src.description,
                # Strip provider capabilities (organization.manage_*) via the canonical
                # helper — they never grant inside a tenant. See Membership for the source.
                'permissions': Membership.project_permissions_for_tenant(src.permissions),
                'slug': f'{src.slug}-{instance.slug}'[:100] if src.slug else None,
            },
        )
