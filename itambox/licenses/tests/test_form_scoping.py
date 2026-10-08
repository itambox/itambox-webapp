"""Explicit scoping declarations of the licenses forms (#584, WP3).

The licenses forms declare tenant scoping, tenant requiredness and TomSelect
behaviour through ``TenantScopedFormMixin`` instead of relying on the global
form patches. These assertions pin each declaration directly so they stay true
when the patches are removed.
"""

from django.test import TestCase
from model_bakery import baker

from assets.models import Asset, Manufacturer
from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from licenses.forms import LicenseCheckOutForm, LicenseForm, LicenseSeatAssignmentForm
from licenses.models import License
from organization.models import AssetHolder, Tenant
from software.models import Software
from subscriptions.models import Subscription

FORMS = (LicenseForm, LicenseSeatAssignmentForm, LicenseCheckOutForm)


class LicenseFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="lfs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="lfs-b")
        manufacturer = Manufacturer.objects.create(name="Vendor", slug="lfs-vendor")
        self.sw_a = Software.objects.create(name="SW A", manufacturer=manufacturer, tenant=self.tenant)
        self.sw_b = Software.objects.create(name="SW B", manufacturer=manufacturer, tenant=self.tenant_b)
        self.lic_a = License.objects.create(name="Lic A", software=self.sw_a, seats=2, tenant=self.tenant)
        self.lic_b = License.objects.create(name="Lic B", software=self.sw_b, seats=2, tenant=self.tenant_b)
        self.sub_a = baker.make(Subscription, tenant=self.tenant)
        self.sub_b = baker.make(Subscription, tenant=self.tenant_b)
        self.holder_a = baker.make(AssetHolder, tenant=self.tenant)
        self.holder_b = baker.make(AssetHolder, tenant=self.tenant_b)
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

    def test_license_form_tenant_requiredness_declaration(self):
        self.assertIs(LicenseForm.tenant_required, True)
        self.assertIs(LicenseForm.tenant_autoset_when_single, True)
        self.assertIs(LicenseForm().fields["tenant"].required, True)

    def test_model_choice_fields_are_scoped_explicitly(self):
        expected = (
            (LicenseForm, ("tenant", "cost_center", "subscription", "software", "tags")),
            (LicenseSeatAssignmentForm, ("license", "asset")),
            (LicenseCheckOutForm, ("assigned_holder", "asset")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_license_form_pickers_follow_the_active_tenant(self):
        form = LicenseForm()
        self.assertEqual(self._pks(form, "subscription"), {self.sub_a.pk})
        self.assertNotIn(self.sub_b.pk, self._pks(form, "subscription"))
        self.assertIn(self.sw_a.pk, self._pks(form, "software"))
        self.assertNotIn(self.sw_b.pk, self._pks(form, "software"))

    def test_seat_assignment_form_follows_the_active_tenant(self):
        form = LicenseSeatAssignmentForm()
        self.assertEqual(self._pks(form, "license"), {self.lic_a.pk})

    def test_checkout_form_follows_the_active_tenant(self):
        form = LicenseCheckOutForm()
        self.assertEqual(self._pks(form, "assigned_holder"), {self.holder_a.pk})
        self.assertNotIn(self.holder_b.pk, self._pks(form, "assigned_holder"))

    def test_checkout_form_pins_candidates_to_the_license_tenant(self):
        form = LicenseCheckOutForm(license=self.lic_a)
        self.assertEqual(self._pks(form, "assigned_holder"), {self.holder_a.pk})
        self.assertTrue(is_tenant_scoped_field(form.fields["assigned_holder"]))
        self.assertTrue(is_tenant_scoped_field(form.fields["asset"]))
        self.assertTrue(all(pk != self.holder_b.pk for pk in self._pks(form, "assigned_holder")))
        self.assertFalse(Asset.objects.filter(pk__in=self._pks(form, "asset"), tenant=self.tenant_b).exists())

    def test_bound_foreign_object_is_rejected(self):
        form = LicenseSeatAssignmentForm(data={"license": self.lic_b.pk})
        self.assertFalse(form.is_valid())
        self.assertIn("license", form.errors)
        form = LicenseCheckOutForm(data={"target_type": "holder", "assigned_holder": self.holder_b.pk})
        self.assertFalse(form.is_valid())
        self.assertIn("assigned_holder", form.errors)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = (
            (LicenseForm, ("software", "supplier", "subscription", "cost_center", "tenant", "license_type", "tags")),
            (LicenseSeatAssignmentForm, ("license", "asset")),
            (LicenseCheckOutForm, ("target_type", "assigned_holder", "asset")),
        )
        for form_class, names in expected:
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)

    def test_product_key_stays_encrypted_at_rest_through_the_form(self):
        form = LicenseForm(
            data={
                "name": "Keyed",
                "license_type": "perpetual_seat",
                "software": self.sw_a.pk,
                "seats": 1,
                "product_key": "SECRET-KEY-123",
                "tenant": self.tenant.pk,
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        lic = form.save()
        lic.refresh_from_db()
        self.assertEqual(lic.decrypted_product_key, "SECRET-KEY-123")
        self.assertNotEqual(lic.product_key, "SECRET-KEY-123")
