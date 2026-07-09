"""Coverage for the RBAC reuse/cleanup bundle (L5/L6/L7/L9/N1).

  * L6/L7 -- ``Role.SCOPE_CHOICES`` / ``Membership.KIND_CHOICES`` are the single
    canonical source for both the filterset choice labels and the badge wording
    used by ``organization.templatetags.rbac_badges``; every call site must agree,
    byte-for-byte, on wording, markup, and escaping.
  * L9 -- ``Membership.is_provider_staff`` is a plain bool, same as its siblings.
  * N1 -- ``RoleListView.queryset`` must ``select_related('tenant', 'provider')``
    so ``Role.owner`` never triggers a per-row query on the list page.
"""
from django.db import connection
from django.template import Context, Template
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils.safestring import SafeString

from core.tests.mixins import TenantTestMixin
from organization.filters import MembershipFilterSet, RoleFilterSet
from organization.models import Membership, Provider, Role
from organization.templatetags.rbac_badges import membership_kind_badge, role_scope_badge
from organization.views.role_views import RoleListView


class KindScopeChoiceLabelConsistencyTests(TestCase):
    """L7: the filtersets must reference the model's own canonical choices constant,
    never an independent hand-copied label list -- and the filter VALUES (used in
    saved URLs / the API) must stay exactly what they were before this cleanup."""

    def test_role_scope_choices_wording(self):
        self.assertEqual(
            [(value, str(label)) for value, label in Role.SCOPE_CHOICES],
            [(Role.SCOPE_TENANT, 'Tenant role'), (Role.SCOPE_PROVIDER, 'Provider role')],
        )

    def test_membership_kind_choices_wording(self):
        self.assertEqual(
            [(value, str(label)) for value, label in Membership.KIND_CHOICES],
            [(Membership.KIND_MEMBER, 'Tenant member'), (Membership.KIND_STAFF, 'Provider staff (technician)')],
        )

    def test_role_filterset_scope_choices_are_role_scope_choices(self):
        choices = RoleFilterSet.base_filters['scope'].extra['choices']
        self.assertEqual(list(choices), list(Role.SCOPE_CHOICES))

    def test_membership_filterset_kind_choices_are_membership_kind_choices(self):
        choices = MembershipFilterSet.base_filters['kind'].extra['choices']
        self.assertEqual(list(choices), list(Membership.KIND_CHOICES))

    def test_filter_values_unchanged_for_url_api_stability(self):
        # Only the label duplication was the debt (L7) -- the values a saved filter
        # URL or an API query string carries must never move.
        self.assertEqual([value for value, _label in Role.SCOPE_CHOICES], ['tenant', 'provider'])
        self.assertEqual([value for value, _label in Membership.KIND_CHOICES], ['member', 'staff'])

    def test_membership_get_kind_display_matches_kind_choices(self):
        # get_kind_display() is hand-written (kind is a derived @property, not a real
        # choices field, so there is no Django-generated accessor) -- it must still
        # agree with KIND_CHOICES exactly.
        member = Membership(tenant_id=1)
        staff = Membership(provider_id=1)
        self.assertEqual(str(member.get_kind_display()), dict(Membership.KIND_CHOICES)[Membership.KIND_MEMBER])
        self.assertEqual(str(staff.get_kind_display()), dict(Membership.KIND_CHOICES)[Membership.KIND_STAFF])


class MembershipIsProviderStaffTests(TestCase):
    """L9: ``is_provider_staff`` returns a plain bool, matching the truthy style of
    its siblings (``container``, ``kind``, ``get_kind_display``, ``save()``) -- no
    behavior change versus the prior ``is not None`` spelling."""

    def test_provider_membership_is_provider_staff(self):
        self.assertIs(Membership(provider_id=1).is_provider_staff, True)

    def test_tenant_membership_is_not_provider_staff(self):
        self.assertIs(Membership(tenant_id=1).is_provider_staff, False)

    def test_unbound_membership_is_not_provider_staff(self):
        self.assertIs(Membership().is_provider_staff, False)


class BadgeHelperTests(TenantTestMixin, TestCase):
    """L6: ``role_scope_badge`` / ``membership_kind_badge`` are the single source of
    markup for the Role.scope / Membership.kind badges -- every template and
    django-tables2 ``render_kind`` method goes through them, so wording and markup
    can never drift apart again. Canonical wording is always the model's own
    ``get_scope_display()`` / ``get_kind_display()``."""

    def setUp(self):
        self.setup_tenant_context()
        self.provider = Provider.objects.create(name='Northwind MSP', slug='northwind-msp')
        self.tenant_role = Role.objects.create(tenant=self.tenant, name='Ops', permissions=[])
        self.provider_role = Role.objects.create(
            provider=self.provider, scope=Role.SCOPE_PROVIDER, name='MSP Ops', permissions=[],
        )

    def tearDown(self):
        self.clear_tenant_context()

    # ------------------------------------------------------------------ role_scope_badge
    def test_tenant_role_badge_no_icon(self):
        html = role_scope_badge(self.tenant_role)
        self.assertIsInstance(html, SafeString)
        self.assertEqual(str(html), '<span class="badge bg-blue-lt text-blue">Tenant role</span>')

    def test_provider_role_badge_no_icon(self):
        html = role_scope_badge(self.provider_role)
        self.assertEqual(str(html), '<span class="badge bg-purple-lt text-purple">Provider role</span>')

    def test_role_badge_with_icon_uses_the_right_icon_per_scope(self):
        # role_form.html / role_detail.html use a different icon for each scope
        # (mdi-domain for provider, mdi-office-building for tenant) -- preserved
        # exactly by the shared helper.
        self.assertEqual(
            str(role_scope_badge(self.tenant_role, icon=True)),
            '<span class="badge bg-blue-lt text-blue">'
            '<i class="mdi mdi-office-building me-1"></i>Tenant role</span>',
        )
        self.assertEqual(
            str(role_scope_badge(self.provider_role, icon=True)),
            '<span class="badge bg-purple-lt text-purple">'
            '<i class="mdi mdi-domain me-1"></i>Provider role</span>',
        )

    def test_role_badge_icon_markup_is_not_double_escaped(self):
        # The icon fragment is itself built with format_html() and then nested inside
        # the outer format_html() call -- it must come through as literal markup, not
        # HTML-entity-escaped text.
        html = str(role_scope_badge(self.provider_role, icon=True))
        self.assertIn('<i class="mdi mdi-domain me-1"></i>', html)
        self.assertNotIn('&lt;i', html)
        self.assertNotIn('&gt;', html)

    def test_role_badge_extra_class(self):
        # role_detail.html additionally carries "align-middle ms-2" on the badge.
        html = role_scope_badge(self.provider_role, icon=True, extra_class='align-middle ms-2')
        self.assertEqual(
            str(html),
            '<span class="badge bg-purple-lt text-purple align-middle ms-2">'
            '<i class="mdi mdi-domain me-1"></i>Provider role</span>',
        )

    def test_role_badge_accepts_bool_for_role_form_is_provider_scoped(self):
        # RoleForm.is_provider_scoped is authoritative before a pk-less instance's own
        # ``scope`` field is trustworthy (see that property's docstring) -- role_form.html
        # passes the bare bool, not a Role instance.
        self.assertEqual(str(role_scope_badge(True)), str(role_scope_badge(self.provider_role)))
        self.assertEqual(str(role_scope_badge(False)), str(role_scope_badge(self.tenant_role)))

    def test_role_scope_badge_template_tag_matches_python_helper(self):
        tpl = Template('{% load rbac_badges %}{% role_scope_badge role %}')
        rendered = tpl.render(Context({'role': self.provider_role}))
        self.assertEqual(rendered, str(role_scope_badge(self.provider_role)))

    # ------------------------------------------------------------------ membership_kind_badge
    def test_tenant_member_badge(self):
        membership = Membership(tenant=self.tenant)
        html = membership_kind_badge(membership)
        self.assertIsInstance(html, SafeString)
        self.assertEqual(str(html), '<span class="badge bg-blue-lt text-blue">Tenant member</span>')

    def test_provider_staff_badge_uses_canonical_wording(self):
        # Canonical wording is get_kind_display()'s -- "Provider staff (technician)" --
        # not the old drifted table label "Provider staff".
        membership = Membership(provider=self.provider)
        html = membership_kind_badge(membership)
        self.assertEqual(
            str(html),
            '<span class="badge bg-purple-lt text-purple">Provider staff (technician)</span>',
        )


class RoleListQueryCountTests(TenantTestMixin, TestCase):
    """N1: RoleListView.queryset must select_related the FKs RoleTable.render_container
    dereferences via Role.owner (tenant XOR provider), or every row triggers its own
    Tenant/Provider query.

    Filters the (module-level, import-time "baked") queryset down to this test's own
    rows by pk so the assertion is independent of whatever tenant context happened to
    be active when the view module was first imported.
    """

    def setUp(self):
        self.setup_tenant_context()
        self.provider = Provider.objects.create(name='Northwind MSP', slug='northwind-msp')
        self.role_pks = []
        for i in range(3):
            role = Role.objects.create(tenant=self.tenant, name=f'Tenant Role {i}', permissions=[])
            self.role_pks.append(role.pk)
        for i in range(3):
            role = Role.objects.create(
                provider=self.provider, scope=Role.SCOPE_PROVIDER, name=f'Provider Role {i}', permissions=[],
            )
            self.role_pks.append(role.pk)

    def tearDown(self):
        self.clear_tenant_context()

    def test_role_list_queryset_touches_owner_with_a_single_query(self):
        qs = RoleListView.queryset.filter(pk__in=self.role_pks)
        with CaptureQueriesContext(connection) as ctx:
            roles = list(qs)
            owners = [str(role.owner) if role.owner else '—' for role in roles]
        self.assertEqual(len(roles), 6)
        self.assertEqual(len(owners), 6)
        self.assertEqual(
            len(ctx.captured_queries), 1,
            "RoleListView.queryset must select_related('tenant', 'provider') so Role.owner "
            "never triggers a query per row (N1).",
        )
