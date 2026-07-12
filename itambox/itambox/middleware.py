import uuid
import contextvars
from .ratelimit import RateLimitMiddleware

_current_user = contextvars.ContextVar('current_user', default=None)
_request_id = contextvars.ContextVar('request_id', default=None)

def get_current_request_id():
    return _request_id.get()

def get_current_user():
    return _current_user.get()

def set_current_user(user):
    """Bind the current-user contextvar after the fact.

    DRF authentication runs inside a view's ``initial()`` — *after*
    ``CurrentUserMiddleware`` has already captured ``request.user`` (which is
    ``AnonymousUser`` for a token-authenticated request at that point). Token-auth
    views (e.g. SCIM) call this once authenticated so changelog rows are attributed
    to the acting principal instead of being recorded as ``user=None`` ('System').
    The middleware's response phase resets the contextvar via its entry token, so
    this set is correctly torn down at request end (no cross-request leak).
    """
    _current_user.set(user)

class CurrentUserMiddleware:
    """
    Middleware to store the current user and a unique request ID in context variables.
    This makes them easily accessible throughout the request lifecycle, especially
    for logging changes, and is fully thread-safe and async-safe.
    """
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        tokens = self.process_request(request)
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_response(request, None, tokens)
            raise e
        return self.process_response(request, response, tokens)

    def process_request(self, request):
        user = request.user if hasattr(request, 'user') and request.user.is_authenticated else None
        # Keep the reset tokens so the prior context is restored (not clobbered to
        # None) — correct for nested/async-shared contexts under ASGI.
        return (
            _current_user.set(user),
            _request_id.set(uuid.uuid4()),
        )

    def process_response(self, request, response, tokens=None):
        if tokens is not None:
            user_token, request_id_token = tokens
            _current_user.reset(user_token)
            _request_id.reset(request_id_token)
        else:
            # Called directly without the entry tokens (e.g. tests, or middleware
            # invoked outside __call__): clear to None as before.
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
                f"script-src 'self' 'nonce-{nonce}'; "
                # 'unsafe-inline' is retained in style-src as tracked tech-debt:
                # ~675 inline style= attributes across templates can't carry a
                # CSP nonce, so removing it would break layout. Full removal needs
                # a templates CSS refactor (move inline styles to classes). Scripts
                # are already nonce'd and do NOT rely on 'unsafe-inline'.
                "style-src 'self' 'unsafe-inline' https://rsms.me; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "media-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'self'"
            )
        else:
            response['Content-Security-Policy'] = (
                "default-src 'self'; "
                "script-src 'self'; "
                # 'unsafe-inline' is retained in style-src as tracked tech-debt:
                # ~675 inline style= attributes across templates can't carry a
                # CSP nonce, so removing it would break layout. Full removal needs
                # a templates CSS refactor (move inline styles to classes).
                "style-src 'self' 'unsafe-inline' https://rsms.me; "
                "img-src 'self' data:; "
                "font-src 'self'; "
                "media-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'self'"
            )
        return response


from core.managers import (
    set_current_tenant, set_current_tenant_group, set_current_membership,
    get_current_tenant, get_current_tenant_group, get_current_membership,
)

class TenantMiddleware:
    """
    Middleware to resolve the active tenant from the session or switch_tenant query parameters,
    validate user's membership for that tenant, and bind the active tenant and membership context.
    """
    def __init__(self, get_response=None):
        self.get_response = get_response

    def __call__(self, request):
        prev = self.process_request(request)
        try:
            response = self.get_response(request)
        except Exception as e:
            self.process_response(request, None, prev)
            raise e
        return self.process_response(request, response, prev)

    def process_request(self, request):
        # Snapshot the context active on entry so process_response can restore it
        # rather than clobbering it to None (the setters also clear the descendant
        # cache, so restore goes through them too).
        prev = (
            get_current_tenant(),
            get_current_tenant_group(),
            get_current_membership(),
        )
        if not hasattr(request, 'user') or not request.user.is_authenticated:
            request.active_tenant = None
            request.active_tenant_group = None
            request.active_membership = None
            set_current_tenant(None)
            set_current_tenant_group(None)
            set_current_membership(None)
            return prev

        # 1. Resolve selected tenant or group from Session or URL Query Parameter
        session_tenant_id = request.session.get('active_tenant_id')
        session_group_id = request.session.get('active_tenant_group_id')

        query_tenant_id = request.GET.get('switch_tenant')
        query_group_id = request.GET.get('switch_tenant_group')

        from organization.models import Membership, Tenant, TenantGroup

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
                    # _base_manager: resolve the active group unscoped (bootstrap —
                    # the scope isn't established yet; membership access is checked
                    # separately above for standard users).
                    active_tenant_group = TenantGroup._base_manager.get(pk=session_group_id)
                except TenantGroup.DoesNotExist:
                    session_group_id = None
                    if 'active_tenant_group_id' in request.session:
                        del request.session['active_tenant_group_id']
        else:
            # Standard (non-superuser) users. Accessible tenants = active direct
            # memberships UNION tenants granted via active cross-tenant user groups.
            from organization.access import accessible_tenant_ids
            accessible = accessible_tenant_ids(request.user)

            def _as_int(value):
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None

            if session_tenant_id:
                if _as_int(session_tenant_id) in accessible:
                    active_tenant = Tenant._base_manager.filter(pk=session_tenant_id).first()
                    # May be None when access is via a group grant (no direct membership).
                    active_membership = Membership.objects.filter(
                        user=request.user,
                        tenant_id=session_tenant_id,
                        is_active=True,
                    ).select_related('tenant').first()
                if active_tenant is None:
                    session_tenant_id = None

            elif session_group_id:
                # Standard user may scope to a tenant-group only if they can access at
                # least one tenant in its SUBTREE (via membership, a group grant, or
                # managed reach). The descendant walk (pruned at soft-deleted nodes)
                # matches filter_by_tenant and the auth backend's group gate — a member
                # whose tenants all sit in a child group may still activate the parent.
                # inline import: avoid a middleware <-> organization cycle at load
                from organization.access import get_descendant_tenant_group_ids
                group_tenant_ids = set(
                    Tenant._base_manager.filter(
                        group_id__in=get_descendant_tenant_group_ids(
                            _as_int(session_group_id), live_only=True,
                        ),
                    ).values_list('pk', flat=True)
                )
                if accessible & group_tenant_ids:
                    # _base_manager: resolve the active group unscoped (bootstrap —
                    # the scope isn't established yet).
                    active_tenant_group = TenantGroup._base_manager.get(pk=session_group_id)
                    active_membership = Membership.objects.filter(
                        user=request.user,
                        tenant_id__in=group_tenant_ids,
                        is_active=True,
                    ).select_related('tenant', 'tenant__group').first()
                else:
                    session_group_id = None

            # If nothing resolved, default to the first accessible tenant (a direct
            # membership first, else a group-granted tenant).
            if not active_tenant and not active_tenant_group:
                active_membership = Membership.objects.filter(
                    user=request.user,
                    is_active=True,
                ).select_related('tenant').first()
                if active_membership:
                    active_tenant = active_membership.tenant
                elif accessible:
                    active_tenant = Tenant._base_manager.filter(pk__in=accessible).order_by('name').first()

                if active_tenant:
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

        return prev

    def process_response(self, request, response, prev=None):
        if prev is not None:
            prev_tenant, prev_group, prev_membership = prev
            set_current_tenant(prev_tenant)
            set_current_tenant_group(prev_group)
            set_current_membership(prev_membership)
        else:
            set_current_tenant(None)
            set_current_tenant_group(None)
            set_current_membership(None)
        return response