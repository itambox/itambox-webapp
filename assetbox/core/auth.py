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
