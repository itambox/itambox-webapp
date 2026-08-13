"""MFA policy for local-password sessions.

Also the home of the privilege classification this policy is built on
(``PRIVILEGED_ROLE_NAMES`` / :func:`role_is_privileged`). The constant used to
live in ``core.auth.provisioning``, which imports :func:`role_is_privileged`
from here — a real cycle that a function-body import only hid (issue #87 phase
D). The classification is policy, not provisioning mechanics, so it belongs on
this side of the edge and leaves this module a dependency-free leaf.
"""

from core.tenant_scope import applicable_grants

PASSWORD_BACKEND = "core.auth.PasswordLoginOnlyBackend"

# Role names that convey privileged access regardless of their permission set.
PRIVILEGED_ROLE_NAMES = {"Admin", "Manager"}


def _role_is_privileged(role_name, permissions, privileged_names_lower) -> bool:
    if role_name and role_name.lower() in privileged_names_lower:
        return True
    for permission in permissions or ():
        if not isinstance(permission, str):
            return True
        codename = permission.rsplit(".", 1)[-1]
        if not codename.startswith("view_"):
            return True
    return False


def role_is_privileged(role) -> bool:
    """Classify privilege by canonical names or any non-view permission."""
    privileged_names_lower = {name.lower() for name in PRIVILEGED_ROLE_NAMES}
    return _role_is_privileged(role.name, role.permissions, privileged_names_lower)


def user_requires_mfa(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return any(bool(grant.scopes.all()) and role_is_privileged(grant.role) for grant in applicable_grants(user))


def is_password_login_session(request) -> bool:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return request.session.get("_auth_user_backend") == PASSWORD_BACKEND


def request_needs_mfa(request) -> bool:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not is_password_login_session(request):
        return False
    return user_requires_mfa(user)
