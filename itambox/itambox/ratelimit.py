import time
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _

class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Sensitive endpoints to rate limit by client IP
        rate_limited_paths = [
            '/accounts/login/',
            '/organization/invite-user/',
            '/organization/accept-invitation/',
        ]
        
        path = request.path
        is_limited = False
        for p in rate_limited_paths:
            if path.startswith(p):
                is_limited = True
                break
                
        if is_limited:
            ip = self.get_client_ip(request)
            key = f"ratelimit_{ip}_{path}"
            
            # Retrieve limits from settings or default to 5 requests per 60 seconds
            limit = getattr(settings, 'RATELIMIT_LIMIT', 5)
            period = getattr(settings, 'RATELIMIT_PERIOD', 60)
            
            request_count = cache.get(key, 0)
            if request_count >= limit:
                return HttpResponse(
                    _("Too many requests. Please try again in a minute."),
                    status=429,
                    content_type="text/plain"
                )
            
            cache.set(key, request_count + 1, period)
            
        return self.get_response(request)

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0].strip()
        else:
            ip = request.META.get('REMOTE_ADDR', '127.0.0.1')
        return ip
