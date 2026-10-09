"""Filter-surface tenant scoping: parity of the removed global form patches (#584).

The global ``ModelChoiceField.queryset`` patch used to re-apply
``filter_by_tenant()`` on every choice read, which covered the model-choice
filters django-filter generates for every list view. WP5 removed the patch; the
WP2 ``TenantScopedFilterSetMixin`` carries the behaviour explicitly, and this
module is the permanent form of the #584 closeout audit's probe:

* the product-wide ``BaseFilterSet`` (and every standalone FilterSet) carries the
  read-time scoping mixin, so a filter field never offers another tenant's
  objects under tenant-group or all-accessible scope;
* a bound filter rejects a foreign id instead of accepting it;
* the deliberately unscoped pickers (``_base_manager`` querysets) stay unscoped.
"""

import importlib
import inspect
from pathlib import Path

import django_filters
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from assets.forms.filter_forms import AssetFilterForm
from assets.models import Asset
from core.filters import BaseFilterSet
from core.forms.scoping import TenantScopedFilterSetMixin, is_tenant_scoped_field
from core.managers import (
    set_current_all_accessible,
    set_current_tenant,
    set_current_tenant_group,
)
from core.tests.mixins import grant
from extras.filters import ScheduledReportFilterSet
from extras.models import ReportTemplate
from itambox.middleware import _current_user
from organization.models import Role, Site, Tenant, TenantGroup

User = get_user_model()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _FilterSurfaceFixture(TestCase):
    """Two reachable tenants and one unreachable tenant inside one group.

    ``a1``/``a2`` belong to the member's group and are granted to them; ``a3``
    sits in the same group but is out of reach; ``other`` is a foreign tenant in
    no group at all.
    """

    def setUp(self):
        self.group = TenantGroup.objects.create(name="Group A", slug="grp-a")
        self.a1 = Tenant.objects.create(name="A1", slug="t-a1", group=self.group)
        self.a2 = Tenant.objects.create(name="A2", slug="t-a2", group=self.group)
        self.a3 = Tenant.objects.create(name="A3", slug="t-a3", group=self.group)
        self.other = Tenant.objects.create(name="Other", slug="t-other")
        self.site_a1 = Site.objects.create(name="Site A1", slug="site-a1", tenant=self.a1)
        self.site_a2 = Site.objects.create(name="Site A2", slug="site-a2", tenant=self.a2)
        self.site_a3 = Site.objects.create(name="Site A3", slug="site-a3", tenant=self.a3)
        self.site_other = Site.objects.create(name="Site Other", slug="site-other", tenant=self.other)
        self.member = User.objects.create_user(username="member", password="pw")
        self.superuser = User.objects.create_superuser(username="root", email="r@x.com", password="pw")
        self.role_a1 = Role.objects.create(tenant=self.a1, name="R", permissions=[])
        self.role_a2 = Role.objects.create(tenant=self.a2, name="R", permissions=[])
        for tenant, role in ((self.a1, self.role_a1), (self.a2, self.role_a2)):
            grant(self.member, tenant, role)

    def asset_filter_form(self, data=None):
        """The product's asset-list filter form (``site`` is rendered server-side)."""
        return AssetFilterForm(data=data, queryset=Asset.objects.none())

    @staticmethod
    def site_pks(form):
        return set(form.fields["site"].queryset.values_list("pk", flat=True))


class ProductFilterSurfaceScopeTests(_FilterSurfaceFixture):
    """G1: the product FilterSet surface follows the ambient scope again."""

    def test_base_filter_set_carries_the_read_time_scoping_mixin(self):
        self.assertTrue(
            issubclass(BaseFilterSet, TenantScopedFilterSetMixin),
            "BaseFilterSet must carry the read-time scoping mixin (#584 G1)",
        )

    def test_single_tenant_scope_offers_only_the_active_tenants_site(self):
        _current_user.set(self.member)
        set_current_tenant(self.a1)
        form = self.asset_filter_form()
        self.assertEqual(self.site_pks(form), {self.site_a1.pk})

    def test_group_scope_does_not_offer_another_tenants_site(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        form = self.asset_filter_form()
        self.assertEqual(
            self.site_pks(form),
            {self.site_a1.pk, self.site_a2.pk},
            "tenant-group scope must offer the reachable group tenants only",
        )
        self.assertNotIn(self.site_other.pk, self.site_pks(form))
        self.assertNotIn("Site Other", str(form))

    def test_all_accessible_scope_does_not_offer_an_unreachable_tenants_site(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(None)
        set_current_all_accessible(True)
        form = self.asset_filter_form()
        self.assertEqual(
            self.site_pks(form),
            {self.site_a1.pk, self.site_a2.pk},
            "all-accessible scope must offer the reachable tenants only",
        )
        self.assertNotIn("Site Other", str(form))

    def test_group_scope_rejects_a_foreign_bound_site(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        form = self.asset_filter_form(data={"site": self.site_other.pk})
        self.assertFalse(form.is_valid())
        self.assertIn("site", form.errors)

    def test_group_scope_accepts_a_reachable_bound_site(self):
        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        form = self.asset_filter_form(data={"site": self.site_a2.pk})
        self.assertTrue(form.is_valid())

    def test_unscoped_superuser_still_sees_every_site(self):
        _current_user.set(self.superuser)
        form = self.asset_filter_form()
        self.assertEqual(
            self.site_pks(form),
            {self.site_a1.pk, self.site_a2.pk, self.site_a3.pk, self.site_other.pk},
        )


class StandaloneFilterSetScopeTests(_FilterSurfaceFixture):
    """FilterSets outside the ``BaseFilterSet`` hierarchy are scoped too."""

    def test_scheduled_report_filter_set_scopes_its_report_choice(self):
        report_type = ReportTemplate.REPORT_TYPE_ASSET_SUMMARY
        reachable = ReportTemplate.objects.create(name="A1 report", report_type=report_type, tenant=self.a1)
        foreign = ReportTemplate.objects.create(name="Other report", report_type=report_type, tenant=self.other)
        _current_user.set(self.member)
        for scope in ("group", "all_accessible"):
            with self.subTest(scope=scope):
                set_current_tenant(None)
                set_current_tenant_group(self.group if scope == "group" else None)
                set_current_all_accessible(scope == "all_accessible")
                filterset = ScheduledReportFilterSet()
                pks = set(filterset.form.fields["report"].queryset.values_list("pk", flat=True))
                self.assertIn(reachable.pk, pks)
                self.assertNotIn(foreign.pk, pks)

    def test_group_scope_leaves_the_base_manager_pickers_unscoped(self):
        """``UserGroupFilterSet`` picks roles and tenants through ``_base_manager``.

        Those two pickers are deliberate: the operator answers "which group grants
        access to this tenant", so the tenant list must stay complete. Adopting the
        mixin must not narrow them (WP2 C4).
        """
        from users.filters import UserGroupFilterSet

        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        form = UserGroupFilterSet().form
        self.assertFalse(is_tenant_scoped_field(form.fields["grants_tenant"]))
        self.assertFalse(is_tenant_scoped_field(form.fields["roles"]))
        tenant_pks = set(form.fields["grants_tenant"].queryset.values_list("pk", flat=True))
        self.assertIn(self.other.pk, tenant_pks)
        role_pks = set(form.fields["roles"].queryset.values_list("pk", flat=True))
        self.assertIn(self.role_a1.pk, role_pks)

    def test_choice_free_filter_set_adoption_is_inert(self):
        """A FilterSet with no model-choice filter keeps its behaviour under any scope."""
        from organization.api.filters import TenantResourceGrantAuditFilterSet

        _current_user.set(self.member)
        set_current_tenant(None)
        set_current_tenant_group(self.group)
        form = TenantResourceGrantAuditFilterSet(data={"state": "active"}).form
        self.assertFalse(any(is_tenant_scoped_field(field) for field in form.fields.values()))
        self.assertTrue(form.is_valid())


class ProductFilterSetCensusTests(SimpleTestCase):
    """Every first-party FilterSet must carry the read-time scoping mixin.

    A FilterSet that opts out of the mixin silently re-opens the disclosure the
    #584 audit found, so the invariant is asserted over the whole tree instead of
    per module.
    """

    @staticmethod
    def _filter_set_classes():
        paths = sorted(PROJECT_ROOT.glob("*/filters.py"))
        paths.append(PROJECT_ROOT / "organization" / "api" / "filters.py")
        for path in paths:
            module_name = path.relative_to(PROJECT_ROOT).with_suffix("").as_posix().replace("/", ".")
            module = importlib.import_module(module_name)
            for name, obj in vars(module).items():
                if not inspect.isclass(obj) or not issubclass(obj, django_filters.FilterSet):
                    continue
                if obj.__module__ == module_name:
                    yield module_name, name, obj

    def test_every_first_party_filter_set_carries_the_scoping_mixin(self):
        offenders = [
            f"{module_name}:{name}"
            for module_name, name, obj in self._filter_set_classes()
            if not issubclass(obj, TenantScopedFilterSetMixin)
        ]
        self.assertEqual(
            offenders,
            [],
            f"these FilterSets would offer unscoped model-choice filters (#584 G1): {offenders}",
        )

    def test_census_reaches_every_filters_module(self):
        modules = {module_name for module_name, _name, _obj in self._filter_set_classes()}
        for expected in (
            "assets.filters",
            "compliance.filters",
            "core.filters",
            "extras.filters",
            "inventory.filters",
            "licenses.filters",
            "organization.filters",
            "procurement.filters",
            "software.filters",
            "subscriptions.filters",
            "users.filters",
        ):
            self.assertIn(expected, modules)
