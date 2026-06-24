"""Provider-level capability checks (the MSP layer above tenants).

Provider capabilities are a small fixed set carried as booleans on ``ProviderRole``
(``can_manage_tenants``, ``can_manage_provider_users``, ``can_manage_groups``). They are
checked DIRECTLY here rather than threaded through Django's per-tenant permission system —
this replaces the old ``GlobalCapabilityBackend`` hack.

All functions are safe to call for any user (including AnonymousUser) and are cheap: each
does at most one small query, and ``is_provider_staff`` caches its result per request on
the user object.
"""


def has_provider_capability(user, capability, provider=None):
    """Return True if ``user`` holds the provider capability ``capability``.

    ``capability`` is the suffix of a ``ProviderRole.can_<capability>`` boolean, e.g.
    ``'manage_tenants'``, ``'manage_provider_users'``, ``'manage_groups'``. Optionally
    restrict to a single ``provider``. Superusers always pass.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    # inline import: avoids AppRegistryNotReady when this module is imported early
    from users.models import ProviderMembership

    qs = ProviderMembership.objects.filter(user=user, is_active=True)
    if provider is not None:
        qs = qs.filter(provider=provider)
    field = f'can_{capability}'
    for pm in qs.select_related('provider_role'):
        role = pm.provider_role
        if role is not None and getattr(role, field, False):
            return True
    return False


def is_provider_staff(user):
    """True if ``user`` has any active ProviderMembership (i.e. is MSP staff).

    Cached per request on the user object (like effective-perm resolution) so repeated
    checks in a single request cost one query at most.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    cached = getattr(user, '_is_provider_staff_cache', None)
    if cached is not None:
        return cached
    # inline import: avoids AppRegistryNotReady when this module is imported early
    from users.models import ProviderMembership
    result = ProviderMembership.objects.filter(user=user, is_active=True).exists()
    setattr(user, '_is_provider_staff_cache', result)
    return result


def can_manage_user_groups(user):
    """Unified gate for managing global/cross-tenant UserGroups.

    True for superusers, provider staff holding ``can_manage_groups``, OR (single-company
    backward compat) a user directly granted the legacy ``users.manage_usergroups``
    capability via ``user_permissions`` — so existing "Group Manager" grants keep working
    without a Provider and without the removed GlobalCapabilityBackend.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    if user.is_superuser:
        return True
    if has_provider_capability(user, 'manage_groups'):
        return True
    return user.user_permissions.filter(
        content_type__app_label='users', codename='manage_usergroups',
    ).exists()
