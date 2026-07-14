"""Canonical badge rendering for RBAC concepts (post-collapse: reach + sharing).

The badges in ``RoleTable``/``MembershipTable`` and the hand-written badges in
``role_form.html`` / ``role_detail.html`` / ``usergroup_detail.html`` go through
these functions — the single source of truth for wording and markup. After the
Provider collapse there is no role "scope" and no membership "kind" anymore;
what remains meaningful is an assignment's ``reach`` (this tenant vs managed
tenants), whether a membership carries any managed reach ("staff"), and whether
a role definition is shared with managed tenants.
"""
from django import template
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _

from ..models import RoleAssignment

register = template.Library()

_REACH_ICONS = {
    RoleAssignment.REACH_MANAGED: 'mdi-domain',
    RoleAssignment.REACH_OWN: 'mdi-office-building',
}


@register.simple_tag
def reach_badge(assignment_or_reach, icon=False, extra_class=''):
    """Badge for an assignment's reach: purple for managed reach, blue for own tenant.

    Accepts a ``RoleAssignment`` instance or a bare reach value string. Wording always
    comes from ``REACH_CHOICES`` labels, never a hand-copied string.
    """
    reach = getattr(assignment_or_reach, 'reach', assignment_or_reach)
    if reach not in dict(RoleAssignment.REACH_CHOICES):
        reach = RoleAssignment.REACH_OWN
    label = dict(RoleAssignment.REACH_CHOICES)[reach]
    is_managed = reach == RoleAssignment.REACH_MANAGED
    css_class = 'bg-purple-lt text-purple' if is_managed else 'bg-blue-lt text-blue'
    if extra_class:
        css_class = f'{css_class} {extra_class}'
    icon_html = ''
    if icon:
        icon_html = format_html('<i class="mdi {} me-1"></i>', _REACH_ICONS[reach])
    return format_html('<span class="badge {}">{}{}</span>', css_class, icon_html, label)


@register.simple_tag
def membership_kind_badge(membership):
    """Badge for a membership: purple "Staff" when any grant carries managed reach,
    blue "Member" otherwise."""
    if membership.is_staff_membership:
        return format_html('<span class="badge bg-purple-lt text-purple">{}</span>', _("Staff"))
    return format_html('<span class="badge bg-blue-lt text-blue">{}</span>', _("Member"))


@register.simple_tag
def shared_role_badge(role):
    """Badge marking a role definition shared with managed tenants (empty otherwise)."""
    if getattr(role, 'shared_with_managed', False):
        return format_html('<span class="badge bg-teal-lt text-teal">{}</span>', _("Shared"))
    return ''
