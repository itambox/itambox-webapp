import time
from django.conf import settings
from django.core.cache import caches
from django.http import HttpResponse
from django.utils.translation import gettext_lazy as _


def _get_cache():
    # Allow operators to point RATELIMIT_CACHE at a Redis alias so counters
    # are shared across all gunicorn workers (LocMemCache is per-process).
    alias = getattr(settings, 'RATELIMIT_CACHE', 'default')
    return caches[alias]


class RateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Sensitive endpoints to rate limit by client IP
        rate_limited_paths = [
            '/accounts/login/',
            '/accounts/password_reset/',
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
            # Bypass rate limit for authenticated users
            if hasattr(request, 'user') and request.user.is_authenticated:
                return self.get_response(request)
            ip = self.get_client_ip(request)
            key = f"ratelimit_{ip}_{path}"

            # Retrieve limits from settings or default to 5 requests per 60 seconds
            limit = getattr(settings, 'RATELIMIT_LIMIT', 5)
            period = getattr(settings, 'RATELIMIT_PERIOD', 60)

            rl_cache = _get_cache()
            request_count = rl_cache.get(key)
            if request_count is None:
                # Key does not exist, initialize it with absolute period timeout
                rl_cache.add(key, 1, period)
            else:
                if request_count >= limit:
                    return HttpResponse(
                        _("Too many requests. Please try again in a minute."),
                        status=429,
                        content_type="text/plain"
                    )
                try:
                    rl_cache.incr(key)
                except ValueError:
                    # Fallback in case key expired between get and incr
                    rl_cache.add(key, 1, period)
            
        return self.get_response(request)

    def get_client_ip(self, request):
        # Default to safe REMOTE_ADDR. Only trust X-Forwarded-For if explicitly configured in settings.
        use_x_forwarded = getattr(settings, 'RATELIMIT_USE_X_FORWARDED_FOR', False)
        if use_x_forwarded:
            num_proxies = getattr(settings, 'RATELIMIT_NUM_PROXIES', 1)
            x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
            if x_forwarded_for:
                parts = [ip.strip() for ip in x_forwarded_for.split(',')]
                if len(parts) >= num_proxies:
                    return parts[-num_proxies]
                return parts[0]
        return request.META.get('REMOTE_ADDR', '127.0.0.1')

