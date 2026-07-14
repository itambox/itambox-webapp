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


def _role_is_privileged(role_name, permissions, privileged_names_lower) -> bool:
    """True if a role is privileged by name or by granting any mutating perm.

    Privilege in this app is the JSON ``permissions`` list on ``Role``,
    not the role name. We treat a role as privileged when either:
    (a) its name is one of the canonical privileged names
        (``core.auth.provisioning.PRIVILEGED_ROLE_NAMES``, case-insensitive), or
    (b) it grants any mutating capability — a ``permissions`` entry whose
        codename is an ``add_``/``change_``/``delete_`` permission (entries are
        ``"<app_label>.<codename>"`` strings, e.g. ``"assets.add_asset"``).
    """
    if role_name and role_name.lower() in privileged_names_lower:
        return True
    for perm in permissions or ():
        codename = perm.rsplit('.', 1)[-1] if isinstance(perm, str) else ''
        if codename.startswith(('add_', 'change_', 'delete_')):
            return True
    return False


def user_requires_mfa(user) -> bool:
    """True if MFA must be enforced for ``user``.

    Required for superusers and for any user holding a *privileged* role in at
    least one tenant. Privilege keys on the role's ``permissions`` (and the
    canonical privileged role names), never on the role name regex alone — so a
    ``Manager``, an ``Admin``, and any custom role granting add/change/delete
    are all covered, while a read-only ``Viewer`` is not.
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    # Lazy imports to avoid an import cycle at app-load (settings/middleware).
    from core.auth.provisioning import PRIVILEGED_ROLE_NAMES
    from organization.models import Role

    privileged_names_lower = {name.lower() for name in PRIVILEGED_ROLE_NAMES}
    # Every role the user carries via any active membership's RoleAssignment
    # rows, across ALL tenants and reaches — MFA policy must not depend on the
    # ambient tenant context, so this rides _base_manager (the tenant-scoped
    # default manager would silently narrow the check to the active tenant).
    # The mutating-perm check needs the JSON inspected in Python.
    roles = Role._base_manager.filter(
        deleted_at__isnull=True,
        assignments__membership__user=user,
        assignments__membership__is_active=True,
    ).values_list('name', 'permissions').distinct()
    return any(
        _role_is_privileged(name, permissions, privileged_names_lower)
        for name, permissions in roles
    )


def is_password_login_session(request) -> bool:
    """True if the current session was authenticated via local password login."""
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return request.session.get('_auth_user_backend') == PASSWORD_BACKEND


def request_needs_mfa(request) -> bool:
    """True if this request must present a verified second factor.

    The per-user policy is computed fresh on every request (the membership query
    is cheap). It is deliberately *not* cached in the session: a stale cache
    would let a member promoted to a privileged role mid-session keep operating
    without a second factor for the rest of the session.
    """
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not is_password_login_session(request):
        return False
    return user_requires_mfa(user)
