"""Provider-level helpers (unified RBAC compatibility shims).

After the unified-RBAC redesign, provider capabilities (``manage_tenants``,
``manage_staff``, ``manage_groups``, ``manage_provider``) are plain Django permissions
declared on ``organization.Provider.Meta.permissions``. They are granted by attaching
them to a provider-scoped ``Role`` carried by an active staff ``Membership`` and resolved
through ``user.has_perm('organization.<cap>', obj=provider)``.

The helpers below survive purely to keep older call sites compiling; new code should call
``has_perm`` directly.
"""


def has_provider_capability(user, capability, provider=None):
    """Return True if ``user`` holds the provider capability ``capability``.

    ``capability`` is the codename suffix (e.g. ``'manage_tenants'``). Resolves through
    ``user.has_perm('organization.<capability>', obj=provider)`` so all gating flows
    through the unified backend.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    perm = f'organization.{capability}'
    if provider is not None:
        return user.has_perm(perm, obj=provider)
    # Without a specific provider, return True if the user holds the cap against ANY
    # provider they staff for.
    from organization.access import accessible_provider_ids
    from organization.models import Provider
    for provider_id in accessible_provider_ids(user):
        prov = Provider._base_manager.filter(pk=provider_id).first()
        if prov is not None and user.has_perm(perm, obj=prov):
            return True
    return False


def is_provider_staff(user):
    """True if ``user`` has any active staff Membership (i.e. is MSP staff)."""
    if not getattr(user, 'is_authenticated', False):
        return False
    cached = getattr(user, '_is_provider_staff_cache', None)
    if cached is not None:
        return cached
    from organization.models import Membership
    result = Membership.objects.filter(
        user=user, is_active=True, provider__isnull=False,
    ).exists()
    setattr(user, '_is_provider_staff_cache', result)
    return result


def can_manage_user_groups(user):
    """Unified gate for managing global UserGroups.

    True for superusers, a user holding ``organization.manage_groups`` against any
    provider they staff for, OR a user directly granted that permission via the
    legacy single-company ``user_permissions`` grant.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if has_provider_capability(user, 'manage_groups'):
        return True
    return user.user_permissions.filter(
        content_type__app_label='organization', codename='manage_groups',
    ).exists()
