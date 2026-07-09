"""Canonical badge rendering for ``Role.scope`` and ``Membership.kind``.

The "kind" badge in ``RoleTable``/``MembershipTable`` (django-tables2 ``render_kind``
methods) and the hand-written badges in ``role_form.html`` / ``role_detail.html`` /
``usergroup_detail.html`` used to duplicate this markup independently, drifting out of
sync with the model's own display accessors (``get_scope_display()`` / ``get_kind_display()``).
These two functions are the single source of truth: every call site -- Python or
template -- goes through them, so wording and markup can never drift apart again.
"""
from django import template
from django.utils.html import format_html

from ..models import Role, Membership

register = template.Library()

_ROLE_SCOPE_ICONS = {
    Role.SCOPE_PROVIDER: 'mdi-domain',
    Role.SCOPE_TENANT: 'mdi-office-building',
}


@register.simple_tag
def role_scope_badge(role_or_is_provider, icon=False, extra_class=''):
    """Badge for ``Role.scope``: purple for provider-scoped, blue for tenant-scoped.

    Wording always comes from ``Role.get_scope_display()``'s labels ('Provider role' /
    'Tenant role'), never a hand-copied string.

    Accepts either a ``Role`` instance (badge reflects ``role.scope``) or a bare bool --
    the latter for ``RoleForm.is_provider_scoped``, which is authoritative before a
    pk-less instance's own ``scope`` field is trustworthy (see that property's docstring).
    """
    if isinstance(role_or_is_provider, Role):
        is_provider = role_or_is_provider.scope == Role.SCOPE_PROVIDER
    else:
        is_provider = bool(role_or_is_provider)
    scope = Role.SCOPE_PROVIDER if is_provider else Role.SCOPE_TENANT
    label = dict(Role.SCOPE_CHOICES)[scope]
    css_class = 'bg-purple-lt text-purple' if is_provider else 'bg-blue-lt text-blue'
    if extra_class:
        css_class = f'{css_class} {extra_class}'
    icon_html = ''
    if icon:
        icon_html = format_html('<i class="mdi {} me-1"></i>', _ROLE_SCOPE_ICONS[scope])
    return format_html('<span class="badge {}">{}{}</span>', css_class, icon_html, label)


@register.simple_tag
def membership_kind_badge(membership):
    """Badge for ``Membership.kind``: purple for provider staff, blue for tenant members.

    Wording always comes from ``get_kind_display()``'s labels ('Provider staff
    (technician)' / 'Tenant member'), never a hand-copied string.
    """
    css_class = 'bg-purple-lt text-purple' if membership.is_provider_staff else 'bg-blue-lt text-blue'
    return format_html('<span class="badge {}">{}</span>', css_class, membership.get_kind_display())
