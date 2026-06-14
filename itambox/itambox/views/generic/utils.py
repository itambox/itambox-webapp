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
