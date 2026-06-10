from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.utils.http import url_has_allowed_host_and_scheme


def safe_return_url(request, candidate, fallback):
    """Return ``candidate`` only if it is a same-host URL; otherwise ``fallback``.

    Guards every user-supplied return_url/Referer redirect against open redirects.
    """
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback


def get_prerequisite_model(queryset):
    try:
        ct = ContentType.objects.get_by_natural_key(
            queryset.model._meta.app_label,
            queryset.model._meta.model_name,
        )
    except ContentType.DoesNotExist:
        raise ImproperlyConfigured(
            f"ContentType not found for {queryset.model._meta.label}"
        )
    return ct.model_class()
