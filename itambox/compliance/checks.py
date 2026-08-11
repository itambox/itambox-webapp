from urllib.parse import urlparse

from django.conf import settings
from django.core.checks import Error, register


@register("security")
def check_itambox_base_url(app_configs, **kwargs):
    """Reject unsafe configured origins before they can receive bearer links."""
    value = getattr(settings, "ITAMBOX_BASE_URL", "")
    if not value:
        return []
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path.endswith("/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return [
            Error(
                "ITAMBOX_BASE_URL must be an absolute http(s) URL without a trailing slash, query, or fragment.",
                id="compliance.E001",
            )
        ]
    return []
