"""Explicit scoping declarations of the assets forms (#584, WP3).

The assets forms declare tenant scoping, tenant requiredness and TomSelect
behaviour through ``TenantScopedFormMixin`` instead of relying on the global
form patches. These assertions pin each declaration directly so they stay true
when the patches are removed.
"""

from django.test import TestCase

from assets.forms import (
    AssetBulkEditForm,
    AssetDisposalForm,
    AssetForm,
    AssetRequestForm,
    AssetReservationForm,
    AssetTagSequenceForm,
    AssetTypeForm,
    SupplierForm,
    WarrantyForm,
)
from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import Location, Site, Tenant

FORMS = (
    AssetForm,
    AssetTypeForm,
    AssetBulkEditForm,
    AssetDisposalForm,
    AssetRequestForm,
    AssetReservationForm,
    AssetTagSequenceForm,
    SupplierForm,
    WarrantyForm,
)


class AssetsFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="afs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="afs-b")
        self.site = Site.objects.create(name="HQ", slug="afs-hq")
        self.loc_a = Location.objects.create(name="Loc A", slug="afs-loc-a", site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Loc B", slug="afs-loc-b", site=self.site, tenant=self.tenant_b)
        self.set_active_tenant(self.tenant)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    def test_forms_use_the_explicit_mixin(self):
        for form_class in FORMS:
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_tenant_requiredness_declarations(self):
        expected = {
            AssetForm: True,
            AssetTagSequenceForm: True,
            SupplierForm: False,
            AssetBulkEditForm: False,
        }
        for form_class, required in expected.items():
            with self.subTest(form=form_class.__name__):
                self.assertIs(form_class.tenant_required, required)
                self.assertIs(form_class().fields["tenant"].required, required)

    def test_supplier_and_bulk_edit_keep_the_tenant_picker_visible(self):
        for form_class in (SupplierForm, AssetBulkEditForm):
            field = form_class().fields["tenant"]
            with self.subTest(form=form_class.__name__):
                self.assertFalse(field.disabled)
                self.assertFalse(form_class.tenant_autoset_when_single)

    def test_model_choice_fields_are_scoped_explicitly(self):
        for form_class, names in (
            (AssetForm, ("location", "tenant")),
            (AssetBulkEditForm, ("location", "tenant")),
            (AssetTagSequenceForm, ("tenant",)),
            (SupplierForm, ("tenant",)),
        ):
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_locations_follow_the_active_tenant(self):
        for form_class in (AssetForm, AssetBulkEditForm):
            pks = set(form_class().fields["location"].queryset.values_list("pk", flat=True))
            with self.subTest(form=form_class.__name__):
                self.assertEqual(pks, {self.loc_a.pk})

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = {
            AssetForm: ("asset_type", "location"),
            AssetBulkEditForm: ("status", "location", "tenant"),
            AssetTagSequenceForm: ("tenant", "category"),
            SupplierForm: ("tenant", "tenant_group"),
        }
        for form_class, names in expected.items():
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
