import uuid
import contextvars
from django.utils.deprecation import MiddlewareMixin

_current_user = contextvars.ContextVar('current_user', default=None)
_request_id = contextvars.ContextVar('request_id', default=None)

def get_current_request_id():
    return _request_id.get()

def get_current_user():
    return _current_user.get()

class CurrentUserMiddleware(MiddlewareMixin):
    """
    Middleware to store the current user and a unique request ID in context variables.
    This makes them easily accessible throughout the request lifecycle, especially
    for logging changes, and is fully thread-safe and async-safe.
    """
    def process_request(self, request):
        user = request.user if hasattr(request, 'user') and request.user.is_authenticated else None
        _current_user.set(user)
        _request_id.set(uuid.uuid4())

    def process_response(self, request, response):
        _current_user.set(None)
        _request_id.set(None)
        return response

    def process_exception(self, request, exception):
        _current_user.set(None)
        _request_id.set(None)


class CSPMiddleware(MiddlewareMixin):
    """
    Adds Content-Security-Policy headers to all responses.
    
    'unsafe-inline' is required because the FOUC-prevention theme script
    in base.html must run before any external JS/CSS loads. Once that
    inline script is replaced with a CSP nonce-based approach, the
    'unsafe-inline' allowance can be removed from script-src.
    """
    def process_response(self, request, response):
        response['Content-Security-Policy'] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://rsms.me; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com; "
            "media-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'self'"
        )
        return response


from core.managers import set_current_tenant

class TenantMiddleware(MiddlewareMixin):
    """
    Middleware to automatically detect the logged-in user's tenant
    and set it in the thread-local context variables context.
    """
    def process_request(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            if not request.user.is_superuser:
                profile = getattr(request.user, 'asset_holder_profile', None)
                if profile and profile.tenant:
                    set_current_tenant(profile.tenant)
                    return
        set_current_tenant(None)

    def process_response(self, request, response):
        set_current_tenant(None)
        return response

    def process_exception(self, request, exception):
        set_current_tenant(None)