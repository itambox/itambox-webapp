from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from .models import TenantMembership, TenantRole

@receiver([post_save, post_delete], sender=TenantMembership)
def clear_tenant_membership_cache(sender, instance, **kwargs):
    user = instance.user
    cache_key = f'_tenant_membership_{instance.tenant_id}'
    if hasattr(user, cache_key):
        delattr(user, cache_key)

@receiver(post_save, sender=TenantRole)
def clear_tenant_membership_cache_on_role_change(sender, instance, **kwargs):
    # Fetch all memberships associated with this role to clear their user cache
    memberships = instance.memberships.select_related('user').all()
    for membership in memberships:
        cache_key = f'_tenant_membership_{instance.tenant_id}'
        if hasattr(membership.user, cache_key):
            delattr(membership.user, cache_key)
