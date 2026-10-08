"""Explicit scoping declarations of the software forms (#584, WP3).

The software forms declare tenant scoping and TomSelect behaviour through
``TenantScopedFormMixin`` instead of relying on the global form patches. These
assertions pin each declaration directly so they stay true when the patches are
removed.
"""

from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, Manufacturer
from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import Tenant
from software.forms import InstalledSoftwareForm, SoftwareForm
from software.models import Software

FORMS = (SoftwareForm, InstalledSoftwareForm)


class SoftwareFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="sfs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="sfs-b")
        manufacturer = Manufacturer.objects.create(name="Vendor", slug="sfs-vendor")
        self.sw_a = Software.objects.create(name="SW A", manufacturer=manufacturer, tenant=self.tenant)
        self.sw_b = Software.objects.create(name="SW B", manufacturer=manufacturer, tenant=self.tenant_b)
        self.asset_a = baker.make(Asset, tenant=self.tenant)
        self.asset_b = baker.make(Asset, tenant=self.tenant_b)
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

    def test_software_form_keeps_the_tenant_optional(self):
        # Software is a shared catalogue: a null tenant is a global entry.
        self.assertIs(SoftwareForm.tenant_required, False)
        self.assertIs(SoftwareForm.tenant_autoset_when_single, True)
        self.assertIs(SoftwareForm().fields["tenant"].required, False)

    def test_model_choice_fields_are_scoped_explicitly(self):
        expected = (
            (SoftwareForm, ("tenant",)),
            (InstalledSoftwareForm, ("asset", "software")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_installed_software_pickers_follow_the_active_tenant(self):
        form = InstalledSoftwareForm()
        self.assertIn(self.asset_a.pk, self._pks(form, "asset"))
        self.assertNotIn(self.asset_b.pk, self._pks(form, "asset"))
        self.assertIn(self.sw_a.pk, self._pks(form, "software"))
        self.assertNotIn(self.sw_b.pk, self._pks(form, "software"))

    def test_bound_foreign_object_is_rejected(self):
        form = InstalledSoftwareForm(data={"asset": self.asset_b.pk, "software": self.sw_b.pk})
        self.assertFalse(form.is_valid())
        self.assertIn("asset", form.errors)
        self.assertIn("software", form.errors)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = (
            (SoftwareForm, ("manufacturer", "category", "license_type", "tenant", "tags")),
            (InstalledSoftwareForm, ("asset", "software")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)

    def test_software_form_saves_with_a_null_tenant(self):
        manufacturer = Manufacturer.objects.get(slug="sfs-vendor")
        form = SoftwareForm(
            data={
                "name": "Shared",
                "manufacturer": manufacturer.pk,
                "category": Software._meta.get_field("category").choices[0][0],
                "license_type": Software._meta.get_field("license_type").choices[0][0],
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
