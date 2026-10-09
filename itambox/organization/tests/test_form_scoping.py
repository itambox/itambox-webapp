"""Explicit scoping declarations of the organization forms (#584, WP3).

The organization forms declare tenant scoping, requiredness and TomSelect
behaviour through ``TenantScopedFormMixin`` instead of relying on the global
form patches. These assertions pin each declaration directly so they stay true
when the patches are removed.
"""

from django.test import TestCase

from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.forms import (
    AssetHolderForm,
    ContactAssignmentForm,
    ContactForm,
    ContactRoleForm,
    CostCenterForm,
    LocationForm,
    RegionForm,
    SiteForm,
    SiteGroupForm,
    TenantForm,
    TenantGroupForm,
)
from organization.forms.resource_grant_form import TenantResourceGrantForm
from organization.models import Location, Region, Site, Tenant

FORMS = (
    AssetHolderForm,
    ContactAssignmentForm,
    ContactForm,
    ContactRoleForm,
    CostCenterForm,
    LocationForm,
    RegionForm,
    SiteForm,
    SiteGroupForm,
    TenantForm,
    TenantGroupForm,
    TenantResourceGrantForm,
)


class OrganizationFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="ofs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="ofs-b")
        self.site_a = Site.objects.create(name="Site A", slug="ofs-site-a", tenant=self.tenant)
        self.site_b = Site.objects.create(name="Site B", slug="ofs-site-b", tenant=self.tenant_b)
        self.region = Region.objects.create(name="Region", slug="ofs-region")
        self.loc_a = Location.objects.create(name="Loc A", slug="ofs-loc-a", site=self.site_a, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Loc B", slug="ofs-loc-b", site=self.site_b, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    @staticmethod
    def _pks(form, name):
        return set(form.fields[name].queryset.values_list("pk", flat=True))

    def test_forms_use_the_explicit_mixin(self):
        for form_class in FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_tenant_requiredness_is_declared(self):
        required = (AssetHolderForm, CostCenterForm, LocationForm, SiteForm)
        for form_class in required:
            with self.subTest(form=form_class.__name__):
                self.assertIs(form_class.tenant_required, True)
                self.assertIs(form_class.tenant_autoset_when_single, True)
                self.assertIs(form_class().fields["tenant"].required, True)

    def test_contact_form_keeps_the_tenant_optional(self):
        # A blank tenant makes the contact global/shared.
        self.assertIs(ContactForm.tenant_required, False)
        self.assertIs(ContactForm.tenant_autoset_when_single, True)
        self.assertIs(ContactForm().fields["tenant"].required, False)

    def test_deliberately_unscoped_pickers_are_declared(self):
        self.assertEqual(TenantForm.tenant_scoped_choice_exclusions, ("managed_by",))
        self.assertEqual(TenantResourceGrantForm.tenant_scoped_choice_exclusions, ("grantee_tenant",))
        self.assertFalse(is_tenant_scoped_field(TenantForm().fields["managed_by"]))
        self.assertFalse(is_tenant_scoped_field(TenantResourceGrantForm().fields["grantee_tenant"]))

    def test_model_choice_fields_are_scoped_explicitly(self):
        # Region, SiteGroup and ContactRole are global reference models: their
        # pickers have no tenant to scope by and are intentionally unscoped.
        expected = (
            (AssetHolderForm, ("tenant",)),
            (ContactAssignmentForm, ("contact",)),
            (ContactForm, ("tenant",)),
            (CostCenterForm, ("tenant", "parent")),
            (LocationForm, ("site", "parent", "tenant")),
            (SiteForm, ("tenant",)),
            (TenantGroupForm, ("parent",)),
            (TenantResourceGrantForm, ("grantee_tenant_group",)),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_location_pickers_follow_the_active_tenant(self):
        form = LocationForm()
        self.assertIn(self.site_a.pk, self._pks(form, "site"))
        self.assertNotIn(self.site_b.pk, self._pks(form, "site"))
        self.assertIn(self.loc_a.pk, self._pks(form, "parent"))
        self.assertNotIn(self.loc_b.pk, self._pks(form, "parent"))

    def test_bound_foreign_site_is_rejected(self):
        form = LocationForm(data={"name": "X", "slug": "ofs-x", "site": self.site_b.pk, "tenant": self.tenant.pk})
        self.assertFalse(form.is_valid())
        self.assertIn("site", form.errors)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = (
            (LocationForm, ("site", "parent", "tenant", "status", "tags")),
            (SiteForm, ("region", "group", "tenant", "status", "tags")),
            (CostCenterForm, ("tenant", "parent")),
            (ContactForm, ("tenant", "tags")),
            (AssetHolderForm, ("tenant", "user", "tags")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
