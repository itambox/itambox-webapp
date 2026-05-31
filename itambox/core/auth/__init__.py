import logging
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from core.managers import get_current_membership

logger = logging.getLogger(__name__)
User = get_user_model()


class PasswordLoginOnlyBackend(ModelBackend):
    """
    A custom authentication backend that delegates authentication (username/password validation)
    to ModelBackend but rejects all permissions checking (has_perm/has_module_perms) to prevent
    bypassing the custom multi-tenant RBAC system.
    """
    def has_perm(self, user_obj, perm, obj=None):
        return False

    def has_module_perms(self, user_obj, app_label):
        return False

    def get_all_permissions(self, user_obj, obj=None):
        return set()

    def get_user_permissions(self, user_obj, obj=None):
        return set()

    def get_group_permissions(self, user_obj, obj=None):
        return set()


class TenantMembershipBackend:
    """
    Resolves permissions dynamically at request time based on the active membership.
    """
    def authenticate(self, request, username=None, password=None, **kwargs):
        return None

    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False

        # Superusers bypass scoping entirely
        if user_obj.is_superuser:
            return True

        membership = None
        # If a specific object is provided, check permissions based on the object's tenant
        if obj is not None:
            obj_tenant = getattr(obj, 'tenant', None)
            
            # If the object itself is a Tenant
            if obj_tenant is None and obj.__class__.__name__.lower() == 'tenant':
                obj_tenant = obj
                
            if obj_tenant is not None:
                from organization.models import TenantMembership
                cache_key = f'_tenant_membership_{obj_tenant.pk}'
                if not hasattr(user_obj, cache_key):
                    try:
                        membership = TenantMembership.objects.select_related('role').get(user=user_obj, tenant=obj_tenant)
                        setattr(user_obj, cache_key, membership)
                    except TenantMembership.DoesNotExist:
                        setattr(user_obj, cache_key, None)
                
                membership = getattr(user_obj, cache_key)
                
                if membership is None:
                    # If they don't have membership in the target tenant, deny permission
                    return False

        # Fallback to active membership if no object-specific tenant is resolved
        if not membership:
            membership = get_current_membership()

        if not membership or not getattr(membership, 'role', None):
            return False

        # Resolve permissions from the custom TenantRole JSON field
        return perm in membership.role.permissions

    def has_module_perms(self, user_obj, app_label):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        membership = get_current_membership()
        if not membership or not getattr(membership, 'role', None):
            return False
        
        # Check if the role permissions list has any permission belonging to the app_label
        return any(p.startswith(app_label + '.') for p in membership.role.permissions)
