import uuid
import contextvars
from .ratelimit import RateLimitMiddleware

_current_user = contextvars.ContextVar('current_user', default=None)
_request_id = contextvars.ContextVar('request_id', default=None)

def get_current_request_id():
    return _request_id.get()

def get_current_user():
    return _current_user.get()

class CurrentUserMiddleware:
    """
    Middleware to store the current user and a unique request ID in context variables.
    This makes them easily accessible throughout the request lifecycle, especially
    for logging changes, and is fully thread-safe and async-safe.
    """
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        self.process_request(request)
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_response(request, None)
            raise e
        return self.process_response(request, response)

    def process_request(self, request):
        user = request.user if hasattr(request, 'user') and request.user.is_authenticated else None
        _current_user.set(user)
        _request_id.set(uuid.uuid4())

    def process_response(self, request, response):
        _current_user.set(None)
        _request_id.set(None)
        return response


class CSPMiddleware:
    """
    Adds Content-Security-Policy headers to all responses.
    
    Uses a cryptographically secure random nonce for inline scripts to eliminate
    the need for 'unsafe-inline' in script-src.
    """
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        import base64
        import os
        # Generate a cryptographically secure random base64 nonce for this request
        request.csp_nonce = base64.b64encode(os.urandom(16)).decode('utf-8')
        response = self.get_response(request)
        return self.process_response(request, response)

    def process_response(self, request, response):
        nonce = getattr(request, 'csp_nonce', '')
        if nonce:
            response['Content-Security-Policy'] = (
                "default-src 'self'; "
                f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://rsms.me; "
                "img-src 'self' data:; "
                "font-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "media-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'self'"
            )
        else:
            response['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://rsms.me; "
                "img-src 'self' data:; "
                "font-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
                "media-src 'self' data:; "
                "connect-src 'self'; "
                "frame-ancestors 'self'"
            )
        return response


from core.managers import set_current_tenant, set_current_tenant_group, set_current_membership

class TenantMiddleware:
    """
    Middleware to resolve the active tenant from the session or switch_tenant query parameters,
    validate user's membership for that tenant, and bind the active tenant and membership context.
    """
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        self.process_request(request)
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_response(request, None)
            raise e
        return self.process_response(request, response)

    def process_request(self, request):
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            request.active_tenant = None
            request.active_tenant_group = None
            request.active_membership = None
            set_current_tenant(None)
            set_current_tenant_group(None)
            set_current_membership(None)
            return

        # 1. Resolve selected tenant or group from Session or URL Query Parameter
        session_tenant_id = request.session.get('active_tenant_id')
        session_group_id = request.session.get('active_tenant_group_id')

        query_tenant_id = request.GET.get('switch_tenant')
        query_group_id = request.GET.get('switch_tenant_group')

        from organization.models import TenantMembership, Tenant, TenantGroup

        # If query parameters are provided to switch, update them
        if query_tenant_id is not None:
            if query_tenant_id == '':
                session_tenant_id = None
                session_group_id = None
            else:
                session_tenant_id = query_tenant_id
                session_group_id = None
            request.session['active_tenant_id'] = session_tenant_id
            if 'active_tenant_group_id' in request.session:
                del request.session['active_tenant_group_id']

        elif query_group_id is not None:
            if query_group_id == '':
                session_tenant_id = None
                session_group_id = None
            else:
                session_tenant_id = None
                session_group_id = query_group_id
            request.session['active_tenant_group_id'] = session_group_id
            if 'active_tenant_id' in request.session:
                del request.session['active_tenant_id']

        active_tenant = None
        active_tenant_group = None
        active_membership = None

        if request.user.is_superuser:
            # Superusers can access any tenant or group
            if session_tenant_id:
                try:
                    active_tenant = Tenant._base_manager.get(pk=session_tenant_id)
                except Tenant.DoesNotExist:
                    session_tenant_id = None
                    if 'active_tenant_id' in request.session:
                        del request.session['active_tenant_id']
            elif session_group_id:
                try:
                    active_tenant_group = TenantGroup.objects.get(pk=session_group_id)
                except TenantGroup.DoesNotExist:
                    session_group_id = None
                    if 'active_tenant_group_id' in request.session:
                        del request.session['active_tenant_group_id']
        else:
            # For standard users, validate membership for the selected tenant or group
            if session_tenant_id:
                active_membership = TenantMembership.objects.filter(
                    user=request.user,
                    tenant_id=session_tenant_id
                ).select_related('tenant', 'role').first()
                if active_membership:
                    active_tenant = active_membership.tenant
                else:
                    session_tenant_id = None

            elif session_group_id:
                # Standard user must have membership in at least one tenant of the group
                memberships = TenantMembership.objects.filter(
                    user=request.user,
                    tenant__group_id=session_group_id
                ).select_related('tenant', 'tenant__group', 'role')
                if memberships.exists():
                    active_tenant_group = TenantGroup.objects.get(pk=session_group_id)
                    # Use first membership for role-based permission fallback within group
                    active_membership = memberships.first()
                else:
                    session_group_id = None

            # If no membership/group is found for the selected choice, default to their first membership
            if not active_tenant and not active_tenant_group:
                active_membership = TenantMembership.objects.filter(
                    user=request.user
                ).select_related('tenant', 'role').first()
                if active_membership:
                    active_tenant = active_membership.tenant
                    request.session['active_tenant_id'] = active_tenant.id
                    if 'active_tenant_group_id' in request.session:
                        del request.session['active_tenant_group_id']
                else:
                    if 'active_tenant_id' in request.session:
                        del request.session['active_tenant_id']
                    if 'active_tenant_group_id' in request.session:
                        del request.session['active_tenant_group_id']

        # Bind to request
        request.active_tenant = active_tenant
        request.active_tenant_group = active_tenant_group
        request.active_membership = active_membership

        # Call core manager thread context setter
        set_current_tenant(active_tenant)
        set_current_tenant_group(active_tenant_group)
        set_current_membership(active_membership)

    def process_response(self, request, response):
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_membership(None)
        return response