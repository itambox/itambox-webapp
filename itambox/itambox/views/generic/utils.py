from django.utils.http import url_has_allowed_host_and_scheme


def resolve_view_model(view):
    """The model a generic view operates on: its ``model`` attribute, else the
    model behind its ``queryset``, else ``None``.

    Callers treat ``None`` as "unresolvable" and must fail closed rather than
    fall back to something permissive.
    """
    model = getattr(view, "model", None)
    if model is None:
        queryset = getattr(view, "queryset", None)
        if queryset is not None:
            model = queryset.model
    return model


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
