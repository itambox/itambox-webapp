from django.conf import settings
from rest_framework.permissions import BasePermission, DjangoObjectPermissions, SAFE_METHODS


class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class TokenPermissions(DjangoObjectPermissions):
    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': [],
        'HEAD': ['%(app_label)s.view_%(model_name)s'],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if getattr(view, '_ignore_model_permissions', False):
            return True

        qs = self._queryset(view)
        perms = self.get_required_permissions(request.method, qs.model)

        if not perms:
            return True

        return request.user.has_perms(perms)

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        if getattr(view, '_ignore_model_permissions', False):
            return True

        qs = self._queryset(view)
        perms = self.get_required_permissions(request.method, qs.model)

        if not perms:
            return True

        return request.user.has_perms(perms, obj=obj)


class IsAuthenticatedOrLoginNotRequired(BasePermission):
    def has_permission(self, request, view):
        if not getattr(settings, 'LOGIN_REQUIRED', True):
            return True
        return request.user.is_authenticated
