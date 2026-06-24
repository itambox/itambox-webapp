"""Model-level privilege-escalation guard.

Privilege-escalation prevention ("you cannot grant a permission you do not
hold") historically lived only in form ``clean()`` methods. That left every
other write path — DRF serializers, SCIM provisioning, management commands,
direct model saves — free to assign arbitrary permissions. This module
centralises the check so a single utility can be called from any entry point.

The guard is deliberately tenant-explicit: it resolves the granting user's
*effective* permissions in a specific tenant (via the canonical
``TenantMembershipBackend._effective_perms``) rather than relying on the ambient
current-tenant contextvar. Superusers bypass it (trusted by definition).
"""
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from core.auth import TenantMembershipBackend


def validate_permission_grant(granting_user, permissions, tenant):
    """Raise ``ValidationError`` if ``granting_user`` may not grant ``permissions``.

    A non-superuser may only grant permission codenames they themselves hold in
    ``tenant``. ``permissions`` is any iterable of codename strings. ``tenant`` is
    the tenant the permissions apply in (e.g. the role's / membership's tenant).

    Superusers, an absent granting user, and an empty permission set are all
    no-ops. When ``tenant`` is ``None`` a non-superuser holds nothing, so any
    non-empty grant is rejected (fail closed).
    """
    if granting_user is None:
        return
    if getattr(granting_user, 'is_superuser', False):
        return

    requested = set(permissions or [])
    if not requested:
        return

    if tenant is None:
        held = frozenset()
    else:
        held = TenantMembershipBackend()._effective_perms(granting_user, tenant)

    escalated = sorted(requested - set(held))
    if escalated:
        raise ValidationError(
            _("Privilege escalation detected: you cannot grant permissions you do "
              "not hold: %(perms)s") % {'perms': ', '.join(escalated)}
        )
