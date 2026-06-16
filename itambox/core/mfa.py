"""
MFA (TOTP) policy helpers.

MFA is enforced for *local password* logins only. SSO/LDAP/SAML/OIDC sessions
delegate the second factor to the upstream IdP and are therefore exempt: they
set a different ``_auth_user_backend`` on the session, and token-authenticated
API requests have no session backend at all.

These helpers are deliberately dependency-light and import the tenant models
lazily so the module is safe to import at app-load time (settings/middleware).
"""

# Dotted path of the backend used by username/password form login. SSO/LDAP set
# their own backend; matching on this is how we distinguish a password session.
PASSWORD_BACKEND = 'core.auth.PasswordLoginOnlyBackend'


def user_requires_mfa(user) -> bool:
    """True if MFA must be enforced for ``user``.

    Required for superusers and for any user who is an ``admin`` or ``owner``
    (case-insensitive role name) in at least one tenant.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    # Lazy import to avoid an import cycle at app-load (settings/middleware).
    from organization.models import TenantMembership
    return TenantMembership.objects.filter(
        user=user,
        role__name__iregex=r'^(admin|owner)$',
    ).exists()


def is_password_login_session(request) -> bool:
    """True if the current session was authenticated via local password login."""
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return request.session.get('_auth_user_backend') == PASSWORD_BACKEND


def request_needs_mfa(request) -> bool:
    """True if this request must present a verified second factor.

    The per-user policy result is cached in the session (``mfa_required``) so the
    membership query runs at most once per session rather than per request.
    """
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not is_password_login_session(request):
        return False

    cached = request.session.get('mfa_required')
    if cached is None:
        cached = user_requires_mfa(user)
        request.session['mfa_required'] = cached
    return bool(cached)
