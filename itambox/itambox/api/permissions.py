from django.conf import settings
from rest_framework.permissions import BasePermission, DjangoObjectPermissions, SAFE_METHODS


class IsSuperuser(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_superuser)


class IsSuperuserOrReadOnly(BasePermission):
    """Allow normal read authorization, but reserve mutations for superusers."""

    def has_permission(self, request, view):
        return request.method in SAFE_METHODS or bool(
            request.user and request.user.is_superuser
        )


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

        qs = self._queryset(view)
        perms = self.get_required_permissions(request.method, qs.model)

        if not perms:
            return True

        # Tenant selection belongs to TenantMiddleware or TokenAuthentication.
        # Permission evaluation must never silently choose the user's first
        # membership: that makes an unbound request's authorization depend on
        # database ordering and can stomp an intentional tenant-group scope.
        from core.managers import get_current_tenant, get_current_tenant_group
        tenant = get_current_tenant()
        token_tenant_id = getattr(getattr(request, 'auth', None), 'tenant_id', None)
        if token_tenant_id is not None:
            # Token requests are single-tenant and must remain pinned to the
            # authenticated token's tenant for the complete request.
            if (
                tenant is None
                or tenant.pk != token_tenant_id
                or getattr(request.auth, 'user_id', None) != request.user.pk
            ):
                return False
        elif not request.user.is_superuser and tenant is None and get_current_tenant_group() is None:
            return False

        return request.user.has_perms(perms)

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        qs = self._queryset(view)
        perms = self.get_required_permissions(request.method, qs.model)

        if not perms:
            return True

        # Pass the object so the permission check resolves against the object's own
        # tenant (MembershipBackend._resolve_tenant), making it self-sufficient
        # rather than relying on StrictTenantPermission to catch a tenant mismatch.
        if request.user.has_perms(perms, obj):
            return True
        # ADR-0001 phase 4b: shared pools and recipient-side assignments are
        # READABLE across the boundary — the view permission is then checked
        # in the ACTIVE tenant instead of the (foreign) object tenant.
        if request.method in SAFE_METHODS:
            from core.managers import get_current_tenant
            tenant = get_current_tenant()
            if tenant is not None and StrictTenantPermission._shared_read_allowed(obj, tenant):
                return request.user.has_perms(perms)
        return False


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
                # ADR-0001 phase 4b: two READ-ONLY cross-tenant exceptions —
                # a stock pool shared to the active tenant by a live grant,
                # and an assignment whose TARGET is the active tenant (the
                # recipient side of a granted checkout). Never mutation.
                if request.method in SAFE_METHODS and self._shared_read_allowed(obj, user_tenant):
                    return True
                from django.http import Http404
                raise Http404()

        return True

    @staticmethod
    def _shared_read_allowed(obj, user_tenant):
        # inline imports: keep the API layer decoupled from organization at load
        from organization.access import shared_resource_ids
        from organization.models import TenantResourceGrant

        label = obj._meta.label_lower
        if label in TenantResourceGrant.APPROVED_RESOURCE_MODELS:
            return shared_resource_ids(type(obj), user_tenant).filter(
                resource_id=obj.pk).exists()
        target_tenant_id = getattr(obj, 'target_tenant_id', None)
        return target_tenant_id is not None and target_tenant_id == user_tenant.pk
