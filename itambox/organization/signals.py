from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import Tenant, TenantMembership, TenantRole, ProviderRoleTemplate

@receiver([post_save, post_delete], sender=TenantMembership)
def clear_tenant_membership_cache(sender, instance, **kwargs):
    user = instance.user
    cache_key = f'_tenant_membership_{instance.tenant_id}'
    if hasattr(user, cache_key):
        delattr(user, cache_key)


@receiver(post_save, sender=Tenant)
def instantiate_default_provider_roles(sender, instance, created, **kwargs):
    """When a new provider-managed tenant is created, instantiate the provider's default
    role templates as TenantRole rows so the tenant ships with the MSP's standard roles.

    No-op for non-provider tenants (instance.provider_id is None) — so single-company
    installs are unaffected.
    """
    if not created or not instance.provider_id:
        return
    templates = ProviderRoleTemplate.objects.filter(
        provider_id=instance.provider_id, is_default=True,
    )
    for tmpl in templates:
        # _base_manager: create the role for THIS tenant regardless of the ambient
        # tenant-scoping context active during tenant creation.
        TenantRole._base_manager.get_or_create(
            tenant=instance,
            name=tmpl.name,
            defaults={
                'description': tmpl.description,
                'permissions': list(tmpl.permissions or []),
            },
        )

@receiver(post_save, sender=TenantRole)
def clear_tenant_membership_cache_on_role_change(sender, instance, **kwargs):
    # Fetch all memberships associated with this role to clear their user cache
    memberships = instance.memberships.select_related('user').all()
    for membership in memberships:
        cache_key = f'_tenant_membership_{instance.tenant_id}'
        if hasattr(membership.user, cache_key):
            delattr(membership.user, cache_key)
