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
        # Clean up context vars after the request is done by resetting to default/None
        _current_user.set(None)
        _request_id.set(None)
        return response

    def process_exception(self, request, exception):
        # Ensure cleanup even if an exception occurs
        _current_user.set(None)
        _request_id.set(None)
 