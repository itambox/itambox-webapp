from django.conf import settings
from rest_framework.permissions import BasePermission, DjangoObjectPermissions, SAFE_METHODS


class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class TokenPermissions(BasePermission):
    perms_map = {
        'GET': ['%(app_label)s.view_%(model_name)s'],
        'OPTIONS': [],
        'HEAD': ['%(app_label)s.view_%(model_name)s'],
        'POST': ['%(app_label)s.add_%(model_name)s'],
        'PUT': ['%(app_label)s.change_%(model_name)s'],
        'PATCH': ['%(app_label)s.change_%(model_name)s'],
        'DELETE': ['%(app_label)s.delete_%(model_name)s'],
    }

    def _queryset(self, view):
        assert hasattr(view, 'get_queryset') or hasattr(view, 'queryset'), (
            'Cannot apply {} on a view that does not set '
            '`.queryset` or have a `.get_queryset()` method.'
        ).format(self.__class__.__name__)

        if hasattr(view, 'get_queryset'):
            queryset = view.get_queryset()
            assert queryset is not None, (
                '{}.get_queryset() returned None'.format(view.__class__.__name__)
            )
            return queryset
        return view.queryset

    def get_required_permissions(self, method, model):
        kwargs = {
            'app_label': model._meta.app_label,
            'model_name': model._meta.model_name
        }

        if method not in self.perms_map:
            from rest_framework.exceptions import MethodNotAllowed
            raise MethodNotAllowed(method)

        return [perm % kwargs for perm in self.perms_map[method]]

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        if getattr(view, '_ignore_model_permissions', False):
            return True

        qs = self._queryset(view)
        perms = self.get_required_permissions(request.method, qs.model)

        if not perms:
            return True

        # Ensure active tenant context is set and valid
        from core.managers import get_current_tenant, set_current_tenant, set_current_membership
        if not get_current_tenant():
            from organization.models import TenantMembership
            membership = TenantMembership.objects.filter(user=request.user, is_active=True).select_related('tenant').first()
            if membership:
                set_current_tenant(membership.tenant)
                set_current_membership(membership)
            else:
                from organization.models import AssetHolder
                holder = request.user.asset_holder_profiles.first()
                if holder and holder.tenant:
                    set_current_tenant(holder.tenant)
                elif request.user.is_superuser:
                    # Superusers are global and unscoped by default
                    pass
                else:
                    # No membership, no asset-holder profile, not a superuser:
                    # the request has no resolvable tenant scope. Deny.
                    return False

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

        # Pass the object so the permission check resolves against the object's own
        # tenant (TenantMembershipBackend._resolve_tenant), making it self-sufficient
        # rather than relying on StrictTenantPermission to catch a tenant mismatch.
        return request.user.has_perms(perms, obj)


class IsAuthenticatedOrLoginNotRequired(BasePermission):
    def has_permission(self, request, view):
        if not getattr(settings, 'LOGIN_REQUIRED', True):
            return True
        return request.user.is_authenticated


class StrictTenantPermission(BasePermission):
    """
    Strict Tenant Security Boundary. Enforces that requests targeting
    specific resources belong strictly to the user's assigned Tenant scope.
    Raises Http404 on violation to prevent primary key enumeration.
    """
    def has_object_permission(self, request, view, obj):
        if request.user.is_superuser:
            return True

        from core.managers import get_current_tenant
        user_tenant = getattr(request, 'active_tenant', None) or get_current_tenant()
        if not user_tenant:
            profile = request.user.asset_holder_profiles.first()
            user_tenant = profile.tenant if profile else None

        if not user_tenant:
            from django.http import Http404
            raise Http404()

        if hasattr(obj, 'tenant'):
            obj_tenant = obj.tenant
            if obj_tenant is None:
                # Global (tenant=None) objects are READABLE but not mutable by
                # non-superusers.  Hide the mutation as a 404 to avoid leaking
                # the object's existence.
                if request.method not in SAFE_METHODS:
                    from django.http import Http404
                    raise Http404()
                return True
            # Enforce boundary: Object's tenant must match user's tenant
            if obj_tenant != user_tenant:
                from django.http import Http404
                raise Http404()

        return True
