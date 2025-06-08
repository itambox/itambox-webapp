import uuid
from threading import local
from django.utils.deprecation import MiddlewareMixin

_thread_locals = local()

def get_current_request_id():
    return getattr(_thread_locals, 'request_id', None)

def get_current_user():
    return getattr(_thread_locals, 'user', None)

class CurrentUserMiddleware(MiddlewareMixin):
    """
    Middleware to store the current user and a unique request ID in thread locals.
    This makes them easily accessible throughout the request lifecycle, especially
    for logging changes.
    """
    def process_request(self, request):
        _thread_locals.user = request.user if hasattr(request, 'user') and request.user.is_authenticated else None
        _thread_locals.request_id = uuid.uuid4()

    def process_response(self, request, response):
        # Clean up thread locals after the request is done
        if hasattr(_thread_locals, 'user'):
            del _thread_locals.user
        if hasattr(_thread_locals, 'request_id'):
            del _thread_locals.request_id
        return response

    def process_exception(self, request, exception):
        # Ensure cleanup even if an exception occurs
        if hasattr(_thread_locals, 'user'):
            del _thread_locals.user
        if hasattr(_thread_locals, 'request_id'):
            del _thread_locals.request_id 