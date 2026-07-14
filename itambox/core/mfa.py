"""MFA policy for local-password sessions."""

PASSWORD_BACKEND = 'core.auth.PasswordLoginOnlyBackend'


def _role_is_privileged(role_name, permissions, privileged_names_lower) -> bool:
    if role_name and role_name.lower() in privileged_names_lower:
        return True
    for permission in permissions or ():
        if not isinstance(permission, str):
            return True
        codename = permission.rsplit('.', 1)[-1]
        if not codename.startswith('view_'):
            return True
    return False


def role_is_privileged(role) -> bool:
    """Classify privilege by canonical names or any non-view permission."""
    # inline import: avoids core.auth initialization during model import.
    from core.auth.provisioning import PRIVILEGED_ROLE_NAMES

    privileged_names_lower = {name.lower() for name in PRIVILEGED_ROLE_NAMES}
    return _role_is_privileged(role.name, role.permissions, privileged_names_lower)


def user_requires_mfa(user) -> bool:
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if getattr(user, 'is_superuser', False):
        return True
    # inline import: avoids core.mfa <-> organization model imports at module load.
    from organization.rbac import applicable_grants

    return any(
        bool(grant.scopes.all()) and role_is_privileged(grant.role)
        for grant in applicable_grants(user)
    )


def is_password_login_session(request) -> bool:
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    return request.session.get('_auth_user_backend') == PASSWORD_BACKEND


def request_needs_mfa(request) -> bool:
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not is_password_login_session(request):
        return False
    return user_requires_mfa(user)
