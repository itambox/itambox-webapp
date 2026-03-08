import logging
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)
User = get_user_model()


class AssetBoxPermissionBackend:
    def authenticate(self, request, username=None, password=None, **kwargs):
        return None

    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False

        if getattr(user_obj, '_rbac_perm_cache', None) is None:
            perm_cache = {}
            try:
                from core.models import PermissionGroup
                for group in PermissionGroup.objects.filter(users=user_obj):
                    for perm_key, perm_value in group.permissions.items():
                        if perm_value:
                            perm_cache[perm_key] = True
            except Exception as e:
                logger.debug("RBAC perm cache build error: %s", e)
            user_obj._rbac_perm_cache = perm_cache

        return user_obj._rbac_perm_cache.get(perm, False)

    def has_module_perms(self, user_obj, app_label):
        from core.models import PermissionGroup

        try:
            for group in PermissionGroup.objects.filter(users=user_obj):
                for perm_key in group.permissions.keys():
                    if perm_key.startswith(app_label + '.'):
                        return True
        except Exception:
            pass

        return False


from core.managers import get_current_membership

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
                try:
                    membership = TenantMembership.objects.get(user=user_obj, tenant=obj_tenant)
                except TenantMembership.DoesNotExist:
                    # If they don't have membership in the target tenant, deny permission
                    return False

        # Fallback to active membership if no object-specific tenant is resolved
        if not membership:
            membership = get_current_membership()

        if not membership:
            return False

        # Map Membership Roles to granular codename permissions
        ROLE_PERMISSIONS = {
            'reader': {
                'assets.view_asset',
                'inventory.view_accessory',
                'inventory.view_consumable',
                'inventory.view_kit',
                'components.view_component',
                'organization.view_location',
                'organization.view_site',
                'organization.view_assetholder',
                'extras.view_dashboard',
            },
            'member': {
                # Reader + Write capabilities
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset',
                'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory',
                'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable',
                'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit',
                'components.view_component', 'components.add_component', 'components.change_component',
                'organization.view_location', 'organization.add_location', 'organization.change_location',
                'organization.view_site', 'organization.add_site', 'organization.change_site',
                'organization.view_assetholder', 'organization.add_assetholder', 'organization.change_assetholder',
                'extras.view_dashboard', 'extras.add_dashboard', 'extras.change_dashboard',
            },
            'admin': {
                # Full admin capabilities inside their tenant
                'assets.view_asset', 'assets.add_asset', 'assets.change_asset', 'assets.delete_asset',
                'inventory.view_accessory', 'inventory.add_accessory', 'inventory.change_accessory', 'inventory.delete_accessory',
                'inventory.view_consumable', 'inventory.add_consumable', 'inventory.change_consumable', 'inventory.delete_consumable',
                'inventory.view_kit', 'inventory.add_kit', 'inventory.change_kit', 'inventory.delete_kit',
                'components.view_component', 'components.add_component', 'components.change_component', 'components.delete_component',
                'organization.view_location', 'organization.add_location', 'organization.change_location', 'organization.delete_location',
                'organization.view_site', 'organization.add_site', 'organization.change_site', 'organization.delete_site',
                'organization.view_assetholder', 'organization.add_assetholder', 'organization.change_assetholder', 'organization.delete_assetholder',
                'extras.view_dashboard', 'extras.add_dashboard', 'extras.change_dashboard', 'extras.delete_dashboard',
            }
        }

        allowed_perms = ROLE_PERMISSIONS.get(membership.role, set())
        return perm in allowed_perms

    def has_module_perms(self, user_obj, app_label):
        if not user_obj.is_active:
            return False
        if user_obj.is_superuser:
            return True
        membership = get_current_membership()
        if not membership:
            return False
        # If they have active membership, they have access to standard modules
        return app_label in ('assets', 'inventory', 'components', 'organization', 'extras')

