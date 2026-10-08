"""Explicit scoping declarations of the procurement forms (#584, WP3).

The procurement forms declare their tenant scoping, tenant requiredness and
TomSelect behaviour through ``TenantScopedFormMixin`` instead of relying on the
global form patches. These assertions pin each declaration directly so they stay
true when the patches are removed.
"""

from django.test import TestCase

from core.forms import TenantScopedFormMixin
from core.forms.scoping import is_tenant_scoped_field
from core.managers import set_current_tenant
from core.tests.mixins import TenantTestMixin
from itambox.middleware import set_current_user
from organization.models import Location, Site, Tenant
from procurement.forms import ContractForm, PurchaseOrderForm, PurchaseOrderLineForm


class ProcurementFormDeclarationTests(TenantTestMixin, TestCase):
    def setUp(self):
        self.setup_tenant_context(name="Tenant A", slug="pfs-a")
        self.tenant_b = Tenant.objects.create(name="Tenant B", slug="pfs-b")
        self.site = Site.objects.create(name="HQ", slug="pfs-hq")
        self.loc_a = Location.objects.create(name="Loc A", slug="pfs-loc-a", site=self.site, tenant=self.tenant)
        self.loc_b = Location.objects.create(name="Loc B", slug="pfs-loc-b", site=self.site, tenant=self.tenant_b)

    def tearDown(self):
        set_current_user(None)
        set_current_tenant(None)
        self.clear_tenant_context()

    def test_forms_use_the_explicit_mixin(self):
        for form_class in (PurchaseOrderForm, PurchaseOrderLineForm, ContractForm):
            with self.subTest(form=form_class.__name__):
                self.assertTrue(issubclass(form_class, TenantScopedFormMixin))

    def test_model_choice_fields_are_scoped_explicitly(self):
        for form_class, names in (
            (PurchaseOrderForm, ("destination_location",)),
            (ContractForm, ("assets", "purchase_order", "cost_center")),
            (PurchaseOrderLineForm, ("component", "accessory", "consumable", "license")),
        ):
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertTrue(is_tenant_scoped_field(form.fields[name]))

    def test_destination_location_follows_the_active_tenant(self):
        self.set_active_tenant(self.tenant)
        pks = set(PurchaseOrderForm().fields["destination_location"].queryset.values_list("pk", flat=True))
        self.assertEqual(pks, {self.loc_a.pk})

    def test_tenant_is_required_once_a_tenant_exists(self):
        self.assertTrue(PurchaseOrderForm().fields["tenant"].required)
        self.assertTrue(ContractForm().fields["tenant"].required)

    def test_select_widgets_carry_the_tom_select_attribute(self):
        expected = {
            PurchaseOrderForm: ("supplier", "currency", "destination_location", "tenant"),
            ContractForm: ("contract_type", "status", "billing_cycle", "assets", "purchase_order"),
            PurchaseOrderLineForm: ("item_category", "asset_type", "component"),
        }
        for form_class, names in expected.items():
            form = form_class()
            for name in names:
                with self.subTest(form=form_class.__name__, field=name):
                    self.assertIn("data-tom-select", form.fields[name].widget.attrs)
